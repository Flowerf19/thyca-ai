from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx

from thyca.core.protocol import Message

from ._http import BaseConnect
from .llm_base import ChatReply
from .openai_parse import parse_chat_bytes, read_sse_reply


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
