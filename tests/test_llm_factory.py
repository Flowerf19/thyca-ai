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


# Moved from test_b4_unification.py / test_b4_p2.py (B4 batch).
def test_x15_connects_share_base() -> None:
    from thyca.llm._http import BaseConnect
    from thyca.llm.openai_chat import OpenAIChat
    from thyca.llm.openai_responses import OpenAIResponses

    assert issubclass(OpenAIChat, BaseConnect)
    assert issubclass(OpenAIResponses, BaseConnect)
    assert OpenAIChat.DROP_PARAM == "reasoning_effort"
    assert OpenAIResponses.DROP_PARAM == "reasoning"
    assert not hasattr(OpenAIChat, "_request") or "_request" not in OpenAIChat.__dict__
