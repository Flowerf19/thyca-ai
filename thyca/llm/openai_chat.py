from __future__ import annotations

import json
from typing import Any, Callable

import httpx

from thyca.config import ProviderCfg
from thyca.protocol import Message, ToolCall

from .llm_base import ChatReply, Connect, LLMError, normalize_usage

_RETRY_STATUS = {429, 502, 503, 504}
_BODY_CAP = 500
_RETRY_AFTER_CAP_S = 5.0


def _chat_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/chat/completions"


def _redact(text: str, secret: str) -> str:
    if secret and secret in text:
        return text.replace(secret, "[redacted]")
    return text


def _cap(text: str) -> str:
    if len(text) <= _BODY_CAP:
        return text
    return text[:_BODY_CAP] + "…"


class OpenAIChat(Connect):
    def __init__(
        self,
        provider: ProviderCfg,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._provider = provider
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0)
        )
        self._retry_hook: Callable[[int, int], None] | None = None

    def set_retry_hook(self, hook: Callable[[int, int], None] | None) -> None:
        """Optional (attempt, max_attempts) callback for transient retries."""
        self._retry_hook = hook

    def _notify_retry(self, attempt: int, max_attempts: int) -> None:
        if self._retry_hook is None:
            return
        try:
            self._retry_hook(attempt, max_attempts)
        except Exception:
            pass

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def chat(
        self, messages: list[Message], tools: list | None = None
    ) -> ChatReply:
        payload: dict[str, Any] = {
            "model": self._provider.model,
            "messages": [_to_openai_message(m) for m in messages],
            "tool_choice": "auto",
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

        response = await self._request(url, payload, headers, key)
        return _parse_reply(response, key)

    async def _request(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        key: str,
    ) -> httpx.Response:
        # Exactly 3 transient attempts; each failure emits retry status
        # (1/3, 2/3, 3/3) before the final provider error.
        max_attempts = 3
        last_error: LLMError | None = None
        transient = 0
        dropped_effort = False
        while True:
            try:
                response = await self._client.post(url, json=payload, headers=headers)
            except httpx.TimeoutException as exc:
                transient += 1
                last_error = LLMError("provider timeout")
                self._notify_retry(transient, max_attempts)
                if transient >= max_attempts:
                    raise last_error from exc
                continue
            except httpx.RequestError as exc:
                raise LLMError(_redact(_cap(str(exc)), key)) from exc

            if (
                response.status_code == 400
                and "reasoning_effort" in payload
                and "reasoning_effort" in response.text
                and not dropped_effort
            ):
                # Model does not support the effort parameter — drop it and
                # retry once (does not count as a transient attempt).
                payload = {k: v for k, v in payload.items() if k != "reasoning_effort"}
                dropped_effort = True
                continue
            if response.status_code in _RETRY_STATUS:
                transient += 1
                last_error = _http_error(response, key)
                self._notify_retry(transient, max_attempts)
                if transient >= max_attempts:
                    raise last_error
                await _sleep_retry_after(response)
                continue
            if response.status_code >= 400:
                raise _http_error(response, key)
            return response


async def _sleep_retry_after(response: httpx.Response) -> None:
    raw = response.headers.get("Retry-After")
    delay = 0.0
    if raw:
        try:
            delay = min(max(float(raw), 0.0), _RETRY_AFTER_CAP_S)
        except ValueError:
            delay = 0.0
    if delay > 0:
        import asyncio

        await asyncio.sleep(delay)


def _http_error(response: httpx.Response, key: str) -> LLMError:
    body = _redact(_cap(response.text), key)
    return LLMError(f"provider HTTP {response.status_code}: {body}")


def _to_openai_message(message: Message) -> dict[str, Any]:
    payload: dict[str, Any] = {"role": message.role, "content": message.content}
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


def _parse_reply(response: httpx.Response, key: str) -> ChatReply:
    try:
        raw = response.json()
    except json.JSONDecodeError as exc:
        raise LLMError(_redact(_cap(response.text), key)) from exc
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
    if not isinstance(raw_usage, dict):
        raw_usage = None
    usage = normalize_usage(raw_usage, "openai") if isinstance(raw_usage, dict) else None
    model = raw.get("model")
    if not isinstance(model, str):
        model = None
    return ChatReply(
        content=content,
        tool_calls=_parse_tool_calls(message.get("tool_calls")),
        usage=usage,
        finish_reason=finish,
        model=model,
    )


def _parse_tool_calls(raw: object) -> list[ToolCall]:
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
        arguments_raw = fn.get("arguments", "{}")
        parse_error: str | None = None
        arguments: dict = {}
        if isinstance(arguments_raw, dict):
            arguments = arguments_raw
        elif isinstance(arguments_raw, str):
            try:
                parsed = json.loads(arguments_raw) if arguments_raw else {}
            except json.JSONDecodeError:
                parse_error = "invalid arguments"
                parsed = {}
            if parse_error is None and not isinstance(parsed, dict):
                parse_error = "invalid arguments"
                parsed = {}
            arguments = parsed
        else:
            parse_error = "invalid arguments"
        calls.append(
            ToolCall(id=call_id, name=name, arguments=arguments, parse_error=parse_error)
        )
    return calls
