from __future__ import annotations

from thyca.llm.prompt_manager import PromptManager
from thyca.memory.active import ActiveSnapshot
from thyca.protocol import Message

from .stage import Stage


class Assemble:
    def __init__(self, prompts: PromptManager | None = None) -> None:
        self._prompts = prompts or PromptManager()

    def assemble(self, stage: Stage, user_msg: str) -> None:
        if not isinstance(user_msg, str):
            raise ValueError("user_msg must be a string")
        messages = [message for message in stage.messages if message.role != "system"]
        if isinstance(stage.hot, ActiveSnapshot):
            messages.insert(0, Message(role="system", content=self._prompts.build(stage.hot)))
        messages.append(Message(role="user", content=user_msg))
        stage.messages = messages
