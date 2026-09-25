from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from thyca.agent.stage import Stage
from thyca.agent.think import ChatReply, Think
from thyca.core.protocol import Message, ToolCall


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

    stage.llm_model = "gpt-4o-mini"
    result = asyncio.run(Think(llm).think(stage))

    assert result is reply
    assert stage.reply is reply
    assert llm.requests == [stage.messages]
    assert stage.llm_model == "gpt-4o-mini"
    assert isinstance(stage.llm_latency_ms, int)
    assert stage.llm_latency_ms >= 0


def test_narrow_port_still_gets_text_deltas() -> None:
    # NB: test/class names avoid the "on_content"/"on_reasoning" substrings:
    # Think degrades by matching those in the TypeError text (which embeds
    # the qualname), so a collision would mask the real kwarg.
    seen: list[str] = []

    class NarrowPort:
        async def chat(self, messages, tools=None, on_content=None):
            if on_content is not None:
                on_content("live")
            return ChatReply(content="done")

    stage = Stage(messages=[Message(role="user", content="hi")], tools=None)
    result = asyncio.run(
        Think(NarrowPort()).think(
            stage, on_reasoning=lambda chunk: None, on_content=seen.append
        )
    )
    assert result.content == "done"
    assert seen == ["live"]


def test_wide_port_still_gets_thinking_deltas() -> None:
    seen: list[str] = []

    class WidePort:
        async def chat(self, messages, tools=None, on_reasoning=None):
            if on_reasoning is not None:
                on_reasoning("thinking")
            return ChatReply(content="done")

    stage = Stage(messages=[Message(role="user", content="hi")], tools=None)
    result = asyncio.run(
        Think(WidePort()).think(
            stage, on_reasoning=seen.append, on_content=lambda chunk: None
        )
    )
    assert result.content == "done"
    assert seen == ["thinking"]


def test_reasoning_only_port_ignores_qualname_noise() -> None:
    # Qualname embeds "on_reasoning" noise (class name); CPython quotes the
    # truly rejected 'on_content'. Must retry reasoning-only.
    seen: list[str] = []

    class Port_on_reasoning:
        async def chat(self, messages, tools=None, on_reasoning=None):
            if on_reasoning is not None:
                on_reasoning("thinking")
            return ChatReply(content="done")

    stage = Stage(messages=[Message(role="user", content="hi")], tools=None)
    result = asyncio.run(
        Think(Port_on_reasoning()).think(
            stage, on_reasoning=seen.append, on_content=lambda chunk: None
        )
    )
    assert result.content == "done"
    assert seen == ["thinking"]


def test_content_only_port_ignores_qualname_noise() -> None:
    # True F12 collision: qualname embeds "on_content" noise (class name)
    # while CPython quotes the truly rejected 'on_reasoning'. Old
    # substring code retried reasoning-only here and lost the deltas.
    seen: list[str] = []

    class Port_on_content:
        async def chat(self, messages, tools=None, on_content=None):
            if on_content is not None:
                on_content("live")
            return ChatReply(content="done")

    stage = Stage(messages=[Message(role="user", content="hi")], tools=None)
    result = asyncio.run(
        Think(Port_on_content()).think(
            stage, on_reasoning=lambda chunk: None, on_content=seen.append
        )
    )
    assert result.content == "done"
    assert seen == ["live"]
