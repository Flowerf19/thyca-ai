"""Loopback chat: list/create sessions and run one AgentLoop turn."""
from __future__ import annotations

import asyncio
import sys
import threading
from datetime import datetime
from pathlib import Path
from time import perf_counter
from zoneinfo import ZoneInfo

from thyca.agent.act import Act
from thyca.agent.assemble import Assemble
from thyca.agent.events import EventSink, TurnEvent, emit_event
from thyca.agent.loop import AgentLoop
from thyca.agent.observe import Observe
from thyca.agent.think import LLMPort, Think
from thyca.config import Config, ConfigError, load
from thyca.llm.llm_base import LLMError
from thyca.llm.llm_factory import ConnectFactory
from thyca.llm.pricing import cost_for
from thyca.memory.active import ActiveMemory
from thyca.protocol import Message, utc_now_ts
from thyca.session_wire import session_detail, session_summary
from thyca.sessions import Session, SessionManager
from thyca.sessions.store import SessionStore
from thyca.sessions.title import display_title, is_blank, propose_title
from thyca.tools.builtin import register_file_tools
from thyca.tools.mcp import MCPManager
from thyca.tools.memory import MemoryFacade
from thyca.tools.memory_tools import register_memory_tools
from thyca.tools.path_guard import PathGuard
from thyca.tools.registry import ToolRegistry
from thyca.turn_state import TurnState

TEXT_MAX = 4000


class ChatApp:
    def __init__(self, root: Path, cfg: Config, connect: LLMPort | None = None) -> None:
        self._root = root
        self._config_file = root / "config.json"
        self._cfg = self._current_cfg() if self._config_file.exists() else cfg
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
        registry = ToolRegistry()
        register_file_tools(registry, PathGuard(root))
        register_memory_tools(
            registry, MemoryFacade(root, timezone_name=cfg.timeline.timezone)
        )
        self._mcp = MCPManager()
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
        self._stopped = False
        self._thread.start()
        try:
            if not self._ready.wait(timeout=5):
                raise RuntimeError("mcp loop thread failed to start")
            for diag in self._submit(self._mcp.spawn_all(cfg.mcpServers)):
                if not diag.ok:
                    print(diag.message, file=sys.stderr)
            for spec in self._mcp.tool_specs():
                try:
                    registry.register(spec)
                except ValueError as exc:
                    print(str(exc), file=sys.stderr)
            self._tools = registry.to_openai_schema()
            self._act = Act(registry, skills_root=root / "skills")
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

    def _current_cfg(self) -> Config:
        """Re-read the config file each turn so settings changes apply
        without restarting the server (reasoningEffort, model, ...)."""
        try:
            return load(self._config_file)
        except ConfigError:
            return self._cfg

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

    def running_sessions(self) -> dict[str, str]:
        """Snapshot of session_id -> started_at for turns in flight."""
        return self._turns.snapshot()

    def create(self) -> dict:
        # Do not wait on any turn: create/list stay responsive while an LLM
        # call is in flight. Blank sessions belonging to a running turn must
        # survive the prune — its transcript is still empty on disk.
        self._sessions.discard_empty(keep=set(self._turns.snapshot()))
        session = self._sessions.create()
        return self._detail(session, self._current_cfg())

    def turn(self, session_id: str, text: str, event_sink: EventSink | None = None) -> dict:
        if not isinstance(text, str):
            raise ValueError("text must be a string")
        cleaned = text.strip()
        if not cleaned:
            raise ValueError("empty")
        if len(cleaned) > TEXT_MAX:
            raise ValueError("too long")
        self._turns.claim(session_id)
        try:
            return self._submit(self._run_turn(session_id, cleaned, event_sink))
        finally:
            self._turns.release(session_id)

    async def _run_turn(
        self, session_id: str, text: str, event_sink: EventSink | None = None
    ) -> dict:
        # Every turn owns its session state (config, SessionManager, current
        # session). Nothing here is shared with a concurrent turn, so a slow
        # provider call in one session cannot block or corrupt another.
        cfg = self._current_cfg()
        sessions = SessionManager(
            limits=cfg.effective_limits(),
            timezone_name=cfg.timeline.timezone,
            store=self._sessions.store,
        )
        sessions.load(session_id)
        connect = self._injected_connect or ConnectFactory.create(
            "openai_chat", cfg.effective_provider()
        )
        owns = self._injected_connect is None
        self._wire_retry_events(connect, event_sink)
        try:
            limits = cfg.effective_limits()
            loop = AgentLoop(
                sessions=sessions,
                assemble=Assemble(),
                think=Think(connect),
                act=self._act,
                observe=Observe(sessions),
                loop_max=limits.loopMax,
                tools=self._tools,
                model=cfg.provider.model,
                pricing=cfg.effective_pricing() or None,
            )
            hot = self._memory.refresh(self._state, datetime.now(self._zone))
            reply = await loop.run(text, hot=hot, event_sink=event_sink)
            await self._name_if_needed(connect, sessions, cfg, event_sink)
            # The turn's own response is not a turn in flight: the client that
            # just received it must not be told to wait for itself.
            detail = self._detail(sessions.current, cfg, running=False)
            return {**detail, "reply": reply}
        finally:
            if owns:
                close = getattr(connect, "aclose", None)
                if close is not None:
                    await close()

    async def _name_if_needed(
        self,
        connect: LLMPort,
        sessions: SessionManager,
        cfg: Config,
        event_sink: EventSink | None = None,
    ) -> bool:
        # The user may have named the notebook from the sidebar while this turn
        # was running. Re-read the title from disk so the model does not
        # overwrite a name that landed mid-turn.
        sessions.refresh_title()
        session = sessions.current
        if session.title:
            return False
        emit_event(event_sink, TurnEvent(type="session.naming.started"))
        updated = False
        captured: dict = {}

        async def spy(messages, tools=None):
            reply = await connect.chat(messages, tools)
            captured["reply"] = reply
            return reply

        started = perf_counter()
        try:
            cleaned = await propose_title(spy, session)
        except LLMError:
            cleaned = None
        latency_ms = int((perf_counter() - started) * 1000)
        if cleaned is not None:
            stored = sessions.set_title(cleaned)
            updated = stored is not None
            if updated:
                self._record_naming(captured.get("reply"), latency_ms, sessions, cfg)
        emit_event(
            event_sink, TurnEvent(type="session.naming.finished", updated=updated)
        )
        return updated

    def _record_naming(
        self, reply: object, latency_ms: int, sessions: SessionManager, cfg: Config
    ) -> None:
        """Persist the naming LLM call as a meta-only assistant message (TASK-009)."""
        usage = getattr(reply, "usage", None)
        model = (getattr(reply, "model", None) or cfg.provider.model or "").strip() or None
        meta: dict = {"kind": "naming", "latency_ms": max(0, latency_ms)}
        if model:
            meta["model"] = model
        if isinstance(usage, dict) and usage:
            meta["usage"] = usage
        if model:
            price = cost_for(
                model,
                usage if isinstance(usage, dict) else None,
                cfg.effective_pricing() or None,
            )
            if price is not None:
                meta["cost_usd"] = price
        sessions.append(Message(role="assistant", content=None, ts=utc_now_ts(), meta=meta))

    def _wire_retry_events(
        self, connect: LLMPort, event_sink: EventSink | None
    ) -> None:
        """Surface provider transient retries as non-error TurnEvents."""
        setter = getattr(connect, "set_retry_hook", None)
        if not callable(setter):
            return

        def on_retry(attempt: int, max_attempts: int) -> None:
            emit_event(
                event_sink,
                TurnEvent(
                    type="llm.retry",
                    attempt=attempt,
                    max_attempts=max_attempts,
                ),
            )

        setter(on_retry)

    def shutdown(self) -> None:

        if self._stopped:
            return
        self._stopped = True
        try:
            if self._loop.is_running():
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
        )


def session_title(session: Session) -> str:
    """Display title for one session (thin alias over the payload module)."""
    return display_title(session)
