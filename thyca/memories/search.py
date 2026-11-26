"""Lexical search orchestration over ArchivedMemory (policy, no facade state).

The facade validates ownership and delegates here; ranking helpers stay in
``rank.py``, storage in ``archive_store.py``.
"""
from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime

from rapidfuzz import fuzz

from thyca.memories.active import visible_to
from thyca.memories.archive_store import TYPO_TRIGRAM_FLOOR
from thyca.memories.archived import (
    CANDIDATE_CAP,
    DATE_RE,
    ArchivedMemory,
    Hit,
    SearchResult,
    dedup_siblings,
)
from thyca.memories.chunk import Chunker
from thyca.memories.heading import (
    TASK_ID_RE,
    TASK_KINDS,
    TASK_STATUSES,
    format_ts,
    is_visible,
    read_text_file,
    utc_now,
)
from thyca.memories.rank import _promote_in_order_span
from thyca.memories.visibility import current_memory_view

_TOKEN_SPLIT_RE = re.compile(r"[^\w]+", re.UNICODE)


def _absolute_proj(value: object) -> str | None:
    from thyca.tools.path_guard import absolutize  # noqa: PLC0415 -- reason: break circular import with tools.path_guard

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


def _search_today_in_memory(
    archive: ArchivedMemory,
    query: str,
    now: datetime | None,
    *,
    timeline_day: str | None,
    proj: str | None,
    chat: str | None,
    reader: str | None,
    task: str | None,
    kind: str | None,
    status: str | None,
    view,
    allow_own_cross_window: bool,
) -> tuple[list[Hit], dict[str, str], set[str]]:
    """Scan today's unindexed daily file in RAM so truncated/overflowed entries stay findable."""
    today = archive.day(now)
    if timeline_day is not None and timeline_day != today:
        return [], {}, set()
    today_path = archive.thyca_dir / "memory" / f"{today}.md"
    try:
        text = read_text_file(today_path)
    except (OSError, UnicodeDecodeError):
        return [], {}, set()
    if not text:
        return [], {}, set()
    query_norm = Chunker.normalize(query)
    if not query_norm:
        return [], {}, set()
    q_tokens = [tok for tok in _TOKEN_SPLIT_RE.split(query_norm) if len(tok) >= 2]
    grams = tuple(
        {tok[i : i + 3] for tok in q_tokens if len(tok) >= 3 for i in range(len(tok) - 2)}
    )
    chunks = archive.chunker.chunk_markdown(
        str(today_path), text, source_kind="daily", timeline_day=today
    )
    eligible = []
    for chunk in chunks:
        if not is_visible(chunk.expires_at, now):
            continue
        if not visible_to(chunk.bots, reader):
            continue
        if proj is not None and chunk.project != proj:
            continue
        if chat is not None and chunk.chat_session != chat:
            continue
        if task is not None and chunk.task != task:
            continue
        if kind is not None and chunk.kind != kind:
            continue
        if status is not None and chunk.status != status:
            continue
        if view is not None and not view.can_read_entry(
            chunk.chat_session,
            chunk.bots,
            allow_own_cross_window=allow_own_cross_window,
        ):
            continue
        eligible.append(chunk)
    leaf_counts: dict[str, int] = {}
    for chunk in eligible:
        leaf_counts[chunk.memory_id] = leaf_counts.get(chunk.memory_id, 0) + 1
    scored: list[Hit] = []
    hays: dict[str, str] = {}
    strong_ids: set[str] = set()
    for chunk in eligible:
        title_norm = Chunker.normalize(chunk.memory_title) if chunk.memory_title else ""
        hay = f"{title_norm} {chunk.text_norm}".strip() if title_norm else chunk.text_norm
        hay_words = {w for w in _TOKEN_SPLIT_RE.split(hay) if w}
        matched_tokens = sum(
            1
            for tok in q_tokens
            if tok in hay_words or (len(tok) >= 4 and any(w.startswith(tok) for w in hay_words))
        )
        score = 0.0
        match_type = "today"
        if query_norm in hay:
            score = 100.0
            strong_ids.add(chunk.chunk_id)
        elif q_tokens and matched_tokens == len(q_tokens):
            score = 95.0
            strong_ids.add(chunk.chunk_id)
        elif q_tokens and matched_tokens > 0:
            score = 60.0 * (matched_tokens / len(q_tokens))
        elif grams and any(g in hay for g in grams):
            fuzzy = float(fuzz.partial_ratio(query_norm, hay))
            if fuzzy >= TYPO_TRIGRAM_FLOOR:
                score = fuzzy
                match_type = "trigram"
        if score <= 0.0:
            continue
        count = leaf_counts.get(chunk.memory_id, 1)
        hays[chunk.chunk_id] = hay
        scored.append(
            Hit(
                path=chunk.path,
                source_kind="daily",
                chunk_id=chunk.chunk_id,
                timeline_day=today,
                memory_id=chunk.memory_id,
                heading=chunk.heading_raw,
                snippet=chunk.text_raw[:250],
                score=score,
                match_type=match_type,
                leaf_ord=chunk.leaf_ord,
                memory_leaf_count=count,
                has_more=count > 1,
                line_start=chunk.line_start,
                line_end=chunk.line_end,
                bots=chunk.bots,
                task=chunk.task,
                cont=chunk.cont,
                kind=chunk.kind,
                status=chunk.status,
                habit=chunk.habit,
                habit_explicit=chunk.habit_explicit,
                chat_session=chunk.chat_session,
            )
        )
    scored.sort(key=lambda item: (-item.score, item.chunk_id))
    return scored[:CANDIDATE_CAP], hays, strong_ids


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
    allow_own_cross_window: bool = False,
) -> SearchResult:
    """FTS + trigram search with clamp/dedup warnings and usage recording.

    ``allow_own_cross_window`` lets a proactive tool read surface the
    in-flight bot's own entries across origin windows; automatic callers
    leave it off so background prefetch keeps the old boundary.
    """
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
    today_str = archive.day(now)
    if timeline_day is not None and timeline_day > today_str:
        warnings.append(
            f"timeline_day {timeline_day} is in the future "
            "(future files are not indexed)"
        )

    view = current_memory_view()
    fts, fts_hidden, fts_capped = archive.fts_memory_hits(
        query, timeline_day, now, CANDIDATE_CAP,
        project=proj, chat_session=chat, reader=reader,
        task=task, kind=kind, status=status,
    )
    if view is None:
        fts_visible_count = len(fts)
    else:
        fts_visible_count = sum(
            1
            for hit in fts
            if view.can_read_entry(
                hit.chat_session, hit.bots,
                allow_own_cross_window=allow_own_cross_window,
            )
        )
    today_hits, today_hays, today_strong = _search_today_in_memory(
        archive,
        query,
        now,
        timeline_day=timeline_day,
        proj=proj,
        chat=chat,
        reader=reader,
        task=task,
        kind=kind,
        status=status,
        view=view,
        allow_own_cross_window=allow_own_cross_window,
    )
    hits: list[Hit] = list(fts)
    trigram = (
        archive.trigram_hits(
            query, timeline_day, now, CANDIDATE_CAP,
            project=proj, chat_session=chat, reader=reader,
            task=task, kind=kind, status=status,
        )
        if fts_visible_count < limit
        else []
    )
    best_trigram: dict[str, Hit] = {}
    for hit in trigram:
        prev = best_trigram.get(hit.memory_id)
        if (
            prev is None
            or hit.score > prev.score
            or (hit.score == prev.score and hit.chunk_id < prev.chunk_id)
        ):
            best_trigram[hit.memory_id] = hit
    trigram = sorted(
        best_trigram.values(), key=lambda item: (-item.score, item.chunk_id)
    )
    if fts_capped or len(trigram) >= CANDIDATE_CAP:
        warnings.append(
            f"candidate cap reached ({CANDIDATE_CAP}): "
            "some matches may be hidden"
        )
    if fts:
        fts_top = max(max(h.score for h in fts), 1.0)
        fts_floor = max(min(h.score for h in fts), 0.1)
        normalized_today: list[Hit] = []
        for hit in today_hits:
            if hit.match_type == "trigram":
                scaled = fts_floor * 0.5 * (hit.score / 100.0)
            elif hit.chunk_id in today_strong:
                scaled = fts_top * (1.02 if hit.score >= 100.0 else 0.98)
            else:
                scaled = fts_floor * 0.75 * (hit.score / 60.0)
            normalized_today.append(replace(hit, score=scaled))
        today_hits = normalized_today
        trigram = [
            replace(hit, score=fts_floor * 0.5 * (hit.score / 100.0))
            for hit in trigram
        ]
    elif today_strong:
        trigram = [replace(hit, score=hit.score * 0.85) for hit in trigram]
    seen_chunks = {hit.chunk_id for hit in hits}
    seen_memories = {hit.memory_id for hit in hits}
    for hit in (*today_hits, *trigram):
        if hit.chunk_id not in seen_chunks and hit.memory_id not in seen_memories:
            hits.append(hit)
            seen_chunks.add(hit.chunk_id)
            seen_memories.add(hit.memory_id)
    if today_hits or trigram:
        hits = [
            item[1]
            for item in sorted(
                enumerate(hits),
                key=lambda pair: (-pair[1].score, pair[0]),
            )
        ]
    hays = archive.store.rank_hays([hit.chunk_id for hit in hits])
    hays.update(today_hays)
    hits = _promote_in_order_span(query, hits, archive.chunker, hays)
    deduped = dedup_siblings(hits)
    hidden = fts_hidden + (len(hits) - len(deduped))
    if hidden:
        noun = "sibling hit" if hidden == 1 else "sibling hits"
        warnings.append(f"dedup hid {hidden} {noun}")
    if view is not None:
        deduped = [
            hit for hit in deduped
            if view.can_read_entry(
                hit.chat_session, hit.bots,
                allow_own_cross_window=allow_own_cross_window)
        ]
    top_slice = deduped[:limit]
    counted = archive.with_counts(top_slice, now)
    hits = [
        orig if orig.chunk_id in today_hays else enriched
        for orig, enriched in zip(top_slice, counted, strict=True)
    ]
    if hits:
        now_ts = format_ts(utc_now(now))
        by_session: dict[str, list[str]] = {}
        for hit in hits:
            by_session.setdefault(hit.memory_id, []).append(hit.chunk_id)
        archive.store.usage.record_searches_batch(by_session, now_ts)
    return SearchResult(hits=hits, warnings=warnings)


def recent(
    archive: ArchivedMemory, limit: int = 5, now: datetime | None = None,
    reader: str | None = None,
    allow_own_cross_window: bool = False,
) -> list[Hit]:
    """Most recently updated archived hits, clamped like the facade.

    ``allow_own_cross_window`` opts proactive tool reads into the owner
    exemption; automatic callers leave it off.
    """
    limit = max(1, min(limit, 10))

    view = current_memory_view()
    pool = archive.recent_hits(CANDIDATE_CAP, now, reader=reader)
    if view is not None:
        pool = [
            hit for hit in pool
            if view.can_read_entry(
                hit.chat_session, hit.bots,
                allow_own_cross_window=allow_own_cross_window)
        ]
    return archive.with_counts(pool[:limit], now)
