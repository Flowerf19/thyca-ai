from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx

from thyca.config import ProviderCfg
from thyca.core.protocol import Message

from .llm_base import ChatReply, Connect, LLMError
from ._http import _RETRY_STATUS, _cap, _redact, _sleep_retry_after
from .openai_parse import parse_chat_bytes, read_sse_reply


def _chat_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/chat/completions"


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
            timeout=httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)
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

    async def _request(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        key: str,
        on_reasoning: Callable[[str], None] | None,
        on_content: Callable[[str], None] | None = None,
    ) -> ChatReply:
        # Exactly 3 transient attempts; each failure emits retry status
        # (1/3, 2/3, 3/3) before the final provider error.
        max_attempts = 3
        last_error: LLMError | None = None
        transient = 0
        dropped_effort = False
        while True:
            try:
                async with self._client.stream(
                    "POST", url, json=payload, headers=headers
                ) as response:
                    reply = await self._consume(
                        response, payload, key, on_reasoning, on_content, dropped_effort
                    )
            except _DropEffort:
                payload = {k: v for k, v in payload.items() if k != "reasoning_effort"}
                dropped_effort = True
                continue
            except httpx.RequestError as exc:
                transient += 1
                if isinstance(exc, httpx.TimeoutException):
                    last_error = LLMError("provider timeout")
                else:
                    last_error = LLMError(_redact(_cap(str(exc)), key))
                self._notify_retry(transient, max_attempts)
                if transient >= max_attempts:
                    raise last_error from exc
                continue

            if isinstance(reply, LLMError):
                transient += 1
                last_error = reply
                self._notify_retry(transient, max_attempts)
                if transient >= max_attempts:
                    raise last_error
                continue
            return reply


    async def _consume(
        self,
        response: httpx.Response,
        payload: dict[str, Any],
        key: str,
        on_reasoning: Callable[[str], None] | None,
        on_content: Callable[[str], None] | None,
        dropped_effort: bool,
    ) -> ChatReply | LLMError:
        status = response.status_code
        if (
            status == 400
            and "reasoning_effort" in payload
            and not dropped_effort
        ):
            body = (await response.aread()).decode("utf-8", errors="replace")
            if "reasoning_effort" in body:
                raise _DropEffort
            raise LLMError(f"provider HTTP 400: {_redact(_cap(body), key)}")
        if status in _RETRY_STATUS:
            await _sleep_retry_after(response)
            body = (await response.aread()).decode("utf-8", errors="replace")
            return LLMError(f"provider HTTP {status}: {_redact(_cap(body), key)}")
        if status >= 400:
            body = (await response.aread()).decode("utf-8", errors="replace")
            raise LLMError(f"provider HTTP {status}: {_redact(_cap(body), key)}")
        ctype = response.headers.get("content-type", "")
        if "event-stream" in ctype:
            return await read_sse_reply(response, key, on_reasoning, on_content)
        raw = await response.aread()
        reply = parse_chat_bytes(raw, key)
        if on_reasoning and reply.reasoning:
            try:
                on_reasoning(reply.reasoning)
            except Exception:
                pass
        if on_content and reply.content:
            try:
                on_content(reply.content)
            except Exception:
                pass
        return reply


class _DropEffort(Exception):
    """Retry the request once without reasoning_effort."""


def _to_openai_message(message: Message) -> dict[str, Any]:
    payload: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.reasoning_details:
        # Round-trip provider thinking signatures; absent for providers that
        # never emit them, so payloads there are byte-identical to before.
        payload["reasoning_details"] = [dict(detail) for detail in message.reasoning_details]
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
