"""WorkspaceTurnService (WS-2): channel-event turn orchestration.

Owns what ``ChatApp.turn`` used to do inline: text/config validation,
responder resolution, the window claim, sink wiring, job submit, and the
terminal-event publish/release. The executor coroutine runs inside the
responding bot's workspace lease — one active turn per bot across all
windows — while window claims keep their existing per-conversation
semantics (follow/cancel/409 unchanged).
"""
from __future__ import annotations

import uuid
from zoneinfo import ZoneInfo

from thyca.agents.events import EventSink, TurnEvent
from thyca.app.loop_turns import TurnCancelled
from thyca.app.turn_event import (
    bind_channel_event,
    reset_channel_event,
)
from thyca.app.turn_options import (
    SYSTEM_TEXT_MAX,
    TEXT_MAX,
    _clean_turn_text,
    overlay_turn_cfg,
    resolve_bot_cfg,
)
from thyca.bots.workspace import WorkspaceRegistry
from thyca.conversations.runtime import ConversationBusy
from thyca.conversations.runtime.errors import NoLiveResponder
from thyca.core.channel import ActorRef, ChannelEvent
from thyca.core.protocol import utc_now_ts
from thyca.memories.visibility import MemoryView, bound_memory_view


class WorkspaceTurnService:
    """Runs one claimed window turn under the responder's bot lease."""

    def __init__(self, app, registry: WorkspaceRegistry) -> None:
        self._app = app
        self._workspaces = registry

    async def _leased(self, bot_id: str, window, factory, event=None,
                      skip_busy: bool = False):

        if skip_busy and self._workspaces.busy(bot_id):
            raise ConversationBusy(f"{bot_id} turn lease held")
        async with self._workspaces.lease(bot_id):
            self._app._bots.require(bot_id)
            if window is not None:
                window = self._app._webui.window_for(
                    self._app._conversations.store.load(window.window_id)
                )
                self._workspaces.store.bind_window(bot_id, window)

            _ev_token = bind_channel_event(event)
            try:
                with bound_memory_view(
                    MemoryView(
                        window=window, bot_id=bot_id, thyca_dir=self._app._root
                    )
                ):
                    return await factory()
            finally:
                reset_channel_event(_ev_token)

    def run(
        self,
        event: ChannelEvent,
        *,
        window=None,
        event_sink: EventSink | None = None,
        model: str | None = None,
        effort: str | None = None,
        retry: bool = False,
        skip_busy: bool = False,
        diagnostic_sink=None,
    ) -> dict:
        app = self._app
        conversation_id = event.window_id
        system = getattr(event.actor, "kind", None) == "system"
        cleaned = _clean_turn_text(
            event.text,
            retry=retry,
            text_max=SYSTEM_TEXT_MAX if system else TEXT_MAX,
        )
        base_cfg = app._current_cfg()
        bots = app.room_bots(conversation_id)
        live_ids = {
            doc.card.bot_id
            for doc in app._bots.list()
            if doc.ok and doc.card is not None
        }
        paused = {
            doc.card.bot_id
            for doc in app._bots.list()
            if doc.ok and doc.card is not None and doc.card.paused
        }
        unavailable = paused | {bot_id for bot_id in bots if bot_id not in live_ids}
        routing_text = cleaned
        if retry:
            room = app._conversations.store.load(conversation_id)
            routing_text = next(
                (
                    message.content or ""
                    for message in reversed(room.messages)
                    if message.role == "user"
                ),
                "",
            )
        responder = app._resolve_responder(bots, routing_text, paused=unavailable)
        if responder in unavailable:

            raise NoLiveResponder(conversation_id)
        card = app._bots.require(responder)
        requested_cfg = (
            overlay_turn_cfg(base_cfg, model, None)
            if model is not None
            else base_cfg
        )
        resolved_cfg = resolve_bot_cfg(
            requested_cfg, card, model_override=model
        )
        resolved_cfg = overlay_turn_cfg(resolved_cfg, None, effort)
        app._gateway.set_soft_timeout_s(
            resolved_cfg.effective_limits().softTimeoutS
        )
        app._memory.tail_kb = resolved_cfg.effective_limits().hotTailKB
        app._memory.timezone_name = base_cfg.timeline.timezone
        app._zone = ZoneInfo(base_cfg.timeline.timezone)
        app._sync_mcp(resolved_cfg)
        with app._claim_lock:
            hub = app._turns.claim(conversation_id)
            job = app._loop_turns.begin(conversation_id)
        if not retry:
            app._agent_status.note_user_message(conversation_id)
        try:

            def sink(turn_event: TurnEvent) -> None:
                hub.publish(turn_event)
                if event_sink is not None:
                    event_sink(turn_event)

            def executor():
                return app._run_bot_turn(
                    conversation_id,
                    cleaned,
                    sink,
                    turn_cfg=requested_cfg,
                    effort=effort,
                    retry=retry,
                    bot_id=responder,
                    model_override=model,
                    diagnostic_sink=diagnostic_sink,
                )
            coro = self._leased(
                responder, window, executor, event=event,
                skip_busy=skip_busy)
            try:
                detail = app._loop_turns.submit(job, coro)
            except TurnCancelled:
                hub.publish(("cancelled", None))
                raise
            except Exception as exc:
                hub.publish(("failed", exc))
                raise
            hub.publish(("completed", detail))
            return detail
        finally:
            with app._claim_lock:
                app._loop_turns.end(conversation_id, job)
                app._turns.release(conversation_id)

    def run_system(
        self,
        bot_id: str,
        text: str,
        *,
        event_id: str | None = None,
    ) -> dict:


        dm = self._app.bot_dm(bot_id)
        conversation = self._app._conversations.store.load(dm["conversation_id"])
        window = self._app._webui.window_for(conversation)
        event = ChannelEvent(
            event_id=event_id or uuid.uuid4().hex,
            channel_id="webui",
            window_id=dm["conversation_id"],
            actor=ActorRef(kind="system", id="scheduler"),
            text=text,
            visibility="system",
            received_at=utc_now_ts(),
        )
        return self.run(event, window=window, skip_busy=True)
