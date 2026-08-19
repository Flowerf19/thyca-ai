from __future__ import annotations

from thyca.protocol import Message

from .stage import Stage


class Assemble:
    def assemble(self, stage: Stage, user_msg: str) -> None:
        if not isinstance(user_msg, str):
            raise ValueError("user_msg must be a string")
        stage.messages = list(stage.messages)
        stage.messages.append(Message(role="user", content=user_msg))
