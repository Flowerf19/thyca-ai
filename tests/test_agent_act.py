from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from thyca.agent.act import Act
from thyca.agent.events import TurnEvent
from thyca.agent.stage import Stage
from thyca.agent.think import ChatReply
from thyca.protocol import ToolCall, ToolResult


@dataclass
class FakeDispatcher:
    result: ToolResult | None = None
    error: Exception | None = None
    calls: list[ToolCall] = field(default_factory=list)

    async def dispatch(self, call: ToolCall) -> ToolResult:
        self.calls.append(call)
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


def _stage(call: ToolCall) -> Stage:
    return Stage(reply=ChatReply(content=None, tool_calls=[call]))


def _stage_round(call: ToolCall) -> Stage:
    stage = _stage(call)
    stage.round = 1
    return stage


def test_parse_error_skips_dispatch() -> None:
    dispatcher = FakeDispatcher()
    call = ToolCall(id="call-1", name="weather", parse_error="invalid arguments")

    results = asyncio.run(Act(dispatcher).act(_stage(call)))

    assert dispatcher.calls == []
    assert results == [
        ToolResult(
            tool_call_id="call-1",
            name="weather",
            content="invalid arguments",
            is_error=True,
        )
    ]


def _event_pairs(events: list[TurnEvent]) -> list[tuple[str, bool | None]]:
    return [(event.type, event.ok) for event in events]


def test_parse_error_emits_started_then_finished_ok_false() -> None:
    dispatcher = FakeDispatcher()
    call = ToolCall(id="call-1", name="weather", parse_error="invalid arguments")
    events: list[TurnEvent] = []

    results = asyncio.run(Act(dispatcher).act(_stage_round(call), event_sink=events.append))

    assert _event_pairs(events) == [
        ("tool.started", None),
        ("tool.finished", False),
    ]
    started, finished = events
    assert (started.type, started.round, started.call_id, started.name) == (
        "tool.started",
        1,
        "call-1",
        "weather",
    )
    assert (finished.round, finished.call_id, finished.name) == (1, "call-1", "weather")
    assert results[0].is_error is True


def test_happy_path_emits_started_then_finished_ok_true() -> None:
    dispatcher = FakeDispatcher(
        result=ToolResult(tool_call_id="call-1", name="weather", content="sunny")
    )
    call = ToolCall(id="call-1", name="weather")
    events: list[TurnEvent] = []

    results = asyncio.run(Act(dispatcher).act(_stage_round(call), event_sink=events.append))

    assert _event_pairs(events) == [
        ("tool.started", None),
        ("tool.finished", True),
    ]
    assert results[0].is_error is False


def test_dispatch_exception_emits_started_then_finished_ok_false() -> None:
    dispatcher = FakeDispatcher(error=RuntimeError("tool unavailable"))
    call = ToolCall(id="call-1", name="weather")
    events: list[TurnEvent] = []

    asyncio.run(Act(dispatcher).act(_stage_round(call), event_sink=events.append))

    assert _event_pairs(events) == [
        ("tool.started", None),
        ("tool.finished", False),
    ]


def test_dispatcher_error_result_emits_finished_ok_false() -> None:
    dispatcher = FakeDispatcher(
        result=ToolResult(
            tool_call_id="call-1", name="weather", content="rate limited", is_error=True
        )
    )
    call = ToolCall(id="call-1", name="weather")
    events: list[TurnEvent] = []

    asyncio.run(Act(dispatcher).act(_stage_round(call), event_sink=events.append))

    assert _event_pairs(events) == [
        ("tool.started", None),
        ("tool.finished", False),
    ]


def test_sink_raise_still_returns_same_tool_result() -> None:
    dispatcher = FakeDispatcher(
        result=ToolResult(tool_call_id="call-1", name="weather", content="sunny")
    )
    call = ToolCall(id="call-1", name="weather")

    def boom(_event: TurnEvent) -> None:
        raise RuntimeError("sink exploded")

    results = asyncio.run(Act(dispatcher).act(_stage_round(call), event_sink=boom))

    assert results[0].tool_call_id == "call-1"
    assert results[0].content == "sunny"
    assert results[0].is_error is False


def test_no_sink_emits_no_events() -> None:
    dispatcher = FakeDispatcher(
        result=ToolResult(tool_call_id="call-1", name="weather", content="sunny")
    )
    call = ToolCall(id="call-1", name="weather")
    stage = _stage(call)
    stage.round = 0

    asyncio.run(Act(dispatcher).act(stage))

    assert dispatcher.calls == [call]
    assert stage.results[0].is_error is False


def test_parallel_finished_in_completion_order() -> None:
    async def fast(_call: ToolCall) -> ToolResult:
        return ToolResult(tool_call_id="call-1", name="fast", content="two")

    async def slow(_call: ToolCall) -> ToolResult:
        await asyncio.sleep(0.02)
        return ToolResult(tool_call_id="call-1", name="slow", content="one")

    class TwoDispatcher:
        async def dispatch(self, call: ToolCall) -> ToolResult:
            if call.name == "fast":
                return await fast(call)
            return await slow(call)

    slow_call = ToolCall(id="slow", name="slow")
    fast_call = ToolCall(id="fast", name="fast")
    stage = Stage(reply=ChatReply(content=None, tool_calls=[slow_call, fast_call]))
    stage.round = 1
    events: list[TurnEvent] = []

    asyncio.run(Act(TwoDispatcher()).act(stage, event_sink=events.append))

    assert [(event.type, event.name) for event in events] == [
        ("tool.started", "slow"),
        ("tool.started", "fast"),
        ("tool.finished", "fast"),
        ("tool.finished", "slow"),
    ]
    assert [event.round for event in events] == [1, 1, 1, 1]


def test_happy_path_preserves_call_identity() -> None:
    dispatcher = FakeDispatcher(
        result=ToolResult(tool_call_id="wrong-id", name="wrong-name", content="sunny")
    )
    call = ToolCall(id="call-1", name="weather")
    stage = _stage(call)

    asyncio.run(Act(dispatcher).act(stage))

    assert dispatcher.calls == [call]
    result = stage.results[0]
    assert result.content == "sunny"
    assert result.is_error is False
    assert result.tool_call_id == call.id
    assert result.name == call.name


def test_dispatch_exception_becomes_error_result() -> None:
    dispatcher = FakeDispatcher(error=RuntimeError("tool unavailable"))
    call = ToolCall(id="call-1", name="weather")
    stage = _stage(call)

    asyncio.run(Act(dispatcher).act(stage))

    result = stage.results[0]
    assert result.tool_call_id == call.id
    assert result.name == call.name
    assert result.content == "tool unavailable"
    assert result.is_error is True
