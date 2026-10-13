"""Bot runtime: one shared AgentLoop architecture, configured per card.

Every bot turn is an instance of the existing ``build_agent_loop`` wiring
(master §1: no new loop engine), with model/tools/snapshot taken from the
bot's Agent Card. Each bot keeps one internal session per room (plan B);
continuity lives in the bot's memory/skills, not in a lifelong transcript.
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from thyca.agent.events import EventSink
from thyca.bots.bus import Bus
from thyca.bots.card import BotCard
from thyca.bots.policy import bind_hidden_tools, reset_hidden_tools
from thyca.bots.registry import BotRegistry, RegistryError
from thyca.config import Config
from thyca.llm.llm_factory import ConnectFactory
from thyca.memory.identity import bind_bot_id, reset_bot_id
from thyca.sessions import Session, SessionManager
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


def filtered_tool_schemas(
    schemas: list[dict] | None, card: BotCard
) -> list[dict] | None:
    """Apply the card's allowlist + hidden_tools (+ role defaults).

    Rules (master §2): ``hidden_tools`` wins over ``tools``; child bots
    never see ``bot_*`` admin tools; ``memory{read,write,propose_team}``
    and ``bash: deny`` drop their tool families. ``tools`` non-empty means
    allowlist: only those names (plus the gateway's own ``tool_read`` /
    ``tool_kill`` poll tools) stay visible.
    """
    if not schemas:
        return schemas
    names = {schema.get("function", {}).get("name") for schema in schemas}
    keep: set[str] = set(names) - set(card.hidden_tools)
    if not card.system:
        keep = {name for name in keep if not str(name).startswith(ADMIN_TOOL_PREFIX)}
    if not card.memory.read:
        keep -= MEMORY_READ_TOOLS
    if not card.memory.write:
        keep -= MEMORY_WRITE_TOOLS
    if not card.memory.propose_team:
        keep.discard(MEMORY_PROPOSE_TOOL)
    if card.bash == "deny":
        keep.discard("bash")
    if card.tools:
        keep &= set(card.tools) | {"tool_read", "tool_kill"}
    # NOTE: card.bash == "ask" keeps the bash tool: the ask-approval gate
    # is future UX work, not a silent deny (honest default, recorded here).
    return [
        schema
        for schema in schemas
        if schema.get("function", {}).get("name") in keep
    ]


class BotRuntime:
    """Per-bot loop factory + internal (bot × room) sessions + bus send."""

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
    ) -> str:
        """Run one turn of ``card`` in its internal session for ``room_id``.

        ``scope`` is the app's :class:`TurnScope` (act/memory/state/zone +
        connect); the session store, tool snapshot, hot prompt, model, and
        attribution context are the bot's own. Returns the reply text.
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
            loop = build_agent_loop(
                sessions=sessions,
                connect=connect,
                act=scope.act,
                tools=filtered_tool_schemas(scope.tools, card),
                loop_max=turn_cfg.effective_limits().loopMax,
                model=model,
                pricing=turn_cfg.effective_pricing() or None,
                context_tokens=turn_cfg.effective_limits().contextTokens,
            )
            hot = scope.memory.refresh(
                scope.state, datetime.now(scope.zone), session_id=room_id,
                reader=None if card.system or card.bot_id == "thyca" else card.bot_id,
            )
            hot = replace(
                hot,
                soul=card.persona or hot.soul,
                skills=self._registry.skill_index_text(card.bot_id) or hot.skills,
                team=self._registry.team_index_text(),
            )
            bot_token = bind_bot_id(card.bot_id)
            chat_token = bind_chat_session(room_id)
            hidden_token = bind_hidden_tools(frozenset(card.hidden_tools))
            try:
                text = self._prepend_mailbox(card.bot_id, room_id, text)
                return await loop.run(
                    text,
                    hot=hot,
                    event_sink=event_sink,
                    bot_ctx={
                        "bot_id": card.bot_id,
                        "trace_id": trace_id,
                        "parent_span_id": parent_span_id,
                    },
                )
            finally:
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
        )

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
            lines.append(envelope.payload[:2000])
        return "\n".join(lines) + "\n\n" + text

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
