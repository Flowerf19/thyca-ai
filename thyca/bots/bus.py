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

from thyca.core.protocol import utc_now_ts

ENVELOPE_KINDS = frozenset(
    {"delegate", "message", "step", "status", "result", "help_request"}
)

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

    def to_dict(self) -> dict:
        return {
            "from": self.sender,
            "to": self.recipient,
            "kind": self.kind,
            "session_id": self.session_id,
            "trace_id": self.trace_id,
            "parent_span_id": self.parent_span_id,
            "ts": self.ts,
            "payload": self.payload,
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

    def __init__(self, max_hops: int = MAX_HOPS) -> None:
        self._max_hops = max_hops
        self._lock = threading.Lock()
        self._boxes: dict[str, queue.Queue[Envelope]] = {}
        self._hops: dict[str, int] = {}
        self._log: list[BusEvent] = []

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
    ) -> Envelope:
        if kind not in ENVELOPE_KINDS:
            raise BusError(f"unknown envelope kind: {kind!r}")
        if not recipient or not isinstance(recipient, str):
            raise BusError("recipient must be a non-empty bot id")
        envelope = Envelope(
            sender=sender,
            recipient=recipient,
            kind=kind,
            session_id=session_id,
            trace_id=trace_id or new_trace_id(),
            parent_span_id=parent_span_id,
            ts=utc_now_ts(),
            payload=payload,
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
