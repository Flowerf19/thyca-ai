from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from thyca.agent.stage import Stage
from thyca.agent.think import ChatReply, Think
from thyca.protocol import Message, ToolCall


@dataclass
class FakeLLM:
    reply: ChatReply
    requests: list[list[Message]] = field(default_factory=list)

    async def chat(self, messages: list[Message], tools: list | None = None) -> ChatReply:
        self.requests.append(list(messages))
        return self.reply


def test_think_writes_reply_on_stage() -> None:
    call = ToolCall(id="call-1", name="echo")
    reply = ChatReply(content=None, tool_calls=[call], usage={"tokens": 1}, finish_reason="tool_calls")
    llm = FakeLLM(reply)
    stage = Stage(
        messages=[Message(role="user", content="hello")],
        tools=[{"name": "echo"}],
    )

    result = asyncio.run(Think(llm).think(stage))

    assert result is reply
    assert stage.reply is reply
    assert llm.requests == [stage.messages]
