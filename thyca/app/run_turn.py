"""Per-turn execution for ChatApp: own session state, run one AgentLoop turn.

ChatApp owns lifecycle (loop thread, MCP sync, claims); this module owns the
turn itself — provider resolution, loop wiring, error marking, naming — so
the app shell stays under its size budget. All shared refs arrive in
:class:`TurnScope`; nothing here touches ChatApp privates.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from thyca.agent.act import Act
from thyca.agent.events import EventSink, TurnEvent, emit_event
from thyca.agent.think import LLMPort
from thyca.app.naming import _name_if_needed
from thyca.app.toolchain import build_agent_loop
from thyca.app.turn_options import InvalidTurnOption
from thyca.config import Config
from thyca.llm.llm_base import LLMError
from thyca.llm.llm_factory import ConnectFactory
from thyca.memory.active import ActiveMemory, ActiveState
from thyca.sessions import SessionManager
from thyca.sessions.store import SessionStore
from thyca.tools.memory_tools import bind_chat_session, reset_chat_session


@dataclass
class TurnScope:
    """ChatApp-shared refs one turn needs (plus its own zone snapshot)."""

    store: SessionStore
    act: Act
    tools: list | None
    memory: ActiveMemory
    state: ActiveState
    zone: ZoneInfo
    injected_connect: LLMPort | None = None


@dataclass
class TurnRequest:
    """One turn's inputs (already validated/cleaned by ChatApp.turn)."""

    session_id: str
    text: str
    turn_cfg: Config
    event_sink: EventSink | None = None
    effort: str | None = None
    retry: bool = False


async def run_turn(
    scope: TurnScope,
    request: TurnRequest,
    detail: Any,
) -> dict:
    """Run one turn on its own session state; ``detail`` builds the payload."""
    # Every turn owns its session state (config, SessionManager, current
    # session). Nothing here is shared with a concurrent turn, so a slow
    # provider call in one session cannot block or corrupt another.
    turn_cfg = request.turn_cfg
    sessions = SessionManager(
        limits=turn_cfg.effective_limits(),
        timezone_name=turn_cfg.timeline.timezone,
        store=scope.store,
    )
    sessions.load(request.session_id)
    if request.retry and not sessions.truncate_to_last_user():
        raise InvalidTurnOption("no user")
    token = bind_chat_session(request.session_id)
    try:
        provider = turn_cfg.effective_provider()
        if request.effort is not None:
            # overlay_turn_cfg already approved this level model-aware;
            # carry the model's own set so a custom level resolves.
            chosen = turn_cfg.models.get(turn_cfg.defaultModel)
            own_set = chosen.reasoningEfforts if chosen is not None else ()
            provider = replace(
                provider, reasoningEffort=request.effort, reasoningEfforts=own_set
            )
        connect = scope.injected_connect or ConnectFactory.create(
            provider.api, provider
        )
        owns = scope.injected_connect is None
        wire_retry_events(connect, request.event_sink)
        try:
            limits = turn_cfg.effective_limits()
            loop = build_agent_loop(
                sessions=sessions,
                connect=connect,
                act=scope.act,
                tools=scope.tools,
                loop_max=limits.loopMax,
                model=turn_cfg.provider.model,
                pricing=turn_cfg.effective_pricing() or None,
                context_tokens=limits.contextTokens,
            )
            hot = scope.memory.refresh(
                scope.state,
                datetime.now(scope.zone),
                session_id=sessions.current.id,
            )
            try:
                reply = await loop.run(
                    request.text,
                    hot=hot,
                    event_sink=request.event_sink,
                    persist_user=not request.retry,
                )
            except asyncio.CancelledError:
                raise
            except LLMError as exc:
                mark_turn_error(sessions, "llm_error", str(exc))
                raise
            except Exception:
                # Precise code stays in serve.log via bridge; the
                # transcript marker stays generic on purpose.
                mark_turn_error(sessions, "chat_unavailable", "chat unavailable")
                raise
            await _name_if_needed(connect, sessions, turn_cfg, request.event_sink)
            # The turn's own response is not a turn in flight: the client that
            # just received it must not be told to wait for itself.
            landed = detail(sessions.current, turn_cfg, running=False)
            return {**landed, "reply": reply}
        finally:
            if owns:
                close = getattr(connect, "aclose", None)
                if close is not None:
                    await close()
    finally:
        reset_chat_session(token)


def mark_turn_error(
    sessions: SessionManager, code: str, message: str
) -> None:
    """Best-effort transcript marker; never masks the original failure."""
    try:
        sessions.mark_turn_error(code, message)
    except Exception:
        pass


def wire_retry_events(
    connect: LLMPort, event_sink: EventSink | None
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
