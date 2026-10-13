"""In-process A2A mailbox bus: bot-to-bot envelopes, same process (v1).

The bus only carries envelopes, enforces the per-trace hop/round limit,
and keeps a log the app layer records into the room transcript. Bots never
write the room transcript directly; delivery (running the recipient's
AgentLoop turn) is the runtime's job.
"""
from __future__ import annotations

import queue
import secrets
import threading
from dataclasses import dataclass, field

from thyca.bots.artifacts import BusArtifactStore
from thyca.core.protocol import utc_now_ts

ENVELOPE_KINDS = frozenset(
    {"delegate", "message", "step", "status", "result", "help_request"}
)

#: Technical default (team-context layer 3): the prompt-facing body of an
#: envelope is an excerpt of at most this many chars. The full body is
#: kept in the bus artifact store keyed by ``span_id`` and fetched on
#: demand via ``bot_fetch``; large content should travel as ``refs``
#: (file paths / memory entry ids / span ids) instead of body text.
EXCERPT_CHARS = 1000

#: Technical default: max hops for one root trace_id. Recorded here (not a
#: business rule): it only bounds infinite bot↔bot ping-pong, never content.
MAX_HOPS = 16


class BusError(RuntimeError):
    """Envelope rejected (unknown kind, hop limit, unknown recipient)."""


@dataclass(frozen=True)
class Envelope:
    sender: str
    recipient: str
    kind: str
    session_id: str
    trace_id: str
    parent_span_id: str | None
    ts: str
    payload: str
    # This envelope's own span: room rows for it carry this id, and the
    # recipient turn's rows parent to it (FE delegation tree).
    span_id: str | None = None
    # By-reference payloads (CBE-3): file paths, memory entry ids, or
    # span ids the recipient resolves itself. ``payload`` stays the
    # prompt-facing excerpt (<= EXCERPT_CHARS); when the sender's body
    # was longer, the full text lives in the artifact store under
    # ``span_id`` and ``truncated`` says so.
    refs: tuple[str, ...] = ()
    truncated: bool = False

    def to_dict(self) -> dict:
        return {
            "from": self.sender,
            "to": self.recipient,
            "kind": self.kind,
            "session_id": self.session_id,
            "trace_id": self.trace_id,
            "parent_span_id": self.parent_span_id,
            "span_id": self.span_id,
            "ts": self.ts,
            "payload": self.payload,
            "refs": list(self.refs),
            "truncated": self.truncated,
        }


def new_trace_id() -> str:
    return f"tr_{secrets.token_hex(8)}"


def new_span_id() -> str:
    return f"sp_{secrets.token_hex(8)}"


@dataclass
class BusEvent:
    """One bus log row the app layer renders into the room transcript."""

    envelope: Envelope
    hops: int


class Bus:
    """Per-bot mailboxes + hop-limited send. Thread-safe, in-process."""

    def __init__(
        self, max_hops: int = MAX_HOPS, artifacts: BusArtifactStore | None = None
    ) -> None:
        self._max_hops = max_hops
        self._lock = threading.Lock()
        self._boxes: dict[str, queue.Queue[Envelope]] = {}
        self._hops: dict[str, int] = {}
        self._log: list[BusEvent] = []
        #: Full bodies of excerpted envelopes, keyed by span_id (CBE-3).
        self.artifacts = artifacts if artifacts is not None else BusArtifactStore()

    def register(self, bot_id: str) -> None:
        with self._lock:
            self._boxes.setdefault(bot_id, queue.Queue())

    def unregister(self, bot_id: str) -> None:
        with self._lock:
            self._boxes.pop(bot_id, None)

    def send(
        self,
        *,
        sender: str,
        recipient: str,
        kind: str,
        session_id: str,
        payload: str,
        trace_id: str | None = None,
        parent_span_id: str | None = None,
        span_id: str | None = None,
        refs: tuple[str, ...] | list[str] = (),
    ) -> Envelope:
        if kind not in ENVELOPE_KINDS:
            raise BusError(f"unknown envelope kind: {kind!r}")
        if not recipient or not isinstance(recipient, str):
            raise BusError("recipient must be a non-empty bot id")
        if not isinstance(payload, str):
            raise BusError("payload must be text")
        clean_refs = tuple(
            ref for ref in refs if isinstance(ref, str) and ref.strip()
        )
        own_span = span_id or new_span_id()
        # Excerpt-cap the prompt-facing body; keep the full text in the
        # bounded artifact store so prompts never carry large payloads.
        truncated = len(payload) > EXCERPT_CHARS
        if truncated:
            self.artifacts.put(own_span, payload)
        envelope = Envelope(
            sender=sender,
            recipient=recipient,
            kind=kind,
            session_id=session_id,
            trace_id=trace_id or new_trace_id(),
            parent_span_id=parent_span_id,
            ts=utc_now_ts(),
            payload=payload[:EXCERPT_CHARS] if truncated else payload,
            span_id=own_span,
            refs=clean_refs,
            truncated=truncated,
        )
        with self._lock:
            if recipient not in self._boxes:
                raise BusError(f"unknown recipient bot: {recipient!r}")
            hops = self._hops.get(envelope.trace_id, 0) + 1
            if hops > self._max_hops:
                raise BusError(
                    f"trace {envelope.trace_id} exceeded {self._max_hops} hops"
                )
            self._hops[envelope.trace_id] = hops
            self._boxes[recipient].put(envelope)
            self._log.append(BusEvent(envelope=envelope, hops=hops))
            # Bound process-lifetime growth; readers only need the tail.
            del self._log[:-500]
        return envelope

    def drain(self, bot_id: str) -> list[Envelope]:
        """Take every queued envelope for one bot (delivery = runtime turn)."""
        with self._lock:
            box = self._boxes.get(bot_id)
            if box is None:
                # Unregistered bots have no mailbox: nothing to deliver.
                return []
        out: list[Envelope] = []
        try:
            while True:
                out.append(box.get_nowait())
        except queue.Empty:
            pass
        return out

    def take(self, envelope: Envelope) -> bool:
        """Remove one specific queued envelope (delivery consumed it).

        Delivery runs the recipient turn with the envelope payload as its
        text, so the queued copy must not be re-delivered as mailbox
        context on that same turn. Other queued envelopes stay queued."""
        with self._lock:
            box = self._boxes.get(envelope.recipient)
            if box is None:
                return False
        kept: list[Envelope] = []
        removed = False
        try:
            while True:
                item = box.get_nowait()
                if not removed and item == envelope:
                    removed = True
                    continue
                kept.append(item)
        except queue.Empty:
            pass
        for item in kept:
            box.put(item)
        return removed

    def drain_log(self, session_id: str) -> list[BusEvent]:
        """Take (remove) every logged event for one room.

        Per-room draining replaces index-over-global-log bookkeeping:
        the bus log is trimmed for growth, so a saved index can silently
        point past live rows and drop or duplicate events."""
        with self._lock:
            taken = [e for e in self._log if e.envelope.session_id == session_id]
            self._log = [e for e in self._log if e.envelope.session_id != session_id]
            return taken

    def pending_for(self, bot_id: str) -> list[Envelope]:
        """Snapshot of one bot's queued envelopes (no draining).

        The task view (CBE-4) reads this: queued envelopes are open work
        and unanswered outgoing mail, so it must see them without
        consuming them."""
        with self._lock:
            box = self._boxes.get(bot_id)
            if box is None:
                return []
            return list(box.queue)

    def pending(self, bot_id: str) -> int:
        with self._lock:
            box = self._boxes.get(bot_id)
            return box.qsize() if box is not None else 0

    def log(self) -> list[BusEvent]:
        with self._lock:
            return list(self._log)

    def hops_of(self, trace_id: str) -> int:
        with self._lock:
            return self._hops.get(trace_id, 0)
