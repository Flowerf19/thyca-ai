from __future__ import annotations

import asyncio
from typing import Protocol

from thyca.protocol import ToolCall, ToolResult

from .stage import Stage


class ToolDispatcher(Protocol):
    async def dispatch(self, call: ToolCall) -> ToolResult: ...


class Act:
    def __init__(self, dispatcher: ToolDispatcher) -> None:
        self._dispatcher = dispatcher

    async def act(self, stage: Stage) -> list[ToolResult]:
        if stage.reply is None or not stage.reply.tool_calls:
            stage.results = []
            return stage.results
        raw = await asyncio.gather(*(self._one(call) for call in stage.reply.tool_calls))
        stage.results = list(raw)
        return stage.results

    async def _one(self, call: ToolCall) -> ToolResult:
        if call.parse_error is not None:
            return ToolResult(
                tool_call_id=call.id,
                name=call.name,
                content=str(call.parse_error),
                is_error=True,
            )
        try:
            result = await self._dispatcher.dispatch(call)
        except Exception as exc:
            return ToolResult(
                tool_call_id=call.id,
                name=call.name,
                content=str(exc),
                is_error=True,
            )
        return ToolResult(
            tool_call_id=call.id,
            name=call.name,
            content=result.content,
            is_error=result.is_error,
        )
