"""Lexical search orchestration over ArchivedMemory (policy, no facade state).

The facade validates ownership and delegates here; ranking helpers stay in
``rank.py``, storage in ``archive_store.py``.
"""
from __future__ import annotations

from datetime import datetime

from thyca.memory.archived import (
    CANDIDATE_CAP,
    DATE_RE,
    ArchivedMemory,
    Hit,
    SearchResult,
    dedup_siblings,
)
from thyca.memory.heading import format_ts, utc_now
from thyca.memory.rank import _promote_in_order_span


def _absolute_proj(value: object) -> str | None:
    # Local import: thyca.tools.__init__ re-exports MemoryFacade, which
    # imports this module, so a top-level import here would cycle.
    from thyca.tools.path_guard import absolutize

    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("proj must be an absolute path")
    text = value.strip()
    if not text:
        return None
    path = absolutize(text)
    if not path.is_absolute():
        raise ValueError("proj must be an absolute path")
    return str(path)


def lexical_search(
    archive: ArchivedMemory,
    query: str,
    *,
    limit: int = 5,
    timeline_day: str | None = None,
    now: datetime | None = None,
    proj: str | None = None,
    chat: str | None = None,
) -> SearchResult:
    """FTS + trigram search with clamp/dedup warnings and usage recording."""
    if timeline_day is not None and not (
        isinstance(timeline_day, str) and DATE_RE.fullmatch(timeline_day)
    ):
        return SearchResult(warnings=["invalid timeline_day"])
    requested_limit = limit
    limit = max(1, min(limit, 10))
    warnings: list[str] = []
    if limit != requested_limit:
        warnings.append(f"limit clamped from {requested_limit} to {limit}")
    if not isinstance(query, str):
        return SearchResult(warnings=["invalid query"])
    if not query.strip():
        return SearchResult(warnings=["empty query"])
    try:
        proj = _absolute_proj(proj)
    except ValueError:
        return SearchResult(warnings=["invalid proj"])
    if timeline_day is not None and timeline_day >= archive.day(now):
        warnings.append(
            f"timeline_day {timeline_day} is not indexed "
            "(today and future files are excluded)"
        )
    fts = archive.fts_hits(
        query, timeline_day, now, CANDIDATE_CAP,
        project=proj, chat_session=chat,
    )
    hits: list[Hit] = list(fts)
    trigram = archive.trigram_hits(
        query, timeline_day, now, CANDIDATE_CAP,
        project=proj, chat_session=chat,
    )
    if len(fts) >= CANDIDATE_CAP or len(trigram) >= CANDIDATE_CAP:
        warnings.append(
            f"candidate cap reached ({CANDIDATE_CAP}): "
            "some matches may be hidden"
        )
    seen = {hit.chunk_id for hit in hits}
    for hit in trigram:
        if hit.chunk_id not in seen:
            hits.append(hit)
            seen.add(hit.chunk_id)
    hays = archive.store.rank_hays([hit.chunk_id for hit in hits])
    hits = _promote_in_order_span(query, hits, archive.chunker, hays)
    deduped = dedup_siblings(hits)
    hidden = len(hits) - len(deduped)
    if hidden:
        noun = "sibling hit" if hidden == 1 else "sibling hits"
        warnings.append(f"dedup hid {hidden} {noun}")
    hits = archive.with_counts(deduped[:limit], now)
    if hits:
        now_ts = format_ts(utc_now(now))
        by_session: dict[str, list[str]] = {}
        for hit in hits:
            by_session.setdefault(hit.session_id, []).append(hit.chunk_id)
        for sid, chunk_ids in by_session.items():
            archive.store.usage.record_searches(chunk_ids, sid, now_ts)
    return SearchResult(hits=hits, warnings=warnings)


def recent(
    archive: ArchivedMemory, limit: int = 5, now: datetime | None = None
) -> list[Hit]:
    """Most recently updated archived hits, clamped like the facade."""
    limit = max(1, min(limit, 10))
    return archive.with_counts(archive.recent_hits(limit, now), now)
