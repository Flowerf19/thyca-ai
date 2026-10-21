"""Agent-callable session tools (compact): context-gated handler."""
from __future__ import annotations

import asyncio

from thyca.bots.context import (
    BotTurnContext,
    bind_bot_turn_context,
    reset_bot_turn_context,
)
from thyca.tools.registry import ToolRegistry
from thyca.tools.session_tools import compact_spec, register_session_tools


def test_compact_unavailable_outside_bot_turn() -> None:
    out = asyncio.run(compact_spec().handler({}))
    assert "unavailable" in out


def test_compact_calls_turn_callback() -> None:
    async def fake_compact() -> dict:
        return {"compacted": True, "summary": "llm"}

    ctx = BotTurnContext(sender_id="coder", room_id="room1", compact=fake_compact)
    token = bind_bot_turn_context(ctx)
    try:
        out = asyncio.run(compact_spec().handler({}))
        assert out == "compacted: summary=llm"
    finally:
        reset_bot_turn_context(token)


def test_compact_noop_and_failure_are_honest() -> None:
    async def noop() -> dict:
        return {"compacted": False}

    async def broken() -> dict:
        raise RuntimeError("summarizer down")

    ctx = BotTurnContext(sender_id="coder", room_id="room1", compact=noop)
    token = bind_bot_turn_context(ctx)
    try:
        out = asyncio.run(compact_spec().handler({}))
        assert "no compaction needed" in out
    finally:
        reset_bot_turn_context(token)

    ctx2 = BotTurnContext(sender_id="coder", room_id="room1", compact=broken)
    token2 = bind_bot_turn_context(ctx2)
    try:
        out = asyncio.run(compact_spec().handler({}))
        assert out == "compact failed: RuntimeError"
    finally:
        reset_bot_turn_context(token2)


def test_register_session_tools_is_idempotent() -> None:
    registry = ToolRegistry()
    register_session_tools(registry)
    register_session_tools(registry)
    assert registry.get("compact") is not None
    assert len(registry.to_openai_schema()) == 1