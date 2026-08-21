"""Leaf usage stats. Counts come only from memory_get."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from thyca.memory.chunk import Chunk

SNIPPET_LEN = 250
L2_SESSION_RE = re.compile(r"^(?:\d{4}-\d{2}-\d{2}|memory)#[0-9a-f]{8}$")


@dataclass(frozen=True)
class LeafStat:
    chunk_id: str
    session_id: str
    heading: str
    snippet: str
    source_kind: str
    timeline_day: str | None
    get_count: int
    last_get_at: str | None
    expires_at: str | None
    is_today: bool = False


@dataclass
class MemoryStatsResult:
    total: int
    used: int
    unused: int
    leaves: list[LeafStat] = field(default_factory=list)
    suggest_removal: list[LeafStat] = field(default_factory=list)


class MemoryStats:
    """Filter L2 heading leaves and rank unused archived ones."""

    @staticmethod
    def build(
        archived: list[dict[str, object]],
        today_chunks: list[Chunk],
        gets: dict[str, tuple[int, str]],
        today: str,
        now_ts: str,
    ) -> MemoryStatsResult:
        by_id: dict[str, LeafStat] = {}
        for row in archived:
            stat = _from_archived(row, gets, today)
            if stat is not None:
                by_id[stat.chunk_id] = stat
        for chunk in today_chunks:
            if chunk.chunk_id in by_id:
                continue
            if chunk.expires_at and chunk.expires_at <= now_ts:
                continue
            stat = _from_today(chunk, gets)
            if stat is not None:
                by_id[stat.chunk_id] = stat
        leaves = sorted(by_id.values(), key=lambda item: (-item.get_count, item.chunk_id))
        unused = [item for item in leaves if item.get_count == 0]
        suggest = [item for item in unused if not item.is_today]
        suggest.sort(key=lambda item: (item.expires_at is None, item.expires_at or "", item.chunk_id))
        return MemoryStatsResult(
            total=len(leaves),
            used=sum(1 for item in leaves if item.get_count >= 1),
            unused=len(unused),
            leaves=leaves,
            suggest_removal=suggest,
        )


def _from_archived(
    row: dict[str, object],
    gets: dict[str, tuple[int, str]],
    today: str,
) -> LeafStat | None:
    session_id = str(row["session_id"])
    if L2_SESSION_RE.fullmatch(session_id) is None:
        return None
    chunk_id = str(row["chunk_id"])
    count, last = gets.get(chunk_id, (0, None))
    day = row["timeline_day"]
    timeline_day = str(day) if day is not None else None
    expires = row["expires_at"]
    return LeafStat(
        chunk_id=chunk_id,
        session_id=session_id,
        heading=str(row["heading_raw"] or ""),
        snippet=str(row["text_raw"])[:SNIPPET_LEN],
        source_kind=str(row["source_kind"]),
        timeline_day=timeline_day,
        get_count=count,
        last_get_at=last,
        expires_at=str(expires) if expires else None,
        is_today=timeline_day == today,
    )


def _from_today(chunk: Chunk, gets: dict[str, tuple[int, str]]) -> LeafStat | None:
    if L2_SESSION_RE.fullmatch(chunk.session_id) is None:
        return None
    count, last = gets.get(chunk.chunk_id, (0, None))
    return LeafStat(
        chunk_id=chunk.chunk_id,
        session_id=chunk.session_id,
        heading=chunk.heading_raw,
        snippet=chunk.text_raw[:SNIPPET_LEN],
        source_kind=chunk.source_kind,
        timeline_day=chunk.timeline_day,
        get_count=count,
        last_get_at=last,
        expires_at=chunk.expires_at,
        is_today=True,
    )
