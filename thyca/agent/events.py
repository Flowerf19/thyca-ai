"""Operational turn events. No prompt, content, args, result, or exception text."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import re

_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]+$")
_IDENTIFIER_MAX = 64

_PUBLIC_NAME = "tool"
_PUBLIC_CALL_ID = "call"

_FIELDS = ("round", "tool_count", "call_id", "name", "ok", "updated", "attempt", "max_attempts")

_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "turn.accepted": frozenset(),
    "llm.started": frozenset({"round"}),
    "llm.finished": frozenset({"round", "tool_count"}),
    "llm.retry": frozenset({"attempt", "max_attempts"}),
    "tool.started": frozenset({"round", "call_id", "name"}),
    "tool.finished": frozenset({"round", "call_id", "name", "ok"}),
    "skill.started": frozenset({"round", "call_id", "name"}),
    "skill.finished": frozenset({"round", "call_id", "name", "ok"}),
    "session.naming.started": frozenset(),
    "session.naming.finished": frozenset({"updated"}),
}


def _public_identifier(value: object, fallback: str) -> str:
    if (
        isinstance(value, str)
        and 0 < len(value) <= _IDENTIFIER_MAX
        and _IDENTIFIER.fullmatch(value)
    ):
        return value
    return fallback


@dataclass(frozen=True)
class TurnEvent:
    type: str
    round: int | None = None
    tool_count: int | None = None
    call_id: str | None = None
    name: str | None = None
    ok: bool | None = None
    updated: bool | None = None
    attempt: int | None = None
    max_attempts: int | None = None

    def __post_init__(self) -> None:
        allowed = _ALLOWED_FIELDS.get(self.type)
        if allowed is None:
            raise ValueError(f"unknown event type {self.type!r}")
        for field_name in _FIELDS:
            value = getattr(self, field_name)
            if field_name not in allowed:
                if value is not None:
                    raise ValueError(f"unexpected field {field_name!r} for {self.type!r}")
                continue
            if field_name == "round":
                if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                    raise ValueError("round must be an integer >= 1")
            elif field_name == "tool_count":
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise ValueError("tool_count must be an integer >= 0")
            elif field_name == "ok":
                if not isinstance(value, bool):
                    raise ValueError("ok must be a bool")
            elif field_name == "updated":
                if not isinstance(value, bool):
                    raise ValueError("updated must be a bool")
            elif field_name == "attempt":
                if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                    raise ValueError("attempt must be an integer >= 1")
            elif field_name == "max_attempts":
                if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                    raise ValueError("max_attempts must be an integer >= 1")
            elif field_name == "name":
                # Provider text is untrusted; never forward raw identifiers.
                object.__setattr__(self, "name", _public_identifier(value, _PUBLIC_NAME))
            elif field_name == "call_id":
                object.__setattr__(self, "call_id", _public_identifier(value, _PUBLIC_CALL_ID))
        if self.type == "llm.retry":
            if self.attempt is None or self.max_attempts is None:
                raise ValueError("llm.retry requires attempt and max_attempts")
            if self.attempt > self.max_attempts:
                raise ValueError("attempt must be <= max_attempts")

    def to_dict(self) -> dict:
        payload: dict = {"type": self.type}
        for field_name in _FIELDS:
            value = getattr(self, field_name)
            if value is not None:
                payload[field_name] = value
        return payload


EventSink = Callable[[TurnEvent], None]


def emit_event(sink: EventSink | None, event: TurnEvent) -> None:
    if sink is None:
        return
    try:
        sink(event)
    except Exception:
        # Telemetry must never break a turn; do not log (could leak).
        pass
