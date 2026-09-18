"""Store for tool calls that outlived the registry's soft timeout.

A handler still running when ``ToolRegistry``'s soft window expires keeps
executing as a tracked task; the model polls its final result with the
``tool_read`` tool. One store per app (daemon or one CLI run); handlers run
on the app's own event loop, so no thread locking is needed.
"""
from __future__ import annotations

import asyncio
import itertools

from thyca.protocol import ToolResult
from thyca.tools.registry import ToolSpec

_READ_WAIT_MAX_S = 60


class _TrackedTask:
    """One escalated tool call and its settled outcome."""

    __slots__ = ("content", "done", "exposed", "id", "is_error", "task")

    def __init__(self, tid: str, task: asyncio.Task) -> None:
        self.id = tid
        self.task = task
        self.done = asyncio.Event()
        self.content: str | None = None
        self.is_error = True
        # True once the synthetic "still running" answer exposed the id to
        # the model; never-exposed entries are dropped when they settle.
        self.exposed = False


class TaskStore:
    """Registry of escalated tool tasks, keyed by ``task<N>`` ids."""

    def __init__(self) -> None:
        self._tasks: dict[str, _TrackedTask] = {}
        self._counter = itertools.count(1)

    def track(self, task: asyncio.Task) -> _TrackedTask:
        """Start tracking a running handler task; returns its entry."""
        tid = f"task{next(self._counter)}"
        entry = _TrackedTask(tid, task)
        self._tasks[tid] = entry
        task.add_done_callback(lambda t, entry=entry: self._settle(entry, t))
        return entry

    def known_ids(self) -> list[str]:
        return list(self._tasks)

    def untrack(self, tid: str) -> None:
        """Drop a finished entry whose id was never exposed to the model
        (fast path — it completed within the soft window)."""
        self._tasks.pop(tid, None)

    def _settle(self, entry: _TrackedTask, task: asyncio.Task) -> None:
        # Retrieving result/exception here also marks them as retrieved,
        # avoiding "Task exception was never retrieved" warnings.
        if task.cancelled():
            entry.content, entry.is_error = "task was cancelled", True
        elif (exc := task.exception()) is not None:
            entry.content, entry.is_error = str(exc), True
        else:
            raw = task.result()
            if isinstance(raw, ToolResult):
                entry.content, entry.is_error = raw.content, raw.is_error
            elif isinstance(raw, str):
                entry.content, entry.is_error = raw, False
            else:
                entry.content = "handler must return str or ToolResult"
                entry.is_error = True
        entry.done.set()
        if not entry.exposed:
            # Cancelled mid-call: the id never reached the model, so nothing
            # can ever poll it — do not let the entry linger in the store.
            self._tasks.pop(entry.id, None)

    async def read(self, tid: str, wait_s: int) -> ToolResult:
        entry = self._tasks.get(tid)
        if entry is None:
            known = ", ".join(self.known_ids()) or "none"
            raise ValueError(f"unknown task id: {tid} (known: {known})")
        if wait_s > 0 and not entry.done.is_set():
            try:
                await asyncio.wait_for(entry.done.wait(), timeout=wait_s)
            except TimeoutError:
                pass
        if not entry.done.is_set():
            return ToolResult(
                tool_call_id=tid,
                name="tool_read",
                content=f"status: running\n{tid} is still executing",
                is_error=False,
            )
        return ToolResult(
            tool_call_id=tid,
            name="tool_read",
            content=entry.content or "",
            is_error=entry.is_error,
        )


def tool_read_spec(store: TaskStore | None) -> ToolSpec:
    async def handler(args: dict) -> ToolResult:
        if store is None:
            raise ValueError("no task store in this context")
        tid = args.get("id")
        if not isinstance(tid, str) or not tid.strip():
            raise ValueError("id must be a non-empty string")
        raw_wait = args.get("wait")
        if raw_wait is None:
            wait_s = 0
        elif isinstance(raw_wait, bool) or not isinstance(raw_wait, int) or raw_wait < 0:
            raise ValueError("wait must be a non-negative integer")
        else:
            wait_s = min(raw_wait, _READ_WAIT_MAX_S)
        return await store.read(tid, wait_s)

    return ToolSpec(
        name="tool_read",
        description=(
            "Read the result of a tool call that moved to background after the "
            "60-second soft timeout (the tool returned 'still running: task<N>'). "
            "The task keeps running even if its turn is cancelled. wait: seconds "
            "to wait for completion before replying (0..60, default 0)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "wait": {"type": "integer"},
            },
            "required": ["id"],
            "additionalProperties": False,
        },
        handler=handler,
        parallel_safe=True,
        # Reading a result is bounded by wait<=60s and must never be wrapped
        # itself (recursive tracking would give every read a fresh task id).
        escalates=True,
    )
