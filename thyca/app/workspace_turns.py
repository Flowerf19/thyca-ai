"""WorkspaceTurnService (WS-2): channel-event turn orchestration.

Owns what ``ChatApp.turn`` used to do inline: text/config validation,
responder resolution, the window claim, sink wiring, job submit, and the
terminal-event publish/release. The executor coroutine runs inside the
responding bot's workspace lease — one active turn per bot across all
windows — while window claims keep their existing per-session
semantics (follow/cancel/409 unchanged).
"""
from __future__ import annotations

from zoneinfo import ZoneInfo

from thyca.agent.events import EventSink, TurnEvent
from thyca.app.loop_turns import TurnCancelled
from thyca.app.turn_options import (
    SYSTEM_TEXT_MAX,
    TEXT_MAX,
    _clean_turn_text,
    overlay_turn_cfg,
    resolve_bot_cfg,
)
from thyca.bots.workspace import WorkspaceRegistry
from thyca.core.channel import ChannelEvent


class WorkspaceTurnService:
    """Runs one claimed window turn under the responder's bot lease."""

    def __init__(self, app, registry: WorkspaceRegistry) -> None:
        self._app = app
        self._workspaces = registry

    async def _leased(self, bot_id: str, window, factory, event=None,
                      skip_busy: bool = False):
        from thyca.memory.visibility import MemoryView, bound_memory_view

        if skip_busy and self._workspaces.busy(bot_id):
            from thyca.sessions import SessionBusy
            raise SessionBusy(f"{bot_id} turn lease held")
        async with self._workspaces.lease(bot_id):
            # Deletion may have removed this card while the turn waited for
            # its lease. Validate before binding, which creates workspace state.
            self._app._bots.require(bot_id)
            # Re-derive the window AFTER the queue wait: the session may
            # have changed while this turn was queued, so the view and the
            # registry binding must reflect the fresh state.
            if window is not None:
                window = self._app._webui.window_for(
                    self._app._sessions.store.load(window.window_id)
                )
                self._workspaces.store.bind_window(bot_id, window)
            from thyca.app.turn_event import (
                bind_channel_event,
                reset_channel_event,
            )

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
        session_id = event.window_id
        # System actors (scheduler, curation) inject data into the prompt,
        # so they use the system cap instead of the user-message cap.
        system = getattr(event.actor, "kind", None) == "system"
        cleaned = _clean_turn_text(
            event.text,
            retry=retry,
            text_max=SYSTEM_TEXT_MAX if system else TEXT_MAX,
        )
        base_cfg = app._current_cfg()
        # Resolve the responder before model-dependent settings and provider
        # synchronization are applied.
        bots = app.room_bots(session_id)
        paused = {
            doc.card.bot_id
            for doc in app._bots.list()
            if doc.ok and doc.card is not None and doc.card.paused
        }
        routing_text = cleaned
        if retry:
            room = app._sessions.store.load(session_id)
            routing_text = next(
                (
                    message.content or ""
                    for message in reversed(room.messages)
                    if message.role == "user"
                ),
                "",
            )
        responder = app._resolve_responder(bots, routing_text, paused=paused)
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
        # Shared mutable services retain global settings; per-model hot limits
        # are passed with each read/refresh below the service boundary.
        app._gateway.set_soft_timeout_s(
            resolved_cfg.effective_limits().softTimeoutS
        )
        app._memory.tail_kb = base_cfg.limits.hotTailKB
        app._memory.timezone_name = base_cfg.timeline.timezone
        app._zone = ZoneInfo(base_cfg.timeline.timezone)
        app._sync_mcp(resolved_cfg)
        with app._claim_lock:
            hub = app._turns.claim(session_id)
            job = app._loop_turns.begin(session_id)
        if not retry:
            # A fresh user message answers any waiting/error card in
            # this room; a retry replays the same message and must not.
            # Only after the claim: a refused (busy) turn posted nothing.
            app._agent_status.note_user_message(session_id)
        try:

            def sink(turn_event: TurnEvent) -> None:
                hub.publish(turn_event)
                if event_sink is not None:
                    event_sink(turn_event)

            executor = lambda: app._run_bot_turn(
                session_id,
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
                app._loop_turns.end(session_id, job)
                app._turns.release(session_id)

    def run_system(
        self,
        bot_id: str,
        text: str,
        *,
        event_id: str | None = None,
    ) -> dict:
        import uuid

        from thyca.core.channel import ActorRef
        from thyca.core.protocol import utc_now_ts

        dm = self._app.bot_dm(bot_id)
        session = self._app._sessions.store.load(dm["id"])
        window = self._app._webui.window_for(session)
        event = ChannelEvent(
            event_id=event_id or uuid.uuid4().hex,
            channel_id="webui",
            window_id=dm["id"],
            actor=ActorRef(kind="system", id="scheduler"),
            text=text,
            visibility="system",
            received_at=utc_now_ts(),
        )
        return self.run(event, window=window, skip_busy=True)
