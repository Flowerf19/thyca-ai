from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from thyca.core.protocol import Message


def is_blank(conversation: ConversationRuntime) -> bool:
    return not any(
        item.role == "user" and item.content and item.content.strip()
        for item in conversation.messages
    )


@dataclass
class ConversationRuntime:
    conversation_id: str
    path: Path
    messages: list[Message]
    bots: list[str] | None = None
    dm_bot: str | None = None
    title: str | None = None
