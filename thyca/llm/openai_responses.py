from __future__ import annotations

from thyca.protocol import Message

from .llm_base import ChatReply, Connect


class OpenAIResponses(Connect):
    """OpenAI `/v1/responses` — not implemented in v1."""

    async def chat(
        self, messages: list[Message], tools: list | None = None
    ) -> ChatReply:
        raise NotImplementedError("OpenAI Responses connect is not implemented")
