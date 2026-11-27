"""Entry reading, canonical file inspection, and today-chunk helpers for MemoryFacade."""
from __future__ import annotations

from datetime import datetime

from thyca.memories.archived import (
    GET_MEMORY_CAP,
    NO_FALLBACK_PREFIXES,
    ArchiveError,
)
from thyca.memories.chunk import Chunk
from thyca.memories.fallback import read_unindexed_chunk
from thyca.memories.habits import strip_auto_habits
from thyca.memories.heading import format_ts, parse_heading, read_text_file, utc_now
from thyca.memories.stats import CanonicalFile
from thyca.memories.visibility import current_memory_view


def read_memory_entry(
    facade,
    *,
    chunk_id: str | None = None,
    memory_id: str | None = None,
    now: datetime | None = None,
    allow_own_cross_window: bool = False,
) -> str:
    """Resolve and read one chunk or memory session with visibility + reinforcement."""
    provided = [
        (name, value)
        for name, value in (
            ("chunk_id", chunk_id),
            ("memory_id", memory_id),
        )
        if value is not None
    ]
    if len(provided) != 1:
        raise ArchiveError("exactly one of chunk_id, memory_id is required")
    name, value = provided[0]
    if not isinstance(value, str) or not value.strip():
        raise ArchiveError(f"{name} must be a non-empty string")
    now_ts = format_ts(utc_now(now))
    try:
        text = facade.archive.get(chunk_id=chunk_id, memory_id=memory_id, now=now)
        if chunk_id is not None:
            sid = facade.archive.lookup_memory_id(chunk_id, now)
            chunk_ids = [chunk_id]
        else:
            sid = memory_id or ""
            rows = facade.archive.store.get_memory(sid, now_ts)
            chunk_ids = [str(row["chunk_id"]) for row in rows[:GET_MEMORY_CAP]]
    except ArchiveError as original:
        if str(original).startswith(NO_FALLBACK_PREFIXES):
            raise
        if chunk_id is not None:
            text, sid = read_unindexed_chunk(
                facade.archive, facade.writer, chunk_id, now, original
            )
            chunk_ids = [chunk_id]
        elif memory_id is None:
            raise
        else:
            text = facade.writer.read_memory(memory_id, now=now)
            sid = memory_id
            chunk_ids = memory_leaf_ids(facade, memory_id, text)[:GET_MEMORY_CAP]
    if not sid.startswith("canonical#"):
        facade._check_read(sid, now)
        origin_chat = None
        try:
            origin_rows = facade.archive.store.get_memory(sid, now_ts)
        except ArchiveError:
            origin_rows = []
        if origin_rows:
            keys = origin_rows[0].keys()
            if "chat_session" in keys:
                origin_chat = origin_rows[0]["chat_session"]
        if origin_chat is None and isinstance(text, str):
            for line in text.splitlines():
                if not line.startswith("## "):
                    break
                meta = parse_heading(line)
                if meta is not None:
                    origin_chat = meta.chat
                break
        bots = facade._lookup_bots(sid, now)
        if not facade._origin_allowed(
            origin_chat, bots, allow_own_cross_window=allow_own_cross_window
        ):
            raise ArchiveError("entry is not readable in this window")
    if chunk_ids and sid:
        facade.archive.store.usage.record_gets(chunk_ids, sid, now_ts)
    if sid.startswith("canonical#"):
        _view = current_memory_view()
        if (
            _view is not None
            and getattr(_view.window, "kind", None) == "group"
            and isinstance(text, str)
        ):
            return strip_auto_habits(text)
        return text
    if facade._may_mutate(sid, now):
        facade.reinforce(sid, now=now)
        try:
            if chunk_id is not None:
                return facade.archive.get(chunk_id=chunk_id, now=now)
            return facade.archive.get(memory_id=sid, now=now)
        except ArchiveError as reread_miss:
            if str(reread_miss).startswith(NO_FALLBACK_PREFIXES):
                raise
            if chunk_id is not None:
                leaf, _ = read_unindexed_chunk(
                    facade.archive, facade.writer, chunk_id, now, reread_miss
                )
                return leaf
            return facade.writer.read_memory(sid, now=now)
    return text


def canonical_files(facade) -> list[CanonicalFile]:
    """Read SOUL/USER/IDENTITY.md snapshots for stats reporting."""
    files: list[CanonicalFile] = []
    for name in facade.CANONICAL_NAMES:
        path = facade.thyca_dir / name
        try:
            text = read_text_file(path)
        except OSError:
            text = None
        except UnicodeDecodeError as exc:
            raise ArchiveError(f"memory file is not valid UTF-8: {path}") from exc
        files.append(CanonicalFile(name=name, content=text or ""))
    return files


def today_chunks(facade, now: datetime | None) -> list[Chunk]:
    """Chunk today's unindexed daily file on the fly."""
    day = facade.archive.day(now)
    path = facade.thyca_dir / "memory" / f"{day}.md"
    try:
        text = read_text_file(path)
    except OSError:
        return []
    except UnicodeDecodeError as exc:
        raise ArchiveError(f"memory file is not valid UTF-8: {path}") from exc
    if text is None:
        return []
    return facade.archive.chunker.chunk_markdown(
        path, text, source_kind="daily", timeline_day=day
    )


def memory_leaf_ids(facade, memory_id: str, text: str) -> list[str]:
    """Extract leaf chunk_ids belonging to ``memory_id`` from raw daily text."""
    path, _ = facade.writer.locate(memory_id)
    day = memory_id.split("#", 1)[0]
    chunks = facade.archive.chunker.chunk_markdown(
        path, text, source_kind="daily", timeline_day=day
    )
    return [chunk.chunk_id for chunk in chunks if chunk.memory_id == memory_id]
