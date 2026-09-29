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
    shrink_stage_messages,
)
from thyca.agent.think import Think
from thyca.core.protocol import Message, ToolCall, ToolResult
from thyca.core.protocol import estimate_tokens as _chars_to_tokens
from thyca.llm.llm_base import ChatReply
from thyca.sessions import SessionManager


def _tool(content: str, round_no: int | None = None, call_id: str = "c1") -> Message:
    meta = {"round": round_no} if round_no is not None else None
    return Message(role="tool", content=content, tool_call_id=call_id, meta=meta)


def test_exec_ids_come_from_metadata_never_text() -> None:
    # Quoted ids in output text are not references: without gateway
    # metadata (or an original tool_read call) there is no pointer.
    content = "x" * 3000 + '\nread more: tool_read id="exec3"'
    messages = [_tool(content, 1)]
    estimate = estimate_wire_tokens(messages)
    context = int(estimate / 0.85)
    shrunk, count, _, _ = shrink_stage_messages(
        messages, current_round=10, context_tokens=context
    )
    assert count == 1
    assert 'id="exec3"' not in (shrunk[0].content or "")
    # Gateway-attached metadata is the authority: the pointer names it.
    trusted = Message(
        role="tool",
        content="x" * 3000,
        tool_call_id="c1",
        meta={"round": 1, "exec_id": "exec3"},
    )
    shrunk, count, _, _ = shrink_stage_messages(
        [trusted], current_round=10, context_tokens=context
    )
    assert count == 1
    assert 'tool_read id="exec3"' in (shrunk[0].content or "")


def test_refetchable_shape_head_plus_pointer() -> None:
    body = "x" * 3000
    content = f"{body}\nread more: tool_read id=\"exec9\""
    message = Message(
        role="tool", content=content, tool_call_id="c1",
        meta={"round": 1, "exec_id": "exec9"},
    )
    messages = [message]
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
    assert shrunk[0].meta == {"round": 1, "exec_id": "exec9"}
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


def test_quoted_exec_refs_never_become_pointers() -> None:
    # Two quoted ids in text used to block shrinking (ambiguity); text
    # carries no authority now, so the output shrinks as a fast path.
    content = "z" * 3000 + '\ntool_read id="exec1" and tool_read id="exec2"'
    messages = [_tool(content, 1)]
    estimate = estimate_wire_tokens(messages)
    context = int(estimate / 0.85)
    shrunk, count, _, _ = shrink_stage_messages(
        messages, current_round=10, context_tokens=context
    )
    assert count == 1
    assert 'id="exec1"' not in (shrunk[0].content or "")
    assert 'id="exec2"' not in (shrunk[0].content or "")


def test_last_three_rounds_protected() -> None:
    messages = [_tool("a" * 4000, round_no) for round_no in (1, 2, 3, 4, 5)]
    estimate = estimate_wire_tokens(messages)
    context = int(estimate / 0.85)
    shrunk, count, _, _ = shrink_stage_messages(
        messages, current_round=5, context_tokens=context
    )
    assert count >= 1
    # Rounds 3,4,5 never touched.
    for original, new in zip(messages[2:], shrunk[2:], strict=True):
        assert new.content == original.content
    # At least round 1 (oldest eligible) shrunk.
    assert shrunk[0].content != messages[0].content


def test_refetchable_before_fastpath_despite_age() -> None:
    fastpath = _tool("f" * 6000, 1, call_id="old")
    refetch = Message(
        role="tool",
        content="r" * 6000 + '\nread more: tool_read id="exec4"',
        tool_call_id="new",
        meta={"round": 2, "exec_id": "exec4"},
    )
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
        [Message(role="user", content="new q"), *manager.current.messages]
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
    wire_tool = next(m for m in llm.requests[0] if m.role == "tool")
    assert "[shrunk:" in (wire_tool.content or "")
    stored = SessionManager(tmp_path).load(session.id).messages
    disk_tool = next(m for m in stored if m.tool_call_id == "old-call")
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


def test_loop_live_exec_ids_reaches_think_under_pressure(tmp_path: Path) -> None:
    # Bug 1: loop passed Act.live_exec_ids without parens (bound method, not
    # a set), so any scanned in-run candidate crashed `in` with TypeError.
    manager = SessionManager(tmp_path)
    manager.create()
    big = "x" * 8000
    dispatcher = _FakeDispatcher(
        {
            "c1": ToolResult(
                tool_call_id="c1", name="bash", content=big, exec_ref="exec1"
            ),
            "c2": ToolResult(tool_call_id="c2", name="bash", content="ok2"),
            "c3": ToolResult(tool_call_id="c3", name="bash", content="ok3"),
        }
    )
    dispatcher.executions = {"exec1": object()}  # gateway-style retention
    llm = _FakeLLM(
        [
            ChatReply(
                content="",
                tool_calls=[ToolCall(id="c1", name="bash", arguments={})],
            ),
            ChatReply(
                content="",
                tool_calls=[ToolCall(id="c2", name="bash", arguments={})],
            ),
            ChatReply(
                content="",
                tool_calls=[ToolCall(id="c3", name="bash", arguments={})],
            ),
            ChatReply(content="done"),
        ]
    )
    # Round-4 wire shape: only the round-1 tool escapes round protection
    # (4 - 1 >= 3) and reaches the live-set membership check.
    round4 = [
        Message(role="user", content="go"),
        Message(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="c1", name="bash", arguments={})],
        ),
        Message(role="tool", content=big, tool_call_id="c1"),
        Message(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="c2", name="bash", arguments={})],
        ),
        Message(role="tool", content="ok2", tool_call_id="c2"),
        Message(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="c3", name="bash", arguments={})],
        ),
        Message(role="tool", content="ok3", tool_call_id="c3"),
    ]
    estimate = estimate_wire_tokens(round4)
    context = int(estimate / 0.85)
    assert estimate > int(context * TRIGGER_RATIO)  # guard must scan
    assert estimate <= int(context * 0.95)  # ...but no backstop
    loop = _loop(manager, llm, dispatcher, context)
    events: list[TurnEvent] = []
    assert asyncio.run(loop.run("go", event_sink=events.append)) == "done"
    assert len(llm.requests) == 4
    shrunk_events = [event for event in events if event.type == "context.shrunk"]
    assert len(shrunk_events) == 1
    assert shrunk_events[0].tool_count == 1
    # Live-set membership trusted the id: refetchable pointer on the wire.
    wire_c1 = next(m for m in llm.requests[3] if m.tool_call_id == "c1")
    assert 'tool_read id="exec1"' in (wire_c1.content or "")


def test_pending_user_uses_wire_estimate(tmp_path: Path) -> None:
    # Bug 2: loop counted raw chars while the guard counts the wire payload
    # (~8-token undercount). Compactor and guard must agree within a few.
    manager = SessionManager(tmp_path)
    manager.create()
    seen: dict = {}
    real_compact = manager.compact_if_needed

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real_compact(*args, **kwargs)

    manager.compact_if_needed = spy  # type: ignore[method-assign]
    user_msg = "please summarize the logs"
    llm = _FakeLLM([ChatReply(content="ok")])
    loop = _loop(manager, llm, _FakeDispatcher({}), 4000)
    assert asyncio.run(loop.run(user_msg)) == "ok"
    wire = estimate_wire_tokens([Message(role="user", content=user_msg)])
    assert abs(seen["pending_user_tokens"] - wire) <= 3
    # Sensitivity: raw char-count really is several tokens short here.
    assert wire - _chars_to_tokens(user_msg) >= 5
