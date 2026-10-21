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
from thyca.memory.heading import (
    TASK_ID_RE,
    TASK_KINDS,
    TASK_STATUSES,
    format_ts,
    utc_now,
)
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
    reader: str | None = None,
    task: str | None = None,
    kind: str | None = None,
    status: str | None = None,
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
    for name, value, allowed in (
        ("task", task, None),
        ("kind", kind, TASK_KINDS),
        ("status", status, TASK_STATUSES),
    ):
        if value is not None and (
            not isinstance(value, str)
            or not value
            or (allowed is not None and value not in allowed)
            or (name == "task" and TASK_ID_RE.fullmatch(value) is None)
        ):
            return SearchResult(warnings=[f"invalid {name}"])
    if timeline_day is not None and timeline_day >= archive.day(now):
        warnings.append(
            f"timeline_day {timeline_day} is not indexed "
            "(today and future files are excluded)"
        )
    fts = archive.fts_hits(
        query, timeline_day, now, CANDIDATE_CAP,
        project=proj, chat_session=chat, reader=reader,
        task=task, kind=kind, status=status,
    )
    hits: list[Hit] = list(fts)
    trigram = archive.trigram_hits(
        query, timeline_day, now, CANDIDATE_CAP,
        project=proj, chat_session=chat, reader=reader,
        task=task, kind=kind, status=status,
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
    from thyca.memory.visibility import current_memory_view

    view = current_memory_view()
    if view is not None:
        deduped = [
            hit for hit in deduped
            if view.can_read_entry(hit.chat_session, hit.bots)
        ]
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
    archive: ArchivedMemory, limit: int = 5, now: datetime | None = None,
    reader: str | None = None,
) -> list[Hit]:
    """Most recently updated archived hits, clamped like the facade."""
    limit = max(1, min(limit, 10))
    from thyca.memory.visibility import current_memory_view

    view = current_memory_view()
    pool = archive.recent_hits(CANDIDATE_CAP, now, reader=reader)
    if view is not None:
        pool = [
            hit for hit in pool
            if view.can_read_entry(hit.chat_session, hit.bots)
        ]
    return archive.with_counts(pool[:limit], now)
