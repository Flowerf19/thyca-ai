"""Turn claims: which sessions have a turn in flight, and the delete gate.

One turn per session at a time; other sessions stay free. The claim is not just
bookkeeping — a session being deleted must not lose the transcript of a turn
that is still appending to it. ``delete_unclaimed`` therefore does the
keep-check *and* the unlink under the claim lock, so no turn can claim in
between and end up writing into a removed path.

The claim also owns a :class:`TurnHub` so a second client can replay the
in-flight events instead of waiting for the notebook to land.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import queue
import threading

from thyca.core.protocol import utc_now_ts
from thyca.sessions import SessionBusy, SessionError, SessionManager


class TurnHub:
    """Buffered fan-out of one turn's items: events, then one terminal.

    The starter's HTTP connection is just one subscriber. Late joiners replay
    the log and then tail; a disconnect only drops that subscriber.
    """

    SENTINEL = object()

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: list[object] = []
        self._subs: list[queue.Queue] = []
        self._closed = False

    def publish(self, item: object) -> None:
        with self._lock:
            if self._closed:
                return
            self._items.append(item)
            for sub in self._subs:
                sub.put(item)

    def subscribe(self) -> queue.Queue:
        sub: queue.Queue = queue.Queue()
        with self._lock:
            for item in self._items:
                sub.put(item)
            if self._closed:
                sub.put(self.SENTINEL)
            else:
                self._subs.append(sub)
        return sub

    def drop(self, sub: queue.Queue) -> None:
        with self._lock:
            try:
                self._subs.remove(sub)
            except ValueError:
                pass

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for sub in self._subs:
                sub.put(self.SENTINEL)
            self._subs.clear()


class _TurnJob:
    """One turn's asyncio handle: task, cancel flag, and completion future."""

    __slots__ = ("task", "cancel", "cfut", "lock")

    def __init__(self) -> None:
        self.task: asyncio.Task | None = None
        self.cancel = False
        self.cfut: concurrent.futures.Future = concurrent.futures.Future()
        self.lock = threading.Lock()


class _Turn:
    """One claimed turn: started_at stamp, event hub, and loop job."""

    __slots__ = ("hub", "job", "started_at")

    def __init__(self, started_at: str, hub: TurnHub) -> None:
        self.started_at = started_at
        self.hub = hub
        self.job: _TurnJob | None = None


class TurnState:
    """The one turn registry: ``session_id -> turn`` for turns in flight.

    The claim (hub + started_at) and the loop job share one record behind
    one lock; :class:`_LoopTurns` is a view over it for the asyncio side.
    """

    def __init__(self) -> None:
        # Plain lock, not reentrant: nothing here calls back into the claim
        # while holding it, and re-entry would mean the delete gate is being
        # used from inside itself.
        self._lock = threading.Lock()
        self._turns: dict[str, _Turn] = {}

    def snapshot(self) -> dict[str, str]:
        """Copy of the in-flight map, for callers that only need to read it."""
        with self._lock:
            return {sid: turn.started_at for sid, turn in self._turns.items()}

    def started_at(self, session_id: str) -> str | None:
        with self._lock:
            turn = self._turns.get(session_id)
            return turn.started_at if turn is not None else None

    def hub(self, session_id: str) -> TurnHub | None:
        with self._lock:
            turn = self._turns.get(session_id)
            return turn.hub if turn is not None else None

    def job(self, session_id: str) -> _TurnJob | None:
        """The loop job attached by :class:`_LoopTurns`, if any."""
        with self._lock:
            turn = self._turns.get(session_id)
            return turn.job if turn is not None else None

    def claim(self, session_id: str) -> TurnHub:
        """Take the session for a turn, or raise :class:`SessionBusy`.

        Claiming is what makes a second turn on a busy session an explicit 409
        instead of a silent queue behind the first one's LLM call.
        """
        with self._lock:
            if session_id in self._turns:
                raise SessionBusy(session_id)
            hub = TurnHub()
            self._turns[session_id] = _Turn(utc_now_ts(), hub)
            return hub

    def attach(self, session_id: str, job: _TurnJob) -> None:
        """Pin the loop job to a claimed turn (the _LoopTurns.begin half)."""
        with self._lock:
            turn = self._turns.get(session_id)
            if turn is None:
                raise SessionError(f"no turn claimed for session: {session_id}")
            turn.job = job

    def detach(self, session_id: str, job: _TurnJob) -> None:
        """Unpin the loop job, but only when it is still this one."""
        with self._lock:
            turn = self._turns.get(session_id)
            if turn is not None and turn.job is job:
                turn.job = None

    def release(self, session_id: str) -> None:
        with self._lock:
            turn = self._turns.pop(session_id, None)
        if turn is not None:
            turn.hub.close()

    def delete_unclaimed(self, manager: SessionManager, session_id: str) -> None:
        """Delete a session unless a turn holds it.

        The keep-check and the unlink share this lock with :meth:`claim`, so a
        turn either claims before the delete (and gets a 409) or after it (and
        loads a session that no longer exists). What cannot happen is the
        unlink landing between the check and a claim: the turn would then keep
        appending to the removed path, re-creating the file truncated on its
        next write and silently dropping the earlier history.
        """
        with self._lock:
            manager.delete(session_id, keep=set(self._turns))
