"""AgentLoop tests — TASK-321 verification."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from thyca.agent.act import Act
from thyca.agent.assemble import Assemble
from thyca.agent.events import TurnEvent
from thyca.agent.loop import AgentLoop
from thyca.agent.observe import Observe
from thyca.agent.think import ChatReply, Think
from thyca.config import PricingCfg
from thyca.protocol import Message, ToolCall, ToolResult
from thyca.sessions import SessionManager


@dataclass
class FakeLLM:
    replies: list[ChatReply]
    events: list[str] = field(default_factory=list)
    requests: list[list[Message]] = field(default_factory=list)

    async def chat(self, messages: list[Message], tools: list | None = None) -> ChatReply:
        self.events.append("chat")
        self.requests.append(list(messages))
        if not self.replies:
            raise AssertionError("FakeLLM queue is empty")
        return self.replies.pop(0)


@dataclass
class FakeDispatcher:
    results: dict[str, ToolResult]
    delays: dict[str, float] = field(default_factory=dict)
    calls: list[ToolCall] = field(default_factory=list)
    completed: list[str] = field(default_factory=list)

    async def dispatch(self, call: ToolCall) -> ToolResult:
        self.calls.append(call)
        await asyncio.sleep(self.delays.get(call.id, 0))
        self.completed.append(call.id)
        return self.results[call.id]


def _wrap_compaction(manager: SessionManager, events: list[str]) -> None:
    original = manager.compact_if_needed

    def compact() -> bool:
        events.append("compact")
        return original()

    manager.compact_if_needed = compact  # type: ignore[method-assign]


def _load_messages(tmp_path: Path, session_id: str) -> list[Message]:
    return SessionManager(tmp_path).load(session_id).messages


def _loop(
    manager: SessionManager,
    llm: FakeLLM,
    dispatcher: FakeDispatcher,
    *,
    loop_max: int = 3,
) -> AgentLoop:
    return AgentLoop(
        sessions=manager,
        assemble=Assemble(),
        think=Think(llm),
        act=Act(dispatcher),
        observe=Observe(manager),
        loop_max=loop_max,
    )


def test_text_only_event_order_and_persist_unchanged(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    session = manager.create()
    llm = FakeLLM([ChatReply(content="hello back")])
    dispatcher = FakeDispatcher({})
    events: list[TurnEvent] = []

    assert (
        asyncio.run(_loop(manager, llm, dispatcher).run("hello", event_sink=events.append))
        == "hello back"
    )
    assert [(event.type, event.round, event.tool_count) for event in events] == [
        ("turn.accepted", None, None),
        ("llm.started", 1, None),
        ("llm.finished", 1, 0),
    ]
    messages = _load_messages(tmp_path, session.id)
    assert [(message.role, message.content) for message in messages] == [
        ("user", "hello"),
        ("assistant", "hello back"),
    ]


def test_tool_round_then_text_emits_round_and_tool_events(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    session = manager.create()
    call = ToolCall(id="call-1", name="echo", arguments={"value": "x"})
    llm = FakeLLM(
        [
            ChatReply(content=None, tool_calls=[call]),
            ChatReply(content="finished"),
        ]
    )
    dispatcher = FakeDispatcher(
        {"call-1": ToolResult(tool_call_id="call-1", name="echo", content="x")}
    )
    events: list[TurnEvent] = []

    assert (
        asyncio.run(_loop(manager, llm, dispatcher).run("use echo", event_sink=events.append))
        == "finished"
    )
    assert [(event.type, event.round, event.tool_count) for event in events] == [
        ("turn.accepted", None, None),
        ("llm.started", 1, None),
        ("llm.finished", 1, 1),
        ("tool.started", 1, None),
        ("tool.finished", 1, None),
        ("llm.started", 2, None),
        ("llm.finished", 2, 0),
    ]
    started, finished = events[3:5]
    assert (started.type, started.round, started.call_id, started.name) == (
        "tool.started",
        1,
        "call-1",
        "echo",
    )
    assert (finished.type, finished.round, finished.call_id, finished.name, finished.ok) == (
        "tool.finished",
        1,
        "call-1",
        "echo",
        True,
    )
    messages = _load_messages(tmp_path, session.id)
    assert [message.role for message in messages] == ["user", "assistant", "tool", "assistant"]
    assert messages[-1].content == "finished"


def test_loop_max_emits_no_finished_for_skipped_round(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    session = manager.create()
    call = ToolCall(id="call-1", name="echo")
    llm = FakeLLM([ChatReply(content=None, tool_calls=[call])])
    dispatcher = FakeDispatcher(
        {"call-1": ToolResult(tool_call_id="call-1", name="echo", content="result")}
    )
    events: list[TurnEvent] = []

    assert (
        asyncio.run(_loop(manager, llm, dispatcher, loop_max=1).run(
            "stop after one", event_sink=events.append
        ))
        == "loop limit reached"
    )
    assert [(event.type, event.round, event.tool_count) for event in events] == [
        ("turn.accepted", None, None),
        ("llm.started", 1, None),
        ("llm.finished", 1, 1),
        ("tool.started", 1, None),
        ("tool.finished", 1, None),
    ]
    messages = _load_messages(tmp_path, session.id)
    assert [message.role for message in messages] == ["user", "assistant", "tool", "assistant"]
    assert messages[-1].content == "loop limit reached"


def test_sink_raise_fails_open_turn_still_persists(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    session = manager.create()
    llm = FakeLLM([ChatReply(content="hello back")])
    dispatcher = FakeDispatcher({})

    def boom(_event: TurnEvent) -> None:
        raise RuntimeError("sink exploded")

    assert (
        asyncio.run(_loop(manager, llm, dispatcher).run("hello", event_sink=boom))
        == "hello back"
    )
    messages = _load_messages(tmp_path, session.id)
    assert [(message.role, message.content) for message in messages] == [
        ("user", "hello"),
        ("assistant", "hello back"),
    ]


def test_text_only_compacts_before_chat_and_persists_user_and_assistant(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    session = manager.create()
    events: list[str] = []
    _wrap_compaction(manager, events)
    llm = FakeLLM([ChatReply(content="hello back")], events=events)
    dispatcher = FakeDispatcher({})

    assert asyncio.run(_loop(manager, llm, dispatcher).run("hello")) == "hello back"
    assert events == ["compact", "chat"]
    messages = _load_messages(tmp_path, session.id)
    assert [(message.role, message.content) for message in messages] == [
        ("user", "hello"),
        ("assistant", "hello back"),
    ]


def test_one_tool_round_then_text_persists_complete_round(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    session = manager.create()
    call = ToolCall(id="call-1", name="echo", arguments={"value": "x"})
    llm = FakeLLM(
        [
            ChatReply(content=None, tool_calls=[call]),
            ChatReply(content="finished"),
        ]
    )
    dispatcher = FakeDispatcher(
        {"call-1": ToolResult(tool_call_id="call-1", name="echo", content="x")}
    )

    assert asyncio.run(_loop(manager, llm, dispatcher).run("use echo")) == "finished"

    messages = _load_messages(tmp_path, session.id)
    assert [message.role for message in messages] == ["user", "assistant", "tool", "assistant"]
    assert messages[1].content is None
    assert messages[1].tool_calls == [call]
    assert messages[2].tool_call_id == "call-1"
    assert messages[2].content == "x"
    assert messages[3].content == "finished"
    assert llm.requests[1] == messages[:3]


def test_two_tool_calls_persist_results_in_declaration_order(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    session = manager.create()
    first = ToolCall(id="first", name="slow")
    second = ToolCall(id="second", name="fast")
    llm = FakeLLM(
        [
            ChatReply(content="working", tool_calls=[first, second]),
            ChatReply(content="done"),
        ]
    )
    dispatcher = FakeDispatcher(
        {
            "first": ToolResult(tool_call_id="first", name="slow", content="one"),
            "second": ToolResult(tool_call_id="second", name="fast", content="two"),
        },
        delays={"first": 0.02, "second": 0},
    )

    assert asyncio.run(_loop(manager, llm, dispatcher).run("run both")) == "done"

    messages = _load_messages(tmp_path, session.id)
    assert [message.tool_call_id for message in messages if message.role == "tool"] == [
        "first",
        "second",
    ]
    assert dispatcher.completed == ["second", "first"]


def test_loop_max_persists_limit_message_without_second_chat(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    session = manager.create()
    call = ToolCall(id="call-1", name="echo")
    llm = FakeLLM([ChatReply(content=None, tool_calls=[call])])
    dispatcher = FakeDispatcher(
        {"call-1": ToolResult(tool_call_id="call-1", name="echo", content="result")}
    )

    assert asyncio.run(_loop(manager, llm, dispatcher, loop_max=1).run("stop after one")) == (
        "loop limit reached"
    )

    assert len(llm.requests) == 1
    messages = _load_messages(tmp_path, session.id)
    assert [message.role for message in messages] == ["user", "assistant", "tool", "assistant"]
    assert messages[-1].content == "loop limit reached"


def test_parse_error_is_returned_as_tool_error_without_dispatch(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    session = manager.create()
    call = ToolCall(id="bad-call", name="echo", parse_error="invalid arguments")
    llm = FakeLLM(
        [
            ChatReply(content=None, tool_calls=[call]),
            ChatReply(content="recovered"),
        ]
    )
    dispatcher = FakeDispatcher({})

    assert asyncio.run(_loop(manager, llm, dispatcher).run("bad tool call")) == "recovered"

    assert dispatcher.calls == []
    messages = _load_messages(tmp_path, session.id)
    assert messages[2].role == "tool"
    assert messages[2].tool_call_id == "bad-call"
    assert messages[2].content == "invalid arguments"


def test_persists_usage_cost_and_config_model_when_reply_omits_model(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    session = manager.create()
    usage = {
        "prompt_tokens": 100,
        "cached_tokens": 20,
        "completion_tokens": 10,
        "total_tokens": 110,
    }
    llm = FakeLLM([ChatReply(content="hello back", usage=usage, finish_reason="stop")])
    dispatcher = FakeDispatcher({})
    loop = AgentLoop(
        sessions=manager,
        assemble=Assemble(),
        think=Think(llm),
        act=Act(dispatcher),
        observe=Observe(manager),
        loop_max=3,
        model="gpt-4o-mini",
        pricing={"gpt-4o-mini": PricingCfg(input=0.15, cache=0.075, output=0.60)},
    )

    assert asyncio.run(loop.run("hello")) == "hello back"

    messages = _load_messages(tmp_path, session.id)
    meta = messages[-1].meta
    assert meta is not None
    assert meta["model"] == "gpt-4o-mini"
    assert meta["usage"] == usage
    assert meta["cost_usd"] == round((80 * 0.15 + 20 * 0.075 + 10 * 0.60) / 1_000_000, 6)
    assert isinstance(meta["latency_ms"], int)
    assert meta["kind"] == "llm"
    assert meta["round"] == 1


def test_unknown_model_persists_usage_without_cost(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    session = manager.create()
    usage = {"prompt_tokens": 8, "cached_tokens": 0, "completion_tokens": 2, "total_tokens": 10}
    llm = FakeLLM([ChatReply(content="ok", usage=usage)])
    loop = AgentLoop(
        sessions=manager,
        assemble=Assemble(),
        think=Think(llm),
        act=Act(FakeDispatcher({})),
        observe=Observe(manager),
        loop_max=3,
        model="foo/bar",
    )

    assert asyncio.run(loop.run("hello")) == "ok"

    meta = _load_messages(tmp_path, session.id)[-1].meta
    assert meta["model"] == "foo/bar"
    assert meta["usage"] == usage
    assert "cost_usd" not in meta


def test_tool_round_persists_tool_latency(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    session = manager.create()
    call = ToolCall(id="call-1", name="echo", arguments={"value": "x"})
    llm = FakeLLM(
        [
            ChatReply(content=None, tool_calls=[call]),
            ChatReply(content="finished"),
        ]
    )
    dispatcher = FakeDispatcher(
        {"call-1": ToolResult(tool_call_id="call-1", name="echo", content="x")}
    )

    assert asyncio.run(_loop(manager, llm, dispatcher).run("use echo")) == "finished"

    messages = _load_messages(tmp_path, session.id)
    tool = messages[2]
    assert tool.role == "tool"
    assert tool.meta is not None
    assert isinstance(tool.meta["latency_ms"], int)
    assert tool.meta["round"] == 1
