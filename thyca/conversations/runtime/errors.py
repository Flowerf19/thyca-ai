from __future__ import annotations

from pathlib import Path


class ConversationError(RuntimeError):
    """Base error for conversation operations."""


class ConversationNotFound(ConversationError):
    def __init__(self, path: str | Path, msg: str | None = None) -> None:
        self.path = Path(path)
        super().__init__(msg or f"conversation not found: {self.path}")


class ConversationBusy(ConversationError):
    """A turn is already running for this conversation.

    One turn per conversation at a time: two turns on the same transcript would
    interleave appends and corrupt the compaction baseline. Other conversations
    stay free to run their own turns.
    """

    def __init__(self, conversation_id: str) -> None:
        self.conversation_id = conversation_id
        super().__init__(f"conversation busy: {conversation_id}")


class ConversationCorrupt(ConversationError):
    def __init__(self, path: str | Path, line: int | None, cause: str | Exception) -> None:
        self.path = Path(path)
        self.line = line
        self.cause = cause
        suffix = f":{line}" if line is not None else ""
        super().__init__(f"{self.path}{suffix}: {cause}")


# No live bot can answer this room (all participants deleted or paused).
class NoLiveResponder(ConversationError):
    def __init__(self, conversation_id: str) -> None:
        self.conversation_id = conversation_id
        super().__init__(f"no live participant in conversation: {conversation_id}")
