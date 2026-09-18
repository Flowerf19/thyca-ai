"""Loopback chat: list/create sessions and run one AgentLoop turn."""
from __future__ import annotations

import asyncio
import concurrent.futures
import sys
import threading
import time
from dataclasses import replace
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
from thyca.config import REASONING_EFFORTS, Config, ConfigError, load
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
from thyca.tools.builtin.background import BackgroundProcs
from thyca.tools.mcp import MCPManager
from thyca.tools.memory import MemoryFacade
from thyca.tools.memory_tools import bind_chat_session, register_memory_tools, reset_chat_session
from thyca.tools.path_guard import PathGuard
from thyca.tools.registry import ToolRegistry
from thyca.tools.task_store import TaskStore, tool_read_spec
from thyca.turn_state import TurnHub, TurnState

TEXT_MAX = 4000
_CANCEL_WAIT_S = 5.0


class InvalidTurnOption(ValueError):
    """Per-turn model/effort/retry the HTTP layer reports as 400."""


class TurnCancelled(Exception):
    """In-flight turn was cancelled; not a provider failure."""


class SessionIdle(Exception):
    """Cancel arrived while this session had no turn in flight."""


def overlay_turn_cfg(cfg: Config, model: str | None, effort: str | None) -> Config:
    chosen = cfg.provider.model if model is None else model
    if chosen != cfg.provider.model and chosen not in cfg.models:
        raise InvalidTurnOption("invalid model")
    if effort is not None and effort not in REASONING_EFFORTS:
        raise InvalidTurnOption("invalid effort")
    return replace(cfg, provider=replace(cfg.provider, model=chosen))


def _clean_turn_text(text: object, *, retry: bool) -> str:
    if retry:
        return ""
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    cleaned = text.strip()
    if not cleaned:
        raise ValueError("empty")
    if len(cleaned) > TEXT_MAX:
        raise ValueError("too long")
    return cleaned


class _TurnJob:
    __slots__ = ("task", "cancel", "cfut", "lock")

    def __init__(self) -> None:
        self.task: asyncio.Task | None = None
        self.cancel = False
        self.cfut: concurrent.futures.Future = concurrent.futures.Future()
        self.lock = threading.Lock()


class _LoopTurns:
    """asyncio.Task per claimed session; each job is locked across spawn/cancel."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._jobs: dict[str, _TurnJob] = {}
        self._lock = threading.Lock()

    def begin(self, session_id: str) -> _TurnJob:
        job = _TurnJob()
        with self._lock:
            self._jobs[session_id] = job
        return job

    def end(self, session_id: str, job: _TurnJob) -> None:
        with self._lock:
            if self._jobs.get(session_id) is job:
                del self._jobs[session_id]

    def submit(self, job: _TurnJob, coro):
        def spawn() -> None:
            with job.lock:
                if job.cancel:
                    coro.close()
                    job.cfut.cancel()
                    return
                task = self._loop.create_task(coro)
                job.task = task

            def done(finished: asyncio.Task) -> None:
                if job.cfut.done():
                    return
                if finished.cancelled():
                    job.cfut.cancel()
                    return
                exc = finished.exception()
                if exc is not None:
                    job.cfut.set_exception(exc)
                else:
                    job.cfut.set_result(finished.result())

            task.add_done_callback(done)

        self._loop.call_soon_threadsafe(spawn)
        try:
            return job.cfut.result()
        except concurrent.futures.CancelledError:
            raise TurnCancelled() from None

    def request_cancel(self, session_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(session_id)
        if job is None:
            return False
        with job.lock:
            job.cancel = True
            task = job.task
        if task is not None:
            self._loop.call_soon_threadsafe(task.cancel)
        return True


async def _name_if_needed(
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
            _record_naming(captured.get("reply"), latency_ms, sessions, cfg)
    emit_event(
        event_sink, TurnEvent(type="session.naming.finished", updated=updated)
    )
    return updated


def _record_naming(
    reply: object, latency_ms: int, sessions: SessionManager, cfg: Config
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
        registry = ToolRegistry(tasks=self._tasks)
        self._background = BackgroundProcs()
        register_file_tools(registry, PathGuard(root), self._background)
        registry.register(tool_read_spec(self._tasks))
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
        self._loop_turns = _LoopTurns(self._loop)
        self._claim_lock = threading.Lock()
        self._stopped = False
        self._thread.start()
        try:
            if not self._ready.wait(timeout=5):
                raise RuntimeError("mcp loop thread failed to start")
            for diag in self._submit(self._mcp.spawn_all(cfg.mcpServers)):
                if not diag.ok:
                    print(f"{diag.server}: {diag.message}", file=sys.stderr)
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
                "openai_chat", provider
            )
            owns = self._injected_connect is None
            self._wire_retry_events(connect, event_sink)
            try:
                limits = turn_cfg.effective_limits()
                loop = AgentLoop(
                    sessions=sessions,
                    assemble=Assemble(),
                    think=Think(connect),
                    act=self._act,
                    observe=Observe(sessions),
                    loop_max=limits.loopMax,
                    tools=self._tools,
                    model=turn_cfg.provider.model,
                    pricing=turn_cfg.effective_pricing() or None,
                )
                hot = self._memory.refresh(self._state, datetime.now(self._zone))
                reply = await loop.run(
                    text, hot=hot, event_sink=event_sink, persist_user=not retry
                )
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


def session_title(session: Session) -> str:
    """Display title for one session (thin alias over the payload module)."""
    return display_title(session)
