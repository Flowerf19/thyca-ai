"""Thread/asyncio bridge: one asyncio.Task per claimed session (split from chat_app, M8)."""
from __future__ import annotations

import asyncio
import concurrent.futures
import threading

_CANCEL_WAIT_S = 5.0


class TurnCancelled(Exception):
    """In-flight turn was cancelled; not a provider failure.

    Lives here (not next to ChatApp.cancel) because _LoopTurns.submit is
    the only raiser; ChatApp.cancel only requests, never raises it.
    """


class _TurnJob:
    __slots__ = ("task", "cancel", "cfut", "lock")

    def __init__(self) -> None:
        self.task: asyncio.Task | None = None
        self.cancel = False
        self.cfut: concurrent.futures.Future = concurrent.futures.Future()
        self.lock = threading.Lock()


class _LoopTurns:
    """asyncio.Task per claimed session; each job is locked across spawn/cancel."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._jobs: dict[str, _TurnJob] = {}
        self._lock = threading.Lock()

    def begin(self, session_id: str) -> _TurnJob:
        job = _TurnJob()
        with self._lock:
            self._jobs[session_id] = job
        return job

    def end(self, session_id: str, job: _TurnJob) -> None:
        with self._lock:
            if self._jobs.get(session_id) is job:
                del self._jobs[session_id]

    def submit(self, job: _TurnJob, coro):
        def spawn() -> None:
            with job.lock:
                if job.cancel:
                    coro.close()
                    job.cfut.cancel()
                    return
                task = self._loop.create_task(coro)
                job.task = task

            def done(finished: asyncio.Task) -> None:
                if job.cfut.done():
                    return
                if finished.cancelled():
                    job.cfut.cancel()
                    return
                exc = finished.exception()
                if exc is not None:
                    job.cfut.set_exception(exc)
                else:
                    job.cfut.set_result(finished.result())

            task.add_done_callback(done)

        self._loop.call_soon_threadsafe(spawn)
        try:
            return job.cfut.result()
        except concurrent.futures.CancelledError:
            raise TurnCancelled() from None

    def request_cancel(self, session_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(session_id)
        if job is None:
            return False
        with job.lock:
            job.cancel = True
            task = job.task
        if task is not None:
            self._loop.call_soon_threadsafe(task.cancel)
        return True
