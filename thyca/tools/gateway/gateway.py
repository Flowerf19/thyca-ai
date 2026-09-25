"""ToolGateway: the single execution front door for every tool call.

Fast handlers resolve inline (no retained entry); handlers still running
past the soft window keep going as tracked Executions under exec<N> ids.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from thyca.core.protocol import RESULT_CAP_BYTES, ToolCall, ToolResult
from thyca.tools.gateway.execution import (
    PAGE_DEFAULT_LIMIT,
    PROC,
    TASK,
    Detached,
    ExecIds,
    Execution,
    bind_execution,
    cap_reply,
    parse_limit,
    parse_offset,
    parse_wait,
    require_id,
    reset_execution,
    unknown_id,
)
from thyca.tools.gateway.policy import Policy, PolicyDenied
from thyca.tools.registry import ToolSpec

if TYPE_CHECKING:
    from thyca.tools.gateway.background import BackgroundProcs
    from thyca.tools.registry import ToolRegistry
    from thyca.tools.task_store import TaskStore

_SOFT_TIMEOUT_S = 60
_SETTLE_GRACE_S = 5.0
# One retention bound for both maps below. Tracked executions keep engine
# output (up to 1MB each); locks are tiny but keyed by unbounded resource
# args. Past the cap the oldest SETTLED execution / oldest UNLOCKED lock is
# dropped. Running executions and held locks are never evicted, so a burst
# past the cap only overflows transiently instead of breaking live polls.
_RETAIN_CAP = 200


class ToolGateway:
    """Submit every ToolCall here; owns locks, caps, soft-timeout, tracking."""

    def __init__(
        self,
        registry: ToolRegistry,
        tasks: TaskStore,
        background: BackgroundProcs | None = None,
        result_cap: int = RESULT_CAP_BYTES,
        soft_timeout_s: int = _SOFT_TIMEOUT_S,
        policy: Policy | None = None,
    ) -> None:
        self._registry = registry
        self._tasks = tasks
        self._background = background
        self._result_cap = result_cap
        self._soft_timeout_s = soft_timeout_s
        self._policy = policy or Policy()
        self._ids = ExecIds()
        self._executions: dict[str, Execution] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._lock_guard = asyncio.Lock()

    @property
    def executions(self) -> dict[str, Execution]:
        """Tracked executions by exec id (read-only view for tests/probes)."""
        return self._executions

    def set_soft_timeout_s(self, value: int) -> None:
        """Refresh the soft window from current config (called at turn start)."""
        self._soft_timeout_s = value

    async def submit(self, call: ToolCall) -> ToolResult:
        try:
            self._policy.check(call)
        except PolicyDenied as exc:
            return _result(call, str(exc) or "denied by policy", is_error=True)
        if call.parse_error is not None:
            return _result(call, str(call.parse_error), is_error=True)
        spec = self._registry.get(call.name)
        if spec is None:
            return _result(call, f"unknown tool: {call.name}", is_error=True)
        invalid = self._registry.validate_args(spec, call.arguments)
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

    async def read(
        self, id: str, wait: object = 0, offset: object = None, limit: object = None
    ) -> ToolResult:
        """Status + output for one tracked execution: delta for procs, paging by lines."""
        wait_s = parse_wait(wait)
        off = parse_offset(offset)
        lim = parse_limit(limit)
        execution = self._lookup(id)
        page = {} if off is None and lim is None else {
            "offset": off or 0, "limit": lim or PAGE_DEFAULT_LIMIT
        }
        if execution.kind == PROC and execution.proc is not None:
            assert self._background is not None
            text = await self._background.read(execution.proc.id, wait_s, **page)
            return ToolResult(
                tool_call_id=id, name="tool_read", content=text,
                is_error=execution.error,
            )
        assert execution.entry is not None
        result = await self._tasks.read(execution.entry.id, wait_s, **page)
        return ToolResult(
            tool_call_id=id, name="tool_read", content=result.content,
            is_error=result.is_error,
        )

    async def kill(self, id: str) -> str:
        """Terminate one tracked execution; tracked executions only."""
        execution = self._lookup(id)
        if execution.status != "running":
            return f"{id} already {execution.status}"
        if execution.kind == PROC and execution.proc is not None:
            assert self._background is not None
            await self._background.kill(execution.proc.id)
        else:
            assert execution.entry is not None
            await self._tasks.kill(execution.entry.id)
        execution.finish(error=True)
        return f"killed {id}"

    async def shutdown(self) -> None:
        """Cancel tracked tasks → kill procs → settle. Replaces scattered cleanup."""
        victims = [
            e.entry.task
            for e in list(self._executions.values())
            if e.status == "running" and e.kind == TASK and e.entry is not None
        ]
        for task in victims:
            task.cancel()
        if self._background is not None:
            await self._background.kill_all()
        if victims:
            await asyncio.wait(victims, timeout=_SETTLE_GRACE_S)
        self._executions.clear()

    async def _run(self, spec: ToolSpec, call: ToolCall) -> ToolResult:
        execution = Execution(self._ids.next(), spec.name, TASK)
        self._executions[execution.id] = execution
        try:
            return await self._track(spec, call, execution)
        except asyncio.CancelledError:
            # The id never reached the model, so nothing can ever poll it.
            self._drop(execution)
            raise
        except Exception as exc:
            self._drop(execution)
            return _result(call, str(exc), is_error=True)

    async def _track(self, spec: ToolSpec, call: ToolCall, execution: Execution) -> ToolResult:
        """Run the handler with a soft timeout: past it, the handler keeps
        running as a tracked execution and a synthetic result goes back now.
        The call's resource lock (if any) is released as soon as this returns,
        so same-key calls may overlap the tail of a tracked execution."""
        token = bind_execution(execution)
        try:
            task = asyncio.create_task(spec.handler(dict(call.arguments)))
            entry = self._tasks.track(task, id=execution.id)
            execution.entry = entry
            try:
                # Waiting on the settle event (not on the task via shield) keeps
                # the handler immune to cancellation of this awaiter and avoids
                # shield's exception-logging on Python 3.14.
                # Accepted hard≤soft edge: a hard cap at the 59-60s soft boundary may already have fired when this tracks ("still running") — deterministic tracking beats a wait-past-cap carve-out.
                await asyncio.wait_for(entry.done.wait(), self._soft_timeout_s)
            except TimeoutError:
                if task.done():
                    # Finished exactly at the deadline: surface the real outcome
                    # instead of misclassifying it as tracked.
                    if not task.cancelled() and task.exception() is not None:
                        raise task.exception() from None
                    if not task.cancelled():
                        raw = task.result()
                        if isinstance(raw, Detached):
                            return self._retain_detached(call, execution, raw)
                        self._drop(execution)
                        return self._resolve(call, raw)
                execution.exposed = True
                entry.exposed = True
                self._evict_settled()
                # Only genuinely slow handlers get a settle callback: fast ones
                # resolve inline below, never double-handled.
                task.add_done_callback(lambda t, e=execution: self._settle_task(e, t))
                return ToolResult(
                    tool_call_id=call.id,
                    name=call.name,
                    content=(
                        f"still running: {execution.id}\n"
                        f"{call.name} is still executing; poll the result with "
                        "tool_read. It keeps running even if this turn is cancelled."
                    ),
                    is_error=False,
                )
            raw = task.result()
            if isinstance(raw, Detached):
                return self._retain_detached(call, execution, raw)
            self._drop(execution)
            return self._resolve(call, raw)
        finally:
            reset_execution(token)

    def _retain_detached(
        self, call: ToolCall, execution: Execution, raw: Detached
    ) -> ToolResult:
        self._tasks.untrack(execution.id)
        # The entry belonged to the untracked task: proc reads route via
        # execution.proc, so a stale pointer here only invites confusion.
        execution.entry = None
        execution.attach_proc(raw.entry)
        execution.exposed = True
        self._evict_settled()
        if raw.entry.task is not None:
            raw.entry.task.add_done_callback(lambda t, e=execution: self._settle_proc(e))
        return _result(call, self._cap(raw.message, execution.id), is_error=False)

    def _resolve(self, call: ToolCall, raw: object) -> ToolResult:
        if isinstance(raw, ToolResult):
            return _result(call, self._cap(raw.content, None), is_error=raw.is_error)
        if not isinstance(raw, str):
            return _result(call, "handler must return str or ToolResult", is_error=True)
        return _result(call, self._cap(raw, None), is_error=False)

    def _settle_task(self, execution: Execution, task: asyncio.Task) -> None:
        entry = execution.entry
        error = task.cancelled() or (entry is not None and entry.is_error)
        execution.finish(error=error)

    def _settle_proc(self, execution: Execution) -> None:
        entry = execution.proc
        execution.finish(error=entry is not None and entry.killed)

    def _evict_settled(self) -> None:
        """Drop oldest settled executions past the cap, with engine state."""
        overflow = len(self._executions) - _RETAIN_CAP
        if overflow <= 0:
            return
        settled = [e for e in self._executions.values() if e.status != "running"]
        settled.sort(key=lambda e: (e.finished or e.started, e.id))
        for victim in settled[:overflow]:
            self._drop(victim)

    def _drop(self, execution: Execution) -> None:
        self._executions.pop(execution.id, None)
        self._tasks.untrack(execution.id)
        if execution.proc is not None and self._background is not None:
            execution.proc.kill()  # no-op once the proc already exited
            self._background.drop(execution.proc.id)

    def _lookup(self, id: str) -> Execution:
        execution = self._executions.get(id)
        if execution is None:
            raise unknown_id("execution", id, sorted(self._executions))
        return execution

    async def _lock_for(self, key: str) -> asyncio.Lock:
        async with self._lock_guard:
            lock = self._locks.get(key)
            if lock is not None:
                self._locks[key] = self._locks.pop(key)  # LRU touch
                return lock
            self._evict_locks()
            lock = asyncio.Lock()
            self._locks[key] = lock
            return lock

    def _evict_locks(self) -> None:
        """Drop oldest unlocked keys past the cap (LRU). A held lock is never
        evicted: recreating it mid-hold would run two same-key calls
        concurrently and break mutual exclusion. Residual: a lock fetched
        but not yet acquired can be evicted under 200+ key churn (accepted:
        theoretical, needs adversarial interleave)."""
        overflow = len(self._locks) - _RETAIN_CAP + 1
        if overflow <= 0:
            return
        for key in list(self._locks):
            if overflow <= 0:
                break
            if self._locks[key].locked():
                continue
            del self._locks[key]
            overflow -= 1

    def _cap(self, content: str, eid: str | None) -> str:
        more = f'read more: tool_read id="{eid}"' if eid else None
        return cap_reply(content, self._result_cap, more=more)


def _result(call: ToolCall, content: str, *, is_error: bool) -> ToolResult:
    return ToolResult(
        tool_call_id=call.id,
        name=call.name,
        content=content,
        is_error=is_error,
    )


def tool_read_spec(gateway: ToolGateway | None) -> ToolSpec:
    """The one poll tool: progress/logs for any tracked exec id."""

    async def handler(args: dict) -> ToolResult:
        if gateway is None:
            raise ValueError("no gateway in this context")
        eid = require_id(args)
        return await gateway.read(eid, args.get("wait"), args.get("offset"), args.get("limit"))

    return ToolSpec(
        name="tool_read",
        description=(
            "Read the status and output of a tracked tool execution (id exec<N>): "
            "a tool call that moved to background after the soft timeout "
            "('still running: exec<N>') or a bash process started with "
            "background:true ('started: exec<N>'). Each read returns only new "
            "output since the last check; offset/limit page the retained output "
            "by lines. Positional paging never moves the temporal delta cursor: "
            "a later plain read may repeat already-paged bytes. wait: seconds to wait "
            "for completion before replying (0..60, default 0)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "wait": {"type": "integer"},
                "offset": {"type": "integer"},
                "limit": {"type": "integer"},
            },
            "required": ["id"],
            "additionalProperties": False,
        },
        handler=handler,
        parallel_safe=True,
    )


def tool_kill_spec(gateway: ToolGateway | None) -> ToolSpec:
    async def handler(args: dict) -> str:
        if gateway is None:
            raise ValueError("no gateway in this context")
        return await gateway.kill(require_id(args))

    return ToolSpec(
        name="tool_kill",
        description=(
            "Terminate one tracked execution (id exec<N>) that is still running: "
            "kills its process group (bash) or cancels its task. Tracked "
            "executions only; finished ids report their status."
        ),
        parameters={
            "type": "object",
            "properties": {
                "id": {"type": "string"},
            },
            "required": ["id"],
            "additionalProperties": False,
        },
        handler=handler,
        parallel_safe=True,
    )
