"""Assemble a ChatReply from Chat Completions JSON or SSE."""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

import httpx

from thyca.protocol import RESULT_CAP_BYTES, ToolCall

from .llm_base import ChatReply, LLMError, normalize_usage

_FLUSH_CHARS = 64
_FLUSH_S = 0.08


def _redact(text: str, secret: str) -> str:
    if secret and secret in text:
        return text.replace(secret, "[redacted]")
    return text


def _cap(text: str, limit: int = 500) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _reasoning_text(payload: dict) -> str:
    # Same field set pi reads for OpenAI-compatible providers; providers pick
    # whichever they like (this one uses "reasoning").
    for name in ("reasoning_content", "reasoning", "reasoning_text"):
        value = payload.get(name)
        if isinstance(value, str) and value:
            return value
    return ""


class _ReasoningOut:
    def __init__(self, key: str, on_reasoning: Callable[[str], None] | None) -> None:
        self._key = key
        self._on = on_reasoning
        self._buf = ""
        self._last = time.monotonic()
        self._parts: list[str] = []
        self._bytes = 0
        self._capped = False

    def add(self, piece: str) -> None:
        if self._capped or not piece:
            return
        piece = _redact(piece, self._key)
        encoded = piece.encode("utf-8")
        room = RESULT_CAP_BYTES - self._bytes
        if room <= 0:
            self._capped = True
            return
        if len(encoded) > room:
            piece = encoded[:room].decode("utf-8", errors="ignore")
            encoded = piece.encode("utf-8")
            self._capped = True
            if not piece:
                return
        self._parts.append(piece)
        self._bytes += len(encoded)
        self._buf += piece
        if len(self._buf) >= _FLUSH_CHARS or (time.monotonic() - self._last) >= _FLUSH_S:
            self.flush()

    def flush(self) -> None:
        if not self._buf:
            return
        chunk, self._buf = self._buf, ""
        self._last = time.monotonic()
        if self._on is None:
            return
        try:
            self._on(chunk)
        except Exception:
            pass

    def text(self) -> str | None:
        self.flush()
        return "".join(self._parts) or None


def parse_chat_payload(raw: dict, key: str) -> ChatReply:
    if not isinstance(raw, dict):
        raise LLMError("provider response must be an object")
    choices = raw.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMError("provider response missing choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise LLMError("provider choice must be an object")
    message = first.get("message")
    if not isinstance(message, dict):
        raise LLMError("provider message missing")
    content = message.get("content")
    if content is not None and not isinstance(content, str):
        raise LLMError("provider content must be string or null")
    finish = first.get("finish_reason") or ""
    if not isinstance(finish, str):
        finish = str(finish)
    raw_usage = raw.get("usage")
    usage = normalize_usage(raw_usage, "openai") if isinstance(raw_usage, dict) else None
    model = raw.get("model")
    if not isinstance(model, str):
        model = None
    reasoning = _reasoning_text(message)
    return ChatReply(
        content=content,
        tool_calls=parse_tool_calls(message.get("tool_calls")),
        usage=usage,
        finish_reason=finish,
        model=model,
        reasoning=reasoning or None,
    )


def parse_chat_bytes(raw: bytes, key: str) -> ChatReply:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LLMError(_redact(_cap(raw.decode("utf-8", errors="replace")), key)) from exc
    if not isinstance(payload, dict):
        raise LLMError("provider response must be an object")
    return parse_chat_payload(payload, key)


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


async def read_sse_reply(
    response: httpx.Response,
    key: str,
    on_reasoning: Callable[[str], None] | None,
) -> ChatReply:
    content_parts: list[str] = []
    saw_content_str = False
    saw_content_null = False
    saw_choice = False
    finish = ""
    model: str | None = None
    usage: dict | None = None
    slots: dict[int, dict[str, str]] = {}
    reasoning = _ReasoningOut(key, on_reasoning)

    async for line in response.aiter_lines():
        if not line or line.startswith(":"):
            continue
        if not line.startswith("data:"):
            continue
        data = line[5:].lstrip()
        if data == "[DONE]":
            break
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError as exc:
            raise LLMError(_redact(_cap(data), key)) from exc
        if not isinstance(chunk, dict):
            continue
        if isinstance(chunk.get("model"), str) and chunk["model"]:
            model = chunk["model"]
        raw_usage = chunk.get("usage")
        if isinstance(raw_usage, dict):
            usage = normalize_usage(raw_usage, "openai")
        choices = chunk.get("choices")
        if not isinstance(choices, list) or not choices:
            continue
        first = choices[0]
        if not isinstance(first, dict):
            continue
        saw_choice = True
        reason = first.get("finish_reason")
        if isinstance(reason, str) and reason:
            finish = reason
        delta = first.get("delta")
        if not isinstance(delta, dict):
            continue
        if "content" in delta:
            value = delta["content"]
            if value is None:
                saw_content_null = True
            elif isinstance(value, str):
                saw_content_str = True
                content_parts.append(value)
        piece = _reasoning_text(delta)
        if piece:
            reasoning.add(piece)
        _merge_tool_deltas(slots, delta.get("tool_calls"))

    if not saw_choice:
        raise LLMError("provider response missing choices")
    if saw_content_str:
        content: str | None = "".join(content_parts)
    elif saw_content_null:
        content = None
    else:
        content = None
    return ChatReply(
        content=content,
        tool_calls=_slots_to_calls(slots),
        usage=usage,
        finish_reason=finish,
        model=model,
        reasoning=reasoning.text(),
    )


def _merge_tool_deltas(slots: dict[int, dict[str, str]], raw: object) -> None:
    if not isinstance(raw, list):
        return
    for item in raw:
        if not isinstance(item, dict):
            continue
        index = item.get("index", 0)
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            index = 0
        slot = slots.setdefault(index, {"id": "", "name": "", "arguments": ""})
        call_id = item.get("id")
        if isinstance(call_id, str) and call_id:
            slot["id"] = call_id
        fn = item.get("function")
        if not isinstance(fn, dict):
            continue
        name = fn.get("name")
        if isinstance(name, str) and name:
            slot["name"] = name
        arguments = fn.get("arguments")
        if isinstance(arguments, str):
            slot["arguments"] += arguments
        elif isinstance(arguments, dict) and not slot["arguments"]:
            slot["arguments"] = json.dumps(arguments, ensure_ascii=False)


def _slots_to_calls(slots: dict[int, dict[str, str]]) -> list[ToolCall]:
    if not slots:
        return []
    ordered = [slots[index] for index in sorted(slots)]
    return parse_tool_calls(
        [
            {
                "id": slot["id"],
                "function": {"name": slot["name"], "arguments": slot["arguments"] or "{}"},
            }
            for slot in ordered
        ]
    )
