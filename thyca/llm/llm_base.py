from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from thyca.protocol import Message, ToolCall


class LLMError(RuntimeError):
    """Provider HTTP or payload error. Must not contain API keys."""


@dataclass(frozen=True)
class ChatReply:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict | None = None
    finish_reason: str = ""


class Connect(ABC):
    """Product: one chat turn against a provider."""

    @abstractmethod
    async def chat(
        self, messages: list[Message], tools: list | None = None
    ) -> ChatReply:
        raise NotImplementedError

    async def aclose(self) -> None:
        return None
