from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from thyca.agent.act import Act
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
