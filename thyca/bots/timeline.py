"""Canonical per-bot workspace timeline: the ONLY execution store.

Durable JSONL under ``bots/<bot>/workspace/timeline.jsonl`` (+``.lock``),
all writes under one FileLock, complete newline-terminated records,
fsync'd, 0700 dir / 0600 file. Event kinds: ``message`` (one canonical
Message row) and ``view_reset`` (compaction/rewrite output — new basis).

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

from thyca.bots.workspace_store import _check_bot_id
from thyca.core.channel import WindowRef
from thyca.core.protocol import Message, utc_now_ts
from thyca.sessions import Session, SessionStore
from thyca.sessions.errors import SessionError, SessionNotFound

EVENT_KINDS = frozenset({"message", "view_reset"})


class TimelineError(RuntimeError):
    """Malformed/corrupt timeline storage — never self-heals."""


class TimelineConflict(TimelineError):
    """Same ``event_id`` replayed with a different payload."""


class TraceExecutionError(RuntimeError):
    """Canonical execution detail is unavailable for a trace span."""


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
        for line in raw.decode("utf-8").splitlines():
            try:
                ev = json.loads(line)
            except Exception as exc:
                raise TimelineError(f"malformed line: {exc}") from exc
            if not isinstance(ev, dict):
                raise TimelineError("event must be a dict")
            if ev.get("v") != 1:
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

    def _append_locked(self, kind: str, window: WindowRef,
                       payload: dict, event_id: str) -> dict:
        ev = {
            "v": 1,
            "seq": self._seq + 1,
            "event_id": event_id,
            "kind": kind,
            "bot_id": self.bot_id,
            "window": window.to_dict(),
            "payload": payload,
            "ts": utc_now_ts(),
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

    def messages_for_window(self, window: WindowRef) -> list[Message]:
        """Read original MESSAGE events for one exact window.

        Deliberately scans the journal rather than the current projection:
        view_reset copies are compaction bases, not execution events, and
        must neither hide original rows nor duplicate them.
        """
        messages: list[Message] = []
        for event in self._events():
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
        self, window: WindowRef, span_id: str
    ) -> list[Message]:
        """Read original MESSAGE events for one exact window/span."""
        if not isinstance(span_id, str) or not span_id:
            return []
        return [
            message for message in self.messages_for_window(window)
            if (message.meta or {}).get("span_id") == span_id
        ]

    def _find(self, event_id: str) -> dict | None:
        for ev in reversed(self._events()):
            if ev.get("event_id") == event_id:
                return ev
        return None

    # ---------- writers ----------

    def append_message(self, window: WindowRef, message,
                       event_id: str) -> dict:
        payload = {
            "message": (message.to_canonical_dict()
                        if hasattr(message, "to_canonical_dict")
                        else message),
        }
        return self._record("message", window, payload, event_id)

    def replace_view(self, window: WindowRef, messages, *,
                     event_id: str,
                     title: str | None = None, title_source: str | None = None,
                     naming_attempted: bool = False,
                     bots=None, dm=None) -> dict:
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
        return self._record("view_reset", window, payload, event_id)

    def _record(self, kind: str, window: WindowRef, payload: dict,
                event_id: str) -> dict:
        if not isinstance(event_id, str) or not event_id:
            raise TimelineError("event_id required")
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
                # Idempotency compares window_id, not the whole window
                # dict — participants/kind can legitimately drift for
                # the same window across a room's life.
                if (
                    existing["kind"] == kind
                    and existing["window"].get("window_id")
                    == window.window_id
                    and existing["payload"] == payload
                ):
                    return existing
                raise TimelineConflict(
                    f"event_id {event_id} replayed with different payload")
            return self._append_locked(kind, window, payload, event_id)

    # ---------- projector ----------

    def project(self, window: WindowRef, *,
                session_id: str | None = None) -> Session:
        """Project the requested window's execution view.

        Basis = last ``view_reset`` for that window; ``message`` events
        after the basis replay on top. No basis yet → every ``message``
        event for the window since the log start (a fresh window simply
        has none). DM vs group windows never mix; the requested
        ``WindowRef`` is the only scope consulted. The session path is a
        binding only — canonical content lives in the journal.
        """
        events = self._events()
        basis = None
        basis_idx = -1
        for i, ev in enumerate(events):
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
            if ev["window"].get("window_id") != window.window_id:
                continue
            if ev["kind"] == "message":
                raw = ev["payload"].get("message")
                messages.append(
                    Message.from_dict(raw) if hasattr(Message, "from_dict")
                    else raw)
        sid = session_id or window.window_id
        path = SessionStore(
            self.root / "bots" / self.bot_id / "sessions").path_for(sid)
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

    def __init__(self, sessions_dir: Path, *,
                 timeline: WorkspaceTimeline, window: WindowRef,
                 session_id: str) -> None:
        super().__init__(sessions_dir)
        self._timeline = timeline
        self._window = window
        self._session_id = session_id

    # ---------- projection helpers ----------

    def _require_session(self, session_id: str) -> None:
        if session_id != self._session_id:
            raise TimelineError(
                f"canonical store is bound to {self._session_id}, not {session_id}")

    def _require_path(self, path) -> None:
        self._require_session(Path(path).stem)

    def _projected(self) -> Session:
        return self._timeline.project(
            self._window, session_id=self._session_id)

    def _reset(self, messages, *, title, title_source,
               naming_attempted, bots, dm) -> None:
        import uuid

        self._timeline.replace_view(
            self._window, messages, event_id=f"reset-{uuid.uuid4().hex}",
            title=title, title_source=title_source,
            naming_attempted=naming_attempted, bots=bots, dm=dm)

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

    def append(self, path, row) -> None:
        import uuid

        self._require_path(path)
        self._timeline.append_message(
            self._window, row,
            event_id=f"msg-{self._session_id}-{uuid.uuid4().hex}")

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

    def append_title_if_missing(self, path, title, source=None) -> bool:
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

    def append_naming_attempted(self, path) -> None:
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
