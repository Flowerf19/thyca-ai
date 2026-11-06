"""Bot identity for memory ownership (leaf module, stdlib-only deps).

Both the tool layer (which binds the in-flight bot) and the memory facade
(which enforces per-bot read/write policy) import from here, so neither
can cycle on the other.
"""
from __future__ import annotations

from contextvars import ContextVar

#: In-flight bot id, or None for the default Thyca/CLI turn (the user).
_bot_id: ContextVar[str | None] = ContextVar("thyca_bot_id", default=None)


def bind_bot_id(bot_id: str | None):
    """Pin the in-flight bot id. Returns the reset token."""
    return _bot_id.set(bot_id)


def reset_bot_id(token) -> None:
    _bot_id.reset(token)


def current_bot_id() -> str | None:
    return _bot_id.get()
