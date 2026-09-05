"""Memory facade: remember / forget / reinforce / get."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from thyca.memory.active import ActiveMemory
from thyca.memory.archived import (
    CANDIDATE_CAP,
    GET_SESSION_CAP,
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
    format_ts,
    new_entry_id,
    render_heading,
    session_id,
    utc_now,
)
from thyca.memory.chunk import Chunk
from thyca.memory.stats import CanonicalFile, MemoryStats, MemoryStatsResult
from thyca.memory.writer import MemoryWriter


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
        self.archive.store.drop_source(str(self.thyca_dir / "MEMORY.md"))

    def remember(
        self,
        topic: str,
        summary: str,
        content: str = "",
        importance: int = DEFAULT_IMPORTANCE,
        now: datetime | None = None,
    ) -> str:
        self.active.ensure_files(now)
        moment = utc_now(now)
        entry = new_entry_id()
        day = self.archive.day(now)
        path = self.thyca_dir / "memory" / f"{day}.md"
        sid = session_id(day, entry)
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

    def update(
        self,
        session_id: str,
        *,
        topic: str | None = None,
        summary: str | None = None,
        content: str | None = None,
        now: datetime | None = None,
    ) -> None:
        body_lines = None
        if summary is not None:
            body_lines = [f"- {summary.strip()}"]
            for line in str(content).splitlines() if content else []:
                body_lines.append(f"  {line}")
        self.writer.update_session(session_id, topic=topic, body_lines=body_lines)
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
        now_ts = format_ts(utc_now(now))
        try:
            text = self.archive.get(chunk_id=chunk_id, session_id=session_id, now=now)
            if chunk_id is not None:
                sid = self.archive.lookup_session_id(chunk_id, now)
                chunk_ids = [chunk_id]
            else:
                sid = session_id or ""
                rows = self.archive.store.get_session(sid, now_ts)
                chunk_ids = [str(row["chunk_id"]) for row in rows[:GET_SESSION_CAP]]
        except ArchiveError:
            if session_id is None:
                raise
            text = self.writer.read_session(session_id, now=now)
            sid = session_id
            chunk_ids = self._session_leaf_ids(session_id, text)[:GET_SESSION_CAP]
        if chunk_ids and sid:
            self.archive.store.usage.record_gets(chunk_ids, sid, now_ts)
        if not sid:
            return text
        self.reinforce(sid, now=now)
        try:
            return self.archive.get(chunk_id=chunk_id, session_id=session_id or sid, now=now)
        except ArchiveError:
            return self.writer.read_session(sid, now=now)

    def stats(self, now: datetime | None = None) -> MemoryStatsResult:
        now_ts = format_ts(utc_now(now))
        return MemoryStats.build(
            self.archive.store.visible_chunk_maps(now_ts),
            self._today_chunks(now),
            self.archive.store.usage.get_map(),
            self.archive.store.usage.search_map(),
            today=self.archive.day(now),
            now_ts=now_ts,
            files=self._canonical_files(),
        )

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
        if hits:
            now_ts = format_ts(utc_now(now))
            by_session: dict[str, list[str]] = {}
            for hit in hits:
                by_session.setdefault(hit.session_id, []).append(hit.chunk_id)
            for sid, chunk_ids in by_session.items():
                self.archive.store.usage.record_searches(chunk_ids, sid, now_ts)
        return SearchResult(hits=hits)

    def recent(self, limit: int = 5, now: datetime | None = None) -> list[Hit]:
        limit = max(1, min(limit, 10))
        return self.archive.with_counts(self.archive.recent_hits(limit, now))

    def _refresh_index(self, now: datetime | None = None) -> None:
        self.writer.purge_expired(utc_now(now))
        self.archive.reindex(now)
        live = set(self.archive.store.chunk_ids())
        live.update(chunk.chunk_id for chunk in self._today_chunks(now))
        self.archive.store.usage.keep_gets(live)
        self.archive.store.usage.keep_searches(live)

    CANONICAL_NAMES = ("SOUL.md", "USER.md", "IDENTITY.md")

    def write_canonical(self, name: str, content: str) -> None:
        """Ghi đè file canonical (SOUL/USER/IDENTITY.md). Chỉ whitelist, atomic."""
        if name not in self.CANONICAL_NAMES:
            raise ArchiveError(f"unknown canonical file: {name}")
        path = self.thyca_dir / name
        if path.is_symlink():
            raise ArchiveError("refusing to write symlink")
        text = str(content).replace("\r\n", "\n")
        if text and not text.endswith("\n"):
            text += "\n"
        tmp = path.with_name(path.name + ".tmp")
        try:
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise ArchiveError(f"write failed: {name}") from exc

    def _canonical_files(self) -> list[CanonicalFile]:
        files: list[CanonicalFile] = []
        for name in ("SOUL.md", "USER.md", "IDENTITY.md"):
            path = self.thyca_dir / name
            text = ""
            if path.is_file() and not path.is_symlink():
                try:
                    text = path.read_text(encoding="utf-8")
                except OSError:
                    text = ""
            files.append(CanonicalFile(name=name, content=text))
        return files

    def _today_chunks(self, now: datetime | None) -> list[Chunk]:
        day = self.archive.day(now)
        path = self.thyca_dir / "memory" / f"{day}.md"
        if not path.is_file() or path.is_symlink():
            return []
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return []
        return self.archive.chunker.chunk_markdown(
            path, text, source_kind="daily", timeline_day=day
        )

    def _session_leaf_ids(self, session_id: str, text: str) -> list[str]:
        path, _ = self.writer.locate(session_id)
        if session_id.startswith("memory#"):
            kind, day = "canonical", None
        else:
            kind, day = "daily", session_id.split("#", 1)[0]
        chunks = self.archive.chunker.chunk_markdown(
            path, text, source_kind=kind, timeline_day=day
        )
        return [chunk.chunk_id for chunk in chunks if chunk.session_id == session_id]
