from __future__ import annotations

import time
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
        start = time.perf_counter()
        reply = await self._llm.chat(stage.messages, stage.tools)
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        stage.reply = reply
        stage.llm_latency_ms = elapsed_ms
        model = getattr(reply, "model", None)
        if isinstance(model, str) and model.strip():
            stage.llm_model = model.strip()
        return reply
