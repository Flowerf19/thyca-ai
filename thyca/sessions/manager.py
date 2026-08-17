from __future__ import annotations

import json
import os
import re
import secrets
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from thyca.config import DEFAULT_TIMELINE_TIMEZONE, LimitsCfg
from thyca.protocol import Message

from .errors import SessionCorrupt, SessionError, SessionNotFound
from .models import Session

_ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}_[0-9a-f]{4}$")


def _secure_dir(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o700)
    except OSError as exc:
        raise SessionError(f"cannot secure {path}: {exc}") from exc


def _timestamp(timezone_name: str) -> str:
    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        zone = ZoneInfo(DEFAULT_TIMELINE_TIMEZONE)
    return datetime.now(zone).strftime("%Y-%m-%dT%H-%M-%S")


def _read(path: Path) -> list[Message]:
    result: list[Message] = []
    known_calls: set[str] = set()
    try:
        with path.open("r", encoding="utf-8") as stream:
            for number, raw_line in enumerate(stream, 1):
                line = raw_line.rstrip("\n")
                if not line.strip():
                    raise SessionCorrupt(path, number, "empty line")
                try:
                    raw = json.loads(line)
                    msg = Message.from_dict(raw)
                    if msg.role == "system":
                        if number != 1 or not (msg.content or "").startswith("[compaction: "):
                            raise ValueError("system messages are only synthetic compaction markers")
                    elif msg.role == "tool":
                        if not msg.tool_call_id or msg.tool_call_id not in known_calls:
                            raise ValueError("role=tool requires a matching prior assistant tool_call")
                    if msg.role == "assistant" and msg.tool_calls:
                        ids = [call.id for call in msg.tool_calls]
                        if len(ids) != len(set(ids)):
                            raise ValueError("assistant tool_call ids must be unique")
                        known_calls.update(ids)
                except SessionCorrupt:
                    raise
                except (json.JSONDecodeError, ValueError, TypeError) as exc:
                    raise SessionCorrupt(path, number, exc) from exc
                result.append(msg)
    except SessionCorrupt:
        raise
    except OSError as exc:
        raise SessionCorrupt(path, None, exc) from exc
    return result


def estimate_tokens(msg: Message) -> int:
    value = json.dumps(msg.to_canonical_dict(), ensure_ascii=False)
    return (len(value) + 3) // 4


def _turns(messages: list[Message]) -> list[list[Message]]:
    turns: list[list[Message]] = []
    current: list[Message] = []
    pending: set[str] = set()
    for msg in messages:
        current.append(msg)
        if msg.role == "assistant":
            pending.update(call.id for call in (msg.tool_calls or []))
        elif msg.role == "tool":
            pending.discard(msg.tool_call_id or "")
        if msg.role == "assistant" and not pending:
            turns.append(current)
            current = []
        elif msg.role == "tool" and not pending:
            turns.append(current)
            current = []
    if current:
        turns.append(current)
    return turns


def compact(messages: list[Message], context_tokens: int) -> list[Message] | None:
    if sum(estimate_tokens(msg) for msg in messages) <= context_tokens:
        return None
    turns = _turns(messages)
    budget = int(context_tokens * 0.6)
    kept: list[list[Message]] = []
    used = 0
    for turn in reversed(turns):
        cost = sum(estimate_tokens(msg) for msg in turn)
        if kept and used + cost > budget:
            break
        kept.append(turn)
        used += cost
    kept.reverse()
    tail = [msg for turn in kept for msg in turn]
    omitted = messages[: len(messages) - len(tail)] if tail else messages
    excerpt = "\n".join(msg.content for msg in omitted if msg.role in ("user", "assistant") and msg.content)
    marker = Message(role="system", content=f"[compaction: omitted {len(omitted)} messages/{len(turns)-len(kept)} turns; excerpt: {excerpt[:1000]}]")
    return [marker] + tail


def rewrite(sessions_dir: Path, session_id: str, target: Path, messages: list[Message]) -> None:
    tmp = sessions_dir / f".{session_id}.tmp.{secrets.token_hex(4)}"
    try:
        with tmp.open("w", encoding="utf-8") as stream:
            for msg in messages:
                stream.write(json.dumps(msg.to_canonical_dict(), ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        tmp.chmod(0o600)
        os.replace(tmp, target)
        parent_fd = os.open(sessions_dir, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        target.chmod(0o600)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise SessionError(f"compaction rewrite failed: {exc}") from exc


class SessionManager:
    """Coordinate session state and persistence."""

    def __init__(self, sessions_dir: Path | None = None, limits: LimitsCfg | None = None,
                 timezone_name: str | None = None) -> None:
        self.sessions_dir = Path(sessions_dir or Path.home() / ".thyca" / "sessions")
        self.limits = limits or LimitsCfg()
        self.timezone_name = timezone_name or DEFAULT_TIMELINE_TIMEZONE
        self._lock = threading.Lock()
        self._current_id: str | None = None
        self._current_path: Path | None = None

    def _path(self, session_id: str) -> Path:
        if not isinstance(session_id, str) or not _ID_RE.fullmatch(session_id):
            raise SessionNotFound(session_id, f"invalid session id: {session_id!r}")
        root = self.sessions_dir.resolve()
        candidate = (self.sessions_dir / f"{session_id}.jsonl").resolve()
        if candidate.parent != root:
            raise SessionNotFound(candidate, "session id traversal blocked")
        return self.sessions_dir / f"{session_id}.jsonl"

    def _load_unlocked(self, session_id: str) -> Session:
        path = self._path(session_id)
        if not path.is_file() or path.is_symlink():
            raise SessionNotFound(path)
        return Session(session_id, path, _read(path))

    def create(self) -> Session:
        with self._lock:
            _secure_dir(self.sessions_dir)
            timestamp = _timestamp(self.timezone_name)
            for _ in range(10):
                session_id = f"{timestamp}_{secrets.token_hex(2)}"
                path = self.sessions_dir / f"{session_id}.jsonl"
                try:
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    os.close(fd)
                    path.chmod(0o600)
                    session = Session(session_id, path, [])
                    self._current_id, self._current_path = session.id, session.path
                    return session
                except FileExistsError:
                    continue
                except OSError as exc:
                    raise SessionError(f"cannot create session: {exc}") from exc
            raise SessionError("session filename collision")

    def load(self, session_id: str) -> Session:
        with self._lock:
            session = self._load_unlocked(session_id)
            self._current_id, self._current_path = session.id, session.path
            return session

    def continue_last(self) -> Session:
        with self._lock:
            if not self.sessions_dir.is_dir():
                raise SessionNotFound(self.sessions_dir)
            candidates = [p for p in self.sessions_dir.glob("*.jsonl") if p.is_file() and not p.is_symlink()]
            if not candidates:
                raise SessionNotFound(self.sessions_dir)
            candidates.sort(key=lambda p: (p.stat().st_mtime_ns, p.name), reverse=True)
            chosen = candidates[0]
            session_id = chosen.stem
            if not _ID_RE.fullmatch(session_id):
                raise SessionNotFound(chosen, "invalid session filename")
            session = Session(session_id, chosen, _read(chosen))
            self._current_id, self._current_path = session.id, session.path
            return session

    def append(self, msg: Message) -> None:
        with self._lock:
            if self._current_path is None:
                raise SessionError("no current session — call create/load/continue_last first")
            try:
                with self._current_path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(msg.to_canonical_dict(), ensure_ascii=False) + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                self._current_path.chmod(0o600)
            except OSError as exc:
                raise SessionError(f"append failed: {exc}") from exc

    def compact_if_needed(self) -> bool:
        with self._lock:
            if self._current_path is None or self._current_id is None:
                raise SessionError("no current session — call create/load/continue_last first")
            messages = self._load_unlocked(self._current_id).messages
            compacted = compact(messages, self.limits.contextTokens)
            if compacted is None:
                return False
            rewrite(self.sessions_dir, self._current_id, self._current_path, compacted)
            return True
