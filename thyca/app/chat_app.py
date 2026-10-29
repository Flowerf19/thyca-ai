"""Loopback chat facade for room sessions and workspace turns."""
from __future__ import annotations

import asyncio
import re
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TextIO
from zoneinfo import ZoneInfo

from thyca.agent.act import Act
from thyca.agent.events import EventSink
from thyca.agent.think import LLMPort
from thyca.bots.bus import Bus, new_span_id, new_trace_id
from thyca.bots.policy import BotPolicy
from thyca.bots.registry import BotRegistry
from thyca.bots.runtime import BotRuntime
from thyca.bots.status import AgentStatusTracker
from thyca.config import Config, ConfigError, load
from thyca.core.protocol import Message
from thyca.llm.llm_base import LLMError
from thyca.llm.llm_factory import ConnectFactory
from thyca.memory.active import ActiveMemory, ActiveSnapshot
from thyca.sessions.wire import session_summary
from thyca.sessions import Session, SessionBusy, SessionManager
from thyca.sessions.store import SessionStore
from thyca.sessions.title import is_blank
from thyca.tools.gateway.background import BackgroundProcs
from thyca.tools.mcp import MCPManager, ProcessFactory
from thyca.tools.task_store import TaskStore
from thyca.serve.turn_state import TurnHub, TurnState

from thyca.app.loop_turns import _CANCEL_WAIT_S, _LoopTurns, TurnCancelled
from thyca.app.naming import _name_if_needed, session_title
from thyca.bots.context import BotTurnScope
from thyca.app.toolchain import (
    build_tool_gateway,
    build_tool_registry,
    install_mcp_specs,
    report_spawn_diags,
)
from thyca.app.turn_options import (
    InvalidTurnOption,
    InvalidTurnText,
    _clean_turn_text,
    resolve_bot_cfg,
    resolve_bot_provider,
)
from thyca.app.webui_channel import WebUIChannelAdapter
from thyca.app.workspace_turns import WorkspaceTurnService
from thyca.bots.channels import ChannelActionService, ChannelRegistry
from thyca.bots.private_followups import PrivateFollowupError

__all__ = [
    "ChatApp",
    "InvalidTurnOption",
    "InvalidTurnText",
    "SessionIdle",
    "TurnCancelled",
    "TurnInFlight",
    "session_title",
]


class SessionIdle(Exception):
    """Cancel arrived while this session had no turn in flight."""


class TurnInFlight(Exception):
    """Cancel wait expired while the turn was still running."""


class ChatApp:
    def __init__(
        self,
        root: Path,
        cfg: Config,
        connect: LLMPort | None = None,
        mcp_factory: ProcessFactory | None = None,
        stderr: TextIO | None = None,
    ) -> None:
        self._root = root
        self._stderr = stderr if stderr is not None else sys.stderr
        self._config_file = root / "config.json"
        # Seed before re-read: _current_cfg falls back to _cfg on ConfigError.
        self._cfg = cfg
        if self._config_file.exists():
            self._cfg = self._current_cfg()
        self._injected_connect = connect
        self._connect = connect
        self._sessions = SessionManager(
            root / "sessions",
            limits=self._cfg.effective_limits(),
            timezone_name=cfg.timeline.timezone,
        )
        self._memory = ActiveMemory(
            root,
            tail_kb=self._cfg.effective_limits().hotTailKB,
            timezone_name=cfg.timeline.timezone,
        )
        self.skills_root = self._memory.skills_store.root
        self._zone = ZoneInfo(cfg.timeline.timezone)
        self._state = self._memory.open_session(datetime.now(self._zone))
        self._tasks = TaskStore()
        self._background = BackgroundProcs()
        self._registry = build_tool_registry(root, cfg, self._background)
        self._gateway = build_tool_gateway(
            self._registry,
            self._tasks,
            self._background,
            soft_timeout_s=cfg.effective_limits().softTimeoutS,
            policy=BotPolicy(),
        )
        # Team (BE-1..BE-3): bot registry + A2A bus + per-card runtime.
        # ensure_thyca seeds the system card on first run; BotPolicy above
        # denies hidden_tools at submit time for bot turns (allow-all else).
        self._bots = BotRegistry(root)
        self._bots.ensure_thyca()
        self._bus = Bus()
        for _doc in self._bots.list():
            if _doc.ok and _doc.card is not None:
                self._bus.register(_doc.card.bot_id)
        self._agent_status = AgentStatusTracker()
        self._runtime = BotRuntime(
            root, self._bots, self._bus, status_tracker=self._agent_status
        )
        # A2A delivery: send tools run the recipient's turn through the
        # delivery service (installed after construction below).
        # CBE-4: flush-before-compact + post-compact recall hooks.
        self._runtime.memory_flush = self._flush_bot_memory
        self._runtime.memory_searcher = self._search_bot_memory
        # Phase 1C: open memory-task catalog rows per bot.
        self._runtime.memory_task_catalog = self._memory_task_rows
        self._webui = WebUIChannelAdapter()
        self._turn_service = WorkspaceTurnService(
            self, self._runtime.workspaces
        )
        # Thyca-only admin tools (BE-5): registered once on the shared
        # registry; the runtime hides every bot_* name from child bots and
        # each handler re-checks the caller, so a leak still cannot be used.
        from thyca.bots.admin_tools import AdminDeps, register_admin_tools

        from thyca.app.bot_posting import BotPostingDeps, BotPostingService

        self._posting = BotPostingService(BotPostingDeps(
            root=self._root,
            sessions=self._sessions,
            bots=self._bots,
            current_cfg=self._current_cfg,
            bot_dm=self.bot_dm,
            agent_status=self._agent_status,
        ))
        _deps = AdminDeps(
            self._bots, self._runtime, self.job_facade,
            dm_post=self._posting.post_bot_dm_message,
            card_post=self._posting.post_bot_card,
            room_post=self._posting.post_bot_room_message,
            dm_resolve=lambda bid: self.bot_dm(bid)["id"],
        )
        _deps.dm_card_post = self._posting.post_bot_dm_card
        register_admin_tools(self._registry, _deps)
        from thyca.bots.channel_tools import register_channel_tools

        register_channel_tools(self._registry, _deps)
        from thyca.app.webui_channel import WebUIOutboundAdapter

        self._channel_registry = ChannelRegistry()
        self._channel_registry.register(WebUIOutboundAdapter(
            dm_post=self._posting.post_bot_dm_message,
            dm_card_post=self._posting.post_bot_dm_card,
            room_post=self._posting.post_bot_room_message,
            card_post=self._posting.post_bot_card,
            session_for=lambda sid: self._sessions.store.load(sid),
        ))
        self._channel_actions = ChannelActionService(self._channel_registry)
        _deps.channel_actions = self._channel_actions
        self._mcp = (
            MCPManager() if mcp_factory is None else MCPManager(process_factory=mcp_factory)
        )
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(
            target=self._run_loop, name="thyca-mcp-loop", daemon=True
        )
        # session_id -> started_at for every turn in flight. Turns are keyed by
        # session, not serialized globally: a slow turn in one session must not
        # block another (nor its own UI, which reads this map). The claim lock
        # coordinates each app-level turn/compaction start with its loop job.
        self._turns = TurnState()
        self._loop_turns = _LoopTurns(self._loop, self._turns)
        self._claim_lock = threading.Lock()
        # Serializes bot_dm's scan/adopt/create so concurrent lookups can
        # never mint two DMs for the same bot (CBE-5 race fix).
        self._dm_lock = threading.Lock()
        # Guards MCP sync (manager + registry + tools snapshot): concurrent
        # turns share all three. Always taken before _claim_lock, never after.
        self._mcp_lock = threading.Lock()
        self._stopped = False
        self._thread.start()
        try:
            if not self._ready.wait(timeout=5):
                raise RuntimeError("mcp loop thread failed to start")
            report_spawn_diags(
                self._submit(self._mcp.spawn_all(cfg.mcpServers)), err=self._stderr
            )
            install_mcp_specs(self._registry, self._mcp, err=self._stderr)
            self._tools = self._registry.to_openai_schema()
            self._act = Act(self._gateway, skills_root=root / "skills")
        except BaseException:
            self.shutdown()
            raise
        # Read-model service (canonical-workspace reads + metering):
        # concrete collaborators only, no back-reference to ChatApp.
        # tools_fn reads the live snapshot (MCP hot-reload rebinds it).
        from thyca.app.bot_operations import BotOperationsDeps, BotOperationsService

        from thyca.app.a2a_delivery import A2ADeliveryDeps, A2ADeliveryService

        self._delivery = A2ADeliveryService(A2ADeliveryDeps(
            root=self._root,
            sessions_store=self._sessions.store,
            window_for=self._webui.window_for,
            bots=self._bots,
            bus=self._bus,
            runtime=self._runtime,
            injected_connect=self._injected_connect,
            display_meta=self._posting.display_meta,
            room_dm_owner=self._room_dm_owner,
            room_delta_for=self._room_delta_for,
            room_bots=self.room_bots,
            set_room_bots=self.set_room_bots,
        ))
        self._runtime.deliverer = self._delivery.deliver_envelope
        from thyca.app.background_jobs import BackgroundJobsDeps, BackgroundJobsService

        self._background_jobs = BackgroundJobsService(BackgroundJobsDeps(
            root=self._root,
            sessions=self._sessions,
            bots=self._bots,
            runtime=self._runtime,
            turns=self._turns,
            window_for=self._webui.window_for,
            current_cfg=self._current_cfg,
            construction_cfg=self._cfg,
            submit=self._submit,
            scheduled_turn=self.scheduled_turn,
            injected_connect=self._injected_connect,
        ))
        from thyca.app.session_admin import SessionAdminDeps, SessionAdminService

        self._sessions_admin = SessionAdminService(SessionAdminDeps(
            root=self._root,
            sessions=self._sessions,
            bots=self._bots,
            runtime=self._runtime,
            turns=self._turns,
            memory=self._memory,
            agent_status=self._agent_status,
            window_for=self._webui.window_for,
            current_cfg=self._current_cfg,
            set_zone=lambda zone: setattr(self, "_zone", zone),
            sync_mcp=self._sync_mcp,
            detail_fn=self._detail,
            flush_memory=self._flush_bot_memory,
            overhead_fn=self._overhead_tokens,
            submit=self._submit,
            dm_lock=self._dm_lock,
            claim_lock=self._claim_lock,
            injected_connect=self._injected_connect,
        ))
        self._ops = BotOperationsService(BotOperationsDeps(
            root=self._root,
            sessions=self._sessions,
            window_for=self._webui.window_for,
            bots=self._bots,
            memory=self._memory,
            state=self._state,
            tools_fn=lambda: self._tools,
            workspaces_store=self._runtime.workspaces.store,
            current_cfg=self._current_cfg,
            room_delta_for=self._room_delta_for,
            memory_task_rows=self._memory_task_rows,
            resolve_responder=self._resolve_responder,
            pending_relevant=self._runtime._pending_relevant,
            turns=self._turns,
            agent_status=self._agent_status,
            skills_root=self.skills_root,
        ))

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.call_soon(self._ready.set)
        self._loop.run_forever()

    def _submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def list_payload(self) -> dict:
        sessions = [
            session_summary(item)
            for item in self._sessions.list_sessions()
            if not is_blank(item) or (item.dm_bot is None and item.bots is not None)
        ]
        return {"model": self._current_cfg().provider.model, "sessions": sessions}

    def _sync_mcp(self, turn_cfg: Config) -> None:
        """Converge MCP servers to the current config file (hot-reload).

        `mcpServers` edits apply on the next turn without restarting serve.
        Per-server failures stay stderr diagnostics and never fail the turn.
        """
        with self._mcp_lock:
            diags, removed = self._submit(self._mcp.sync(turn_cfg.mcpServers))
            report_spawn_diags(diags, err=self._stderr)
            for name in removed:
                self._registry.unregister(name)
            install_mcp_specs(self._registry, self._mcp, err=self._stderr)
            self._tools = self._registry.to_openai_schema()

    def _current_cfg(self) -> Config:
        """Re-read the config file each turn so settings changes apply
        without restarting the server (reasoningEffort, model, ...)."""
        try:
            return load(self._config_file)
        except ConfigError:
            return self._cfg

    def bots_list(self) -> list[dict]:
        """Public card list for GET /api/bots (contract §6).

        Static card fields come from the registry; ``agent_status`` is
        the live runtime snapshot merged in here (never stored on the
        card itself)."""
        cards = self._bots.public_list()
        paused = {
            card["id"]: card["status"] == "paused" for card in cards
        }
        self._agent_status.retain(paused.keys())
        statuses = self._agent_status.snapshot_all(paused)
        from thyca.bots.private_followups import PrivateFollowupStore
        from thyca.bots.status import workspace_status

        pf_store = PrivateFollowupStore(self._root)
        from thyca.bots.runtime import filtered_tool_schemas

        for card in cards:
            snap = statuses.get(
                card["id"],
                self._agent_status.snapshot(
                    card["id"], paused=paused.get(card["id"], False)
                ),
            )
            pending = pf_store.active(card["id"])
            card["agent_status"] = workspace_status(
                snap, pending=pending, channel="webui")
            # Drawer fields: persona + the EFFECTIVE tool list (same
            # filter the runtime applies — baseline + allowlist -
            # hidden - memory/bash policy), not the raw card rows.
            doc = self._bots.get(card["id"])
            if doc is not None and doc.ok and doc.card is not None:
                card["persona"] = (doc.card.persona or "").strip()
                card["memory"] = {
                    "read": doc.card.memory.read,
                    "write": doc.card.memory.write,
                }
                card["tools"] = [
                    {
                        "name": (s.get("function") or {}).get("name", ""),
                        "description": (s.get("function") or {}).get("description", ""),
                    }
                    for s in filtered_tool_schemas(self._tools, doc.card) or []
                ]
        return cards

    def bot_settings(self, bot_id: str) -> dict:
        """One bot's model settings + resolved effective connection."""
        from thyca.app.bot_settings import get_bot_settings

        return get_bot_settings(self._bots, self._current_cfg(), bot_id)

    def update_bot_settings(self, bot_id: str, payload: dict) -> dict:
        """Full-replacement update of one bot's model settings.

        Holds the model coordination lock across the fresh config load,
        membership validation, and settings write, so a concurrent model
        deletion cannot slip in between (lock order: coordination →
        per-bot). The config is loaded strictly here: unlike the
        read-only ``_current_cfg()`` fallback, an unreadable config
        fails the mutation closed instead of validating against a stale
        snapshot. Contention fails closed as 503 via ``SettingsError``.
        """
        from thyca.app.bot_settings import update_bot_settings as _update
        from thyca.bots.settings import SettingsError
        from thyca.config import ConfigError, load
        from thyca.config.coordination import CoordinationError, hold

        try:
            with hold(self._root):
                try:
                    cfg = load(self._config_file)
                except ConfigError:
                    raise SettingsError("bot settings unavailable") from None
                return _update(self._bots, cfg, bot_id, payload)
        except CoordinationError:
            raise SettingsError("bot settings unavailable") from None

    def bot_dm(self, bot_id: str) -> dict:
        """The bot's fixed DM session (CBE-5): find-or-create, marked.

        Exactly one DM per bot; unknown bots raise RegistryError.
        Implementation lives in the session-admin service.
        """
        return self._sessions_admin.bot_dm(bot_id)

    async def _flush_bot_memory(self, bot_id: str, messages, turn_cfg,
                                room_id: str | None = None) -> None:
        """Extract memories from a bot transcript about to compact (CBE-4).

        Implementation lives in the background-jobs service; installed as
        a BotRuntime hook. Best-effort: the runtime guards failures.
        """
        return await self._background_jobs._flush_bot_memory(
            bot_id, messages, turn_cfg, room_id=room_id
        )

    def _search_bot_memory(self, text: str, bot_id: str) -> list[str]:
        """Bounded memory hits for a bot, read as the bot itself (CBE-4)."""
        return self._background_jobs._search_bot_memory(text, bot_id)

    def _memory_task_rows(self, bot_id: str) -> list:
        """Raw open-task catalog rows for one bot (WS-3 projection)."""
        return self._background_jobs._memory_task_rows(bot_id)

    def job_facade(self):
        """Memory facade for the sidecar job (extract/candidates/promote)."""
        return self._background_jobs.job_facade()

    def sweep_idle_sessions(self, idle_s: int = 900) -> dict:
        """Sweep idle canonical bot timelines once, under their workspace lease."""
        return self._background_jobs.sweep_idle_sessions(idle_s=idle_s)

    def aggregate_team_profiles(self) -> dict:
        """Scheduled Thyca turn: review per-bot profile blocks → shared USER.md.

        TURN-CURATE aggregation: runs at most once a day; best-effort,
        never raises.
        """
        return self._background_jobs.aggregate_team_profiles()

    def get_payload(self, session_id: str) -> dict:
        return self._sessions_admin.get_payload(session_id)

    def rename_session(self, session_id: str, title: str) -> str:
        """Set a title the user typed. Returns the stored (cleaned) title."""
        return self._sessions_admin.rename_session(session_id, title)

    def delete_session(self, session_id: str) -> None:
        """Drop the notebook. Memory leaves written from it are left alone."""
        return self._sessions_admin.delete_session(session_id)

    def compact_session(
        self,
        session_id: str,
        model: str | None = None,
        instructions: str | None = None,
    ) -> dict:
        """Compact each participating bot's existing canonical context.

        The room remains a read model. Implementation lives in the
        session-admin service.
        """
        return self._sessions_admin.compact_session(
            session_id, model=model, instructions=instructions
        )

    async def _compact_async(
        self,
        sessions: SessionManager,
        room_id: str,
        turn_cfg: Config,
        instructions: str | None,
        *,
        bot_id: str,
    ) -> dict:
        return await self._sessions_admin._compact_async(
            sessions, room_id, turn_cfg, instructions, bot_id=bot_id
        )

    def default_model(self) -> str:
        """Default model id (for failure logs when the turn omits model)."""
        return self._current_cfg().defaultModel

    def provider_id_for(self, model: str | None) -> str:
        """Provider id a turn resolves through (for failure logs)."""
        cfg = self._current_cfg()
        return cfg.provider_id_for(model or cfg.defaultModel)

    def running_sessions(self) -> dict[str, str]:
        """Snapshot of session_id -> started_at for turns in flight."""
        return self._turns.snapshot()

    def follow_hub(self, session_id: str) -> TurnHub | None:
        """Hub for an in-flight turn, or None if this session is idle.

        Raises the usual session errors when the notebook does not exist or
        cannot be read, so a follower GET can 404/503 instead of "idle".
        """
        hub = self._turns.hub(session_id)
        if hub is not None:
            return hub
        self._sessions.store.load(session_id)
        return None

    def create(self, bots: list[str] | None = None) -> dict:
        return self._sessions_admin.create(bots)

    def room_bots(self, session_id: str) -> list[str]:
        """Participant list of a room; legacy rooms read as Thyca alone."""
        return self._sessions_admin.room_bots(session_id)

    def _exchange_rooms(self) -> list[list]:
        """Shareable room transcripts for exchange pairing.

        Collection lives in :mod:`thyca.bots.exchanges` next to the
        pairing rule; pairing filters by the involved pair so unrelated
        pairs never surface."""
        from thyca.bots.exchanges import collect_exchange_rooms

        return collect_exchange_rooms(self._sessions.list_sessions())

    def dm_exchange_partners(self, bot_id: str) -> list[dict]:
        """Bots this bot exchanged messages with, newest pair first.

        Each item: ``{partner, count, last_ts}``. Unknown bots raise
        :class:`RegistryError` (404 at the serve layer, like ``bot_dm``)."""
        from thyca.bots.exchanges import partners_for

        self._bots.require(bot_id)
        return partners_for(bot_id, self._exchange_rooms())

    def dm_exchange_thread(self, bot_id: str, partner_id: str) -> list[dict]:
        """Every recorded message between two bots, oldest first.

        Items carry ``sender/kind/content/ts/trace_id/span_id``. The
        partner need not be a registered bot (deleted bots still read
        by id); an unknown base bot raises :class:`RegistryError`."""
        from thyca.bots.exchanges import pair_thread

        self._bots.require(bot_id)
        return pair_thread(bot_id, partner_id, self._exchange_rooms())

    def set_room_bots(self, session_id: str, bot_id: str, action: str) -> list[str]:
        """Add/remove one bot from a room. Returns the new participant list."""
        return self._sessions_admin.set_room_bots(session_id, bot_id, action)

    def turn(
        self,
        session_id: str,
        text: str = "",
        event_sink: EventSink | None = None,
        model: str | None = None,
        effort: str | None = None,
        retry: bool = False,
        diagnostic_sink: Callable[[dict], None] | None = None,
    ) -> dict:
        _clean_turn_text(text, retry=retry)
        session = self._sessions.store.load(session_id)
        window = self._webui.window_for(session)
        event = self._webui.event_for(session, text)
        return self._turn_service.run(
            event,
            window=window,
            event_sink=event_sink,
            model=model,
            effort=effort,
            retry=retry,
            diagnostic_sink=diagnostic_sink,
        )

    def scheduled_turn(self, bot_id: str, text: str) -> dict:
        """Scheduler entry: system-actor turn in the bot's fixed DM."""
        return self._turn_service.run_system(bot_id, text)

    def cancel(self, session_id: str) -> None:
        from thyca.app.private_followups import PrivateFollowupService

        with self._claim_lock:
            running = self._turns.hub(session_id) is not None
            if running:
                self._loop_turns.request_cancel(session_id)
        if not running:
            if PrivateFollowupService(self).cancel_window(session_id):
                return
            self._sessions.store.load(session_id)
            raise SessionIdle()
        deadline = time.monotonic() + _CANCEL_WAIT_S
        while time.monotonic() < deadline:
            if self._turns.hub(session_id) is None:
                PrivateFollowupService(self).cancel_window(session_id)
                return
            time.sleep(0.05)
        raise TurnInFlight("turn still in flight")

    @staticmethod
    def _resolve_responder(
        bots: list[str], text: str, paused: frozenset[str] | set[str] = frozenset()
    ) -> str:
        """Who answers this room turn: @mention wins, else the room.

        A ``@<bot-id>`` mention names the responder inside a shared room;
        a 1-1 room is answered by its only bot; otherwise the first
        participant (the default room's Thyca) answers. Paused bots are
        skipped: a paused mention falls through to the next active
        participant instead of running a paused bot."""
        active = [bot for bot in bots if bot not in paused]
        for mention in re.findall(r"@([a-z0-9]+(?:-[a-z0-9]+)*)", text):
            if mention in active:
                return mention
        return active[0] if active else (bots[0] if bots else "thyca")

    def _room_cursor(self, bot_id: str, room_id: str) -> tuple[int, str] | None:
        """This bot's room-context cursor (truth lives in the registry)."""
        try:
            return self._runtime.room_cursor(bot_id, room_id)
        except Exception:
            return None

    def _room_delta_for(self, bot_id: str, room_id: str,
                        room_messages: list) -> tuple[str, tuple[int, str] | None]:
        """Unread public room text + next cursor for one bot (phase 2A)."""
        from thyca.bots.room_context import room_delta

        return room_delta(room_messages, bot_id, self._room_cursor(bot_id, room_id))

    async def _run_bot_turn(
        self,
        session_id: str,
        text: str,
        event_sink: EventSink | None = None,
        *,
        turn_cfg: Config,
        effort: str | None,
        retry: bool,
        bot_id: str,
        model_override: str | None = None,
        diagnostic_sink: Callable[[dict], None] | None = None,
    ) -> dict:
        """Run one bot turn and record it into the room transcript.

        The bot thinks in its private (bot × room) session; the room only
        ever gains rows written here by the app layer (user row + the
        bot's reply + fresh bus events), each stamped with
        ``bot_id/kind/trace_id`` (contract §6/BE-6)."""
        card = self._bots.require(bot_id)
        operation_cfg = turn_cfg
        turn_cfg = resolve_bot_cfg(
            operation_cfg, card, model_override=model_override
        )
        sessions = SessionManager(
            limits=turn_cfg.effective_limits(),
            timezone_name=turn_cfg.timeline.timezone,
            store=self._sessions.store,
        )
        sessions.load(session_id)
        if retry and not sessions.truncate_to_last_user():
            raise InvalidTurnOption("no user")
        provider = resolve_bot_provider(turn_cfg, card, effort_override=effort)
        connect = self._injected_connect or ConnectFactory.create(
            provider.api, provider
        )
        owns = self._injected_connect is None
        # Phase 2A rework: cursor truth lives in the room map. The delta
        # covers room history BEFORE the current user row; after a
        # successful turn the cursor advances past that row too (DMs skip
        # all of this — strictly private).
        room_context, room_cursor, room_after = "", None, None
        current_msg = None
        is_dm = self._room_dm_owner(session_id) is not None
        if not retry:
            from thyca.app.turn_event import current_channel_event
            from thyca.core.channel import WORK_REF_RE

            _ev = current_channel_event()
            _row_meta = None
            if _ev is not None:
                _row_meta = {
                    "event_id": _ev.event_id,
                    "channel_id": _ev.channel_id,
                    "window_id": _ev.window_id,
                    "actor_kind": _ev.actor.kind,
                }
                _wr = getattr(_ev, "work_ref", None)
                if isinstance(_wr, str) and WORK_REF_RE.fullmatch(_wr):
                    _row_meta["work_id"] = _wr
            current_msg = Message(
                role="user", content=text,
                meta=_row_meta if _row_meta else None)
        if not is_dm:
            try:
                from thyca.bots.room_context import fingerprint as _fp
                from thyca.bots.room_context import public_room_messages as _prm

                prior = list(sessions.current.messages)
                room_context, room_cursor = self._room_delta_for(
                    bot_id, session_id, prior)
                if current_msg is not None and text.strip():
                    room_after = (
                        len(_prm(prior, bot_id)) + 1,
                        _fp({"ts": current_msg.ts, "speaker": "user",
                             "content": text}),
                    )
                else:
                    room_after = room_cursor
            except Exception:
                room_context, room_cursor, room_after = "", None, None
        try:
            scope = BotTurnScope(
                store=self._sessions.store,
                act=self._act,
                tools=self._tools,
                memory=self._memory,
                state=self._state,
                zone=self._zone,
                injected_connect=connect,
            )
            trace_id = new_trace_id()
            span_id = new_span_id()
            if not retry:
                # The user row lands before the turn runs so A2A rows
                # delivered mid-turn order after it in the transcript.
                # Same object fingerprinted for the post-turn cursor.
                early_room = SessionManager(
                    limits=turn_cfg.effective_limits(),
                    timezone_name=turn_cfg.timeline.timezone,
                    store=self._sessions.store,
                )
                early_room.load(session_id)
                early_room.append(
                    current_msg
                    if current_msg is not None
                    else Message(role="user", content=text)
                )
                self._posting.consume_dm_pending(
                    bot_id, session_id, room=early_room)
            try:
                result = await self._runtime.run_bot_turn_detailed(
                    scope=scope,
                    card=card,
                    room_id=session_id,
                    text=text,
                    turn_cfg=operation_cfg,
                    event_sink=event_sink,
                    trace_id=trace_id,
                    span_id=span_id,
                    room_text=room_context,
                    room_cursor_after=room_after,
                    workspace_claimed=True,
                    retry=retry,
                    model_override=model_override,
                    effort=effort,
                    diagnostic_sink=diagnostic_sink,
                )
                reply = result.reply
            except asyncio.CancelledError:
                raise
            except LLMError as exc:
                try:
                    sessions.load(session_id)
                    sessions.mark_turn_error("llm_error", str(exc))
                except Exception:
                    pass
                raise
            except Exception:
                try:
                    sessions.load(session_id)
                    sessions.mark_turn_error("chat_unavailable", "chat unavailable")
                except Exception:
                    pass
                raise
            room = SessionManager(
                limits=turn_cfg.effective_limits(),
                timezone_name=turn_cfg.timeline.timezone,
                store=self._sessions.store,
            )
            room.load(session_id)
            self._record_bus_events(room, session_id)
            room.append(
                Message(
                    role="assistant",
                    content=reply,
                    # Aggregated internal-turn meta (model/usage/cost/
                    # latency) rides along so trace by_bot sees real
                    # numbers for this bot's work.
                    meta=result.room_meta(
                        kind="message", trace_id=trace_id, parent_span_id=None
                    ),
                )
            )
            await _name_if_needed(connect, room, turn_cfg, event_sink, bot_id=bot_id)
            landed = self._detail(room.current, turn_cfg, running=False)
            return {**landed, "reply": reply}
        finally:
            if owns:
                close = getattr(connect, "aclose", None)
                if close is not None:
                    await close()

    def _room_dm_owner(self, session_id: str) -> str | None:
        """Bot id if *session_id* is that bot's fixed DM, else None."""
        for session in self._sessions.list_sessions():
            if session.id == session_id:
                return session.dm_bot
        return None

    def post_bot_dm_message(self, bot_id: str, text: str,
                            current_room_id: str = "", meta: dict | None = None) -> dict:
        """Post *text* into *bot_id*'s own DM under its own name.

        The ``message_user`` tool's landing path: implementation lives
        in the posting service.
        """
        return self._posting.post_bot_dm_message(
            bot_id, text, current_room_id=current_room_id, meta=meta
        )

    def post_bot_dm_card(self, bot_id: str, card: dict) -> dict:
        """Post a structured card into the bot's OWN DM window."""
        return self._posting.post_bot_dm_card(bot_id, card)

    def post_bot_room_message(self, bot_id: str, text: str,
                              room_id: str, meta: dict | None = None) -> dict:
        """Post a plain status row into a room the bot participates in."""
        return self._posting.post_bot_room_message(
            bot_id, text, room_id, meta=meta
        )

    def post_bot_card(self, bot_id: str, card: dict,
                      current_room_id: str = "", content: str = "") -> dict:
        """Post a structured question/error card into the current room."""
        return self._posting.post_bot_card(
            bot_id, card, current_room_id=current_room_id, content=content
        )

    async def _deliver_envelope(self, envelope, ctx) -> str:
        """A2A delivery: run the recipient bot's own turn, record it.

        Installed as ``BotRuntime.deliverer`` (see the delivery service).
        """
        return await self._delivery.deliver_envelope(envelope, ctx)

    async def _deliver_envelope_claimed(self, envelope, ctx) -> str:
        return await self._delivery.deliver_envelope_claimed(envelope, ctx)

    def _record_bus_events(self, room: SessionManager, session_id: str) -> None:
        """Append fresh A2A bus envelopes for *this room* to its transcript.

        Implementation lives in the delivery service; the turn path calls
        through here.
        """
        return self._delivery.record_bus_events(room, session_id)

    def shutdown(self) -> None:

        if self._stopped:
            return
        self._stopped = True
        try:
            if self._loop.is_running():
                self._submit(self._gateway.shutdown())
                self._submit(self._mcp.shutdown())
        except Exception:
            pass
        finally:
            if self._loop.is_running():
                self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=5)

    def trace_store(self) -> SessionStore:
        """Session store for read-only scan surfaces (e.g. /api/traces)."""
        return self._sessions.store

    def trace_execution_messages(
        self, session_id: str, bot_id: str, span_id: str
    ) -> list[Message]:
        """Read canonical execution rows for a trusted room/bot/span tuple."""
        return self._ops.trace_execution_messages(session_id, bot_id, span_id)

    def bot_room_tasks(self, session_id: str, bot_id: str) -> dict:
        """Live task list for exactly one room participant (read-only)."""
        return self._ops.room_tasks(session_id, bot_id)

    def bot_operations(
        self, session_id: str, bot_id: str, *,
        model: str | None = None, effort: str | None = None,
    ) -> dict:
        """Read operational data for exactly one room participant.

        Model/context describe the selected next-turn configuration, not a
        guess at an in-flight provider request. Actual historical usage and
        costs come from original canonical events, including retries.
        """
        return self._ops.bot_operations(
            session_id, bot_id, model=model, effort=effort
        )

    def _workspace_session(self, room: Session, bot_id: str, cfg: Config) -> Session | None:
        """Read an existing canonical context without creating its binding."""
        return self._ops._workspace_session(room, bot_id, cfg)

    def _overhead_tokens(
        self, session_id: str, *, turn_cfg: Config, bot_id: str,
        strict: bool = False,
    ) -> tuple[int, int]:
        """Estimate shared hot/tool overhead, excluding query-only inputs.

        The meter cannot know the next user text, mailbox batch, or
        query-specific post-compaction recall; those are omitted rather than
        fabricated. Runtime and meter use the same prepared-input composer.
        """
        return self._ops._overhead_tokens(
            session_id, turn_cfg=turn_cfg, bot_id=bot_id, strict=strict
        )

    def _context_usage(self, session: Session, cfg: Config) -> dict:
        return self._ops._context_usage(session, cfg)

    def _detail(
        self, session: Session, cfg: Config, *, running: bool | None = None
    ) -> dict:
        """Session payload with this app's in-flight answer filled in."""
        return self._ops._detail(session, cfg, running=running)
