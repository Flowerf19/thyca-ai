"""Anthropic `/v1/messages` connect: streaming text, adaptive thinking, and tool use."""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx

from thyca.core.protocol import Message, ToolCall

from ._http import BaseConnect, cap, iter_sse_data, parse_json_bytes, redact
from ._shared import _arguments, iter_tool_specs, resolve_finish_reason, slots_to_calls
from .llm_base import ChatReply, LLMError, normalize_usage
from .streaming import ContentOut, ReasoningOut

ANTHROPIC_VERSION = "2023-06-01"
_EFFORT_VALUES = frozenset({"low", "medium", "high", "xhigh", "max"})
_DROP_MARKERS = ("thinking", "output_config", "effort")


def _messages_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/messages"


def _to_anthropic_tools(tools: list) -> list[dict[str, Any]]:
    """Chat-schema tools (the ``Connect`` contract) to Anthropic tool definitions."""
    out: list[dict[str, Any]] = []
    for spec in iter_tool_specs(tools):
        if spec.parameters:
            schema = dict(spec.parameters)
            schema.setdefault("type", "object")
        else:
            schema = {"type": "object", "properties": {}}
        tool: dict[str, Any] = {"name": spec.name, "input_schema": schema}
        if spec.description:
            tool["description"] = spec.description
        out.append(tool)
    return out


def _anthropic_reasoning_block(detail: object) -> dict[str, Any] | None:
    """Validate one Anthropic thinking/redacted_thinking detail for round-trip."""
    if not isinstance(detail, dict):
        return None
    dtype = detail.get("type")
    if dtype == "thinking":
        thinking = detail.get("thinking")
        signature = detail.get("signature")
        if isinstance(thinking, str) and isinstance(signature, str) and signature:
            return {"type": "thinking", "thinking": thinking, "signature": signature}
        return None
    if dtype == "redacted_thinking":
        data = detail.get("data")
        if isinstance(data, str) and data:
            return {"type": "redacted_thinking", "data": data}
        return None
    return None


def _append_role_blocks(
    out: list[dict[str, Any]], role: str, blocks: list[dict[str, Any]]
) -> None:
    if not blocks:
        return
    if out and out[-1]["role"] == role:
        out[-1]["content"].extend(blocks)
    else:
        out.append({"role": role, "content": list(blocks)})


def _to_anthropic_messages(messages: list[Message]) -> tuple[str, list[dict[str, Any]]]:
    """Split system prompts and convert transcript into Anthropic alternating messages."""
    system_parts: list[str] = []
    out: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "system":
            if isinstance(message.content, str) and message.content:
                system_parts.append(message.content)
            continue
        if message.role == "user":
            text = message.content if isinstance(message.content, str) else ""
            _append_role_blocks(out, "user", [{"type": "text", "text": text}])
            continue
        if message.role == "assistant":
            blocks: list[dict[str, Any]] = []
            if message.reasoning_details:
                for detail in message.reasoning_details:
                    block = _anthropic_reasoning_block(detail)
                    if block is not None:
                        blocks.append(block)
            if isinstance(message.content, str) and message.content:
                blocks.append({"type": "text", "text": message.content})
            blocks.extend(
                {
                    "type": "tool_use",
                    "id": call.id,
                    "name": call.name,
                    "input": call.arguments,
                }
                for call in message.tool_calls or []
            )
            _append_role_blocks(out, "assistant", blocks)
            continue
        if message.role == "tool":
            if not message.tool_call_id:
                raise ValueError("tool message is missing tool_call_id")
            result_block: dict[str, Any] = {
                "type": "tool_result",
                "tool_use_id": message.tool_call_id,
                "content": message.content if isinstance(message.content, str) else "",
            }
            if isinstance(message.meta, dict) and message.meta.get("is_error") is True:
                result_block["is_error"] = True
            _append_role_blocks(out, "user", [result_block])
    return "\n\n".join(system_parts), out


def parse_anthropic_payload(raw: dict, key: str) -> ChatReply:
    """Assemble a ChatReply from a non-streaming Anthropic `/v1/messages` response."""
    if not isinstance(raw, dict):
        raise LLMError("provider response must be an object")
    if raw.get("type") == "error" or isinstance(raw.get("error"), dict):
        err = raw.get("error") if isinstance(raw.get("error"), dict) else raw
        raise LLMError(f"provider error: {redact(cap(json.dumps(err)), key)}")
    blocks = raw.get("content")
    if not isinstance(blocks, list):
        raise LLMError("provider response missing content")
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    details: list[dict[str, Any]] = []
    calls: list[ToolCall] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text = block.get("text")
            if isinstance(text, str):
                content_parts.append(text)
        elif btype == "thinking":
            thinking = block.get("thinking")
            if isinstance(thinking, str) and thinking:
                reasoning_parts.append(thinking)
            signature = block.get("signature")
            if isinstance(thinking, str) and isinstance(signature, str) and signature:
                details.append(
                    {
                        "type": "thinking",
                        "thinking": redact(thinking, key),
                        "signature": signature,
                    }
                )
        elif btype == "redacted_thinking":
            data = block.get("data")
            if isinstance(data, str) and data:
                details.append({"type": "redacted_thinking", "data": data})
        elif btype == "tool_use":
            call_id = block.get("id")
            name = block.get("name")
            if not isinstance(call_id, str) or not call_id:
                raise LLMError("provider tool_call missing id")
            if not isinstance(name, str) or not name:
                raise LLMError("provider tool_call missing name")
            arguments, parse_error = _arguments(block.get("input", {}))
            calls.append(
                ToolCall(id=call_id, name=name, arguments=arguments, parse_error=parse_error)
            )
    raw_usage = raw.get("usage")
    usage = (
        normalize_usage(raw_usage, "anthropic_messages") if isinstance(raw_usage, dict) else None
    )
    model = raw.get("model")
    stop_reason = raw.get("stop_reason") or ""
    if not isinstance(stop_reason, str):
        stop_reason = str(stop_reason)
    content = redact("".join(content_parts), key) or None
    return ChatReply(
        content=content,
        tool_calls=calls,
        usage=usage,
        finish_reason=resolve_finish_reason(stop_reason, content=content, calls=calls, key=key),
        model=model if isinstance(model, str) else None,
        reasoning=redact("".join(reasoning_parts), key) or None,
        reasoning_details=details or None,
    )


def parse_anthropic_bytes(raw: bytes, key: str) -> ChatReply:
    return parse_anthropic_payload(parse_json_bytes(raw, key), key)


def _block_index(raw_index: object) -> int:
    if isinstance(raw_index, int) and not isinstance(raw_index, bool) and raw_index >= 0:
        return raw_index
    return 0


async def read_anthropic_sse(
    response: httpx.Response,
    key: str,
    on_reasoning: Callable[[str], None] | None,
    on_content: Callable[[str], None] | None = None,
) -> ChatReply:
    content_parts: list[str] = []
    slots: dict[int, dict[str, str]] = {}
    thinking_blocks: dict[int, dict[str, str]] = {}
    details: list[dict[str, Any]] = []
    reasoning = ReasoningOut(key, on_reasoning)
    content_out = ContentOut(on_content, key)
    raw_usage: dict[str, Any] = {}
    model: str | None = None
    stop_reason = ""
    completed = False

    async for chunk in iter_sse_data(response, key):
        event = chunk.get("type")
        if event == "ping":
            continue
        if event == "error":
            err = chunk.get("error") if isinstance(chunk.get("error"), dict) else chunk
            raise LLMError(f"provider error: {redact(cap(json.dumps(err)), key)}")
        if event == "message_start":
            msg = chunk.get("message")
            if isinstance(msg, dict):
                if isinstance(msg.get("model"), str) and msg["model"]:
                    model = msg["model"]
                if isinstance(msg.get("usage"), dict):
                    raw_usage.update(msg["usage"])
        elif event == "content_block_start":
            idx = _block_index(chunk.get("index"))
            block = chunk.get("content_block")
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                text = block.get("text")
                if isinstance(text, str) and text:
                    content_parts.append(text)
                    content_out.add(text)
            elif btype == "thinking":
                text = block.get("thinking") if isinstance(block.get("thinking"), str) else ""
                sig = block.get("signature") if isinstance(block.get("signature"), str) else ""
                thinking_blocks[idx] = {"thinking": text, "signature": sig}
                if text:
                    reasoning.add(text)
            elif btype == "redacted_thinking":
                data = block.get("data")
                if isinstance(data, str) and data:
                    details.append({"type": "redacted_thinking", "data": data})
            elif btype == "tool_use":
                call_id = block.get("id") if isinstance(block.get("id"), str) else ""
                name = block.get("name") if isinstance(block.get("name"), str) else ""
                raw_input = block.get("input")
                initial_args = (
                    json.dumps(raw_input, ensure_ascii=False)
                    if isinstance(raw_input, dict) and raw_input
                    else ""
                )
                slots[idx] = {"id": call_id, "name": name, "arguments": initial_args}
        elif event == "content_block_delta":
            idx = _block_index(chunk.get("index"))
            delta = chunk.get("delta")
            if not isinstance(delta, dict):
                continue
            dtype = delta.get("type")
            if dtype == "text_delta":
                text = delta.get("text")
                if isinstance(text, str) and text:
                    content_parts.append(text)
                    content_out.add(text)
            elif dtype == "thinking_delta":
                piece = delta.get("thinking")
                if isinstance(piece, str) and piece:
                    tb = thinking_blocks.setdefault(idx, {"thinking": "", "signature": ""})
                    tb["thinking"] += piece
                    reasoning.add(piece)
            elif dtype == "signature_delta":
                sig = delta.get("signature")
                if isinstance(sig, str) and sig:
                    tb = thinking_blocks.setdefault(idx, {"thinking": "", "signature": ""})
                    tb["signature"] += sig
            elif dtype == "input_json_delta":
                part = delta.get("partial_json")
                if isinstance(part, str) and part:
                    slot = slots.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                    slot["arguments"] += part
        elif event == "content_block_stop":
            idx = _block_index(chunk.get("index"))
            tb = thinking_blocks.pop(idx, None)
            if tb is not None and tb["signature"]:
                details.append(
                    {
                        "type": "thinking",
                        "thinking": redact(tb["thinking"], key),
                        "signature": tb["signature"],
                    }
                )
        elif event == "message_delta":
            delta = chunk.get("delta")
            if isinstance(delta, dict):
                reason = delta.get("stop_reason")
                if isinstance(reason, str) and reason:
                    stop_reason = reason
            delta_usage = chunk.get("usage")
            if isinstance(delta_usage, dict):
                raw_usage.update(
                    {ukey: uval for ukey, uval in delta_usage.items() if uval is not None}
                )
        elif event == "message_stop":
            completed = True

    if not completed:
        raise LLMError("provider response incomplete")
    content_out.finish()
    usage = normalize_usage(raw_usage, "anthropic_messages") if raw_usage else None
    content = redact("".join(content_parts), key) or None
    calls = slots_to_calls(slots)
    return ChatReply(
        content=content,
        tool_calls=calls,
        usage=usage,
        finish_reason=resolve_finish_reason(stop_reason, content=content, calls=calls, key=key),
        model=model,
        reasoning=reasoning.text(),
        reasoning_details=details or None,
    )


class AnthropicMessages(BaseConnect):
    DROP_PARAM = "thinking"
    DROP_MARKER = "thinking"

    def _drop_reasoning(self, payload: dict[str, Any], body: str) -> dict[str, Any] | None:
        lowered = body.lower()
        if not any(marker in lowered for marker in _DROP_MARKERS):
            return None
        if "thinking" not in payload and "output_config" not in payload:
            return None
        return {k: v for k, v in payload.items() if k not in ("thinking", "output_config")}

    async def chat(
        self,
        messages: list[Message],
        tools: list | None = None,
        on_reasoning: Callable[[str], None] | None = None,
        on_content: Callable[[str], None] | None = None,
    ) -> ChatReply:
        system_prompt, wire_messages = _to_anthropic_messages(messages)
        payload: dict[str, Any] = {
            "model": self._provider.model,
            "messages": wire_messages,
            "max_tokens": self._provider.maxTokens,
            "stream": True,
        }
        if system_prompt:
            payload["system"] = system_prompt
        if tools:
            converted = _to_anthropic_tools(tools)
            if converted:
                payload["tools"] = converted
        if self._provider.reasoningEffort:
            effort = self._provider.reasoningEffort.strip().lower()
            if effort in _EFFORT_VALUES:
                payload["thinking"] = {"type": "adaptive", "display": "summarized"}
                payload["output_config"] = {"effort": effort}

        key = self._provider.api_key()
        headers = {
            "x-api-key": key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        url = _messages_url(self._provider.baseUrl)
        return await self._request(url, payload, headers, key, on_reasoning, on_content)

    async def _read_sse(
        self,
        response: httpx.Response,
        key: str,
        on_reasoning: Callable[[str], None] | None,
        on_content: Callable[[str], None] | None,
    ) -> ChatReply:
        return await read_anthropic_sse(response, key, on_reasoning, on_content)

    def _parse_bytes(self, raw: bytes, key: str) -> ChatReply:
        return parse_anthropic_bytes(raw, key)
