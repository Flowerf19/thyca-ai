from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from thyca.protocol import RESULT_CAP_BYTES, ToolCall, ToolResult

if TYPE_CHECKING:
    from thyca.tools.task_store import TaskStore

Handler = Callable[[dict], Awaitable[str | ToolResult]]
ResourceKeyFn = Callable[[dict], str | None]

_DEFAULT_SOFT_TIMEOUT_S = 60


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict
    handler: Handler
    parallel_safe: bool = True
    resource_key: ResourceKeyFn | None = None
    # True = the tool manages its own long-running behavior (e.g. bash
    # escalates itself); the registry's generic soft-timeout wrapper skips it.
    escalates: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("ToolSpec.name must be a non-empty string")
        if not isinstance(self.description, str) or not self.description:
            raise ValueError("ToolSpec.description must be a non-empty string")
        if not isinstance(self.parameters, dict):
            raise ValueError("ToolSpec.parameters must be a dict")


class ToolRegistry:
    def __init__(
        self,
        result_cap: int = RESULT_CAP_BYTES,
        tasks: TaskStore | None = None,
        soft_timeout_s: int = _DEFAULT_SOFT_TIMEOUT_S,
    ) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._lock_guard = asyncio.Lock()
        self._result_cap = result_cap
        self._tasks = tasks
        self._soft_timeout_s = soft_timeout_s

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._specs:
            raise ValueError(f"tool already registered: {spec.name}")
        self._specs[spec.name] = spec

    def to_openai_schema(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.parameters,
                },
            }
            for spec in self._specs.values()
        ]

    async def dispatch(self, call: ToolCall) -> ToolResult:
        if call.parse_error is not None:
            return _result(call, str(call.parse_error), is_error=True)
        spec = self._specs.get(call.name)
        if spec is None:
            return _result(call, f"unknown tool: {call.name}", is_error=True)
        invalid = _validate_args(spec.parameters, call.arguments)
        if invalid is not None:
            return _result(call, invalid, is_error=True)

        key = spec.resource_key(call.arguments) if spec.resource_key is not None else None
        if key is None and not spec.parallel_safe:
            key = f"tool:{spec.name}"

        if key is None:
            return await self._run(spec, call)
        lock = await self._lock_for(key)
        async with lock:
            return await self._run(spec, call)

    async def _run(self, spec: ToolSpec, call: ToolCall) -> ToolResult:
        try:
            if self._tasks is not None and not spec.escalates:
                raw = await self._run_escalatable(spec, call)
            else:
                raw = await spec.handler(dict(call.arguments))
        except Exception as exc:
            return _result(call, str(exc), is_error=True)
        if isinstance(raw, ToolResult):
            return _result(call, self._cap(raw.content), is_error=raw.is_error)
        if not isinstance(raw, str):
            return _result(call, "handler must return str or ToolResult", is_error=True)
        return _result(call, self._cap(raw), is_error=False)

    async def _run_escalatable(self, spec: ToolSpec, call: ToolCall) -> str | ToolResult:
        """Run the handler with a soft timeout: past it, the handler keeps
        running as a tracked task and a synthetic result goes back now. The
        call's resource lock (if any) is released as soon as this returns, so
        same-key calls may overlap the tail of an escalated task."""
        task = asyncio.create_task(spec.handler(dict(call.arguments)))
        entry = self._tasks.track(task)
        try:
            # Waiting on the settle event (not on the task via shield) keeps
            # the handler immune to cancellation of this awaiter and avoids
            # shield's exception-logging on Python 3.14.
            await asyncio.wait_for(entry.done.wait(), self._soft_timeout_s)
        except TimeoutError:
            if task.done():
                # Finished exactly at the deadline: surface the real outcome
                # instead of misclassifying it as an escalation.
                if not task.cancelled() and task.exception() is not None:
                    raise task.exception() from None
                if not task.cancelled():
                    self._tasks.untrack(entry.id)
                    return task.result()
            entry.exposed = True
            return ToolResult(
                tool_call_id=call.id,
                name=call.name,
                content=(
                    f"still running: {entry.id}\n"
                    f"{call.name} is still executing; poll the result with "
                    "tool_read. It keeps running even if this turn is cancelled."
                ),
                is_error=False,
            )
        self._tasks.untrack(entry.id)
        return task.result()

    async def _lock_for(self, key: str) -> asyncio.Lock:
        async with self._lock_guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
            return lock

    def _cap(self, content: str) -> str:
        raw = content.encode("utf-8")
        if len(raw) <= self._result_cap:
            return content
        clipped = raw[-self._result_cap :]
        while clipped and clipped[0] & 0xC0 == 0x80:
            clipped = clipped[1:]
        return clipped.decode("utf-8")


def _result(call: ToolCall, content: str, *, is_error: bool) -> ToolResult:
    return ToolResult(
        tool_call_id=call.id,
        name=call.name,
        content=content,
        is_error=is_error,
    )


def _validate_args(schema: dict, arguments: dict) -> str | None:
    required = schema.get("required", [])
    if not isinstance(required, list):
        required = []
    for name in required:
        if name not in arguments:
            return f"missing argument: {name}"
    extra = schema.get("additionalProperties", True)
    if extra is False:
        allowed = set((schema.get("properties") or {}).keys())
        unexpected = [key for key in arguments if key not in allowed]
        if unexpected:
            return f"unexpected argument: {unexpected[0]}"
    return None
