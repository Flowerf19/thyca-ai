"""In-flight bot turn context (leaf: stdlib only, no thyca imports).

Bound by the bot runtime (and by the default Thyca turn) for the duration
of one AgentLoop turn. A2A send tools read the sender identity, room,
trace, and span from here — never from tool arguments — and the gateway
policy reads the thyca root for its protected-path checks.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any


@dataclass
class BotTurnContext:
    sender_id: str
    room_id: str
    trace_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    scope: Any = None
    turn_cfg: Any = None
    event_sink: Any = None
    thyca_dir: Any = None


_turn_context: ContextVar[BotTurnContext | None] = ContextVar(
    "thyca_bot_turn_context", default=None
)


def bind_bot_turn_context(ctx: BotTurnContext | None):
    return _turn_context.set(ctx)


def reset_bot_turn_context(token) -> None:
    _turn_context.reset(token)


def current_bot_turn_context() -> BotTurnContext | None:
    return _turn_context.get()
