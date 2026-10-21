"""Per-turn memory visibility guard (WS-3).

A bound :class:`MemoryView` restricts which memory entries a turn may
read by the entry's ORIGIN window (`chat`), on top of the existing bot
owner rule. No view bound = existing behavior, byte for byte.

Rules:

- Group window: the current window's own origin is allowed (the owner
  rule still applies). Any other origin must resolve to a real group
  session whose participant list covers the current window's
  participants. Unknown/missing origins and every DM origin are denied.
- Private view (DM window, native root/CLI context, unresolved/system
  room): a DM origin is readable only by its owner bot; a group origin
  is readable when this bot participates; unresolved/legacy origins keep
  the existing owner-scoped behavior.
"""
from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from thyca.core.channel import WindowRef
from thyca.sessions.store import SessionStore


@dataclass(frozen=True)
class MemoryView:
    """Immutable per-turn view: which origins this bot may read."""

    window: WindowRef | None
    bot_id: str
    thyca_dir: Path

    @property
    def is_group(self) -> bool:
        return self.window is not None and self.window.kind == "group"

    def _origin_info(self, chat: str) -> tuple[bool, str | None, tuple[str, ...]] | None:
        """(is_dm, dm_bot, participants) of the origin session or None.

        Only a genuinely missing session resolves to None; corrupt or
        unreadable files raise — downgrading them to "private" would hide
        a data problem."""
        from thyca.sessions.errors import SessionNotFound

        try:
            session = SessionStore(self.thyca_dir / "sessions").load(chat)
        except (SessionNotFound, FileNotFoundError):
            return None
        participants = tuple(session.bots) if session.bots else ("thyca",)
        return session.dm_bot is not None, session.dm_bot, participants

    def can_read_origin(self, chat: str | None) -> bool:
        if self.is_group:
            if not isinstance(chat, str) or not chat:
                return False
            if chat == self.window.window_id:
                return True
            info = self._origin_info(chat)
            if info is None:
                return False
            is_dm, _dm_bot, participants = info
            if is_dm:
                return False
            return set(self.window.participants) <= set(participants)
        if not isinstance(chat, str) or not chat:
            return True
        if self.window is not None and chat == self.window.window_id:
            return True
        info = self._origin_info(chat)
        if info is None:
            return True
        is_dm, dm_bot, participants = info
        if is_dm:
            return dm_bot == self.bot_id
        return self.bot_id in participants

    def can_read_entry(self, chat: str | None, bots: tuple | None) -> bool:
        from thyca.memory.active import visible_to

        if not visible_to(bots, self.bot_id):
            return False
        return self.can_read_origin(chat)


_current_view: contextvars.ContextVar = contextvars.ContextVar(
    "thyca_memory_view", default=None
)


def current_memory_view() -> MemoryView | None:
    return _current_view.get()


@contextmanager
def bound_memory_view(view: MemoryView):
    token = _current_view.set(view)
    try:
        yield view
    finally:
        _current_view.reset(token)
