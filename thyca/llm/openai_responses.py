"""OpenAI `/v1/responses` connect: streaming text + thinking summaries + tools.

Same ``Connect`` contract as :class:`OpenAIChat`; only the wire protocol
differs (input items, response events, usage shape). Retry/timeout/error
semantics intentionally mirror ``openai_chat``.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

from thyca.core.protocol import Message

from ._http import BaseConnect
from .llm_base import ChatReply
from .responses_parse import (
    _to_responses_input,
    _to_responses_tools,
    parse_responses_bytes,
    read_responses_sse,
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
