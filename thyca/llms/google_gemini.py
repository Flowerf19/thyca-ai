"""Google Gemini REST connect: streaming text, thoughts/signatures, and function calling."""
from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx

from thyca.core.protocol import Message, ToolCall

from ._http import BaseConnect, cap, iter_sse_data, parse_json_bytes, redact
from ._shared import _arguments
from .llm_base import ChatReply, LLMError, normalize_usage
from .streaming import ContentOut, ReasoningOut

_THINKING_LEVELS: dict[str, str] = {
    "minimal": "MINIMAL",
    "low": "LOW",
    "medium": "MEDIUM",
    "high": "HIGH",
    "xhigh": "HIGH",
    "max": "HIGH",
}
_DROP_MARKERS = (
    "thinking",
    "thinkinglevel",
    "thinking_level",
    "thinkingconfig",
    "thinking_config",
    "includethoughts",
    "include_thoughts",
)


def _clean_model_id(model: str) -> str:
    return model.strip().removeprefix("models/")


def _gemini_stream_url(base_url: str, model: str) -> str:
    clean = _clean_model_id(model)
    return f"{base_url.rstrip('/')}/models/{quote(clean, safe='')}:streamGenerateContent?alt=sse"


def _to_gemini_tools(tools: list) -> list[dict[str, Any]]:
    """Chat-schema tools (the ``Connect`` contract) to Gemini functionDeclarations."""
    declarations: list[dict[str, Any]] = []
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
        decl: dict[str, Any] = {"name": name}
        if isinstance(desc, str) and desc:
            decl["description"] = desc
        if isinstance(params, dict) and params:
            decl["parametersJsonSchema"] = params
        declarations.append(decl)
    if not declarations:
        return []
    return [{"functionDeclarations": declarations}]


def _append_content_parts(
    out: list[dict[str, Any]], role: str, parts: list[dict[str, Any]]
) -> None:
    if not parts:
        return
    if out and out[-1]["role"] == role:
        out[-1]["parts"].extend(parts)
    else:
        out.append({"role": role, "parts": list(parts)})


def _extract_gemini_signatures(
    details: list[dict] | None,
) -> tuple[list[dict[str, Any]], dict[str, str], str]:
    thought_parts: list[dict[str, Any]] = []
    call_sigs: dict[str, str] = {}
    text_sig = ""
    if not details:
        return thought_parts, call_sigs, text_sig
    for detail in details:
        if not isinstance(detail, dict):
            continue
        dtype = detail.get("type")
        if dtype == "gemini.thought":
            sig = detail.get("thoughtSignature")
            text = detail.get("text")
            part: dict[str, Any] = {"thought": True}
            if isinstance(text, str) and text:
                part["text"] = text
            if isinstance(sig, str) and sig:
                part["thoughtSignature"] = sig
            if "text" in part or "thoughtSignature" in part:
                thought_parts.append(part)
        elif dtype == "gemini.call_signature":
            cid = detail.get("call_id")
            sig = detail.get("thoughtSignature")
            if isinstance(cid, str) and cid and isinstance(sig, str) and sig:
                call_sigs[cid] = sig
        elif dtype == "gemini.text_signature":
            sig = detail.get("thoughtSignature")
            if isinstance(sig, str) and sig:
                text_sig = sig
    return thought_parts, call_sigs, text_sig


def _to_gemini_contents(
    messages: list[Message],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Convert transcript into Gemini ``systemInstruction`` + ``contents[]``."""
    system_parts: list[str] = []
    contents: list[dict[str, Any]] = []
    call_names: dict[str, str] = {}

    for message in messages:
        if message.role == "system":
            if isinstance(message.content, str) and message.content:
                system_parts.append(message.content)
            continue
        if message.role == "user":
            text = message.content if isinstance(message.content, str) else ""
            _append_content_parts(contents, "user", [{"text": text}])
            continue
        if message.role == "assistant":
            thought_parts, call_sigs, text_sig = _extract_gemini_signatures(
                message.reasoning_details
            )
            parts: list[dict[str, Any]] = list(thought_parts)
            if isinstance(message.content, str) and message.content:
                text_part: dict[str, Any] = {"text": message.content}
                if text_sig:
                    text_part["thoughtSignature"] = text_sig
                parts.append(text_part)
            for call in message.tool_calls or []:
                call_names[call.id] = call.name
                fc: dict[str, Any] = {"name": call.name, "args": call.arguments}
                if not call.id.startswith("gemini_call_"):
                    fc["id"] = call.id
                part: dict[str, Any] = {"functionCall": fc}
                sig = call_sigs.get(call.id)
                if sig:
                    part["thoughtSignature"] = sig
                parts.append(part)
            _append_content_parts(contents, "model", parts)
            continue
        if message.role == "tool":
            if not message.tool_call_id:
                raise ValueError("tool message is missing tool_call_id")
            fn_name = call_names.get(message.tool_call_id, "")
            if not fn_name and isinstance(message.meta, dict):
                meta_name = message.meta.get("tool_name")
                if isinstance(meta_name, str) and meta_name:
                    fn_name = meta_name
            if not fn_name:
                fn_name = message.tool_call_id
            raw_content = message.content if isinstance(message.content, str) else ""
            is_error = isinstance(message.meta, dict) and message.meta.get("is_error") is True
            fr: dict[str, Any] = {
                "name": fn_name,
                "response": {"error": raw_content} if is_error else {"output": raw_content},
            }
            if not message.tool_call_id.startswith("gemini_call_"):
                fr["id"] = message.tool_call_id
            _append_content_parts(contents, "user", [{"functionResponse": fr}])

    system_instruction: dict[str, Any] | None = None
    if system_parts:
        system_instruction = {"parts": [{"text": "\n\n".join(system_parts)}]}
    return system_instruction, contents


def _parse_parts(
    parts: object,
    key: str,
    *,
    content_parts: list[str],
    calls: list[ToolCall],
    details: list[dict[str, Any]],
    on_thought: Callable[[str], None] | None = None,
    on_text: Callable[[str], None] | None = None,
) -> None:
    if not isinstance(parts, list):
        return
    for part in parts:
        if not isinstance(part, dict):
            continue
        sig = part.get("thoughtSignature")
        has_sig = isinstance(sig, str) and bool(sig)
        if part.get("thought") is True:
            text = part.get("text")
            if isinstance(text, str) and text and on_thought is not None:
                on_thought(text)
            if has_sig:
                entry: dict[str, Any] = {"type": "gemini.thought", "thoughtSignature": sig}
                if isinstance(text, str) and text:
                    entry["text"] = redact(text, key)
                details.append(entry)
            continue
        fc = part.get("functionCall")
        if isinstance(fc, dict):
            name = fc.get("name")
            if not isinstance(name, str) or not name:
                raise LLMError("provider tool_call missing name")
            raw_id = fc.get("id")
            call_id = (
                raw_id
                if isinstance(raw_id, str) and raw_id
                else f"gemini_call_{uuid.uuid4().hex[:12]}"
            )
            arguments, parse_error = _arguments(fc.get("args", {}))
            calls.append(
                ToolCall(id=call_id, name=name, arguments=arguments, parse_error=parse_error)
            )
            if has_sig:
                details.append(
                    {
                        "type": "gemini.call_signature",
                        "call_id": call_id,
                        "thoughtSignature": sig,
                    }
                )
            continue
        if "text" in part:
            text = part.get("text")
            if isinstance(text, str):
                content_parts.append(text)
                if text and on_text is not None:
                    on_text(text)
            elif text is not None:
                raise LLMError("provider content must be string or null")
            if has_sig:
                details.append({"type": "gemini.text_signature", "thoughtSignature": sig})


def parse_gemini_payload(raw: dict, key: str) -> ChatReply:
    """Assemble a ChatReply from a non-streaming Gemini generateContent response."""
    if not isinstance(raw, dict):
        raise LLMError("provider response must be an object")
    if isinstance(raw.get("error"), dict):
        raise LLMError(f"provider error: {redact(cap(json.dumps(raw['error'])), key)}")
    candidates = raw.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        feedback = raw.get("promptFeedback")
        if isinstance(feedback, dict) and feedback.get("blockReason"):
            raise LLMError(f"provider error: blocked ({feedback['blockReason']})")
        raise LLMError("provider response missing candidates")
    first = candidates[0]
    if not isinstance(first, dict):
        raise LLMError("provider candidate must be an object")
    content_obj = first.get("content")
    parts = content_obj.get("parts") if isinstance(content_obj, dict) else []
    content_parts: list[str] = []
    thought_parts: list[str] = []
    calls: list[ToolCall] = []
    details: list[dict[str, Any]] = []
    _parse_parts(
        parts,
        key,
        content_parts=content_parts,
        calls=calls,
        details=details,
        on_thought=thought_parts.append,
    )
    raw_usage = raw.get("usageMetadata")
    usage = normalize_usage(raw_usage, "google_gemini") if isinstance(raw_usage, dict) else None
    finish = first.get("finishReason") or ""
    if not isinstance(finish, str):
        finish = str(finish)
    model = raw.get("modelVersion")
    return ChatReply(
        content=redact("".join(content_parts), key) or None,
        tool_calls=calls,
        usage=usage,
        finish_reason=finish,
        model=model if isinstance(model, str) else None,
        reasoning=redact("".join(thought_parts), key) or None,
        reasoning_details=details or None,
    )


def parse_gemini_bytes(raw: bytes, key: str) -> ChatReply:
    return parse_gemini_payload(parse_json_bytes(raw, key), key)


async def read_gemini_sse(
    response: httpx.Response,
    key: str,
    on_reasoning: Callable[[str], None] | None,
    on_content: Callable[[str], None] | None = None,
) -> ChatReply:
    content_parts: list[str] = []
    calls: list[ToolCall] = []
    details: list[dict[str, Any]] = []
    reasoning = ReasoningOut(key, on_reasoning)
    content_out = ContentOut(on_content, key)
    usage: dict | None = None
    model: str | None = None
    finish = ""
    saw_candidate = False

    async for chunk in iter_sse_data(response, key):
        if isinstance(chunk.get("error"), dict):
            raise LLMError(f"provider error: {redact(cap(json.dumps(chunk['error'])), key)}")
        if isinstance(chunk.get("modelVersion"), str) and chunk["modelVersion"]:
            model = chunk["modelVersion"]
        raw_usage = chunk.get("usageMetadata")
        if isinstance(raw_usage, dict):
            usage = normalize_usage(raw_usage, "google_gemini")
        candidates = chunk.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            feedback = chunk.get("promptFeedback")
            if isinstance(feedback, dict) and feedback.get("blockReason"):
                raise LLMError(f"provider error: blocked ({feedback['blockReason']})")
            continue
        first = candidates[0]
        if not isinstance(first, dict):
            continue
        saw_candidate = True
        reason = first.get("finishReason")
        if isinstance(reason, str) and reason:
            finish = reason
        content_obj = first.get("content")
        parts = content_obj.get("parts") if isinstance(content_obj, dict) else None
        _parse_parts(
            parts,
            key,
            content_parts=content_parts,
            calls=calls,
            details=details,
            on_thought=reasoning.add,
            on_text=content_out.add,
        )

    if not saw_candidate:
        raise LLMError("provider response missing candidates")
    if not finish and not calls:
        raise LLMError("provider response incomplete")
    content_out.finish()
    return ChatReply(
        content=redact("".join(content_parts), key) or None,
        tool_calls=calls,
        usage=usage,
        finish_reason=finish,
        model=model,
        reasoning=reasoning.text(),
        reasoning_details=details or None,
    )


class GoogleGemini(BaseConnect):
    DROP_PARAM = "thinkingConfig"
    DROP_MARKER = "thinking"

    def _drop_reasoning(self, payload: dict[str, Any], body: str) -> dict[str, Any] | None:
        lowered = body.lower()
        if not any(marker in lowered for marker in _DROP_MARKERS):
            return None
        gen = payload.get("generationConfig")
        if not isinstance(gen, dict):
            return None
        thinking = gen.get("thinkingConfig")
        if not isinstance(thinking, dict):
            return None
        next_gen = dict(gen)
        if "thinkingLevel" in thinking:
            next_thinking = {k: v for k, v in thinking.items() if k != "thinkingLevel"}
            if next_thinking:
                next_gen["thinkingConfig"] = next_thinking
            else:
                next_gen.pop("thinkingConfig", None)
        else:
            next_gen.pop("thinkingConfig", None)
        next_payload = dict(payload)
        if next_gen:
            next_payload["generationConfig"] = next_gen
        else:
            next_payload.pop("generationConfig", None)
        return next_payload

    async def chat(
        self,
        messages: list[Message],
        tools: list | None = None,
        on_reasoning: Callable[[str], None] | None = None,
        on_content: Callable[[str], None] | None = None,
    ) -> ChatReply:
        system_instruction, contents = _to_gemini_contents(messages)
        payload: dict[str, Any] = {"contents": contents}
        if system_instruction is not None:
            payload["systemInstruction"] = system_instruction
        if tools:
            converted = _to_gemini_tools(tools)
            if converted:
                payload["tools"] = converted
        gen_cfg: dict[str, Any] = {}
        if self._provider.maxTokens:
            gen_cfg["maxOutputTokens"] = self._provider.maxTokens
        if self._provider.reasoningEffort:
            effort = self._provider.reasoningEffort.strip().lower()
            thinking_cfg: dict[str, Any] = {"includeThoughts": True}
            level = _THINKING_LEVELS.get(effort)
            if level:
                thinking_cfg["thinkingLevel"] = level
            gen_cfg["thinkingConfig"] = thinking_cfg
        if gen_cfg:
            payload["generationConfig"] = gen_cfg

        key = self._provider.api_key()
        headers = {
            "x-goog-api-key": key,
            "content-type": "application/json",
        }
        url = _gemini_stream_url(self._provider.baseUrl, self._provider.model)
        return await self._request(url, payload, headers, key, on_reasoning, on_content)

    async def _read_sse(
        self,
        response: httpx.Response,
        key: str,
        on_reasoning: Callable[[str], None] | None,
        on_content: Callable[[str], None] | None,
    ) -> ChatReply:
        return await read_gemini_sse(response, key, on_reasoning, on_content)

    def _parse_bytes(self, raw: bytes, key: str) -> ChatReply:
        return parse_gemini_bytes(raw, key)
