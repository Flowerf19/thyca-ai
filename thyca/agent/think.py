from __future__ import annotations

from typing import Protocol

from thyca.llm.llm_base import ChatReply
from thyca.protocol import Message

from .stage import Stage


class LLMPort(Protocol):
    async def chat(self, messages: list[Message], tools: list | None = None) -> ChatReply: ...


class Think:
    def __init__(self, llm: LLMPort) -> None:
        self._llm = llm

    async def think(self, stage: Stage) -> ChatReply:
        reply = await self._llm.chat(stage.messages, stage.tools)
        stage.reply = reply
        return reply
