"""Bot runtime: one shared AgentLoop architecture, configured per card.

Every bot turn is an instance of the existing ``build_agent_loop`` wiring
(master §1: no new loop engine), with model/tools/snapshot taken from the
bot's Agent Card. Each bot keeps one internal session per room (plan B);
continuity lives in the bot's memory/skills, not in a lifelong transcript.
"""
from __future__ import annotations

import asyncio
import inspect
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from thyca.agent.compacting import compact_with_llm
from thyca.agent.events import EventSink, wire_retry_events
from thyca.bots.bus import MAX_REF_CHARS, MAX_REFS, Bus, Envelope, new_span_id
from thyca.bots.card import BASELINE_BOT_TOOLS, BotCard, identity_text
from thyca.bots.context import (
    BotTurnContext,
    BotTurnScope,
    bind_bot_turn_context,
    current_bot_turn_context,
    reset_bot_turn_context,
)
from thyca.bots.policy import (
    bind_allowed_tools,
    bind_hidden_tools,
    reset_allowed_tools,
    reset_hidden_tools,
)
from thyca.bots.registry import BotRegistry, RegistryError
from thyca.bots.status import AgentStatusTracker
from thyca.bots.workspace import WorkspaceRegistry, WorkspaceTurnConflict
from thyca.config import Config
from thyca.core.channel import WindowRef
from thyca.core.protocol import Message
from thyca.llm.llm_base import LLMError
from thyca.llm.llm_factory import ConnectFactory
from thyca.memory.identity import bind_bot_id, reset_bot_id
from thyca.sessions import SessionManager, calibration_factor
from thyca.sessions.store import SessionStore
from thyca.tools.memory_tools import bind_chat_session, reset_chat_session

#: Admin tools (BE-5, ``bot_*``) live only in Thyca's registry: child-bot
#: tool snapshots drop every name with this prefix. The prefix (not an
#: import) is the seam so this module never cycles on admin_tools.
ADMIN_TOOL_PREFIX = "bot_"

MEMORY_READ_TOOLS = frozenset({"memory_search", "memory_recent", "memory_get", "memory_tasks"})
MEMORY_WRITE_TOOLS = frozenset(
    {"memory_remember", "memory_update", "memory_forget", "memory_reinforce"}
)

#: Peer tools available to child bots (sender identity comes from the
#: in-flight turn context, never from arguments). Exempt from the ``bot_*``
#: admin-prefix drop, but still subject to the fail-closed allowlist.
#: ``bot_fetch`` (CBE-3) reads full envelope bodies by span_id;
#: ``task_open`` (CBE-4) records an open-task event in the transcript.
SEND_TOOL_NAME = "bot_send"
PEER_TOOL_NAMES = frozenset({"bot_send", "bot_fetch", "task_open", "message_user", "ask_user", "report_error"})


def _layered_soul(shared_soul: str, persona: str) -> str:
    """Compose the ``<role>`` section: shared soul + this twin's overlay.

    Every twin (Thyca included) rides the shared SOUL.md; the card's
    persona body is appended as the bot's own working notes and wins
    where it conflicts. Empty persona simply leaves the shared soul —
    there is deliberately no fallback direction to invert.
    """
    own = (persona or "").strip()
    shared = (shared_soul or "").strip()
    if not own:
        return shared_soul
    overlay = (
        "## Your own working notes\n\n"
        "These come from your own TWIN.md. They refine the shared soul "
        "above for you specifically and win where they conflict.\n\n"
        f"{own}"
    )
    if not shared or shared == "# Soul":
        return overlay
    return f"{shared}\n\n{overlay}"


def filtered_tool_schemas(
    schemas: list[dict] | None, card: BotCard
) -> list[dict] | None:
    """Apply the card's allowlist + hidden_tools (+ role defaults).

    Rules (master §2): ``hidden_tools`` wins over ``tools``; child bots
    never see ``bot_*`` admin tools (``bot_send``, the peer-send tool, is
    the one exception); ``memory{read,write}`` and
    ``bash: deny`` drop their tool families.

    Fail-closed for non-system bots: ``tools`` *is* the allowlist, so an
    empty ``tools`` means NO tools at all — nothing is implicit, and the
    gateway poll tools (``tool_read``/``tool_kill``) stay visible only
    when the card lists them explicitly. The Thyca system card keeps the
    full snapshot (root/tools parity).

    One exception, recorded by the context review: the current
    ``BASELINE_BOT_TOOLS`` (peer send/fetch, ``task_open``, memory
    basics) are always kept for non-system bots, even when a card was
    written before a baseline tool existed and never listed it — a
    pre-existing card must gain ``bot_fetch``/``task_open`` on load, not
    only at creation. ``hidden_tools`` and the memory/bash policies
    above still win: they removed their names from ``keep`` before this
    allowlist is applied, so a baseline name they dropped stays dropped.
    """
    if schemas is None:
        return None
    names = {schema.get("function", {}).get("name") for schema in schemas}
    keep: set[str] = set(names) - set(card.hidden_tools)
    if not card.system:
        keep = {
            name
            for name in keep
            if name in PEER_TOOL_NAMES or not str(name).startswith(ADMIN_TOOL_PREFIX)
        }
    if not card.memory.read:
        keep -= MEMORY_READ_TOOLS
    if not card.memory.write:
        keep -= MEMORY_WRITE_TOOLS
    if card.bash == "deny":
        keep.discard("bash")
    if not card.system:
        # Fail-closed: the card's list plus the always-kept baseline
        # (see docstring) is exhaustive for child bots.
        keep &= set(card.tools) | set(BASELINE_BOT_TOOLS)
    elif card.tools:
        keep &= set(card.tools) | {"tool_read", "tool_kill"}
    # NOTE: card.bash == "ask" keeps the bash tool: the ask-approval gate
    # is future UX work, not a silent deny (honest default, recorded here).
    return [
        schema
        for schema in schemas
        if schema.get("function", {}).get("name") in keep
    ]


def _force_compact(connect, sessions, model: str, turn_cfg) -> dict:
    """Agent-invoked compaction of the current session (TURN-CURATE).

    ``LlmSummarizer`` lives under ``thyca.app`` which imports this module,
    so it is imported lazily at call time (mid-turn, all modules loaded).
    """
    from thyca.app.summarizing import LlmSummarizer

    return compact_with_llm(
        LlmSummarizer(connect),
        sessions,
        model=model,
        pricing=turn_cfg.effective_pricing() or None,
        force=True,
    )


def _segment_checkpointed(messages, context_tokens: int) -> bool:
    """True when the agent recorded a checkpoint in the current segment.

    Reuses the context-budget evidence rule (kind='checkpoint' + task link
    after the last compaction marker); band freshness is irrelevant here —
    any checkpoint means the agent already wrote what matters, so the
    system's mono extraction can stand down.
    """
    from thyca.agent.context_budget import checkpoint_state

    return checkpoint_state(
        messages,
        context_tokens=context_tokens,
        # Band 0: every recorded checkpoint counts, stale or not.
        current_ratio=0.0,
    ) != "none"


from dataclasses import dataclass as _dataclass


@_dataclass
class BotTurnResult:
    """One bot turn's reply plus its aggregated assistant-meta totals.

    The app layer stamps these onto the room transcript row so serve
    trace ``by_bot`` and the FE cost grouping see the internal turn's
    real model/usage/cost/latency instead of an unattributed shell."""

    reply: str
    bot_id: str
    span_id: str
    bot_name: str | None = None
    bot_tint: str | None = None
    model: str | None = None
    usage: dict | None = None
    cost_usd: float | None = None
    latency_ms: int | None = None
    #: Real model calls inside the internal turn (assistant rows minus
    #: context-stop markers); stamped onto the room row so trace counts
    #: the bot's true requests instead of one row.
    requests: int = 0
    rounds: int = 0
    status: str | None = None
    #: Real tool calls inside the internal turn (assistant rows minus
    #: context-stop markers); stamped onto the room row like requests.
    tool_calls: int = 0
    channel: str | None = None
    window_id: str | None = None
    work_id: str | None = None

    def room_meta(self, *, kind: str, trace_id: str | None,
                  parent_span_id: str | None) -> dict:
        meta: dict = {
            "bot_id": self.bot_id,
            "kind": kind,
            "trace_id": trace_id,
            "span_id": self.span_id,
            "parent_span_id": parent_span_id,
        }
        if self.bot_name:
            meta["bot_name"] = self.bot_name
        if self.bot_tint:
            meta["bot_tint"] = self.bot_tint
        if self.model is not None:
            meta["model"] = self.model
        if self.usage is not None:
            meta["usage"] = self.usage
        if self.cost_usd is not None:
            meta["cost_usd"] = self.cost_usd
        if isinstance(self.requests, int) and self.requests >= 0:
            meta["requests"] = self.requests
        if isinstance(self.tool_calls, int) and self.tool_calls >= 0:
            meta["tool_call_count"] = self.tool_calls
        if isinstance(self.rounds, int) and self.rounds >= 0:
            meta["rounds"] = self.rounds
        if self.status is not None:
            meta["status"] = self.status
        if self.latency_ms is not None:
            meta["latency_ms"] = self.latency_ms
        if self.channel:
            meta["channel"] = self.channel
        if self.window_id:
            meta["window_id"] = self.window_id
        if self.work_id:
            meta["work_id"] = self.work_id
        return meta


def _compaction_markers(messages: list) -> int:
    return sum(
        1
        for message in messages
        if getattr(message, "role", None) == "system"
        and str(getattr(message, "content", None) or "").startswith("[compaction: ")
    )


def _aggregate_turn_meta(messages: list) -> tuple[str | None, dict | None, float | None, int | None, int, int]:
    """Sum model/usage/cost/latency over one turn's assistant rows.

    Also counts real model calls and real tool calls: same row set, minus
    context-stop markers (which made no provider call)."""
    model: str | None = None
    usage: dict | None = None
    cost: float | None = None
    latency: int | None = None
    requests = 0
    tool_calls = 0
    for message in messages:
        if getattr(message, "role", None) != "assistant":
            continue
        meta = getattr(message, "meta", None) or {}
        if meta.get("status") in {"context_limit", "loop_limit"}:
            continue
        if isinstance(meta.get("model"), str) and meta["model"].strip():
            model = meta["model"].strip()
        raw_usage = meta.get("usage")
        if isinstance(raw_usage, dict):
            if usage is None:
                usage = {}
            for key, value in raw_usage.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    usage[key] = usage.get(key, 0) + value
        if isinstance(meta.get("cost_usd"), (int, float)):
            cost = (cost or 0.0) + float(meta["cost_usd"])
        if isinstance(meta.get("latency_ms"), int) and meta["latency_ms"] >= 0:
            latency = (latency or 0) + meta["latency_ms"]
        requests += 1
        calls = getattr(message, "tool_calls", None) or []
        tool_calls += len(calls)
    return model, usage, cost, latency, requests, tool_calls


def _aggregate_turn_rounds(messages: list) -> int:
    return sum(
        1
        for message in messages
        if getattr(message, "role", None) == "assistant"
        and (getattr(message, "meta", None) or {}).get("kind") == "llm"
        and (getattr(message, "meta", None) or {}).get("status")
        not in {"context_limit", "loop_limit"}
    )


class BotRuntime:
    """Per-bot loop factory + internal (bot × room) sessions + bus send."""

    #: Async delivery hook installed by ChatApp:
    #: ``await deliverer(envelope, ctx) -> reply text``. When set, send
    #: tools run the recipient's turn immediately; unset (unit tests,
    #: direct handler calls) envelopes stay queued for the next turn.
    deliverer: Any = None
    #: CBE-4 hooks installed by ChatApp (fakes in unit tests):
    #: ``memory_flush(bot_id, messages, turn_cfg, room_id=...)`` extracts
    #: memories from
    #: a bot transcript about to compact; ``memory_searcher(text, bot_id)``
    #: returns bounded recalled-memory lines for the first post-compact
    #: turn. Either may be sync or async; both are best-effort.
    memory_flush: Any = None
    memory_searcher: Any = None
    #: Phase 1C hook installed by ChatApp: ``memory_task_index(bot_id)``
    #: returns the open memory-task catalog lines for one bot's prompt.
    #: Sync or async; best-effort like the CBE-4 hooks above.
    memory_task_index: Any = None
    memory_task_catalog: Any = None

    def __init__(
        self,
        thyca_dir: Path,
        registry: BotRegistry,
        bus: Bus,
        status_tracker: AgentStatusTracker | None = None,
    ) -> None:
        self._dir = Path(thyca_dir)
        self._registry = registry
        self._bus = bus
        # Live agent status (GET /api/bots merges snapshots into cards).
        # ChatApp injects its shared tracker; direct runtime users get a
        # private one so tracked turns never leak between app instances.
        self._status = status_tracker or AgentStatusTracker()
        self._workspaces = WorkspaceRegistry(self._dir)
        # (bot_id, room_id) pairs whose last turn compacted: the next turn
        # re-injects relevant memory hits exactly once (CBE-4).
        self._reinject_pending: set[tuple[str, str]] = set()

    @property
    def status_tracker(self) -> AgentStatusTracker:
        return self._status

    @property
    def workspaces(self) -> WorkspaceRegistry:
        """Authoritative workspace registry (WS-1): window bindings."""
        return self._workspaces

    def _canonical_store(
        self, bot_id: str, window: WindowRef, session_id: str
    ) -> CanonicalSessionStore:
        """Canonical journal store for one ``(bot, window)`` turn."""
        from thyca.bots.timeline import CanonicalSessionStore, WorkspaceTimeline

        return CanonicalSessionStore(
            self._dir / "bots" / bot_id / "sessions",
            timeline=WorkspaceTimeline(self._dir, bot_id),
            window=window,
            session_id=session_id,
        )

    def internal_session(self, bot_id: str, room_id: str) -> Session:
        """The bot's canonical session for one room (created on first use).

        The registry carries the stable session id addressing the bot's
        timeline stream; the transcript itself lives in the journal (no
        session file is created). Real window metadata binds when the room
        resolves to one. Synthetic rooms receive a private bot view.
        """
        binding = self._workspaces.store.ensure_session(bot_id, room_id)
        window = self._window_for_room(room_id)
        if window is None:
            window = WindowRef(
                window_id=room_id, channel_id="webui", kind="dm",
                participants=(bot_id,),
            )
        if binding.window != window:
            binding = self._workspaces.store.bind_window(bot_id, window)
        return self._canonical_store(
            bot_id, window, binding.internal_session_id).load(
                binding.internal_session_id)

    async def run_bot_turn(
        self,
        *,
        scope: BotTurnScope,
        card: BotCard,
        room_id: str,
        text: str,
        turn_cfg: Config,
        event_sink: EventSink | None = None,
        trace_id: str | None = None,
        parent_span_id: str | None = None,
        span_id: str | None = None,
        room_text: str = "",
        room_cursor_after: tuple[int, str] | None = None,
        retry: bool = False,
        model_override: str | None = None,
        effort: str | None = None,
    ) -> str:
        """Run one bot turn; return only the reply text."""
        result = await self.run_bot_turn_detailed(
            scope=scope, card=card, room_id=room_id, text=text,
            turn_cfg=turn_cfg, event_sink=event_sink, trace_id=trace_id,
            parent_span_id=parent_span_id, span_id=span_id,
            room_text=room_text,
            room_cursor_after=room_cursor_after,
            retry=retry,
            model_override=model_override,
            effort=effort,
        )
        return result.reply

    async def run_bot_turn_detailed(
        self,
        *,
        scope: BotTurnScope,
        card: BotCard,
        room_id: str,
        text: str,
        turn_cfg: Config,
        event_sink: EventSink | None = None,
        trace_id: str | None = None,
        parent_span_id: str | None = None,
        span_id: str | None = None,
        room_text: str = "",
        room_cursor_after: tuple[int, str] | None = None,
        workspace_claimed: bool = False,
        retry: bool = False,
        model_override: str | None = None,
        effort: str | None = None,
        diagnostic_sink=None,
    ) -> BotTurnResult:
        """Lease-gated entry: one active turn per bot (WS-2).

        ``workspace_claimed=True`` is an internal contract from callers
        that already hold this bot's lease for the current task — it is
        validated against the registry, never trusted blindly."""
        if workspace_claimed:
            if not self._workspaces.holds(card.bot_id):
                raise WorkspaceTurnConflict(
                    f"workspace_claimed without lease: {card.bot_id}"
                )
            self._registry.require(card.bot_id)
        from thyca.app.turn_options import overlay_turn_cfg, resolve_bot_cfg
        operation_cfg = turn_cfg
        turn_cfg = resolve_bot_cfg(
            operation_cfg, card, model_override=model_override
        )
        turn_cfg = overlay_turn_cfg(turn_cfg, None, effort)
        from thyca.memory.visibility import (
            MemoryView,
            bound_memory_view,
            current_memory_view,
        )

        current = current_memory_view()
        if (
            current is not None
            and current.bot_id == card.bot_id
            and current.window is not None
            and current.window.window_id == room_id
        ):
            view = current
        else:
            view = MemoryView(
                window=self._window_for_room(room_id),
                bot_id=card.bot_id,
                thyca_dir=self._dir,
            )
        if workspace_claimed:
            with bound_memory_view(view):
                return await self._run_bot_turn_detailed(
                    scope=scope, card=card, room_id=room_id, text=text,
                    turn_cfg=turn_cfg, context_cfg=operation_cfg,
                    event_sink=event_sink, trace_id=trace_id,
                    parent_span_id=parent_span_id, span_id=span_id,
                    room_text=room_text,
                    room_cursor_after=room_cursor_after,
                    retry=retry,
                    model_override=model_override,
                    effort=effort,
                    diagnostic_sink=diagnostic_sink,
                )
        async with self._workspaces.lease(card.bot_id):
            # A queued turn must not recreate workspace state after deletion.
            self._registry.require(card.bot_id)
            with bound_memory_view(view):
                return await self._run_bot_turn_detailed(
                    scope=scope, card=card, room_id=room_id, text=text,
                    turn_cfg=turn_cfg, context_cfg=operation_cfg,
                    event_sink=event_sink, trace_id=trace_id,
                    parent_span_id=parent_span_id, span_id=span_id,
                    room_text=room_text,
                    room_cursor_after=room_cursor_after,
                    retry=retry,
                    model_override=model_override,
                    effort=effort,
                    diagnostic_sink=diagnostic_sink,
                )

    def _window_for_room(self, room_id: str):
        """WindowRef for a real room session; None for synthetic rooms.

        Only a genuinely missing session means "synthetic room -> private
        fallback"; corrupt/permission failures propagate rather than
        silently downgrade."""
        from thyca.core.channel import WindowRef
        from thyca.sessions.errors import SessionNotFound

        try:
            session = SessionStore(self._dir / "sessions").load(room_id)
        except (SessionNotFound, FileNotFoundError):
            return None
        return WindowRef(
            window_id=session.id,
            channel_id="webui",
            kind="dm" if session.dm_bot is not None else "group",
            participants=tuple(session.bots) if session.bots else ("thyca",),
        )

    async def _run_bot_turn_detailed(
        self,
        *,
        scope: BotTurnScope,
        card: BotCard,
        room_id: str,
        text: str,
        turn_cfg: Config,
        context_cfg: Config,
        event_sink: EventSink | None = None,
        trace_id: str | None = None,
        parent_span_id: str | None = None,
        span_id: str | None = None,
        room_text: str = "",
        room_cursor_after: tuple[int, str] | None = None,
        retry: bool = False,
        model_override: str | None = None,
        effort: str | None = None,
        diagnostic_sink=None,
    ) -> BotTurnResult:
        """Run one turn of ``card`` in its canonical session for ``room_id``.

        ``scope`` is the app's :class:`BotTurnScope` (act/memory/state/zone +
        connect); the session store, tool snapshot, hot prompt, model, and
        attribution context are the bot's own. Returns the reply plus the
        internal turn's aggregated assistant meta for room attribution.

        ``room_text`` is the unread public room delta for this turn: it
        rides the wire ephemerally (never persisted — the registry cursor
        advances past it only on a successful turn).
        """
        if card.paused:
            raise RegistryError(f"bot is paused: {card.bot_id}")
        # Status begins before any setup that can fail or wait (store,
        # connect, loop build, memory flush): a setup failure is still
        # this bot's failed turn, and preparation time is working time.
        own_span = span_id or new_span_id()
        self._status.begin_turn(
            card.bot_id, room_id, trace_id=trace_id, span_id=own_span
        )
        raw_sink = event_sink

        def tracked_sink(event, _sink=raw_sink) -> None:
            self._status.observe_event(card.bot_id, own_span, event)
            if _sink is not None:
                _sink(event)

        try:
            from thyca.memory.visibility import current_memory_view

            _window = None
            _view = current_memory_view()
            if _view is not None:
                _window = _view.window
            if _window is None:
                _window = self._window_for_room(room_id)
            if _window is None:
                # Synthetic room (A2A without a room): a private window
                # scoped to this bot, matching the private-view fallback.
                from thyca.core.channel import WindowRef

                _window = WindowRef(
                    window_id=room_id, channel_id="webui", kind="dm",
                    participants=(card.bot_id,),
                )
            # Real window metadata binds inside internal_session;
            # synthetic rooms keep binding.window unset (projection
            # scoping uses the local _window either way). NB: the
            # binding's window_id is the room id in both cases.
            internal = self.internal_session(card.bot_id, room_id)
            session_id = internal.id
            store = self._canonical_store(
                card.bot_id, _window, session_id)
            sessions = SessionManager(
                limits=turn_cfg.effective_limits(),
                timezone_name=turn_cfg.timeline.timezone,
                store=store,
            )
            sessions.load(session_id)
            if retry:
                from thyca.app.turn_options import InvalidTurnOption

                if not sessions.truncate_to_last_user():
                    raise InvalidTurnOption("no user")
        except asyncio.CancelledError:
            self._status.finish_turn(card.bot_id, own_span, "cancelled")
            raise
        except Exception as exc:
            self._status.finish_turn(
                card.bot_id, own_span, "error", code=type(exc).__name__
            )
            raise

        from thyca.app.turn_options import resolve_bot_provider

        provider = resolve_bot_provider(turn_cfg, card, effort_override=effort)
        model = turn_cfg.provider.model
        # Local import: thyca.app.toolchain pulls thyca.app.__init__
        # (ChatApp), which imports this module — top-level would cycle.
        from thyca.app.toolchain import build_agent_loop

        try:
            connect = scope.injected_connect or ConnectFactory.create(provider.api, provider)
            wire_retry_events(connect, tracked_sink, bot_id=card.bot_id)
        except asyncio.CancelledError:
            self._status.finish_turn(card.bot_id, own_span, "cancelled")
            raise
        except Exception as exc:
            self._status.finish_turn(
                card.bot_id, own_span, "error", code=type(exc).__name__
            )
            raise
        owns = scope.injected_connect is None
        try:
            filtered = filtered_tool_schemas(scope.tools, card)
            loop = build_agent_loop(
                sessions=sessions,
                connect=connect,
                act=scope.act,
                tools=filtered,
                loop_max=turn_cfg.effective_limits().loopMax,
                model=model,
                pricing=turn_cfg.effective_pricing() or None,
                context_tokens=turn_cfg.effective_limits().contextTokens,
            )
            limits = turn_cfg.effective_limits()
            hot = scope.memory.refresh(
                scope.state,
                datetime.now(scope.zone),
                session_id=room_id,
                reader=None if card.system or card.bot_id == "thyca" else card.bot_id,
                tail_kb=limits.hotTailKB,
                timezone_name=turn_cfg.timeline.timezone,
            )
            bot_user_text = (
                self._registry.bot_user_text(card.bot_id)
                if card.memory.read
                else ""
            )
            # CBE-4: open-task index (compact, counted in hot tokens via
            # PromptManager.build) + one-shot post-compaction recall.
            from thyca.bots.tasks import derive_open_tasks

            open_tasks = derive_open_tasks(
                list(sessions.current.messages),
                self._pending_relevant(card.bot_id),
                bot_id=card.bot_id,
            )
            # Phase 1C: open memory-task catalog (separate section from
            # runtime open tasks above; empty when the bot sees no tasks).
            catalog_rows: tuple = ()
            index = ""
            if card.memory.read:
                try:
                    if self.memory_task_catalog is not None:
                        rows = self.memory_task_catalog(card.bot_id)
                        if inspect.isawaitable(rows):
                            rows = await rows
                        catalog_rows = tuple(rows or ())
                    elif self.memory_task_index is not None:
                        index = self.memory_task_index(card.bot_id)
                        if inspect.isawaitable(index):
                            index = await index
                        index = str(index) if index else ""
                except Exception:
                    # catalog is best-effort; the turn must go on
                    catalog_rows = ()
                    index = ""
            reinject_key = (card.bot_id, room_id)
            # One-shot recall is consumed only on successful delivery:
            # it stays armed here and is discarded after loop.run
            # returns, so a failed turn re-offers it on the next turn.
            recall_armed = reinject_key in self._reinject_pending
            bounded: list[str] = []
            if recall_armed and self.memory_searcher is not None and card.memory.read:
                try:
                    lines = self.memory_searcher(text, card.bot_id)
                    if inspect.isawaitable(lines):
                        lines = await lines
                    bounded = [str(line)[:200] for line in list(lines)[:5]]
                except Exception:
                    bounded = []  # recall is best-effort; the turn must go on
            # The builder composes the final snapshot BEFORE the compact
            # prediction below, so every injected section — including the
            # channel brief — is priced by PromptManager.build(hot).
            from thyca.bots.workspace_context import build_workspace_hot
            from thyca.memory.visibility import current_memory_view
            from thyca.bots.private_followups import PrivateFollowupStore

            _view = current_memory_view()
            _window = _view.window if _view is not None else None
            _pf_store = PrivateFollowupStore(self._dir)
            try:
                _active_pf = _pf_store.active(card.bot_id)
            except Exception:
                _active_pf = None
            hot = build_workspace_hot(
                hot,
                thyca_dir=self._dir,
                bot_id=card.bot_id,
                window=_window,
                card=card,
                runtime_tasks=tuple(open_tasks[:20]),
                memory_catalog=catalog_rows,
                memory_task_text=index,
                recalled=bounded,
                room_delta=room_text,
                bot_user=bot_user_text,
                skills=self._registry.skill_index_text(card.bot_id),
                team=self._registry.team_index_text(),
                pending_followup=_active_pf,
            )
            # Freeze the visible mailbox batch once before compact planning.
            # The exact same wire text is used by the plan and AgentLoop;
            # envelopes arriving later remain queued for the next turn.
            wire_text = text if retry else self._prepend_mailbox(card.bot_id, room_id, text)
            # The raw user row persists BEFORE the compact prediction so
            # the plan and the loop's own pre-run compact see the exact
            # same transcript (and the flush below can extract from the
            # current text). The mailbox rides the wire only
            # (persist_user=False at loop.run) and the room delta rides
            # the hot snapshot (room_history section, ephemeral).
            # CBE-4 flush-before-compact: if this turn's pre-turn compact
            # will fire, extract memories from the intact transcript
            # FIRST, then let the loop compact. The current user row is
            # added by AgentLoop after its compact decision (not included
            # in that plan twice); the provider-facing wire text contains
            # the same frozen mailbox batch while the transcript row stays
            # raw. The prediction matches the loop's input and reserves the
            # summarizer's body size (``SUMMARY_MAX_CHARS``, as
            # ``compact_with_llm`` passes).
            #
            # Policy decision (lane plan, recorded explicitly): this
            # flush is *system-driven* extraction — Thyca extracting the
            # bot's transcript through its privileged facade before the
            # system compacts it. It is not the bot calling a memory
            # tool, so a card's ``memory.write=false`` does not gate it;
            # that policy still gates the bot's own ``memory_*`` tools
            # in ``filtered_tool_schemas``/``BotPolicy``.
            markers_before = _compaction_markers(sessions.current.messages)
            will_compact = False
            if self.memory_flush is not None:
                try:
                    from thyca.agent.shrink import estimate_wire_tokens
                    from thyca.core.protocol import Message as _Msg
                    from thyca.core.protocol import estimate_tokens as _tok
                    from thyca.llm.prompt_manager import PromptManager
                    from thyca.sessions.summary import SUMMARY_MAX_CHARS

                    plan = sessions.compactor.plan(
                        list(sessions.current.messages),
                        turn_cfg.effective_limits().contextTokens,
                        hot_tokens=_tok(PromptManager().build(hot)),
                        tools_tokens=_tok(json.dumps(filtered or [], ensure_ascii=False)),
                        pending_user_tokens=estimate_wire_tokens(
                            [_Msg(role="user", content=wire_text)]
                        ),
                        calibration=calibration_factor(
                            list(sessions.current.messages), model=model
                        ),
                        body_chars=SUMMARY_MAX_CHARS,
                    )
                    will_compact = plan is not None
                except Exception:
                    will_compact = False
                if will_compact:
                    # TURN-CURATE: when the agent already recorded a
                    # checkpoint in this segment, it has decided what to
                    # keep — the system's mono extraction stands down
                    # instead of writing duplicates behind its back. The
                    # mono path stays as the safety net for segments where
                    # the agent did not write anything.
                    if not _segment_checkpointed(
                        list(sessions.current.messages),
                        turn_cfg.effective_limits().contextTokens,
                    ):
                        try:
                            flush_kwargs = {}
                            try:
                                params = inspect.signature(
                                    self.memory_flush).parameters
                                if "room_id" in params or any(
                                    p.kind is inspect.Parameter.VAR_KEYWORD
                                    for p in params.values()
                                ):
                                    flush_kwargs["room_id"] = room_id
                            except (TypeError, ValueError):
                                pass
                            flushed = self.memory_flush(
                                card.bot_id, list(sessions.current.messages),
                                turn_cfg, **flush_kwargs
                            )
                            if inspect.isawaitable(flushed):
                                await flushed
                        except Exception:
                            pass  # extraction is best-effort, never blocks compact
            allowed = None
            if not card.system:
                allowed = frozenset(
                    schema.get("function", {}).get("name")
                    for schema in (filtered or [])
                )
            bot_token = bind_bot_id(card.bot_id)
            chat_token = bind_chat_session(room_id)
            hidden_token = bind_hidden_tools(frozenset(card.hidden_tools))
            allowed_token = bind_allowed_tools(allowed)
            ctx_token = bind_bot_turn_context(
                BotTurnContext(
                    sender_id=card.bot_id,
                    room_id=room_id,
                    trace_id=trace_id,
                    span_id=own_span,
                    parent_span_id=parent_span_id,
                    scope=scope,
                    turn_cfg=context_cfg,
                    model_override=model_override,
                    effort_override=effort,
                    event_sink=tracked_sink,
                    thyca_dir=self._dir,
                    window=_window,
                    pending_private_id=(
                        _active_pf.pending_id if _active_pf is not None else None
                    ),
                    # TURN-CURATE: the agent calls the ``compact`` tool; the
                    # callback force-compacts THIS turn's session through the
                    # same summarizer the pre-turn compact uses. The agent is
                    # expected to have written durable facts to memory first.
                    compact=lambda: _force_compact(
                        connect, sessions, model, turn_cfg
                    ),
                )
            )
            try:
                _ev = None
                try:
                    from thyca.app.turn_event import current_channel_event
                    _ev = current_channel_event()
                except Exception:
                    pass
                _bot_ctx = {
                    "bot_id": card.bot_id,
                    "trace_id": trace_id,
                    "parent_span_id": parent_span_id,
                    "span_id": own_span,
                }
                if _window is not None:
                    _bot_ctx["channel"] = getattr(
                        _window, "channel_id", None) or "webui"
                    _bot_ctx["window_id"] = getattr(
                        _window, "window_id", None) or room_id
                    if (
                        _ev is not None
                        and getattr(_ev, "window_id", None)
                        == _bot_ctx["window_id"]
                    ):
                        _wr = getattr(_ev, "work_ref", None)
                        from thyca.core.channel import WORK_REF_RE
                        if isinstance(_wr, str) and WORK_REF_RE.fullmatch(
                            _wr
                        ):
                            _bot_ctx["work_id"] = _wr
                on_hot_ready = None
                if diagnostic_sink is not None:
                    from thyca.llm.prompt_manager import PromptManager

                    def on_hot_ready(final_hot) -> None:
                        diagnostic_sink({
                            "session_id": room_id,
                            "model": model,
                            "provider": turn_cfg.provider_id_for(model),
                            "base_url": turn_cfg.effective_provider_for(model).baseUrl,
                            "system": PromptManager().build(final_hot),
                            "tools": list(filtered or []),
                        })

                reply = await loop.run(
                    wire_text,
                    hot=hot,
                    event_sink=tracked_sink,
                    bot_ctx=_bot_ctx,
                    persisted_user_msg=text,
                    persist_user=not retry,
                    on_hot_ready=on_hot_ready,
                )
                new_messages = [
                    message
                    for message in sessions.current.messages
                    if (getattr(message, "meta", None) or {}).get("span_id") == own_span
                ]
                # The turn's own user message is now read: advance past it.
                # Errors/cancels skip this, so a failed turn's message is
                # still unread next turn (only the delivered delta stays).
                if room_cursor_after is not None:
                    self.set_room_cursor(card.bot_id, room_id, room_cursor_after)
                # The turn delivered successfully: only now is the
                # one-shot recall consumed (a raised turn above keeps
                # it armed for the next turn).
                if recall_armed:
                    self._reinject_pending.discard(reinject_key)
                # A compaction landed this turn (new marker, or the
                # pre-turn plan said it would): queue the one-shot
                # memory re-injection for this bot's next turn (CBE-4).
                if will_compact or _compaction_markers(
                    sessions.current.messages
                ) > markers_before:
                    self._reinject_pending.add(reinject_key)
                agg_model, agg_usage, agg_cost, agg_latency, agg_requests, agg_tool_calls = _aggregate_turn_meta(new_messages)
                agg_rounds = _aggregate_turn_rounds(new_messages)
                agg_status = next(
                    (
                        status
                        for status in ("context_limit", "loop_limit")
                        if any(
                            (getattr(message, "meta", None) or {}).get("status") == status
                            for message in new_messages
                        )
                    ),
                    None,
                )
                self._status.finish_turn(card.bot_id, own_span, "done")
                _res_channel = getattr(
                    _window, "channel_id", None) or "webui" if _window else None
                _res_window = getattr(
                    _window, "window_id", None) if _window else None
                _res_work = None
                if (
                    _ev is not None
                    and getattr(_ev, "window_id", None) == _res_window
                ):
                    _wr = getattr(_ev, "work_ref", None)
                    from thyca.core.channel import WORK_REF_RE
                    if isinstance(_wr, str) and WORK_REF_RE.fullmatch(_wr):
                        _res_work = _wr
                return BotTurnResult(
                    reply=reply,
                    bot_id=card.bot_id,
                    span_id=own_span,
                    bot_name=card.name,
                    bot_tint=card.tint,
                    model=agg_model,
                    usage=agg_usage,
                    cost_usd=agg_cost,
                    latency_ms=agg_latency,
                    requests=agg_requests,
                    tool_calls=agg_tool_calls,
                    rounds=agg_rounds,
                    status=agg_status,
                    channel=_res_channel,
                    window_id=_res_window,
                    work_id=_res_work,
                )
            except asyncio.CancelledError:
                self._status.finish_turn(card.bot_id, own_span, "cancelled")
                raise
            except Exception as exc:
                try:
                    # Mark the canonical user row; the app marks the
                    # public room row separately. This is journaled like
                    # every other canonical write.
                    # Best-effort: never masks the in-flight failure.
                    sessions.mark_turn_error(
                        *(
                            ("llm_error", str(exc))
                            if isinstance(exc, LLMError)
                            else ("chat_unavailable", "chat unavailable")
                        )
                    )
                except Exception:
                    pass
                self._status.finish_turn(
                    card.bot_id, own_span, "error", code=type(exc).__name__
                )
                raise
            finally:
                reset_bot_turn_context(ctx_token)
                reset_allowed_tools(allowed_token)
                reset_hidden_tools(hidden_token)
                reset_chat_session(chat_token)
                reset_bot_id(bot_token)
        except asyncio.CancelledError:
            # The loop-level finish already ran; this is a no-op then,
            # and covers setup failures before the loop started.
            self._status.finish_turn(card.bot_id, own_span, "cancelled")
            raise
        except Exception as exc:
            self._status.finish_turn(
                card.bot_id, own_span, "error", code=type(exc).__name__
            )
            raise
        finally:
            if owns:
                close = getattr(connect, "aclose", None)
                if close is not None:
                    await close()

    def send(
        self,
        *,
        sender: str,
        recipient: str,
        kind: str,
        session_id: str,
        payload: str,
        trace_id: str | None = None,
        parent_span_id: str | None = None,
        refs: tuple[str, ...] | list[str] = (),
    ):
        """Send one A2A envelope (hop-limited by the bus)."""
        self._bus.register(recipient)
        return self._bus.send(
            sender=sender,
            recipient=recipient,
            kind=kind,
            session_id=session_id,
            payload=payload,
            trace_id=trace_id,
            parent_span_id=parent_span_id,
            refs=refs,
        )

    def fetch_artifact(self, span_id: str, caller: str | None = None) -> str | None:
        """Full body of an excerpted envelope, or None (CBE-3).

        With a ``caller`` (the bot_fetch path), the body is returned
        only when the caller is the envelope's sender or recipient;
        without one this is the raw internal lookup."""
        if caller is None:
            return self._bus.artifacts.get(span_id)
        return self._bus.artifacts.get_for(span_id, caller)

    def artifact_parties(self, span_id: str) -> tuple[str | None, str | None] | None:
        """(sender, recipient) recorded for an artifact, or None."""
        return self._bus.artifacts.parties(span_id)

    async def deliver(self, envelope: Envelope) -> str | None:
        """Run the recipient's turn for a just-sent envelope, if wired.

        ChatApp installs ``deliverer``; it runs the recipient bot's own
        AgentLoop turn (bounded by the bus hop limit already applied at
        send), records its attributed rows in the room transcript, and
        returns the reply for the sender's tool result. Without a
        deliverer (direct unit-level calls) the envelope stays queued
        and this returns None."""
        if self.deliverer is None:
            return None
        ctx = current_bot_turn_context()
        if ctx is None:
            return None
        return await self.deliverer(envelope, ctx)

    def _prepend_mailbox(self, bot_id: str, room_id: str, text: str) -> str:
        """Prepend drained A2A envelopes so delegation is never a dead letter.

        A bot's turn starts by reading its mailbox; queued ``delegate`` /
        ``message`` envelopes arrive as context above the user's text."""
        from thyca.memory.visibility import current_memory_view

        view = current_memory_view()
        if view is None:
            pending = self._bus.drain(bot_id)
        else:
            pending = self._bus.drain_matching(
                bot_id,
                lambda env: view.can_read_origin(env.session_id),
            )
        return self._mailbox_text(pending, room_id, text)

    @staticmethod
    def _mailbox_text(pending: list[Envelope], room_id: str, text: str) -> str:
        """Render queued envelopes above the turn text (pure).

        Shared by delivery (drained envelopes) and the flush pre-check
        (a pending snapshot), so the size the runtime predicts is the
        size the loop actually assembles. Refs are clipped here too —
        at most ``MAX_REFS`` entries of ``MAX_REF_CHARS`` chars, aligned
        with the room transcript row's cap — so a hand-built envelope
        that bypassed ``Bus.send`` cannot flood the prompt either."""
        if not pending:
            return text
        lines = [f"[A2A mailbox: {len(pending)} queued message(s)]"]
        for envelope in pending:
            if envelope.session_id != room_id:
                lines.append(
                    f"- ({envelope.kind} from {envelope.sender}, "
                    f"another room {envelope.session_id}, trace {envelope.trace_id}):"
                )
            else:
                lines.append(
                    f"- ({envelope.kind} from {envelope.sender}, "
                    f"trace {envelope.trace_id}):"
                )
            # Envelope payload is already the <=1000-char excerpt (CBE-3).
            lines.append(envelope.payload)
            if envelope.refs:
                clipped = ", ".join(
                    ref[:MAX_REF_CHARS] for ref in envelope.refs[:MAX_REFS]
                )
                lines.append(f"  refs: {clipped}")
            if envelope.truncated:
                lines.append(
                    f"  [truncated: full body via bot_fetch "
                    f"span_id={envelope.span_id}]"
                )
        return "\n".join(lines) + "\n\n" + text

    def _pending_relevant(self, bot_id: str) -> list[Envelope]:
        """Queued envelopes to or from ``bot_id`` (task-view input)."""
        from thyca.memory.visibility import current_memory_view

        view = current_memory_view()

        def readable(envelope) -> bool:
            return view is None or view.can_read_origin(envelope.session_id)

        relevant = [
            e for e in self._bus.pending_for(bot_id) if readable(e)]
        for doc in self._registry.list():
            if not doc.ok or doc.card is None or doc.card.bot_id == bot_id:
                continue
            for envelope in self._bus.pending_for(doc.card.bot_id):
                if envelope.sender == bot_id and readable(envelope):
                    relevant.append(envelope)
        return relevant

    def room_cursor(self, bot_id: str, room_id: str) -> tuple[int, str] | None:
        """This bot's room-context cursor from the registry binding.

        Read-only: never creates a binding just to read a cursor."""
        try:
            binding = self._workspaces.store.get_binding(bot_id, room_id)
        except Exception:
            return None
        return binding.cursor if binding is not None else None

    def set_room_cursor(self, bot_id: str, room_id: str,
                        cursor: tuple[int, str] | None) -> None:
        """Persist a room-context cursor on the registry binding.

        A None cursor writes nothing; an unknown window writes nothing
        (callers only persist after the binding exists)."""
        try:
            self._workspaces.store.set_cursor(bot_id, room_id, cursor)
        except Exception:
            pass

    def zone(self) -> ZoneInfo:
        from thyca.config import DEFAULT_TIMELINE_TIMEZONE

        try:
            return ZoneInfo(DEFAULT_TIMELINE_TIMEZONE)
        except Exception:
            return ZoneInfo("UTC")
