from __future__ import annotations

from pathlib import Path


class SessionError(RuntimeError):
    """Base error for session operations."""


class SessionNotFound(SessionError):
    def __init__(self, path: str | Path, msg: str | None = None) -> None:
        self.path = Path(path)
        super().__init__(msg or f"session not found: {self.path}")


class SessionCorrupt(SessionError):
    def __init__(self, path: str | Path, line: int | None, cause: str | Exception) -> None:
        self.path, self.line, self.cause = Path(path), line, cause
        suffix = f":{line}" if line is not None else ""
        super().__init__(f"{self.path}{suffix}: {cause}")
