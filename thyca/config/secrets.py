"""API-key resolution: inline secret or environment fallback."""
from __future__ import annotations

import os

from .errors import ConfigError


def _resolve_api_key(apiKey: str | None, apiKeyEnv: str) -> str:
    """Shared secret resolution for ProviderCfg/ProviderEntry."""
    if apiKey:
        if apiKey.startswith("!"):
            raise ConfigError(
                "provider.apiKey '!command' is no longer supported — "
                f"save the key in auth.json or export {apiKeyEnv}"
            )
        return apiKey
    value = os.environ.get(apiKeyEnv, "")
    if not value:
        raise ConfigError(
            f"{apiKeyEnv} not set — export {apiKeyEnv} "
            "or save the key in auth.json"
        )
    return value
