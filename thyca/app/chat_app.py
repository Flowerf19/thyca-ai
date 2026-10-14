"""Loopback chat: list/create sessions and run one AgentLoop turn."""
from __future__ import annotations

import asyncio
import json
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from thyca.agent.act import Act
from thyca.agent.compacting import compact_with_llm
from thyca.agent.events import EventSink, TurnEvent
from thyca.agent.think import LLMPort
from thyca.app.summarizing import LlmSummarizer
from thyca.bots.bus import Bus, new_span_id, new_trace_id
from thyca.bots.policy import BotPolicy
from thyca.bots.registry import BotRegistry, RegistryError
from thyca.bots.runtime import BotRuntime
from thyca.bots.status import AgentStatusTracker
from thyca.core.protocol import Message
from thyca.config import Config, ConfigError, load
from thyca.core.protocol import Message, estimate_tokens as _chars_to_tokens
from thyca.llm.llm_base import LLMError
from thyca.llm.llm_factory import ConnectFactory
from thyca.llm.prompt_manager import PromptManager
from thyca.memory.active import ActiveMemory, ActiveSnapshot
from thyca.sessions.wire import session_detail, session_summary
from thyca.sessions import Session, SessionManager, calibration_factor, context_usage
from thyca.sessions.store import SessionStore
from thyca.sessions.title import is_blank
from thyca.tools.gateway.background import BackgroundProcs
from thyca.tools.mcp import MCPManager, ProcessFactory
from thyca.tools.task_store import TaskStore
from thyca.serve.turn_state import TurnHub, TurnState

from thyca.app.loop_turns import _CANCEL_WAIT_S, _LoopTurns, TurnCancelled
from thyca.app.naming import _name_if_needed, session_title
from thyca.app.run_turn import TurnRequest, TurnScope, mark_turn_error, run_turn
from thyca.app.toolchain import (
    build_tool_gateway,
    build_tool_registry,
    install_mcp_specs,
    report_spawn_diags,
)
from thyca.app.turn_options import InvalidTurnOption, InvalidTurnText, _clean_turn_text, overlay_turn_cfg

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
    ) -> None:
        self._root = root
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
        # A2A delivery: send tools run the recipient's turn through here.
        self._runtime.deliverer = self._deliver_envelope
        # CBE-4: flush-before-compact + post-compact recall hooks.
        self._runtime.memory_flush = self._flush_bot_memory
        self._runtime.memory_searcher = self._search_bot_memory
        # Thyca-only admin tools (BE-5): registered once on the shared
        # registry; the runtime hides every bot_* name from child bots and
        # each handler re-checks the caller, so a leak still cannot be used.
        from thyca.bots.admin_tools import AdminDeps, register_admin_tools

        register_admin_tools(
            self._registry,
            AdminDeps(self._bots, self._runtime, self.job_facade,
                      dm_post=self.post_bot_dm_message,
                      card_post=self.post_bot_card),
        )
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
        # block another (nor its own UI, which reads this map). Shared with the
        # delete gate so a claim cannot slip between check and unlink.
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
                self._submit(self._mcp.spawn_all(cfg.mcpServers)), err=sys.stderr
            )
            install_mcp_specs(self._registry, self._mcp, err=sys.stderr)
            self._tools = self._registry.to_openai_schema()
            self._act = Act(self._gateway, skills_root=root / "skills")
        except BaseException:
            self.shutdown()
            raise

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
            if not is_blank(item)
        ]
        return {"model": self._current_cfg().provider.model, "sessions": sessions}

    def _sync_mcp(self, turn_cfg: Config) -> None:
        """Converge MCP servers to the current config file (hot-reload).

        `mcpServers` edits apply on the next turn without restarting serve.
        Per-server failures stay stderr diagnostics and never fail the turn.
        """
        with self._mcp_lock:
            diags, removed = self._submit(self._mcp.sync(turn_cfg.mcpServers))
            report_spawn_diags(diags, err=sys.stderr)
            for name in removed:
                self._registry.unregister(name)
            install_mcp_specs(self._registry, self._mcp, err=sys.stderr)
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
        for card in cards:
            card["agent_status"] = statuses.get(
                card["id"],
                self._agent_status.snapshot(
                    card["id"], paused=paused.get(card["id"], False)
                ),
            )
        return cards

    def bot_dm(self, bot_id: str) -> dict:
        """The bot's fixed DM session (CBE-5): find-or-create, marked.

        Exactly one DM per bot: a room session with ``bots == [bot_id]``
        and the ``dm`` meta marker, lazily created on first lookup. A
        legacy unmarked solo room is adopted (marked) instead of making
        a second window. Returns the session detail payload (``dm`` =
        bot id, ``bots`` = [bot_id]). Unknown bots raise RegistryError.

        The whole scan -> adopt -> create sequence runs under
        ``_dm_lock``: without it, two concurrent first lookups could
        both miss the scan and each create a session, leaving the bot
        with two competing "fixed" DMs."""
        card = self._bots.require(bot_id)
        if card.paused:
            raise RegistryError(f"bot is paused: {bot_id}")
        with self._dm_lock:
            legacy = None
            for session in self._sessions.list_sessions():
                if session.dm_bot == bot_id:
                    return self.get_payload(session.id)
                bots = list(session.bots) if session.bots else ["thyca"]
                if bots == [bot_id] and legacy is None:
                    legacy = session
            if legacy is not None:
                self._sessions.store.append_dm(legacy.path, bot_id)
                self._sessions.set_dm(legacy.id, bot_id)
                return self.get_payload(legacy.id)
            created = self.create(bots=[bot_id])
            session = self._sessions.store.load(created["id"])
            self._sessions.store.append_dm(session.path, bot_id)
            return self.get_payload(created["id"])

    async def _flush_bot_memory(self, bot_id: str, messages, turn_cfg) -> None:
        """Extract memories from a bot transcript about to compact (CBE-4).

        Same machinery and order as the room idle sweep: the existing
        extractor runs on the intact transcript and writes through the
        Thyca-privileged facade (per-bot owners come from the extractor
        or default to this bot); compaction happens only after this
        returns, in the caller. Best-effort: the runtime guards failures.

        Policy decision (lane plan, explicit): this is Thyca-managed,
        system-driven extraction tied to system compaction, so it runs
        even for ``memory.write=false`` cards — that flag gates the
        bot's own ``memory_*`` tools, not the system's housekeeping."""
        from thyca.memory import jobs as mem_jobs

        provider = turn_cfg.effective_provider()
        connect = self._injected_connect or ConnectFactory.create(
            provider.api, provider
        )
        owns = self._injected_connect is None
        try:
            transcript = mem_jobs.transcript_tail(list(messages))
            if not transcript.strip():
                return
            extracted = await mem_jobs.extract_from_transcript(
                connect, turn_cfg.provider.model, transcript
            )
            if extracted:
                mem_jobs.apply_extracted(
                    self.job_facade(), extracted, default_bot=bot_id
                )
        finally:
            if owns:
                close = getattr(connect, "aclose", None)
                if close is not None:
                    await close()

    def _search_bot_memory(self, text: str, bot_id: str) -> list[str]:
        """Bounded memory hits for a bot, read as the bot itself (CBE-4)."""
        from thyca.memory.identity import bind_bot_id, reset_bot_id

        token = bind_bot_id(bot_id)
        try:
            result = self.job_facade().search(text, limit=5)
        finally:
            reset_bot_id(token)
        return [
            f"- {hit.heading}: {hit.snippet}" for hit in result.hits[:5]
        ]

    def job_facade(self):
        """Memory facade for the sidecar job (extract/candidates/promote).

        Lazily built and cached: the constructor reindexes, so sharing one
        instance beats rebuilding it per sweep. Locks are shared process-wide."""
        from thyca.memory.facade import MemoryFacade

        facade = getattr(self, "_job_facade", None)
        if facade is None:
            facade = MemoryFacade(self._root, timezone_name=self._cfg.timeline.timezone)
            self._job_facade = facade
        return facade

    def sweep_idle_sessions(self, idle_s: int = 900) -> dict:
        """Backend idle trigger (BE-4): extract memories, then compact.

        Every room quiet past ``idle_s`` gets its transcript extracted
        first and compacted after — never the reverse. Rooms with a turn
        (or compact) in flight are skipped. Best-effort per room: one
        room's failure never blocks the rest. Returns a per-room report."""
        from thyca.core.protocol import utc_now_ts
        from thyca.memory import jobs as mem_jobs
        from thyca.sessions.wire import updated_at

        now_ts = utc_now_ts()
        running = set(self._turns.snapshot())
        report: dict[str, dict] = {}
        turn_cfg = self._current_cfg()
        for item in self._sessions.list_sessions():
            if item.id in running:
                continue
            try:
                last_ts = updated_at(item)
            except Exception:
                continue
            if not mem_jobs.should_idle_sweep(last_ts, now_ts, idle_s):
                continue
            entry: dict = {"idle_since": last_ts}
            try:
                provider = turn_cfg.effective_provider()
                connect = self._injected_connect or ConnectFactory.create(
                    provider.api, provider
                )
                owns = self._injected_connect is None
                try:
                    transcript = mem_jobs.transcript_tail(list(item.messages))
                    extracted = self._submit(
                        mem_jobs.extract_from_transcript(
                            connect, turn_cfg.provider.model, transcript
                        )
                    )
                    facade = self.job_facade()
                    applied = mem_jobs.apply_extracted(facade, extracted)
                    entry["extracted"] = len(applied["written"])
                    entry["proposed"] = len(applied["proposed"])
                    if applied["written"]:
                        # Merge funnel production caller: freshly written
                        # entries are the merge candidates; the reviewer
                        # is the turn model, called only for candidates.
                        from thyca.memory import merge as mem_merge

                        async def _review(a: str, b: str, _c=connect) -> str:
                            return await mem_merge.llm_review_pair(
                                _c, turn_cfg.provider.model, a, b
                            )

                        merged = self._submit(
                            mem_merge.run_merge_pass(facade, applied["written"], _review)
                        )
                        entry["merged"] = len(merged["linked"])
                        entry["merge_reviews"] = merged["review_calls"]
                finally:
                    if owns:
                        close = getattr(connect, "aclose", None)
                        if close is not None:
                            self._submit(close())
                detail = self.compact_session(item.id)
                entry["compacted"] = bool(
                    (detail.get("compaction") or {}).get("compacted", False)
                )
            except Exception as exc:
                entry["error"] = type(exc).__name__
            report[item.id] = entry
        # Bot internal sessions (CBE-4): same extract-then-compact order
        # for every (bot x room) transcript quiet past the idle window.
        # Like the room sweep above, an internal session whose *room*
        # has a turn in flight is skipped: ``running`` is keyed by room
        # session id, so the bot's room map (built once per bot, before
        # any compacting rewrites it) translates internal -> room.
        bot_report: dict[str, dict] = {}
        for doc in self._bots.list():
            if not doc.ok or doc.card is None:
                continue
            bot_id = doc.card.bot_id
            try:
                store = self._runtime.store_for(bot_id)
                manager = SessionManager(
                    limits=turn_cfg.effective_limits(),
                    timezone_name=turn_cfg.timeline.timezone, store=store,
                )
                room_by_internal = {
                    internal: room
                    for room, internal in self._runtime._read_room_map(bot_id).items()
                }
                for item in manager.list_sessions():
                    if room_by_internal.get(item.id) in running:
                        continue
                    key = f"{bot_id}:{item.id}"
                    try:
                        last_ts = updated_at(item)
                    except Exception:
                        continue
                    if not mem_jobs.should_idle_sweep(last_ts, now_ts, idle_s):
                        continue
                    entry = {"idle_since": last_ts}
                    try:
                        awaitable = self._flush_bot_memory(
                            bot_id, list(item.messages), turn_cfg
                        )
                        self._submit(awaitable)
                        manager.load(item.id)
                        compacted = manager.compact_manual()
                        entry["compacted"] = bool(compacted["compacted"])
                        if entry["compacted"]:
                            # Runtime keys re-injection by room id; the
                            # sweep holds internal ids, so translate via
                            # the room map built above (unknown: skip).
                            room_key = room_by_internal.get(item.id)
                            if room_key is not None:
                                self._runtime._reinject_pending.add((bot_id, room_key))
                    except Exception as exc:
                        entry["error"] = type(exc).__name__
                    bot_report[key] = entry
            except Exception as exc:
                bot_report[bot_id] = {"error": type(exc).__name__}
        return {"now": now_ts, "rooms": report, "bots": bot_report}

    def get_payload(self, session_id: str) -> dict:
        session = self._sessions.store.load(session_id)
        return self._detail(session, self._current_cfg())

    def rename_session(self, session_id: str, title: str) -> str:
        """Set a title the user typed. Returns the stored (cleaned) title."""
        if not isinstance(title, str):
            raise ValueError("title must be a string")
        return self._sessions.rename(session_id, title)

    def delete_session(self, session_id: str) -> None:
        """Drop the notebook. Memory leaves written from it are left alone."""
        self._turns.delete_unclaimed(self._sessions, session_id)
        self._agent_status.forget_room(session_id)

    def compact_session(
        self,
        session_id: str,
        model: str | None = None,
        instructions: str | None = None,
    ) -> dict:
        """Manually compact one idle session; return its fresh detail.

        Mirrors turn setup (live knobs + MCP sync + per-operation
        SessionManager) so the tail budget uses the same limits and
        overhead the next turn would. Summarization runs on the loop
        thread with the session's own model under the TurnState compact
        gate; a turn in flight (or another compact) raises
        ``SessionBusy``. Any summarizer failure falls back to the
        rule-based excerpt inside ``compact_with_llm``.
        """
        turn_cfg = overlay_turn_cfg(self._current_cfg(), model, None)
        self._memory.tail_kb = turn_cfg.limits.hotTailKB
        self._memory.timezone_name = turn_cfg.timeline.timezone
        self._zone = ZoneInfo(turn_cfg.timeline.timezone)
        self._sync_mcp(turn_cfg)
        sessions = SessionManager(
            limits=turn_cfg.effective_limits(),
            timezone_name=turn_cfg.timeline.timezone,
            store=self._sessions.store,
        )
        sessions.load(session_id)
        self._turns.begin_compact(session_id)
        try:
            result = self._submit(
                self._compact_async(sessions, session_id, turn_cfg, instructions)
            )
            detail = self._detail(sessions.current, turn_cfg, running=False)
        finally:
            self._turns.end_compact(session_id)
        return {**detail, "compaction": result}

    async def _compact_async(
        self,
        sessions: SessionManager,
        session_id: str,
        turn_cfg: Config,
        instructions: str | None,
    ) -> dict:
        provider = turn_cfg.effective_provider()
        connect = self._injected_connect or ConnectFactory.create(
            provider.api, provider
        )
        owns = self._injected_connect is None
        try:
            hot_tokens, tools_tokens = self._overhead_tokens(session_id)
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
        # Do not wait on any turn: create/list stay responsive while an LLM
        # call is in flight. Blank sessions belonging to a running turn must
        # survive the prune — its transcript is still empty on disk. Blank
        # bot DMs survive it too (discard_empty keeps ``dm_bot`` sessions:
        # a lazily created DM is blank until its first turn).
        participants = self._clean_participants(bots) if bots is not None else None
        self._sessions.discard_empty(keep=set(self._turns.snapshot()))
        session = self._sessions.create()
        if participants is not None:
            self._sessions.store.append_bots(session.path, participants)
            session.bots = list(participants)
        return self._detail(session, self._current_cfg())

    def room_bots(self, session_id: str) -> list[str]:
        """Participant list of a room; legacy rooms read as Thyca alone."""
        session = self._sessions.store.load(session_id)
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
        allowed, as it can only move the room back toward DM shape."""
        if action not in ("add", "remove"):
            raise ValueError("action must be add|remove")
        card = self._bots.require(bot_id)
        # Paused bots cannot join, but must always be removable.
        if action == "add" and card.paused:
            raise RegistryError(f"bot is paused: {bot_id}")
        session = self._sessions.store.load(session_id)
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
        self._sessions.store.append_bots(session.path, current)
        self._sessions.set_bots(session_id, current)
        return current

    def _clean_participants(self, bots: list[str]) -> list[str]:
        if not isinstance(bots, list) or not bots:
            raise ValueError("bots must be a non-empty list of bot ids")
        cleaned: list[str] = []
        for bot_id in bots:
            if not isinstance(bot_id, str) or not bot_id:
                raise ValueError("bots must be a non-empty list of bot ids")
            card = self._bots.require(bot_id)
            if card.paused:
                raise RegistryError(f"bot is paused: {bot_id}")
            if bot_id not in cleaned:
                cleaned.append(bot_id)
        return cleaned

    def turn(
        self,
        session_id: str,
        text: str = "",
        event_sink: EventSink | None = None,
        model: str | None = None,
        effort: str | None = None,
        retry: bool = False,
    ) -> dict:
        cleaned = _clean_turn_text(text, retry=retry)
        turn_cfg = overlay_turn_cfg(self._current_cfg(), model, effort)
        # The gateway/memory are built once; refresh live knobs from current
        # config so Provider-page saves apply without a restart.
        limits = turn_cfg.effective_limits()
        self._gateway.set_soft_timeout_s(limits.softTimeoutS)
        # Global section, not effective: ActiveMemory is shared by concurrent
        # turns, so a per-model value would race across turns. (softTimeoutS
        # is global-only too; ModelCfg has no such field.)
        self._memory.tail_kb = turn_cfg.limits.hotTailKB
        self._memory.timezone_name = turn_cfg.timeline.timezone
        self._zone = ZoneInfo(turn_cfg.timeline.timezone)
        self._sync_mcp(turn_cfg)
        bots = self.room_bots(session_id)
        paused = {
            doc.card.bot_id
            for doc in self._bots.list()
            if doc.ok and doc.card is not None and doc.card.paused
        }
        responder = self._resolve_responder(bots, cleaned, paused=paused)
        with self._claim_lock:
            hub = self._turns.claim(session_id)
            job = self._loop_turns.begin(session_id)
        if not retry:
            # A fresh user message answers any waiting/error card in
            # this room; a retry replays the same message and must not.
            # Only after the claim: a refused (busy) turn posted nothing.
            self._agent_status.note_user_message(session_id)
        try:

            def sink(event: TurnEvent) -> None:
                hub.publish(event)
                if event_sink is not None:
                    event_sink(event)

            if responder == "thyca":
                coro = self._run_turn(
                    session_id,
                    cleaned,
                    sink,
                    turn_cfg=turn_cfg,
                    effort=effort,
                    retry=retry,
                )
            else:
                coro = self._run_bot_turn(
                    session_id,
                    cleaned,
                    sink,
                    turn_cfg=turn_cfg,
                    effort=effort,
                    retry=retry,
                    bot_id=responder,
                )
            try:
                detail = self._loop_turns.submit(job, coro)
            except TurnCancelled:
                hub.publish(("cancelled", None))
                raise
            except Exception as exc:
                hub.publish(("failed", exc))
                raise
            hub.publish(("completed", detail))
            return detail
        finally:
            with self._claim_lock:
                self._loop_turns.end(session_id, job)
                self._turns.release(session_id)

    def cancel(self, session_id: str) -> None:
        with self._claim_lock:
            if self._turns.hub(session_id) is None:
                self._sessions.store.load(session_id)
                raise SessionIdle()
            self._loop_turns.request_cancel(session_id)
        deadline = time.monotonic() + _CANCEL_WAIT_S
        while time.monotonic() < deadline:
            if self._turns.hub(session_id) is None:
                return
            time.sleep(0.05)
        raise TurnInFlight("turn still in flight")

    async def _run_turn(
        self,
        session_id: str,
        text: str,
        event_sink: EventSink | None = None,
        *,
        turn_cfg: Config,
        effort: str | None,
        retry: bool,
    ) -> dict:
        # Turn execution lives in app.run_turn; the app shell only
        # assembles the shared scope and the detail builder here.
        # Thyca reads the same open-task view bots get (CBE-4): derived
        # from this room's transcript + queued bus envelopes, no store.
        from thyca.bots.tasks import derive_open_tasks, format_task_index

        try:
            room_messages = list(self._sessions.store.load(session_id).messages)
        except Exception:
            room_messages = []
        thyca_tasks = derive_open_tasks(
            room_messages, self._runtime._pending_relevant("thyca"), bot_id="thyca"
        )
        scope = TurnScope(
            store=self._sessions.store,
            act=self._act,
            tools=self._tools,
            memory=self._memory,
            state=self._state,
            zone=self._zone,
            injected_connect=self._injected_connect,
            hot_tasks=format_task_index(thyca_tasks[:20]) if thyca_tasks else "",
            status_tracker=self._agent_status,
        )
        request = TurnRequest(
            session_id=session_id,
            text=text,
            event_sink=event_sink,
            turn_cfg=turn_cfg,
            effort=effort,
            retry=retry,
        )
        return await run_turn(scope, request, self._detail)

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
    ) -> dict:
        """Run one bot turn and record it into the room transcript.

        The bot thinks in its private (bot × room) session; the room only
        ever gains rows written here by the app layer (user row + the
        bot's reply + fresh bus events), each stamped with
        ``bot_id/kind/trace_id`` (contract §6/BE-6)."""
        card = self._bots.require(bot_id)
        sessions = SessionManager(
            limits=turn_cfg.effective_limits(),
            timezone_name=turn_cfg.timeline.timezone,
            store=self._sessions.store,
        )
        sessions.load(session_id)
        if retry and not sessions.truncate_to_last_user():
            raise InvalidTurnOption("no user")
        provider = turn_cfg.effective_provider()
        connect = self._injected_connect or ConnectFactory.create(
            provider.api, provider
        )
        owns = self._injected_connect is None
        try:
            scope = TurnScope(
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
                early_room = SessionManager(
                    limits=turn_cfg.effective_limits(),
                    timezone_name=turn_cfg.timeline.timezone,
                    store=self._sessions.store,
                )
                early_room.load(session_id)
                early_room.append(Message(role="user", content=text))
            try:
                result = await self._runtime.run_bot_turn_detailed(
                    scope=scope,
                    card=card,
                    room_id=session_id,
                    text=text,
                    turn_cfg=turn_cfg,
                    event_sink=event_sink,
                    trace_id=trace_id,
                    span_id=span_id,
                )
                reply = result.reply
            except asyncio.CancelledError:
                raise
            except LLMError as exc:
                mark_turn_error(sessions, "llm_error", str(exc))
                raise
            except Exception:
                mark_turn_error(sessions, "chat_unavailable", "chat unavailable")
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

    def _bot_display_meta(self, bot_id: str) -> dict:
        """Display companions so transcript/Trace use the card's own
        name+tint instead of the FE hash fallback."""
        try:
            card = self._bots.require(bot_id)
        except Exception:
            return {}
        return {"bot_name": card.name, "bot_tint": card.tint}

    def _room_dm_owner(self, session_id: str) -> str | None:
        """Bot id if *session_id* is that bot's fixed DM, else None."""
        for session in self._sessions.list_sessions():
            if session.id == session_id:
                return session.dm_bot
        return None

    def post_bot_dm_message(self, bot_id: str, text: str,
                            current_room_id: str = "") -> dict:
        """Post *text* into *bot_id*'s own DM under its own name.

        The ``message_user`` tool's landing path (user decision
        2026-10-13): a bot that needs to talk privately with the user
        writes into its own DM window — never into the room its current
        turn happens to run in. Refused when the current turn already
        runs in that DM, where a normal reply is the private answer."""
        dm = self.bot_dm(bot_id)
        if current_room_id and current_room_id == dm["id"]:
            return {"ok": False, "error": (
                "you are already in your private DM with the user; "
                "just reply normally")}
        cfg = self._current_cfg()
        room = SessionManager(
            limits=cfg.effective_limits(),
            timezone_name=cfg.timeline.timezone,
            store=self._sessions.store,
        )
        room.load(dm["id"])
        room.append(Message(
            role="assistant",
            content=text,
            meta={"bot_id": bot_id, "kind": "message", **self._bot_display_meta(bot_id)},
        ))
        return {"ok": True, "session_id": dm["id"]}

    def post_bot_card(self, bot_id: str, card: dict,
                      current_room_id: str = "", content: str = "") -> dict:
        """Post a structured question/error card into the current room.

        The ``ask_user``/``report_error`` landing path: the card rides
        on an assistant row's ``meta.card`` (the FE renderer was ready
        before this contract extension existed). Privacy gates mirror
        A2A recording: another bot's DM never receives the card, and a
        non-DM room only accepts cards from its participants."""
        if not current_room_id:
            return {"ok": False, "error": "no current room for a card"}
        session = next(
            (s for s in self._sessions.list_sessions() if s.id == current_room_id),
            None,
        )
        if session is None:
            return {"ok": False, "error": f"unknown room: {current_room_id}"}
        if session.dm_bot is not None and session.dm_bot != bot_id:
            return {"ok": False, "error": (
                "current room is another bot's private DM; return the "
                "question/problem to that bot or use message_user in "
                "your own DM")}
        participants = list(session.bots) if session.bots else ["thyca"]
        if bot_id not in participants:
            return {"ok": False, "error": f"bot is not a participant of this room: {bot_id}"}
        meta: dict = {"bot_id": bot_id, "kind": "card", "card": dict(card)}
        meta.update(self._bot_display_meta(bot_id))
        cfg = self._current_cfg()
        room = SessionManager(
            limits=cfg.effective_limits(),
            timezone_name=cfg.timeline.timezone,
            store=self._sessions.store,
        )
        room.load(current_room_id)
        room.append(Message(role="assistant", content=content, meta=meta))
        card_type = card.get("type") if isinstance(card, dict) else None
        self._agent_status.note_card_posted(bot_id, current_room_id, card_type)
        return {"ok": True, "session_id": current_room_id}

    def _promote_participant(self, session_id: str, bot_id: str) -> None:
        """Add a delivery recipient to a normal room's participants.

        A plain session becomes a team session when work is delegated
        in it (frontend plan §4.1): without this the room shows the
        recipient's rows while its participant list denies membership.
        DMs never promote (private by definition; set_room_bots also
        guards them) — the caller checks ownership first."""
        try:
            if bot_id not in self.room_bots(session_id):
                self.set_room_bots(session_id, bot_id, "add")
        except Exception:
            pass  # promotion is presentational; delivery must not fail on it

    async def _deliver_envelope(self, envelope, ctx) -> str:
        """A2A delivery: run the recipient bot's own turn, record it.

        Installed as ``BotRuntime.deliverer``. The triggering envelope is
        taken off the recipient's mailbox (its payload *is* the turn
        text), the recipient runs in its private (bot x room) session,
        and its reply lands in the room transcript under its own name
        with aggregated cost meta and a span parented to the envelope.
        The reply text is returned for the sender's tool result, so
        multi-hop bot->bot flows compose inside one sender turn.

        DM privacy (user decision 2026-10-13): a fixed DM is strictly
        user + its owner bot, so nothing A2A is recorded into someone
        else's DM — the recipient still runs and its reply still
        returns to the sender, who reports it under its own name. A
        result may land in a DM only when the recipient owns that DM
        (scheduled/independent work reporting home)."""
        room_id = envelope.session_id or ctx.room_id
        try:
            card = self._bots.require(envelope.recipient)
        except Exception as exc:
            return f"delivery failed: {exc}"
        if card.paused:
            return f"delivery failed: bot is paused: {envelope.recipient}"
        self._bus.take(envelope)
        span_id = new_span_id()
        room: SessionManager | None = None
        dm_owner = self._room_dm_owner(room_id) if room_id else None
        if room_id:
            try:
                room = SessionManager(
                    limits=ctx.turn_cfg.effective_limits(),
                    timezone_name=ctx.turn_cfg.timeline.timezone,
                    store=self._sessions.store,
                )
                room.load(room_id)
                if dm_owner is None:
                    self._promote_participant(room_id, envelope.recipient)
                self._record_bus_events(room, room_id)
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
        try:
            result = await self._runtime.run_bot_turn_detailed(
                scope=ctx.scope,
                card=card,
                room_id=room_id or f"a2a:{envelope.sender}",
                text=text,
                turn_cfg=ctx.turn_cfg,
                event_sink=ctx.event_sink,
                trace_id=envelope.trace_id,
                parent_span_id=envelope.span_id,
                span_id=span_id,
            )
        except Exception as exc:
            return f"delivery failed: {type(exc).__name__}: {exc}"
        if room is not None and (dm_owner is None or dm_owner == envelope.recipient):
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

    def _record_bus_events(self, room: SessionManager, session_id: str) -> None:
        """Append fresh A2A bus envelopes for *this room* to its transcript.

        One row per envelope under the sender's name; the app layer is the
        only writer (bots never touch the room transcript directly). The
        bus log is drained per room, so the bus's trimmed global log can
        never shift an index past live rows for other rooms.

        A fixed DM never receives A2A rows (strictly user + owner): its
        envelopes are still drained so the per-room cursor advances,
        they are just not written into the private transcript."""
        events = self._bus.drain_log(session_id)
        if self._room_dm_owner(session_id) is not None:
            return
        for event in events:
            envelope = event.envelope
            # Payload is already the <=1000-char prompt-facing excerpt;
            # refs ride in meta so the room row stays fetchable (CBE-3).
            meta: dict = {
                "bot_id": envelope.sender,
                **self._bot_display_meta(envelope.sender),
                "kind": envelope.kind,
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

    def _overhead_tokens(self, session_id: str) -> tuple[int, int]:
        """(hot, tools) sizes the next turn would add, AgentLoop-style.

        Reads via ``peek`` (never ``refresh``): a GET must not roll the
        day, recreate files, or race a turn's real refresh on shared
        state. Best-effort for read paths: a memory file that fails to
        read must not blank the transcript, so hot degrades to 0 (the
        ratio becomes a lower bound) while a real turn still surfaces
        the error.
        """
        tools_tokens = (
            _chars_to_tokens(json.dumps(self._tools, ensure_ascii=False))
            if self._tools
            else 0
        )
        try:
            hot = self._memory.peek(
                self._state, datetime.now(self._zone), session_id=session_id
            )
            hot_tokens = (
                _chars_to_tokens(PromptManager().build(hot))
                if isinstance(hot, ActiveSnapshot)
                else 0
            )
        except Exception:
            hot_tokens = 0
        return hot_tokens, tools_tokens

    def _context_usage(self, session: Session, cfg: Config) -> dict:
        hot_tokens, tools_tokens = self._overhead_tokens(session.id)
        return context_usage(
            session.messages,
            cfg.effective_limits().contextTokens,
            hot_tokens=hot_tokens,
            tools_tokens=tools_tokens,
            calibration=calibration_factor(session.messages, model=cfg.provider.model),
        )

    def _detail(
        self, session: Session, cfg: Config, *, running: bool | None = None
    ) -> dict:
        """Session payload with this app's in-flight answer filled in."""
        started_at = None if running is not None else self._turns.started_at(session.id)
        return session_detail(
            session,
            cfg,
            skills_root=self.skills_root,
            running=running,
            started_at=started_at,
            context=self._context_usage(session, cfg),
        )
