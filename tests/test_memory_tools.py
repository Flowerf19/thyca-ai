from __future__ import annotations

import json

import pytest

from thyca.protocol import ToolCall
from thyca.tools.memory import MemoryFacade
from thyca.tools.memory_tools import register_memory_tools
from thyca.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_memory_remember_and_search_roundtrip(tmp_path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    registry = ToolRegistry()
    register_memory_tools(registry, facade)
    remembered = await registry.dispatch(
        ToolCall(
            id="r1",
            name="memory_remember",
            arguments={"topic": "cafe", "summary": "cafedenunique", "target": "memory"},
        )
    )
    assert not remembered.is_error
    assert remembered.content.startswith("memory#")
    found = await registry.dispatch(
        ToolCall(id="s1", name="memory_search", arguments={"query": "cafedenunique"})
    )
    assert not found.is_error
    payload = json.loads(found.content)
    assert payload["hits"]
    assert any("cafedenunique" in hit["snippet"] for hit in payload["hits"])


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
    assert "unexpected argument" in result.content or "daily|memory" in result.content
