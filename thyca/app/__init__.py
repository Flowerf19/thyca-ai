"""App layer: chat orchestration, CLI, UI, onboarding."""
from __future__ import annotations

from .chat_app import ChatApp
from .chat_ui import ChatUi
from .cli import Cli, build_parser, main
from .onboarding import (
    ProviderProbeError,
    apply_provider,
    provider_ready,
    test_chat,
    test_provider_api,
    test_responses_chat,
    validate_provider,
)

__all__ = [
    "ChatApp",
    "ChatUi",
    "Cli",
    "ProviderProbeError",
    "apply_provider",
    "build_parser",
    "main",
    "provider_ready",
    "test_chat",
    "test_provider_api",
    "test_responses_chat",
    "validate_provider",
]
