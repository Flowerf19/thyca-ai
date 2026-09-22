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

import queue
import threading

from thyca.core.protocol import utc_now_ts
from thyca.sessions import SessionBusy, SessionManager


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


class TurnState:
    """``session_id -> started_at`` for turns in flight, behind one lock."""

    def __init__(self) -> None:
        # Plain lock, not reentrant: nothing here calls back into the claim
        # while holding it, and re-entry would mean the delete gate is being
        # used from inside itself.
        self._lock = threading.Lock()
        self._running: dict[str, str] = {}
        self._hubs: dict[str, TurnHub] = {}

    def snapshot(self) -> dict[str, str]:
        """Copy of the in-flight map, for callers that only need to read it."""
        with self._lock:
            return dict(self._running)

    def started_at(self, session_id: str) -> str | None:
        with self._lock:
            return self._running.get(session_id)

    def hub(self, session_id: str) -> TurnHub | None:
        with self._lock:
            return self._hubs.get(session_id)

    def claim(self, session_id: str) -> TurnHub:
        """Take the session for a turn, or raise :class:`SessionBusy`.

        Claiming is what makes a second turn on a busy session an explicit 409
        instead of a silent queue behind the first one's LLM call.
        """
        with self._lock:
            if session_id in self._running:
                raise SessionBusy(session_id)
            self._running[session_id] = utc_now_ts()
            hub = TurnHub()
            self._hubs[session_id] = hub
            return hub

    def release(self, session_id: str) -> None:
        with self._lock:
            self._running.pop(session_id, None)
            hub = self._hubs.pop(session_id, None)
        if hub is not None:
            hub.close()

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
            manager.delete(session_id, keep=set(self._running))
