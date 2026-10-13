"""Bot runtime: one shared AgentLoop architecture, configured per card.

Every bot turn is an instance of the existing ``build_agent_loop`` wiring
(master §1: no new loop engine), with model/tools/snapshot taken from the
bot's Agent Card. Each bot keeps one internal session per room (plan B);
continuity lives in the bot's memory/skills, not in a lifelong transcript.
"""
from __future__ import annotations

import inspect
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from thyca.agent.events import EventSink
from thyca.bots.bus import Bus, Envelope, new_span_id
from thyca.bots.card import BotCard
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
from thyca.bots.registry import BotRegistry, RegistryError
from thyca.config import Config
from thyca.llm.llm_factory import ConnectFactory
from thyca.memory.identity import bind_bot_id, reset_bot_id
from thyca.sessions import Session, SessionManager, calibration_factor
from thyca.sessions.store import SessionStore
from thyca.tools.memory_tools import bind_chat_session, reset_chat_session

#: Admin tools (BE-5, ``bot_*``) live only in Thyca's registry: child-bot
#: tool snapshots drop every name with this prefix. The prefix (not an
#: import) is the seam so this module never cycles on admin_tools.
ADMIN_TOOL_PREFIX = "bot_"

MEMORY_READ_TOOLS = frozenset({"memory_search", "memory_recent", "memory_get"})
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
PEER_TOOL_NAMES = frozenset({"bot_send", "bot_fetch", "task_open"})


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
        # Fail-closed: the card's list is exhaustive for child bots.
        keep &= set(card.tools)
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
    model: str | None = None
    usage: dict | None = None
    cost_usd: float | None = None
    latency_ms: int | None = None

    def room_meta(self, *, kind: str, trace_id: str | None,
                  parent_span_id: str | None) -> dict:
        meta: dict = {
            "bot_id": self.bot_id,
            "kind": kind,
            "trace_id": trace_id,
            "span_id": self.span_id,
            "parent_span_id": parent_span_id,
        }
        if self.model is not None:
            meta["model"] = self.model
        if self.usage is not None:
            meta["usage"] = self.usage
        if self.cost_usd is not None:
            meta["cost_usd"] = self.cost_usd
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


def _aggregate_turn_meta(messages: list) -> tuple[str | None, dict | None, float | None, int | None]:
    """Sum model/usage/cost/latency over one turn's assistant rows."""
    model: str | None = None
    usage: dict | None = None
    cost: float | None = None
    latency: int | None = None
    for message in messages:
        if getattr(message, "role", None) != "assistant":
            continue
        meta = getattr(message, "meta", None) or {}
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
    return model, usage, cost, latency


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

    def __init__(
        self,
        thyca_dir: Path,
        registry: BotRegistry,
        bus: Bus,
    ) -> None:
        self._dir = Path(thyca_dir)
        self._registry = registry
        self._bus = bus
        self._stores: dict[str, SessionStore] = {}
        # (bot_id, room_id) pairs whose last turn compacted: the next turn
        # re-injects relevant memory hits exactly once (CBE-4).
        self._reinject_pending: set[tuple[str, str]] = set()

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
        internal_id = mapping.get(room_id)
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
    ) -> str:
        """Run one bot turn; return only the reply text."""
        result = await self.run_bot_turn_detailed(
            scope=scope, card=card, room_id=room_id, text=text,
            turn_cfg=turn_cfg, event_sink=event_sink, trace_id=trace_id,
            parent_span_id=parent_span_id, span_id=span_id,
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
    ) -> BotTurnResult:
        """Run one turn of ``card`` in its internal session for ``room_id``.

        ``scope`` is the app's :class:`TurnScope` (act/memory/state/zone +
        connect); the session store, tool snapshot, hot prompt, model, and
        attribution context are the bot's own. Returns the reply plus the
        internal turn's aggregated assistant meta for room attribution.
        """
        if card.paused:
            raise RegistryError(f"bot is paused: {card.bot_id}")
        store = self.store_for(card.bot_id)
        sessions = SessionManager(
            limits=turn_cfg.effective_limits(),
            timezone_name=turn_cfg.timeline.timezone,
            store=store,
        )
        internal = self.internal_session(card.bot_id, room_id)
        sessions.load(internal.id)

        provider = turn_cfg.effective_provider()
        model = turn_cfg.provider.model
        if card.model is not None and card.model.id != "default":
            model = card.model.id
        # Local import: thyca.app.toolchain pulls thyca.app.__init__
        # (ChatApp), which imports this module — top-level would cycle.
        from thyca.app.toolchain import build_agent_loop

        connect = scope.injected_connect or ConnectFactory.create(provider.api, provider)
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
            hot = replace(
                hot,
                soul=card.persona or hot.soul,
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
            reinject_key = (card.bot_id, room_id)
            if reinject_key in self._reinject_pending:
                self._reinject_pending.discard(reinject_key)
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
            # will fire (the session compactor's own plan, BACKSTOP
            # semantics, hot+tools overhead counted), extract memories
            # from the intact transcript FIRST, then let the loop compact.
            markers_before = _compaction_markers(sessions.current.messages)
            will_compact = False
            if self.memory_flush is not None:
                try:
                    from thyca.core.protocol import estimate_tokens as _tok
                    from thyca.llm.prompt_manager import PromptManager

                    plan = sessions.compactor.plan(
                        list(sessions.current.messages),
                        turn_cfg.effective_limits().contextTokens,
                        hot_tokens=_tok(PromptManager().build(hot)),
                        tools_tokens=_tok(json.dumps(filtered or [], ensure_ascii=False)),
                        pending_user_tokens=_tok(text),
                        calibration=calibration_factor(
                            list(sessions.current.messages), model=model
                        ),
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
            own_span = span_id or new_span_id()
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
                    event_sink=event_sink,
                    thyca_dir=self._dir,
                )
            )
            try:
                before = len(sessions.current.messages)
                text = self._prepend_mailbox(card.bot_id, room_id, text)
                reply = await loop.run(
                    text,
                    hot=hot,
                    event_sink=event_sink,
                    bot_ctx={
                        "bot_id": card.bot_id,
                        "trace_id": trace_id,
                        "parent_span_id": parent_span_id,
                        "span_id": own_span,
                    },
                )
                new_messages = list(sessions.current.messages)[before:]
                # A compaction landed this turn (new marker, or the
                # pre-turn plan said it would): queue the one-shot
                # memory re-injection for this bot's next turn (CBE-4).
                if will_compact or _compaction_markers(
                    sessions.current.messages
                ) > markers_before:
                    self._reinject_pending.add(reinject_key)
                agg_model, agg_usage, agg_cost, agg_latency = _aggregate_turn_meta(new_messages)
                return BotTurnResult(
                    reply=reply,
                    bot_id=card.bot_id,
                    span_id=own_span,
                    model=agg_model,
                    usage=agg_usage,
                    cost_usd=agg_cost,
                    latency_ms=agg_latency,
                )
            finally:
                reset_bot_turn_context(ctx_token)
                reset_allowed_tools(allowed_token)
                reset_hidden_tools(hidden_token)
                reset_chat_session(chat_token)
                reset_bot_id(bot_token)
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

    def fetch_artifact(self, span_id: str) -> str | None:
        """Full body of an excerpted envelope, or None (CBE-3)."""
        return self._bus.artifacts.get(span_id)

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
        pending = self._bus.drain(bot_id)
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
                lines.append(f"  refs: {', '.join(envelope.refs)}")
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

    def zone(self) -> ZoneInfo:
        from thyca.config import DEFAULT_TIMELINE_TIMEZONE

        try:
            return ZoneInfo(DEFAULT_TIMELINE_TIMEZONE)
        except Exception:
            return ZoneInfo("UTC")

    def _read_room_map(self, bot_id: str) -> dict[str, str]:
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
        return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}

    def _write_room_map(self, bot_id: str, mapping: dict[str, str]) -> None:
        from thyca.config import atomic_write_text

        path = self._room_map_path(bot_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(mapping, ensure_ascii=False))
