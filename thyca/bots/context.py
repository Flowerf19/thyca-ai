"""In-flight bot turn context (leaf: stdlib only, no thyca imports).

Bound by BotRuntime for the duration of every AgentLoop turn. A2A send
tools read the sender identity, room,
trace, and span from here — never from tool arguments — and the gateway
policy reads the thyca root for its protected-path checks.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from zoneinfo import ZoneInfo

    from thyca.agent.act import Act
    from thyca.agent.think import LLMPort
    from thyca.memory.active import ActiveMemory, ActiveState
    from thyca.sessions.store import SessionStore


@dataclass
class BotTurnScope:
    """Minimal shared app references required by every workspace turn."""

    store: SessionStore
    act: Act
    tools: list[dict] | None
    memory: ActiveMemory
    state: ActiveState
    zone: ZoneInfo
    injected_connect: LLMPort | None = None


@dataclass
class BotTurnContext:
    sender_id: str
    room_id: str
    trace_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    scope: BotTurnScope | None = None
    turn_cfg: Any = None
    model_override: str | None = None
    effort_override: str | None = None
    event_sink: Any = None
    thyca_dir: Any = None
    window: Any = None
    pending_private_id: str | None = None
    # Agent-callable compaction of the current session (TURN-CURATE):
    # bound by BotRuntime per turn; the ``compact`` tool calls it. None
    # outside a bot turn or when the runtime does not wire it.
    compact: Callable[[], Awaitable[dict]] | None = None


_turn_context: ContextVar[BotTurnContext | None] = ContextVar(
    "thyca_bot_turn_context", default=None
)


def bind_bot_turn_context(ctx: BotTurnContext | None):
    return _turn_context.set(ctx)


def reset_bot_turn_context(token) -> None:
    _turn_context.reset(token)


def current_bot_turn_context() -> BotTurnContext | None:
    return _turn_context.get()
