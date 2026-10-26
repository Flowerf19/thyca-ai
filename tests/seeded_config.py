"""Test-only runnable config: one provider + default model on empty defaults.

Production ``default_config()`` is intentionally empty (no provider); suites
that run turns against a mock LLM seed an explicit provider here so every
file shares the same fixture shape. Nothing here changes product behavior.
"""
from __future__ import annotations

from dataclasses import replace

from thyca.config import Config, ProviderEntry, default_config


def seeded_config(
    *,
    provider_id: str = "test",
    base_url: str = "https://test.invalid/v1",
    api_key: str = "test-key",
    model: str = "gpt-4o-mini",
) -> Config:
    """A runnable config: ``provider_id`` serves ``model`` (both explicit)."""
    return replace(
        default_config(),
        providers={
            provider_id: ProviderEntry(baseUrl=base_url, apiKey=api_key),
        },
        defaultProvider=provider_id,
        defaultModel=model,
    )
