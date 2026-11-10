from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from thyca.core.protocol import Message


# Whether one conversation still waits for its first user message.
def is_blank(conversation: "ConversationRuntime") -> bool:
    return not any(
        item.role == "user" and item.content and item.content.strip()
        for item in conversation.messages
    )


@dataclass
class ConversationRuntime:
    conversation_id: str
    path: Path
    messages: list[Message]
    # Team room participants (BE-3): bot ids in this room. None means the
    # legacy default ("thyca" alone); populated by ConversationStore.load.
    bots: list[str] | None = None
    # Bot main window (CBE-5): the bot id whose fixed DM this conversation is,
    # or None for ordinary rooms. Populated by ConversationStore.load.
    dm_bot: str | None = None
