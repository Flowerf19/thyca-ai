"""Leaf usage stats. used = get; searched = search hit; unused = never get."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from thyca.memory.chunk import Chunk

SNIPPET_LEN = 250
EXPIRE_SOON_DAYS = 14
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
    search_count: int = 0
    last_search_at: str | None = None


@dataclass(frozen=True)
class CanonicalFile:
    name: str
    content: str


@dataclass
class MemoryStatsResult:
    total: int
    used: int
    unused: int
    searched: int = 0
    untouched: int = 0
    leaves: list[LeafStat] = field(default_factory=list)
    suggest_removal: list[LeafStat] = field(default_factory=list)
    expiring: list[LeafStat] = field(default_factory=list)
    files: list[CanonicalFile] = field(default_factory=list)


class MemoryStats:
    """Filter L2 heading leaves and rank unused archived ones."""

    @staticmethod
    def build(
        archived: list[dict[str, object]],
        today_chunks: list[Chunk],
        gets: dict[str, tuple[int, str]],
        searches: dict[str, tuple[int, str]],
        today: str,
        now_ts: str,
        files: list[CanonicalFile] | None = None,
    ) -> MemoryStatsResult:
        by_id: dict[str, LeafStat] = {}
        for row in archived:
            stat = _from_archived(row, gets, searches, today)
            if stat is not None:
                by_id[stat.chunk_id] = stat
        for chunk in today_chunks:
            if chunk.chunk_id in by_id:
                continue
            if chunk.expires_at and chunk.expires_at <= now_ts:
                continue
            stat = _from_today(chunk, gets, searches)
            if stat is not None:
                by_id[stat.chunk_id] = stat
        leaves = sorted(by_id.values(), key=lambda item: (-item.get_count, item.chunk_id))
        unused = [item for item in leaves if item.get_count == 0]
        suggest = [
            item
            for item in unused
            if not item.is_today and item.search_count == 0
        ]
        suggest.sort(key=lambda item: (item.expires_at is None, item.expires_at or "", item.chunk_id))
        return MemoryStatsResult(
            total=len(leaves),
            used=sum(1 for item in leaves if item.get_count >= 1),
            unused=len(unused),
            searched=sum(1 for item in leaves if item.search_count >= 1),
            untouched=sum(1 for item in leaves if item.get_count == 0 and item.search_count == 0),
            leaves=leaves,
            suggest_removal=suggest,
            expiring=expiring_soon(leaves, now_ts),
            files=list(files or ()),
        )


def _from_archived(
    row: dict[str, object],
    gets: dict[str, tuple[int, str]],
    searches: dict[str, tuple[int, str]],
    today: str,
) -> LeafStat | None:
    session_id = str(row["session_id"])
    if L2_SESSION_RE.fullmatch(session_id) is None:
        return None
    chunk_id = str(row["chunk_id"])
    count, last = gets.get(chunk_id, (0, None))
    search_count, last_search = searches.get(chunk_id, (0, None))
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
        search_count=search_count,
        last_search_at=last_search,
        expires_at=str(expires) if expires else None,
        is_today=timeline_day == today,
    )


def _from_today(
    chunk: Chunk,
    gets: dict[str, tuple[int, str]],
    searches: dict[str, tuple[int, str]],
) -> LeafStat | None:
    if L2_SESSION_RE.fullmatch(chunk.session_id) is None:
        return None
    count, last = gets.get(chunk.chunk_id, (0, None))
    search_count, last_search = searches.get(chunk.chunk_id, (0, None))
    return LeafStat(
        chunk_id=chunk.chunk_id,
        session_id=chunk.session_id,
        heading=chunk.heading_raw,
        snippet=chunk.text_raw[:SNIPPET_LEN],
        source_kind=chunk.source_kind,
        timeline_day=chunk.timeline_day,
        get_count=count,
        last_get_at=last,
        search_count=search_count,
        last_search_at=last_search,
        expires_at=chunk.expires_at,
        is_today=True,
    )


def expiring_soon(leaves: list[LeafStat], now_ts: str) -> list[LeafStat]:
    now = _parse_ts(now_ts)
    if now is None:
        return []
    horizon = now + timedelta(days=EXPIRE_SOON_DAYS)
    rows = [
        item
        for item in leaves
        if not item.is_today and _expires_by(item.expires_at, horizon)
    ]
    rows.sort(key=lambda item: (item.expires_at or "", item.chunk_id))
    return rows


def _expires_by(expires_at: str | None, horizon: datetime) -> bool:
    exp = _parse_ts(expires_at or "")
    return exp is not None and exp <= horizon


def _parse_ts(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
