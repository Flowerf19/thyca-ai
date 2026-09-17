from __future__ import annotations

import time
from typing import Protocol

from thyca.llm.llm_base import ChatReply
from thyca.protocol import Message

from .stage import Stage


class LLMPort(Protocol):
    async def chat(
        self,
        messages: list[Message],
        tools: list | None = None,
        on_reasoning: object = None,
    ) -> ChatReply: ...


class Think:
    def __init__(self, llm: LLMPort) -> None:
        self._llm = llm

    async def think(self, stage: Stage, on_reasoning=None) -> ChatReply:
        start = time.perf_counter()
        if on_reasoning is None:
            reply = await self._llm.chat(stage.messages, stage.tools)
        else:
            try:
                reply = await self._llm.chat(
                    stage.messages, stage.tools, on_reasoning=on_reasoning
                )
            except TypeError as exc:
                if "on_reasoning" not in str(exc):
                    raise
                reply = await self._llm.chat(stage.messages, stage.tools)
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        stage.reply = reply
        stage.llm_latency_ms = elapsed_ms
        model = getattr(reply, "model", None)
        if isinstance(model, str) and model.strip():
            stage.llm_model = model.strip()
        return reply
