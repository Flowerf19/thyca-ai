from __future__ import annotations

import json
import os
import re
import secrets
from pathlib import Path

from thyca.core.protocol import Message

from .errors import SessionCorrupt, SessionError, SessionNotFound
from .models import Session

#: The one session-id grammar: timestamp + 4 hex. Serve route regexes compose
#: from this so the grammar cannot drift between layers.
SESSION_ID_PATTERN = r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}_[0-9a-f]{4}"
_ID_RE = re.compile(rf"^{SESSION_ID_PATTERN}$")


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

    def read_title(self, path: Path) -> tuple[str, str | None] | None:
        """Last meta line's ``(title, source)``, read from the end of the file.

        The naming step needs only the title, and a full ``scan`` parses the
        whole transcript (tens of milliseconds on a long session). Meta lines
        are appended, so the last one wins — walking backwards from the tail
        stops after the first match.

        Returns None when the file has no meta line (or cannot be read), which
        callers treat as "leave the in-memory title alone".
        """
        try:
            with path.open("rb") as stream:
                for raw in _lines_backwards(stream):
                    if b'"type"' not in raw or b"title" not in raw:
                        continue
                    try:
                        payload = json.loads(raw)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    if not _is_meta(payload):
                        continue
                    title = _meta_title(payload)
                    if title:
                        return title, _meta_source(payload)
        except OSError:
            return None
        return None

    def scan(self, path: Path) -> tuple[list[Message], str | None, str | None]:
        result: list[Message] = []
        title: str | None = None
        title_source: str | None = None
        known_calls: set[str] = set()
        try:
            with path.open("r", encoding="utf-8") as stream:
                for number, raw_line in enumerate(stream, 1):
                    line = raw_line.rstrip("\n")
                    if not line.strip():
                        # Blank lines carry no information: skip so sessions
                        # self-heal instead of bricking the whole transcript.
                        continue
                    try:
                        payload = json.loads(line)
                        if _is_meta(payload):
                            extracted = _meta_title(payload)
                            if extracted:
                                title = extracted
                                title_source = _meta_source(payload)
                            continue
                        msg = Message.from_dict(payload)
                        if msg.role == "system" and (
                            result or not (msg.content or "").startswith("[compaction: ")
                        ):
                            raise ValueError(
                                "system messages are only synthetic compaction markers"
                            )
                        if msg.role == "tool" and (
                            not msg.tool_call_id or msg.tool_call_id not in known_calls
                        ):
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
        except UnicodeDecodeError as exc:
            raise SessionCorrupt(path, None, "invalid UTF-8") from exc
        except FileNotFoundError as exc:
            raise SessionNotFound(path) from exc
        except OSError as exc:
            raise SessionCorrupt(path, None, exc) from exc
        return result, title, title_source

    def load(self, session_id: str) -> Session:
        path = self.path_for(session_id)
        if not path.is_file() or path.is_symlink():
            raise SessionNotFound(path)
        messages, title, title_source = self.scan(path)
        return Session(session_id, path, messages, title, title_source)

    def list_paths(self) -> list[Path]:
        if not self.sessions_dir.is_dir():
            return []
        candidates = [
            path
            for path in self.sessions_dir.glob("*.jsonl")
            if path.is_file() and not path.is_symlink() and _ID_RE.fullmatch(path.stem)
        ]
        candidates.sort(key=lambda path: (_mtime_ns(path), path.name), reverse=True)
        return candidates

    def latest(self) -> Session:
        candidates = self.list_paths()
        if not candidates:
            raise SessionNotFound(self.sessions_dir)
        for chosen in candidates:
            session_id = chosen.stem
            try:
                messages, title, title_source = self.scan(chosen)
            except (SessionCorrupt, SessionNotFound):
                continue
            return Session(session_id, chosen, messages, title, title_source)
        raise SessionNotFound(self.sessions_dir, "no valid sessions")

    def append(self, path: Path, msg: Message) -> None:
        self._append_json(path, msg.to_canonical_dict())

    def append_meta(self, path: Path, title: str, source: str | None = None) -> None:
        self._append_json(path, _meta_payload(title, source))

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
        title_source: str | None = None,
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
                        meta = _meta_payload(title, title_source)
                        stream.write(
                            json.dumps(meta, ensure_ascii=False) + "\n"
                        )
                    for msg in messages:
                        stream.write(json.dumps(msg.to_canonical_dict(), ensure_ascii=False) + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
            except (TypeError, ValueError) as exc:
                raise SessionError(f"compaction rewrite failed: {exc}") from exc
            self._chmod_best_effort(tmp)
            os.replace(tmp, path)
        except OSError as exc:
            raise SessionError(f"compaction rewrite failed: {exc}") from exc
        finally:
            # One cleanup path: a failed write never leaves its tmp behind
            # (on success the rename already moved it away, so this no-ops).
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
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


def _mtime_ns(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return -1


def _lines_backwards(stream, chunk_size: int = 8192):
    """Yield the file's lines newest-first without loading it whole."""
    stream.seek(0, os.SEEK_END)
    position = stream.tell()
    pending = b""
    while position > 0:
        read_size = min(chunk_size, position)
        position -= read_size
        stream.seek(position)
        pending = stream.read(read_size) + pending
        parts = pending.split(b"\n")
        pending = parts[0]
        for line in reversed(parts[1:]):
            if line.strip():
                yield line
    if pending.strip():
        yield pending


def _meta_payload(title: str, source: str | None) -> dict:
    """The one meta-line builder shared by append and rewrite."""
    payload: dict = {"type": "meta", "title": title}
    if source:
        payload["source"] = source
    return payload


def _is_meta(payload: object) -> bool:
    return isinstance(payload, dict) and payload.get("type") == "meta" and "role" not in payload


def _meta_title(payload: dict) -> str | None:
    raw = payload.get("title")
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    return text or None


def _meta_source(payload: dict) -> str | None:
    raw = payload.get("source")
    return raw.strip() if isinstance(raw, str) and raw.strip() else None
