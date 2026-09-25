"""Parse `/v1/responses` payloads: input mapping plus SSE/object assembly."""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx

from thyca.core.protocol import Message, ToolCall

from ._http import cap, iter_sse_data, parse_json_bytes, redact
from .llm_base import ChatReply, LLMError, normalize_usage
from .openai_parse import parse_tool_calls, slots_to_calls
from .streaming import ContentOut, ReasoningOut


def _to_responses_tools(tools: list) -> list[dict[str, Any]]:
    """Chat-schema tools (the ``Connect`` contract) to flat responses tools."""
    out: list[dict[str, Any]] = []
    for spec in tools:
        if not isinstance(spec, dict):
            continue
        fn = spec.get("function")
        if isinstance(fn, dict):
            name, desc, params = fn.get("name"), fn.get("description"), fn.get("parameters")
        else:
            name, desc, params = spec.get("name"), spec.get("description"), spec.get("parameters")
        if not isinstance(name, str) or not name:
            continue
        tool: dict[str, Any] = {
            "type": "function",
            "name": name,
            "parameters": params if isinstance(params, dict) else {},
        }
        if isinstance(desc, str) and desc:
            tool["description"] = desc
        out.append(tool)
    return out


def _message_items(message: Message) -> list[dict[str, Any]]:
    """One transcript message to zero or more ``input[]`` items."""
    if message.role == "system":
        # Empty (or None) system content passes through as empty: the
        # provider decides, mirroring the chat path which never drops.
        return [{"role": "system", "content": message.content or ""}]
    if message.role == "user":
        return [{"role": "user", "content": message.content or ""}]
    if message.role == "assistant":
        items = (
            [{"role": "assistant", "content": message.content}]
            if isinstance(message.content, str) and message.content
            else []
        )
        return items + [
            {
                "type": "function_call",
                "call_id": call.id,
                "name": call.name,
                "arguments": json.dumps(call.arguments, ensure_ascii=False),
            }
            for call in message.tool_calls or []
        ]
    if message.role == "tool":
        if not message.tool_call_id:
            # Always a bug (a result must answer a call): fail fast instead
            # of sending a function_call the provider sees as unanswered.
            raise ValueError("tool message is missing tool_call_id")
        return [
            {
                "type": "function_call_output",
                "call_id": message.tool_call_id,
                "output": message.content if isinstance(message.content, str) else "",
            }
        ]
    # Unknown roles are ignored for forward compatibility.
    return []


def _to_responses_input(messages: list[Message]) -> list[dict[str, Any]]:
    """Transcript to ``input[]`` items: messages + function calls/outputs."""
    return [item for message in messages for item in _message_items(message)]


def _function_slot(slots: dict[int, dict[str, str]], raw_index: object) -> dict[str, str]:
    index = raw_index if isinstance(raw_index, int) and not isinstance(raw_index, bool) else 0
    return slots.setdefault(index, {"call_id": "", "name": "", "arguments": ""})


def parse_responses_payload(raw: dict, key: str) -> ChatReply:
    """Assemble a ChatReply from a non-streaming `/v1/responses` object."""
    if not isinstance(raw, dict):
        raise LLMError("provider response must be an object")
    if isinstance(raw.get("error"), dict):
        raise LLMError(f"provider error: {redact(cap(json.dumps(raw['error'])), key)}")
    status = raw.get("status")
    # Failed means failed: a terminal bad status without an error object is
    # still a provider error, never a silent empty turn.
    if status in ("failed", "incomplete"):
        raise LLMError(f"provider response {status}")
    output = raw.get("output")
    if not isinstance(output, list):
        raise LLMError("provider response missing output")
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message":
            for part in item.get("content") or []:
                if isinstance(part, dict) and part.get("type") == "output_text":
                    text = part.get("text")
                    if isinstance(text, str) and text:
                        content_parts.append(text)
        elif item.get("type") == "reasoning":
            for part in item.get("summary") or []:
                if isinstance(part, dict) and part.get("type") == "summary_text":
                    text = part.get("text")
                    if isinstance(text, str) and text:
                        reasoning_parts.append(text)
    calls = [
        {
            "id": item.get("call_id"),
            "function": {"name": item.get("name"), "arguments": item.get("arguments", "{}")},
        }
        for item in output
        if isinstance(item, dict) and item.get("type") == "function_call"
    ]
    raw_usage = raw.get("usage")
    usage = normalize_usage(raw_usage, "openai_responses") if isinstance(raw_usage, dict) else None
    model = raw.get("model")
    return ChatReply(
        content=redact("".join(content_parts), key) or None,
        tool_calls=parse_tool_calls(calls),
        usage=usage,
        finish_reason=status if isinstance(status, str) and status else "completed",
        model=model if isinstance(model, str) else None,
        reasoning=redact("".join(reasoning_parts), key) or None,
    )


def parse_responses_bytes(raw: bytes, key: str) -> ChatReply:
    return parse_responses_payload(parse_json_bytes(raw, key), key)


async def read_responses_sse(
    response: httpx.Response,
    key: str,
    on_reasoning: Callable[[str], None] | None,
    on_content: Callable[[str], None] | None = None,
) -> ChatReply:
    content_parts: list[str] = []
    slots: dict[int, dict[str, str]] = {}
    reasoning = ReasoningOut(key, on_reasoning)
    content_out = ContentOut(on_content, key)
    usage: dict | None = None
    model: str | None = None
    status = ""
    completed = False

    async for chunk in iter_sse_data(response, key):
        event = chunk.get("type")
        if event == "response.output_text.delta":
            delta = chunk.get("delta")
            if isinstance(delta, str) and delta:
                content_parts.append(delta)
                content_out.add(delta)
        elif event in ("response.reasoning_summary_text.delta", "response.reasoning_text.delta"):
            delta = chunk.get("delta")
            if isinstance(delta, str) and delta:
                reasoning.add(delta)
        elif event == "response.output_item.added":
            item = chunk.get("item")
            if isinstance(item, dict) and item.get("type") == "function_call":
                slot = _function_slot(slots, chunk.get("output_index"))
                call_id = item.get("call_id")
                if isinstance(call_id, str) and call_id:
                    slot["call_id"] = call_id
                name = item.get("name")
                if isinstance(name, str) and name:
                    slot["name"] = name
        elif event == "response.function_call_arguments.delta":
            delta = chunk.get("delta")
            if isinstance(delta, str) and delta:
                _function_slot(slots, chunk.get("output_index"))["arguments"] += delta
        elif event == "response.function_call_arguments.done":
            arguments = chunk.get("arguments")
            if isinstance(arguments, str) and arguments:
                slot = _function_slot(slots, chunk.get("output_index"))
                if not slot["arguments"]:
                    slot["arguments"] = arguments
        elif event == "response.output_item.done":
            item = chunk.get("item")
            if isinstance(item, dict) and item.get("type") == "function_call":
                slot = _function_slot(slots, chunk.get("output_index"))
                for field, target in (("call_id", "call_id"), ("name", "name")):
                    value = item.get(field)
                    if isinstance(value, str) and value and not slot[target]:
                        slot[target] = value
                arguments = item.get("arguments")
                if isinstance(arguments, str) and arguments and not slot["arguments"]:
                    slot["arguments"] = arguments
        elif event == "response.completed":
            completed = True
            finished = chunk.get("response")
            if isinstance(finished, dict):
                if isinstance(finished.get("model"), str) and finished["model"]:
                    model = finished["model"]
                if isinstance(finished.get("status"), str) and finished["status"]:
                    status = finished["status"]
                raw_usage = finished.get("usage")
                if isinstance(raw_usage, dict):
                    usage = normalize_usage(raw_usage, "openai_responses")
                error = finished.get("error")
                if isinstance(error, dict) and error:
                    raise LLMError(
                        f"provider error: {redact(cap(json.dumps(error)), key)}"
                    )
                if status in ("failed", "incomplete"):
                    raise LLMError(f"provider response {status}")
        # Unknown events (created, in_progress, subscription_usage, ...) are ignored.

    if not completed:
        raise LLMError("provider response incomplete")
    content_out.finish()
    return ChatReply(
        content=redact("".join(content_parts), key) or None,
        tool_calls=slots_to_calls(slots, id_key="call_id"),
        usage=usage,
        finish_reason=status or "completed",
        model=model,
        reasoning=reasoning.text(),
    )


