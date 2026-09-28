"""Q1 mid-turn context guard — shrink policy + loop integration."""
from __future__ import annotations

import asyncio
from pathlib import Path

from thyca.agent.act import Act
from thyca.agent.assemble import Assemble
from thyca.agent.events import TurnEvent
from thyca.agent.loop import AgentLoop
from thyca.agent.observe import Observe
from thyca.agent.shrink import (
    FASTPATH_HEAD_CHARS,
    REFETCH_HEAD_CHARS,
    TRIGGER_RATIO,
    estimate_wire_tokens,
    find_exec_ids,
    shrink_stage_messages,
)
from thyca.agent.think import Think
from thyca.core.protocol import Message, ToolCall, ToolResult
from thyca.llm.llm_base import ChatReply
from thyca.sessions import SessionManager


def _tool(content: str, round_no: int | None = None, call_id: str = "c1") -> Message:
    meta = {"round": round_no} if round_no is not None else None
    return Message(role="tool", content=content, tool_call_id=call_id, meta=meta)


def test_find_exec_ids_markers() -> None:
    assert find_exec_ids('out\nread more: tool_read id="exec3"') == {"exec3"}
    assert find_exec_ids("still running: exec12\npoll") == {"exec12"}
    assert find_exec_ids("started: exec7\nrunning") == {"exec7"}
    assert find_exec_ids("plain output") == set()
    assert find_exec_ids('tool_read id="exec1" and tool_read id="exec2"') == {
        "exec1",
        "exec2",
    }


def test_refetchable_shape_head_plus_pointer() -> None:
    body = "x" * 3000
    content = f"{body}\nread more: tool_read id=\"exec9\""
    messages = [_tool(content, 1)]
    estimate = estimate_wire_tokens(messages)
    context = int(estimate / 0.85)
    shrunk, count, hidden, _ = shrink_stage_messages(
        messages, current_round=10, context_tokens=context
    )
    assert count == 1
    assert hidden > 0
    new_content = shrunk[0].content or ""
    assert new_content.startswith("x" * REFETCH_HEAD_CHARS)
    assert new_content.endswith(
        f'[shrunk: {hidden} bytes hidden; re-read via tool_read id="exec9" offset/limit]'
    )
    assert shrunk[0].role == "tool"
    assert shrunk[0].tool_call_id == "c1"
    assert shrunk[0].meta == {"round": 1}
    # Original untouched (in-memory-only).
    assert messages[0].content == content


def test_fastpath_shape_head_plus_rerun() -> None:
    content = "y" * 5000
    messages = [_tool(content, 1)]
    estimate = estimate_wire_tokens(messages)
    context = int(estimate / 0.85)
    shrunk, count, hidden, _ = shrink_stage_messages(
        messages, current_round=10, context_tokens=context
    )
    assert count == 1
    new_content = shrunk[0].content or ""
    assert new_content.startswith("y" * FASTPATH_HEAD_CHARS)
    assert new_content.endswith(
        f"[shrunk: {hidden} bytes hidden; re-run the tool if needed]"
    )


def test_ambiguous_exec_refs_kept_intact() -> None:
    content = "z" * 3000 + '\ntool_read id="exec1" and tool_read id="exec2"'
    messages = [_tool(content, 1)]
    estimate = estimate_wire_tokens(messages)
    context = int(estimate / 0.85)
    shrunk, count, _, _ = shrink_stage_messages(
        messages, current_round=10, context_tokens=context
    )
    assert count == 0
    assert shrunk[0].content == content


def test_last_three_rounds_protected() -> None:
    messages = [_tool("a" * 4000, round_no) for round_no in (1, 2, 3, 4, 5)]
    estimate = estimate_wire_tokens(messages)
    context = int(estimate / 0.85)
    shrunk, count, _, _ = shrink_stage_messages(
        messages, current_round=5, context_tokens=context
    )
    assert count >= 1
    # Rounds 3,4,5 never touched.
    for original, new in zip(messages[2:], shrunk[2:]):
        assert new.content == original.content
    # At least round 1 (oldest eligible) shrunk.
    assert shrunk[0].content != messages[0].content


def test_refetchable_before_fastpath_despite_age() -> None:
    fastpath = _tool("f" * 6000, 1, call_id="old")
    refetch = _tool("r" * 6000 + '\nread more: tool_read id="exec4"', 2, call_id="new")
    messages = [fastpath, refetch]
    # One shrink must suffice: total just over 80%, one refetch save drops under 70%.
    estimate = estimate_wire_tokens(messages)
    context = int(estimate / 0.82)
    shrunk, count, _, new_estimate = shrink_stage_messages(
        messages, current_round=10, context_tokens=context
    )
    assert count == 1
    assert new_estimate < int(context * 0.7)
    assert shrunk[0].content == fastpath.content  # fast-path intact
    assert shrunk[1].content != refetch.content  # refetchable shrunk first
    assert 'tool_read id="exec4"' in (shrunk[1].content or "")


def test_user_and_system_never_shrunk() -> None:
    messages = [
        Message(role="system", content="s" * 5000),
        Message(role="user", content="u" * 5000),
        Message(role="system", content="[compaction: omitted 1 messages/1 turns; excerpt: e]"),
    ]
    estimate = estimate_wire_tokens(messages)
    context = int(estimate / 0.9)
    shrunk, count, _, _ = shrink_stage_messages(
        messages, current_round=10, context_tokens=context
    )
    assert count == 0
    assert [message.content for message in shrunk] == [
        message.content for message in messages
    ]


def test_context_shrunk_event_shape() -> None:
    event = TurnEvent(type="context.shrunk", round=2, tool_count=3, hidden_bytes=1200)
    assert event.to_dict() == {
        "type": "context.shrunk",
        "round": 2,
        "tool_count": 3,
        "hidden_bytes": 1200,
    }


class _FakeLLM:
    def __init__(self, replies: list[ChatReply]) -> None:
        self.replies = replies
        self.requests: list[list[Message]] = []

    async def chat(self, messages: list[Message], tools=None) -> ChatReply:
        self.requests.append(list(messages))
        return self.replies.pop(0)


class _FakeDispatcher:
    def __init__(self, results: dict[str, ToolResult]) -> None:
        self.results = results

    async def submit(self, call: ToolCall) -> ToolResult:
        return self.results[call.id]


def _loop(
    manager: SessionManager, llm: _FakeLLM, dispatcher: _FakeDispatcher, context_tokens: int
) -> AgentLoop:
    return AgentLoop(
        sessions=manager,
        assemble=Assemble(),
        think=Think(llm),
        act=Act(dispatcher),
        observe=Observe(manager),
        loop_max=10,
        context_tokens=context_tokens,
    )


def test_loop_shrink_history_then_continue_disk_intact(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    session = manager.create()
    big = "h" * 8000
    manager.append(Message(role="user", content="old q"))
    manager.append(
        Message(
            role="assistant",
            content="old a",
            tool_calls=[ToolCall(id="old-call", name="bash", arguments={})],
            meta={"round": 9},
        )
    )
    manager.append(_tool(big, 9, call_id="old-call"))
    llm = _FakeLLM([ChatReply(content="fresh answer")])
    events: list[TurnEvent] = []
    # History alone exceeds 80%: guard must shrink it in-memory and continue.
    history_estimate = estimate_wire_tokens(
        [Message(role="user", content="new q")] + manager.current.messages
    )
    context = int(history_estimate / 0.85)
    loop = _loop(manager, llm, _FakeDispatcher({}), context)
    assert (
        asyncio.run(loop.run("new q", event_sink=events.append)) == "fresh answer"
    )
    shrunk_events = [event for event in events if event.type == "context.shrunk"]
    assert len(shrunk_events) == 1
    assert shrunk_events[0].tool_count == 1
    assert (shrunk_events[0].hidden_bytes or 0) > 0
    # Wire saw the stub; disk keeps the full output.
    wire_tool = [m for m in llm.requests[0] if m.role == "tool"][0]
    assert "[shrunk:" in (wire_tool.content or "")
    stored = SessionManager(tmp_path).load(session.id).messages
    disk_tool = [m for m in stored if m.tool_call_id == "old-call"][0]
    assert disk_tool.content == big
    assert "[shrunk:" not in (disk_tool.content or "")


def test_loop_backstop_when_still_over_95(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    session = manager.create()
    llm = _FakeLLM([ChatReply(content="never reached")])
    # User text alone exceeds 95% and is never shrinkable.
    context = 500
    big_user = "q" * 3000
    loop = _loop(manager, llm, _FakeDispatcher({}), context)
    events: list[TurnEvent] = []
    assert asyncio.run(loop.run(big_user, event_sink=events.append)) == (
        "context limit reached"
    )
    assert llm.requests == []
    assert [event.type for event in events] == ["turn.accepted"]
    stored = SessionManager(tmp_path).load(session.id).messages
    assert stored[-1].role == "assistant"
    assert stored[-1].content == "context limit reached"
    assert (stored[-1].meta or {}).get("status") == "context_limit"


def test_loop_guard_disabled_by_default(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    manager.create()
    llm = _FakeLLM([ChatReply(content="ok")])
    loop = AgentLoop(
        sessions=manager,
        assemble=Assemble(),
        think=Think(llm),
        act=Act(_FakeDispatcher({})),
        observe=Observe(manager),
        loop_max=3,
    )
    events: list[TurnEvent] = []
    assert asyncio.run(loop.run("x" * 5000, event_sink=events.append)) == "ok"
    assert all(event.type != "context.shrunk" for event in events)


def test_shrink_is_idempotent_across_rounds() -> None:
    # Unshrinkable user bulk keeps the estimate over trigger after round 1,
    # forcing a real second scan (not the early return).
    messages = [
        Message(role="user", content="u" * 40000),
        _tool("y" * 2500, 1),
    ]
    estimate = estimate_wire_tokens(messages)
    context = int(estimate / 0.85)
    shrunk, count, _, est1 = shrink_stage_messages(
        messages, current_round=10, context_tokens=context
    )
    assert count == 1
    assert "[shrunk:" in (shrunk[1].content or "")
    assert est1 > int(context * TRIGGER_RATIO)
    first = [message.content for message in shrunk]
    shrunk2, count2, _, est2 = shrink_stage_messages(
        shrunk, current_round=11, context_tokens=context
    )
    assert count2 == 0
    assert [message.content for message in shrunk2] == first
    assert est2 == est1


def test_history_before_run_start_ignores_round_protection() -> None:
    history = _tool("h" * 4000, 1)  # round collides with current_round=1
    own = _tool("o" * 4000, 1)
    messages = [history, own]
    estimate = estimate_wire_tokens(messages)
    context = int(estimate / 0.85)
    shrunk, count, _, _ = shrink_stage_messages(
        messages, current_round=1, context_tokens=context, run_start=1
    )
    assert count == 1
    assert shrunk[0].content != history.content  # history: oldest-first
    assert shrunk[1].content == own.content  # own round: protected


def test_loop_backstop_after_shrink_reports_both(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    session = manager.create()
    manager.append(Message(role="user", content="old q"))
    manager.append(
        Message(
            role="assistant",
            content="old a",
            tool_calls=[ToolCall(id="old-call", name="bash", arguments={})],
            meta={"round": 1},
        )
    )
    manager.append(_tool("h" * 8000, 1, call_id="old-call"))
    llm = _FakeLLM([ChatReply(content="never reached")])
    loop = _loop(manager, llm, _FakeDispatcher({}), 2000)
    events: list[TurnEvent] = []
    assert (
        asyncio.run(loop.run("n" * 8000, event_sink=events.append))
        == "context limit reached"
    )
    assert llm.requests == []
    assert [event.type for event in events] == ["turn.accepted", "context.shrunk"]
    stored = SessionManager(tmp_path).load(session.id).messages
    assert stored[-1].content == "context limit reached"
    assert (stored[-1].meta or {}).get("status") == "context_limit"
    # Minimal backstop meta: no stale usage/cost from a previous round.
    assert "usage" not in (stored[-1].meta or {})
