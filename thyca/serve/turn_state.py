"""Turn claims: which conversations have a turn in flight, and the delete gate.

One turn per conversation at a time; other conversations stay free. The claim is not just
bookkeeping — a conversation being deleted must not lose the transcript of a turn
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
from thyca.conversations.runtime import ConversationBusy, ConversationError, ConversationManager


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
    """The one turn registry: ``conversation_id -> turn`` for turns in flight.

    The claim (hub + started_at) and the loop job share one record behind
    one lock; :class:`_LoopTurns` is a view over it for the asyncio side.
    """

    def __init__(self) -> None:
        # Plain lock, not reentrant: nothing here calls back into the claim
        # while holding it, and re-entry would mean the delete gate is being
        # used from inside itself.
        self._lock = threading.Lock()
        self._turns: dict[str, _Turn] = {}
        # Conversations with a manual LLM compaction in flight. Unlike the
        # sync rule path (rewrite under this lock), summarization spends
        # seconds off-lock, so the conversation needs its own busy marker that
        # claim/delete honor for the whole operation.
        self._compacts: set[str] = set()

    def snapshot(self) -> dict[str, str]:
        """Copy of the in-flight map, for callers that only need to read it."""
        with self._lock:
            return {sid: turn.started_at for sid, turn in self._turns.items()}

    def started_at(self, conversation_id: str) -> str | None:
        with self._lock:
            turn = self._turns.get(conversation_id)
            return turn.started_at if turn is not None else None

    # True when a turn or manual compaction holds one conversation.
    def is_busy(self, conversation_id: str) -> bool:
        with self._lock:
            return conversation_id in self._turns or conversation_id in self._compacts

    def hub(self, conversation_id: str) -> TurnHub | None:
        with self._lock:
            turn = self._turns.get(conversation_id)
            return turn.hub if turn is not None else None

    def job(self, conversation_id: str) -> _TurnJob | None:
        """The loop job attached by :class:`_LoopTurns`, if any."""
        with self._lock:
            turn = self._turns.get(conversation_id)
            return turn.job if turn is not None else None

    def claim(self, conversation_id: str) -> TurnHub:
        """Take the conversation for a turn, or raise :class:`ConversationBusy`.

        Claiming is what makes a second turn on a busy conversation an explicit 409
        instead of a silent queue behind the first one's LLM call.
        """
        with self._lock:
            if conversation_id in self._turns or conversation_id in self._compacts:
                raise ConversationBusy(conversation_id)
            hub = TurnHub()
            self._turns[conversation_id] = _Turn(utc_now_ts(), hub)
            return hub

    def begin_compact(self, conversation_id: str) -> None:
        """Take a conversation for manual compaction, or raise ConversationBusy."""
        with self._lock:
            if conversation_id in self._turns or conversation_id in self._compacts:
                raise ConversationBusy(conversation_id)
            self._compacts.add(conversation_id)

    def end_compact(self, conversation_id: str) -> None:
        with self._lock:
            self._compacts.discard(conversation_id)

    def attach(self, conversation_id: str, job: _TurnJob) -> None:
        """Pin the loop job to a claimed turn (the _LoopTurns.begin half)."""
        with self._lock:
            turn = self._turns.get(conversation_id)
            if turn is None:
                raise ConversationError(f"no turn claimed for conversation: {conversation_id}")
            turn.job = job

    def detach(self, conversation_id: str, job: _TurnJob) -> None:
        """Unpin the loop job, but only when it is still this one."""
        with self._lock:
            turn = self._turns.get(conversation_id)
            if turn is not None and turn.job is job:
                turn.job = None

    def release(self, conversation_id: str) -> None:
        with self._lock:
            turn = self._turns.pop(conversation_id, None)
        if turn is not None:
            turn.hub.close()

    # Delete one door unless a turn currently holds it.
    def delete_unclaimed(self, manager: ConversationManager, conversation_id: str) -> None:
        """Delete a conversation unless a turn holds it.

        The keep-check and the delete share this lock with :meth:`claim`, so a
        turn either claims before the delete (and gets a 409) or after it (and
        loads a conversation that no longer exists). What cannot happen is the
        delete landing between the check and a claim: the turn would then keep
        appending to a removed door and silently drop the earlier history.
        """
        with self._lock:
            manager.delete(conversation_id, keep=set(self._turns) | self._compacts)
