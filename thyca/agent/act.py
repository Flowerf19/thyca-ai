from __future__ import annotations

import asyncio
from typing import Protocol

from thyca.protocol import ToolCall, ToolResult

from .events import EventSink, TurnEvent, emit_event
from .stage import Stage


class ToolDispatcher(Protocol):
    async def dispatch(self, call: ToolCall) -> ToolResult: ...


class Act:
    def __init__(self, dispatcher: ToolDispatcher) -> None:
        self._dispatcher = dispatcher

    async def act(self, stage: Stage, event_sink: EventSink | None = None) -> list[ToolResult]:
        if stage.reply is None or not stage.reply.tool_calls:
            stage.results = []
            return stage.results
        raw = await asyncio.gather(
            *(self._one(call, stage.round, event_sink) for call in stage.reply.tool_calls)
        )
        stage.results = list(raw)
        return stage.results

    async def _one(
        self, call: ToolCall, round: int, event_sink: EventSink | None = None
    ) -> ToolResult:
        # Guard on sink: no-sink callers (round may be 0) must behave byte-for-byte as before.
        if event_sink is not None:
            emit_event(
                event_sink,
                TurnEvent(type="tool.started", round=round, call_id=call.id, name=call.name),
            )
        if call.parse_error is not None:
            result = ToolResult(
                tool_call_id=call.id,
                name=call.name,
                content=str(call.parse_error),
                is_error=True,
            )
        else:
            try:
                dispatched = await self._dispatcher.dispatch(call)
            except Exception as exc:
                result = ToolResult(
                    tool_call_id=call.id,
                    name=call.name,
                    content=str(exc),
                    is_error=True,
                )
            else:
                result = ToolResult(
                    tool_call_id=call.id,
                    name=call.name,
                    content=dispatched.content,
                    is_error=dispatched.is_error,
                )
        if event_sink is not None:
            emit_event(
                event_sink,
                TurnEvent(
                    type="tool.finished",
                    round=round,
                    call_id=call.id,
                    name=call.name,
                    ok=not result.is_error,
                ),
            )
        return result
