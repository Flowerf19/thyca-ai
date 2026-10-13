"""Provider-backed compaction summarizer (naming-style sidecar call).

One standalone chat call on the session's own model, no tools. Any
failure returns None so the caller falls back to the excerpt; only
cancellation propagates.
"""
from __future__ import annotations

import asyncio
from time import perf_counter

from thyca.agent.compacting import SummaryOutcome
from thyca.agent.think import LLMPort
from thyca.core.protocol import Message
from thyca.sessions.summary import propose_summary


class LlmSummarizer:
    def __init__(self, connect: LLMPort) -> None:
        self._connect = connect

    async def summarize(
        self,
        fresh: list[Message],
        prior_text: str,
        instructions: str | None,
    ) -> SummaryOutcome | None:
        captured: dict = {}

        async def spy(messages, tools=None):
            reply = await self._connect.chat(messages, tools)
            captured["reply"] = reply
            return reply

        started = perf_counter()
        try:
            text = await propose_summary(spy, fresh, prior_text, instructions)
        except asyncio.CancelledError:
            raise
        except Exception:
            return None
        if text is None:
            return None
        return SummaryOutcome(
            text=text,
            reply=captured.get("reply"),
            latency_ms=int((perf_counter() - started) * 1000),
        )
