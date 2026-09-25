"""Regression tests for the three approved memory-tool contract fixes.

One focused file keeps the existing memory/serve test files untouched.
GOAL-001 (update validation) tests live here; GOAL-002/003 follow.
"""
from __future__ import annotations

import pytest

from thyca.core.protocol import ToolCall
from thyca.serve.memory import memory_endpoint
from thyca.memory.facade import MemoryFacade
from thyca.tools.memory_tools import register_memory_tools
from thyca.tools.gateway import ToolGateway
from thyca.tools.registry import ToolRegistry
from thyca.tools.task_store import TaskStore


def _daily_text(tmp_path) -> str:
    return next((tmp_path / "memory").glob("*.md")).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_update_content_without_summary_is_rejected(tmp_path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    registry = ToolRegistry()
    register_memory_tools(registry, facade)
    gateway = ToolGateway(registry, TaskStore())
    sid = (
        await gateway.submit(
            ToolCall(
                id="r1",
                name="memory_remember",
                arguments={"topic": "cafe", "summary": "orig-summary", "content": "orig-details"},
            )
        )
    ).content
    before = _daily_text(tmp_path)
    refused = await gateway.submit(
        ToolCall(
            id="u1",
            name="memory_update",
            arguments={"session_id": sid, "content": "new-details"},
        )
    )
    assert refused.is_error
    assert "summary" in refused.content.lower()
    assert _daily_text(tmp_path) == before


@pytest.mark.asyncio
async def test_update_content_with_title_but_no_summary_is_rejected(tmp_path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    registry = ToolRegistry()
    register_memory_tools(registry, facade)
    gateway = ToolGateway(registry, TaskStore())
    sid = (
        await gateway.submit(
            ToolCall(
                id="r1",
                name="memory_remember",
                arguments={"topic": "cafe", "summary": "orig-summary"},
            )
        )
    ).content
    before = _daily_text(tmp_path)
    refused = await gateway.submit(
        ToolCall(
            id="u1",
            name="memory_update",
            arguments={"session_id": sid, "topic": "tra da", "content": "new-details"},
        )
    )
    assert refused.is_error
    assert _daily_text(tmp_path) == before


@pytest.mark.asyncio
async def test_update_with_no_fields_and_blank_fields_rejected(tmp_path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    registry = ToolRegistry()
    register_memory_tools(registry, facade)
    gateway = ToolGateway(registry, TaskStore())
    sid = (
        await gateway.submit(
            ToolCall(
                id="r1",
                name="memory_remember",
                arguments={"topic": "cafe", "summary": "orig-summary"},
            )
        )
    ).content
    before = _daily_text(tmp_path)
    for arguments in (
        {"session_id": sid},
        {"session_id": sid, "topic": "   "},
        {"session_id": sid, "summary": "  "},
        {"session_id": sid, "content": ""},
    ):
        refused = await gateway.submit(
            ToolCall(id="u", name="memory_update", arguments=arguments)
        )
        assert refused.is_error, arguments
        assert _daily_text(tmp_path) == before


@pytest.mark.asyncio
async def test_update_positive_cases_still_work(tmp_path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    registry = ToolRegistry()
    register_memory_tools(registry, facade)
    gateway = ToolGateway(registry, TaskStore())
    sid = (
        await gateway.submit(
            ToolCall(
                id="r1",
                name="memory_remember",
                arguments={"topic": "cafe", "summary": "orig-summary", "content": "orig-details"},
            )
        )
    ).content
    # Title-only keeps the body.
    ok = await gateway.submit(
        ToolCall(id="u1", name="memory_update", arguments={"session_id": sid, "topic": "tra da"})
    )
    assert not ok.is_error
    body = (
        await gateway.submit(
            ToolCall(id="g1", name="memory_get", arguments={"session_id": sid})
        )
    ).content
    assert "tra da" in body and "orig-summary" in body and "orig-details" in body
    # Explicit empty content with a valid summary clears details.
    ok = await gateway.submit(
        ToolCall(
            id="u2",
            name="memory_update",
            arguments={"session_id": sid, "summary": "new-summary", "content": ""},
        )
    )
    assert not ok.is_error
    body = (
        await gateway.submit(
            ToolCall(id="g2", name="memory_get", arguments={"session_id": sid})
        )
    ).content
    assert "new-summary" in body and "orig-details" not in body
    # Project-only update works.
    ok = await gateway.submit(
        ToolCall(
            id="u3",
            name="memory_update",
            arguments={"session_id": sid, "proj": "/home/flowerf/Projects/thyca-ai"},
        )
    )
    assert not ok.is_error


def test_facade_update_validation_leaves_data_intact(tmp_path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    sid = facade.remember("cafe", "orig-summary", content="orig-details")
    before = _daily_text(tmp_path)
    for kwargs in (
        {"content": "x"},
        {"topic": "t", "content": "x"},
        {"topic": "   "},
        {"summary": ""},
        {},
    ):
        with pytest.raises(ValueError):
            facade.update(sid, **kwargs)
        assert _daily_text(tmp_path) == before
    got = facade.get(session_id=sid)
    assert "orig-summary" in got and "orig-details" in got


def test_update_endpoint_maps_invalid_to_400(tmp_path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    sid = facade.remember("cafe", "orig-summary", content="orig-details")
    before = _daily_text(tmp_path)
    # Newly rejected shapes must be 400 (not 503) with no path/stack leak.
    for payload in (
        {"session_id": sid, "topic": "tra", "content": "x"},
        {"session_id": sid, "summary": "   "},
        {"session_id": sid, "topic": "  "},
        {"session_id": sid, "content": "x"},
        {"session_id": sid},
    ):
        status, body = memory_endpoint(facade, "update", payload)
        assert status == 400, payload
        assert "error" in body
        assert str(tmp_path) not in body["error"]
    assert _daily_text(tmp_path) == before
    # Valid updates still succeed.
    status, body = memory_endpoint(facade, "update", {"session_id": sid, "topic": "tra"})
    assert (status, body) == (200, {"ok": True})


# --- GOAL-002: unambiguous retrieval ---


def _seed_two_leaf_session(root) -> tuple[str, str]:
    (root / "memory").mkdir()
    (root / "memory" / "2026-08-13.md").write_text(
        "# 2026-08-13\n"
        '## 08:00 — cafe <!-- thyca {"id":"dddddddd","imp":3,"exp":"2027-09-12T00:00:00Z"} -->\n'
        "- first cafe leaf is long enough\n"
        "- second cafe leaf is long enough\n",
        encoding="utf-8",
    )
    return "2026-08-13#dddddddd", "2026-08-13#dddddddd#1"


@pytest.mark.asyncio
async def test_get_rejects_zero_and_multiple_selectors(tmp_path) -> None:
    sid, cid = _seed_two_leaf_session(tmp_path)
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    registry = ToolRegistry()
    register_memory_tools(registry, facade)
    gateway = ToolGateway(registry, TaskStore())
    combos = [
        {},
        {"chunk_id": cid, "session_id": sid},
    ]
    for arguments in combos:
        result = await gateway.submit(ToolCall(id="g", name="memory_get", arguments=arguments))
        assert result.is_error, arguments
        assert "exactly one" in result.content, arguments
    assert facade.archive.store.usage.get_map() == {}
    assert facade.archive.store.usage.search_map() == {}


@pytest.mark.asyncio
async def test_get_rejects_blank_and_wrong_type_selectors(tmp_path) -> None:
    sid, cid = _seed_two_leaf_session(tmp_path)
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    registry = ToolRegistry()
    register_memory_tools(registry, facade)
    gateway = ToolGateway(registry, TaskStore())
    before = (tmp_path / "memory" / "2026-08-13.md").read_text(encoding="utf-8")
    for arguments in (
        {"chunk_id": ""},
        {"session_id": "   "},
        {"chunk_id": 123},
        {"session_id": sid, "chunk_id": ""},
    ):
        result = await gateway.submit(ToolCall(id="g", name="memory_get", arguments=arguments))
        assert result.is_error, arguments
    assert facade.archive.store.usage.get_map() == {}
    assert (tmp_path / "memory" / "2026-08-13.md").read_text(encoding="utf-8") == before
    # The untouched session is still fully readable afterwards.
    got = await gateway.submit(
        ToolCall(id="g", name="memory_get", arguments={"session_id": sid})
    )
    assert not got.is_error
    assert "first cafe leaf" in got.content and "second cafe leaf" in got.content


def test_get_valid_selectors_still_work(tmp_path) -> None:
    sid, cid = _seed_two_leaf_session(tmp_path)
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    facade.archive.reindex()
    # Chunk read returns the leaf, not the whole session.
    leaf = facade.get(chunk_id=cid)
    assert "first cafe leaf" in leaf
    assert "second cafe leaf" not in leaf
    # Session read returns the whole session.
    session = facade.get(session_id=sid)
    assert "first cafe leaf" in session and "second cafe leaf" in session
    # Today's unindexed session still falls back to the daily file.
    today_sid = facade.remember("lunch", "eat pho today maybe")
    assert "eat pho today maybe" in facade.get(session_id=today_sid)


# --- GOAL-003: canonical search→get without TTL mutation ---


@pytest.mark.asyncio
async def test_canonical_search_then_get_reads_without_profile_mutation(tmp_path) -> None:
    import json

    (tmp_path / "SOUL.md").write_text(
        "# Soul\nsoul-copper-lantern first paragraph\n\nsoul-copper-lantern second paragraph\n",
        encoding="utf-8",
    )
    (tmp_path / "USER.md").write_text(
        "# User\nuser-silver-compass only paragraph\n",
        encoding="utf-8",
    )
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    registry = ToolRegistry()
    register_memory_tools(registry, facade)
    gateway = ToolGateway(registry, TaskStore())
    soul_before = (tmp_path / "SOUL.md").read_bytes()
    user_before = (tmp_path / "USER.md").read_bytes()
    for query, session_prefix in (
        ("soul-copper-lantern", "canonical#soul"),
        ("user-silver-compass", "canonical#user"),
    ):
        found = await gateway.submit(
            ToolCall(id="s", name="memory_search", arguments={"query": query})
        )
        assert not found.is_error
        hits = json.loads(found.content)["hits"]
        hit = next(h for h in hits if h["session_id"] == session_prefix)
        for selector in ({"chunk_id": hit["chunk_id"]}, {"session_id": hit["session_id"]}):
            got = await gateway.submit(
                ToolCall(id="g", name="memory_get", arguments=selector)
            )
            assert not got.is_error, (query, selector, got.content)
            assert query.split("-")[1] in got.content, (query, selector)
    assert (tmp_path / "SOUL.md").read_bytes() == soul_before
    assert (tmp_path / "USER.md").read_bytes() == user_before
    # Successful canonical reads still count toward get usage.
    assert facade.archive.store.usage.get_map()


@pytest.mark.asyncio
async def test_canonical_chunk_is_leaf_session_is_whole(tmp_path) -> None:
    import json

    (tmp_path / "SOUL.md").write_text(
        "# Soul\nleafsep-alpha first paragraph\n\nleafsep-alpha second paragraph\n",
        encoding="utf-8",
    )
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    registry = ToolRegistry()
    register_memory_tools(registry, facade)
    gateway = ToolGateway(registry, TaskStore())
    found = await gateway.submit(
        ToolCall(id="s", name="memory_search", arguments={"query": "leafsep-alpha"})
    )
    hit = next(
        h for h in json.loads(found.content)["hits"] if h["session_id"] == "canonical#soul"
    )
    leaf = await gateway.submit(
        ToolCall(id="g1", name="memory_get", arguments={"chunk_id": "canonical#soul#1"})
    )
    assert not leaf.is_error
    assert "first paragraph" in leaf.content
    assert "second paragraph" not in leaf.content
    whole = await gateway.submit(
        ToolCall(id="g2", name="memory_get", arguments={"session_id": "canonical#soul"})
    )
    assert not whole.is_error
    assert "first paragraph" in whole.content and "second paragraph" in whole.content


@pytest.mark.asyncio
async def test_get_path_selector_is_rejected(tmp_path) -> None:
    sid, _cid = _seed_two_leaf_session(tmp_path)
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    registry = ToolRegistry()
    register_memory_tools(registry, facade)
    gateway = ToolGateway(registry, TaskStore())
    refused = await gateway.submit(
        ToolCall(
            id="g",
            name="memory_get",
            arguments={"path": str(tmp_path / "memory" / "2026-08-13.md")},
        )
    )
    assert refused.is_error
    assert "unexpected argument: path" in refused.content
    with pytest.raises(TypeError):
        facade.get(path=str(tmp_path / "memory" / "2026-08-13.md"))


# Moved from test_b4_unification.py / test_b4_p2.py (B4 batch).
from pathlib import Path

def test_x8_facade_types_canonical_decode_errors(tmp_path: Path) -> None:
    from thyca.memory.archived import ArchiveError
    from thyca.memory.facade import MemoryFacade

    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    (tmp_path / "SOUL.md").write_bytes(b"\xff\xfe binary")
    with pytest.raises(ArchiveError, match="not valid UTF-8"):
        facade.stats()


def test_m5_blank_selector_falls_through_to_session(tmp_path: Path) -> None:
    from datetime import UTC, datetime, timedelta

    from thyca.memory.facade import MemoryFacade

    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    recent = datetime.now(UTC) - timedelta(days=5)
    sid = facade.remember(
        "t", "blank selector summary", importance=5, now=recent
    )
    facade.archive.reindex()  # real today: the file is now archivable past
    text = facade.archive.get(chunk_id="", session_id=sid)
    assert "blank selector summary" in text


def test_m5_recent_is_daily_only(tmp_path: Path) -> None:
    from datetime import UTC, datetime, timedelta

    from thyca.memory.facade import MemoryFacade

    (tmp_path / "SOUL.md").write_text(
        "## 08:00 — soul session <!-- thyca {\"id\":\"aaaaaaaa\",\"imp\":3} -->\n- soul leaf\n",
        encoding="utf-8",
    )
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    recent = datetime.now(UTC) - timedelta(days=5)
    facade.remember("t", "daily leaf here", importance=5, now=recent)
    facade.archive.reindex()  # real today: the file is now archivable past
    hits = facade.recent()
    assert hits
    assert {hit.source_kind for hit in hits} == {"daily"}
