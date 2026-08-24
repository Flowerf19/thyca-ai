from __future__ import annotations

import secrets
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from thyca.config import DEFAULT_TIMELINE_TIMEZONE, LimitsCfg
from thyca.protocol import Message

from .compaction import SessionCompactor
from .errors import SessionCorrupt, SessionError, SessionNotFound
from .models import Session
from .store import SessionStore
from .title import sanitize_title


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

    @property
    def current(self) -> Session:
        with self._lock:
            if self._session is None:
                raise SessionError("no current session — call create/load/continue_last first")
            return self._session

    def _new_id(self) -> str:
        try:
            zone = ZoneInfo(self.timezone_name)
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            zone = ZoneInfo(DEFAULT_TIMELINE_TIMEZONE)
        timestamp = datetime.now(zone).strftime("%Y-%m-%dT%H-%M-%S")
        return f"{timestamp}_{secrets.token_hex(2)}"

    def create(self) -> Session:
        with self._lock:
            self.store.ensure_dir()
            for _ in range(10):
                session_id = self._new_id()
                try:
                    path = self.store.create(session_id)
                except FileExistsError:
                    continue
                session = Session(session_id, path, [])
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
                except (SessionCorrupt, SessionNotFound, SessionError):
                    continue
            return sessions

    def append(self, msg: Message) -> None:
        with self._lock:
            if self._session is None:
                raise SessionError("no current session — call create/load/continue_last first")
            self.store.append(self._session.path, msg)
            self._session.messages.append(msg)

    def compact_if_needed(self) -> bool:
        with self._lock:
            if self._session is None:
                raise SessionError("no current session — call create/load/continue_last first")
            on_disk, title = self.store.scan(self._session.path)
            self._session.messages[:] = on_disk
            if title:
                self._session.title = title
            compacted = self.compactor.compact(on_disk, self.limits.contextTokens)
            if compacted is None:
                return False
            self.store.rewrite(
                self._session.id,
                self._session.path,
                compacted,
                title=self._session.title,
            )
            self._session.messages[:] = compacted
            return True

    def set_title(self, title: str) -> str | None:
        with self._lock:
            if self._session is None:
                raise SessionError("no current session — call create/load/continue_last first")
            cleaned = sanitize_title(title)
            if cleaned is None:
                return None
            self.store.append_meta(self._session.path, cleaned)
            self._session.title = cleaned
            return cleaned
