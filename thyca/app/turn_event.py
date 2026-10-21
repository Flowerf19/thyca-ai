"""Trusted inbound channel event for the in-flight turn.

Bound by WorkspaceTurnService before the executor starts; the appending
path stamps the user row's metadata from it, and the pending-followup
consume runs off the same context. Never model-visible.
"""
from __future__ import annotations

from contextvars import ContextVar

from thyca.core.channel import ChannelEvent

_current: ContextVar[ChannelEvent | None] = ContextVar(
    "thyca_channel_event", default=None
)


def current_channel_event() -> ChannelEvent | None:
    return _current.get()


def bind_channel_event(event: ChannelEvent | None):
    return _current.set(event)


def reset_channel_event(token) -> None:
    _current.reset(token)
