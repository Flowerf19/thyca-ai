from __future__ import annotations

import pytest

from thyca.protocol import ToolCall
from thyca.tools.memory import MemoryFacade
from thyca.tools.memory_tools import register_memory_tools
from thyca.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_memory_remember_and_get_roundtrip(tmp_path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    registry = ToolRegistry()
    register_memory_tools(registry, facade)
    remembered = await registry.dispatch(
        ToolCall(
            id="r1",
            name="memory_remember",
            arguments={"topic": "cafe", "summary": "cafedenunique"},
        )
    )
    assert not remembered.is_error
    assert remembered.content[10] == "#"
    got = await registry.dispatch(
        ToolCall(id="g1", name="memory_get", arguments={"session_id": remembered.content})
    )
    assert not got.is_error
    assert "cafedenunique" in got.content


@pytest.mark.asyncio
async def test_memory_remember_rejects_soul_target(tmp_path) -> None:
    registry = ToolRegistry()
    register_memory_tools(registry, MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh"))
    result = await registry.dispatch(
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
    remembered = await registry.dispatch(
        ToolCall(
            id="r1",
            name="memory_remember",
            arguments={"topic": "cafe", "summary": "cafedenunique"},
        )
    )
    assert not remembered.is_error
    updated = await registry.dispatch(
        ToolCall(
            id="u1",
            name="memory_update",
            arguments={"session_id": remembered.content, "topic": "tra da"},
        )
    )
    assert not updated.is_error
    got = await registry.dispatch(
        ToolCall(id="g1", name="memory_get", arguments={"session_id": remembered.content})
    )
    assert not got.is_error
    assert "tra da" in got.content
    assert "cafedenunique" in got.content


@pytest.mark.asyncio
async def test_remember_injects_bound_chat_and_update_rejects_chat(tmp_path) -> None:
    from thyca.memory.heading import parse_heading
    from thyca.tools.memory_tools import bind_chat_session, reset_chat_session

    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    registry = ToolRegistry()
    register_memory_tools(registry, facade)
    token = bind_chat_session("2026-09-16T14-39-01_a1b2")
    try:
        remembered = await registry.dispatch(
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
    daily = next((tmp_path / "memory").glob("*.md"))
    meta = next(m for line in daily.read_text(encoding="utf-8").splitlines() if (m := parse_heading(line)))
    assert meta.chat == "2026-09-16T14-39-01_a1b2"
    assert meta.proj == "/home/flowerf/Projects/thyca-ai"

    refused = await registry.dispatch(
        ToolCall(
            id="u1",
            name="memory_update",
            arguments={"session_id": remembered.content, "chat": "nope"},
        )
    )
    assert refused.is_error
    assert "unexpected argument" in refused.content
