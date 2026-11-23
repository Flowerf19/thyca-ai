from __future__ import annotations

from thyca.configs import ProviderCfg

from .anthropic_messages import AnthropicMessages
from .google_gemini import GoogleGemini
from .llm_base import Connect
from .openai_chat import OpenAIChat
from .openai_responses import OpenAIResponses

_KINDS = {
    "openai": OpenAIChat,
    "openai_chat": OpenAIChat,
    "openai_compat": OpenAIChat,
    "openai_responses": OpenAIResponses,
    "responses": OpenAIResponses,
    "anthropic": AnthropicMessages,
    "anthropic_messages": AnthropicMessages,
    "google": GoogleGemini,
    "google_gemini": GoogleGemini,
    "gemini": GoogleGemini,
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
        return cls(provider or ProviderCfg())
