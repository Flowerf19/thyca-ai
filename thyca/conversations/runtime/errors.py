from __future__ import annotations

from pathlib import Path


class SessionError(RuntimeError):
    """Base error for session operations."""


class SessionNotFound(SessionError):
    def __init__(self, path: str | Path, msg: str | None = None) -> None:
        self.path = Path(path)
        super().__init__(msg or f"session not found: {self.path}")


class SessionBusy(SessionError):
    """A turn is already running for this session.

    One turn per session at a time: two turns on the same transcript would
    interleave appends and corrupt the compaction baseline. Other sessions
    stay free to run their own turns.
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        super().__init__(f"session busy: {session_id}")


class SessionCorrupt(SessionError):
    def __init__(self, path: str | Path, line: int | None, cause: str | Exception) -> None:
        self.path = Path(path)
        self.line = line
        self.cause = cause
        suffix = f":{line}" if line is not None else ""
        super().__init__(f"{self.path}{suffix}: {cause}")
