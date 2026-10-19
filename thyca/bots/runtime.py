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

from thyca.agent.events import EventSink
from thyca.bots.bus import MAX_REF_CHARS, MAX_REFS, Bus, Envelope, new_span_id
from thyca.bots.card import BASELINE_BOT_TOOLS, BotCard, identity_text
from thyca.bots.context import (
    BotTurnContext,
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
from thyca.bots.registry import SYSTEM_BOT_ID, BotRegistry, RegistryError
from thyca.bots.status import AgentStatusTracker
from thyca.config import Config
from thyca.core.protocol import Message
from thyca.llm.llm_factory import ConnectFactory
from thyca.memory.identity import bind_bot_id, reset_bot_id
from thyca.sessions import Session, SessionManager, calibration_factor
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
MEMORY_PROPOSE_TOOL = "memory_propose_team"

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
    the one exception); ``memory{read,write,propose_team}`` and
    ``bash: deny`` drop their tool families.

    Fail-closed for non-system bots: ``tools`` *is* the allowlist, so an
    empty ``tools`` means NO tools at all — nothing is implicit, and the
    gateway poll tools (``tool_read``/``tool_kill``) stay visible only
    when the card lists them explicitly. The Thyca system card keeps the
    legacy full snapshot.

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
    if not card.memory.propose_team:
        keep.discard(MEMORY_PROPOSE_TOOL)
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
    #: Real tool calls inside the internal turn (assistant rows minus
    #: context-stop markers); stamped onto the room row like requests.
    tool_calls: int = 0

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
        if self.latency_ms is not None:
            meta["latency_ms"] = self.latency_ms
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
        if meta.get("status") == "context_limit":
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


class BotRuntime:
    """Per-bot loop factory + internal (bot × room) sessions + bus send."""

    #: Async delivery hook installed by ChatApp:
    #: ``await deliverer(envelope, ctx) -> reply text``. When set, send
    #: tools run the recipient's turn immediately; unset (unit tests,
    #: direct handler calls) envelopes stay queued for the next turn.
    deliverer: Any = None
    #: CBE-4 hooks installed by ChatApp (fakes in unit tests):
    #: ``memory_flush(bot_id, messages, turn_cfg)`` extracts memories from
    #: a bot transcript about to compact; ``memory_searcher(text, bot_id)``
    #: returns bounded recalled-memory lines for the first post-compact
    #: turn. Either may be sync or async; both are best-effort.
    memory_flush: Any = None
    memory_searcher: Any = None
    #: Phase 1C hook installed by ChatApp: ``memory_task_index(bot_id)``
    #: returns the open memory-task catalog lines for one bot's prompt.
    #: Sync or async; best-effort like the CBE-4 hooks above.
    memory_task_index: Any = None

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
        self._stores: dict[str, SessionStore] = {}
        # (bot_id, room_id) pairs whose last turn compacted: the next turn
        # re-injects relevant memory hits exactly once (CBE-4).
        self._reinject_pending: set[tuple[str, str]] = set()

    @property
    def status_tracker(self) -> AgentStatusTracker:
        return self._status

    def store_for(self, bot_id: str) -> SessionStore:
        store = self._stores.get(bot_id)
        if store is None:
            sessions_dir = self._dir / "bots" / bot_id / "sessions"
            sessions_dir.mkdir(parents=True, exist_ok=True)
            try:
                sessions_dir.chmod(0o700)
            except OSError:
                pass
            store = SessionStore(sessions_dir)
            self._stores[bot_id] = store
        return store

    def _room_map_path(self, bot_id: str) -> Path:
        return self._dir / "bots" / bot_id / "room_sessions.json"

    def internal_session(self, bot_id: str, room_id: str) -> Session:
        """The bot's private session for one room (created on first use)."""
        store = self.store_for(bot_id)
        mapping = self._read_room_map(bot_id)
        internal_id = self._internal_id_of(mapping.get(room_id))
        if internal_id is not None:
            try:
                return store.load(internal_id)
            except Exception:
                pass
        manager = SessionManager(store=store)
        session = manager.create()
        mapping[room_id] = session.id
        self._write_room_map(bot_id, mapping)
        return session

    async def run_bot_turn(
        self,
        *,
        scope: Any,
        card: BotCard,
        room_id: str,
        text: str,
        turn_cfg: Config,
        event_sink: EventSink | None = None,
        trace_id: str | None = None,
        parent_span_id: str | None = None,
        span_id: str | None = None,
        room_context: str = "",
        room_cursor: tuple[int, str] | None = None,
        room_cursor_after: tuple[int, str] | None = None,
    ) -> str:
        """Run one bot turn; return only the reply text."""
        result = await self.run_bot_turn_detailed(
            scope=scope, card=card, room_id=room_id, text=text,
            turn_cfg=turn_cfg, event_sink=event_sink, trace_id=trace_id,
            parent_span_id=parent_span_id, span_id=span_id,
            room_context=room_context, room_cursor=room_cursor,
            room_cursor_after=room_cursor_after,
        )
        return result.reply

    async def run_bot_turn_detailed(
        self,
        *,
        scope: Any,
        card: BotCard,
        room_id: str,
        text: str,
        turn_cfg: Config,
        event_sink: EventSink | None = None,
        trace_id: str | None = None,
        parent_span_id: str | None = None,
        span_id: str | None = None,
        room_context: str = "",
        room_cursor: tuple[int, str] | None = None,
        room_cursor_after: tuple[int, str] | None = None,
    ) -> BotTurnResult:
        """Run one turn of ``card`` in its internal session for ``room_id``.

        ``scope`` is the app's :class:`TurnScope` (act/memory/state/zone +
        connect); the session store, tool snapshot, hot prompt, model, and
        attribution context are the bot's own. Returns the reply plus the
        internal turn's aggregated assistant meta for room attribution.
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
            store = self.store_for(card.bot_id)
            sessions = SessionManager(
                limits=turn_cfg.effective_limits(),
                timezone_name=turn_cfg.timeline.timezone,
                store=store,
            )
            internal = self.internal_session(card.bot_id, room_id)
            sessions.load(internal.id)
        except asyncio.CancelledError:
            self._status.finish_turn(card.bot_id, own_span, "cancelled")
            raise
        except Exception as exc:
            self._status.finish_turn(
                card.bot_id, own_span, "error", code=type(exc).__name__
            )
            raise
        if room_context:
            # Phase 2A: unread public room history lands as one synthetic
            # user row BEFORE compact prediction/flush, so the prediction
            # accounts for it and the extractor never sees it as the bot's
            # own words (jobs.transcript_tail skips kind="room_context").
            # The cursor rides in meta for the next turn's delta.
            count, last_fp = room_cursor if room_cursor is not None else (0, "")
            sessions.append(
                Message(
                    role="user",
                    content=room_context,
                    meta={
                        "kind": "room_context",
                        "room_cursor": count,
                        "room_fingerprint": last_fp,
                    },
                )
            )
            # The map is the cursor's source of truth; the row above
            # keeps the same values for traceability only.
            self.set_room_cursor(card.bot_id, room_id, room_cursor)

        provider = turn_cfg.effective_provider()
        model = turn_cfg.provider.model
        if card.model is not None and card.model.id != "default":
            model = card.model.id
        # Local import: thyca.app.toolchain pulls thyca.app.__init__
        # (ChatApp), which imports this module — top-level would cycle.
        from thyca.app.toolchain import build_agent_loop

        try:
            connect = scope.injected_connect or ConnectFactory.create(provider.api, provider)
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
            hot = scope.memory.refresh(
                scope.state, datetime.now(scope.zone), session_id=room_id,
                reader=None if card.system or card.bot_id == "thyca" else card.bot_id,
            )
            if not card.memory.read:
                # memory.read=false: the hot snapshot carries no memory
                # content at all for this bot (own or team).
                hot = replace(hot, today="", today_elsewhere="")
            else:
                # Phase 1E: this bot's own USER.md rides as <bot_user>.
                bot_user_text = self._registry.bot_user_text(card.bot_id)
                if bot_user_text.strip():
                    hot = replace(hot, bot_user=bot_user_text)
            # Two-layer identity (user decision 2026-10-13, hardened at the
            # source in phase 1D): the shared soul (SOUL.md — voice/values
            # only, names nobody) stays for every twin; a child bot's
            # persona is its own overlay on top, never a replacement-or-
            # fallback. The snapshot for a child already carries no
            # identity (ActiveMemory only loads IDENTITY.md for privileged
            # readers), and the <identity> slot is set here from its own
            # TWIN.md as defense in depth — Thyca's IDENTITY.md must never
            # ride a child turn (that leak made children answer as Thyca).
            # Thyca itself keeps its real identity section.
            is_manager = card.system or card.bot_id == SYSTEM_BOT_ID
            hot = replace(
                hot,
                soul=_layered_soul(hot.soul, card.persona),
                identity=hot.identity if is_manager else identity_text(card),
                # Exactly private + team skills for this bot; never fall
                # back to Thyca's private index from the shared snapshot.
                skills=self._registry.skill_index_text(card.bot_id),
                team=self._registry.team_index_text(),
            )
            # CBE-4: open-task index (compact, counted in hot tokens via
            # PromptManager.build) + one-shot post-compaction recall.
            from thyca.bots.tasks import derive_open_tasks, format_task_index

            open_tasks = derive_open_tasks(
                list(sessions.current.messages),
                self._pending_relevant(card.bot_id),
                bot_id=card.bot_id,
            )
            if open_tasks:
                hot = replace(
                    hot, tasks=format_task_index(open_tasks[:20])
                )
            # Phase 1C: open memory-task catalog (separate section from
            # runtime open tasks above; empty when the bot sees no tasks).
            if self.memory_task_index is not None and card.memory.read:
                try:
                    index = self.memory_task_index(card.bot_id)
                    if inspect.isawaitable(index):
                        index = await index
                    if index:
                        hot = replace(hot, memory_tasks=str(index))
                except Exception:
                    pass  # catalog is best-effort; the turn must go on
            reinject_key = (card.bot_id, room_id)
            # One-shot recall is consumed only on successful delivery:
            # it stays armed here and is discarded after loop.run
            # returns, so a failed turn re-offers it on the next turn.
            recall_armed = reinject_key in self._reinject_pending
            if recall_armed:
                if self.memory_searcher is not None and card.memory.read:
                    try:
                        lines = self.memory_searcher(text, card.bot_id)
                        if inspect.isawaitable(lines):
                            lines = await lines
                        bounded = [str(line)[:200] for line in list(lines)[:5]]
                        if bounded:
                            hot = replace(hot, recalled="\n".join(bounded))
                    except Exception:
                        pass  # recall is best-effort; the turn must go on
            # CBE-4 flush-before-compact: if this turn's pre-turn compact
            # will fire, extract memories from the intact transcript
            # FIRST, then let the loop compact. The prediction must match
            # the loop's actual decision exactly (context review): the
            # loop plans on the mailbox-prepended wire text (wire-token
            # estimate of the assembled user message, not raw chars/4)
            # and reserves the summarizer's body size
            # (``SUMMARY_MAX_CHARS``, as ``compact_with_llm`` passes), so
            # this pre-check mirrors both, against a mailbox snapshot
            # (the real drain still happens just before loop.run).
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

                    wire_text = self._mailbox_text(
                        self._bus.pending_for(card.bot_id), room_id, text
                    )
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
                    try:
                        flushed = self.memory_flush(
                            card.bot_id, list(sessions.current.messages), turn_cfg
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
                    turn_cfg=turn_cfg,
                    event_sink=tracked_sink,
                    thyca_dir=self._dir,
                )
            )
            try:
                before = len(sessions.current.messages)
                text = self._prepend_mailbox(card.bot_id, room_id, text)
                reply = await loop.run(
                    text,
                    hot=hot,
                    event_sink=tracked_sink,
                    bot_ctx={
                        "bot_id": card.bot_id,
                        "trace_id": trace_id,
                        "parent_span_id": parent_span_id,
                        "span_id": own_span,
                    },
                )
                new_messages = list(sessions.current.messages)[before:]
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
                self._status.finish_turn(card.bot_id, own_span, "done")
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
                )
            except asyncio.CancelledError:
                self._status.finish_turn(card.bot_id, own_span, "cancelled")
                raise
            except Exception as exc:
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
        return self._mailbox_text(self._bus.drain(bot_id), room_id, text)

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
        relevant = list(self._bus.pending_for(bot_id))
        for doc in self._registry.list():
            if not doc.ok or doc.card is None or doc.card.bot_id == bot_id:
                continue
            for envelope in self._bus.pending_for(doc.card.bot_id):
                if envelope.sender == bot_id:
                    relevant.append(envelope)
        return relevant

    def room_cursor(self, bot_id: str, room_id: str) -> tuple[int, str] | None:
        """This bot's room-context cursor: map first, legacy meta second.

        A stored map cursor wins. When the map has no cursor yet (old
        string shape, or dict without cursor keys) but the internal
        session already carries phase-2A synthetic rows, the newest such
        row seeds the cursor so history is not redelivered from scratch.
        Never creates an internal session just to read a cursor."""
        mapping = self._read_room_map(bot_id)
        entry = mapping.get(room_id)
        if isinstance(entry, dict):
            count, fingerprint = entry.get("room_cursor"), entry.get("room_fingerprint")
            if isinstance(count, int) and isinstance(fingerprint, str):
                return (count, fingerprint)
        internal_id = self._internal_id_of(entry)
        if internal_id is None:
            return None
        try:
            messages = self.store_for(bot_id).load(internal_id).messages
        except Exception:
            return None
        for message in reversed(messages):
            meta = message.meta or {}
            if isinstance(meta, dict) and meta.get("kind") == "room_context":
                count, fingerprint = meta.get("room_cursor"), meta.get("room_fingerprint")
                if isinstance(count, int) and isinstance(fingerprint, str):
                    return (count, fingerprint)
        return None

    def set_room_cursor(self, bot_id: str, room_id: str,
                        cursor: tuple[int, str] | None) -> None:
        """Persist a room-context cursor (phase 2A rework).

        A None cursor writes nothing. The internal session id is always
        preserved; when the bot has no internal session for the room,
        nothing is created or written — callers only persist after a
        session already exists."""
        if cursor is None:
            return
        count, fingerprint = cursor
        if not isinstance(count, int) or not isinstance(fingerprint, str):
            return
        mapping = self._read_room_map(bot_id)
        internal_id = self._internal_id_of(mapping.get(room_id))
        if internal_id is None:
            return
        mapping[room_id] = {
            "session": internal_id,
            "room_cursor": count,
            "room_fingerprint": fingerprint,
        }
        self._write_room_map(bot_id, mapping)

    def zone(self) -> ZoneInfo:
        from thyca.config import DEFAULT_TIMELINE_TIMEZONE

        try:
            return ZoneInfo(DEFAULT_TIMELINE_TIMEZONE)
        except Exception:
            return ZoneInfo("UTC")

    def _read_room_map(self, bot_id: str) -> dict:
        """Room map: old values are plain internal session ids.

        New values are ``{"session": id, "room_cursor": int,
        "room_fingerprint": fp}`` once a cursor exists; readers accept
        both shapes."""
        path = self._room_map_path(bot_id)
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return {}
        try:
            data = json.loads(raw)
        except ValueError:
            return {}
        if not isinstance(data, dict):
            return {}
        return {k: v for k, v in data.items() if isinstance(k, str)}

    def _internal_id_of(self, entry) -> str | None:
        if isinstance(entry, dict):
            entry = entry.get("session")
        return entry if isinstance(entry, str) and entry else None

    def _write_room_map(self, bot_id: str, mapping: dict) -> None:
        from thyca.config import atomic_write_text

        path = self._room_map_path(bot_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(mapping, ensure_ascii=False))
