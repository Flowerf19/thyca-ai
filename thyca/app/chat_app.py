"""Loopback chat facade for room sessions and workspace turns."""
from __future__ import annotations

import asyncio
import json
import re
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import TextIO
from zoneinfo import ZoneInfo

from thyca.agent.act import Act
from thyca.agent.compacting import compact_with_llm
from thyca.agent.events import EventSink
from thyca.agent.think import LLMPort
from thyca.app.summarizing import LlmSummarizer
from thyca.bots.bus import Bus, new_span_id, new_trace_id
from thyca.bots.policy import BotPolicy
from thyca.bots.registry import BotRegistry, RegistryError
from thyca.bots.runtime import BotRuntime
from thyca.bots.status import AgentStatusTracker
from thyca.config import Config, ConfigError, load
from thyca.core.protocol import Message, estimate_tokens as _chars_to_tokens
from thyca.llm.llm_base import LLMError
from thyca.llm.llm_factory import ConnectFactory
from thyca.llm.prompt_manager import PromptManager
from thyca.memory.active import ActiveMemory, ActiveSnapshot
from thyca.sessions.wire import session_detail, session_summary
from thyca.sessions import (
    Session,
    SessionBusy,
    SessionManager,
    calibration_factor,
    context_usage,
)
from thyca.sessions.store import SessionStore
from thyca.sessions.title import is_blank
from thyca.tools.gateway.background import BackgroundProcs
from thyca.tools.mcp import MCPManager, ProcessFactory
from thyca.tools.task_store import TaskStore
from thyca.serve.turn_state import TurnHub, TurnState

from thyca.app.loop_turns import _CANCEL_WAIT_S, _LoopTurns, TurnCancelled
from thyca.app.naming import _name_if_needed, session_title
from thyca.bots.context import BotTurnScope, current_bot_turn_context
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
    overlay_turn_cfg,
    resolve_bot_cfg,
)
from thyca.app.webui_channel import WebUIChannelAdapter
from thyca.app.workspace_turns import WorkspaceTurnService
from thyca.bots.channels import ChannelActionService, ChannelRegistry
from thyca.bots.private_followups import (
    PrivateFollowupError,
    PrivateFollowupStore,
)
from thyca.bots.workspace import WorkspaceTurnConflict

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
        # A2A delivery: send tools run the recipient's turn through here.
        self._runtime.deliverer = self._deliver_envelope
        # CBE-4: flush-before-compact + post-compact recall hooks.
        self._runtime.memory_flush = self._flush_bot_memory
        self._runtime.memory_searcher = self._search_bot_memory
        # Phase 1C: open memory-task catalog lines per bot.
        self._runtime.memory_task_index = self._memory_task_index
        self._runtime.memory_task_catalog = self._memory_task_rows
        self._webui = WebUIChannelAdapter()
        self._turn_service = WorkspaceTurnService(
            self, self._runtime.workspaces
        )
        # Thyca-only admin tools (BE-5): registered once on the shared
        # registry; the runtime hides every bot_* name from child bots and
        # each handler re-checks the caller, so a leak still cannot be used.
        from thyca.bots.admin_tools import AdminDeps, register_admin_tools

        _deps = AdminDeps(
            self._bots, self._runtime, self.job_facade,
            dm_post=self.post_bot_dm_message,
            card_post=self.post_bot_card,
            room_post=self.post_bot_room_message,
            dm_resolve=lambda bid: self.bot_dm(bid)["id"],
        )
        _deps.dm_card_post = self.post_bot_dm_card
        register_admin_tools(self._registry, _deps)
        from thyca.bots.channel_tools import register_channel_tools

        register_channel_tools(self._registry, _deps)
        from thyca.app.webui_channel import WebUIOutboundAdapter

        self._channel_registry = ChannelRegistry()
        self._channel_registry.register(WebUIOutboundAdapter(
            dm_post=self.post_bot_dm_message,
            dm_card_post=self.post_bot_dm_card,
            room_post=self.post_bot_room_message,
            card_post=self.post_bot_card,
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
        return cards

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
        card = self._bots.require(bot_id)
        if card.paused:
            raise RegistryError(f"bot is paused: {bot_id}")
        # Shared helper owns find-or-create; this lock keeps the
        # concurrent one-DM guarantee (the helper itself is lock-free).
        # Prune only on the create path: an existing DM returns at once
        # (rework 2B — pruning on every lookup could eat a freshly made
        # blank group when opening another bot's DM).
        with self._dm_lock:
            for session in self._sessions.list_sessions():
                if session.dm_bot == bot_id:
                    return self.get_payload(session.id)
            keep = set(self._turns.snapshot())
            ctx = current_bot_turn_context()
            if ctx is not None and ctx.room_id:
                keep.add(ctx.room_id)
            from thyca.bots.private_followups import PrivateFollowupStore

            pf_store = PrivateFollowupStore(self._root)
            for other in self._bots.list():
                card = getattr(other, "card", other)
                other_id = getattr(card, "bot_id", None)
                if not isinstance(other_id, str) or not other_id:
                    continue
                pending = pf_store.active(other_id)
                if pending is not None:
                    keep.add(pending.origin_window)
                    keep.add(pending.dm_window)
            self._sessions.discard_empty(keep=keep)
            session = self._sessions.ensure_dm(bot_id)
            return self.get_payload(session.id)

    async def _flush_bot_memory(self, bot_id: str, messages, turn_cfg,
                                room_id: str | None = None) -> None:
        """Extract memories from a bot transcript about to compact (CBE-4).

        Same machinery and order as the room idle sweep: the existing
        extractor runs on the intact transcript and writes through the
        Thyca-privileged facade (per-bot owners come from the extractor
        or default to this bot); compaction happens only after this
        returns, in the caller. Best-effort: the runtime guards failures.

        Policy decision (lane plan, explicit): this is Thyca-managed,
        system-driven extraction tied to system compaction, so it runs
        even for ``memory.write=false`` cards — that flag gates the
        bot's own ``memory_*`` tools, not the system's housekeeping.

        TURN-CURATE: the runtime skips this hook entirely when the agent
        recorded a checkpoint in the segment (it already wrote what
        matters before compacting itself via the ``compact`` tool)."""
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
            from thyca.memory.identity import bind_bot_id, reset_bot_id

            facade = self.job_facade()
            token = bind_bot_id(bot_id)
            try:
                open_tasks = facade.tasks(status="open").get("tasks", [])
            finally:
                reset_bot_id(token)
            extracted = await mem_jobs.extract_from_transcript(
                connect, turn_cfg.provider.model, transcript,
                open_tasks=open_tasks,
            )
            if extracted:
                applied = mem_jobs.apply_extracted(
                    facade, extracted, default_bot=bot_id,
                    open_tasks=open_tasks, chat=room_id,
                )
                if applied["written"]:
                    # Dedupe now triggers here, per bot: freshly written
                    # entries are reviewed for team duplicates. Best-effort
                    # like every hook on this path — a dedupe failure never
                    # breaks the flush (or the compaction after it).
                    try:
                        from thyca.memory import dedupe as mem_dedupe

                        await mem_dedupe.propose_duplicates(
                            facade, applied["written"],
                            lambda a, b: mem_dedupe.llm_review_pair(
                                connect, turn_cfg.provider.model, a, b),
                        )
                    except Exception:
                        pass
            # Phase 1E habit sweep (best-effort, never breaks the flush):
            # newly-qualified habit labels append to this bot's own USER.md.
            try:
                from thyca.memory import habits as mem_habits

                sweep_token = bind_bot_id(bot_id)
                try:
                    mem_habits.sweep_bot_habits(
                        facade, bot_id, self._bots.bot_dir(bot_id) / "USER.md"
                    )
                finally:
                    reset_bot_id(sweep_token)
            except Exception:
                pass
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

    def _memory_task_rows(self, bot_id: str) -> list:
        """Raw open-task catalog rows for one bot (WS-3 projection)."""
        from thyca.memory.identity import bind_bot_id, reset_bot_id

        token = bind_bot_id(bot_id)
        try:
            catalog = self.job_facade().tasks(status="open")
        finally:
            reset_bot_id(token)
        return list(catalog.get("tasks", []))

    def _memory_task_index(self, bot_id: str) -> str:
        """Open memory-task catalog lines for one bot (phase 1C)."""
        from thyca.memory.facade import format_task_catalog
        from thyca.memory.identity import bind_bot_id, reset_bot_id

        token = bind_bot_id(bot_id)
        try:
            catalog = self.job_facade().tasks(status="open")
        finally:
            reset_bot_id(token)
        return format_task_catalog(catalog.get("tasks", []))

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
        """Sweep idle canonical bot timelines once, under their workspace lease."""
        from thyca.core.protocol import utc_now_ts
        from thyca.memory import jobs as mem_jobs
        from thyca.sessions.wire import updated_at

        now_ts = utc_now_ts()
        running = set(self._turns.snapshot())
        base_cfg = self._current_cfg()
        # Every bot, including Thyca, is swept only through canonical
        # workspace timelines; public room transcripts are read models.
        bot_report: dict[str, dict] = {}

        async def _sweep_workspace_window(
            bot_id: str, window_id: str, bot_cfg: Config
        ):
            async with self._runtime.workspaces.lease(bot_id):
                # Bindings and transcript may have changed while waiting
                # for the lease. Never compact a pre-lease snapshot.
                binding = self._runtime.workspaces.store.get_binding(
                    bot_id, window_id)
                if binding is None or binding.internal_session_id is None:
                    return None
                session_id = binding.internal_session_id
                window = binding.window
                if window is None:
                    try:
                        room_session = self._sessions.store.load(window_id)
                        window = self._webui.window_for(room_session)
                    except Exception:
                        return None
                sweep_manager = SessionManager(
                    limits=bot_cfg.effective_limits(),
                    timezone_name=bot_cfg.timeline.timezone,
                    store=self._runtime._canonical_store(
                        bot_id, window, session_id),
                )
                try:
                    sweep_item = sweep_manager.load(session_id)
                    last_ts = updated_at(sweep_item)
                except Exception:
                    return None
                if not mem_jobs.should_idle_sweep(last_ts, now_ts, idle_s):
                    return None
                entry = {"idle_since": last_ts}
                try:
                    await self._flush_bot_memory(
                        bot_id, list(sweep_item.messages), bot_cfg,
                        room_id=window_id,
                    )
                    sweep_manager.load(session_id)
                    compacted = sweep_manager.compact_manual()
                    entry["compacted"] = bool(compacted["compacted"])
                    if entry["compacted"]:
                        self._runtime._reinject_pending.add(
                            (bot_id, window_id))
                except Exception as exc:
                    entry["error"] = type(exc).__name__
                return f"{bot_id}:{session_id}", entry

        for doc in self._bots.list():
            if not doc.ok or doc.card is None:
                continue
            bot_id = doc.card.bot_id
            bot_cfg = resolve_bot_cfg(base_cfg, doc.card)
            try:
                bindings = self._runtime.workspaces.store.load(bot_id)
            except Exception as exc:
                bot_report[bot_id] = {"error": type(exc).__name__}
                continue
            if not bindings:
                continue
            for window_id in bindings:
                if window_id in running or self._runtime.workspaces.busy(bot_id):
                    continue
                try:
                    result = self._submit(
                        _sweep_workspace_window(bot_id, window_id, bot_cfg))
                except Exception as exc:
                    bot_report[f"{bot_id}:{window_id}"] = {
                        "error": type(exc).__name__}
                    continue
                if result is not None:
                    key, entry = result
                    bot_report[key] = entry
        # TURN-CURATE: shared-profile aggregation (Thyca reviews per-bot
        # profile blocks and writes the merged set via profile_update).
        try:
            profiles_aggregated = self.aggregate_team_profiles()
        except Exception:
            profiles_aggregated = {"error": "profile aggregation failed"}
        return {"now": now_ts, "rooms": {}, "bots": bot_report,
                "profiles_aggregated": profiles_aggregated}

    def _user_direct_profile_observations(self) -> list[dict]:
        """kind='habit' observations the user stated directly (no owner)."""
        from thyca.memory.habits import habit_observations

        try:
            records = habit_observations(self.job_facade(), None)
        except Exception:
            return []
        return [
            record for record in records if record.get("bots") is None
        ]

    def aggregate_team_profiles(self) -> dict:
        """Scheduled Thyca turn: review per-bot profile blocks → shared USER.md.

        TURN-CURATE aggregation: one prompt carries every bot's
        ``## Profile`` block with provenance, user-direct observations,
        the current shared block, and bot cards as scope context; Thyca
        reviews (dedupe / scope / conflict) and writes the merged set
        through the ``profile_update`` tool. Runs at most once a day, or
        immediately when bot profile files changed. Best-effort: never
        raises.
        """
        from thyca.memory import profile as mem_profile

        try:
            paths = {}
            for doc in self._bots.list():
                if doc.ok and doc.card is not None:
                    paths[doc.card.bot_id] = (
                        self._bots.bot_dir(doc.card.bot_id) / "USER.md"
                    )
            current: dict[str, tuple[int, int] | None] = {}
            for bot_id, path in paths.items():
                try:
                    stat = path.stat() if path.is_file() else None
                except OSError:
                    stat = None
                current[bot_id] = (
                    None if stat is None else (stat.st_mtime_ns, stat.st_size))
            previous = getattr(self, "_profile_signatures", None)
            now = time.time()
            last = getattr(self, "_profile_last_run", 0.0)
            if previous is not None and current == previous and now - last < 24 * 3600:
                return {"ran": False}
            blocks: dict[str, list[str]] = {}
            for bot_id, path in paths.items():
                try:
                    text = path.read_text(encoding="utf-8") if path.is_file() else ""
                except OSError:
                    text = ""
                lines = mem_profile.parse_profile_lines(text)
                if lines:
                    blocks[bot_id] = lines
            shared = self._root / "USER.md"
            try:
                shared_text = (
                    shared.read_text(encoding="utf-8") if shared.is_file() else ""
                )
            except OSError:
                shared_text = ""
            current_profile = mem_profile.profile_block_lines(shared_text)
            user_direct = self._user_direct_profile_observations()
            if not any(blocks.values()) and not user_direct:
                self._profile_signatures = current
                self._profile_last_run = now
                return {"ran": False}
            prompt = self._profile_aggregation_prompt(
                blocks, user_direct, current_profile
            )
            detail = self.scheduled_turn("thyca", prompt)
            # Migration cleanup: the legacy ## Habits auto block drops only
            # when every legacy line already lives in the ## Profile block.
            try:
                mem_profile.maybe_strip_legacy_block(
                    shared, lock_for=self.job_facade().writer.lock_for
                )
            except Exception:
                pass
            self._profile_signatures = current
            self._profile_last_run = now
            return {"ran": True, "reply": str(detail.get("reply", ""))[:200]}
        except Exception:
            return {"error": "profile aggregation failed"}

    def _profile_aggregation_prompt(
        self,
        blocks: dict[str, list[str]],
        user_direct: list[dict],
        current_profile: list[str],
    ) -> str:
        parts = [
            "Nhiệm vụ định kỳ: tổng hợp hồ sơ (profile) của user vào USER.md chung.",
            "",
            "PROFILE TỪNG BOT ĐÃ TỰ GHI (dòng có qualifier như 'Discord: ...' "
            "chỉ đúng ở kênh/bot đó):",
        ]
        for bot_id in sorted(blocks):
            card = self._profile_card_line(bot_id)
            parts.append(f"### bot {bot_id}{card}")
            parts.extend(f"- {line}" for line in blocks[bot_id])
        parts.append("")
        parts.append("USER NÓI TRỰC TIẾP (tin cậy cao nhất, kèm ngày):")
        for obs in user_direct:
            day = obs.get("timeline_day") or "?"
            explicit = " explicit" if obs.get("habit_explicit") else ""
            parts.append(f"- {obs.get('habit', '')}  (ngày {day}{explicit})")
        parts.append("")
        parts.append("PROFILE CHUNG HIỆN TẠI (auto block — thay thế toàn bộ khi cần):")
        parts.extend(f"- {line}" for line in current_profile)
        parts.append("")
        parts.append(
            "QUY TẮC TỔNG HỢP:\n"
            "1. Gộp các dòng cùng nghĩa (biến thể câu); dòng chỉ đúng ở 1 "
            "kênh/bot thì GIỮ qualifier ('Discord: ...'); dòng được ≥2 "
            "bot/kênh độc lập xác nhận thì thành universal (bỏ qualifier).\n"
            "2. User nói trực tiếp luôn thắng; cùng phạm vi mâu thuẫn thì ưu "
            "tiên dòng mới hơn (ngày).\n"
            "3. Chỉ giữ fact bền về user (sở thích, ưu tiên, tên gọi, phong "
            "cách, mục tiêu...); bỏ chuyện vặt, tạm thời, bí mật, credential, "
            "nội dung không phải về user.\n"
            "4. Gọi profile_update với lines = danh sách ĐẦY ĐỦ bạn muốn giữ "
            "trong auto block (KHÔNG phải diff). Nếu kết quả không khác gì "
            "hiện tại thì KHÔNG gọi tool.\n"
            "Trả lời ngắn gọn."
        )
        return "\n".join(parts)

    def _profile_card_line(self, bot_id: str) -> str:
        try:
            card = self._bots.require(bot_id)
        except Exception:
            return ""
        persona = " ".join((card.persona or "").split())[:200]
        line = f" | role: {card.role} | desc: {card.description[:200]}"
        if persona:
            line += f" | persona: {persona}"
        return line

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
        """Compact each participating bot's existing canonical context.

        The room remains a read model. Existing bindings are reloaded and
        compacted sequentially under each bot's workspace lease; absent
        contexts are reported without creating bindings.
        """
        base_cfg = self._current_cfg()
        if model is not None:
            base_cfg = overlay_turn_cfg(base_cfg, model, None)
        self._memory.tail_kb = base_cfg.limits.hotTailKB
        self._memory.timezone_name = base_cfg.timeline.timezone
        self._zone = ZoneInfo(base_cfg.timeline.timezone)
        self._sync_mcp(base_cfg)
        with self._claim_lock:
            room = self._sessions.store.load(session_id)
            self._turns.begin_compact(session_id)
        try:
            per_bot: dict[str, dict] = {}
            for bot_id in room.bots or ["thyca"]:
                try:
                    binding = self._runtime.workspaces.store.get_binding(
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

                card = self._bots.require(bot_id)
                bot_cfg = resolve_bot_cfg(
                    base_cfg, card, model_override=model
                )

                async def compact_one(_bot_id=bot_id, _cfg=bot_cfg):
                    async with self._runtime.workspaces.lease(_bot_id):
                        fresh = self._runtime.workspaces.store.get_binding(
                            _bot_id, session_id
                        )
                        if fresh is None or fresh.internal_session_id is None:
                            return {
                                "compacted": False,
                                "skipped": "no_existing_context",
                            }
                        internal_id = fresh.internal_session_id
                        window = fresh.window or self._webui.window_for(room)
                        sessions = SessionManager(
                            limits=_cfg.effective_limits(),
                            timezone_name=_cfg.timeline.timezone,
                            store=self._runtime._canonical_store(
                                _bot_id, window, internal_id
                            ),
                        )
                        sessions.load(internal_id)
                        flush_error = None
                        try:
                            await self._flush_bot_memory(
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
                            self._runtime._reinject_pending.add(
                                (_bot_id, session_id)
                            )
                        if flush_error is not None:
                            result["flush_error"] = flush_error
                        return result

                try:
                    per_bot[bot_id] = self._submit(compact_one())
                except Exception as exc:
                    per_bot[bot_id] = {
                        "compacted": False,
                        "error": type(exc).__name__,
                    }
            detail = self._detail(room, base_cfg, running=False)
        finally:
            self._turns.end_compact(session_id)
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
        turn_cfg: Config,
        instructions: str | None,
        *,
        bot_id: str,
    ) -> dict:
        provider = turn_cfg.effective_provider()
        connect = self._injected_connect or ConnectFactory.create(
            provider.api, provider
        )
        owns = self._injected_connect is None
        try:
            hot_tokens, tools_tokens = self._overhead_tokens(
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
        self._sessions.discard_empty(keep=set(self._turns.snapshot()))
        session = self._sessions.create()
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
        turn_cfg = overlay_turn_cfg(turn_cfg, None, effort)
        sessions = SessionManager(
            limits=turn_cfg.effective_limits(),
            timezone_name=turn_cfg.timeline.timezone,
            store=self._sessions.store,
        )
        sessions.load(session_id)
        if retry and not sessions.truncate_to_last_user():
            raise InvalidTurnOption("no user")
        provider = turn_cfg.effective_provider()
        if effort is not None:
            selected = turn_cfg.models.get(turn_cfg.defaultModel)
            own_set = selected.reasoningEfforts if selected is not None else ()
            provider = replace(
                provider, reasoningEffort=effort, reasoningEfforts=own_set
            )
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
                self._consume_dm_pending(
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

    def _consume_dm_pending(self, bot_id: str, session_id: str,
                            room) -> None:
        """A DM answer to a waiting private follow-up -> received.

        Runs right after the user row is persisted (never on retry, so a
        replays can't double-consume). The stored answer_ref points at
        the real transcript row; the raw body is never copied into the
        pending."""
        from thyca.app.turn_event import current_channel_event
        from thyca.bots.private_followups import PrivateFollowupStore

        ev = current_channel_event()
        if ev is None or not ev.event_id:
            return
        if getattr(ev.actor, "kind", None) != "user":
            return
        session = self._sessions.store.load(session_id)
        if session.dm_bot != bot_id:
            return
        store = PrivateFollowupStore(self._root)
        pending = store.active(bot_id)
        if pending is None or pending.status != "waiting_private":
            return
        if pending.dm_window != session_id:
            return
        row_index = len(self._sessions.store.load(session_id).messages) - 1
        store.receive(
            bot_id,
            pending.pending_id,
            answer_event_id=ev.event_id,
            answer_ref=f"{session_id}:{row_index}",
            idempotency_key=ev.event_id,
        )

    def post_bot_dm_message(self, bot_id: str, text: str,
                            current_room_id: str = "", meta: dict | None = None) -> dict:
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
        if meta and meta.get("kind") == "private_question":
            for m in reversed(list(room.current.messages)):
                m_meta = m.meta or {}
                if m_meta.get("pfq_marker") == meta.get("pfq_marker"):
                    return {"ok": True, "session_id": dm["id"],
                            "deduped": True}
        row_meta = {"bot_id": bot_id, "kind": "message",
                    **self._bot_display_meta(bot_id)}
        if meta:
            row_meta.update(meta)
        room.append(Message(
            role="assistant",
            content=text,
            meta=row_meta,
        ))
        return {"ok": True, "session_id": dm["id"]}

    def post_bot_dm_card(self, bot_id: str, card: dict) -> dict:
        """Post a structured card into the bot's OWN DM window."""
        dm = self.bot_dm(bot_id)
        cfg = self._current_cfg()
        room = SessionManager(
            limits=cfg.effective_limits(),
            timezone_name=cfg.timeline.timezone,
            store=self._sessions.store,
        )
        room.load(dm["id"])
        pf = card.get("private_followup") if isinstance(card, dict) else None
        if isinstance(pf, dict) and pf.get("pending_id"):
            for m in reversed(list(room.current.messages)):
                m_card = (m.meta or {}).get("card") or {}
                m_pf = m_card.get("private_followup") or {}
                if (
                    m_pf.get("pending_id") == pf.get("pending_id")
                    and m_pf.get("revision") == pf.get("revision")
                ):
                    return {"ok": True, "session_id": dm["id"],
                            "deduped": True}
        room.append(Message(
            role="assistant",
            content=card.get("title", "question"),
            meta={"bot_id": bot_id, "kind": "card", "card": dict(card),
                  **self._bot_display_meta(bot_id)},
        ))
        return {"ok": True, "session_id": dm["id"]}

    def post_bot_room_message(self, bot_id: str, text: str,
                              room_id: str, meta: dict | None = None) -> dict:
        """Post a plain status row into a room the bot participates in.

        ``meta`` is for trusted internal callers only — when it carries a
        ``pending_id`` marker the room is scanned first and a matching
        row means the write already happened (crash-safe resume): the
        existing row is returned instead of a duplicate."""
        if not room_id:
            return {"ok": False, "error": "no room"}
        session = next(
            (s for s in self._sessions.list_sessions() if s.id == room_id),
            None,
        )
        if session is None:
            return {"ok": False, "error": f"unknown room: {room_id}"}
        if session.dm_bot is not None and session.dm_bot != bot_id:
            return {"ok": False, "error": "room is another bot's DM"}
        participants = list(session.bots) if session.bots else ["thyca"]
        if bot_id not in participants:
            return {"ok": False, "error": f"not a participant: {bot_id}"}
        cfg = self._current_cfg()
        room = SessionManager(
            limits=cfg.effective_limits(),
            timezone_name=cfg.timeline.timezone,
            store=self._sessions.store,
        )
        room.load(room_id)
        marker = (meta or {}).get("pending_id")
        if marker is not None:
            for m in reversed(list(room.current.messages)):
                m_meta = m.meta or {}
                if (
                    m_meta.get("pending_id") == marker
                    and m_meta.get("kind") == (meta or {}).get("kind")
                    and m_meta.get("proposal_revision") == (meta or {}).get("proposal_revision")
                    and m_meta.get("bot_id") == bot_id
                ):
                    return {"ok": True, "session_id": room_id,
                            "deduped": True}
        row_meta = {"bot_id": bot_id, "kind": "status",
                    **self._bot_display_meta(bot_id)}
        if meta:
            row_meta.update(meta)
        room.append(Message(
            role="assistant",
            content=text,
            meta=row_meta,
        ))
        return {"ok": True, "session_id": room_id}

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
        from thyca.app.turn_event import current_channel_event
        from thyca.core.channel import WORK_REF_RE

        _ev = current_channel_event()
        if _ev is not None:
            meta.setdefault("channel", _ev.channel_id)
            meta.setdefault("window_id", _ev.window_id)
            _wr = getattr(_ev, "work_ref", None)
            if isinstance(_wr, str) and WORK_REF_RE.fullmatch(_wr):
                meta.setdefault("work_id", _wr)
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
        try:
            async with self._runtime.workspaces.lease(envelope.recipient):
                from thyca.memory.visibility import (
                    MemoryView,
                    bound_memory_view,
                )

                window = None
                rid = envelope.session_id or ctx.room_id
                if rid:
                    try:
                        window = self._webui.window_for(
                            self._sessions.store.load(rid)
                        )
                    except Exception:
                        window = None
                with bound_memory_view(
                    MemoryView(
                        window=window,
                        bot_id=envelope.recipient,
                        thyca_dir=self._root,
                    )
                ):
                    return await self._deliver_envelope_claimed(envelope, ctx)
        except WorkspaceTurnConflict as exc:
            return f"delivery failed: {exc}"

    async def _deliver_envelope_claimed(self, envelope, ctx) -> str:
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
        # Phase 2A: the recipient reads the room's public history before
        # handling this envelope. Computed AFTER recording bus events: the
        # fresh envelope row carries bus_event=True so it can never leak
        # into its own delta. Skipped for DM rooms.
        room_context, room_cursor = "", None
        if room is not None and dm_owner is None:
            try:
                room_context, room_cursor = self._room_delta_for(
                    envelope.recipient, room_id, list(room.current.messages))
            except Exception:
                room_context, room_cursor = "", None
        try:
            if room_id:
                try:
                    window = self._webui.window_for(
                        self._sessions.store.load(room_id)
                    )
                except Exception:
                    window = None
                if window is not None:
                    self._runtime.workspaces.store.bind_window(
                        envelope.recipient, window
                    )
            result = await self._runtime.run_bot_turn_detailed(
                scope=replace(
                    ctx.scope, injected_connect=self._injected_connect
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
            # bus_event marks A2A control rows so the public room filter
            # (phase 2A) skips them absolutely; kind stays for FE/trace.
            meta: dict = {
                "bot_id": envelope.sender,
                **self._bot_display_meta(envelope.sender),
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
        from thyca.bots.timeline import TraceExecutionError, WorkspaceTimeline

        room = self._sessions.store.load(session_id)
        window = self._webui.window_for(room)
        if bot_id not in window.participants:
            raise TraceExecutionError(
                "execution bot is not a participant in the referenced room"
            )
        binding = self._runtime.workspaces.store.get_binding(bot_id, session_id)
        if binding is None or binding.internal_session_id is None:
            raise TraceExecutionError(
                "canonical execution binding is missing for this room and bot"
            )
        if (
            binding.window is None
            or binding.window.window_id != window.window_id
        ):
            raise TraceExecutionError(
                "canonical execution binding does not match the referenced room"
            )
        timeline_path = (
            self._root / "bots" / bot_id / "workspace" / "timeline.jsonl"
        )
        if not timeline_path.is_file():
            raise TraceExecutionError(
                "canonical execution timeline is missing for this room and bot"
            )
        try:
            timeline = WorkspaceTimeline(self._root, bot_id, create=False)
        except FileNotFoundError as exc:
            raise TraceExecutionError(
                "canonical execution timeline disappeared while reading"
            ) from exc
        messages = timeline.messages_for_span(window, span_id)
        if not messages:
            raise TraceExecutionError(
                "canonical execution span is missing from the room timeline"
            )
        return messages

    def bot_operations(
        self, session_id: str, bot_id: str, *,
        model: str | None = None, effort: str | None = None,
    ) -> dict:
        """Read operational data for exactly one room participant.

        Model/context describe the selected next-turn configuration, not a
        guess at an in-flight provider request. Actual historical usage and
        costs come from original canonical events, including retries.
        """
        from thyca.bots.timeline import WorkspaceTimeline
        from thyca.bots.usage import usage_for_window
        from thyca.llm.pricing import pricing_for
        from thyca.sessions.wire import turn_slices

        room = self._sessions.store.load(session_id)
        window = self._webui.window_for(room)
        if bot_id not in window.participants:
            raise RegistryError("bot is not a participant in this room")
        card = self._bots.require(bot_id)
        base = self._current_cfg()
        if model is not None:
            base = overlay_turn_cfg(base, model, None)
        cfg = resolve_bot_cfg(base, card, model_override=model)
        cfg = overlay_turn_cfg(cfg, None, effort)
        provider = cfg.effective_provider()
        effective_effort = effort if effort is not None else provider.reasoningEffort
        canonical = self._workspace_session(room, bot_id, cfg)
        messages = list(canonical.messages) if canonical is not None else []
        history = []
        if canonical is not None:
            timeline = WorkspaceTimeline(self._root, bot_id, create=False)
            history = timeline.messages_for_window(window)
        usage = usage_for_window(history)
        latest = usage["last_turn"]
        trace = None
        if latest is not None:
            for index, rows in enumerate(turn_slices(room.messages)):
                if any(
                    (message.meta or {}).get("bot_id") == bot_id
                    and (message.meta or {}).get("span_id") == latest["span_id"]
                    for message in rows
                ):
                    trace = {"session_id": session_id, "turn_index": index}
                    break
        hot_tokens, tools_tokens = self._overhead_tokens(
            session_id, turn_cfg=cfg, bot_id=bot_id, strict=True,
        )
        context = context_usage(
            messages, cfg.effective_limits().contextTokens,
            hot_tokens=hot_tokens, tools_tokens=tools_tokens,
            calibration=calibration_factor(messages, model=cfg.defaultModel),
        )
        rates = pricing_for(cfg.defaultModel, cfg.effective_pricing() or None)
        return {
            "bot_id": bot_id,
            "session_id": session_id,
            "model": {
                "id": cfg.defaultModel,
                "provider": cfg.provider_id_for(cfg.defaultModel),
                "reasoning_effort": effective_effort,
                "scope": "next_turn_configuration",
            },
            "context": {**context, "estimated": True},
            **usage,
            "rates": None if rates is None else {
                "currency": "USD", "per_tokens": 1_000_000,
                "input": rates.input, "output": rates.output, "cache": rates.cache,
            },
            "trace": trace,
        }

    def _workspace_session(self, room: Session, bot_id: str, cfg: Config) -> Session | None:
        """Read an existing canonical context without creating its binding."""
        binding = self._runtime.workspaces.store.get_binding(bot_id, room.id)
        if binding is None or binding.internal_session_id is None:
            return None
        window = binding.window or self._webui.window_for(room)
        from thyca.bots.timeline import WorkspaceTimeline

        timeline = WorkspaceTimeline(self._root, bot_id, create=False)
        return timeline.project(window, session_id=binding.internal_session_id)

    def _overhead_tokens(
        self, session_id: str, *, turn_cfg: Config, bot_id: str,
        strict: bool = False,
    ) -> tuple[int, int]:
        """Estimate shared hot/tool overhead, excluding query-only inputs.

        The meter cannot know the next user text, mailbox batch, or
        query-specific post-compaction recall; those are omitted rather than
        fabricated. Runtime and meter use the same prepared-input composer.
        """
        tools_tokens = 0
        try:
            from thyca.bots.tasks import derive_open_tasks
            from thyca.bots.workspace_context import build_workspace_hot
            from thyca.memory.visibility import MemoryView, bound_memory_view
            from thyca.bots.runtime import filtered_tool_schemas

            room = self._sessions.store.load(session_id)
            window = self._webui.window_for(room)
            doc = self._bots.get(bot_id)
            card = doc.card if doc.ok else None
            filtered = filtered_tool_schemas(self._tools, card) if card else self._tools
            tools_tokens = _chars_to_tokens(
                json.dumps(filtered or [], ensure_ascii=False)
            )
            canonical = self._workspace_session(room, bot_id, turn_cfg)
            messages = list(canonical.messages) if canonical is not None else []
            room_delta = ""
            if room.dm_bot is None:
                room_delta, _ = self._room_delta_for(
                    bot_id, session_id, list(room.messages)
                )
            limits = turn_cfg.effective_limits()
            zone = ZoneInfo(turn_cfg.timeline.timezone)
            pending = PrivateFollowupStore(self._root).active(bot_id)
            with bound_memory_view(
                MemoryView(window=window, bot_id=bot_id, thyca_dir=self._root)
            ):
                hot = self._memory.peek(
                    self._state,
                    datetime.now(zone),
                    session_id=session_id,
                    reader=None if card is None or card.system or bot_id == "thyca" else bot_id,
                    tail_kb=limits.hotTailKB,
                    timezone_name=turn_cfg.timeline.timezone,
                )
                bot_tasks = derive_open_tasks(
                    messages,
                    self._runtime._pending_relevant(bot_id),
                    bot_id=bot_id,
                )
                memory_read = card is None or card.memory.read
                hot = build_workspace_hot(
                    hot,
                    thyca_dir=self._root,
                    bot_id=bot_id,
                    window=window,
                    card=card,
                    runtime_tasks=tuple(bot_tasks[:20]),
                    memory_catalog=(
                        tuple(self._memory_task_rows(bot_id)) if memory_read else ()
                    ),
                    room_delta=room_delta,
                    bot_user=(
                        self._bots.bot_user_text(bot_id) if memory_read else ""
                    ),
                    skills=self._bots.skill_index_text(bot_id),
                    team=self._bots.team_index_text(),
                    pending_followup=pending,
                )
            hot_tokens = _chars_to_tokens(PromptManager().build(hot))
        except Exception:
            if strict:
                raise
            hot_tokens = 0
        return hot_tokens, tools_tokens

    def _context_usage(self, session: Session, cfg: Config) -> dict:
        responder = session.dm_bot or self._resolve_responder(
            list(session.bots or ["thyca"]), ""
        )
        card = self._bots.require(responder)
        context_cfg = resolve_bot_cfg(cfg, card)
        canonical = self._workspace_session(session, responder, context_cfg)
        messages = list(canonical.messages) if canonical is not None else []
        model = context_cfg.defaultModel
        hot_tokens, tools_tokens = self._overhead_tokens(
            session.id, turn_cfg=context_cfg, bot_id=responder
        )
        return context_usage(
            messages,
            context_cfg.effective_limits().contextTokens,
            hot_tokens=hot_tokens,
            tools_tokens=tools_tokens,
            calibration=calibration_factor(messages, model=model),
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
