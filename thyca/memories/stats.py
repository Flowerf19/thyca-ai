"""Leaf usage stats. used = get; searched = search hit; unused = never get."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from thyca.memories.archive_store import decode_bots
from thyca.memories.chunk import Chunk
from thyca.memories.heading import format_ts, is_expired, utc_now

EXPIRE_SOON_DAYS = 14
SUGGEST_IDLE_DAYS = 7
L2_MEMORY_RE = re.compile(
    r"^(?:\d{4}-\d{2}-\d{2}#[0-9a-f]{8}|canonical#[^#]+#[0-9a-f]{8})$"
)


@dataclass(frozen=True)
class LeafStat:
    chunk_id: str
    memory_id: str
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
    bots: tuple[str, ...] | None = None
    kind: str | None = None
    task: str | None = None
    status: str | None = None
    habit: str | None = None


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
    bot_id: str | None = None


def build_stats(
    archive,
    *,
    today_chunks: list[Chunk],
    files: list[CanonicalFile] | None,
    now: datetime | None = None,
    bot_id: str | None = None,
) -> MemoryStatsResult:
    """Assemble a stats report from archive maps plus unindexed inputs."""
    now_ts = format_ts(utc_now(now))
    return MemoryStats.build(
        archive.store.visible_chunk_maps(now_ts),
        today_chunks,
        archive.store.usage.get_map(),
        archive.store.usage.search_map(),
        today=archive.day(now),
        now_ts=now_ts,
        files=files,
        bot_id=bot_id,
    )


def _belongs_to(owners: tuple[str, ...] | None, bot_id: str | None) -> bool:
    """Owner selection, not a claim about which bot used a memory.

    The overall view includes every visible leaf once. Individual views
    exclude team/unassigned entries even when shared data is readable by
    that bot. Never infer ownership from titles, body text, or origin.
    """
    return bot_id is None or (
        owners is not None and "team" not in owners and bot_id in owners
    )


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
        bot_id: str | None = None,
    ) -> MemoryStatsResult:
        by_chunk: dict[str, tuple[int, LeafStat]] = {}
        for row in archived:
            if not _belongs_to(decode_bots(row.get("bots")), bot_id):
                continue
            stat = _from_archived(row, gets, searches, today)
            if stat is not None:
                raw_ord = row.get("leaf_ord")
                leaf_ord = (
                    raw_ord
                    if isinstance(raw_ord, int) and not isinstance(raw_ord, bool)
                    else _chunk_ord(stat.chunk_id)
                )
                by_chunk[stat.chunk_id] = (leaf_ord, stat)
        for chunk in today_chunks:
            if not _belongs_to(chunk.bots, bot_id):
                continue
            if chunk.chunk_id in by_chunk:
                continue
            if is_expired(chunk.expires_at, _parse_ts(now_ts)):
                continue
            stat = _from_today(chunk, gets, searches)
            if stat is not None:
                by_chunk[stat.chunk_id] = (chunk.leaf_ord, stat)
        by_memory: dict[str, list[tuple[int, LeafStat]]] = {}
        for entry in by_chunk.values():
            by_memory.setdefault(entry[1].memory_id, []).append(entry)
        merged = [_merge_memory_group(group) for group in by_memory.values()]
        leaves = sorted(merged, key=lambda item: (-item.get_count, item.chunk_id))
        unused = [item for item in leaves if item.get_count == 0]
        suggest = [
            item
            for item in unused
            if not item.is_today
            and item.search_count == 0
            and _idle_long_enough(item, now_ts)
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
            files=list(files or ()) if bot_id is None else [],
            bot_id=bot_id,
        )


def _chunk_ord(chunk_id: str) -> int:
    tail = chunk_id.rsplit("#", 1)[-1]
    return int(tail) if tail.isdigit() else 0


def _latest_ts(values) -> str | None:
    best_raw: str | None = None
    best_dt: datetime | None = None
    for raw in values:
        if not raw:
            continue
        parsed = _parse_ts(raw)
        if best_raw is None:
            best_raw, best_dt = raw, parsed
            continue
        if (
            parsed is not None
            and best_dt is not None
            and (parsed.tzinfo is None) == (best_dt.tzinfo is None)
        ):
            if parsed > best_dt:
                best_raw, best_dt = raw, parsed
            continue
        if raw > best_raw:
            best_raw, best_dt = raw, parsed
    return best_raw


def _merge_memory_group(group: list[tuple[int, LeafStat]]) -> LeafStat:
    ordered = sorted(group, key=lambda item: (item[0], item[1].chunk_id))
    if len(ordered) == 1:
        return ordered[0][1]
    first = ordered[0][1]
    parts = [item.snippet for _, item in ordered if item.snippet]
    return LeafStat(
        chunk_id=first.chunk_id,
        memory_id=first.memory_id,
        heading=first.heading,
        snippet="\n".join(parts),
        source_kind=first.source_kind,
        timeline_day=first.timeline_day,
        get_count=sum(item.get_count for _, item in ordered),
        last_get_at=_latest_ts(item.last_get_at for _, item in ordered),
        expires_at=first.expires_at,
        is_today=any(item.is_today for _, item in ordered),
        search_count=sum(item.search_count for _, item in ordered),
        last_search_at=_latest_ts(item.last_search_at for _, item in ordered),
        bots=first.bots,
        kind=first.kind,
        task=first.task,
        status=first.status,
        habit=first.habit,
    )


def _from_archived(
    row: dict[str, object],
    gets: dict[str, tuple[int, str]],
    searches: dict[str, tuple[int, str]],
    today: str,
) -> LeafStat | None:
    memory_id = str(row["memory_id"])
    if L2_MEMORY_RE.fullmatch(memory_id) is None:
        return None
    chunk_id = str(row["chunk_id"])
    count, last = gets.get(chunk_id, (0, None))
    search_count, last_search = searches.get(chunk_id, (0, None))
    day = row["timeline_day"]
    timeline_day = str(day) if day is not None else None
    expires = row["expires_at"]
    return LeafStat(
        chunk_id=chunk_id,
        memory_id=memory_id,
        heading=str(row["heading_raw"] or ""),
        snippet=str(row["text_raw"]),
        source_kind=str(row["source_kind"]),
        timeline_day=timeline_day,
        get_count=count,
        last_get_at=last,
        search_count=search_count,
        last_search_at=last_search,
        expires_at=str(expires) if expires else None,
        is_today=timeline_day == today,
        bots=decode_bots(row.get("bots")),
        kind=row.get("kind"),
        task=row.get("task"),
        status=row.get("status"),
        habit=row.get("habit"),
    )


def _from_today(
    chunk: Chunk,
    gets: dict[str, tuple[int, str]],
    searches: dict[str, tuple[int, str]],
) -> LeafStat | None:
    if L2_MEMORY_RE.fullmatch(chunk.memory_id) is None:
        return None
    count, last = gets.get(chunk.chunk_id, (0, None))
    search_count, last_search = searches.get(chunk.chunk_id, (0, None))
    return LeafStat(
        chunk_id=chunk.chunk_id,
        memory_id=chunk.memory_id,
        heading=chunk.heading_raw,
        snippet=chunk.text_raw,
        source_kind=chunk.source_kind,
        timeline_day=chunk.timeline_day,
        get_count=count,
        last_get_at=last,
        search_count=search_count,
        last_search_at=last_search,
        expires_at=chunk.expires_at,
        is_today=True,
        bots=chunk.bots,
        kind=chunk.kind,
        task=chunk.task,
        status=chunk.status,
        habit=chunk.habit,
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


def _idle_long_enough(item: LeafStat, now_ts: str) -> bool:
    now = _parse_ts(now_ts)
    touched = _parse_ts(_last_touch(item) or "")
    if now is None or touched is None:
        return False
    return now - touched >= timedelta(days=SUGGEST_IDLE_DAYS)


def _last_touch(item: LeafStat) -> str | None:
    return item.last_get_at or item.last_search_at or _created_ts(item)


def _created_ts(item: LeafStat) -> str | None:
    if item.timeline_day:
        return f"{item.timeline_day}T00:00:00Z"
    return None


def _parse_ts(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
