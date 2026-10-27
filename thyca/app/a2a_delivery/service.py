"""A2A delivery service: run the recipient turn, record bus/result rows.

Owns the BotRuntime deliverer: mailbox take, bus-event recording, DM
privacy gates (no room context, no promotion for DMs), recipient turn
execution, and reply/result recording. ChatApp keeps thin wrappers with
identical signatures; the deliverer hook installs the service method.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from thyca.bots.bus import new_span_id
from thyca.bots.workspace import WorkspaceTurnConflict
from thyca.core.protocol import Message
from thyca.sessions import Session, SessionManager

if TYPE_CHECKING:
    from thyca.app.webui_channel import WindowRef
    from thyca.bots.bus import Bus
    from thyca.bots.registry import BotRegistry
    from thyca.bots.runtime import BotRuntime


@dataclass
class A2ADeliveryDeps:
    """Concrete collaborators for :class:`A2ADeliveryService`.

    Values are captured once (ChatApp never rebinds them). Shared
    helpers that stay in ChatApp arrive as callables.
    """

    root: Path
    sessions_store: Any
    window_for: Callable[[Session], WindowRef]
    bots: BotRegistry
    bus: Bus
    runtime: BotRuntime
    injected_connect: Any
    display_meta: Callable[[str], dict]
    room_dm_owner: Callable[[str], str | None]
    room_delta_for: Callable[..., tuple[str, Any]]
    room_bots: Callable[[str], list[str]]
    set_room_bots: Callable[..., list[str]]


class A2ADeliveryService:
    """Bot-to-bot envelope delivery (see module doc)."""

    def __init__(self, deps: A2ADeliveryDeps) -> None:
        self._deps = deps

    def _promote_participant(self, session_id: str, bot_id: str) -> None:
        """Add a delivery recipient to a normal room's participants.

        A plain session becomes a team session when work is delegated
        in it (frontend plan §4.1): without this the room shows the
        recipient's rows while its participant list denies membership.
        DMs never promote (private by definition; set_room_bots also
        guards them) — the caller checks ownership first."""
        try:
            if bot_id not in self._deps.room_bots(session_id):
                self._deps.set_room_bots(session_id, bot_id, "add")
        except Exception:
            pass  # promotion is presentational; delivery must not fail on it

    async def deliver_envelope(self, envelope, ctx) -> str:
        """A2A delivery: run the recipient bot's own turn, record it.

        Installed as ``BotRuntime.deliverer``. The triggering envelope is
        taken off the recipient's mailbox (its payload *is* the turn
        text), the recipient runs in its private (bot x room) session,
        and its reply lands in the room transcript under its own name
        with aggregated cost meta and a span parented to the envelope.
        The reply text is returned for the sender's tool result, so
        multi-hop bot->bot flows compose inside one sender turn.

        DM privacy (user decision 2026-10-13): a fixed DM is strictly
        user + its owner bot — the recipient still runs without
        reading the DM's history (no room context, no promotion) and
        its reply still returns to the sender as a tool result. For
        display, the DM-origin exchange (bus + result rows) is
        recorded into the origin DM transcript so it groups there;
        a result may also land in a DM when the recipient owns that
        DM (scheduled/independent work reporting home)."""
        try:
            async with self._deps.runtime.workspaces.lease(envelope.recipient):
                from thyca.memory.visibility import (
                    MemoryView,
                    bound_memory_view,
                )

                window = None
                rid = envelope.session_id or ctx.room_id
                if rid:
                    try:
                        window = self._deps.window_for(
                            self._deps.sessions_store.load(rid)
                        )
                    except Exception:
                        window = None
                with bound_memory_view(
                    MemoryView(
                        window=window,
                        bot_id=envelope.recipient,
                        thyca_dir=self._deps.root,
                    )
                ):
                    return await self.deliver_envelope_claimed(envelope, ctx)
        except WorkspaceTurnConflict as exc:
            return f"delivery failed: {exc}"

    async def deliver_envelope_claimed(self, envelope, ctx) -> str:
        room_id = envelope.session_id or ctx.room_id
        try:
            card = self._deps.bots.require(envelope.recipient)
        except Exception as exc:
            return f"delivery failed: {exc}"
        if card.paused:
            return f"delivery failed: bot is paused: {envelope.recipient}"
        self._deps.bus.take(envelope)
        span_id = new_span_id()
        room: SessionManager | None = None
        dm_owner = self._deps.room_dm_owner(room_id) if room_id else None
        if room_id:
            try:
                room = SessionManager(
                    limits=ctx.turn_cfg.effective_limits(),
                    timezone_name=ctx.turn_cfg.timeline.timezone,
                    store=self._deps.sessions_store,
                )
                room.load(room_id)
                if dm_owner is None:
                    self._promote_participant(room_id, envelope.recipient)
                self.record_bus_events(room, room_id)
            except Exception:
                room = None
        # Delivery bypasses the mailbox render (Bus.take above), so a
        # truncated envelope must carry the same bot_fetch marker that
        # render provides: without it the recipient cannot know its
        # prompt is only the excerpt or fetch the full body (CBE-3).
        text = envelope.payload
        if envelope.truncated:
            text = (
                f"{text}\n  [truncated: full body via bot_fetch "
                f"span_id={envelope.span_id}]"
            )
        # Phase 2A: the recipient reads the room's public history before
        # handling this envelope. Computed AFTER recording bus events: the
        # fresh envelope row carries bus_event=True so it can never leak
        # into its own delta. Skipped for DM rooms.
        room_context, room_cursor = "", None
        if room is not None and dm_owner is None:
            try:
                room_context, room_cursor = self._deps.room_delta_for(
                    envelope.recipient, room_id, list(room.current.messages))
            except Exception:
                room_context, room_cursor = "", None
        try:
            if room_id:
                try:
                    window = self._deps.window_for(
                        self._deps.sessions_store.load(room_id)
                    )
                except Exception:
                    window = None
                if window is not None:
                    self._deps.runtime.workspaces.store.bind_window(
                        envelope.recipient, window
                    )
            result = await self._deps.runtime.run_bot_turn_detailed(
                scope=replace(
                    ctx.scope, injected_connect=self._deps.injected_connect
                ),
                card=card,
                room_id=room_id or f"a2a:{envelope.sender}",
                text=text,
                turn_cfg=ctx.turn_cfg,
                event_sink=ctx.event_sink,
                model_override=ctx.model_override,
                effort=ctx.effort_override,
                trace_id=envelope.trace_id,
                parent_span_id=envelope.span_id,
                span_id=span_id,
                room_text=room_context,
                room_cursor_after=room_cursor,
                workspace_claimed=True,
            )
        except Exception as exc:
            return f"delivery failed: {type(exc).__name__}: {exc}"
        # Display recording: the recipient's reply lands in the origin
        # transcript (room or DM) under its own name so the A-B pair
        # groups in the DM entry/modal. Scoped to the exchange pair: a
        # DM shows rows only when its owner is the sender or the
        # recipient (a send explicitly targeted into a third bot's DM
        # records nowhere — the reply still returns as a tool result).
        # Delivery privacy is unchanged: the recipient never reads a
        # DM's history (room context and promotion stay gated on
        # non-DM above).
        if room is not None and (dm_owner is None or dm_owner in (envelope.sender, envelope.recipient)):
            room.append(
                Message(
                    role="assistant",
                    content=result.reply,
                    meta=result.room_meta(
                        kind="result",
                        trace_id=envelope.trace_id,
                        parent_span_id=envelope.span_id,
                    ),
                )
            )
        return result.reply

    def record_bus_events(self, room: SessionManager, session_id: str) -> None:
        """Append fresh A2A bus envelopes for *this room* to its transcript.

        One row per envelope under the sender's name; the app layer is the
        only writer (bots never touch the room transcript directly). The
        bus log is drained per room, so the bus's trimmed global log can
        never shift an index past live rows for other rooms.

        DM-origin envelopes are recorded into the origin DM transcript
        for display grouping (one entry per partner + thread modal),
        but only when the DM owner is the envelope's sender or
        recipient: a send explicitly targeted into a third bot's DM
        (via the delegate room arg) records nothing there. The log is
        always drained regardless so the per-room cursor advances.
        Delivery privacy is unchanged: the recipient never reads the
        DM's history, and DM participants are never promoted."""
        events = self._deps.bus.drain_log(session_id)
        dm_owner = self._deps.room_dm_owner(session_id)
        for event in events:
            envelope = event.envelope
            if dm_owner is not None and dm_owner not in (envelope.sender, envelope.recipient):
                continue
            # Payload is already the <=1000-char prompt-facing excerpt;
            # refs ride in meta so the room row stays fetchable (CBE-3).
            # bus_event marks A2A control rows so the public room filter
            # (phase 2A) skips them absolutely; kind stays for FE/trace.
            meta: dict = {
                "bot_id": envelope.sender,
                # Explicit pair side for new rows: older rows resolve the
                # recipient best-effort through the result row's
                # parent_span_id (see thyca/bots/exchanges.py).
                "recipient": envelope.recipient,
                **self._deps.display_meta(envelope.sender),
                "kind": envelope.kind,
                "bus_event": True,
                "trace_id": envelope.trace_id,
                "parent_span_id": envelope.parent_span_id,
                "span_id": envelope.span_id,
            }
            if envelope.refs:
                meta["refs"] = list(envelope.refs)[:8]
            if envelope.truncated:
                meta["truncated"] = True
            room.append(
                Message(
                    role="assistant",
                    content=envelope.payload,
                    meta=meta,
                )
            )
