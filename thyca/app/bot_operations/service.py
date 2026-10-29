"""Bot read-model/operations service: canonical-workspace reads + metering.

The queries behind the operations/trace surfaces — execution-row reads,
per-participant operations payloads, canonical projections, hot/tool
overhead estimates, context composition, and the session detail payload.
Pure orchestration over injected collaborators; owns no turns, sessions,
or bus writes. ChatApp keeps thin delegating wrappers with identical
signatures so serve/ and internal callers are untouched.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from zoneinfo import ZoneInfo

from thyca.bots.private_followups import PrivateFollowupStore
from thyca.bots.registry import RegistryError
from thyca.core.protocol import Message
from thyca.sessions import Session, calibration_factor, context_usage
from thyca.sessions.wire import session_detail
from thyca.app.turn_options import (
    overlay_turn_cfg,
    resolve_bot_cfg,
    resolve_bot_provider,
)

if TYPE_CHECKING:
    from thyca.app.webui_channel import WindowRef
    from thyca.bots.registry import BotRegistry
    from thyca.config import Config
    from thyca.memory.active import ActiveMemory, ActiveState
    from thyca.serve.turn_state import TurnState
    from thyca.sessions import SessionManager


@dataclass
class BotOperationsDeps:
    """Concrete collaborators for :class:`BotOperationsService`.

    Values are captured once (ChatApp never rebinds them) except
    ``tools_fn``, which must read the live tool schema snapshot:
    ChatApp rebuilds ``self._tools`` on MCP hot-reload.
    """

    root: Path
    sessions: SessionManager
    window_for: Callable[[Session], WindowRef]
    bots: BotRegistry
    memory: ActiveMemory
    state: ActiveState
    tools_fn: Callable[[], list]
    workspaces_store: Any
    current_cfg: Callable[[], Config]
    room_delta_for: Callable[..., tuple[str, Any]]
    memory_task_rows: Callable[[str], list]
    resolve_responder: Callable[..., str]
    pending_relevant: Callable[[str], list]
    turns: TurnState
    agent_status: Any
    skills_root: Path


class BotOperationsService:
    """Read-model queries over canonical bot workspaces (see module doc)."""

    def __init__(self, deps: BotOperationsDeps) -> None:
        self._deps = deps

    def room_tasks(self, session_id: str, bot_id: str) -> dict:
        """Open/waiting runtime tasks plus live calls for one participant.

        ``tasks`` are the runtime units from
        :func:`thyca.bots.tasks.derive_open_tasks` over the bot's CURRENT
        projected view of this exact window (its canonical journal
        projection, which survives room compaction and carries in-flight
        rows): task_open/delegate opens, pending envelopes owed in this
        window, minus completion records. Completed work and the
        saved-memory task catalog never appear here. When the bot never
        ran in this window there is genuinely nothing to project, so only
        queued envelopes can owe it work; an unreadable journal for a
        bound bot is unavailable, never empty. Room rows are the shared
        aggregate read model (all senders/recipients) and are never
        used, so another bot's work or private rows cannot leak in.
        ``calls`` are the ephemeral in-memory tool/skill invocations for
        the collapsed preview/+N list only — merely starting a tool never
        creates a task row. Read-only: no bindings created, no migration.
        Membership and room existence follow the operations route
        conventions (RegistryError/SessionNotFound)."""
        room = self._deps.sessions.store.load(session_id)
        window = self._deps.window_for(room)
        if bot_id not in window.participants:
            raise RegistryError("bot is not a participant in this room")
        self._deps.bots.require(bot_id)
        payload = self._deps.agent_status.room_tasks(bot_id, session_id)
        payload["tasks"] = self._room_task_units(window, room, bot_id)
        return payload

    def _room_task_units(self, window, room, bot_id: str) -> list[dict]:
        """Narrow serialized open tasks for exactly one bot+window."""
        from thyca.bots.tasks import derive_open_tasks
        from thyca.bots.timeline import CanonicalSessionStore, WorkspaceTimeline

        binding = self._deps.workspaces_store.get_binding(bot_id, room.id)
        if binding is None or binding.internal_session_id is None:
            # Genuinely uninitialized window: the bot never ran here, so
            # its projected view is empty by construction — only queued
            # envelopes (filtered to this exact session below) can owe
            # it work. Never derive from room rows: they aggregate every
            # sender/recipient and would misattribute other bots' tasks.
            messages: list = []
        else:
            # Canonical projection for this bot+window: post-compaction
            # basis plus replay, in journal order. Construction AND read
            # failures propagate (503 unavailable) instead of asserting
            # an empty list on a failed read.
            timeline = WorkspaceTimeline(self._deps.root, bot_id, create=False)
            store = CanonicalSessionStore(
                self._deps.root / "bots" / bot_id / "sessions",
                timeline=timeline, window=window,
                session_id=binding.internal_session_id)
            messages = list(store.load(binding.internal_session_id).messages)
        pending = [
            envelope for envelope in self._deps.pending_relevant(bot_id)
            if getattr(envelope, "session_id", None) == room.id
        ]
        units = derive_open_tasks(messages, pending, bot_id=bot_id)
        return [
            {"title": unit.task, "status": unit.status,
             "waiting_on": unit.waiting_on}
            for unit in units
        ]

    def trace_execution_messages(
        self, session_id: str, bot_id: str, span_id: str
    ) -> list[Message]:
        """Read canonical execution rows for a trusted room/bot/span tuple."""
        from thyca.bots.timeline import TraceExecutionError, WorkspaceTimeline

        room = self._deps.sessions.store.load(session_id)
        window = self._deps.window_for(room)
        if bot_id not in window.participants:
            raise TraceExecutionError(
                "execution bot is not a participant in the referenced room"
            )
        binding = self._deps.workspaces_store.get_binding(bot_id, session_id)
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
            self._deps.root / "bots" / bot_id / "workspace" / "timeline.jsonl"
        )
        if not timeline_path.is_file():
            raise TraceExecutionError(
                "canonical execution timeline is missing for this room and bot"
            )
        try:
            timeline = WorkspaceTimeline(self._deps.root, bot_id, create=False)
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

        room = self._deps.sessions.store.load(session_id)
        window = self._deps.window_for(room)
        if bot_id not in window.participants:
            raise RegistryError("bot is not a participant in this room")
        card = self._deps.bots.require(bot_id)
        base = self._deps.current_cfg()
        if model is not None:
            base = overlay_turn_cfg(base, model, None)
        cfg = resolve_bot_cfg(base, card, model_override=model)
        provider = resolve_bot_provider(cfg, card, effort_override=effort)
        effective_effort = provider.reasoningEffort
        canonical = self._workspace_session(room, bot_id, cfg)
        messages = list(canonical.messages) if canonical is not None else []
        history = []
        if canonical is not None:
            timeline = WorkspaceTimeline(self._deps.root, bot_id, create=False)
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
        binding = self._deps.workspaces_store.get_binding(bot_id, room.id)
        if binding is None or binding.internal_session_id is None:
            return None
        window = binding.window or self._deps.window_for(room)
        from thyca.bots.timeline import WorkspaceTimeline

        timeline = WorkspaceTimeline(self._deps.root, bot_id, create=False)
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
            from thyca.bots.workspace_context import build_workspace_hot, overhead_tokens
            from thyca.memory.visibility import MemoryView, bound_memory_view
            from thyca.bots.runtime import filtered_tool_schemas

            room = self._deps.sessions.store.load(session_id)
            window = self._deps.window_for(room)
            doc = self._deps.bots.get(bot_id)
            card = doc.card if doc.ok else None
            filtered = filtered_tool_schemas(self._deps.tools_fn(), card) if card else self._deps.tools_fn()
            _, tools_tokens = overhead_tokens(tool_schemas=filtered)
            canonical = self._workspace_session(room, bot_id, turn_cfg)
            messages = list(canonical.messages) if canonical is not None else []
            room_delta = ""
            if room.dm_bot is None:
                room_delta, _ = self._deps.room_delta_for(
                    bot_id, session_id, list(room.messages)
                )
            limits = turn_cfg.effective_limits()
            zone = ZoneInfo(turn_cfg.timeline.timezone)
            pending = PrivateFollowupStore(self._deps.root).active(bot_id)
            with bound_memory_view(
                MemoryView(window=window, bot_id=bot_id, thyca_dir=self._deps.root)
            ):
                hot = self._deps.memory.peek(
                    self._deps.state,
                    datetime.now(zone),
                    session_id=session_id,
                    reader=None if card is None or card.system or bot_id == "thyca" else bot_id,
                    tail_kb=limits.hotTailKB,
                    timezone_name=turn_cfg.timeline.timezone,
                )
                bot_tasks = derive_open_tasks(
                    messages,
                    self._deps.pending_relevant(bot_id),
                    bot_id=bot_id,
                )
                memory_read = card is None or card.memory.read
                hot = build_workspace_hot(
                    hot,
                    thyca_dir=self._deps.root,
                    bot_id=bot_id,
                    window=window,
                    card=card,
                    runtime_tasks=tuple(bot_tasks[:20]),
                    memory_catalog=(
                        tuple(self._deps.memory_task_rows(bot_id)) if memory_read else ()
                    ),
                    room_delta=room_delta,
                    bot_user=(
                        self._deps.bots.bot_user_text(bot_id) if memory_read else ""
                    ),
                    skills=self._deps.bots.skill_index_text(bot_id),
                    team=self._deps.bots.team_index_text(),
                    pending_followup=pending,
                )
            hot_tokens, _ = overhead_tokens(hot=hot)
        except Exception:
            if strict:
                raise
            hot_tokens = 0
        return hot_tokens, tools_tokens

    def _context_usage(self, session: Session, cfg: Config) -> dict:
        responder = session.dm_bot or self._deps.resolve_responder(
            list(session.bots or ["thyca"]), ""
        )
        card = self._deps.bots.require(responder)
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
        started_at = None if running is not None else self._deps.turns.started_at(session.id)
        return session_detail(
            session,
            cfg,
            skills_root=self._deps.skills_root,
            running=running,
            started_at=started_at,
            context=self._context_usage(session, cfg),
        )
