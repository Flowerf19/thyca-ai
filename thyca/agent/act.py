from __future__ import annotations

import asyncio
import time
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
        # measure each tool individually so Stage.tool_latencies keeps real parallelism
        async def timed(call: ToolCall) -> tuple[ToolResult, int]:
            start = time.perf_counter()
            result = await self._one(call, stage.round, event_sink)
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            return result, elapsed_ms

        pairs = await asyncio.gather(*(timed(call) for call in stage.reply.tool_calls))
        stage.results = [result for result, _ in pairs]
        # keep latencies keyed by call_id for Observe to attach to tool messages
        latencies = {result.tool_call_id: elapsed for result, elapsed in pairs}
        # merge rather than replace — loop may have previous rounds
        existing = getattr(stage, "tool_latencies", {}) or {}
        existing.update(latencies)
        stage.tool_latencies = existing
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
