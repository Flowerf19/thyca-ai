"""Loopback chat: list/create sessions and run one AgentLoop turn."""
from __future__ import annotations

import asyncio
import sys
import threading
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
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
from thyca.llm.prompt_manager import PromptManager
from thyca.memory.active import ActiveMemory
from thyca.sessions.wire import session_detail, session_summary
from thyca.sessions import Session, SessionManager
from thyca.sessions.store import SessionStore
from thyca.sessions.title import is_blank
from thyca.tools.builtin.background import BackgroundProcs
from thyca.tools.mcp import MCPManager
from thyca.tools.memory_tools import bind_chat_session, reset_chat_session
from thyca.tools.task_store import TaskStore
from thyca.serve.turn_state import TurnHub, TurnState

from thyca.app.loop_turns import _CANCEL_WAIT_S, _LoopTurns, TurnCancelled
from thyca.app.naming import _name_if_needed, session_title
from thyca.app.toolchain import build_tool_registry, install_mcp_specs, report_spawn_diags
from thyca.app.turn_options import InvalidTurnOption, _clean_turn_text, overlay_turn_cfg

__all__ = [
    "ChatApp",
    "InvalidTurnOption",
    "SessionIdle",
    "TurnCancelled",
    "session_title",
]


class SessionIdle(Exception):
    """Cancel arrived while this session had no turn in flight."""


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
        self._tasks = TaskStore()
        self._background = BackgroundProcs()
        registry = build_tool_registry(root, cfg, self._tasks, self._background)
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
        self._loop_turns = _LoopTurns(self._loop)
        self._claim_lock = threading.Lock()
        self._stopped = False
        self._thread.start()
        try:
            if not self._ready.wait(timeout=5):
                raise RuntimeError("mcp loop thread failed to start")
            report_spawn_diags(
                self._submit(self._mcp.spawn_all(cfg.mcpServers)), err=sys.stderr
            )
            install_mcp_specs(registry, self._mcp, err=sys.stderr)
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

    def create(self) -> dict:
        # Do not wait on any turn: create/list stay responsive while an LLM
        # call is in flight. Blank sessions belonging to a running turn must
        # survive the prune — its transcript is still empty on disk.
        self._sessions.discard_empty(keep=set(self._turns.snapshot()))
        session = self._sessions.create()
        return self._detail(session, self._current_cfg())

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
        with self._claim_lock:
            hub = self._turns.claim(session_id)
            job = self._loop_turns.begin(session_id)
        try:

            def sink(event: TurnEvent) -> None:
                hub.publish(event)
                if event_sink is not None:
                    event_sink(event)

            try:
                detail = self._loop_turns.submit(
                    job,
                    self._run_turn(
                        session_id,
                        cleaned,
                        sink,
                        turn_cfg=turn_cfg,
                        effort=effort,
                        retry=retry,
                    ),
                )
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
        # Every turn owns its session state (config, SessionManager, current
        # session). Nothing here is shared with a concurrent turn, so a slow
        # provider call in one session cannot block or corrupt another.
        sessions = SessionManager(
            limits=turn_cfg.effective_limits(),
            timezone_name=turn_cfg.timeline.timezone,
            store=self._sessions.store,
        )
        sessions.load(session_id)
        if retry and not sessions.truncate_to_last_user():
            raise InvalidTurnOption("no user")
        token = bind_chat_session(session_id)
        try:
            provider = turn_cfg.effective_provider()
            if effort is not None:
                provider = replace(provider, reasoningEffort=effort)
            connect = self._injected_connect or ConnectFactory.create(
                provider.api, provider
            )
            owns = self._injected_connect is None
            self._wire_retry_events(connect, event_sink)
            try:
                limits = turn_cfg.effective_limits()
                loop = AgentLoop(
                    sessions=sessions,
                    assemble=Assemble(PromptManager()),
                    think=Think(connect),
                    act=self._act,
                    observe=Observe(sessions),
                    loop_max=limits.loopMax,
                    tools=self._tools,
                    model=turn_cfg.provider.model,
                    pricing=turn_cfg.effective_pricing() or None,
                )
                hot = self._memory.refresh(self._state, datetime.now(self._zone))
                try:
                    reply = await loop.run(
                        text, hot=hot, event_sink=event_sink, persist_user=not retry
                    )
                except asyncio.CancelledError:
                    raise
                except LLMError as exc:
                    self._mark_turn_error(sessions, "llm_error", str(exc))
                    raise
                except Exception:
                    # Precise code stays in serve.log via bridge; the
                    # transcript marker stays generic on purpose.
                    self._mark_turn_error(sessions, "chat_unavailable", "chat unavailable")
                    raise
                await _name_if_needed(connect, sessions, turn_cfg, event_sink)
                # The turn's own response is not a turn in flight: the client that
                # just received it must not be told to wait for itself.
                detail = self._detail(sessions.current, turn_cfg, running=False)
                return {**detail, "reply": reply}
            finally:
                if owns:
                    close = getattr(connect, "aclose", None)
                    if close is not None:
                        await close()
        finally:
            reset_chat_session(token)

    @staticmethod
    def _mark_turn_error(sessions: SessionManager, code: str, message: str) -> None:
        """Best-effort transcript marker; never masks the original failure."""
        try:
            sessions.mark_turn_error(code, message)
        except Exception:
            pass

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
                self._submit(self._background.kill_all())
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
