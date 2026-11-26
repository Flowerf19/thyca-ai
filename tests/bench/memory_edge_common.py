"""Shared types, constants, and offline stubs for memory edge simulation suites."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from thyca.agents.think import ChatReply
from thyca.core.protocol import Message

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
    """Offline fake LLM for idle sweep extraction that records prompt calls."""

    def __init__(
        self,
        reply_json: list[dict[str, Any]] | str,
        *,
        raise_exc: Exception | None = None,
    ) -> None:
        self._payload = (
            reply_json
            if isinstance(reply_json, str)
            else json.dumps(reply_json, ensure_ascii=False)
        )
        self._raise_exc = raise_exc
        self.calls: int = 0
        self.prompts: list[str] = []

    async def chat(self, messages: list[Message], _tools: list | None = None) -> ChatReply:
        self.calls += 1
        if messages:
            self.prompts.append(str(messages[-1].content or ""))
        if self._raise_exc is not None:
            raise self._raise_exc
        return ChatReply(content=self._payload)

    async def aclose(self) -> None:
        return None
