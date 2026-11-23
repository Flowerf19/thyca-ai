"""A2A delivery service: run the recipient turn, record bus/result rows.

Owns the BotRuntime deliverer: mailbox take, bus-event recording, DM
privacy gates (no room context for DMs), recipient turn
execution, and reply/result recording. A non-member recipient of a
group envelope runs privately (no group history, no group rows) and
is never added to the room. ChatApp keeps thin wrappers with
identical signatures; the deliverer hook installs the service method.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from thyca.bots.bus import new_span_id
from thyca.bots.workspace import WorkspaceTurnConflict
from thyca.conversations.runtime import ConversationManager, ConversationRuntime
from thyca.conversations.runtime.manager import _manager_runtime_root
from thyca.conversations.store import write_adapter as _write_adapter
from thyca.conversations.store.runtime import (
    RuntimeWriteIntent,
    resolve_ref,
)
from thyca.core.protocol import Message
from thyca.memories.visibility import (
    MemoryView,
    bound_memory_view,
)
from thyca.memories.visibility import current_memory_view as _cmv
from thyca.memories.visibility import current_memory_view as _cmv2

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
    conversations_store: Any
    window_for: Callable[[ConversationRuntime], WindowRef]
    bots: BotRegistry
    bus: Bus
    runtime: BotRuntime
    injected_connect: Any
    display_meta: Callable[[str], dict]
    room_dm_owner: Callable[[str], str | None]
    room_delta_for: Callable[..., tuple[str, Any]]
    room_bots: Callable[[str], list[str]]


class A2ADeliveryService:
    """Bot-to-bot envelope delivery (see module doc)."""

    def __init__(self, deps: A2ADeliveryDeps) -> None:
        self._deps = deps

    async def deliver_envelope(self, envelope, ctx) -> str:
        """A2A delivery: run the recipient bot's own turn, record it.

        Installed as ``BotRuntime.deliverer``. The triggering envelope is
        taken off the recipient's mailbox (its payload *is* the turn
        text), the recipient runs in its private (bot x room) conversation,
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
            _caller0 = _cmv()
        except Exception:
            _caller0 = None
        inherited: frozenset[str] = (
            _caller0.denied_origins
            if _caller0 is not None
            and isinstance(getattr(_caller0, "denied_origins", None), frozenset)
            else frozenset()
        )
        try:
            _flag_room = envelope.conversation_id or ctx.room_id
            _flag_dm = self._deps.room_dm_owner(_flag_room) if _flag_room else None
        except Exception:
            _flag_room, _flag_dm = None, None
        if getattr(envelope, "private_chain", False) and _flag_room and _flag_dm is None:
            inherited = inherited | frozenset({_flag_room})
        _rid0 = envelope.conversation_id or ctx.room_id
        try:
            _dm0 = self._deps.room_dm_owner(_rid0) if _rid0 else None
        except Exception:
            _dm0 = None
        chain_private = bool(inherited) and bool(_rid0) and _dm0 is None
        try:
            async with self._deps.runtime.workspaces.lease(envelope.recipient):

                window = None
                rid = envelope.conversation_id or ctx.room_id
                if rid:
                    try:
                        window = self._deps.window_for(
                            self._deps.conversations_store.load(rid)
                        )
                    except Exception:
                        window = None
                with bound_memory_view(
                    MemoryView(
                        window=window,
                        bot_id=envelope.recipient,
                        thyca_dir=self._deps.root,
                        denied_origins=inherited,
                    )
                ):
                    return await self.deliver_envelope_claimed(envelope, ctx)
        except WorkspaceTurnConflict as exc:
            if chain_private:
                try:
                    self._deps.bus.take(envelope)
                except Exception:
                    pass
                try:
                    self._deps.bus.expunge_envelope(
                        getattr(envelope, "span_id", None))
                except Exception:
                    pass
            return f"delivery failed: {exc}"

    def _append_group_result(self, room, room_id: str, envelope, result) -> bool:
        """Atomically append the result row iff the recipient participates."""
        try:
            runtime_root = _manager_runtime_root(room)
            if runtime_root is None:
                return False
            ref = resolve_ref(runtime_root, room_id)
            message = Message(
                role="assistant",
                content=result.reply,
                meta=result.room_meta(
                    kind="result",
                    trace_id=envelope.trace_id,
                    parent_span_id=envelope.span_id,
                ),
            )
            _write_adapter.append_message(
                runtime_root, ref, envelope.recipient, message,
                intent=RuntimeWriteIntent("runtime-append"),
            )
        except Exception:
            return False
        try:
            room.load(room_id)
        except Exception:
            pass
        return True

    async def deliver_envelope_claimed(self, envelope, ctx) -> str:
        room_id = envelope.conversation_id or ctx.room_id
        try:
            card = self._deps.bots.require(envelope.recipient)
        except Exception as exc:
            return f"delivery failed: {exc}"
        if card.paused:
            return f"delivery failed: bot is paused: {envelope.recipient}"
        self._deps.bus.take(envelope)
        span_id = new_span_id()
        room: ConversationManager | None = None
        dm_owner = self._deps.room_dm_owner(room_id) if room_id else None
        try:
            _caller2 = _cmv2()
        except Exception:
            _caller2 = None
        _chained = bool(
            _caller2 is not None
            and isinstance(getattr(_caller2, "denied_origins", None), frozenset)
            and len(_caller2.denied_origins) > 0
            and room_id
            and dm_owner is None
        )
        if room_id and dm_owner is None and getattr(
                envelope, "private_chain", False):
            _chained = True
        is_member = True
        if room_id and dm_owner is None:
            try:
                is_member = envelope.recipient in self._deps.room_bots(room_id)
            except Exception:
                is_member = False
            if _chained:
                is_member = False
        is_private = bool(room_id and dm_owner is None and not is_member)
        if is_private and room_id:
            try:
                self._deps.bus.expunge_envelope(
                    getattr(envelope, "span_id", None))
            except Exception:
                pass
        record_ok = True
        if room_id:
            try:
                room = ConversationManager(
                    limits=ctx.turn_cfg.effective_limits(),
                    timezone_name=ctx.turn_cfg.timeline.timezone,
                    store=self._deps.conversations_store,
                )
                room.load(room_id)
                record_ok = self.record_bus_events(
                    room, room_id,
                    current_envelope=envelope,
                    current_is_member=is_member,
                )
            except Exception:
                room = None
                record_ok = False
        text = envelope.payload
        if envelope.truncated:
            text = (
                f"{text}\n  [truncated: full body via bot_fetch "
                f"span_id={envelope.span_id}]"
            )
        room_context, room_cursor = "", None
        if room is not None and dm_owner is None and is_member:
            try:
                room_context, room_cursor = self._deps.room_delta_for(
                    envelope.recipient, room_id, list(room.current.messages))
            except Exception:
                room_context, room_cursor = "", None
        try:
            if room_id and not is_private:
                try:
                    window = self._deps.window_for(
                        self._deps.conversations_store.load(room_id)
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
                private_invocation=is_private,
            )
        except Exception as exc:
            return f"delivery failed: {type(exc).__name__}: {exc}"
        _fresh_member = False
        if room is not None and dm_owner is None:
            try:
                _fresh_member = envelope.recipient in self._deps.room_bots(room_id)
            except Exception:
                _fresh_member = False
        if room is not None and dm_owner is None and (
            is_member and record_ok and _fresh_member
        ):
            self._append_group_result(room, room_id, envelope, result)
        elif room is not None and dm_owner is not None and (
            dm_owner in (envelope.sender, envelope.recipient) and record_ok
        ):
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

    def _append_group_display(self, room: ConversationManager, room_id: str,
                              envelope, content: str, meta: dict,
                              actor: str | None = None) -> bool:
        """Append one group request row iff sender and recipient participate."""
        try:
            runtime_root = _manager_runtime_root(room)
            if runtime_root is None:
                return False
            ref = resolve_ref(runtime_root, room_id)
            row = Message(role="assistant", content=content, meta=dict(meta))
            sender = getattr(envelope, "sender", None) or actor
            recipient = getattr(envelope, "recipient", None)
            if not isinstance(sender, str) or not isinstance(recipient, str):
                return False
            _write_adapter.append_message(
                runtime_root, ref, sender, row,
                intent=RuntimeWriteIntent("runtime-append"),
                require_participant=recipient,
            )
        except Exception:
            return False
        try:
            room.current.messages.append(row)
        except Exception:
            try:
                room.load(room_id)
            except Exception:
                return False
        return True

    @staticmethod
    def _envelope_matches(left, right) -> bool:
        for attr in ("sender", "recipient", "kind", "trace_id",
                     "parent_span_id", "payload", "truncated",
                     "private_chain"):
            if getattr(left, attr, None) != getattr(right, attr, None):
                return False
        left_refs = list(getattr(left, "refs", None) or [])[:8]
        right_refs = list(getattr(right, "refs", None) or [])[:8]
        if len(left_refs) != len(right_refs):
            return False
        for left_ref, right_ref in zip(left_refs, right_refs, strict=True):
            if not isinstance(left_ref, str) or not isinstance(right_ref, str):
                return False
            if left_ref != right_ref:
                return False
        return True

    @staticmethod
    def _bus_row_matches(message, envelope) -> bool:
        if getattr(message, "role", None) != "assistant":
            return False
        meta = getattr(message, "meta", None)
        if not isinstance(meta, dict):
            return False
        if meta.get("bus_event") is not True:
            return False
        if meta.get("span_id") != getattr(envelope, "span_id", None):
            return False
        if meta.get("bot_id") != getattr(envelope, "sender", None):
            return False
        if meta.get("recipient") != getattr(envelope, "recipient", None):
            return False
        if meta.get("trace_id") != getattr(envelope, "trace_id", None):
            return False
        if meta.get("kind") != getattr(envelope, "kind", None):
            return False
        if meta.get("parent_span_id") != getattr(envelope, "parent_span_id", None):
            return False
        row_truncated = meta.get("truncated", False)
        env_truncated = getattr(envelope, "truncated", False)
        if not isinstance(row_truncated, bool) or not isinstance(env_truncated, bool):
            return False
        if row_truncated != env_truncated:
            return False
        if (message.content or "") != (getattr(envelope, "payload", None) or ""):
            return False
        row_refs = meta.get("refs", [])
        env_refs = list(getattr(envelope, "refs", None) or [])[:8]
        if not isinstance(row_refs, list) or len(row_refs) != len(env_refs):
            return False
        for row_ref, env_ref in zip(row_refs, env_refs, strict=True):
            if not isinstance(row_ref, str) or not isinstance(env_ref, str):
                return False
            if row_ref != env_ref:
                return False
        return True

    def _bus_row_present(self, room: ConversationManager, envelope) -> bool:
        if getattr(envelope, "private_chain", False):
            return False
        return any(self._bus_row_matches(message, envelope) for message in room.current.messages)

    def record_bus_events(
        self,
        room: ConversationManager,
        conversation_id: str,
        *,
        current_envelope=None,
        current_is_member: bool | None = None,
    ) -> bool:
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
        Group envelopes record only when the recipient is a member;
        a non-member exchange stays private to the sender's tool result.
        When pinned, this returns the current envelope's publication
        eligibility: dispatch and roster must both allow and its request
        row must be verifiably present (an unrelated drained envelope
        never authorizes it). Unpinned calls return roster health only.
        Delivery privacy is
        unchanged: no group history is read here, and rooms never gain
        members through delivery."""
        pinned = current_envelope is not None
        pinned_span = getattr(current_envelope, "span_id", None) if pinned else None
        events = self._deps.bus.drain_log(conversation_id)
        dm_owner = self._deps.room_dm_owner(conversation_id)
        participants: list[str] | None = None
        if dm_owner is None:
            try:
                participants = list(self._deps.room_bots(conversation_id))
            except Exception:
                return False
        current_allowed: bool | None = None
        for event in events:
            envelope = event.envelope
            if dm_owner is not None:
                if dm_owner not in (envelope.sender, envelope.recipient):
                    if pinned and getattr(envelope, "span_id", None) == pinned_span:
                        current_allowed = False
                    continue
                if pinned and getattr(envelope, "span_id", None) == pinned_span:
                    if not self._envelope_matches(envelope, current_envelope):
                        current_allowed = False
                        continue
                    current_allowed = True
            else:
                is_current = (
                    pinned
                    and getattr(envelope, "span_id", None) is not None
                    and getattr(envelope, "span_id", None) == pinned_span
                )
                if getattr(envelope, "private_chain", False):
                    if is_current:
                        current_allowed = False
                    continue
                if is_current and not self._envelope_matches(
                        envelope, current_envelope):
                    current_allowed = False
                    continue
                if is_current and current_is_member is not True:
                    continue
                if envelope.recipient not in (participants or []) or envelope.sender not in (participants or []):
                    if is_current:
                        current_allowed = False
                    continue
                if is_current:
                    current_allowed = True
            meta: dict = {
                "bot_id": envelope.sender,
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
            if dm_owner is not None:
                room.append(
                    Message(
                        role="assistant",
                        content=envelope.payload,
                        meta=meta,
                    )
                )
            else:
                if not self._append_group_display(
                    room, conversation_id, envelope,
                    envelope.payload, meta,
                    actor=envelope.sender,
                ):
                    if is_current:
                        current_allowed = False
                    continue
                if is_current:
                    current_allowed = True
        if not pinned:
            return True
        if getattr(current_envelope, "private_chain", False):
            return False
        if current_allowed is True:
            return self._bus_row_present(room, current_envelope)
        if pinned and self._bus_row_present(room, current_envelope):
            if dm_owner is not None:
                sender = getattr(current_envelope, "sender", None)
                recipient = getattr(current_envelope, "recipient", None)
                if dm_owner in (sender, recipient):
                    return True
            elif current_is_member is True:
                try:
                    still_recipient = getattr(
                        current_envelope, "recipient", None) in (participants or [])
                    still_sender = getattr(
                        current_envelope, "sender", None) in (participants or [])
                except Exception:
                    still_recipient = False
                    still_sender = False
                if still_recipient and still_sender:
                    return True
        return False
