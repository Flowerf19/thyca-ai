"""Shared helpers across L2 memory ledgers (Task, Habit, Other)."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from thyca.memories.active import visible_to
from thyca.memories.archive_store import ArchiveError
from thyca.memories.chunk import Chunker
from thyca.memories.heading import (
    TTL_DAYS,
    HeadingMeta,
    is_visible,
    iter_session_blocks,
    memory_id,
    read_text_file,
)
from thyca.memories.identity import current_bot_id

_WORD_SEP_RE = re.compile(r"[^\w]+", re.UNICODE)
TASK_UPDATED_DAY_RE = re.compile(
    r"^\s*-\s*updated:\s*(\d{4}-\d{2}-\d{2})\s*$", re.MULTILINE
)


def validate_entry_fields(
    topic: object, summary: object, content: object, importance: int
) -> None:
    """Validate common memory entry fields shared across all 3 ledgers."""
    if not isinstance(topic, str) or not topic.strip():
        raise ValueError("topic must be a non-empty string")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("summary must be a non-empty string")
    if not isinstance(content, str):
        raise ValueError("content must be a string")
    if importance not in TTL_DAYS:
        raise ValueError(f"importance must be 1..5, got {importance}")


def resolve_owners(
    bots: list[str] | tuple[str, ...] | None, reader: str | None
) -> tuple[str, ...] | None:
    """Owner list for a new entry (BE-4).

    Default is ``[self]`` for a bot turn, legacy ``None`` (team) for the
    user. An explicit list is validated; a child bot may never claim
    ``"team"`` — promotion goes through the candidate queue."""
    if bots is None:
        if reader is None:
            return None
        return (reader,)
    if not isinstance(bots, (list, tuple)) or not bots:
        raise ValueError("bots must be a non-empty list of bot ids")
    cleaned = []
    for item in bots:
        if not isinstance(item, str) or not item or len(item) > 64:
            raise ValueError("bots must be a non-empty list of bot ids")
        if item not in cleaned:
            cleaned.append(item)
    if reader is not None and reader != "thyca" and "team" in cleaned:
        raise ArchiveError(f"bot '{reader}' cannot write team memory directly")
    return tuple(cleaned)


def first_summary_line(text: str) -> str:
    """Return the first non-empty body line stripped of any leading ``- ``."""
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return first[2:].strip() if first.startswith("- ") else first


def norm_words(text: str) -> str:
    """Normalize accents, case, and punctuation into space-separated words."""
    return " ".join(_WORD_SEP_RE.sub(" ", Chunker.normalize(text)).split())


def extract_task_updated_day(text: str | None) -> str | None:
    """Latest ``updated: YYYY-MM-DD`` stamp recorded inside a task card body."""
    if not text:
        return None
    matches = TASK_UPDATED_DAY_RE.findall(text)
    return max(matches) if matches else None


def today_blocks(
    facade, now: datetime | None
) -> tuple[str, Path, list[str], list[tuple[HeadingMeta, str, int, int]]]:
    """Read and parse today's daily file blocks."""
    today_str = facade.archive.day(now)
    path = facade.thyca_dir / "memory" / f"{today_str}.md"
    try:
        text = read_text_file(path)
    except OSError:
        return today_str, path, [], []
    except UnicodeDecodeError as exc:
        raise ArchiveError(f"memory file is not valid UTF-8: {path}") from exc
    if not text:
        return today_str, path, [], []
    lines = text.splitlines(keepends=True)
    return today_str, path, lines, list(iter_session_blocks(lines, str(path)))


def locate_visible_block(
    facade, memory_id_str: str | None, now: datetime | None
) -> tuple[HeadingMeta, list[str]] | None:
    """Locate one visible memory block on disk and return ``(meta, body_lines)``."""
    if not memory_id_str:
        return None
    try:
        path, entry = facade.writer.locate(memory_id_str)
        text = read_text_file(path)
    except (ArchiveError, OSError, UnicodeDecodeError):
        return None
    if not text:
        return None
    lines = text.splitlines(keepends=True)
    for meta, resolved, start, end in iter_session_blocks(lines, str(path)):
        if resolved != entry:
            continue
        if not is_visible(meta.expires_at, now):
            return None
        body = [
            line.rstrip("\r\n")
            for line in lines[start + 1 : end]
            if line.strip()
        ]
        return meta, body
    return None


def today_entries(
    facade,
    *,
    now: datetime | None = None,
    chat: str | None = None,
    reader: str | None = None,
) -> list[dict]:
    """Visible entries written today for prompt/delta inspection."""
    effective_reader = current_bot_id() if reader is None else reader
    today_str, _path, lines, blocks = today_blocks(facade, now)
    entries: list[dict] = []
    for meta, resolved, start, end in blocks:
        if not is_visible(meta.expires_at, now):
            continue
        if not visible_to(meta.bots, effective_reader):
            continue
        if not facade._entry_readable(
            meta.chat, meta.bots, allow_own_cross_window=True
        ):
            continue
        if chat is not None and meta.chat != chat:
            continue
        entries.append(
            {
                "memory_id": memory_id(today_str, resolved),
                "title": meta.title,
                "body": "".join(lines[start + 1 : end]).strip(),
                "bots": list(meta.bots) if meta.bots is not None else None,
                "chat": meta.chat,
                "proj": meta.proj,
                "task": meta.task,
                "kind": meta.kind,
                "status": meta.status,
                "habit": meta.habit,
                "habit_explicit": bool(meta.habit_explicit),
            }
        )
    return entries
