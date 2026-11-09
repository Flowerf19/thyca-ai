from __future__ import annotations

import json

import pytest

from thyca.core.protocol import ToolCall
from thyca.memories.facade import MemoryFacade
from thyca.tools.memory_tools import register_memory_tools
from thyca.tools.gateway import ToolGateway
from thyca.tools.registry import ToolRegistry
from thyca.tools.task_store import TaskStore


@pytest.mark.asyncio
async def test_memory_remember_and_get_roundtrip(tmp_path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    registry = ToolRegistry()
    register_memory_tools(registry, facade)
    gateway = ToolGateway(registry, TaskStore())
    remembered = await gateway.submit(
        ToolCall(
            id="r1",
            name="memory_remember",
            arguments={"topic": "cafe", "summary": "cafedenunique"},
        )
    )
    assert not remembered.is_error
    payload = json.loads(remembered.content)
    assert payload["session_id"][10] == "#"
    assert payload["task_id"] is None
    got = await gateway.submit(
        ToolCall(id="g1", name="memory_get", arguments={"session_id": payload["session_id"]})
    )
    assert not got.is_error
    assert "cafedenunique" in got.content


@pytest.mark.asyncio
async def test_memory_remember_rejects_soul_target(tmp_path) -> None:
    registry = ToolRegistry()
    register_memory_tools(registry, MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh"))
    gateway = ToolGateway(registry, TaskStore())
    result = await gateway.submit(
        ToolCall(
            id="bad",
            name="memory_remember",
            arguments={"topic": "me", "summary": "x", "target": "soul"},
        )
    )
    assert result.is_error
    assert "unexpected argument" in result.content


@pytest.mark.asyncio
async def test_memory_update_keeps_session_id(tmp_path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    registry = ToolRegistry()
    register_memory_tools(registry, facade)
    gateway = ToolGateway(registry, TaskStore())
    remembered = await gateway.submit(
        ToolCall(
            id="r1",
            name="memory_remember",
            arguments={"topic": "cafe", "summary": "cafedenunique"},
        )
    )
    assert not remembered.is_error
    sid = json.loads(remembered.content)["session_id"]
    updated = await gateway.submit(
        ToolCall(
            id="u1",
            name="memory_update",
            arguments={"session_id": sid, "topic": "tra da"},
        )
    )
    assert not updated.is_error
    got = await gateway.submit(
        ToolCall(id="g1", name="memory_get", arguments={"session_id": sid})
    )
    assert not got.is_error
    assert "tra da" in got.content
    assert "cafedenunique" in got.content


@pytest.mark.asyncio
async def test_memory_search_returns_owner_dm_hit_in_group(tmp_path) -> None:
    # Tool-path search carries the owner exemption: coder's DM-origin hit
    # surfaces under a bound group view.
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from thyca.core.channel import WindowRef
    from thyca.memories.identity import bind_bot_id, reset_bot_id
    from thyca.memories.visibility import MemoryView, bound_memory_view

    from thyca.conversations.runtime.store import SessionStore
    from thyca.conversations.store.runtime import ensure_runtime_store
    from thyca.conversations.runtime import SessionManager

    tz = ZoneInfo("Asia/Ho_Chi_Minh")
    yesterday = (datetime.now(tz).date() - timedelta(days=1)).isoformat()
    ensure_runtime_store(tmp_path)
    SessionManager(tmp_path / "sessions").ensure_dm("coder")
    store = SessionStore(tmp_path / "sessions")
    store.ensure_dir()
    group_id = "group-" + "d" * 32
    store.append_bots(store.create(group_id), ["thyca", "coder"])

    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    facade.remember(
        "OWNERDM-GATEWAY-HIT", "OWNERDM-GATEWAY-BODY",
        now=datetime.fromisoformat(f"{yesterday}T12:00:00+07:00"),
        chat="coder", bots=["coder"])
    facade._refresh_index()
    registry = ToolRegistry()
    register_memory_tools(registry, facade)
    gateway = ToolGateway(registry, TaskStore())
    window = WindowRef(
        window_id=group_id, channel_id="webui", kind="group",
        participants=("thyca", "coder"))
    token = bind_bot_id("coder")
    try:
        with bound_memory_view(MemoryView(window, "coder", tmp_path)):
            result = await gateway.submit(
                ToolCall(id="s1", name="memory_search",
                         arguments={"query": "OWNERDM-GATEWAY-HIT"}))
    finally:
        reset_bot_id(token)
    assert not result.is_error
    assert "OWNERDM-GATEWAY-HIT" in result.content


@pytest.mark.asyncio
async def test_memory_recent_returns_owner_dm_hit_in_group(tmp_path) -> None:
    # Tool-path recent carries the owner exemption: coder's DM-origin hit
    # surfaces under a bound group view.
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from thyca.core.channel import WindowRef
    from thyca.memories.identity import bind_bot_id, reset_bot_id
    from thyca.memories.visibility import MemoryView, bound_memory_view

    from thyca.conversations.runtime.store import SessionStore
    from thyca.conversations.store.runtime import ensure_runtime_store
    from thyca.conversations.runtime import SessionManager

    tz = ZoneInfo("Asia/Ho_Chi_Minh")
    yesterday = (datetime.now(tz).date() - timedelta(days=1)).isoformat()
    ensure_runtime_store(tmp_path)
    SessionManager(tmp_path / "sessions").ensure_dm("coder")
    store = SessionStore(tmp_path / "sessions")
    store.ensure_dir()
    group_id = "group-" + "e" * 32
    store.append_bots(store.create(group_id), ["thyca", "coder"])

    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    facade.remember(
        "OWNERDM-GATEWAY-RECENT", "OWNERDM-GATEWAY-RECENT-BODY",
        now=datetime.fromisoformat(f"{yesterday}T12:00:00+07:00"),
        chat="coder", bots=["coder"])
    facade._refresh_index()
    registry = ToolRegistry()
    register_memory_tools(registry, facade)
    gateway = ToolGateway(registry, TaskStore())
    window = WindowRef(
        window_id=group_id, channel_id="webui", kind="group",
        participants=("thyca", "coder"))
    token = bind_bot_id("coder")
    try:
        with bound_memory_view(MemoryView(window, "coder", tmp_path)):
            result = await gateway.submit(
                ToolCall(id="r1", name="memory_recent", arguments={}))
    finally:
        reset_bot_id(token)
    assert not result.is_error
    assert "OWNERDM-GATEWAY-RECENT" in result.content


@pytest.mark.asyncio
async def test_remember_injects_bound_chat_and_update_rejects_chat(tmp_path) -> None:
    from thyca.memories.heading import parse_heading
    from thyca.tools.memory_tools import bind_chat_session, reset_chat_session

    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    registry = ToolRegistry()
    register_memory_tools(registry, facade)
    gateway = ToolGateway(registry, TaskStore())
    token = bind_chat_session("2026-09-16T14-39-01_a1b2")
    try:
        remembered = await gateway.submit(
            ToolCall(
                id="r1",
                name="memory_remember",
                arguments={
                    "topic": "link",
                    "summary": "bound-chat-token",
                    "proj": "/home/flowerf/Projects/thyca-ai",
                },
            )
        )
    finally:
        reset_chat_session(token)
    assert not remembered.is_error
    sid = json.loads(remembered.content)["session_id"]
    daily = next((tmp_path / "memory").glob("*.md"))
    meta = next(m for line in daily.read_text(encoding="utf-8").splitlines() if (m := parse_heading(line)))
    assert meta.chat == "2026-09-16T14-39-01_a1b2"
    assert meta.proj == "/home/flowerf/Projects/thyca-ai"

    refused = await gateway.submit(
        ToolCall(
            id="u1",
            name="memory_update",
            arguments={"session_id": sid, "chat": "nope"},
        )
    )
    assert refused.is_error
    assert "unexpected argument" in refused.content
