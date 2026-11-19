"""Offline memory benchmark integration and parity tests."""
from __future__ import annotations

from pathlib import Path

from tests.bench.memory_bench import (
    DEFAULT_SEED_DAY,
    EVAL_NOW,
    SeedEntry,
    run_ledgers_eval,
    run_wiki_passage_benchmark,
    seed_daily_entries,
)


def test_ledgers_eval_passes_all_scenarios(tmp_path: Path) -> None:
    report = run_ledgers_eval(tmp_path)
    failed = [c for c in report["cases"] if not c["passed"]]
    assert not failed, f"Failed ledger scenarios: {failed}"
    assert report["passed"] == 12
    assert report["total"] == 12


def test_seed_daily_entries_fast_matches_via_facade(tmp_path: Path) -> None:
    sample = [
        SeedEntry(
            key=f"s{i}",
            topic=f"Topic {i}",
            summary=f"Summary sentence {i}",
            content=f"Detail line {i} on Fedora Bazzite",
            day=DEFAULT_SEED_DAY,
        )
        for i in range(5)
    ]
    fast = seed_daily_entries(tmp_path / "fast", sample, via_facade=False)
    slow = seed_daily_entries(tmp_path / "slow", sample, via_facade=True)

    fast_ids = list(fast.key_to_memory_id.values())
    slow_ids = list(slow.key_to_memory_id.values())
    assert len(fast_ids) == len(slow_ids) == 5
    fast_daily = [
        cid for cid in fast.facade.archive.store.chunk_ids() if cid.startswith(f"{DEFAULT_SEED_DAY}#")
    ]
    slow_daily = [
        cid for cid in slow.facade.archive.store.chunk_ids() if cid.startswith(f"{DEFAULT_SEED_DAY}#")
    ]
    assert len(fast_daily) == len(slow_daily) == 5
    for f_id, s_id in zip(fast_ids, slow_ids, strict=True):
        f_body = fast.facade.archive.get(memory_id=f_id, now=EVAL_NOW).replace(f_id, "ID")
        s_body = slow.facade.archive.get(memory_id=s_id, now=EVAL_NOW).replace(s_id, "ID")
        assert f_body == s_body
    # Parity check between read-only archive.get and MemoryFacade.get
    assert fast.facade.get(memory_id=fast_ids[0], now=EVAL_NOW) == fast.facade.archive.get(
        memory_id=fast_ids[0], now=EVAL_NOW
    )


def test_wiki_passage_benchmark_smoke_and_invariants(tmp_path: Path) -> None:
    report = run_wiki_passage_benchmark(tmp_path, limit=25)
    assert report["seeded_entries"] == 748
    assert report["unique_docs"] == 66
    assert report["queries"] == 25
    assert 0.0 <= report["passage_recall@1"] <= report["passage_recall@5"] <= report["passage_recall@10"] <= 1.0
    assert 0.0 <= report["doc_recall@1"] <= report["doc_recall@5"] <= report["doc_recall@10"] <= 1.0
    assert 0.0 <= report["answer_hit@1"] <= report["answer_hit@5"] <= report["answer_hit@10"] <= 1.0
    assert 0.0 <= report["passage_mrr@10"] <= 1.0
    assert 0.0 <= report["doc_mrr@10"] <= 1.0
