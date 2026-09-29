"""OpenAI `/v1/responses` connect: streaming text + thinking summaries + tools.

Same ``Connect`` contract as :class:`OpenAIChat`; only the wire protocol
differs (input items, response events, usage shape). Retry/timeout/error
semantics intentionally mirror ``openai_chat``. Transcript-to-``input[]``
mapping plus SSE/object assembly live here too (merged from
``responses_parse.py``, TASK-006).
"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx

from thyca.core.protocol import Message

from ._http import BaseConnect, cap, iter_sse_data, parse_json_bytes, redact
from ._shared import parse_tool_calls, slots_to_calls, typed_text_parts
from .llm_base import ChatReply, LLMError, normalize_usage
from .streaming import ContentOut, ReasoningOut


def _output_texts(raw: object) -> list[str]:
    """Valid output_text strings from a message item's content list."""
    if not isinstance(raw, list):
        return []
    return [
        text
        for part in raw
        if isinstance(part, dict) and part.get("type") == "output_text"
        for text in (part.get("text"),)
        if isinstance(text, str) and text
    ]


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


def _reasoning_summary_parts(raw: object, key: str) -> list[dict[str, Any]]:
    """Validate reasoning summary[] parts; invalid entries dropped, never raises."""
    return typed_text_parts(raw, key, "summary_text")


def _reasoning_content_parts(raw: object, key: str) -> list[dict[str, Any]]:
    """Validate reasoning content[] parts (reasoning_text); invalid dropped."""
    return typed_text_parts(raw, key, "reasoning_text")


def _responses_reasoning_detail(item: object, key: str = "") -> dict[str, Any] | None:
    """Validate one reasoning item; None when unusable. Never raises.

    Chat parity (openai_chat._reasoning_detail): type check, required-field
    check, id passthrough, summary/text redacted, signature blobs untouched
    and never capped (truncating would break round-trip).
    """
    if not isinstance(item, dict):
        return None
    if item.get("type") != "reasoning":
        return None
    out: dict[str, Any] = {"type": "reasoning"}
    item_id = item.get("id")
    if isinstance(item_id, str) and item_id.strip():
        out["id"] = item_id
    raw_summary = item.get("summary")
    if isinstance(raw_summary, list):
        summary = _reasoning_summary_parts(raw_summary, key)
        if summary or not raw_summary:
            # An explicitly present valid empty summary is kept: the
            # official input schema requires summary but not nonemptiness.
            # A nonempty raw list validating to nothing is malformed input
            # and stays dropped.
            out["summary"] = summary
    content = _reasoning_content_parts(item.get("content"), key)
    if content:
        out["content"] = content
    encrypted = item.get("encrypted_content")
    if isinstance(encrypted, str) and encrypted:
        out["encrypted_content"] = encrypted
    signature = item.get("signature")
    if isinstance(signature, str) and signature:
        out["signature"] = signature
    if len(out) == 1:
        return None
    return out


def _reasoning_input_item(detail: dict[str, Any] | None) -> dict[str, Any] | None:
    """Validated detail to a provider-acceptable reasoning input, or None."""
    if detail is None:
        return None
    summary = detail.get("summary")
    if "summary" not in detail or not isinstance(summary, list):
        # Missing or malformed summary (never the wire: live 400). An
        # explicitly present empty list passes — empty is not missing.
        return None
    item_id = detail.get("id")
    if not isinstance(item_id, str) or not item_id.strip():
        # Native reasoning without a usable id cannot round-trip: the
        # official input schema requires id alongside summary and type.
        return None
    item: dict[str, Any] = {"type": "reasoning", "summary": summary}
    item["id"] = item_id
    encrypted = detail.get("encrypted_content")
    if isinstance(encrypted, str) and encrypted:
        item["encrypted_content"] = encrypted
    return item


def _message_items(message: Message) -> list[dict[str, Any]]:
    """One transcript message to zero or more ``input[]`` items."""
    if message.role == "system":
        # Empty (or None) system content passes through as empty: the
        # provider decides, mirroring the chat path which never drops.
        return [{"role": "system", "content": message.content or ""}]
    if message.role == "user":
        return [{"role": "user", "content": message.content or ""}]
    if message.role == "assistant":
        # Round-trip prior reasoning items for tool-loop continuity (chat
        # parity: _to_openai_message sends reasoning_details verbatim).
        # Native reasoning items only; chat shapes/invalid ignored, never
        # raises. Providers require `summary` on reasoning inputs, so
        # summary-less items (id/encrypted-only) are dropped, and only the
        # input-contract keys (id/summary/encrypted_content) are sent. The
        # official input schema also permits `content`, but this path omits
        # content/signature: output-observed fields with no evidenced
        # acceptance on the supported compatible endpoint — do not re-add
        # them without a passing transport test.
        prefix: list[dict[str, Any]] = []
        if message.reasoning_details:
            for detail in message.reasoning_details:
                validated = _responses_reasoning_detail(detail, "")
                item = _reasoning_input_item(validated)
                if item is not None:
                    prefix.append(item)
        items = (
            [{"role": "assistant", "content": message.content}]
            if isinstance(message.content, str) and message.content
            else []
        )
        return prefix + items + [
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
    details: list[dict[str, Any]] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message":
            content_parts.extend(_output_texts(item.get("content")))
        elif item.get("type") == "reasoning":
            # Reuse the validator (already redacted; the final redact below
            # is idempotent) so non-list summary cannot iterate chars.
            reasoning_parts.extend(
                part["text"]
                for part in _reasoning_summary_parts(item.get("summary"), key)
            )
            detail = _responses_reasoning_detail(item, key)
            if detail is not None:
                details.append(detail)
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
        reasoning_details=details or None,
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
    details: list[dict[str, Any]] = []
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
            if not isinstance(item, dict):
                continue
            if item.get("type") == "function_call":
                slot = _function_slot(slots, chunk.get("output_index"))
                for field, target in (("call_id", "call_id"), ("name", "name")):
                    value = item.get(field)
                    if isinstance(value, str) and value and not slot[target]:
                        slot[target] = value
                arguments = item.get("arguments")
                if isinstance(arguments, str) and arguments and not slot["arguments"]:
                    slot["arguments"] = arguments
            elif item.get("type") == "reasoning":
                # Done carries the full item (id/summary/encrypted_content);
                # added is id-only and would duplicate, so only done is kept.
                detail = _responses_reasoning_detail(item, key)
                if detail is not None:
                    details.append(detail)
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
        reasoning_details=details or None,
    )


def _responses_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/responses"


class OpenAIResponses(BaseConnect):
    DROP_PARAM = "reasoning"
    DROP_MARKER = "reasoning"

    async def chat(
        self,
        messages: list[Message],
        tools: list | None = None,
        on_reasoning: Callable[[str], None] | None = None,
        on_content: Callable[[str], None] | None = None,
    ) -> ChatReply:
        payload: dict[str, Any] = {
            "model": self._provider.model,
            "input": _to_responses_input(messages),
            "stream": True,
            "store": False,
        }
        if tools:
            converted = _to_responses_tools(tools)
            if converted:
                payload["tools"] = converted
        if self._provider.reasoningEffort:
            payload["reasoning"] = {"effort": self._provider.reasoningEffort, "summary": "auto"}

        key = self._provider.api_key()
        headers = {"Authorization": f"Bearer {key}"}
        url = _responses_url(self._provider.baseUrl)
        return await self._request(url, payload, headers, key, on_reasoning, on_content)

    async def _read_sse(
        self,
        response: httpx.Response,
        key: str,
        on_reasoning: Callable[[str], None] | None,
        on_content: Callable[[str], None] | None,
    ) -> ChatReply:
        return await read_responses_sse(response, key, on_reasoning, on_content)

    def _parse_bytes(self, raw: bytes, key: str) -> ChatReply:
        return parse_responses_bytes(raw, key)
