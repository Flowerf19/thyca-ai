"""Assemble a ChatReply from Chat Completions JSON or SSE."""
from __future__ import annotations

import json
from collections.abc import Callable

import httpx

from thyca.core.protocol import ToolCall

from .llm_base import ChatReply, LLMError, normalize_usage
from .streaming import ContentOut, ReasoningOut


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


def _reasoning_detail(item: object) -> dict | None:
    """Validate one reasoning_details item; None when unusable.

    Never raises: providers vary, and a malformed detail must not fail the
    whole turn. Mirrors pi's OpenAI detail shapes (text/summary/encrypted)
    plus the shared id/format/index passthrough.
    """
    if not isinstance(item, dict):
        return None
    dtype = item.get("type")
    if dtype == "reasoning.text":
        text = item.get("text")
        if not isinstance(text, str) or not text:
            return None
        out: dict[str, object] = {"type": dtype, "text": text}
        signature = item.get("signature")
        if isinstance(signature, str) and signature:
            out["signature"] = signature
    elif dtype == "reasoning.summary":
        summary = item.get("summary")
        if not isinstance(summary, str) or not summary:
            return None
        out = {"type": dtype, "summary": summary}
    elif dtype == "reasoning.encrypted":
        data = item.get("data")
        if not isinstance(data, str) or not data:
            return None
        out = {"type": dtype, "data": data}
    else:
        return None
    detail_id = item.get("id")
    if isinstance(detail_id, str) and detail_id:
        out["id"] = detail_id
    detail_format = item.get("format")
    if isinstance(detail_format, str) and detail_format:
        out["format"] = detail_format
    index = item.get("index")
    if isinstance(index, int) and not isinstance(index, bool):
        out["index"] = index
    return out


class _ReasoningDetailsOut:
    """Accumulate reasoning_details across chunks, merging consecutive splits.

    Streaming splits one logical block across chunks; consecutive same-type
    text/summary pieces concatenate (pi parity), anything else appends.
    """

    def __init__(self) -> None:
        self._items: list[dict] = []

    def add(self, raw: object) -> None:
        if not isinstance(raw, list):
            return
        for item in raw:
            detail = _reasoning_detail(item)
            if detail is None:
                continue
            last = self._items[-1] if self._items else None
            if (
                detail["type"] == "reasoning.text"
                and last is not None
                and last["type"] == "reasoning.text"
            ):
                last["text"] += detail["text"]
                if "signature" in detail:
                    last.setdefault("signature", detail["signature"])
                for key in ("id", "format", "index"):
                    if key in detail:
                        last.setdefault(key, detail[key])
            elif (
                detail["type"] == "reasoning.summary"
                and last is not None
                and last["type"] == "reasoning.summary"
            ):
                last["summary"] += detail["summary"]
                for key in ("id", "format", "index"):
                    if key in detail:
                        last.setdefault(key, detail[key])
            else:
                self._items.append(detail)

    def items(self) -> list[dict] | None:
        return [dict(item) for item in self._items] or None


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
    details = _ReasoningDetailsOut()
    details.add(message.get("reasoning_details"))
    return ChatReply(
        content=content,
        tool_calls=parse_tool_calls(message.get("tool_calls")),
        usage=usage,
        finish_reason=finish,
        model=model,
        reasoning=reasoning or None,
        reasoning_details=details.items(),
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
    on_content: Callable[[str], None] | None = None,
) -> ChatReply:
    content_parts: list[str] = []
    saw_content_str = False
    saw_content_null = False
    saw_choice = False
    finish = ""
    model: str | None = None
    usage: dict | None = None
    slots: dict[int, dict[str, str]] = {}
    reasoning = ReasoningOut(key, on_reasoning)
    content_out = ContentOut(on_content)
    details = _ReasoningDetailsOut()

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
                content_out.add(value)
        piece = _reasoning_text(delta)
        if piece:
            reasoning.add(piece)
        details.add(delta.get("reasoning_details"))
        _merge_tool_deltas(slots, delta.get("tool_calls"))

    if not saw_choice:
        raise LLMError("provider response missing choices")
    content_out.flush()
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
        reasoning_details=details.items(),
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
