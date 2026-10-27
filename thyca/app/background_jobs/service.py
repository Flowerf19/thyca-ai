"""Background jobs service: idle sweep, profile aggregation, memory hooks.

Owns the sidecar work ChatApp installs as BotRuntime hooks
(memory_flush/memory_searcher/memory_task_catalog), the scheduler's
idle sweep, and the daily team-profile aggregation. ChatApp keeps thin
wrappers with identical signatures; hook installs and scheduler calls
are untouched.

State ownership (explicit, single copy): the MemoryFacade cache and
the profile-aggregation signatures/timestamps live on the service
instance (same lifetime as the app: constructed once in __init__).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from thyca.app.turn_options import resolve_bot_cfg, resolve_bot_provider
from thyca.llm.llm_factory import ConnectFactory

if TYPE_CHECKING:
    from thyca.app.webui_channel import WindowRef
    from thyca.bots.registry import BotRegistry
    from thyca.bots.runtime import BotRuntime
    from thyca.config import Config
    from thyca.sessions import SessionManager


@dataclass
class BackgroundJobsDeps:
    """Concrete collaborators for :class:`BackgroundJobsService`."""

    root: Path
    sessions: SessionManager
    bots: BotRegistry
    runtime: BotRuntime
    turns: Any
    window_for: Callable[[Any], WindowRef]
    current_cfg: Callable[[], Config]
    construction_cfg: Config
    submit: Callable[[Any], Any]
    scheduled_turn: Callable[[str, str], dict]
    injected_connect: Any


class BackgroundJobsService:
    """Sidecar jobs + memory hooks (see module doc)."""

    def __init__(self, deps: BackgroundJobsDeps) -> None:
        self._deps = deps
        self._job_facade = None
        self._profile_signatures = None
        self._profile_last_run = 0.0

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

        # Bot-specific extraction requires the bot's valid card: a missing
        # or invalid card (or settings) must surface through the caller's
        # best-effort handler, never silently fall back to global config.
        card = self._deps.bots.require(bot_id)
        provider = resolve_bot_provider(turn_cfg, card)
        connect = self._deps.injected_connect or ConnectFactory.create(
            provider.api, provider
        )
        owns = self._deps.injected_connect is None
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
                mem_jobs.apply_extracted(
                    facade, extracted, default_bot=bot_id,
                    open_tasks=open_tasks, chat=room_id,
                )
            # Phase 1E habit sweep (best-effort, never breaks the flush):
            # newly-qualified habit labels append to this bot's own USER.md.
            try:
                from thyca.memory import habits as mem_habits

                sweep_token = bind_bot_id(bot_id)
                try:
                    mem_habits.sweep_bot_habits(
                        facade, bot_id, self._deps.bots.bot_dir(bot_id) / "USER.md"
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

    def job_facade(self):
        """Memory facade for the sidecar job (extract/candidates/promote).

        Lazily built and cached: the constructor reindexes, so sharing one
        instance beats rebuilding it per sweep. Locks are shared process-wide."""
        from thyca.memory.facade import MemoryFacade

        facade = self._job_facade
        if facade is None:
            facade = MemoryFacade(
                self._deps.root,
                timezone_name=self._deps.construction_cfg.timeline.timezone,
            )
            self._job_facade = facade
        return facade

    def sweep_idle_sessions(self, idle_s: int = 900) -> dict:
        """Sweep idle canonical bot timelines once, under their workspace lease."""
        from thyca.core.protocol import utc_now_ts
        from thyca.memory import jobs as mem_jobs
        from thyca.sessions import SessionManager
        from thyca.sessions.wire import updated_at

        now_ts = utc_now_ts()
        running = set(self._deps.turns.snapshot())
        base_cfg = self._deps.current_cfg()
        # Every bot, including Thyca, is swept only through canonical
        # workspace timelines; public room transcripts are read models.
        bot_report: dict[str, dict] = {}

        async def _sweep_workspace_window(
            bot_id: str, window_id: str, bot_cfg
        ):
            async with self._deps.runtime.workspaces.lease(bot_id):
                # Bindings and transcript may have changed while waiting
                # for the lease. Never compact a pre-lease snapshot.
                binding = self._deps.runtime.workspaces.store.get_binding(
                    bot_id, window_id)
                if binding is None or binding.internal_session_id is None:
                    return None
                session_id = binding.internal_session_id
                window = binding.window
                if window is None:
                    try:
                        room_session = self._deps.sessions.store.load(window_id)
                        window = self._deps.window_for(room_session)
                    except Exception:
                        return None
                sweep_manager = SessionManager(
                    limits=bot_cfg.effective_limits(),
                    timezone_name=bot_cfg.timeline.timezone,
                    store=self._deps.runtime._canonical_store(
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
                        self._deps.runtime._reinject_pending.add(
                            (bot_id, window_id))
                except Exception as exc:
                    entry["error"] = type(exc).__name__
                return f"{bot_id}:{session_id}", entry

        for doc in self._deps.bots.list():
            if not doc.ok or doc.card is None:
                continue
            bot_id = doc.card.bot_id
            bot_cfg = resolve_bot_cfg(base_cfg, doc.card)
            try:
                bindings = self._deps.runtime.workspaces.store.load(bot_id)
            except Exception as exc:
                bot_report[bot_id] = {"error": type(exc).__name__}
                continue
            if not bindings:
                continue
            for window_id in bindings:
                if window_id in running or self._deps.runtime.workspaces.busy(bot_id):
                    continue
                try:
                    result = self._deps.submit(
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

    def _profile_card_line(self, bot_id: str) -> str:
        from thyca.memory.profile import format_card_line

        try:
            card = self._deps.bots.require(bot_id)
        except Exception:
            return ""
        return format_card_line(card.role, card.description, card.persona)

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
            for doc in self._deps.bots.list():
                if doc.ok and doc.card is not None:
                    paths[doc.card.bot_id] = (
                        self._deps.bots.bot_dir(doc.card.bot_id) / "USER.md"
                    )
            current: dict[str, tuple[int, int] | None] = {}
            for bot_id, path in paths.items():
                try:
                    stat = path.stat() if path.is_file() else None
                except OSError:
                    stat = None
                current[bot_id] = (
                    None if stat is None else (stat.st_mtime_ns, stat.st_size))
            previous = self._profile_signatures
            now = time.time()
            last = self._profile_last_run
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
            shared = self._deps.root / "USER.md"
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
            prompt = mem_profile.aggregation_prompt(
                blocks, user_direct, current_profile, self._profile_card_line
            )
            detail = self._deps.scheduled_turn("thyca", prompt)
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
