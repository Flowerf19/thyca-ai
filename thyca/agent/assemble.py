from __future__ import annotations

from thyca.llm.prompt_manager import PromptManager
from thyca.memory.active import ActiveSnapshot
from thyca.core.protocol import Message
from thyca.sessions.wire import is_naming_message

from .stage import Stage


def _is_compaction_marker(message: Message) -> bool:
    return message.role == "system" and (message.content or "").startswith(
        "[compaction: "
    )


class Assemble:
    def __init__(self, prompts: PromptManager | None = None) -> None:
        self._prompts = prompts or PromptManager()

    def assemble(self, stage: Stage, user_msg: str, *, append_user: bool = True) -> None:
        if append_user and not isinstance(user_msg, str):
            raise ValueError("user_msg must be a string")
        # Naming rows are transcript-only (assistant with null content and no
        # tool calls): strict providers 400 on them, so they never reach the
        # model. Canonical predicate lives in sessions/wire (shared with trace).
        # Compaction markers are the only history system rows the model may
        # see; stale hot prompts stay dropped. Markers are hoisted right
        # after the fresh hot prompt below.
        markers = [
            message
            for message in stage.messages
            if _is_compaction_marker(message) and not is_naming_message(message)
        ]
        messages = [
            message
            for message in stage.messages
            if message.role != "system" and not is_naming_message(message)
        ]
        insert_at = 0
        if isinstance(stage.hot, ActiveSnapshot):
            messages.insert(0, Message(role="system", content=self._prompts.build(stage.hot)))
            insert_at = 1
        messages[insert_at:insert_at] = markers
        if append_user:
            messages.append(Message(role="user", content=user_msg))
        stage.messages = messages
