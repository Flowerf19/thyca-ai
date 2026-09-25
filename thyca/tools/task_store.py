"""Store for tool calls that outlived the gateway's soft window.

A handler still running when ``ToolGateway``'s soft timeout expires keeps
executing as a tracked task; the model polls its final result with the
``tool_read`` tool. One store per app (daemon or one CLI run); handlers run
on the app's own event loop, so no thread locking is needed.
"""
from __future__ import annotations

import asyncio
import itertools

from thyca.core.protocol import ToolResult
from thyca.tools.gateway.execution import (
    RETAINED_MAX_BYTES,
    await_done,
    head_bytes,
    render_page,
    unknown_id,
)

_KILL_WAIT_S = 5.0


class _TrackedTask:
    """One escalated tool call and its settled outcome."""

    __slots__ = ("content", "done", "exposed", "id", "is_error", "overflow", "task")

    def __init__(self, tid: str, task: asyncio.Task) -> None:
        self.id = tid
        self.task = task
        self.done = asyncio.Event()
        self.content: str | None = None
        self.is_error = True
        # Bytes past retention that were never kept (readers see the gap).
        self.overflow = 0
        # True once the synthetic "still running" answer exposed the id to
        # the model; never-exposed entries are dropped when they settle.
        self.exposed = False


class TaskStore:
    """Registry of escalated tool tasks, keyed by gateway exec ids
    (``task<N>`` fallback when tracking outside the gateway)."""

    def __init__(self) -> None:
        self._tasks: dict[str, _TrackedTask] = {}
        self._counter = itertools.count(1)

    def track(self, task: asyncio.Task, *, id: str | None = None) -> _TrackedTask:
        """Start tracking a running handler task; returns its entry."""
        tid = id if id is not None else f"task{next(self._counter)}"
        if tid in self._tasks:
            raise ValueError(f"task already tracked: {tid}")
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
            text, entry.is_error = "task was cancelled", True
        elif (exc := task.exception()) is not None:
            text, entry.is_error = str(exc), True
        else:
            raw = task.result()
            if isinstance(raw, ToolResult):
                text, entry.is_error = raw.content, raw.is_error
            elif isinstance(raw, str):
                text, entry.is_error = raw, False
            else:
                text = "handler must return str or ToolResult"
                entry.is_error = True
        kept = head_bytes(text.encode("utf-8"), RETAINED_MAX_BYTES)
        entry.content = kept.decode("utf-8")
        entry.overflow = len(text.encode("utf-8")) - len(kept)
        entry.done.set()
        if not entry.exposed:
            # Cancelled mid-call: the id never reached the model, so nothing
            # can ever poll it — do not let the entry linger in the store.
            self._tasks.pop(entry.id, None)

    async def read(
        self,
        tid: str,
        wait_s: int,
        *,
        offset: int | None = None,
        limit: int | None = None,
    ) -> ToolResult:
        entry = self._tasks.get(tid)
        if entry is None:
            raise unknown_id("task", tid, self.known_ids())
        if wait_s > 0 and not entry.done.is_set():
            await await_done(entry.done, wait_s)
        if not entry.done.is_set():
            return ToolResult(
                tool_call_id=tid,
                name="tool_read",
                content=f"status: running\n{tid} is still executing",
                is_error=False,
            )
        text = entry.content or ""
        more = f'read more: tool_read id="{tid}"'
        content = render_page(text, offset, limit, more=more, unstored=entry.overflow)
        return ToolResult(
            tool_call_id=tid,
            name="tool_read",
            content=content,
            is_error=entry.is_error,
        )

    async def kill(self, tid: str) -> None:
        """Cancel one tracked task and await its settle (kill grace, then give up)."""
        entry = self._tasks.get(tid)
        if entry is None:
            raise unknown_id("task", tid, self.known_ids())
        entry.task.cancel()
        await await_done(entry.done, _KILL_WAIT_S)
