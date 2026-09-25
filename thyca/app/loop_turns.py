"""Thread/asyncio bridge: run a claimed turn's coroutine on the loop thread.

The per-session registry lives in :class:`TurnState` (the one turn registry);
this module is the asyncio view over it: job spawn/await plus cancel.
"""
from __future__ import annotations

import asyncio
import concurrent.futures

from thyca.serve.turn_state import _TurnJob, TurnState

_CANCEL_WAIT_S = 5.0


class TurnCancelled(Exception):
    """In-flight turn was cancelled; not a provider failure.

    Lives here (not next to ChatApp.cancel) because _LoopTurns.submit is
    the only raiser; ChatApp.cancel only requests, never raises it.
    """


class _LoopTurns:
    """asyncio.Task per claimed session; each job is locked across spawn/cancel."""

    def __init__(self, loop: asyncio.AbstractEventLoop, turns: TurnState) -> None:
        self._loop = loop
        self._turns = turns

    def begin(self, session_id: str) -> _TurnJob:
        job = _TurnJob()
        self._turns.attach(session_id, job)
        return job

    def end(self, session_id: str, job: _TurnJob) -> None:
        self._turns.detach(session_id, job)

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
        job = self._turns.job(session_id)
        if job is None:
            return False
        with job.lock:
            job.cancel = True
            task = job.task
        if task is not None:
            self._loop.call_soon_threadsafe(task.cancel)
        return True
