from __future__ import annotations

import re
import time
from typing import Protocol

from thyca.llm.llm_base import ChatReply
from thyca.core.protocol import Message

from .stage import Stage


_UNEXPECTED_KWARG = re.compile(r"got an unexpected keyword argument '([^']+)'")


def _rejected_kwarg(message: str) -> str | None:
    """The kwarg CPython rejected, or None for non-standard TypeErrors."""
    match = _UNEXPECTED_KWARG.search(message)
    return match.group(1) if match else None


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
            # Match the quoted kwarg, not substrings: the message embeds the
            # qualname, which may itself contain "on_content"/"on_reasoning".
            rejected = _rejected_kwarg(message)
            if rejected is None:
                # Non-standard TypeError: keep the old substring priority.
                if "on_content" in message:
                    rejected = "on_content"
                elif "on_reasoning" in message:
                    rejected = "on_reasoning"
                else:
                    raise
            if rejected == "on_content":
                try:
                    return await self._llm.chat(
                        stage.messages, stage.tools, on_reasoning=on_reasoning
                    )
                except TypeError as inner:
                    # Swallow only a proven second rejection; anything else
                    # is a real bug and must surface.
                    if _rejected_kwarg(str(inner)) != "on_reasoning":
                        raise
            elif rejected == "on_reasoning":
                # Content-only port: keep on_content working instead of
                # falling through to a bare chat().
                try:
                    return await self._llm.chat(
                        stage.messages, stage.tools, on_content=on_content
                    )
                except TypeError as inner:
                    # Swallow only a proven second rejection; anything else
                    # is a real bug and must surface.
                    if _rejected_kwarg(str(inner)) != "on_content":
                        raise
            else:
                raise
        return await self._llm.chat(stage.messages, stage.tools)
