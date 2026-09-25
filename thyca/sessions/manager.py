from __future__ import annotations

import secrets
import threading
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from thyca.config import DEFAULT_TIMELINE_TIMEZONE, LimitsCfg
from thyca.core.protocol import Message

from .compaction import SessionCompactor
from .errors import SessionBusy, SessionError
from .models import Session
from .store import SessionStore
from .title import USER_TITLE_SOURCE, is_blank, sanitize_title, sanitize_user_title


def _last_user_index(messages: list[Message]) -> int | None:
    """Index of the last user message, or None when there is none.

    The one reverse scan shared by ``truncate_to_last_user`` and
    ``mark_turn_error``."""
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].role == "user":
            return index
    return None


class SessionManager:
    """Orchestrate store + compactor behind a single-process lock."""

    def __init__(
        self,
        sessions_dir: Path | None = None,
        limits: LimitsCfg | None = None,
        timezone_name: str | None = None,
        store: SessionStore | None = None,
        compactor: SessionCompactor | None = None,
    ) -> None:
        self.limits = limits or LimitsCfg()
        self.timezone_name = timezone_name or DEFAULT_TIMELINE_TIMEZONE
        self.store = store or SessionStore(
            Path(sessions_dir or Path.home() / ".thyca" / "sessions")
        )
        self.sessions_dir = self.store.sessions_dir
        self.compactor = compactor or SessionCompactor()
        self._lock = threading.Lock()
        self._session: Session | None = None

    def _current_locked(self) -> Session:
        """Return the current session. The caller must hold ``self._lock``."""
        if self._session is None:
            raise SessionError("no current session — call create/load/continue_last first")
        return self._session

    @property
    def current(self) -> Session:
        with self._lock:
            return self._current_locked()

    def _new_id(self) -> str:
        try:
            zone = ZoneInfo(self.timezone_name)
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            zone = ZoneInfo(DEFAULT_TIMELINE_TIMEZONE)
        timestamp = datetime.now(zone).strftime("%Y-%m-%dT%H-%M-%S")
        return f"{timestamp}_{secrets.token_hex(2)}"

    def create(self, *, make_current: bool = True) -> Session:
        with self._lock:
            self.store.ensure_dir()
            for _ in range(10):
                session_id = self._new_id()
                try:
                    path = self.store.create(session_id)
                except FileExistsError:
                    continue
                session = Session(session_id, path, [])
                if make_current:
                    self._session = session
                return session
            raise SessionError("session filename collision")

    def load(self, session_id: str) -> Session:
        with self._lock:
            session = self.store.load(session_id)
            self._session = session
            return session

    def continue_last(self) -> Session:
        with self._lock:
            session = self.store.latest()
            self._session = session
            return session

    def list_sessions(self) -> list[Session]:
        with self._lock:
            sessions: list[Session] = []
            for path in self.store.list_paths():
                try:
                    sessions.append(self.store.load(path.stem))
                except SessionError:
                    continue
            return sessions

    def discard_empty(self, keep: set[str] | None = None) -> list[str]:
        """Drop blank sessions except the ones named in ``keep``.

        ``keep`` is every session with an in-flight turn: a turn writes its
        first message a moment after it starts, so an empty file may still
        belong to a running turn.
        """
        with self._lock:
            removed: list[str] = []
            for path in self.store.list_paths():
                try:
                    session = self.store.load(path.stem)
                except SessionError:
                    continue
                if keep and session.id in keep:
                    continue
                if not is_blank(session):
                    continue
                self.store.delete(session.id)
                if self._session is not None and self._session.id == session.id:
                    self._session = None
                removed.append(session.id)
            return removed

    def append(self, msg: Message) -> None:
        with self._lock:
            session = self._current_locked()
            self.store.append(session.path, msg)
            session.messages.append(msg)

    def truncate_to_last_user(self) -> bool:
        """Drop messages after the last user. No-op if the tail is already a user.

        A stale failure marker on the kept user message is stripped: a retry
        starts clean, and re-marks itself only if it fails again.
        Returns False when the transcript has no ``role=user`` message.
        """
        with self._lock:
            session = self._current_locked()
            messages = session.messages
            last = _last_user_index(messages)
            if last is None:
                return False
            stripped = False
            if (messages[last].meta or {}).get("error") is not None:
                kept_user = messages[last]
                cleaned = {
                    key: value
                    for key, value in (kept_user.meta or {}).items()
                    if key != "error"
                }
                messages[last] = replace(kept_user, meta=cleaned or None)
                stripped = True
            if last == len(messages) - 1 and not stripped:
                return True
            kept = messages[: last + 1]
            self.store.rewrite(
                session.id,
                session.path,
                kept,
                title=session.title,
                title_source=session.title_source,
            )
            session.messages[:] = kept
            return True

    def mark_turn_error(self, code: str, message: str) -> bool:
        """Stamp the turn's user message with the failure and persist it.

        The marker is what Trace shows for a failed turn; the live chat keeps
        using the stream terminal. Returns False when there is no user
        message to mark. Disk failures raise SessionError — callers that must
        not mask the original error guard this call.
        """
        with self._lock:
            session = self._current_locked()
            messages = session.messages
            last = _last_user_index(messages)
            if last is None:
                return False
            marked = dict(messages[last].meta or {})
            marked["error"] = {"code": code, "message": message}
            messages[last] = replace(messages[last], meta=marked)
            self.store.rewrite(
                session.id,
                session.path,
                messages,
                title=session.title,
                title_source=session.title_source,
            )
            return True

    def compact_if_needed(self) -> bool:
        with self._lock:
            session = self._current_locked()
            on_disk, title, title_source = self.store.scan(session.path)
            session.messages[:] = on_disk
            if title:
                session.title = title
                session.title_source = title_source
            compacted = self.compactor.compact(on_disk, self.limits.contextTokens)
            if compacted is None:
                return False
            self.store.rewrite(
                session.id,
                session.path,
                compacted,
                title=session.title,
                title_source=session.title_source,
            )
            session.messages[:] = compacted
            return True

    def refresh_title(self) -> None:
        """Re-read the title a stored meta line carries.

        A turn holds its own ``Session`` snapshot from load time; a title the
        user typed in the meantime is on disk only. Without this the agent's
        naming step would append its own meta line over the user's name.
        """
        with self._lock:
            if self._session is None:
                return
            found = self.store.read_title(self._session.path)
            if found is None:
                return
            title, title_source = found
            if title:
                self._session.title = title
                self._session.title_source = title_source

    def set_title(self, title: str, *, source: str | None = None) -> str | None:
        with self._lock:
            session = self._current_locked()
            cleaned = (
                sanitize_user_title(title)
                if source == USER_TITLE_SOURCE
                else sanitize_title(title)
            )
            if cleaned is None:
                return None
            self.store.append_meta(session.path, cleaned, source)
            session.title = cleaned
            session.title_source = source
            return cleaned

    def rename(self, session_id: str, title: str) -> str:
        """Set the title of any stored session, not only the current one."""
        cleaned = sanitize_user_title(title)
        if cleaned is None:
            raise ValueError("empty title")
        with self._lock:
            session = self.store.load(session_id)
            self.store.append_meta(session.path, cleaned, USER_TITLE_SOURCE)
            session.title = cleaned
            session.title_source = USER_TITLE_SOURCE
            if self._session is not None and self._session.id == session_id:
                self._session.title = cleaned
                self._session.title_source = USER_TITLE_SOURCE
            return cleaned

    def delete(self, session_id: str, *, keep: set[str] | None = None) -> None:
        """Remove a session file outright.

        ``keep`` names sessions with a turn in flight. The caller must pass a
        snapshot that cannot go stale between the check and the unlink — i.e.
        hold the same lock a turn claims under — or a claim landing in that
        window leaves the writer appending to a deleted path, which re-creates
        the file truncated on its next write.
        """
        with self._lock:
            if keep and session_id in keep:
                raise SessionBusy(session_id)
            self.store.delete(session_id)
            if self._session is not None and self._session.id == session_id:
                self._session = None
