from __future__ import annotations

import pytest

from thyca.configs import ProviderCfg
from thyca.llms._http import BaseConnect
from thyca.llms.anthropic_messages import AnthropicMessages
from thyca.llms.google_gemini import GoogleGemini
from thyca.llms.llm_factory import ConnectFactory
from thyca.llms.openai_chat import OpenAIChat
from thyca.llms.openai_responses import OpenAIResponses


def test_factory_openai_kinds() -> None:
    assert isinstance(ConnectFactory.create("openai_chat"), OpenAIChat)
    assert isinstance(ConnectFactory.create("openai"), OpenAIChat)
    assert isinstance(ConnectFactory.create("openai_compat"), OpenAIChat)
    assert isinstance(ConnectFactory.create("openai_responses"), OpenAIResponses)
    assert isinstance(ConnectFactory.create("responses"), OpenAIResponses)
    assert isinstance(ConnectFactory.create("anthropic_messages"), AnthropicMessages)
    assert isinstance(ConnectFactory.create("anthropic"), AnthropicMessages)
    assert isinstance(ConnectFactory.create("google_gemini"), GoogleGemini)
    assert isinstance(ConnectFactory.create("google"), GoogleGemini)
    assert isinstance(ConnectFactory.create("gemini"), GoogleGemini)


def test_factory_unknown_kind() -> None:
    with pytest.raises(ValueError, match="unknown connect kind"):
        ConnectFactory.create("cohere")
    with pytest.raises(ValueError, match="unknown connect kind"):
        ConnectFactory.create("bedrock")


def test_factory_responses_injects_provider() -> None:
    provider = ProviderCfg(baseUrl="https://x.example/v1", model="m", apiKey="k")
    connect = ConnectFactory.create("openai_responses", provider)
    assert isinstance(connect, OpenAIResponses)
    assert connect._provider is provider
    anth = ConnectFactory.create("anthropic_messages", provider)
    assert isinstance(anth, AnthropicMessages)
    assert anth._provider is provider
    goog = ConnectFactory.create("google_gemini", provider)
    assert isinstance(goog, GoogleGemini)
    assert goog._provider is provider


def test_x15_connects_share_base() -> None:

    assert issubclass(OpenAIChat, BaseConnect)
    assert issubclass(OpenAIResponses, BaseConnect)
    assert issubclass(AnthropicMessages, BaseConnect)
    assert issubclass(GoogleGemini, BaseConnect)
    assert OpenAIChat.DROP_PARAM == "reasoning_effort"
    assert OpenAIResponses.DROP_PARAM == "reasoning"
    assert AnthropicMessages.DROP_PARAM == "thinking"
    assert GoogleGemini.DROP_PARAM == "thinkingConfig"
    for cls in (OpenAIChat, OpenAIResponses, AnthropicMessages, GoogleGemini):
        assert "_request" not in cls.__dict__
