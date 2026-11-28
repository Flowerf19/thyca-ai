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

import hashlib
import inspect
import time
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from thyca.app.turn_options import resolve_bot_cfg, resolve_bot_provider
from thyca.conversations.runtime import ConversationManager
from thyca.conversations.runtime.wire import updated_at
from thyca.core.protocol import utc_now_ts
from thyca.llms.llm_factory import ConnectFactory
from thyca.memories import habits as mem_habits
from thyca.memories import jobs as mem_jobs
from thyca.memories import profile as mem_profile
from thyca.memories.facade import MemoryFacade
from thyca.memories.habits import habit_observations
from thyca.memories.identity import bind_bot_id, reset_bot_id
from thyca.memories.profile import format_card_line
from thyca.memories.visibility import MemoryView, bound_memory_view, current_memory_view

if TYPE_CHECKING:
    from thyca.app.webui_channel import WindowRef
    from thyca.bots.registry import BotRegistry
    from thyca.bots.runtime import BotRuntime
    from thyca.configs import Config
    from thyca.conversations.runtime import ConversationManager


@dataclass
class BackgroundJobsDeps:
    """Concrete collaborators for :class:`BackgroundJobsService`."""

    root: Path
    conversations: ConversationManager
    bots: BotRegistry
    runtime: BotRuntime
    turns: Any
    window_for: Callable[[Any], WindowRef]
    current_cfg: Callable[[], Config]
    construction_cfg: Config
    submit: Callable[[Any], Any]
    scheduled_turn: Callable[[str, str], dict]
    injected_connect: Any
    facade: Any = None


def _idle_watermark(messages: list, last_ts: str) -> tuple[str, int, str, str]:
    """Fingerprint the latest non-system message so unchanged idle turns do not re-flush."""
    non_system_count = sum(1 for msg in messages if getattr(msg, "role", None) != "system")
    for msg in reversed(messages):
        role = getattr(msg, "role", None)
        if role == "system":
            continue
        content = getattr(msg, "content", None) or ""
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        return (last_ts, non_system_count, str(role or ""), digest)
    return (last_ts, 0, "", "")


class BackgroundJobsService:
    """Sidecar jobs + memory hooks (see module doc)."""

    def __init__(self, deps: BackgroundJobsDeps) -> None:
        self._deps = deps
        self._job_facade = deps.facade
        self._profile_signatures = None
        self._profile_last_run = 0.0
        self._idle_swept: dict[tuple[str, str], tuple[str, int, str, str]] = {}

    async def _flush_bot_memory(self, bot_id: str, messages, turn_cfg,
                                room_id: str | None = None, *,
                                strict: bool = False) -> None:
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

        card = self._deps.bots.require(bot_id)
        msg_list = list(messages)
        binding = None
        if room_id:
            try:
                binding = self._deps.runtime.workspaces.store.get_binding(
                    bot_id, room_id
                )
            except Exception:
                binding = None
        after_cursor = binding.memory_cursor if binding is not None else None
        transcript = mem_jobs.transcript_tail(msg_list, after_cursor=after_cursor)
        if not transcript.strip():
            return
        next_cursor = mem_jobs.latest_extraction_cursor(msg_list)

        provider = resolve_bot_provider(turn_cfg, card)
        connect = self._deps.injected_connect or ConnectFactory.create(
            provider.api, provider
        )
        owns = self._deps.injected_connect is None
        try:
            facade = self.job_facade()
            bot_user_path = self._deps.bots.bot_dir(bot_id) / "USER.md"
            resolved_window = None
            if room_id and current_memory_view() is None:
                if binding is not None and binding.window is not None:
                    resolved_window = binding.window
                if resolved_window is None:
                    try:
                        room_conv = self._deps.conversations.store.load(room_id)
                        resolved_window = self._deps.window_for(room_conv)
                    except Exception:
                        resolved_window = None
            view_ctx = (
                bound_memory_view(
                    MemoryView(window=resolved_window, bot_id=bot_id, thyca_dir=self._deps.root)
                )
                if resolved_window is not None and current_memory_view() is None
                else nullcontext()
            )
            token = bind_bot_id(bot_id)
            try:
                with view_ctx:
                    open_tasks = facade.tasks(status="open", limit=50).get("tasks", [])
                    today_entries = facade.today_entries(chat=room_id, reader=bot_id)
                    known_habits = mem_habits.known_habit_labels(
                        facade, bot_id, bot_user_path
                    )
                    await mem_jobs.extract_memories(
                        connect,
                        facade,
                        transcript,
                        bot_id=bot_id,
                        open_tasks=open_tasks,
                        today_entries=today_entries,
                        known_habits=known_habits,
                        chat=room_id,
                        strict=True,
                    )
            except mem_jobs.ExtractionError:
                if strict:
                    raise
                return
            finally:
                reset_bot_id(token)
            if room_id and next_cursor is not None:
                try:
                    self._deps.runtime.workspaces.store.set_memory_cursor(
                        bot_id, room_id, next_cursor
                    )
                except Exception:
                    pass
            try:
                sweep_token = bind_bot_id(bot_id)
                try:
                    mem_habits.sweep_bot_habits(
                        facade, bot_id, bot_user_path
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

        facade = self._job_facade
        if facade is None:
            facade = MemoryFacade(
                self._deps.root,
                timezone_name=self._deps.construction_cfg.timeline.timezone,
            )
            self._job_facade = facade
        return facade

    def sweep_idle_conversations(self, idle_s: int = 900) -> dict:
        """Sweep idle canonical bot timelines once, under their workspace lease."""

        now_ts = utc_now_ts()
        running = set(self._deps.turns.snapshot())
        base_cfg = self._deps.current_cfg()
        bot_report: dict[str, dict] = {}

        async def _sweep_workspace_window(
            bot_id: str, window_id: str, bot_cfg
        ):
            async with self._deps.runtime.workspaces.lease(bot_id):
                binding = self._deps.runtime.workspaces.store.get_binding(
                    bot_id, window_id)
                if binding is None or binding.internal_conversation_id is None:
                    return None
                conversation_id = binding.internal_conversation_id
                window = binding.window
                if window is None:
                    try:
                        room_conversation = self._deps.conversations.store.load(window_id)
                        window = self._deps.window_for(room_conversation)
                    except Exception:
                        return None
                sweep_manager = ConversationManager(
                    limits=bot_cfg.effective_limits(),
                    timezone_name=bot_cfg.timeline.timezone,
                    store=self._deps.runtime._canonical_store(
                        bot_id, window, conversation_id),
                )
                try:
                    sweep_item = sweep_manager.load(conversation_id)
                    last_ts = updated_at(sweep_item)
                except Exception:
                    return None
                if not mem_jobs.should_idle_sweep(last_ts, now_ts, idle_s):
                    return None
                mark = _idle_watermark(list(sweep_item.messages), last_ts)
                if self._idle_swept.get((bot_id, window_id)) == mark:
                    return None
                entry = {"idle_since": last_ts}
                try:
                    flush_kwargs: dict[str, Any] = {"room_id": window_id}
                    try:
                        params = inspect.signature(self._flush_bot_memory).parameters
                        if "strict" in params or any(
                            p.kind is inspect.Parameter.VAR_KEYWORD
                            for p in params.values()
                        ):
                            flush_kwargs["strict"] = True
                    except (TypeError, ValueError):
                        pass
                    await self._flush_bot_memory(
                        bot_id, list(sweep_item.messages), bot_cfg,
                        **flush_kwargs,
                    )
                    sweep_manager.load(conversation_id)
                    compacted = sweep_manager.compact_manual()
                    entry["compacted"] = bool(compacted["compacted"])
                    if entry["compacted"]:
                        self._deps.runtime._reinject_pending.add(
                            (bot_id, window_id))
                    self._idle_swept[(bot_id, window_id)] = _idle_watermark(
                        list(sweep_manager.current.messages),
                        updated_at(sweep_manager.current),
                    )
                except Exception as exc:
                    entry["error"] = type(exc).__name__
                return f"{bot_id}:{conversation_id}", entry

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
        facade = self.job_facade()
        for doc in self._deps.bots.list():
            if not doc.ok or doc.card is None:
                continue
            bot_id = doc.card.bot_id
            try:
                bot_user_path = self._deps.bots.bot_dir(bot_id) / "USER.md"
                sweep_token = bind_bot_id(bot_id)
                try:
                    mem_habits.sweep_bot_habits(facade, bot_id, bot_user_path)
                finally:
                    reset_bot_id(sweep_token)
            except Exception:
                pass
        try:
            profiles_aggregated = self.aggregate_team_profiles()
        except Exception:
            profiles_aggregated = {"error": "profile aggregation failed"}
        return {"now": now_ts, "rooms": {}, "bots": bot_report,
                "profiles_aggregated": profiles_aggregated}

    def _user_direct_profile_observations(self) -> list[dict]:
        """kind='habit' observations the user stated directly (unowned or explicit to Thyca)."""

        try:
            records = habit_observations(self.job_facade(), None)
        except Exception:
            return []
        out: list[dict] = []
        for record in records:
            bots = record.get("bots")
            if bots is None or (
                isinstance(bots, (list, tuple))
                and "thyca" in bots
                and bool(record.get("habit_explicit"))
            ):
                out.append(record)
        return out

    def _profile_card_line(self, bot_id: str) -> str:

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
