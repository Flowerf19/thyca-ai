"""CBE-3: A2A by reference — refs, 1000-char excerpt, artifact fetch."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from thyca.bots.admin_tools import AdminDeps, register_admin_tools
from thyca.bots.artifacts import BusArtifactStore
from thyca.bots.bus import EXCERPT_CHARS, Bus, BusError
from thyca.bots.card import BotCard
from thyca.bots.context import BotTurnContext, bind_bot_turn_context, reset_bot_turn_context
from thyca.bots.registry import BotRegistry
from thyca.bots.runtime import BotRuntime
from thyca.tools.registry import ToolRegistry


def test_large_payload_excerpted_and_fully_fetchable_via_store() -> None:
    bus = Bus()
    bus.register("coder")
    body = "x" * 5000
    envelope = bus.send(
        sender="thyca", recipient="coder", kind="delegate",
        session_id="room1", payload=body, refs=["/tmp/spec.md", "2026-08-01#abc"],
    )
    # Prompt-facing envelope carries only the excerpt + refs.
    assert len(envelope.payload) == EXCERPT_CHARS
    assert envelope.truncated is True
    assert envelope.refs == ("/tmp/spec.md", "2026-08-01#abc")
    assert envelope.to_dict()["refs"] == ["/tmp/spec.md", "2026-08-01#abc"]
    # Full body is fetchable from the bounded artifact store by span_id.
    assert bus.artifacts.get(envelope.span_id) == body
    # The queued copy the recipient prompt sees is the excerpt, not the body.
    queued = bus.drain("coder")[0]
    assert len(queued.payload) == EXCERPT_CHARS


def test_short_payload_not_truncated_and_no_artifact() -> None:
    bus = Bus()
    bus.register("coder")
    envelope = bus.send(sender="a", recipient="coder", kind="message",
                        session_id="r", payload="short")
    assert envelope.payload == "short"
    assert envelope.truncated is False
    assert bus.artifacts.get(envelope.span_id) is None


def test_hop_limit_still_holds_for_large_payloads() -> None:
    bus = Bus(max_hops=2)
    bus.register("a")
    bus.register("b")
    trace = None
    for _ in range(2):
        envelope = bus.send(sender="a", recipient="b", kind="message",
                            session_id="r", payload="y" * 3000, trace_id=trace)
        trace = envelope.trace_id
    with pytest.raises(BusError):
        bus.send(sender="b", recipient="a", kind="message",
                 session_id="r", payload="y" * 3000, trace_id=trace)
    assert bus.hops_of(trace) == 2


def test_artifact_store_bounded_oldest_first() -> None:
    store = BusArtifactStore(max_artifacts=3, max_total_chars=100)
    store.put("sp_1", "a" * 40)
    store.put("sp_2", "b" * 40)
    store.put("sp_3", "c" * 40)  # total 120 > 100 -> sp_1 evicted
    assert store.get("sp_1") is None
    assert store.get("sp_3") == "c" * 40
    assert len(store) <= 3
    assert store.total_chars <= 100


def test_bot_fetch_tool_returns_full_body(tmp_path: Path) -> None:
    registry = BotRegistry(tmp_path)
    registry.ensure_thyca()
    registry.create(BotCard(bot_id="coder", name="coder", role="C",
                            description="D", persona="P"))
    bus = Bus()
    runtime = BotRuntime(tmp_path, registry, bus)
    tools = ToolRegistry()
    register_admin_tools(tools, AdminDeps(registry, runtime, lambda: None))
    envelope = runtime.send(
        sender="thyca", recipient="coder", kind="delegate",
        session_id="room1", payload="z" * 4000, refs=["memory#x"],
    )
    spec = tools.get("bot_fetch")
    assert spec is not None
    token = bind_bot_turn_context(BotTurnContext(sender_id="coder", room_id="room1"))
    try:
        out = json.loads(asyncio.run(spec.handler({"span_id": envelope.span_id})))
        missing = json.loads(asyncio.run(spec.handler({"span_id": "sp_nope"})))
    finally:
        reset_bot_turn_context(token)
    assert out["ok"] is True
    assert out["content"] == "z" * 4000
    assert missing["ok"] is False


def test_bot_send_accepts_refs_and_child_sees_fetch_not_admin(tmp_path: Path) -> None:
    registry = BotRegistry(tmp_path)
    registry.ensure_thyca()
    registry.create(BotCard(bot_id="coder", name="coder", role="C",
                            description="D", persona="P"))
    bus = Bus()
    runtime = BotRuntime(tmp_path, registry, bus)
    tools = ToolRegistry()
    register_admin_tools(tools, AdminDeps(registry, runtime, lambda: None))
    token = bind_bot_turn_context(BotTurnContext(sender_id="thyca", room_id="room1"))
    try:
        out = json.loads(asyncio.run(tools.get("bot_send").handler(
            {"bot_id": "coder", "message": "see refs", "refs": ["/tmp/a.md"]}
        )))
    finally:
        reset_bot_turn_context(token)
    assert out["ok"] is True
    assert bus.log()[0].envelope.refs == ("/tmp/a.md",)

    from thyca.bots.admin_tools import visible_for_bot
    from thyca.bots.runtime import filtered_tool_schemas

    schemas = [
        {"type": "function", "function": {"name": name}}
        for name in ("bot_send", "bot_fetch", "bot_create", "task_open", "read")
    ]
    names = {s["function"]["name"] for s in visible_for_bot(schemas, "coder")}
    assert "bot_fetch" in names and "bot_send" in names
    assert "bot_create" not in names

    # Legacy card: written before bot_fetch/task_open existed, so its
    # tools list cannot name them — they must still be kept as current
    # baseline peer tools (hidden/memory/bash policies aside).
    card = BotCard(bot_id="coder", name="coder", role="C", description="D",
                   persona="P", tools=("read",))
    kept = {s["function"]["name"] for s in filtered_tool_schemas(schemas, card)}
    assert {"bot_fetch", "task_open", "read"} <= kept
    assert "bot_create" not in kept
