"""BE-4: merge funnel (exact hash → lexical → stub review → Union-Find)."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from thyca.memory import merge
from thyca.memory.facade import MemoryFacade
from thyca.memory.identity import bind_bot_id, reset_bot_id

T0 = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def _facade(tmp_path: Path) -> MemoryFacade:
    return MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")


def test_union_find_clusters() -> None:
    union = merge.UnionFind()
    union.union("a", "b")
    union.union("b", "c")
    union.union("d", "e")
    clusters = union.clusters()
    assert sorted(map(sorted, clusters.values())) == [["a", "b", "c"], ["d", "e"]]


def test_exact_dup_links_without_llm(tmp_path: Path) -> None:
    import asyncio

    facade = _facade(tmp_path)
    a = facade.remember("note", "Mom likes egg coffee", now=T0, importance=5)
    b = facade.remember("note", "mom likes egg coffee", now=T0, importance=5)
    # Reindexed view: rows land in SQLite (same-day files are not indexed).
    facade = _facade(tmp_path)
    calls = []

    async def reviewer(x: str, y: str) -> str:
        calls.append((x, y))
        return "không"

    # Same normalized text: exact stage links; the pair is also a lexical
    # candidate, but the stub says unrelated so no second edge is added.
    out = asyncio.run(merge.run_merge_pass(facade, [a], reviewer))
    assert [a, b, "trùng"] in out["linked"]
    assert facade.archive.store.related_of(a)


def test_no_candidates_means_no_review_call(tmp_path: Path) -> None:
    import asyncio

    facade = _facade(tmp_path)
    sid = facade.remember("lonely", "zxqvkj unique token", now=T0, importance=5)
    facade = _facade(tmp_path)
    calls = []

    async def reviewer(x: str, y: str) -> str:
        calls.append((x, y))
        return "trùng"

    out = asyncio.run(merge.run_merge_pass(facade, [sid], reviewer))
    # Only itself matches: no pair to review, no edge, no LLM call.
    assert out == {"linked": [], "review_calls": 0}
    assert calls == []


def test_review_links_related_pair(tmp_path: Path) -> None:
    import asyncio

    facade = _facade(tmp_path)
    a = facade.remember("gift", "egg coffee for birthday", now=T0, importance=5)
    b = facade.remember("shop", "buy egg coffee beans", now=T0, importance=5)
    facade = _facade(tmp_path)

    async def reviewer(x: str, y: str) -> str:
        return "liên quan"

    out = asyncio.run(merge.run_merge_pass(facade, [a], reviewer))
    assert out["review_calls"] >= 1
    assert [a, b, "liên quan"] in out["linked"]


def test_cluster_report_and_union_apply(tmp_path: Path) -> None:
    facade = _facade(tmp_path)
    token = bind_bot_id("coder")
    try:
        a = facade.remember("a", "coder note one", now=T0, importance=5)
    finally:
        reset_bot_id(token)
    b = facade.remember("shared", "team note", now=T0, bots=["team"], importance=5)
    facade = _facade(tmp_path)
    from thyca.memory.heading import format_ts, utc_now

    facade.archive.store.add_related(a, b, "liên quan", format_ts(utc_now(None)))
    reports = merge.cluster_report(facade)
    assert len(reports) == 1
    assert sorted(reports[0].members) == sorted([a, b])
    assert sorted(reports[0].bots_union) == ["coder", "team"]
    # Applying the union is explicit and Thyca-privileged.
    assert merge.apply_cluster_union(facade, [a, b]) == 2
    assert set(facade.archive.store.session_bots(a)) == {"coder", "team"}


def test_llm_review_parsing() -> None:
    import asyncio

    class Reply:
        def __init__(self, content: str) -> None:
            self.content = content

    class FakeConnect:
        def __init__(self, content: str) -> None:
            self.content = content

        async def chat(self, messages, tools=None):
            return Reply(self.content)

    assert asyncio.run(merge.llm_review_pair(FakeConnect("LIÊN QUAN"), "m", "a", "b")) == "liên quan"
    assert asyncio.run(merge.llm_review_pair(FakeConnect("blah"), "m", "a", "b")) == "không"
