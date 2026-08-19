from __future__ import annotations

import pytest

from thyca.llm.anthropic_chat import AnthropicChat
from thyca.llm.google_chat import GoogleChat
from thyca.llm.llm_factory import ConnectFactory
from thyca.llm.openai_chat import OpenAIChat
from thyca.llm.openai_responses import OpenAIResponses
from thyca.protocol import Message


def test_factory_openai_kinds() -> None:
    assert isinstance(ConnectFactory.create("openai_chat"), OpenAIChat)
    assert isinstance(ConnectFactory.create("openai"), OpenAIChat)
    assert isinstance(ConnectFactory.create("openai_compat"), OpenAIChat)
    assert isinstance(ConnectFactory.create("openai_responses"), OpenAIResponses)


def test_factory_stubs() -> None:
    assert isinstance(ConnectFactory.create("google"), GoogleChat)
    assert isinstance(ConnectFactory.create("anthropic"), AnthropicChat)


def test_factory_unknown_kind() -> None:
    with pytest.raises(ValueError, match="unknown connect kind"):
        ConnectFactory.create("cohere")


@pytest.mark.asyncio
async def test_stubs_not_implemented() -> None:
    messages = [Message(role="user", content="hi")]
    with pytest.raises(NotImplementedError):
        await GoogleChat().chat(messages)
    with pytest.raises(NotImplementedError):
        await AnthropicChat().chat(messages)
    with pytest.raises(NotImplementedError):
        await OpenAIResponses().chat(messages)
