"""Canonical per-bot workspace timeline: the ONLY execution store.

Durable JSONL under ``bots/<bot>/workspace/timeline.jsonl`` (+``.lock``),
all writes under one FileLock, complete newline-terminated records,
fsync'd, 0700 dir / 0600 file. Event kinds: ``message`` (one canonical
Message row) and ``view_reset`` (compaction/rewrite output — new basis).

Record versions: ``v: 1`` is the ordinary shared record. ``v: 2`` is a
private-execution record carrying ``priv: {"span": <turn span>}``; it
reuses the real group window (no synthetic identity). Default
projections and window/span readers exclude v2; a private projection
selects only its own span, using its latest same-span reset as the
basis and replaying same-span messages after it. Older v1-only
readers reject v2 as unknown (fail closed). Existing v1 records stay
readable without migration or rewriting.

Semantics: ``event_id`` is unique and idempotent — replaying the same
``event_id`` with an identical kind/window/payload returns the existing
event; a payload mismatch under the same id raises ``TimelineConflict``.
Any malformed line, torn tail, seq gap, unknown ``v``, unknown kind,
or duplicate-id mismatch raises ``TimelineError`` — the file is NEVER
truncated, replaced, or silently healed.

The projector reads the LAST ``view_reset`` as the basis for the
requested window, then applies ``message`` events after it — one window
only, never mixing windows, never leaking a DM view into a group
projection. A window with no ``view_reset`` yet projects every
``message`` event for it since the log start. ``CanonicalSessionStore``
reads the projection and journals timeline events only.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from filelock import FileLock

from thyca.bots.workspace_store import _INTERNAL_SESSION_ID_RE, _check_bot_id
from thyca.core.channel import WindowRef
from thyca.core.protocol import Message, utc_now_ts
from thyca.conversations.runtime import Session, SessionStore
from thyca.conversations.runtime.errors import SessionError, SessionNotFound

EVENT_KINDS = frozenset({"message", "view_reset"})

# Private-execution record version (option B): v2 carries the existing
# turn span so private rows stay addressable without another identity.
EVENT_V1 = 1
EVENT_V2 = 2


class TimelineError(RuntimeError):
    """Malformed/corrupt timeline storage — never self-heals."""


class TimelineConflict(TimelineError):
    """Same ``event_id`` replayed with a different payload."""


class TraceExecutionError(RuntimeError):
    """Canonical execution detail is unavailable for a trace span."""


# Validate the private-execution marker: exactly the existing turn span.
def _validate_priv(priv) -> dict:
    """Checked v2 marker or raise; never silently accept a bad scope."""
    if not isinstance(priv, dict):
        raise TimelineError("v2 records must carry a priv marker")
    if set(priv.keys()) != {"span"}:
        raise TimelineError("priv marker must carry only span")
    span = priv.get("span")
    if not isinstance(span, str) or not span:
        raise TimelineError("priv span must be a non-empty string")
    return {"span": span}


# Resolve one canonical internal id to its binding path without touching disk.
def _canonical_binding_path(sessions_dir: Path, session_id: str) -> Path:
    if not isinstance(session_id, str) or not _INTERNAL_SESSION_ID_RE.fullmatch(session_id):
        raise SessionNotFound(session_id, f"invalid canonical session id: {session_id!r}")
    root = Path(sessions_dir).resolve()
    candidate = (Path(sessions_dir) / f"{session_id}.jsonl").resolve()
    if candidate.parent != root:
        raise SessionNotFound(candidate, "session id traversal blocked")
    return Path(sessions_dir) / f"{session_id}.jsonl"


# Resolve the on-disk workspace directory for one bot.
def _dir_for(root: Path, bot_id: str) -> Path:
    _check_bot_id(bot_id)
    path = root / "bots" / bot_id / "workspace"
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


class WorkspaceTimeline:
    """Append-only event journal for one bot's workspace."""

    def __init__(self, root: Path, bot_id: str, *, create: bool = True) -> None:
        self.root = Path(root)
        self.bot_id = bot_id
        if create:
            directory = _dir_for(self.root, bot_id)
        else:
            _check_bot_id(bot_id)
            directory = self.root / "bots" / bot_id / "workspace"
        self.path = directory / "timeline.jsonl"
        if not create and not self.path.is_file():
            raise FileNotFoundError(self.path)
        self._lock = FileLock(str(self.path) + ".lock")
        if not self.path.exists():
            if not create:
                raise FileNotFoundError(self.path)
            fd = os.open(
                self.path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            os.close(fd)
        self._size = 0
        self._seq = self._scan()

    # ---------- internal ----------

    def _iter_events(self):
        """Strictly validate and yield every event in the file.

        Any malformed, torn, unknown-version, out-of-sequence,
        duplicate-id, or bad-shape record raises ``TimelineError`` — the
        same contract on every read, so an event appended outside the
        lock (another process, a future writer bug) fails loud instead
        of being silently projected.
        """
        raw = self.path.read_bytes()
        if not raw:
            return
        if not raw.endswith(b"\n"):
            raise TimelineError("torn timeline tail (no newline)")
        seq = 0
        seen: set[str] = set()
        # Records are LF-terminated only. Do not use splitlines(): it
        # also cuts at U+2028/U+2029/U+0085, which json.dumps leaves raw
        # inside strings (valid JSON, e.g. pasted web content), so one
        # record would be read as two malformed fragments. The trailing
        # LF checked above leaves one empty tail fragment; drop it.
        lines = raw.decode("utf-8").split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        for line in lines:
            try:
                ev = json.loads(line)
            except Exception as exc:
                raise TimelineError(f"malformed line: {exc}") from exc
            if not isinstance(ev, dict):
                raise TimelineError("event must be a dict")
            if ev.get("v") not in (EVENT_V1, EVENT_V2):
                raise TimelineError(f"unknown version {ev.get('v')}")
            if ev.get("kind") not in EVENT_KINDS:
                raise TimelineError(f"unknown kind {ev.get('kind')}")
            eid = ev.get("event_id")
            if not isinstance(eid, str) or not eid:
                raise TimelineError("event_id missing")
            ev_seq = ev.get("seq")
            if not isinstance(ev_seq, int) or ev_seq != seq + 1:
                raise TimelineError("sequence gap/regression")
            window = ev.get("window")
            if not isinstance(window, dict) or not window.get("window_id"):
                raise TimelineError("window missing")
            if not isinstance(ev.get("payload"), dict):
                raise TimelineError("payload must be a dict")
            # v2 private records carry the existing turn span; anything
            # else is malformed and fails closed like any torn record.
            if ev.get("v") == EVENT_V2:
                _validate_priv(ev.get("priv"))
            elif "priv" in ev:
                raise TimelineError("v1 records must not carry priv")
            if eid in seen:
                raise TimelineError(f"duplicate event_id {eid}")
            seen.add(eid)
            seq = ev_seq
            yield ev

    def _scan(self) -> int:
        """Read+validate the whole file; return the highest seq."""
        seq = 0
        for ev in self._iter_events():
            seq = ev["seq"]
        try:
            self._size = self.path.stat().st_size
        except OSError:
            self._size = 0
        return seq

    # Append one journal event with the next sequence number.
    def _append_locked(self, kind: str, window: WindowRef,
                       payload: dict, event_id: str,
                       priv: dict | None = None) -> dict:
        # Private execution writes v2 with the existing turn span; the
        # journal keeps the real group window (no synthetic identity).
        if priv is None:
            version = EVENT_V1
            marker: dict[str, object] = {}
        else:
            version = EVENT_V2
            marker = {"priv": _validate_priv(priv)}
        ev = {
            "v": version,
            "seq": self._seq + 1,
            "event_id": event_id,
            "kind": kind,
            "bot_id": self.bot_id,
            "window": window.to_dict(),
            "payload": payload,
            "ts": utc_now_ts(),
            **marker,
        }
        data = (json.dumps(ev, ensure_ascii=False) + "\n").encode("utf-8")
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND, 0o600)
        try:
            written = 0
            while written < len(data):
                n = os.write(fd, data[written:])
                if n <= 0:
                    raise OSError("os.write made no progress")
                written += n
            os.fsync(fd)
        finally:
            os.close(fd)
        self._seq = ev["seq"]
        try:
            self._size = self.path.stat().st_size
        except OSError:
            pass
        return ev

    def _events(self) -> list[dict]:
        # Strictly re-validated on every read (same rules as _scan).
        # This scans the complete append-only journal so trace reads can
        # recover original message events across view_reset boundaries.
        return list(self._iter_events())

    def messages_for_window(self, window: WindowRef,
                              *, include_private: bool = False) -> list[Message]:
        """Read original MESSAGE events for one exact window.

        Deliberately scans the journal rather than the current projection:
        view_reset copies are compaction bases, not execution events, and
        must neither hide original rows nor duplicate them. Private v2
        execution is excluded unless explicitly requested."""
        messages: list[Message] = []
        for event in self._events():
            if event.get("v") == EVENT_V2 and not include_private:
                continue
            if (
                event["kind"] != "message"
                or any(
                    event["window"].get(key) != getattr(window, key)
                    for key in ("window_id", "channel_id", "kind")
                )
            ):
                continue
            raw = event["payload"].get("message")
            if not isinstance(raw, dict):
                raise TimelineError("message event payload missing message")
            try:
                messages.append(Message.from_dict(raw))
            except (KeyError, TypeError, ValueError) as exc:
                raise TimelineError("message event payload invalid") from exc
        return messages

    def messages_for_span(
        self, window: WindowRef, span_id: str,
        *, include_private: bool = False,
    ) -> list[Message]:
        """Read original MESSAGE events for one exact window/span."""
        if not isinstance(span_id, str) or not span_id:
            return []
        return [
            message for message in self.messages_for_window(
                window, include_private=include_private)
            if (message.meta or {}).get("span_id") == span_id
        ]

    def _find(self, event_id: str) -> dict | None:
        for ev in reversed(self._events()):
            if ev.get("event_id") == event_id:
                return ev
        return None

    # ---------- writers ----------

    # Record one canonical message row for an exact window.
    def append_message(self, window: WindowRef, message,
                       event_id: str, priv: dict | None = None) -> dict:
        payload = {
            "message": (message.to_canonical_dict()
                        if hasattr(message, "to_canonical_dict")
                        else message),
        }
        return self._record("message", window, payload, event_id, priv=priv)

    # Record a view_reset compaction or error basis for one window.
    def replace_view(self, window: WindowRef, messages, *,
                     event_id: str,
                     title: str | None = None, title_source: str | None = None,
                     naming_attempted: bool = False,
                     bots=None, dm=None, priv: dict | None = None) -> dict:
        payload = {
            "messages": [
                m.to_canonical_dict() if hasattr(m, "to_canonical_dict")
                else m for m in messages
            ],
            "title": title,
            "title_source": title_source,
            "naming_attempted": bool(naming_attempted),
            "bots": list(bots) if bots else None,
            "dm": dm,
        }
        return self._record("view_reset", window, payload, event_id,
                            priv=priv)

    # Validate and append one journal record with idempotency checks.
    def _record(self, kind: str, window: WindowRef, payload: dict,
                event_id: str, priv: dict | None = None) -> dict:
        if not isinstance(event_id, str) or not event_id:
            raise TimelineError("event_id required")
        clean_priv = None if priv is None else _validate_priv(priv)
        with self._lock:
            try:
                if self.path.stat().st_size != self._size:
                    # Another live instance appended since this object's
                    # last scan; re-validate now rather than appending a
                    # conflicting seq that bricks the log on next read.
                    self._seq = self._scan()
            except OSError:
                pass
            existing = self._find(event_id)
            if existing is not None:
                # v1 idempotency compares window_id only — participants
                # can legitimately drift for the same window across a
                # room's life. Any v2 involvement compares the exact
                # execution scope: version, full window, payload, priv.
                if existing.get("v") == EVENT_V2 or clean_priv is not None:
                    # Execution scope: version, window identity triple,
                    # kind, payload, private span. Participants alone
                    # never conflict (approved drift compatibility).
                    _old_win = existing.get("window") if isinstance(existing.get("window"), dict) else {}
                    _new_win = window.to_dict()
                    if (
                        existing["kind"] == kind
                        and existing.get("v") == (EVENT_V2 if clean_priv is not None else EVENT_V1)
                        and (_old_win.get("window_id"), _old_win.get("channel_id"), _old_win.get("kind"))
                        == (_new_win.get("window_id"), _new_win.get("channel_id"), _new_win.get("kind"))
                        and existing.get("payload") == payload
                        and existing.get("priv", None) == clean_priv
                    ):
                        return existing
                elif (
                    existing["kind"] == kind
                    and existing["window"].get("window_id")
                    == window.window_id
                    and existing["payload"] == payload
                ):
                    return existing
                raise TimelineConflict(
                    f"event_id {event_id} replayed with different payload")
            return self._append_locked(kind, window, payload, event_id,
                                       priv=clean_priv)

    # ---------- projector ----------

    def project(self, window: WindowRef, *,
                session_id: str | None = None,
                priv_span: str | None = None) -> Session:
        """Project the requested window's execution view.

        Default (member) basis = last v1 ``view_reset`` for that window;
        v1 ``message`` events after the basis replay on top. v2 private
        execution never enters a default projection. A private projection
        (``priv_span`` set) uses the latest same-span ``view_reset``
        as its basis and replays only same-span v2 messages after it,
        so one invocation can never read another's transcript. The requested ``WindowRef`` is the only
        non-private scope consulted. The session path is a binding only
        — canonical content lives in the journal.
        """
        if priv_span is not None and (
                not isinstance(priv_span, str) or not priv_span):
            raise TimelineError("priv_span must be a non-empty string")
        events = self._events()
        if priv_span is not None:
            # Same-span reset is this invocation's basis; later same-span
            # messages replay after it. Other spans, v1 records, and all
            # group bases stay out.
            basis_msgs: list[Message] = []
            basis_idx = -1
            for i, ev in enumerate(events):
                if ev.get("v") != EVENT_V2:
                    continue
                if ev["window"].get("window_id") != window.window_id:
                    continue
                marker = ev.get("priv")
                if not isinstance(marker, dict):
                    continue
                if marker.get("span") != priv_span:
                    continue
                if ev["kind"] != "view_reset":
                    continue
                payload = ev.get("payload")
                if not isinstance(payload, dict):
                    continue
                basis_msgs = [
                    Message.from_dict(m) if hasattr(Message, "from_dict")
                    else m for m in payload.get("messages", [])
                ]
                basis_idx = i
            selected: list[Message] = list(basis_msgs)
            for ev in events[basis_idx + 1:]:
                if ev.get("v") != EVENT_V2:
                    continue
                if ev["window"].get("window_id") != window.window_id:
                    continue
                marker = ev.get("priv")
                if not isinstance(marker, dict):
                    continue
                if marker.get("span") != priv_span:
                    continue
                if ev["kind"] != "message":
                    continue
                raw = ev["payload"].get("message")
                selected.append(
                    Message.from_dict(raw) if hasattr(Message, "from_dict")
                    else raw)
            sid = session_id or window.window_id
            path = _canonical_binding_path(
                self.root / "bots" / self.bot_id / "sessions", sid)
            return Session(sid, path, selected)
        basis = None
        basis_idx = -1
        for i, ev in enumerate(events):
            if ev.get("v") == EVENT_V2:
                continue
            if ev["window"].get("window_id") != window.window_id:
                continue
            if ev["kind"] == "view_reset":
                basis = ev
                basis_idx = i
        messages: list[Message] = []
        title = title_source = None
        naming_attempted = False
        bots = dm = None
        if basis is not None:
            payload = basis["payload"]
            messages = [
                Message.from_dict(m) if hasattr(Message, "from_dict")
                else m for m in payload.get("messages", [])
            ]
            title = payload.get("title")
            title_source = payload.get("title_source")
            naming_attempted = bool(payload.get("naming_attempted"))
            bots = payload.get("bots")
            dm = payload.get("dm")
        for ev in events[basis_idx + 1:]:
            if ev.get("v") == EVENT_V2:
                continue
            if ev["window"].get("window_id") != window.window_id:
                continue
            if ev["kind"] == "message":
                raw = ev["payload"].get("message")
                messages.append(
                    Message.from_dict(raw) if hasattr(Message, "from_dict")
                    else raw)
        sid = session_id or window.window_id
        path = _canonical_binding_path(
            self.root / "bots" / self.bot_id / "sessions", sid)
        return Session(
            sid, path, messages,
            title=title, title_source=title_source,
            naming_attempted=naming_attempted,
            bots=bots, dm_bot=dm,
        )


class CanonicalSessionStore(SessionStore):
    """Canonical store for one ``(bot, window, session_id)`` — the
    ONLY execution store for child-bot turns.

    Subclasses ``SessionStore`` for its session-store interface but never
touches session files: reads come from the timeline projection, writes
land as ``message``/``view_reset`` journal events only. An address to any
other session fails instead of falling back to a file-backed store.
    """

    # Bind one canonical session with an optional private span selector.
    def __init__(self, sessions_dir: Path, *,
                 timeline: WorkspaceTimeline, window: WindowRef,
                 session_id: str, priv_span: str | None = None) -> None:
        # A private execution binds the real group window for persistence
        # and selects only its own span; the visible DM-like window lives
        # only in the turn and is never journaled.
        if priv_span is not None and (
                not isinstance(priv_span, str) or not priv_span):
            raise TimelineError("priv_span must be a non-empty string")
        super().__init__(sessions_dir)
        self._timeline = timeline
        self._window = window
        self._session_id = session_id
        self._priv_span = priv_span

    # Execution marker for this store: none shared, span only when private.
    def _priv(self) -> dict | None:
        return None if self._priv_span is None else {"span": self._priv_span}

    # Resolve one canonical internal id to its binding path without touching disk.
    def path_for(self, session_id: str) -> Path:
        return _canonical_binding_path(self.sessions_dir, session_id)

    # ---------- projection helpers ----------

    # Require the requested id to be this store's bound canonical session.
    def _require_session(self, session_id: str) -> None:
        if session_id != self._session_id:
            raise TimelineError(
                f"canonical store is bound to {self._session_id}, not {session_id}")

    def _require_path(self, path) -> None:
        self._require_session(Path(path).stem)

    def _projected(self) -> Session:
        return self._timeline.project(
            self._window, session_id=self._session_id,
            priv_span=self._priv_span)

    def _reset(self, messages, *, title, title_source,
               naming_attempted, bots, dm) -> None:
        import uuid

        self._timeline.replace_view(
            self._window, messages, event_id=f"reset-{uuid.uuid4().hex}",
            title=title, title_source=title_source,
            naming_attempted=naming_attempted, bots=bots, dm=dm,
            priv=self._priv())

    # ---------- reads ----------

    def load(self, session_id: str) -> Session:
        self._require_session(session_id)
        return self._projected()

    def scan(self, path):
        self._require_path(path)
        projected = self._projected()
        return (
            list(projected.messages), projected.title,
            projected.title_source, projected.naming_attempted,
        )

    def read_title(self, path):
        self._require_path(path)
        projected = self._projected()
        if projected.title:
            return projected.title, projected.title_source
        return None

    def read_dm(self, path):
        self._require_path(path)
        return self._projected().dm_bot

    def read_bots(self, path):
        self._require_path(path)
        return self._projected().bots

    def list_paths(self) -> list:
        # Honest empty: the bound session has no file on disk. Session
        # discovery (discard_empty/list_sessions/continue_last) runs on
        # the USER store, never through this wrapper — verified callers.
        return []

    def latest(self) -> Session:
        raise SessionNotFound(self.sessions_dir)

    # ---------- writes ----------

    def create(self, session_id: str):
        self._require_session(session_id)
        # No file: the canonical session's content lives in the
        # timeline; the path is only a binding for Session.path.
        return self.path_for(session_id)

    # Append one canonical row (actor accepted for interface parity).
    def append(self, path, row, *, actor=None) -> None:
        import uuid

        self._require_path(path)
        self._timeline.append_message(
            self._window, row,
            event_id=f"msg-{self._session_id}-{uuid.uuid4().hex}",
            priv=self._priv())

    def rewrite(self, session_id, target, messages, title=None,
                title_source=None, naming_attempted=False, bots=None,
                dm=None) -> None:
        self._require_session(session_id)
        self._require_path(target)
        # Preserve metadata and record the reset as one journal transaction.
        # The caller owns the workspace lease across any earlier async
        # compaction work; this lock only makes the final RMW atomic.
        with self._timeline._lock:
            projected = self._projected()
            self._reset(
                messages, title=title, title_source=title_source,
                naming_attempted=bool(naming_attempted),
                bots=bots if bots is not None else projected.bots,
                dm=dm if dm is not None else projected.dm_bot)

    def delete(self, session_id: str) -> None:
        self._require_session(session_id)
        # Tombstone-by-reset: internal sessions aren't user-facing, so
        # deleting one clears the canonical view instead of unlinking.
        self._reset([], title=None, title_source=None,
                    naming_attempted=False, bots=None, dm=None)

    def append_meta(self, path, title, source=None) -> None:
        self._require_path(path)
        with self._timeline._lock:
            projected = self._projected()
            self._reset(
                projected.messages, title=title, title_source=source,
                naming_attempted=projected.naming_attempted,
                bots=projected.bots, dm=projected.dm_bot)

    # Store a canonical title when none exists (actor is interface parity).
    def append_title_if_missing(self, path, title, source=None, *, actor=None) -> bool:
        self._require_path(path)
        with self._timeline._lock:
            projected = self._projected()
            if projected.title:
                return False
            self._reset(
                projected.messages, title=title, title_source=source,
                naming_attempted=projected.naming_attempted,
                bots=projected.bots, dm=projected.dm_bot)
            return True

    # Mark the canonical naming attempt (actor is interface parity).
    def append_naming_attempted(self, path, *, actor=None) -> None:
        self._require_path(path)
        with self._timeline._lock:
            projected = self._projected()
            self._reset(
                projected.messages, title=projected.title,
                title_source=projected.title_source,
                naming_attempted=True,
                bots=projected.bots, dm=projected.dm_bot)

    def append_bots(self, path, bots) -> None:
        self._require_path(path)
        cleaned = [bot for bot in bots if isinstance(bot, str) and bot]
        if not cleaned:
            raise SessionError("bots must be a non-empty list of bot ids")
        with self._timeline._lock:
            projected = self._projected()
            self._reset(
                projected.messages, title=projected.title,
                title_source=projected.title_source,
                naming_attempted=projected.naming_attempted,
                bots=cleaned, dm=projected.dm_bot)

    def append_dm(self, path, bot_id) -> None:
        self._require_path(path)
        if not isinstance(bot_id, str) or not bot_id:
            raise SessionError("dm bot must be a non-empty bot id")
        with self._timeline._lock:
            projected = self._projected()
            self._reset(
                projected.messages, title=projected.title,
                title_source=projected.title_source,
                naming_attempted=projected.naming_attempted,
                bots=projected.bots, dm=bot_id)
