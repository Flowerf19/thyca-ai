"""Markdown mutations for memory headings. No search/index."""
from __future__ import annotations

import os
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from thyca.memory.archived import ArchiveError
from thyca.memory.heading import (
    HeadingMeta,
    expiry_ts,
    is_expired,
    is_visible,
    parse_heading,
    render_heading,
    resolve_entry_id,
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
            lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
            found: HeadingMeta | None = None
            out: list[str] = []
            seen: dict[str, int] = {}
            for line in lines:
                meta = parse_heading(line)
                if meta is None:
                    out.append(line)
                    continue
                seen[meta.title] = seen.get(meta.title, 0) + 1
                resolved = resolve_entry_id(meta, str(path), seen[meta.title])
                if resolved != entry_id:
                    out.append(line)
                    continue
                if meta.entry_id is None:
                    meta = HeadingMeta(meta.time, meta.title, resolved, meta.importance, meta.expires_at)
                found = mutate(meta)
                if found.entry_id is None:
                    found = HeadingMeta(found.time, found.title, resolved, found.importance, found.expires_at)
                out.append(render_heading(found))
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
    ) -> None:
        """Rewrite one session's title, body, and/or linking metadata in place.

        entry_id / importance / expires_at stay untouched — the id the index
        and callers hold never changes; only the visible text moves.
        proj/chat only change when the caller passes them.
        """
        path, entry = self.locate(session_id)
        with self.lock_for(path):
            self._update_session(
                path, entry, topic=topic, body_lines=body_lines,
                proj=proj, chat=chat,
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
    ) -> None:
        if not path.is_file():
            raise ArchiveError(f"memory file missing: {path}")
        if topic is None and body_lines is None and proj is None and chat is None:
            return
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        out: list[str] = []
        index = 0
        found = False
        seen: dict[str, int] = {}
        while index < len(lines):
            meta = parse_heading(lines[index])
            if meta is None:
                out.append(lines[index])
                index += 1
                continue
            seen[meta.title] = seen.get(meta.title, 0) + 1
            end = index + 1
            while end < len(lines) and parse_heading(lines[end]) is None:
                end += 1
            resolved = resolve_entry_id(meta, str(path), seen[meta.title])
            if resolved == entry_id:
                found = True
                new_meta = HeadingMeta(
                    meta.time,
                    topic if topic is not None else meta.title,
                    meta.entry_id or resolved,
                    meta.importance,
                    meta.expires_at,
                    meta.proj if proj is None else proj,
                    meta.chat if chat is None else chat,
                )
                out.append(render_heading(new_meta))
                if body_lines is not None:
                    out.extend(
                        line if line.endswith("\n") else f"{line}\n" for line in body_lines
                    )
                else:
                    out.extend(lines[index + 1 : end])
                index = end
                continue
            out.extend(lines[index:end])
            index = end
        if not found:
            raise ArchiveError(f"session not found: {entry_id}")
        _atomic_write(path, "".join(out))

    def reinforce(
        self,
        session_id: str,
        importance: int | None = None,
        now: datetime | None = None,
    ) -> str:
        path, entry = self.locate(session_id)

        def touch(meta: HeadingMeta) -> HeadingMeta:
            imp = importance if importance is not None else meta.importance
            return HeadingMeta(meta.time, meta.title, meta.entry_id, imp, expiry_ts(imp, now))

        with self.lock_for(path):
            return self.map_heading(path, entry, touch).expires_at or ""

    def read_session(self, session_id: str, now: datetime | None = None) -> str:
        path, entry = self.locate(session_id)
        if not path.is_file():
            raise ArchiveError(f"session not found: {session_id}")
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        captured: list[str] = []
        taking = False
        seen: dict[str, int] = {}
        for line in lines:
            meta = parse_heading(line)
            if meta is not None:
                if taking:
                    break
                seen[meta.title] = seen.get(meta.title, 0) + 1
                if resolve_entry_id(meta, str(path), seen[meta.title]) == entry:
                    if not is_visible(meta.expires_at, now):
                        raise ArchiveError(f"session not found: {session_id}")
                    taking = True
                    captured.append(line)
                continue
            if taking:
                captured.append(line)
        if not captured:
            raise ArchiveError(f"session not found: {session_id}")
        return "".join(captured)

    def purge_expired(self, now: datetime) -> None:
        memory_dir = self.thyca_dir / "memory"
        dailies = sorted(memory_dir.glob("????-??-??.md")) if memory_dir.is_dir() else []
        for path in dailies:
            if not path.is_file():
                continue
            with self.lock_for(path):
                self._purge(path, now)

    def _remove_session(self, path: Path, entry_id: str) -> None:
        if not path.is_file():
            raise ArchiveError(f"memory file missing: {path}")
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        out: list[str] = []
        index = 0
        found = False
        seen: dict[str, int] = {}
        while index < len(lines):
            meta = parse_heading(lines[index])
            if meta is None:
                out.append(lines[index])
                index += 1
                continue
            seen[meta.title] = seen.get(meta.title, 0) + 1
            end = index + 1
            while end < len(lines) and parse_heading(lines[end]) is None:
                end += 1
            if resolve_entry_id(meta, str(path), seen[meta.title]) == entry_id:
                found = True
                index = end
                continue
            out.extend(lines[index:end])
            index = end
        if not found:
            raise ArchiveError(f"session not found: {entry_id}")
        _atomic_write(path, "".join(out))

    def _purge(self, path: Path, now: datetime) -> None:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        out: list[str] = []
        index = 0
        while index < len(lines):
            meta = parse_heading(lines[index])
            if meta is None:
                out.append(lines[index])
                index += 1
                continue
            end = index + 1
            while end < len(lines) and parse_heading(lines[end]) is None:
                end += 1
            if is_expired(meta.expires_at, now):
                index = end
                continue
            out.extend(lines[index:end])
            index = end
        _atomic_write(path, "".join(out))


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)
