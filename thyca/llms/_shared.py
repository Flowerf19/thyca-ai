"""Tool-call assembly shared by the chat and responses parsers.

Both APIs end at the same ``ToolCall`` list: the chat path reads
``tool_calls[]`` / ``delta.tool_calls[]`` (key ``id``), the responses path
reads ``function_call`` items / events (key ``call_id``). ``slots_to_calls``
bridges that one-key difference so the validation below runs once.

``typed_text_parts`` validates the responses reasoning part lists
(``summary_text`` / ``reasoning_text``): same shape, only the tag differs.
"""
from __future__ import annotations

import json
from typing import Any

from thyca.core.protocol import ToolCall

from ._http import redact
from .llm_base import LLMError


def parse_tool_calls(raw: object) -> list[ToolCall]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise LLMError("provider tool_calls must be a list")
    calls: list[ToolCall] = []
    for item in raw:
        if not isinstance(item, dict):
            raise LLMError("provider tool_call must be an object")
        fn = item.get("function")
        if not isinstance(fn, dict):
            fn = {}
        call_id = item.get("id")
        name = fn.get("name")
        if not isinstance(call_id, str) or not call_id:
            raise LLMError("provider tool_call missing id")
        if not isinstance(name, str) or not name:
            raise LLMError("provider tool_call missing name")
        arguments, parse_error = _arguments(fn.get("arguments", "{}"))
        calls.append(
            ToolCall(id=call_id, name=name, arguments=arguments, parse_error=parse_error)
        )
    return calls


def _arguments(arguments_raw: object) -> tuple[dict, str | None]:
    if isinstance(arguments_raw, dict):
        return arguments_raw, None
    if isinstance(arguments_raw, str):
        try:
            parsed = json.loads(arguments_raw) if arguments_raw else {}
        except json.JSONDecodeError:
            return {}, "invalid arguments"
        if not isinstance(parsed, dict):
            return {}, "invalid arguments"
        return parsed, None
    return {}, "invalid arguments"


def slots_to_calls(slots: dict[int, dict[str, str]], *, id_key: str = "id") -> list[ToolCall]:
    """Index-slot tool calls to ToolCalls; the responses path passes ``id_key='call_id'``."""
    if not slots:
        return []
    ordered = [slots[index] for index in sorted(slots)]
    return parse_tool_calls(
        [
            {
                "id": slot[id_key],
                "function": {"name": slot["name"], "arguments": slot["arguments"] or "{}"},
            }
            for slot in ordered
        ]
    )


def typed_text_parts(raw: object, key: str, part_type: str) -> list[dict[str, Any]]:
    """Validate typed text parts; invalid entries dropped, never raises."""
    out: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    for part in raw:
        if not isinstance(part, dict):
            continue
        if part.get("type") != part_type:
            continue
        text = part.get("text")
        if not isinstance(text, str) or not text:
            continue
        out.append({"type": part_type, "text": redact(text, key)})
    return out
