from __future__ import annotations

from thyca.protocol import Message

from .llm_base import ChatReply, Connect


class GoogleChat(Connect):
    async def chat(
        self, messages: list[Message], tools: list | None = None
    ) -> ChatReply:
        raise NotImplementedError("Google connect is not implemented")
