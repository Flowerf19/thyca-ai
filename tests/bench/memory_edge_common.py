"""Shared types, constants, and offline stubs for memory edge simulation suites."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from thyca.agents.think import ChatReply
from thyca.core.protocol import Message, ToolCall

TZ_NAME = "Asia/Ho_Chi_Minh"
TZ = ZoneInfo(TZ_NAME)
BASE_NOW = datetime(2026, 11, 25, 10, 0, tzinfo=TZ)


@dataclass
class EdgeCaseResult:
    case_id: str
    group: str
    passed: bool
    summary: str
    severity: str = "P1"
    findings: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


class StubExtractorConnect:
    """Offline fake LLM for idle sweep extraction that records prompt & tool schema calls."""

    def __init__(
        self,
        tool_calls: list[ToolCall] | tuple[ToolCall, ...] = (),
        *,
        raise_exc: Exception | None = None,
    ) -> None:
        self._tool_calls = list(tool_calls)
        self._raise_exc = raise_exc
        self.calls: int = 0
        self.prompts: list[str] = []
        self.tools_seen: list[list | None] = []

    async def chat(self, messages: list[Message], tools: list | None = None) -> ChatReply:
        self.calls += 1
        if messages:
            self.prompts.append(str(messages[-1].content or ""))
        self.tools_seen.append(tools)
        if self._raise_exc is not None:
            raise self._raise_exc
        return ChatReply(content="", tool_calls=list(self._tool_calls))

    async def aclose(self) -> None:
        return None
