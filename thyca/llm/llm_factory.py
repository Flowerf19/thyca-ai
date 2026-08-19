from __future__ import annotations

from thyca.config import ProviderCfg

from .anthropic_chat import AnthropicChat
from .google_chat import GoogleChat
from .llm_base import Connect
from .openai_chat import OpenAIChat
from .openai_responses import OpenAIResponses

_KINDS = {
    "openai": OpenAIChat,
    "openai_chat": OpenAIChat,
    "openai_compat": OpenAIChat,
    "openai_responses": OpenAIResponses,
    "responses": OpenAIResponses,
    "google": GoogleChat,
    "anthropic": AnthropicChat,
}


class ConnectFactory:
    @staticmethod
    def create(
        kind: str = "openai_chat",
        provider: ProviderCfg | None = None,
    ) -> Connect:
        key = kind.strip().lower()
        if key not in _KINDS:
            raise ValueError(f"unknown connect kind: {kind!r}")
        cls = _KINDS[key]
        if cls is OpenAIChat:
            return OpenAIChat(provider or ProviderCfg())
        return cls()
