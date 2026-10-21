"""Read-only usage summaries of original canonical message events.

Input must already be scoped to one bot/window. Never pass view-reset copies:
those are context bases, not additional billable requests. Recorded costs are
used as-is; current pricing must not retrospectively reprice old requests.
"""
from __future__ import annotations

import math

from thyca.core.protocol import Message

_TOKEN_FIELDS = ("prompt_tokens", "cached_tokens", "completion_tokens", "total_tokens")
_STOP_STATUSES = frozenset({"context_limit", "loop_limit"})


def _calls(messages: list[Message]) -> list[Message]:
    return [
        message for message in messages
        if message.role == "assistant"
        and (message.meta or {}).get("kind") == "llm"
        and (message.meta or {}).get("status") not in _STOP_STATUSES
    ]


def _summary(messages: list[Message]) -> dict:
    calls = _calls(messages)
    tokens = {}
    for field in _TOKEN_FIELDS:
        values = [
            (message.meta or {}).get("usage", {}).get(field)
            if isinstance((message.meta or {}).get("usage"), dict) else None
            for message in calls
        ]
        complete = bool(values) and all(
            isinstance(value, int) and not isinstance(value, bool) and value >= 0
            for value in values
        )
        tokens[field] = sum(values) if complete else None
    known_costs = []
    for message in calls:
        value = (message.meta or {}).get("cost_usd")
        if (
            isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0
        ):
            known_costs.append(value)
    complete_cost = bool(calls) and len(known_costs) == len(calls)
    known_cost = round(sum(known_costs), 6) if known_costs else None
    return {
        "requests": len(calls),
        "usage": tokens,
        "cost_usd": known_cost if complete_cost else None,
        "known_cost_usd": known_cost,
        "unpriced_requests": len(calls) - len(known_costs),
    }


def usage_for_window(messages: list[Message]) -> dict:
    """Summarize one window, including retries and pre-compaction requests.

    A turn is identified by its trusted execution span, not by list position
    in the compacted projection. A synthetic stop can identify the latest
    turn but never counts as another model request. Missing usage/cost is
    unknown, not zero; explicit recorded zero remains zero.
    """
    assistant_rows = [
        message for message in messages
        if message.role == "assistant" and (message.meta or {}).get("kind") == "llm"
    ]
    latest = None
    if assistant_rows:
        meta = assistant_rows[-1].meta or {}
        span = meta.get("span_id")
        if isinstance(span, str) and span:
            rows = [
                message for message in assistant_rows
                if (message.meta or {}).get("span_id") == span
            ]
            model = next((
                (message.meta or {}).get("model")
                for message in reversed(_calls(rows))
                if isinstance((message.meta or {}).get("model"), str)
                and (message.meta or {}).get("model")
            ), None)
            latest = {
                "span_id": span,
                "trace_id": meta.get("trace_id"),
                "model": model,
                **_summary(rows),
            }
    return {"last_turn": latest, "conversation": _summary(messages)}
