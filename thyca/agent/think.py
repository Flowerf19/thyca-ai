from __future__ import annotations

import time
from typing import Protocol

from thyca.llm.llm_base import ChatReply
from thyca.core.protocol import Message

from .stage import Stage


class LLMPort(Protocol):
    async def chat(
        self,
        messages: list[Message],
        tools: list | None = None,
        on_reasoning: object = None,
        on_content: object = None,
    ) -> ChatReply: ...


class Think:
    def __init__(self, llm: LLMPort) -> None:
        self._llm = llm

    async def think(
        self, stage: Stage, on_reasoning=None, on_content=None
    ) -> ChatReply:
        start = time.perf_counter()
        if on_reasoning is None and on_content is None:
            reply = await self._llm.chat(stage.messages, stage.tools)
        else:
            reply = await self._chat_with_callbacks(on_reasoning, on_content, stage)
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        stage.reply = reply
        stage.llm_latency_ms = elapsed_ms
        model = getattr(reply, "model", None)
        if isinstance(model, str) and model.strip():
            stage.llm_model = model.strip()
        return reply

    async def _chat_with_callbacks(self, on_reasoning, on_content, stage: Stage):
        # Callback support is opt-in per port: older/custom ports accept
        # neither or only one of the kwargs. Degrade per missing kwarg.
        try:
            return await self._llm.chat(
                stage.messages, stage.tools, on_reasoning=on_reasoning, on_content=on_content
            )
        except TypeError as exc:
            message = str(exc)
            if "on_content" in message:
                try:
                    return await self._llm.chat(
                        stage.messages, stage.tools, on_reasoning=on_reasoning
                    )
                except TypeError as inner:
                    if "on_reasoning" not in str(inner):
                        raise
            elif "on_reasoning" not in message:
                raise
        return await self._llm.chat(stage.messages, stage.tools)
