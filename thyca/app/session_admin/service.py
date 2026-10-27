"""Session-admin service: DMs, rooms, titles, compaction.

Owns room/session lifecycle (create/find-or-create DM, group sessions,
renames, deletes, per-bot compaction) plus participant and exchange
read helpers. ChatApp keeps thin wrappers with identical signatures so
the serve layer, scheduler, and internal callers are untouched.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from zoneinfo import ZoneInfo

from thyca.agent.compacting import compact_with_llm
from thyca.app.summarizing import LlmSummarizer
from thyca.app.turn_options import (
    overlay_turn_cfg,
    resolve_bot_cfg,
    resolve_bot_provider,
)
from thyca.bots.context import current_bot_turn_context
from thyca.bots.registry import RegistryError
from thyca.llm.llm_factory import ConnectFactory
from thyca.sessions import Session, SessionManager, calibration_factor

if TYPE_CHECKING:
    from thyca.app.webui_channel import WindowRef
    from thyca.bots.registry import BotRegistry
    from thyca.bots.runtime import BotRuntime
    from thyca.config import Config
    from thyca.memory.active import ActiveMemory
    from thyca.serve.turn_state import TurnState


@dataclass
class SessionAdminDeps:
    """Concrete collaborators for :class:`SessionAdminService`.

    Values are captured once (ChatApp never rebinds them). Mutable
    runtime configuration flows through callables: ``set_zone`` applies
    the compaction timezone, ``sync_mcp`` hot-reloads providers,
    ``detail_fn``/``flush_memory``/``overhead_fn`` reach sibling
    services through ChatApp's thin wrappers.
    """

    root: Path
    sessions: SessionManager
    bots: BotRegistry
    runtime: BotRuntime
    turns: TurnState
    memory: ActiveMemory
    agent_status: Any
    claim_lock: Any
    injected_connect: Any
    window_for: Callable[[Session], WindowRef]
    current_cfg: Callable[[], Config]
    set_zone: Callable[[ZoneInfo], None]
    sync_mcp: Callable[[Config], None]
    detail_fn: Callable[..., dict]
    flush_memory: Callable[..., Any]
    overhead_fn: Callable[..., tuple[int, int]]
    submit: Callable[[Any], Any]
    dm_lock: Any


class SessionAdminService:
    """Room/session lifecycle (see module doc)."""

    def __init__(self, deps: SessionAdminDeps) -> None:
        self._deps = deps

    def create(self, bots: list[str] | None = None) -> dict:
        # Do not wait on any turn: create/list stay responsive while an LLM
        # call is in flight. Blank sessions belonging to a running turn must
        # survive the prune — its transcript is still empty on disk. Blank
        # bot DMs survive it too (discard_empty keeps ``dm_bot`` sessions:
        # a lazily created DM is blank until its first turn).
        # Phase 2B user-facing model: exactly dm or group. Only None or
        # an empty list means Thyca's DM; anything else goes to validation
        # as-is (a stray string must hit the list check, not split chars).
        if bots is None or bots == []:
            bots = ["thyca"]
        participants = self._clean_participants(bots)
        if len(participants) == 1:
            return self.bot_dm(participants[0])
        return self._create_group_session(participants)

    def _create_group_session(self, participants: list[str]) -> dict:
        """Fresh multi-bot group session (phase 2B; callers validated)."""
        self._deps.sessions.discard_empty(keep=set(self._deps.turns.snapshot()))
        session = self._deps.sessions.create()
        self._deps.sessions.store.append_bots(session.path, participants)
        session.bots = list(participants)
        return self._deps.detail_fn(session, self._deps.current_cfg())

    def get_payload(self, session_id: str) -> dict:
        session = self._deps.sessions.store.load(session_id)
        return self._deps.detail_fn(session, self._deps.current_cfg())

    def rename_session(self, session_id: str, title: str) -> str:
        """Set a title the user typed. Returns the stored (cleaned) title."""
        if not isinstance(title, str):
            raise ValueError("title must be a string")
        return self._deps.sessions.rename(session_id, title)

    def delete_session(self, session_id: str) -> None:
        """Drop the notebook. Memory leaves written from it are left alone."""
        self._deps.turns.delete_unclaimed(self._deps.sessions, session_id)
        self._deps.agent_status.forget_room(session_id)

    def compact_session(
        self,
        session_id: str,
        model: str | None = None,
        instructions: str | None = None,
    ) -> dict:
        """Compact each participating bot's existing canonical context.

        The room remains a read model. Existing bindings are reloaded and
        compacted sequentially under each bot's workspace lease; absent
        contexts are reported without creating bindings.
        """
        base_cfg = self._deps.current_cfg()
        if model is not None:
            base_cfg = overlay_turn_cfg(base_cfg, model, None)
        self._deps.memory.tail_kb = base_cfg.limits.hotTailKB
        self._deps.memory.timezone_name = base_cfg.timeline.timezone
        self._deps.set_zone(ZoneInfo(base_cfg.timeline.timezone))
        self._deps.sync_mcp(base_cfg)
        with self._deps.claim_lock:
            room = self._deps.sessions.store.load(session_id)
            self._deps.turns.begin_compact(session_id)
        try:
            per_bot: dict[str, dict] = {}
            for bot_id in room.bots or ["thyca"]:
                try:
                    binding = self._deps.runtime.workspaces.store.get_binding(
                        bot_id, session_id
                    )
                except Exception as exc:
                    per_bot[bot_id] = {
                        "compacted": False,
                        "error": type(exc).__name__,
                    }
                    continue
                if binding is None or binding.internal_session_id is None:
                    per_bot[bot_id] = {
                        "compacted": False,
                        "skipped": "no_existing_context",
                    }
                    continue

                card = self._deps.bots.require(bot_id)
                bot_cfg = resolve_bot_cfg(
                    base_cfg, card, model_override=model
                )

                async def compact_one(_bot_id=bot_id, _cfg=bot_cfg):
                    async with self._deps.runtime.workspaces.lease(_bot_id):
                        fresh = self._deps.runtime.workspaces.store.get_binding(
                            _bot_id, session_id
                        )
                        if fresh is None or fresh.internal_session_id is None:
                            return {
                                "compacted": False,
                                "skipped": "no_existing_context",
                            }
                        internal_id = fresh.internal_session_id
                        window = fresh.window or self._deps.window_for(room)
                        sessions = SessionManager(
                            limits=_cfg.effective_limits(),
                            timezone_name=_cfg.timeline.timezone,
                            store=self._deps.runtime._canonical_store(
                                _bot_id, window, internal_id
                            ),
                        )
                        sessions.load(internal_id)
                        flush_error = None
                        try:
                            await self._deps.flush_memory(
                                _bot_id,
                                list(sessions.current.messages),
                                _cfg,
                                room_id=session_id,
                            )
                        except Exception as exc:
                            flush_error = type(exc).__name__
                        sessions.load(internal_id)
                        result = await self._compact_async(
                            sessions,
                            session_id,
                            _cfg,
                            instructions,
                            bot_id=_bot_id,
                        )
                        if result.get("compacted"):
                            self._deps.runtime._reinject_pending.add(
                                (_bot_id, session_id)
                            )
                        if flush_error is not None:
                            result["flush_error"] = flush_error
                        return result

                try:
                    per_bot[bot_id] = self._deps.submit(compact_one())
                except Exception as exc:
                    per_bot[bot_id] = {
                        "compacted": False,
                        "error": type(exc).__name__,
                    }
            detail = self._deps.detail_fn(room, base_cfg, running=False)
        finally:
            self._deps.turns.end_compact(session_id)
        return {
            **detail,
            "compaction": {
                "compacted": any(
                    result.get("compacted") is True
                    for result in per_bot.values()
                ),
                "per_bot": per_bot,
            },
        }

    async def _compact_async(
        self,
        sessions: SessionManager,
        room_id: str,
        turn_cfg,
        instructions: str | None,
        *,
        bot_id: str,
    ) -> dict:
        # Compaction runs as this bot: require its valid card (model +
        # thinking) instead of silently falling back to global config.
        card = self._deps.bots.require(bot_id)
        provider = resolve_bot_provider(turn_cfg, card)
        connect = self._deps.injected_connect or ConnectFactory.create(
            provider.api, provider
        )
        owns = self._deps.injected_connect is None
        try:
            hot_tokens, tools_tokens = self._deps.overhead_fn(
                room_id, turn_cfg=turn_cfg, bot_id=bot_id
            )
            return await compact_with_llm(
                LlmSummarizer(connect),
                sessions,
                model=turn_cfg.provider.model,
                pricing=turn_cfg.effective_pricing() or None,
                hot_tokens=hot_tokens,
                tools_tokens=tools_tokens,
                force=True,
                instructions=instructions,
                calibration=calibration_factor(
                    list(sessions.current.messages),
                    model=turn_cfg.provider.model,
                ),
            )
        finally:
            if owns:
                close = getattr(connect, "aclose", None)
                if close is not None:
                    await close()

    def room_bots(self, session_id: str) -> list[str]:
        """Participant list of a room; legacy rooms read as Thyca alone."""
        session = self._deps.sessions.store.load(session_id)
        return list(session.bots) if session.bots else ["thyca"]

    def set_room_bots(self, session_id: str, bot_id: str, action: str) -> list[str]:
        """Add/remove one bot from a room. Returns the new participant list.

        DM integrity (chosen semantics, recorded in the lane plan): a
        session marked as a bot's fixed DM protects its participants —
        the DM's own bot cannot be removed from it, and no other bot
        can be added (that would silently turn the private DM into a
        shared room while the marker still claims it is the DM). There
        is no marker-clearing/convert operation in v1, so both raise;
        removing some *other* participant from a marked session stays
        allowed, as it can only move the room back toward DM shape.
        """
        if action not in ("add", "remove"):
            raise ValueError("action must be add|remove")
        card = self._deps.bots.require(bot_id)
        # Paused bots cannot join, but must always be removable.
        if action == "add" and card.paused:
            raise RegistryError(f"bot is paused: {bot_id}")
        session = self._deps.sessions.store.load(session_id)
        if session.dm_bot is not None:
            if action == "add" and bot_id != session.dm_bot:
                raise RegistryError(
                    f"cannot add {bot_id} to {session.dm_bot}'s DM: "
                    "a DM stays private to its bot"
                )
            if action == "remove" and bot_id == session.dm_bot:
                raise RegistryError(
                    f"cannot remove {bot_id} from its own DM"
                )
        current = list(session.bots) if session.bots else ["thyca"]
        if action == "add":
            if bot_id not in current:
                current.append(bot_id)
        else:
            if bot_id not in current:
                raise RegistryError(f"bot not in room: {bot_id}")
            current = [bot for bot in current if bot != bot_id]
            if not current:
                raise RegistryError("room must keep at least one bot")
        self._deps.sessions.store.append_bots(session.path, current)
        self._deps.sessions.set_bots(session_id, current)
        return current

    def _clean_participants(self, bots: list[str]) -> list[str]:
        if not isinstance(bots, list) or not bots:
            raise ValueError("bots must be a non-empty list of bot ids")
        cleaned: list[str] = []
        for bot_id in bots:
            if not isinstance(bot_id, str) or not bot_id:
                raise ValueError("bots must be a non-empty list of bot ids")
            card = self._deps.bots.require(bot_id)
            if card.paused:
                raise RegistryError(f"bot is paused: {bot_id}")
            if bot_id not in cleaned:
                cleaned.append(bot_id)
        return cleaned

    def bot_dm(self, bot_id: str) -> dict:
        """The bot's fixed DM session (CBE-5): find-or-create, marked.

        Exactly one DM per bot: only a room session already carrying the
        ``dm`` marker for this bot is adopted — an unmarked solo room is
        an ordinary group under the phase-2B model, never silently
        converted. Otherwise a fresh DM is created and marked. Returns
        the session detail payload (``dm`` = bot id, ``bots`` = [bot_id],
        ``kind`` = "dm"). Unknown bots raise RegistryError.

        The whole scan -> create sequence runs under
        ``_dm_lock``: without it, two concurrent first lookups could
        both miss the scan and each create a session, leaving the bot
        with two competing "fixed" DMs."""
        card = self._deps.bots.require(bot_id)
        if card.paused:
            raise RegistryError(f"bot is paused: {bot_id}")
        # Shared helper owns find-or-create; this lock keeps the
        # concurrent one-DM guarantee (the helper itself is lock-free).
        # Prune only on the create path: an existing DM returns at once
        # (rework 2B — pruning on every lookup could eat a freshly made
        # blank group when opening another bot's DM).
        with self._deps.dm_lock:
            for session in self._deps.sessions.list_sessions():
                if session.dm_bot == bot_id:
                    return self.get_payload(session.id)
            keep = set(self._deps.turns.snapshot())
            ctx = current_bot_turn_context()
            if ctx is not None and ctx.room_id:
                keep.add(ctx.room_id)
            from thyca.bots.private_followups import PrivateFollowupStore

            pf_store = PrivateFollowupStore(self._deps.root)
            for other in self._deps.bots.list():
                card = getattr(other, "card", other)
                other_id = getattr(card, "bot_id", None)
                if not isinstance(other_id, str) or not other_id:
                    continue
                pending = pf_store.active(other_id)
                if pending is not None:
                    keep.add(pending.origin_window)
                    keep.add(pending.dm_window)
            self._deps.sessions.discard_empty(keep=keep)
            session = self._deps.sessions.ensure_dm(bot_id)
            return self.get_payload(session.id)
