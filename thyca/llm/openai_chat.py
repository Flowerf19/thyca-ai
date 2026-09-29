"""Chat Completions wire: build the request, parse JSON, read SSE replies."""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx

from thyca.core.protocol import Message

from ._http import BaseConnect, iter_sse_data, parse_json_bytes, redact

# Re-exported: tests import these from openai_chat.
from ._shared import parse_tool_calls, slots_to_calls
from .llm_base import ChatReply, LLMError, normalize_usage
from .streaming import ContentOut, ReasoningOut


def _chat_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/chat/completions"


class OpenAIChat(BaseConnect):
    DROP_PARAM = "reasoning_effort"
    DROP_MARKER = "reasoning_effort"

    async def chat(
        self,
        messages: list[Message],
        tools: list | None = None,
        on_reasoning: Callable[[str], None] | None = None,
        on_content: Callable[[str], None] | None = None,
    ) -> ChatReply:
        payload: dict[str, Any] = {
            "model": self._provider.model,
            "messages": [_to_openai_message(m) for m in messages],
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            payload["tools"] = tools
        # Some OpenAI-compatible providers reject reasoning_effort for
        # non-reasoning models; _request drops it once and retries in that case.
        if self._provider.reasoningEffort:
            payload["reasoning_effort"] = self._provider.reasoningEffort

        key = self._provider.api_key()
        headers = {"Authorization": f"Bearer {key}"}
        url = _chat_url(self._provider.baseUrl)
        return await self._request(url, payload, headers, key, on_reasoning, on_content)

    async def _read_sse(
        self,
        response: httpx.Response,
        key: str,
        on_reasoning: Callable[[str], None] | None,
        on_content: Callable[[str], None] | None,
    ) -> ChatReply:
        return await read_sse_reply(response, key, on_reasoning, on_content)

    def _parse_bytes(self, raw: bytes, key: str) -> ChatReply:
        return parse_chat_bytes(raw, key)


def _to_openai_message(message: Message) -> dict[str, Any]:
    payload: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.reasoning_details:
        # Round-trip provider thinking signatures; absent for providers that
        # never emit them, so payloads there are byte-identical to before.
        # Responses-native items (type "reasoning") are not part of the
        # Chat contract (chat shapes are reasoning.text/summary/encrypted):
        # history persisted under Responses must not leak them onto the Chat
        # wire after a model/provider switch. Chat shapes pass byte-identical.
        kept = [
            dict(detail)
            for detail in message.reasoning_details
            if isinstance(detail, dict) and detail.get("type") != "reasoning"
        ]
        if kept:
            payload["reasoning_details"] = kept
    if message.tool_calls:
        payload["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False),
                },
            }
            for call in message.tool_calls
        ]
    if message.tool_call_id is not None:
        payload["tool_call_id"] = message.tool_call_id
    return payload


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

    def __init__(self, key: str = "") -> None:
        self._key = key
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
        # Redact the merged text, not each chunk: a key split across two
        # SSE chunks is whole only here.
        out = []
        for item in self._items:
            copied = dict(item)
            for field in ("text", "summary"):
                value = copied.get(field)
                if isinstance(value, str) and value:
                    copied[field] = redact(value, self._key)
            out.append(copied)
        return out or None


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
    details = _ReasoningDetailsOut(key)
    details.add(message.get("reasoning_details"))
    return ChatReply(
        content=redact(content, key) if isinstance(content, str) else content,
        tool_calls=parse_tool_calls(message.get("tool_calls")),
        usage=usage,
        finish_reason=finish,
        model=model,
        reasoning=redact(reasoning, key) or None,
        reasoning_details=details.items(),
    )


def parse_chat_bytes(raw: bytes, key: str) -> ChatReply:
    return parse_chat_payload(parse_json_bytes(raw, key), key)


async def read_sse_reply(
    response: httpx.Response,
    key: str,
    on_reasoning: Callable[[str], None] | None,
    on_content: Callable[[str], None] | None = None,
) -> ChatReply:
    content_parts: list[str] = []
    saw_content_str = False
    saw_choice = False
    finish = ""
    model: str | None = None
    usage: dict | None = None
    slots: dict[int, dict[str, str]] = {}
    reasoning = ReasoningOut(key, on_reasoning)
    content_out = ContentOut(on_content, key)
    details = _ReasoningDetailsOut(key)
    saw_done: list[bool] = []

    async for chunk in iter_sse_data(response, key, done=saw_done):
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
            if isinstance(value, str):
                saw_content_str = True
                content_parts.append(value)
                content_out.add(value)
            elif value is not None:
                # Array (or other non-string) delta content: the non-stream
                # path raises on this shape, so the stream must too — no
                # silent text loss.
                raise LLMError("provider content must be string or null")
        piece = _reasoning_text(delta)
        if piece:
            reasoning.add(piece)
        details.add(delta.get("reasoning_details"))
        _merge_tool_deltas(slots, delta.get("tool_calls"))

    if not saw_choice:
        raise LLMError("provider response missing choices")
    if not finish and not saw_done:
        # Clean EOF with neither finish_reason nor [DONE]: the provider cut
        # the stream, so this is provider-incomplete, never partial success.
        raise LLMError("provider response incomplete")
    content_out.finish()
    content: str | None = redact("".join(content_parts), key) if saw_content_str else None
    return ChatReply(
        content=content,
        tool_calls=slots_to_calls(slots),
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
