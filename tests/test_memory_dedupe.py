"""Phase 1B: duplicate-fact detection → team proposals (replaces merge)."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from thyca.memory import dedupe
from thyca.memory.candidates import CandidateQueue
from thyca.memory.facade import MemoryFacade
from thyca.memory.identity import bind_bot_id, reset_bot_id

T0 = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
T1 = T0 + timedelta(days=3)


def _facade(tmp_path: Path) -> MemoryFacade:
    return MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")


def _remember(facade: MemoryFacade, bot: str | None, topic: str, summary: str,
              now: datetime) -> str:
    token = bind_bot_id(bot) if bot else None
    try:
        return facade.remember(topic, summary, now=now, importance=5)
    finally:
        if token is not None:
            reset_bot_id(token)


def _review_result(value: str):
    async def reviewer(x: str, y: str) -> str:
        return value
    return reviewer


def test_exact_dup_across_bots_proposes_without_llm(tmp_path: Path) -> None:
    facade = _facade(tmp_path)
    a = _remember(facade, "coder", "note", "Mom likes egg coffee", T0)
    b = _remember(facade, "designer", "note", "mom likes egg coffee", T1)
    facade = _facade(tmp_path)
    calls = []

    async def reviewer(x: str, y: str) -> str:
        calls.append((x, y))
        return "không"

    out = asyncio.run(dedupe.propose_duplicates(facade, [b], reviewer))
    # Older entry proposed, no reviewer call for the exact match.
    assert out["proposed"] == [a]
    assert out["review_calls"] == 0
    assert calls == []
    queue = CandidateQueue(tmp_path)
    pending = queue.list_pending()
    assert len(pending) == 1
    assert pending[0].bot_id == "coder" and pending[0].session_id == a


def test_review_trung_proposes_lien_quan_does_nothing(tmp_path: Path) -> None:
    # Isolated home per verdict: entries must not leak across iterations
    # (shared tmp + search limit + random chunk ids made review_calls flaky).
    for index, (verdict, expected) in enumerate((("trùng", 1), ("liên quan", 0), ("không", 0))):
        home = tmp_path / f"case{index}"
        home.mkdir()
        facade = _facade(home)
        a = _remember(facade, "coder", "gift", "egg coffee for birthday xyz", T0)
        b = _remember(facade, "designer", "shop", "buy egg coffee beans xyz", T1)
        facade = _facade(home)
        out = asyncio.run(dedupe.propose_duplicates(facade, [b], _review_result(verdict)))
        assert len(out["proposed"]) == expected, verdict
        assert out["review_calls"] == 1, verdict


def test_same_owner_set_proposes_nothing(tmp_path: Path) -> None:
    facade = _facade(tmp_path)
    _remember(facade, "coder", "one", "egg coffee morning xyz", T0)
    b = _remember(facade, "coder", "two", "egg coffee evening xyz", T1)
    facade = _facade(tmp_path)
    calls = []

    async def reviewer(x: str, y: str) -> str:
        calls.append((x, y))
        return "trùng"

    out = asyncio.run(dedupe.propose_duplicates(facade, [b], reviewer))
    assert out == {"proposed": [], "review_calls": 0}
    assert calls == []


def test_team_visible_entry_proposes_nothing(tmp_path: Path) -> None:
    facade = _facade(tmp_path)
    _remember(facade, None, "shared", "egg coffee team xyz", T0)
    b = _remember(facade, "coder", "mine", "egg coffee mine xyz", T1)
    facade = _facade(tmp_path)
    out = asyncio.run(
        dedupe.propose_duplicates(facade, [b], _review_result("trùng")))
    assert out["proposed"] == []


def test_decided_candidate_never_reproposed(tmp_path: Path) -> None:
    facade = _facade(tmp_path)
    a = _remember(facade, "coder", "note", "Mom likes egg coffee", T0)
    b = _remember(facade, "designer", "note", "mom likes egg coffee", T1)
    facade = _facade(tmp_path)
    queue = CandidateQueue(tmp_path)
    first = asyncio.run(dedupe.propose_duplicates(facade, [b], _review_result("không"), queue))
    assert first["proposed"] == [a]
    target = queue.list_pending()[0]
    queue.decide(target.id, False, "thyca")
    second = asyncio.run(
        dedupe.propose_duplicates(facade, [b], _review_result("trùng"), queue))
    assert second == {"proposed": [], "review_calls": 0}


def test_propose_never_changes_owners(tmp_path: Path) -> None:
    facade = _facade(tmp_path)
    a = _remember(facade, "coder", "note", "Mom likes egg coffee", T0)
    b = _remember(facade, "designer", "note", "mom likes egg coffee", T1)
    facade = _facade(tmp_path)
    asyncio.run(dedupe.propose_duplicates(facade, [b], _review_result("không")))
    assert facade.archive.store.session_bots(a) == ("coder",)
    assert facade.archive.store.session_bots(b) == ("designer",)


def test_no_candidates_means_no_review_call(tmp_path: Path) -> None:
    facade = _facade(tmp_path)
    sid = _remember(facade, "coder", "lonely", "zxqvkj unique token", T0)
    facade = _facade(tmp_path)
    calls = []

    async def reviewer(x: str, y: str) -> str:
        calls.append((x, y))
        return "trùng"

    out = asyncio.run(dedupe.propose_duplicates(facade, [sid], reviewer))
    # Only itself matches: no pair to review, no proposal, no LLM call.
    assert out == {"proposed": [], "review_calls": 0}
    assert calls == []


def test_llm_review_parsing() -> None:
    class Reply:
        def __init__(self, content: str) -> None:
            self.content = content

    class FakeConnect:
        def __init__(self, content: str) -> None:
            self.content = content

        async def chat(self, messages, tools=None):
            return Reply(self.content)

    assert asyncio.run(dedupe.llm_review_pair(FakeConnect("LIÊN QUAN"), "m", "a", "b")) == "liên quan"
    assert asyncio.run(dedupe.llm_review_pair(FakeConnect("blah"), "m", "a", "b")) == "không"
