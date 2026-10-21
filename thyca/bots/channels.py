"""Channel adapters + action service (WS-4).

A channel adapter knows how to land a :class:`ChannelAction` on its own
transport. The registry holds the native adapters (WebUI today); the
service validates actions centrally (kind/capabilities/targets) and is
the only place tools may ask for outbound writes. Sender and target
identity come from the trusted turn context passed to ``dispatch`` —
never from model arguments.
"""
from __future__ import annotations

from typing import Protocol

from thyca.core.channel import ChannelAction, ChannelCapabilities


class ChannelAdapter(Protocol):
    """Native transport adapter (WebUI is the only registered one)."""

    channel_id: str
    capabilities: ChannelCapabilities

    def land(self, action: ChannelAction, *, ctx) -> dict:
        """Deliver ``action`` on this channel; returns the payload the
        low-level writer produced ({"ok": True, "session_id": ...})."""


class ChannelRegistry:
    """channel_id -> adapter, for action dispatch + capability checks."""

    def __init__(self) -> None:
        self._adapters: dict[str, ChannelAdapter] = {}

    def register(self, adapter: ChannelAdapter) -> None:
        self._adapters[adapter.channel_id] = adapter

    def get(self, channel_id: str) -> ChannelAdapter | None:
        return self._adapters.get(channel_id)


class ChannelActionError(ValueError):
    """Action failed central validation (kind/capability/target)."""


_KINDS = ("reply", "post", "ask_private", "ask_choice", "status")


class ChannelActionService:
    """Central validator + dispatcher for outbound channel actions."""

    def __init__(self, registry: ChannelRegistry) -> None:
        self._registry = registry

    def validate(
        self, action: ChannelAction, *, channel_id: str, ctx
    ) -> None:
        if action.kind not in _KINDS:
            raise ChannelActionError(f"unknown action kind: {action.kind}")
        adapter = self._registry.get(channel_id)
        if adapter is None:
            raise ChannelActionError(f"no adapter for channel {channel_id}")
        sender = getattr(ctx, "sender_id", None) if ctx is not None else None
        if not sender:
            raise ChannelActionError("action requires a trusted sender")
        caps = adapter.capabilities
        if action.kind == "ask_private" and not caps.can_dm:
            raise ChannelActionError("channel cannot deliver DM ask")
        if action.kind == "ask_choice" and (
            not caps.can_card or action.card is None
        ):
            raise ChannelActionError("ask_choice needs a card-capable channel")
        if action.card is not None and not caps.can_card:
            raise ChannelActionError("channel cannot render cards")
        if caps.max_chars is not None and len(action.text) > caps.max_chars:
            raise ChannelActionError("text exceeds channel max_chars")

    def dispatch(
        self, action: ChannelAction, *, channel_id: str, ctx
    ) -> dict:
        self.validate(action, channel_id=channel_id, ctx=ctx)
        adapter = self._registry.get(channel_id)
        assert adapter is not None
        return adapter.land(action, ctx=ctx)
