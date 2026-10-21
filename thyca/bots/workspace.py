"""BotWorkspace aggregate + per-process registry (WS-2 turn routing).

One bot = one workspace; DM/group windows are bound views into it. The
registry also owns the per-bot turn lease enforcing one active turn per
bot across all windows; a nested cross-bot request that would close a
wait-for cycle raises :class:`WorkspaceTurnConflict` instead of
deadlocking.
"""
from __future__ import annotations

import asyncio
import contextvars
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from thyca.bots.workspace_store import (
    WindowBinding,
    WorkspaceStore,
    _check_bot_id,
)
from thyca.core.channel import WindowRef


class WorkspaceTurnConflict(Exception):
    """Granting this turn's nested lease would close a wait-for cycle."""


class _LeaseOwner:
    """Ownership token for one turn's leases.

    Lives in a contextvar so tool handlers running in child tasks of a
    turn (``asyncio.gather``/``create_task``) inherit the same owner —
    that is what lets the wait-for graph see the leases a nested A2A
    delivery already holds, and what makes a same-bot re-acquire a
    conflict instead of a silent reentry."""

    __slots__ = ()


class _BotLease:
    __slots__ = ("lock", "owner")

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.owner: _LeaseOwner | None = None


class BotWorkspace:
    """One bot's workspace: window bindings over the registry."""

    def __init__(
        self,
        bot_id: str,
        store: WorkspaceStore,
        registry: "WorkspaceRegistry | None" = None,
    ) -> None:
        _check_bot_id(bot_id)
        self.bot_id = bot_id
        self._store = store
        self._registry = registry

    def bindings(self) -> dict[str, WindowBinding]:
        """Every known window binding in the registry."""
        return self._store.load(self.bot_id)

    def get_window(self, window_id: str) -> WindowBinding | None:
        return self._store.get_binding(self.bot_id, window_id)

    def bind_window(
        self,
        window: WindowRef,
        *,
        internal_session_id: str | None = None,
        cursor: tuple[int, str] | None = None,
    ) -> WindowBinding:
        return self._store.bind_window(
            self.bot_id,
            window,
            internal_session_id=internal_session_id,
            cursor=cursor,
        )

    def turn_lease(self):
        """This bot's one-turn-at-a-time lease (async context manager)."""
        if self._registry is None:
            raise RuntimeError("workspace has no turn registry")
        return self._registry.lease(self.bot_id)


class WorkspaceRegistry:
    """All workspaces for one ``thyca_dir``; shared singleton per instance.

    Turn leases are asyncio locks on the app's event loop; the wait-for
    edges between held bots and their requested bot power the deadlock
    guard for nested A2A delivery."""

    def __init__(self, thyca_dir: Path) -> None:
        self._store = WorkspaceStore(thyca_dir)
        self._workspaces: dict[str, BotWorkspace] = {}
        self._lock = threading.Lock()
        self._leases: dict[str, _BotLease] = {}
        self._waiting: dict[_LeaseOwner, dict[str, int]] = {}
        self._owner_ctx: contextvars.ContextVar = contextvars.ContextVar(
            "thyca_workspace_lease_owner", default=None
        )

    @property
    def store(self) -> WorkspaceStore:
        return self._store

    def workspace(self, bot_id: str) -> BotWorkspace:
        """The one BotWorkspace for ``bot_id`` in this process."""
        with self._lock:
            workspace = self._workspaces.get(bot_id)
            if workspace is None:
                workspace = BotWorkspace(bot_id, self._store, registry=self)
                self._workspaces[bot_id] = workspace
            return workspace

    def holds(self, bot_id: str) -> bool:
        """True when the current turn context owns ``bot_id``'s lease."""
        owner = self._owner_ctx.get()
        lease = self._leases.get(bot_id)
        return (
            owner is not None and lease is not None and lease.owner is owner
        )

    def busy(self, bot_id: str) -> bool:
        """True when ``bot_id``'s turn lease is currently held.

        Read-only peek used by the scheduler's skip_busy path — never
        creates or mutates a lease."""
        lease = self._leases.get(bot_id)
        return lease is not None and lease.lock.locked()

    def _wait_cycle(self, owner: _LeaseOwner) -> bool:
        """True when granting ``owner``'s pending waits closes a cycle.

        DFS through CURRENT lease owners: from each bot ``owner`` waits
        on, follow that bot's ``lease.owner`` to the waiting owner, then
        its pending targets, until a chain reaches ``owner`` again."""
        seen: set[int] = set()
        stack: list[_LeaseOwner] = []
        for target in self._waiting.get(owner, {}):
            lease = self._leases.get(target)
            if lease is not None and lease.owner is not None:
                stack.append(lease.owner)
        while stack:
            waiting_owner = stack.pop()
            if waiting_owner is owner:
                return True
            if id(waiting_owner) in seen:
                continue
            seen.add(id(waiting_owner))
            for target in self._waiting.get(waiting_owner, {}):
                lease = self._leases.get(target)
                if lease is not None and lease.owner is not None:
                    stack.append(lease.owner)
        return False

    @asynccontextmanager
    async def lease(self, bot_id: str):
        """Exclusive turn lease for ``bot_id`` (one bot, one turn).

        Never re-entrant: acquiring a lease the calling turn context
        already owns raises :class:`WorkspaceTurnConflict` — legitimate
        reuse goes through ``holds()`` + ``workspace_claimed`` instead.
        An owner already holding bots records a counted wait-for edge to
        ``bot_id`` before blocking (parallel nested requests keep every
        edge); if that would close a cycle the acquire raises
        :class:`WorkspaceTurnConflict` instead of waiting. Cancellation
        while queued drops only this request's edge and acquires
        nothing."""
        _check_bot_id(bot_id)
        owner = self._owner_ctx.get()
        if owner is None:
            owner = _LeaseOwner()
        lease = self._leases.get(bot_id)
        if lease is None:
            lease = self._leases[bot_id] = _BotLease()
            self._leases[bot_id] = lease
        if lease.owner is owner:
            raise WorkspaceTurnConflict(
                f"turn {bot_id} already running in this context"
            )
        pending = self._waiting.setdefault(owner, {})
        pending[bot_id] = pending.get(bot_id, 0) + 1
        try:
            if self._wait_cycle(owner):
                raise WorkspaceTurnConflict(
                    f"turn lease cycle: waiting on {bot_id} closes a loop"
                )
            await lease.lock.acquire()
        finally:
            pending = self._waiting.get(owner)
            if pending is not None and pending.get(bot_id):
                pending[bot_id] -= 1
                if pending[bot_id] <= 0:
                    del pending[bot_id]
                if not pending:
                    del self._waiting[owner]
        lease.owner = owner
        token = self._owner_ctx.set(owner)
        try:
            yield
        finally:
            self._owner_ctx.reset(token)
            lease.owner = None
            lease.lock.release()
