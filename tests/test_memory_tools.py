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
