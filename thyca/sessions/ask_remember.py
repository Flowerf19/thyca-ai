"""Read-only: whether a session should be nudged to remember."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from thyca.protocol import Message

IDLE = timedelta(minutes=15)
_REMEMBER = "memory_remember"


def ask_remember(messages: list[Message], now: datetime) -> bool:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    results = _tool_results(messages)
    last_user_i = -1
    last_user_at: datetime | None = None
    last_ok_i = -1
    for index, message in enumerate(messages):
        if message.role == "user" and (message.content or "").strip():
            last_user_i = index
            last_user_at = _ts(message.ts)
        if message.role != "assistant" or not message.tool_calls:
            continue
        for call in message.tool_calls:
            if call.name != _REMEMBER:
                continue
            result = results.get(call.id)
            if result is None or result[1]:
                continue
            last_ok_i = max(last_ok_i, result[0])
    if last_user_at is None or last_ok_i > last_user_i:
        return False
    return now.astimezone(timezone.utc) - last_user_at >= IDLE


def _tool_results(messages: list[Message]) -> dict[str, tuple[int, bool]]:
    found: dict[str, tuple[int, bool]] = {}
    for index, message in enumerate(messages):
        if message.role != "tool" or not message.tool_call_id:
            continue
        errored = bool((message.meta or {}).get("is_error"))
        found[message.tool_call_id] = (index, errored)
    return found


def _ts(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
