"""Read-only: whether a session should be nudged to remember."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from thyca.protocol import Message

IDLE = timedelta(minutes=15)
_REMEMBER = "memory_remember"


def ask_remember(messages: list[Message], now: datetime) -> bool:
    last_user_i = -1
    last_user_at: datetime | None = None
    last_remember_i = -1
    for index, message in enumerate(messages):
        if message.role == "user" and (message.content or "").strip():
            last_user_i = index
            last_user_at = _ts(message.ts)
        if message.role == "assistant" and message.tool_calls:
            if any(call.name == _REMEMBER for call in message.tool_calls):
                last_remember_i = index
    if last_user_at is None or last_remember_i > last_user_i:
        return False
    current = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    return current - last_user_at >= IDLE


def _ts(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
