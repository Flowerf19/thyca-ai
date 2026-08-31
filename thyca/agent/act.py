from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Protocol

from thyca.protocol import ToolCall, ToolResult

from .events import EventSink, TurnEvent, emit_event
from .skill_event import classify_skill_read, public_skill_name
from .stage import Stage


class ToolDispatcher(Protocol):
    async def dispatch(self, call: ToolCall) -> ToolResult: ...


class Act:
    def __init__(
        self, dispatcher: ToolDispatcher, skills_root: Path | None = None
    ) -> None:
        self._dispatcher = dispatcher
        self._skills_root = skills_root

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
        # A read inside the skills dir emits skill.* instead of tool.* — one
        # action is one beat pair, never tool.* and skill.* together.
        skill_name = self._skill_name(call)
        kind = "skill" if skill_name is not None else "tool"
        name = call.name if skill_name is None else public_skill_name(skill_name)
        # Guard on sink: no-sink callers (round may be 0) must behave byte-for-byte as before.
        if event_sink is not None:
            emit_event(
                event_sink,
                TurnEvent(type=f"{kind}.started", round=round, call_id=call.id, name=name),
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
                    type=f"{kind}.finished",
                    round=round,
                    call_id=call.id,
                    name=name,
                    ok=not result.is_error,
                ),
            )
        return result

    def _skill_name(self, call: ToolCall) -> str | None:
        """Skill name when this call is a read inside the skills dir, else None."""
        if self._skills_root is None or call.name != "read" or call.parse_error is not None:
            return None
        path = call.arguments.get("path")
        if not isinstance(path, str):
            return None
        try:
            resolved = Path(path).expanduser().resolve()
        except (OSError, ValueError):
            return None
        return classify_skill_read(self._skills_root, resolved)
