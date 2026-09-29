"""Memory facade: remember / forget / reinforce / get."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from thyca.config import atomic_write_text
from thyca.memory.active import ActiveMemory
from thyca.memory.archived import (
    GET_SESSION_CAP,
    NO_FALLBACK_PREFIXES,
    ArchivedMemory,
    ArchiveError,
    Hit,
    SearchResult,
)
from thyca.memory.chunk import Chunk
from thyca.memory.fallback import read_unindexed_chunk
from thyca.memory.heading import (
    DEFAULT_IMPORTANCE,
    TTL_DAYS,
    HeadingMeta,
    expiry_ts,
    format_body,
    format_ts,
    new_entry_id,
    read_text_file,
    render_heading,
    session_id,
    utc_now,
)
from thyca.memory.search import _absolute_proj, lexical_search
from thyca.memory.search import recent as _recent_search
from thyca.memory.stats import CanonicalFile, MemoryStatsResult, build_stats
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
        with self.writer.mutation_lock():
            self.archive.store.drop_source(str(self.thyca_dir / "MEMORY.md"))
            self.archive.reindex()

    def remember(
        self,
        topic: str,
        summary: str,
        content: str = "",
        importance: int = DEFAULT_IMPORTANCE,
        now: datetime | None = None,
        proj: str | None = None,
        chat: str | None = None,
    ) -> str:
        if not isinstance(topic, str) or not topic.strip():
            raise ValueError("topic must be a non-empty string")
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("summary must be a non-empty string")
        if not isinstance(content, str):
            raise ValueError("content must be a string")
        normalized_proj = _absolute_proj(proj)
        with self.writer.mutation_lock():
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
                proj=normalized_proj,
                chat=chat if isinstance(chat, str) and chat.strip() else None,
            )
            leaf = "\n".join(format_body(summary, content))
            with self.writer.lock_for(path):
                if not path.is_file():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(f"# {path.stem}\n", encoding="utf-8")
                self.writer.append(path, render_heading(meta) + leaf + "\n")
                self._refresh_index(now)
            return sid

    def forget(self, session_id: str, now: datetime | None = None) -> None:
        self._reject_legacy_session(session_id)
        # The writer owns per-file locking and locates under mutation_lock:
        # no facade-side pre-locate (TOCTOU + double locate).
        with self.writer.mutation_lock():
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
        proj: str | None = None,
    ) -> None:
        self._reject_legacy_session(session_id)
        normalized_proj = _absolute_proj(proj)
        for name, value in (("topic", topic), ("summary", summary), ("content", content)):
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{name} must be a string")
        if topic is not None and not topic.strip():
            raise ValueError("topic must not be blank")
        if summary is not None and not summary.strip():
            raise ValueError("summary must not be blank")
        if content is not None and summary is None:
            raise ValueError("content requires summary")
        if topic is None and summary is None and content is None and normalized_proj is None:
            raise ValueError("nothing to update")
        topic = topic.strip() if topic is not None else None
        body_lines = None
        keep_details = False
        if summary is not None:
            if content is None:
                # Summary-only: the caller did not pass details, so keep them.
                keep_details = True
                body_lines = format_body(summary)
            else:
                body_lines = format_body(summary, content)
        with self.writer.mutation_lock():
            self.writer.update_session(
                session_id, topic=topic, body_lines=body_lines,
                proj=normalized_proj, keep_details=keep_details,
            )
            self._refresh_index(now)

    def reinforce(
        self,
        session_id: str,
        importance: int | None = None,
        now: datetime | None = None,
    ) -> str:
        self._reject_legacy_session(session_id)
        if importance is not None and importance not in TTL_DAYS:
            raise ValueError(f"importance must be 1..5, got {importance}")
        with self.writer.mutation_lock():
            exp = self.writer.reinforce(session_id, importance, now)
            self._refresh_index(now)
            return exp

    def get(
        self,
        *,
        chunk_id: str | None = None,
        session_id: str | None = None,
        now: datetime | None = None,
    ) -> str:
        self._reject_legacy_session(session_id)
        provided = [
            (name, value)
            for name, value in (
                ("chunk_id", chunk_id),
                ("session_id", session_id),
            )
            if value is not None
        ]
        if len(provided) != 1:
            raise ArchiveError("exactly one of chunk_id, session_id is required")
        name, value = provided[0]
        if not isinstance(value, str) or not value.strip():
            raise ArchiveError(f"{name} must be a non-empty string")
        if chunk_id is not None and chunk_id.startswith("memory#"):
            raise ArchiveError("MEMORY.md is no longer supported")
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
        except ArchiveError as original:
            if str(original).startswith(NO_FALLBACK_PREFIXES):
                raise
            if chunk_id is not None:
                text, sid = read_unindexed_chunk(
                    self.archive, self.writer, chunk_id, now, original
                )
                chunk_ids = [chunk_id]
            elif session_id is None:
                raise
            else:
                text = self.writer.read_session(session_id, now=now)
                sid = session_id
                chunk_ids = self._session_leaf_ids(session_id, text)[:GET_SESSION_CAP]
        if chunk_ids and sid:
            self.archive.store.usage.record_gets(chunk_ids, sid, now_ts)
        # sid is always a validated non-empty id here (blank selectors raise
        # above; lookup raises on miss), so no empty-sid early return.
        if sid.startswith("canonical#"):
            # Canonical reads are profile content, not daily memory: return
            # the fetched text without TTL renewal or profile-file mutation.
            return text
        self.reinforce(sid, now=now)
        try:
            if chunk_id is not None:
                return self.archive.get(chunk_id=chunk_id, now=now)
            return self.archive.get(session_id=sid, now=now)
        except ArchiveError as reread_miss:
            if str(reread_miss).startswith(NO_FALLBACK_PREFIXES):
                raise
            if chunk_id is not None:
                leaf, _ = read_unindexed_chunk(
                    self.archive, self.writer, chunk_id, now, reread_miss
                )
                return leaf
            return self.writer.read_session(sid, now=now)

    def stats(self, now: datetime | None = None) -> MemoryStatsResult:
        return build_stats(
            self.archive,
            today_chunks=self._today_chunks(now),
            files=self._canonical_files(),
            now=now,
        )

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        timeline_day: str | None = None,
        now: datetime | None = None,
        proj: str | None = None,
        chat: str | None = None,
    ) -> SearchResult:
        return lexical_search(
            self.archive,
            query,
            limit=limit,
            timeline_day=timeline_day,
            now=now,
            proj=proj,
            chat=chat,
        )

    def recent(self, limit: int = 5, now: datetime | None = None) -> list[Hit]:
        return _recent_search(self.archive, limit, now)

    @staticmethod
    def _reject_legacy_session(session_id: str | None) -> None:
        if isinstance(session_id, str) and session_id.startswith("memory#"):
            raise ArchiveError("MEMORY.md is no longer supported")

    def _refresh_index(self, now: datetime | None = None) -> None:
        with self.writer.mutation_lock():
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
        try:
            with self.writer.mutation_lock(), self.writer.lock_for(path):
                atomic_write_text(path, text)
        except OSError as exc:
            raise ArchiveError(f"write failed: {name}") from exc
        # Outside the write guard: the file already landed, so a reindex
        # failure must surface as itself, not as "write failed".
        # Raw OSError propagates (HTTP 503); only write-phase failures wrap.
        with self.writer.mutation_lock():
            self._refresh_index()

    def _canonical_files(self) -> list[CanonicalFile]:
        files: list[CanonicalFile] = []
        for name in self.CANONICAL_NAMES:
            path = self.thyca_dir / name
            try:
                text = read_text_file(path)
            except OSError:
                text = None
            except UnicodeDecodeError as exc:
                raise ArchiveError(f"memory file is not valid UTF-8: {path}") from exc
            files.append(CanonicalFile(name=name, content=text or ""))
        return files

    def _today_chunks(self, now: datetime | None) -> list[Chunk]:
        day = self.archive.day(now)
        path = self.thyca_dir / "memory" / f"{day}.md"
        try:
            text = read_text_file(path)
        except OSError:
            return []
        except UnicodeDecodeError as exc:
            raise ArchiveError(f"memory file is not valid UTF-8: {path}") from exc
        if text is None:
            return []
        return self.archive.chunker.chunk_markdown(
            path, text, source_kind="daily", timeline_day=day
        )

    def _session_leaf_ids(self, session_id: str, text: str) -> list[str]:
        path, _ = self.writer.locate(session_id)
        day = session_id.split("#", 1)[0]
        chunks = self.archive.chunker.chunk_markdown(
            path, text, source_kind="daily", timeline_day=day
        )
        return [chunk.chunk_id for chunk in chunks if chunk.session_id == session_id]
