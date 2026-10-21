"""WebUI channel adapter (WS-2): session rows -> channel contract.

Derives real window metadata from the session's own ``dm_bot``/``bots``
markers — nothing is guessed from ids, and the actor is always the
native user (``ActorRef("user", "user")``), assigned here by the trusted
adapter, never from model-supplied values.
"""
from __future__ import annotations

import uuid

from thyca.core.channel import (
    ActorRef,
    ChannelAction,
    ChannelCapabilities,
    ChannelEvent,
    ChannelRef,
    WindowRef,
)
from thyca.core.protocol import utc_now_ts
from thyca.sessions import Session

WEBUI_CHANNEL = ChannelRef(channel_id="webui", kind="webui")

WEBUI_CAPABILITIES = ChannelCapabilities(
    can_dm=True,
    can_card=True,
    supports_streaming=True,
)


class WebUIChannelAdapter:
    """Maps room sessions to ``webui`` windows and inbound events."""

    channel = WEBUI_CHANNEL
    capabilities = WEBUI_CAPABILITIES

    def window_for(self, session: Session) -> WindowRef:
        participants = (
            tuple(session.bots) if session.bots else ("thyca",)
        )
        if session.dm_bot is not None:
            return WindowRef(
                window_id=session.id,
                channel_id=self.channel.channel_id,
                kind="dm",
                participants=participants,
            )
        return WindowRef(
            window_id=session.id,
            channel_id=self.channel.channel_id,
            kind="group",
            participants=participants,
        )

    def event_for(
        self,
        session: Session,
        text: str,
        *,
        event_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> ChannelEvent:
        window = self.window_for(session)
        return ChannelEvent(
            event_id=event_id or f"ev-{uuid.uuid4().hex}",
            channel_id=window.channel_id,
            window_id=window.window_id,
            actor=ActorRef(kind="user", id="user"),
            text=text,
            visibility=window.visibility,
            received_at=utc_now_ts(),
            idempotency_key=idempotency_key or uuid.uuid4().hex,
        )


class WebUIOutboundAdapter:
    """Outbound half of the WebUI channel: real room writers.

    Holds the app's low-level callbacks; ``land`` resolves the target
    window and calls them under the trusted turn context — the sender
    and target never come from model arguments.
    """

    channel_id = "webui"
    capabilities = WEBUI_CAPABILITIES

    def __init__(
        self,
        *,
        dm_post,
        dm_card_post,
        room_post,
        card_post,
        session_for,
    ) -> None:
        self._dm_post = dm_post
        self._dm_card_post = dm_card_post
        self._room_post = room_post
        self._card_post = card_post
        self._session_for = session_for

    def land(self, action: ChannelAction, *, ctx) -> dict:
        sender = ctx.sender_id
        session = self._session_for(action.target_window)
        if session is None:
            return {"ok": False, "error": f"unknown room: {action.target_window}"}
        if session.dm_bot is not None and session.dm_bot != sender:
            return {"ok": False, "error": "room is another bot's DM"}
        if session.dm_bot is None and sender not in (session.bots or ["thyca"]):
            return {"ok": False, "error": "sender is not a participant"}
        if (
            session.dm_bot is None
            and action.target_window != ctx.room_id
        ):
            return {"ok": False, "error": "cross-window public action denied"}
        if action.kind == "ask_private":
            if session.dm_bot != sender:
                return {"ok": False, "error": "ask_private target is not own DM"}
            return self._dm_post(
                sender, action.text, ctx.room_id,
                meta=action.metadata,
            )
        if action.kind in ("post", "reply") and session.dm_bot == sender:
            return self._dm_post(
                sender, action.text, ctx.room_id, meta=action.metadata)
        if action.kind == "ask_choice":
            return self._card_post(
                sender, action.card, action.target_window, action.text)
        return self._room_post(
            sender, action.text, action.target_window,
            meta=action.metadata,
        )
