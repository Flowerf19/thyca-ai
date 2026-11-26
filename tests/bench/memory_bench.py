"""Isolated seeding and offline memory benchmark suite for Thyca."""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from thyca.core.protocol import ToolCall
from thyca.memories import jobs
from thyca.memories.chunk import Chunker
from thyca.memories.facade import MemoryFacade
from thyca.memories.habits import habit_observations, sweep_bot_habits
from thyca.memories.heading import (
    HeadingMeta,
    expiry_ts,
    format_body,
    memory_id,
    new_entry_id,
    parse_heading,
    render_heading,
    utc_now,
)
from thyca.memories.identity import bind_bot_id, reset_bot_id
from thyca.memories.ledger_common import validate_entry_fields
from thyca.memories.task_ledger import format_task_catalog
from thyca.tools.memory_tools import sidecar_memory_tools

TZ_NAME = "Asia/Ho_Chi_Minh"
TZ = ZoneInfo(TZ_NAME)
DEFAULT_SEED_DAY = "2026-11-10"
EVAL_NOW = datetime(2026, 11, 18, 10, 0, tzinfo=TZ)
REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = REPO_ROOT / "tests" / "data"


@dataclass(frozen=True)
class SeedEntry:
    """One L2 memory entry to seed into an isolated benchmark root."""

    key: str
    topic: str
    summary: str
    content: str = ""
    day: str = DEFAULT_SEED_DAY
    importance: int = 5


@dataclass
class SeedReport:
    """Result of seeding entries and building the derived SQLite index."""

    root: Path
    facade: MemoryFacade
    key_to_memory_id: dict[str, str]
    seed_ms: float
    reindex_ms: float
    chunks_count: int
    sqlite_kb: float


@dataclass(frozen=True)
class EvalQuery:
    """Single retrieval benchmark query with expected target ids and answers."""

    question: str
    expected_title: str
    target_ids: frozenset[str]
    primary_ids: frozenset[str] | None = None
    answers: tuple[str, ...] = ()


def _clean_single_line(text: str, *, fallback: str) -> str:
    cleaned = " ".join(text.replace("<!--", "").replace("-->", "").split())
    return cleaned or fallback


def _split_passage_summary(text: str, max_chars: int = 160) -> tuple[str, str]:
    """Split a passage into a concise first summary line and remaining body."""
    collapsed = " ".join(text.split())
    if not collapsed:
        return "Untitled", ""
    if len(collapsed) <= max_chars:
        return collapsed, ""
    head = collapsed[:max_chars]
    sent_end = max(head.rfind(". "), head.rfind("! "), head.rfind("? "))
    if sent_end >= 25:
        cut = sent_end + 1
    else:
        space_pos = head.rfind(" ")
        cut = space_pos if space_pos >= 25 else max_chars
    return collapsed[:cut].strip(), collapsed[cut:].strip()


def _day_datetime(day_str: str) -> datetime:
    y, m, d = (int(part) for part in day_str.split("-"))
    return datetime(y, m, d, 9, 0, tzinfo=TZ)


def seed_daily_entries(
    root: Path,
    entries: Sequence[SeedEntry],
    *,
    eval_now: datetime = EVAL_NOW,
    via_facade: bool = False,
) -> SeedReport:
    """Seed ``entries`` as separate ``##`` blocks in an isolated ``root``."""
    root.mkdir(parents=True, exist_ok=True)
    key_to_id: dict[str, str] = {}
    t0 = time.perf_counter()

    if via_facade:
        facade = MemoryFacade(root, timezone_name=TZ_NAME)
        for entry in entries:
            topic_clean = _clean_single_line(entry.topic, fallback="Untitled")
            summary_clean = _clean_single_line(entry.summary, fallback=topic_clean)
            validate_entry_fields(topic_clean, summary_clean, entry.content, entry.importance)
            key_to_id[entry.key] = facade.remember(
                topic_clean,
                summary_clean,
                content=entry.content,
                importance=entry.importance,
                now=_day_datetime(entry.day),
            )
        seed_ms = (time.perf_counter() - t0) * 1000.0
    else:
        by_day: dict[str, list[SeedEntry]] = {}
        for entry in entries:
            by_day.setdefault(entry.day, []).append(entry)
        mem_dir = root / "memory"
        mem_dir.mkdir(parents=True, exist_ok=True)
        for day_str, day_entries in sorted(by_day.items()):
            moment = utc_now(_day_datetime(day_str))
            used_ids: set[str] = set()
            blocks: list[str] = [f"# {day_str}\n"]
            for entry in day_entries:
                topic_clean = _clean_single_line(entry.topic, fallback="Untitled")
                summary_clean = _clean_single_line(entry.summary, fallback=topic_clean)
                validate_entry_fields(
                    topic_clean, summary_clean, entry.content, entry.importance
                )
                eid = new_entry_id()
                while eid in used_ids:
                    eid = new_entry_id()
                used_ids.add(eid)
                meta = HeadingMeta(
                    time="09:00",
                    title=topic_clean,
                    entry_id=eid,
                    importance=entry.importance,
                    expires_at=expiry_ts(entry.importance, moment),
                )
                leaf = "\n".join(format_body(summary_clean, entry.content))
                blocks.append(render_heading(meta) + leaf + "\n")
                key_to_id[entry.key] = memory_id(day_str, eid)
            (mem_dir / f"{day_str}.md").write_text("".join(blocks), encoding="utf-8")
        seed_ms = (time.perf_counter() - t0) * 1000.0
        t_init = time.perf_counter()
        facade = MemoryFacade(root, timezone_name=TZ_NAME)
        init_ms = (time.perf_counter() - t_init) * 1000.0

    t_idx = time.perf_counter()
    facade.archive.reindex(eval_now)
    reindex_only_ms = (time.perf_counter() - t_idx) * 1000.0
    reindex_ms = reindex_only_ms if via_facade else (init_ms + reindex_only_ms)
    sqlite_path = root / "memory.sqlite"
    sqlite_kb = round(sqlite_path.stat().st_size / 1024.0, 1) if sqlite_path.is_file() else 0.0
    chunks_count = len(facade.archive.store.chunk_ids())
    return SeedReport(
        root=root,
        facade=facade,
        key_to_memory_id=key_to_id,
        seed_ms=round(seed_ms, 2),
        reindex_ms=round(reindex_ms, 2),
        chunks_count=chunks_count,
        sqlite_kb=sqlite_kb,
    )


def _rank_metrics(ranked_ids: list[str], targets: frozenset[str]) -> tuple[dict[int, bool], float]:
    first_rank: int | None = None
    for idx, sid in enumerate(ranked_ids[:10], start=1):
        if sid in targets:
            first_rank = idx
            break
    hits_at = {k: (first_rank is not None and first_rank <= k) for k in (1, 5, 10)}
    rr = (1.0 / first_rank) if first_rank is not None else 0.0
    return hits_at, rr


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, max(0, math.ceil(pct * len(sorted_vals)) - 1))
    return sorted_vals[idx]


def _evaluate_retrieval(
    facade: MemoryFacade,
    queries: Sequence[EvalQuery],
    *,
    eval_now: datetime = EVAL_NOW,
    check_primary: bool = False,
    check_answers: bool = False,
    max_sample_misses: int = 8,
) -> dict[str, Any]:
    """Run ``facade.search`` over ``queries`` and summarize recall, MRR, and latency."""
    total = len(queries)
    doc_hits = {1: 0, 5: 0, 10: 0}
    doc_rr_sum = 0.0
    pri_hits = {1: 0, 5: 0, 10: 0}
    pri_rr_sum = 0.0
    ans_hits = {1: 0, 5: 0, 10: 0}
    match_types: Counter[str] = Counter()
    warning_counts: Counter[str] = Counter()
    zero_hits = 0
    latencies_ms: list[float] = []
    misses: list[dict[str, Any]] = []
    raw_results: list[tuple[EvalQuery, list[Any]]] = []

    for q in queries:
        t0 = time.perf_counter()
        res = facade.search(q.question, limit=10, now=eval_now)
        latencies_ms.append((time.perf_counter() - t0) * 1000.0)
        raw_results.append((q, res.hits))

        for warn in res.warnings:
            if warn.startswith("candidate cap"):
                warning_counts["candidate_cap"] += 1
            elif warn.startswith("dedup hid"):
                warning_counts["dedup_hid"] += 1
            else:
                warning_counts["other"] += 1

        if not res.hits:
            zero_hits += 1
            match_types["none"] += 1
        else:
            match_types[res.hits[0].match_type] += 1

        ranked_ids = [hit.memory_id for hit in res.hits]
        d_at, d_rr = _rank_metrics(ranked_ids, q.target_ids)
        for k in (1, 5, 10):
            doc_hits[k] += int(d_at[k])
        doc_rr_sum += d_rr

        if check_primary and q.primary_ids is not None:
            p_at, p_rr = _rank_metrics(ranked_ids, q.primary_ids)
            for k in (1, 5, 10):
                pri_hits[k] += int(p_at[k])
            pri_rr_sum += p_rr

        missed_target = not (p_at[10] if (check_primary and q.primary_ids) else d_at[10])
        if missed_target and len(misses) < max_sample_misses:
            top1 = res.hits[0] if res.hits else None
            misses.append({
                "question": q.question,
                "expected_title": q.expected_title,
                "hit_count": len(res.hits),
                "top1_title": top1.heading if top1 else None,
                "top1_match_type": top1.match_type if top1 else None,
                "top1_score": round(float(top1.score), 3) if top1 else None,
            })

    if check_answers and total > 0:
        norm_cache: dict[str, str] = {}
        for q, hits in raw_results:
            ans_norms = [Chunker.normalize(a).strip() for a in q.answers if a and a.strip()]
            first_ans_rank: int | None = None
            if ans_norms:
                for idx, hit in enumerate(hits[:10], start=1):
                    sid = hit.memory_id
                    if sid not in norm_cache:
                        norm_cache[sid] = Chunker.normalize(
                            facade.archive.get(memory_id=sid, now=eval_now)
                        )
                    hay = norm_cache[sid]
                    if any(ans in hay for ans in ans_norms):
                        first_ans_rank = idx
                        break
            for k in (1, 5, 10):
                if first_ans_rank is not None and first_ans_rank <= k:
                    ans_hits[k] += 1

    sorted_lat = sorted(latencies_ms)
    denom = max(1, total)
    out: dict[str, Any] = {
        "queries": total,
        "doc_recall@1": round(doc_hits[1] / denom, 4),
        "doc_recall@5": round(doc_hits[5] / denom, 4),
        "doc_recall@10": round(doc_hits[10] / denom, 4),
        "doc_mrr@10": round(doc_rr_sum / denom, 4),
        "zero_hit_count": zero_hits,
        "zero_hit_rate": round(zero_hits / denom, 4),
        "top1_match_type": dict(match_types),
        "warning_counts": dict(warning_counts),
        "latency_ms": {
            "mean": round(sum(latencies_ms) / denom, 3),
            "p50": round(_percentile(sorted_lat, 0.50), 3),
            "p95": round(_percentile(sorted_lat, 0.95), 3),
            "max": round(max(latencies_ms) if latencies_ms else 0.0, 3),
        },
        "sample_misses": misses,
    }
    if check_primary:
        out["passage_recall@1"] = round(pri_hits[1] / denom, 4)
        out["passage_recall@5"] = round(pri_hits[5] / denom, 4)
        out["passage_recall@10"] = round(pri_hits[10] / denom, 4)
        out["passage_mrr@10"] = round(pri_rr_sum / denom, 4)
    if check_answers:
        out["answer_hit@1"] = round(ans_hits[1] / denom, 4)
        out["answer_hit@5"] = round(ans_hits[5] / denom, 4)
        out["answer_hit@10"] = round(ans_hits[10] / denom, 4)
    return out


def _load_wiki_rows(data_root: Path = DATA_ROOT) -> list[dict[str, Any]]:
    path = data_root / "wikipedia_ge10" / "gold_ge10.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def run_wiki_passage_benchmark(
    root: Path,
    *,
    limit: int | None = None,
    via_facade: bool = False,
    data_root: Path = DATA_ROOT,
) -> dict[str, Any]:
    """Seed all 748 unique Wikipedia ``gold_context`` passages and benchmark search."""
    rows = _load_wiki_rows(data_root)
    entries: list[SeedEntry] = []
    pair_to_key: dict[tuple[str, str], str] = {}
    doc_to_keys: dict[str, list[str]] = {}

    for row in rows:
        doc_id = str(row["gold_document_id"])
        ctx = str(row["gold_context"])
        pair = (doc_id, ctx)
        if pair not in pair_to_key:
            key = f"wp-{len(entries)}"
            pair_to_key[pair] = key
            doc_to_keys.setdefault(doc_id, []).append(key)
            summary, remainder = _split_passage_summary(ctx)
            entries.append(
                SeedEntry(
                    key=key,
                    topic=str(row.get("mlqa_title") or doc_id),
                    summary=summary,
                    content=remainder,
                )
            )

    report = seed_daily_entries(root, entries, via_facade=via_facade)
    eval_rows = rows[:limit] if limit is not None else rows
    queries = [
        EvalQuery(
            question=str(r["question"]),
            expected_title=str(r.get("mlqa_title") or r["gold_document_id"]),
            target_ids=frozenset(
                report.key_to_memory_id[k]
                for k in doc_to_keys[str(r["gold_document_id"])]
            ),
            primary_ids=frozenset(
                [report.key_to_memory_id[pair_to_key[(str(r["gold_document_id"]), str(r["gold_context"]))]]]
            ),
            answers=tuple(str(a) for a in (r.get("answers") or [])),
        )
        for r in eval_rows
    ]
    metrics = _evaluate_retrieval(
        report.facade,
        queries,
        check_primary=True,
        check_answers=True,
    )
    metrics.update({
        "suite": "wiki_passage",
        "seeded_entries": len(entries),
        "unique_docs": len(doc_to_keys),
        "chunks_count": report.chunks_count,
        "seed_ms": report.seed_ms,
        "reindex_ms": report.reindex_ms,
        "sqlite_kb": report.sqlite_kb,
    })
    return metrics


def run_wiki_doc_benchmark(
    root: Path,
    *,
    limit: int | None = None,
    data_root: Path = DATA_ROOT,
) -> dict[str, Any]:
    """Group Wikipedia passages by ``gold_document_id`` (66 docs) and benchmark search."""
    rows = _load_wiki_rows(data_root)
    doc_passages: dict[str, list[str]] = {}
    doc_titles: dict[str, str] = {}
    for row in rows:
        doc_id = str(row["gold_document_id"])
        ctx = str(row["gold_context"])
        doc_titles.setdefault(doc_id, str(row.get("mlqa_title") or doc_id))
        bucket = doc_passages.setdefault(doc_id, [])
        if ctx not in bucket:
            bucket.append(ctx)

    entries: list[SeedEntry] = []
    for doc_id, passages in doc_passages.items():
        first_sum, first_rem = _split_passage_summary(passages[0])
        rest = ([first_rem] if first_rem else []) + passages[1:]
        entries.append(
            SeedEntry(
                key=doc_id,
                topic=doc_titles[doc_id],
                summary=first_sum,
                content="\n\n".join(rest),
            )
        )

    report = seed_daily_entries(root, entries)
    eval_rows = rows[:limit] if limit is not None else rows
    queries = [
        EvalQuery(
            question=str(r["question"]),
            expected_title=doc_titles[str(r["gold_document_id"])],
            target_ids=frozenset([report.key_to_memory_id[str(r["gold_document_id"])]]),
        )
        for r in eval_rows
    ]
    metrics = _evaluate_retrieval(report.facade, queries)
    metrics.update({
        "suite": "wiki_doc",
        "seeded_entries": len(entries),
        "chunks_count": report.chunks_count,
        "avg_chunks_per_doc": round(report.chunks_count / max(1, len(entries)), 2),
        "seed_ms": report.seed_ms,
        "reindex_ms": report.reindex_ms,
        "sqlite_kb": report.sqlite_kb,
    })
    return metrics


def _day_at(day_num: int, hour: int = 10) -> datetime:
    return datetime(2026, 11, day_num, hour, 0, tzinfo=TZ)


def run_ledgers_eval(root: Path) -> dict[str, Any]:
    """Run 12 deterministic 3-ledger, dedupe, search, and rollover checks."""
    root.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []

    def check(name: str, fn: Callable[[Path], None]) -> None:
        case_dir = root / f"case_{name}"
        case_dir.mkdir(parents=True, exist_ok=True)
        try:
            fn(case_dir)
            results.append({"name": name, "passed": True, "error": None})
        except Exception as exc:
            results.append({"name": name, "passed": False, "error": f"{type(exc).__name__}: {exc}"})

    def case_task_in_place_upsert_multiday(d: Path) -> None:
        f = MemoryFacade(d, timezone_name=TZ_NAME)
        tid = f.new_task_id()
        s1 = f.upsert_task_card("Build bench", "Kickoff", task=tid, status="open", now=_day_at(10))
        s2 = f.upsert_task_card("progress", "Step 2 done", task=tid, status="open", now=_day_at(12))
        assert s1 == s2
        text = (d / "memory" / "2026-11-10.md").read_text(encoding="utf-8")
        assert text.count("## ") == 1
        assert "updated: 2026-11-12" in text
        cat = f.tasks(now=_day_at(13))["tasks"]
        assert len(cat) == 1 and cat[0]["entry_count"] == 1

    def case_task_close_hides_from_catalog(d: Path) -> None:
        f = MemoryFacade(d, timezone_name=TZ_NAME)
        t_open, t_done, t_cancel = f.new_task_id(), f.new_task_id(), f.new_task_id()
        f.upsert_task_card("Open work", "Pending", task=t_open, status="open", now=_day_at(10))
        f.upsert_task_card("Done work", "Finished", task=t_done, status="done", now=_day_at(10))
        f.upsert_task_card("Cancelled work", "Dropped", task=t_cancel, status="cancelled", now=_day_at(10))
        all_rows = f.tasks(now=_day_at(11))["tasks"]
        rendered = format_task_catalog(all_rows)
        assert t_open in rendered and t_done not in rendered and t_cancel not in rendered
        assert [r["task_id"] for r in f.tasks(status="open", now=_day_at(11))["tasks"]] == [t_open]

    def case_task_cross_window_dm_isolation(d: Path) -> None:
        f = MemoryFacade(d, timezone_name=TZ_NAME)
        tid = f.new_task_id()
        gid = f.upsert_task_card("Group work", "Public", task=tid, chat="group-1", bots=["coder"], now=_day_at(10))
        did = f.upsert_task_card("DM note", "DM_PRIVATE", task=tid, chat="coder", bots=["coder"], now=_day_at(11))
        assert did != gid
        d10 = (d / "memory" / "2026-11-10.md").read_text(encoding="utf-8")
        d11 = (d / "memory" / "2026-11-11.md").read_text(encoding="utf-8")
        assert "DM_PRIVATE" not in d10 and "DM_PRIVATE" in d11
        dm_meta = parse_heading(next(line for line in d11.splitlines() if line.startswith("## ")))
        assert dm_meta is not None and dm_meta.cont == gid

    def case_task_mismatched_cont_protection(d: Path) -> None:
        f = MemoryFacade(d, timezone_name=TZ_NAME)
        ta, tb = f.new_task_id(), f.new_task_id()
        f.upsert_task_card("Task A", "Orig A", task=ta, chat="r1", now=_day_at(10))
        sb = f.upsert_task_card("Task B", "Orig B", task=tb, chat="r1", now=_day_at(10))
        out = f.upsert_task_card("Task A update", "New A", task=ta, chat="r1", target_memory_id=sb, now=_day_at(10))
        assert out != sb
        d10 = (d / "memory" / "2026-11-10.md").read_text(encoding="utf-8")
        assert "Orig B" in d10 and "New A" in d10

    def case_habit_3day_promotion(d: Path) -> None:
        f = MemoryFacade(d, timezone_name=TZ_NAME)
        bot_user = d / "bots" / "thyca" / "USER.md"
        tok = bind_bot_id("thyca")
        try:
            s1 = f.record_habit_entry("Style", "Short bullet reply", habit="Tra loi ngan gon", now=_day_at(10))
            s2 = f.record_habit_entry("Style", "Concise bullets", habit="Trả lời ngắn gọn", now=_day_at(11))
            s3 = f.record_habit_entry("Style", "Brief again", habit="tra loi ngan gon", now=_day_at(12))
            assert s1 == s2 == s3
            promoted = sweep_bot_habits(f, "thyca", bot_user, now=_day_at(12))
            assert promoted == ["Tra loi ngan gon"]
        finally:
            reset_bot_id(tok)
        assert "Tra loi ngan gon" in bot_user.read_text(encoding="utf-8")
        assert (d / "memory" / "2026-11-10.md").read_text(encoding="utf-8").count("## ") == 1

    def case_habit_explicit_immediate_promotion(d: Path) -> None:
        f = MemoryFacade(d, timezone_name=TZ_NAME)
        bot_user = d / "bots" / "thyca" / "USER.md"
        tok = bind_bot_id("thyca")
        try:
            f.record_habit_entry("Pkg", "User likes uv", habit="Dung uv", habit_explicit=True, now=_day_at(10))
            promoted = sweep_bot_habits(f, "thyca", bot_user, now=_day_at(10))
            assert promoted == ["Dung uv"]
        finally:
            reset_bot_id(tok)

    def case_other_fp_guards(d: Path) -> None:
        f = MemoryFacade(d, timezone_name=TZ_NAME)
        now10 = _day_at(10)
        f.append_other_journal("Ver", "Project thyca version is 0.88.0.dev2 on branch main", chat="c1", now=now10)
        f.append_other_journal("Ver", "Project thyca version is 0.88.0.dev3 on branch main", chat="c1", now=now10)
        f.append_other_journal("DB", "User switched database from PostgreSQL to SQLite", chat="c2", now=now10)
        f.append_other_journal("DB", "User switched database from SQLite to PostgreSQL", chat="c2", now=now10)
        f.append_other_journal("PolEN", "User is not using docker for local dev", chat="c3", now=now10)
        f.append_other_journal("PolEN", "User is using docker for local dev", chat="c3", now=now10)
        f.append_other_journal("PolVI", "Trong nhóm nhớ dùng tab bốn khoảng để code", chat="c4", now=now10)
        f.append_other_journal("PolVI", "Trong nhóm đừng dùng tab bốn khoảng để code", chat="c4", now=now10)
        f.append_other_journal("Std", "Team coding standards for Python", content="Use ruff", chat="c5", now=now10)
        f.append_other_journal("Std", "Team coding standards for Python", content="Use uv", chat="c5", now=now10)
        f.append_other_journal("Dep", "Backend deployment notes", chat="c6", now=now10)
        f.append_other_journal("Dep", "Backend deployment notes", content="Use systemd", chat="c6", now=now10)
        txt = (d / "memory" / "2026-11-10.md").read_text(encoding="utf-8")
        for expected in (
            "0.88.0.dev2",
            "0.88.0.dev3",
            "from PostgreSQL to SQLite",
            "from SQLite to PostgreSQL",
            "is not using docker",
            "is using docker",
            "nhớ dùng tab",
            "đừng dùng tab",
            "Use ruff",
            "Use uv",
            "Use systemd",
        ):
            assert expected in txt, f"missing {expected!r}"

    def case_other_fn_dedupe(d: Path) -> None:
        f = MemoryFacade(d, timezone_name=TZ_NAME)
        d10, d11 = _day_at(10), _day_at(11)
        f.append_other_journal("OS", "Máy chạy Fedora Bazzite Linux", chat="c1", now=d10)
        f.append_other_journal("OS", "may chay fedora bazzite linux!", chat="c1", now=d10)
        f.append_other_journal(
            "Stack",
            "Backend choices",
            content="Line one uses FastAPI\nLine two uses SQLite WAL\nLine three deploys on Linux",
            chat="c2",
            now=d10,
        )
        f.append_other_journal(
            "Stack",
            "Backend choices",
            content="Line one runs FastAPI\nLine two runs SQLite WAL\nLine three runs on Linux",
            chat="c2",
            now=d10,
        )
        p_sid = f.append_other_journal("Repo", "Repo dùng uv cho Python", proj="/tmp/repo", chat="c3", now=d10)
        n_sid = f.append_other_journal("Repo", "Repo dung uv cho Python", proj=None, chat="c3", now=d10)
        assert p_sid == n_sid
        x1 = f.append_other_journal("Host", "Server deploys in Singapore", chat="c4", now=d10)
        f.append_other_journal("Extra", "CI runs pytest", chat="c4", now=d11)
        x2 = f.append_other_journal("Host", "Server deploys in Singapore", chat="c4", now=d11)
        assert x1 == x2
        d10_txt = (d / "memory" / "2026-11-10.md").read_text(encoding="utf-8")
        d11_txt = (d / "memory" / "2026-11-11.md").read_text(encoding="utf-8")
        assert d10_txt.count("Fedora Bazzite") == 1
        assert "Line one runs FastAPI" not in d10_txt
        assert "Singapore" not in d11_txt

    def case_search_heading_only(d: Path) -> None:
        f = MemoryFacade(d, timezone_name=TZ_NAME)
        sid = f.remember("UniqueHeadingToken99", "Laptop configuration", now=_day_at(10))
        f.archive.reindex(_day_at(11))
        hits = f.search("UniqueHeadingToken99", now=_day_at(11)).hits
        assert [h.memory_id for h in hits] == [sid]

    def case_search_vietnamese_d_stroke(d: Path) -> None:
        f = MemoryFacade(d, timezone_name=TZ_NAME)
        sid = f.remember("Trip", "Du lịch Đà Nẵng cuối tuần", now=_day_at(10))
        f.archive.reindex(_day_at(11))
        fts = f.archive.fts_hits("da nang", None, _day_at(11), 5)
        assert [h.memory_id for h in fts] == [sid]

    def case_rollover_auto_reindex(d: Path) -> None:
        f = MemoryFacade(d, timezone_name=TZ_NAME)
        d10, d11 = _day_at(10), _day_at(11)
        sid = f.remember("Rollover", "RolloverCheckToken yesterday", now=d10)
        hid = f.remember("Habit", "Morning coffee", kind="habit", habit="morning coffee", now=d10)
        assert f.archive.fts_memory_hits("RolloverCheckToken", None, d10, 5)[0] == []
        d10_hits = f.search("RolloverCheckToken", now=d10).hits
        assert [(h.memory_id, h.match_type) for h in d10_hits] == [(sid, "today")]
        d11_hits = f.search("RolloverCheckToken", now=d11).hits
        assert [(h.memory_id, h.match_type) for h in d11_hits] == [(sid, "fts")]
        assert any(h.memory_id == sid for h in f.recent(limit=5, now=d11))
        assert f.stats(now=d11).total >= 2
        assert f.tasks(now=d11)["tasks"] == []
        assert any(item["memory_id"] == hid for item in habit_observations(f, now=d11))

    def case_extractor_tool_calls(d: Path) -> None:
        f = MemoryFacade(d, timezone_name=TZ_NAME)
        reg = sidecar_memory_tools(f)
        out = asyncio.run(
            jobs.apply_memory_tool_calls(
                reg,
                [
                    ToolCall(
                        id="c1",
                        name="memory_note",
                        arguments={"topic": "OS", "summary": "Fedora Bazzite"},
                    )
                ],
                bot_id="thyca",
            )
        )
        assert len(out["written"]) == 1

    cases = [
        ("task_in_place_upsert_multiday", case_task_in_place_upsert_multiday),
        ("task_close_hides_from_catalog", case_task_close_hides_from_catalog),
        ("task_cross_window_dm_isolation", case_task_cross_window_dm_isolation),
        ("task_mismatched_cont_protection", case_task_mismatched_cont_protection),
        ("habit_3day_promotion", case_habit_3day_promotion),
        ("habit_explicit_immediate_promotion", case_habit_explicit_immediate_promotion),
        ("other_fp_guards", case_other_fp_guards),
        ("other_fn_dedupe", case_other_fn_dedupe),
        ("search_heading_only", case_search_heading_only),
        ("search_vietnamese_d_stroke", case_search_vietnamese_d_stroke),
        ("rollover_auto_reindex", case_rollover_auto_reindex),
        ("extractor_tool_calls", case_extractor_tool_calls),
    ]
    for name, fn in cases:
        check(name, fn)

    passed = sum(1 for item in results if item["passed"])
    total = len(results)
    return {
        "suite": "ledgers_eval",
        "passed": passed,
        "total": total,
        "pass_rate": round(passed / max(1, total), 4),
        "cases": results,
    }


def run_all_benchmarks(
    work_root: Path | None = None,
    *,
    suites: Sequence[str] = ("wiki_passage", "wiki_doc", "ledgers"),
    limit: int | None = None,
    data_root: Path = DATA_ROOT,
) -> dict[str, Any]:
    """Run requested benchmark suites inside an isolated temporary root."""
    if work_root is not None:
        work_root.mkdir(parents=True, exist_ok=True)
        return _run_suites_in_root(work_root, suites=suites, limit=limit, data_root=data_root)
    with tempfile.TemporaryDirectory(prefix="thyca_mem_bench_") as tmp:
        return _run_suites_in_root(Path(tmp), suites=suites, limit=limit, data_root=data_root)


def _run_suites_in_root(
    root: Path,
    *,
    suites: Sequence[str],
    limit: int | None,
    data_root: Path,
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    if "wiki_passage" in suites:
        results["wiki_passage"] = run_wiki_passage_benchmark(
            root / "wiki_passage", limit=limit, data_root=data_root
        )
    if "wiki_doc" in suites:
        results["wiki_doc"] = run_wiki_doc_benchmark(
            root / "wiki_doc", limit=limit, data_root=data_root
        )
    if "ledgers" in suites:
        results["ledgers_eval"] = run_ledgers_eval(root / "ledgers_eval")
    return results


def format_markdown_report(results: dict[str, Any]) -> str:
    """Render concise Markdown summary tables for retrieval and ledger suites."""
    lines = ["## Thyca Memory Offline Benchmark Summary", ""]
    retrieval_keys = [k for k in ("wiki_passage", "wiki_doc") if k in results]
    if retrieval_keys:
        lines.extend([
            "| Suite | Docs / Chunks | Queries | Doc R@1 | Doc R@5 | Doc R@10 | Doc MRR@10 | Zero-Hit | Mean ms | p95 ms | Reindex ms |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ])
        for key in retrieval_keys:
            r = results[key]
            lat = r["latency_ms"]
            lines.append(
                f"| `{key}` | {r['seeded_entries']} / {r['chunks_count']} | {r['queries']} | "
                f"{r['doc_recall@1']:.1%} | {r['doc_recall@5']:.1%} | {r['doc_recall@10']:.1%} | "
                f"{r['doc_mrr@10']:.4f} | {r['zero_hit_rate']:.1%} ({r['zero_hit_count']}) | "
                f"{lat['mean']:.2f} | {lat['p95']:.2f} | {r['reindex_ms']:.1f} |"
            )
        lines.append("")
        for key in retrieval_keys:
            r = results[key]
            extra = []
            if "passage_recall@1" in r:
                extra.append(
                    f"Passage R@1/5/10={r['passage_recall@1']:.1%}/{r['passage_recall@5']:.1%}/{r['passage_recall@10']:.1%} "
                    f"(MRR={r['passage_mrr@10']:.4f})"
                )
            if "answer_hit@1" in r:
                extra.append(
                    f"AnswerHit@1/5/10={r['answer_hit@1']:.1%}/{r['answer_hit@5']:.1%}/{r['answer_hit@10']:.1%}"
                )
            extra.append(f"top1={r['top1_match_type']}")
            extra.append(f"warnings={r['warning_counts']}")
            extra.append(f"db={r['sqlite_kb']}KB")
            lines.append(f"- **`{key}`**: " + " · ".join(extra))
            for idx, miss in enumerate(r.get("sample_misses", [])[:4], start=1):
                lines.append(
                    f"  - miss #{idx}: q={miss['question']!r} | gold={miss['expected_title']!r} | "
                    f"hits={miss['hit_count']} top1={miss['top1_title']!r} ({miss['top1_match_type']})"
                )
        lines.append("")

    if "ledgers_eval" in results:
        le = results["ledgers_eval"]
        lines.append(
            f"### `ledgers_eval`: **{le['passed']}/{le['total']}** passed ({le['pass_rate']:.1%})"
        )
        for c in le["cases"]:
            mark = "PASS" if c["passed"] else f"FAIL ({c['error']})"
            lines.append(f"- `{c['name']}`: {mark}")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Thyca L2 memory & 3-ledger offline benchmark")
    parser.add_argument(
        "--suite",
        choices=("all", "wiki_passage", "wiki_doc", "ledgers"),
        default="all",
    )
    parser.add_argument("--limit", type=int, default=None, help="Cap queries per retrieval suite")
    parser.add_argument("--json-out", type=Path, default=None, help="Write structured metrics JSON")
    parser.add_argument("--keep-dir", type=Path, default=None, help="Use explicit scratch directory")
    args = parser.parse_args(argv)

    suites = (
        ("wiki_passage", "wiki_doc", "ledgers")
        if args.suite == "all"
        else (args.suite,)
    )
    results = run_all_benchmarks(args.keep_dir, suites=suites, limit=args.limit)
    print(format_markdown_report(results))
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Wrote JSON metrics to {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
