from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from thyca.protocol import Message, ToolResult

if TYPE_CHECKING:
    from .think import ChatReply


@dataclass
class Stage:
    """Shared workspace for one user turn. Phases only read/write this."""

    messages: list[Message] = field(default_factory=list)
    round: int = 0
    hot: object = None
    tools: list | None = None
    reply: ChatReply | None = None
    results: list[ToolResult] = field(default_factory=list)

    def __post_init__(self) -> None:
        if isinstance(self.round, bool) or not isinstance(self.round, int) or self.round < 0:
            raise ValueError("Stage.round must be a non-negative integer")
