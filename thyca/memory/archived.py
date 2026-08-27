"""Archived lexical memory orchestration: chunking, reindex, lexical search.

SQLite I/O lives in ``archive_store.py``; this module keeps the orchestration
and re-exports the store vocabulary so existing imports stay stable.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from thyca.config import DEFAULT_TIMELINE_TIMEZONE
from thyca.memory.archive_store import (
    CANDIDATE_CAP,  # noqa: F401 — re-exported
    DATE_RE,
    GET_SESSION_CAP,
    SCHEMA_VERSION,
    TRIGRAM_MIN_FTS,
    ArchiveError,
    Hit,
    SearchResult,
)
from thyca.memory.archive_store import ArchiveStore, _hit_from_row
from thyca.memory.chunk import Chunk, Chunker
from thyca.memory.heading import format_ts
from thyca.memory.usage import LeafUsage

class ArchivedMemory:
    """Orchestrate chunking, reindex, and lexical search."""

    def __init__(
        self,
        thyca_dir: Path | None = None,
        timezone_name: str | None = None,
        store: ArchiveStore | None = None,
        chunker: Chunker | None = None,
    ) -> None:
        self.thyca_dir = Path(thyca_dir or Path.home() / ".thyca")
        self.timezone_name = timezone_name or DEFAULT_TIMELINE_TIMEZONE
        db_path = self.thyca_dir / "memory.sqlite"
        self.store = store or ArchiveStore(db_path)
        self.chunker = chunker or Chunker()

    def zone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone_name)
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            return ZoneInfo(DEFAULT_TIMELINE_TIMEZONE)

    def day(self, now: datetime | None = None) -> str:
        moment = now or datetime.now(self.zone())
        zone = self.zone()
        aware = moment.replace(tzinfo=zone) if moment.tzinfo is None else moment.astimezone(zone)
        return aware.date().isoformat()

    def lookup_session_id(self, chunk_id: str, now: datetime | None = None) -> str:
        row = self.store.get_chunk(chunk_id, format_ts(now))
        if row is None:
            raise ArchiveError(f"chunk not found: {chunk_id}")
        return str(row["session_id"])

    def reindex(self, now: datetime | None = None) -> None:
        today = self.day(now)
        wanted: set[str] = set()
        for name in ("SOUL.md", "USER.md"):
            path = self.thyca_dir / name
            wanted.add(str(path))
            self._reindex_file(path, "canonical", None, today)
        memory_dir = self.thyca_dir / "memory"
        if memory_dir.is_dir():
            for path in sorted(memory_dir.glob("????-??-??.md")):
                if not path.is_file() or path.is_symlink():
                    continue
                day = path.stem
                if not DATE_RE.fullmatch(day):
                    continue
                wanted.add(str(path))
                self._reindex_file(path, "daily", day, today)
        existing = set(self.store.list_paths())
        for stale in existing - wanted:
            self.store.drop_source(stale)

    def fts_hits(
        self, query: str, timeline_day: str | None, now: datetime | None, limit: int
    ) -> list[Hit]:
        return self.store.fts_search(query, timeline_day, limit, format_ts(now))

    def trigram_hits(
        self, query: str, timeline_day: str | None, now: datetime | None, limit: int
    ) -> list[Hit]:
        return self.store.trigram_search(
            self.chunker.normalize(query), timeline_day, limit, format_ts(now)
        )

    def recent_hits(self, limit: int, now: datetime | None = None) -> list[Hit]:
        return [
            _hit_from_row(row, "recent", snippet=row["text_raw"][:250])
            for row in self.store.recent_rows(limit, format_ts(now))
        ]

    def with_counts(self, hits: list[Hit]) -> list[Hit]:
        counted: list[Hit] = []
        for hit in hits:
            count = self.store.session_leaf_count(hit.session_id)
            counted.append(
                Hit(
                    path=hit.path,
                    source_kind=hit.source_kind,
                    chunk_id=hit.chunk_id,
                    timeline_day=hit.timeline_day,
                    session_id=hit.session_id,
                    heading=hit.heading,
                    snippet=hit.snippet,
                    score=hit.score,
                    match_type=hit.match_type,
                    bm25=hit.bm25,
                    leaf_ord=hit.leaf_ord,
                    session_leaf_count=count,
                    has_more=count > 1,
                    line_start=hit.line_start,
                    line_end=hit.line_end,
                )
            )
        return counted

    def get(
        self,
        *,
        chunk_id: str | None = None,
        session_id: str | None = None,
        path: str | None = None,
        now: datetime | None = None,
    ) -> str:
        selectors = [item for item in (chunk_id, session_id, path) if item]
        if len(selectors) != 1:
            raise ArchiveError("exactly one of chunk_id, session_id, path is required")
        now = format_ts(now)
        if chunk_id is not None:
            row = self.store.get_chunk(chunk_id, now)
            if row is None:
                raise ArchiveError(f"chunk not found: {chunk_id}")
            return row["text_raw"]
        if session_id is not None:
            rows = self.store.get_session(session_id, now)
            if not rows:
                raise ArchiveError(f"session not found: {session_id}")
            heading = rows[0]["heading_raw"]
            body = [row["text_raw"] for row in rows[:GET_SESSION_CAP]]
            text = "\n".join([heading, *body] if heading else body)
            if len(rows) > GET_SESSION_CAP:
                text += f"\n<!-- more:{len(rows) - GET_SESSION_CAP} -->"
            return text
        allowed = self._allowed_path(Path(path or ""))
        return allowed.read_text(encoding="utf-8")

    def _reindex_file(self, path: Path, kind: str, day: str | None, today: str) -> None:
        if kind == "daily" and day is not None and day >= today:
            return
        if not path.is_file() or path.is_symlink():
            self.store.drop_source(str(path))
            return
        stat = path.stat()
        prev = self.store.source_stat(str(path))
        if prev == (stat.st_mtime_ns, stat.st_size):
            return
        text = path.read_text(encoding="utf-8")
        chunks = self.chunker.chunk_markdown(path, text, source_kind=kind, timeline_day=day)
        self.store.replace_source(str(path), kind, day, stat.st_mtime_ns, stat.st_size, chunks)

    def _allowed_path(self, path: Path) -> Path:
        root = self.thyca_dir.resolve()
        target = path.expanduser().resolve()
        if target.parent == root and target.name in {"SOUL.md", "USER.md"}:
            return target
        memory_dir = (root / "memory").resolve()
        if target.parent == memory_dir and DATE_RE.fullmatch(target.stem) and target.suffix == ".md":
            today = self.day()
            if target.stem >= today:
                raise ArchiveError("today daily is not archived")
            return target
        raise ArchiveError(f"path not an archived memory source: {path}")


def dedup_siblings(hits: list[Hit]) -> list[Hit]:
    seen: set[str] = set()
    out: list[Hit] = []
    for hit in hits:
        if hit.session_id in seen:
            continue
        seen.add(hit.session_id)
        out.append(hit)
    return out
