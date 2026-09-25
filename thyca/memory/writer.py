"""Markdown mutations for memory headings. No search/index."""
from __future__ import annotations

import os
import threading
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from thyca.config import atomic_write_text
from thyca.memory.archive_store import ArchiveError
from thyca.memory.heading import (
    TTL_DAYS,
    HeadingMeta,
    expiry_ts,
    is_expired,
    is_visible,
    iter_session_blocks,
    render_heading,
)


class MemoryWriter:
    _locks: dict[str, threading.RLock] = {}
    _guard = threading.Lock()
    _mutation_lock = threading.RLock()

    def __init__(self, thyca_dir: Path) -> None:
        self.thyca_dir = thyca_dir

    def lock_for(self, path: Path) -> threading.RLock:
        key = str(path.resolve())
        with MemoryWriter._guard:
            return MemoryWriter._locks.setdefault(key, threading.RLock())

    def mutation_lock(self) -> threading.RLock:
        return MemoryWriter._mutation_lock

    def locate(self, session_id: str) -> tuple[Path, str]:
        if session_id.startswith("canonical#"):
            raise ArchiveError("cannot forget or reinforce SOUL/USER as a whole")
        if "#" not in session_id:
            raise ArchiveError(f"invalid session_id {session_id!r}")
        prefix, entry = session_id.split("#", 1)
        if prefix == "memory":
            raise ArchiveError("MEMORY.md is no longer supported")
        if len(prefix) == 10 and prefix[4] == "-" and prefix[7] == "-":
            return self.thyca_dir / "memory" / f"{prefix}.md", entry
        raise ArchiveError(f"invalid session_id {session_id!r}")

    def append(self, path: Path, text: str) -> None:
        with self.lock_for(path):
            with path.open("a", encoding="utf-8") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())

    def map_heading(self, path: Path, entry_id: str, mutate: Callable[[HeadingMeta], HeadingMeta]) -> HeadingMeta:
        with self.lock_for(path):
            if not path.is_file():
                raise ArchiveError(f"memory file missing: {path}")
            lines = _read_lines(path)
            found: HeadingMeta | None = None
            out: list[str] = []
            pos = 0
            for meta, resolved, start, end in iter_session_blocks(lines, str(path)):
                out.extend(lines[pos:start])
                pos = end
                if resolved != entry_id:
                    out.extend(lines[start:end])
                    continue
                if meta.entry_id is None:
                    meta = replace(meta, entry_id=resolved)
                found = mutate(meta)
                if found.entry_id is None:
                    found = replace(found, entry_id=resolved)
                out.append(render_heading(found))
                out.extend(lines[start + 1 : end])
                break  # duplicate ids: first match wins, the rest stay verbatim
            out.extend(lines[pos:])
            if found is None:
                raise ArchiveError(f"session not found: {entry_id}")
            _atomic_write(path, "".join(out))
            return found

    def forget(self, session_id: str, now: datetime | None = None) -> None:
        path, entry = self.locate(session_id)
        with self.lock_for(path):
            self._remove_session(path, entry)

    def update_session(
        self,
        session_id: str,
        *,
        topic: str | None = None,
        body_lines: list[str] | None = None,
        proj: str | None = None,
        chat: str | None = None,
        keep_details: bool = False,
    ) -> None:
        """Rewrite one session's title, body, and/or linking metadata in place.

        entry_id / importance / expires_at stay untouched — the id the index
        and callers hold never changes; only the visible text moves.
        proj/chat only change when the caller passes them. keep_details keeps
        the existing body lines after the summary line (summary-only update).
        """
        path, entry = self.locate(session_id)
        with self.lock_for(path):
            self._update_session(
                path, entry, topic=topic, body_lines=body_lines,
                proj=proj, chat=chat, keep_details=keep_details,
            )

    def _update_session(
        self,
        path: Path,
        entry_id: str,
        *,
        topic: str | None,
        body_lines: list[str] | None,
        proj: str | None = None,
        chat: str | None = None,
        keep_details: bool = False,
    ) -> None:
        if not path.is_file():
            raise ArchiveError(f"memory file missing: {path}")
        if topic is not None and not topic.strip():
            raise ArchiveError("topic must not be blank")
        if topic is None and body_lines is None and proj is None and chat is None:
            return
        lines = _read_lines(path)
        out: list[str] = []
        pos = 0
        found = False
        for meta, resolved, start, end in iter_session_blocks(lines, str(path)):
            out.extend(lines[pos:start])
            pos = end
            if resolved != entry_id:
                out.extend(lines[start:end])
                continue
            found = True
            new_meta = replace(
                meta,
                title=topic if topic is not None else meta.title,
                entry_id=meta.entry_id or resolved,
                proj=meta.proj if proj is None else proj,
                chat=meta.chat if chat is None else chat,
            )
            out.append(render_heading(new_meta))
            if body_lines is not None:
                merged = list(body_lines)
                if keep_details:
                    # Replace the old summary (first body line), keep the rest.
                    merged.extend(lines[start + 1 : end][1:])
                out.extend(
                    line if line.endswith("\n") else f"{line}\n" for line in merged
                )
            else:
                out.extend(lines[start + 1 : end])
            break  # duplicate ids: first match wins, the rest stay verbatim
        out.extend(lines[pos:])
        if not found:
            raise ArchiveError(f"session not found: {entry_id}")
        _atomic_write(path, "".join(out))

    def reinforce(
        self,
        session_id: str,
        importance: int | None = None,
        now: datetime | None = None,
    ) -> str:
        if importance is not None and importance not in TTL_DAYS:
            raise ArchiveError(f"importance must be 1..5, got {importance}")
        path, entry = self.locate(session_id)

        def touch(meta: HeadingMeta) -> HeadingMeta:
            imp = importance if importance is not None else meta.importance
            return replace(
                meta, importance=imp, expires_at=expiry_ts(imp, now)
            )

        with self.lock_for(path):
            return self.map_heading(path, entry, touch).expires_at or ""

    def read_session(self, session_id: str, now: datetime | None = None) -> str:
        path, entry = self.locate(session_id)
        if not path.is_file():
            raise ArchiveError(f"session not found: {session_id}")
        lines = _read_lines(path)
        for _meta, resolved, start, end in iter_session_blocks(lines, str(path)):
            if resolved != entry:
                continue
            if not is_visible(_meta.expires_at, now):
                raise ArchiveError(f"session not found: {session_id}")
            return "".join(lines[start:end])
        raise ArchiveError(f"session not found: {session_id}")

    def purge_expired(self, now: datetime) -> None:
        memory_dir = self.thyca_dir / "memory"
        dailies = sorted(memory_dir.glob("????-??-??.md")) if memory_dir.is_dir() else []
        for path in dailies:
            if not path.is_file() or path.is_symlink():
                continue
            with self.lock_for(path):
                self._purge(path, now)

    def _remove_session(self, path: Path, entry_id: str) -> None:
        if not path.is_file():
            raise ArchiveError(f"memory file missing: {path}")
        lines = _read_lines(path)
        out: list[str] = []
        pos = 0
        found = False
        for _meta, resolved, start, end in iter_session_blocks(lines, str(path)):
            out.extend(lines[pos:start])
            pos = end
            if resolved == entry_id and not found:
                found = True
                continue  # duplicate ids: first match wins, the rest stay
            out.extend(lines[start:end])
        out.extend(lines[pos:])
        if not found:
            raise ArchiveError(f"session not found: {entry_id}")
        _atomic_write(path, "".join(out))

    def _purge(self, path: Path, now: datetime) -> None:
        lines = _read_lines(path)
        out: list[str] = []
        pos = 0
        for meta, _resolved, start, end in iter_session_blocks(lines, str(path)):
            out.extend(lines[pos:start])
            pos = end
            if is_expired(meta.expires_at, now):
                continue
            out.extend(lines[start:end])
        out.extend(lines[pos:])
        _atomic_write(path, "".join(out))


def _read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines(keepends=True)
    except UnicodeDecodeError as exc:
        raise ArchiveError(f"memory file is not valid UTF-8: {path}") from exc


def _atomic_write(path: Path, text: str) -> None:
    # The one atomic write lives in config.store (X7); this stays as the
    # monkeypatch seam the lifecycle tests pin.
    atomic_write_text(path, text)
