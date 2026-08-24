from __future__ import annotations

import json
import os
import re
import secrets
from pathlib import Path

from thyca.protocol import Message

from .errors import SessionCorrupt, SessionError, SessionNotFound
from .models import Session

_ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}_[0-9a-f]{4}$")


class SessionStore:
    """Durable JSONL I/O. No compaction policy."""

    def __init__(self, sessions_dir: Path) -> None:
        self.sessions_dir = sessions_dir

    def ensure_dir(self) -> None:
        try:
            self.sessions_dir.mkdir(parents=True, exist_ok=True)
            self.sessions_dir.chmod(0o700)
        except OSError as exc:
            raise SessionError(f"cannot secure {self.sessions_dir}: {exc}") from exc

    def path_for(self, session_id: str) -> Path:
        if not isinstance(session_id, str) or not _ID_RE.fullmatch(session_id):
            raise SessionNotFound(session_id, f"invalid session id: {session_id!r}")
        root = self.sessions_dir.resolve()
        candidate = (self.sessions_dir / f"{session_id}.jsonl").resolve()
        if candidate.parent != root:
            raise SessionNotFound(candidate, "session id traversal blocked")
        return self.sessions_dir / f"{session_id}.jsonl"

    def create(self, session_id: str) -> Path:
        path = self.path_for(session_id)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
        except FileExistsError:
            raise
        except OSError as exc:
            raise SessionError(f"cannot create session: {exc}") from exc
        self._chmod_best_effort(path)
        return path

    def delete(self, session_id: str) -> None:
        path = self.path_for(session_id)
        try:
            path.unlink()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise SessionError(f"cannot delete session: {exc}") from exc

    def read(self, path: Path) -> list[Message]:
        return self.scan(path)[0]

    def scan(self, path: Path) -> tuple[list[Message], str | None]:
        result: list[Message] = []
        title: str | None = None
        known_calls: set[str] = set()
        try:
            with path.open("r", encoding="utf-8") as stream:
                for number, raw_line in enumerate(stream, 1):
                    line = raw_line.rstrip("\n")
                    if not line.strip():
                        raise SessionCorrupt(path, number, "empty line")
                    try:
                        payload = json.loads(line)
                        if _is_meta(payload):
                            extracted = _meta_title(payload)
                            if extracted:
                                title = extracted
                            continue
                        msg = Message.from_dict(payload)
                        if msg.role == "system":
                            if result or not (msg.content or "").startswith("[compaction: "):
                                raise ValueError(
                                    "system messages are only synthetic compaction markers"
                                )
                        elif msg.role == "tool":
                            if not msg.tool_call_id or msg.tool_call_id not in known_calls:
                                raise ValueError(
                                    "role=tool requires a matching prior assistant tool_call"
                                )
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
        return result, title

    def load(self, session_id: str) -> Session:
        path = self.path_for(session_id)
        if not path.is_file() or path.is_symlink():
            raise SessionNotFound(path)
        messages, title = self.scan(path)
        return Session(session_id, path, messages, title)

    def list_paths(self) -> list[Path]:
        if not self.sessions_dir.is_dir():
            return []
        candidates = [
            path
            for path in self.sessions_dir.glob("*.jsonl")
            if path.is_file() and not path.is_symlink() and _ID_RE.fullmatch(path.stem)
        ]
        candidates.sort(key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)
        return candidates

    def latest(self) -> Session:
        if not self.sessions_dir.is_dir():
            raise SessionNotFound(self.sessions_dir)
        candidates = [
            path
            for path in self.sessions_dir.glob("*.jsonl")
            if path.is_file() and not path.is_symlink()
        ]
        if not candidates:
            raise SessionNotFound(self.sessions_dir)
        candidates.sort(key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)
        chosen = candidates[0]
        session_id = chosen.stem
        if not _ID_RE.fullmatch(session_id):
            raise SessionNotFound(chosen, "invalid session filename")
        messages, title = self.scan(chosen)
        return Session(session_id, chosen, messages, title)

    def append(self, path: Path, msg: Message) -> None:
        self._append_json(path, msg.to_canonical_dict())

    def append_meta(self, path: Path, title: str) -> None:
        self._append_json(path, {"type": "meta", "title": title})

    def _append_json(self, path: Path, payload: dict) -> None:
        try:
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as exc:
            raise SessionError(f"append failed: {exc}") from exc
        self._chmod_best_effort(path)

    def rewrite(
        self,
        session_id: str,
        target: Path,
        messages: list[Message],
        title: str | None = None,
    ) -> None:
        path = self.path_for(session_id)
        if path.resolve() != target.resolve():
            raise SessionError("rewrite target does not match session id")
        tmp = self.sessions_dir / f".{path.stem}.tmp.{secrets.token_hex(4)}"
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    if title:
                        stream.write(
                            json.dumps({"type": "meta", "title": title}, ensure_ascii=False)
                            + "\n"
                        )
                    for msg in messages:
                        stream.write(json.dumps(msg.to_canonical_dict(), ensure_ascii=False) + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
            except Exception:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
                raise
            self._chmod_best_effort(tmp)
            os.replace(tmp, path)
        except OSError as exc:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise SessionError(f"compaction rewrite failed: {exc}") from exc
        self._fsync_dir_best_effort()
        self._chmod_best_effort(path)

    def _chmod_best_effort(self, path: Path) -> None:
        try:
            path.chmod(0o600)
        except OSError:
            pass

    def _fsync_dir_best_effort(self) -> None:
        try:
            parent_fd = os.open(self.sessions_dir, os.O_RDONLY | os.O_DIRECTORY)
        except OSError:
            return
        try:
            os.fsync(parent_fd)
        except OSError:
            pass
        finally:
            os.close(parent_fd)


def _is_meta(payload: object) -> bool:
    return isinstance(payload, dict) and payload.get("type") == "meta" and "role" not in payload


def _meta_title(payload: dict) -> str | None:
    raw = payload.get("title")
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    return text or None
