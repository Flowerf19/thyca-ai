"""BE-2/BE-6-meta: bus envelopes, hop limit, tool filtering, gateway policy."""
from __future__ import annotations

import pytest

from thyca.bots.bus import Bus, BusError
from thyca.bots.card import BotCard
from thyca.bots.policy import BotPolicy, bind_hidden_tools, reset_hidden_tools
from thyca.bots.runtime import filtered_tool_schemas
from thyca.core.protocol import Message, ToolCall
from thyca.tools.gateway.policy import PolicyDenied


def _schemas(*names: str) -> list[dict]:
    return [{"type": "function", "function": {"name": name}} for name in names]


def test_bus_send_drain_round_trip() -> None:
    bus = Bus()
    bus.register("coder")
    envelope = bus.send(sender="thyca", recipient="coder", kind="delegate",
                        session_id="room1", payload="do it")
    assert envelope.trace_id.startswith("tr_")
    assert bus.pending("coder") == 1
    got = bus.drain("coder")
    assert [item.payload for item in got] == ["do it"]
    assert bus.pending("coder") == 0
    assert bus.log()[0].envelope.kind == "delegate"


def test_bus_rejects_unknown_kind_and_recipient() -> None:
    bus = Bus()
    bus.register("coder")
    with pytest.raises(BusError):
        bus.send(sender="a", recipient="coder", kind="nope",
                 session_id="r", payload="x")
    with pytest.raises(BusError):
        bus.send(sender="a", recipient="ghost", kind="message",
                 session_id="r", payload="x")


def test_bus_hop_limit_per_trace() -> None:
    bus = Bus(max_hops=3)
    bus.register("a")
    bus.register("b")
    trace = None
    for _ in range(3):
        envelope = bus.send(sender="a", recipient="b", kind="message",
                            session_id="r", payload="x", trace_id=trace)
        trace = envelope.trace_id
    with pytest.raises(BusError):
        bus.send(sender="b", recipient="a", kind="message",
                 session_id="r", payload="x", trace_id=trace)
    assert bus.hops_of(trace) == 3


def _card(**overrides) -> BotCard:
    base = dict(bot_id="coder", name="coder", role="Coder",
                description="Codes.", persona="persona")
    base.update(overrides)
    return BotCard(**base)


def test_compact_tool_is_baseline_for_child_bots() -> None:
    # A pre-existing card that never listed "compact" still gains it via
    # BASELINE_BOT_TOOLS (the same rule that grants memory_* retroactively);
    # tools outside the card list and baseline stay dropped.
    card = _card(tools=("read",))
    out = filtered_tool_schemas(_schemas("read", "compact", "write"), card)
    names = [s["function"]["name"] for s in out]
    assert "compact" in names
    assert "read" in names
    assert "write" not in names


def test_hidden_tools_win_over_allowlist() -> None:
    card = _card(tools=("read", "bash"), hidden_tools=("bash",))
    out = filtered_tool_schemas(_schemas("read", "bash", "write"), card)
    assert [s["function"]["name"] for s in out] == ["read"]


def test_child_bot_never_sees_admin_tools() -> None:
    card = _card(tools=("bot_delegate", "read"))
    out = filtered_tool_schemas(_schemas("bot_delegate", "read"), card)
    assert [s["function"]["name"] for s in out] == ["read"]


def test_thyca_keeps_admin_tools() -> None:
    card = _card(bot_id="thyca", name="thyca", system=True)
    out = filtered_tool_schemas(_schemas("bot_delegate", "read"), card)
    assert {s["function"]["name"] for s in out} == {"bot_delegate", "read"}


def test_memory_and_bash_filters() -> None:
    from thyca.bots.card import BotMemoryPolicy

    card = _card(memory=BotMemoryPolicy(read=False, write=False),
                 bash="deny",
                 tools=("memory_search", "memory_remember",
                        "bash", "read"))
    # Legacy card without the newer baseline peer tools in `tools`:
    # bot_fetch/task_open are always kept, while the memory and bash
    # masking above still removes its own families.
    out = filtered_tool_schemas(
        _schemas("memory_search", "memory_remember",
                 "bash", "bot_fetch", "task_open", "read", "memory_tasks"),
        card,
    )
    assert [s["function"]["name"] for s in out] == ["bot_fetch", "task_open", "read"]


def test_child_empty_tools_allowlist_is_fail_closed() -> None:
    # Non-system bot, empty `tools`: NO tools at all — poll tools are not
    # implicit; they appear only when explicitly listed.
    card = _card()
    out = filtered_tool_schemas(_schemas("read", "tool_read", "tool_kill"), card)
    assert out == []
    card2 = _card(tools=("read", "tool_read"))
    out2 = filtered_tool_schemas(_schemas("read", "tool_read", "tool_kill"), card2)
    assert [s["function"]["name"] for s in out2] == ["read", "tool_read"]
    # The Thyca system card keeps its full snapshot with an empty list.
    thyca = _card(bot_id="thyca", name="thyca", system=True)
    out3 = filtered_tool_schemas(_schemas("read", "bash"), thyca)
    assert {s["function"]["name"] for s in out3} == {"read", "bash"}


def test_gateway_policy_denies_hidden_by_name() -> None:
    policy = BotPolicy()
    call = ToolCall(id="c1", name="bash", arguments={})
    policy.check(call)  # allow-all default
    token = bind_hidden_tools(frozenset({"bash"}))
    try:
        with pytest.raises(PolicyDenied):
            policy.check(call)
        policy.check(ToolCall(id="c2", name="read", arguments={}))
    finally:
        reset_hidden_tools(token)
    policy.check(call)  # allow-all again after reset


def test_bus_rejects_self_send() -> None:
    bus = Bus()
    bus.register("coder")
    with pytest.raises(BusError):
        bus.send(sender="coder", recipient="coder", kind="message",
                 session_id="r", payload="note to self")
    assert bus.pending("coder") == 0


def test_memory_tasks_in_baseline_read_gated_hidden_wins() -> None:
    from thyca.bots.card import BASELINE_BOT_TOOLS, BotMemoryPolicy

    assert "memory_tasks" in BASELINE_BOT_TOOLS
    # Default card: visible like the other read tools.
    card = _card(tools=("memory_tasks", "read"))
    out = filtered_tool_schemas(_schemas("memory_tasks", "read"), card)
    assert [s["function"]["name"] for s in out] == ["memory_tasks", "read"]
    # memory.read=false hides it even when listed by hand.
    nor = _card(memory=BotMemoryPolicy(read=False, write=True),
                tools=("memory_tasks", "read"))
    assert [s["function"]["name"]
            for s in filtered_tool_schemas(_schemas("memory_tasks", "read"), nor)] == ["read"]
    # hidden_tools still wins.
    hid = _card(tools=("memory_tasks", "read"), hidden_tools=("memory_tasks",))
    assert [s["function"]["name"]
            for s in filtered_tool_schemas(_schemas("memory_tasks", "read"), hid)] == ["read"]


def test_private_followup_tools_in_baseline() -> None:
    from thyca.bots.card import BASELINE_BOT_TOOLS

    assert "ask_private" in BASELINE_BOT_TOOLS
    assert "private_followup" in BASELINE_BOT_TOOLS
    card = _card(tools=())
    out = filtered_tool_schemas(
        _schemas("ask_private", "private_followup", "read"), card)
    assert [s["function"]["name"] for s in out] == [
        "ask_private", "private_followup"]


def test_loop_limit_marker_does_not_double_count_aggregate_or_trace(
    tmp_path,
) -> None:
    from thyca.bots.runtime import (
        BotTurnResult,
        _aggregate_turn_meta,
        _aggregate_turn_rounds,
    )
    from thyca.sessions import SessionManager
    from thyca.serve.trace import turns_from_session

    call = ToolCall(id="c1", name="read", arguments={})
    meta = {
        "kind": "llm",
        "bot_id": "coder",
        "model": "m",
        "usage": {"prompt_tokens": 11, "completion_tokens": 2},
        "cost_usd": 0.01,
        "latency_ms": 5,
        "span_id": "sp_1",
    }
    rows = [
        Message(role="assistant", content="", tool_calls=[call], meta=meta),
        Message(
            role="assistant",
            content="loop limit reached",
            meta={**meta, "status": "loop_limit"},
        ),
    ]
    model, usage, cost, latency, requests, tool_calls = _aggregate_turn_meta(rows)
    rounds = _aggregate_turn_rounds(rows)
    assert (model, usage, cost, latency, requests, tool_calls, rounds) == (
        "m",
        {"prompt_tokens": 11, "completion_tokens": 2},
        0.01,
        5,
        1,
        1,
        1,
    )

    aggregate = BotTurnResult(
        reply="loop limit reached",
        bot_id="coder",
        span_id="sp_1",
        model=model,
        usage=usage,
        cost_usd=cost,
        latency_ms=latency,
        requests=requests,
        tool_calls=tool_calls,
        rounds=rounds,
        status="loop_limit",
    )
    manager = SessionManager(tmp_path)
    session = manager.create()
    manager.append(Message(role="user", content="go"))
    manager.append(
        Message(
            role="assistant",
            content=aggregate.reply,
            meta=aggregate.room_meta(
                kind="message", trace_id="tr_1", parent_span_id=None
            ),
        )
    )
    turn = turns_from_session(manager.current)[0]
    assert turn.status == "loop_limit"
    assert (turn.rounds, turn.requests, turn.tool_calls) == (1, 1, 1)
    assert (turn.prompt_tokens, turn.completion_tokens) == (11, 2)
    assert turn.cost_usd == 0.01
    assert turn.latency_ms == 5


def test_aggregate_counts_internal_rounds_not_rows() -> None:
    from thyca.bots.runtime import _aggregate_turn_meta
    from thyca.core.protocol import Message

    def row(kind=None, **meta):
        return Message(role="assistant", content="x", meta={"kind": kind, **meta})

    model, usage, cost, latency, requests, tool_calls = _aggregate_turn_meta([
        row("llm", model="m", usage={"prompt_tokens": 3}, cost_usd=0.1),
        row("llm", model="m", usage={"prompt_tokens": 5}, cost_usd=0.2),
        Message(role="tool", content="out"),
        Message(role="assistant", content="stop",
                meta={"kind": "llm", "status": "context_limit"}),
    ])
    assert requests == 2
    assert tool_calls == 0
    assert usage == {"prompt_tokens": 8}
    assert cost == pytest.approx(0.3)


def test_aggregate_counts_tool_calls_on_same_rows() -> None:
    from thyca.bots.runtime import _aggregate_turn_meta
    from thyca.core.protocol import Message, ToolCall

    def call(cid):
        return ToolCall(id=cid, name="read", arguments={})

    _, _, _, _, requests, tool_calls = _aggregate_turn_meta([
        Message(role="assistant", content="a",
                tool_calls=[call("c1"), call("c2")],
                meta={"kind": "llm"}),
        Message(role="assistant", content="b",
                tool_calls=[call("c3")],
                meta={"kind": "llm"}),
        Message(role="assistant", content="stop",
                tool_calls=[call("c4")],
                meta={"kind": "llm", "status": "context_limit"}),
    ])
    assert requests == 2
    assert tool_calls == 3


def test_room_meta_carries_request_count() -> None:
    from thyca.bots.runtime import BotTurnResult

    result = BotTurnResult(
        reply="done", bot_id="coder", span_id="sp_1", model="m",
        usage={"prompt_tokens": 2}, cost_usd=0.01, latency_ms=5,
        requests=3, tool_calls=4,
    )
    meta = result.room_meta(kind="result", trace_id="t", parent_span_id=None)
    assert meta["requests"] == 3
    assert meta["tool_call_count"] == 4
