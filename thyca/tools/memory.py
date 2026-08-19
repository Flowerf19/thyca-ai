"""Memory facade: remember / forget / reinforce / get."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from thyca.memory.active import ActiveMemory
from thyca.memory.archived import (
    CANDIDATE_CAP,
    TRIGRAM_MIN_FTS,
    ArchiveError,
    ArchivedMemory,
    Hit,
    SearchResult,
    DATE_RE,
    dedup_siblings,
)
from thyca.memory.heading import (
    DEFAULT_IMPORTANCE,
    HeadingMeta,
    expiry_ts,
    new_entry_id,
    render_heading,
    session_id,
    utc_now,
)
from thyca.memory.writer import MemoryWriter

Target = Literal["daily", "user", "memory", "soul"]


class MemoryFacade:
    def __init__(
        self,
        thyca_dir: Path | None = None,
        timezone_name: str | None = None,
        archive: ArchivedMemory | None = None,
        writer: MemoryWriter | None = None,
    ) -> None:
        self.thyca_dir = Path(thyca_dir or Path.home() / ".thyca")
        self.active = ActiveMemory(self.thyca_dir, timezone_name=timezone_name)
        self.archive = archive or ArchivedMemory(self.thyca_dir, timezone_name=timezone_name)
        self.writer = writer or MemoryWriter(self.thyca_dir)

    def remember(
        self,
        topic: str,
        summary: str,
        content: str = "",
        target: Target = "daily",
        importance: int = DEFAULT_IMPORTANCE,
        now: datetime | None = None,
    ) -> str:
        self.active.ensure_files(now)
        if target in {"user", "soul"}:
            path = self.thyca_dir / ("USER.md" if target == "user" else "SOUL.md")
            extra = f"\n  {content}" if content else ""
            with self.writer.lock_for(path):
                self.writer.append(path, f"- {summary}{extra}\n")
            self._refresh_index(now)
            return f"canonical#{target}"
        moment = utc_now(now)
        entry = new_entry_id()
        if target == "daily":
            day = self.archive.day(now)
            path = self.thyca_dir / "memory" / f"{day}.md"
            sid = session_id(day, entry)
        elif target == "memory":
            path = self.thyca_dir / "MEMORY.md"
            sid = session_id("memory", entry)
        else:
            raise ArchiveError(f"invalid target {target!r}")
        hour = moment.astimezone(self.archive.zone()).strftime("%H:%M")
        meta = HeadingMeta(
            time=hour,
            title=topic,
            entry_id=entry,
            importance=importance,
            expires_at=expiry_ts(importance, moment),
        )
        leaf = f"- {summary}" + (f"\n  {content}" if content else "")
        with self.writer.lock_for(path):
            if not path.is_file():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"# {path.stem}\n", encoding="utf-8")
            self.writer.append(path, render_heading(meta) + leaf + "\n")
        self._refresh_index(now)
        return sid

    def forget(self, session_id: str, now: datetime | None = None) -> None:
        self.writer.forget(session_id, now)
        self._refresh_index(now)

    def reinforce(
        self,
        session_id: str,
        importance: int | None = None,
        now: datetime | None = None,
    ) -> str:
        exp = self.writer.reinforce(session_id, importance, now)
        self._refresh_index(now)
        return exp

    def get(
        self,
        *,
        chunk_id: str | None = None,
        session_id: str | None = None,
        path: str | None = None,
        now: datetime | None = None,
    ) -> str:
        if path is not None:
            return self.archive.get(path=path, now=now)
        try:
            text = self.archive.get(chunk_id=chunk_id, session_id=session_id, now=now)
        except ArchiveError:
            if session_id is None:
                raise
            text = self.writer.read_session(session_id)
        sid = session_id or (self.archive.lookup_session_id(chunk_id, now) if chunk_id else None)
        if sid is None:
            return text
        self.reinforce(sid, now=now)
        try:
            return self.archive.get(chunk_id=chunk_id, session_id=session_id or sid, now=now)
        except ArchiveError:
            return self.writer.read_session(sid)

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        timeline_day: str | None = None,
        now: datetime | None = None,
    ) -> SearchResult:
        if timeline_day is not None and not DATE_RE.fullmatch(timeline_day):
            return SearchResult(warnings=["invalid timeline_day"])
        limit = max(1, min(limit, 10))
        if not query.strip():
            return SearchResult(warnings=["empty query"])
        fts = self.archive.fts_hits(query, timeline_day, now, CANDIDATE_CAP)
        hits: list[Hit] = list(fts)
        if len(fts) < TRIGRAM_MIN_FTS:
            seen = {hit.chunk_id for hit in hits}
            for hit in self.archive.trigram_hits(query, timeline_day, now, CANDIDATE_CAP):
                if hit.chunk_id not in seen:
                    hits.append(hit)
                    seen.add(hit.chunk_id)
        hits = self.archive.with_counts(dedup_siblings(hits)[:limit])
        return SearchResult(hits=hits)

    def recent(self, limit: int = 5, now: datetime | None = None) -> list[Hit]:
        limit = max(1, min(limit, 10))
        return self.archive.with_counts(self.archive.recent_hits(limit, now))

    def _refresh_index(self, now: datetime | None = None) -> None:
        self.writer.purge_expired(utc_now(now))
        self.archive.reindex(now)
