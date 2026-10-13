"""BE-2/BE-6-meta: bus envelopes, hop limit, tool filtering, gateway policy."""
from __future__ import annotations

import pytest

from thyca.bots.bus import Bus, BusError
from thyca.bots.card import BotCard
from thyca.bots.policy import BotPolicy, bind_hidden_tools, reset_hidden_tools
from thyca.bots.runtime import filtered_tool_schemas
from thyca.core.protocol import ToolCall
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

    card = _card(memory=BotMemoryPolicy(read=False, write=False, propose_team=False),
                 bash="deny")
    out = filtered_tool_schemas(
        _schemas("memory_search", "memory_remember", "memory_propose_team",
                 "bash", "read"),
        card,
    )
    assert [s["function"]["name"] for s in out] == ["read"]


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
