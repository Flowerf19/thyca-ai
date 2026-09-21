from __future__ import annotations

import pytest

from thyca.config import ProviderCfg
from thyca.llm.llm_factory import ConnectFactory
from thyca.llm.openai_chat import OpenAIChat
from thyca.llm.openai_responses import OpenAIResponses


def test_factory_openai_kinds() -> None:
    assert isinstance(ConnectFactory.create("openai_chat"), OpenAIChat)
    assert isinstance(ConnectFactory.create("openai"), OpenAIChat)
    assert isinstance(ConnectFactory.create("openai_compat"), OpenAIChat)
    assert isinstance(ConnectFactory.create("openai_responses"), OpenAIResponses)


def test_factory_unknown_kind() -> None:
    with pytest.raises(ValueError, match="unknown connect kind"):
        ConnectFactory.create("cohere")
    with pytest.raises(ValueError, match="unknown connect kind"):
        ConnectFactory.create("google")
    with pytest.raises(ValueError, match="unknown connect kind"):
        ConnectFactory.create("anthropic")


def test_factory_responses_injects_provider() -> None:
    provider = ProviderCfg(baseUrl="https://x.example/v1", model="m", apiKey="k")
    connect = ConnectFactory.create("openai_responses", provider)
    assert isinstance(connect, OpenAIResponses)
    assert connect._provider is provider
