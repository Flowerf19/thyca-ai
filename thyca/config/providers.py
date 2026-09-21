"""Provider entities: stored entries and resolved per-turn connections."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .defaults import (
    DEFAULT_PROVIDER_API,
    DEFAULT_PROVIDER_API_KEY_ENV,
    DEFAULT_PROVIDER_BASE_URL,
    DEFAULT_PROVIDER_MODEL,
    DEFAULT_PROVIDER_REASONING_EFFORT,
    PROVIDER_APIS,
)
from .errors import ConfigError
from .secrets import _resolve_api_key
from .validation import _text


def _provider_to_dict(entry: ProviderEntry) -> dict[str, Any]:
    """Wire form without secrets: keys live in auth.json, never config.json."""
    data = asdict(entry)
    data.pop("apiKey", None)
    return data


def _provider_fields(entry: ProviderCfg | ProviderEntry, prefix: str) -> None:
    for value, name in (
        (entry.baseUrl, f"{prefix}.baseUrl"),
        (entry.apiKeyEnv, f"{prefix}.apiKeyEnv"),
    ):
        _text(value, name)
    _text(entry.apiKey, f"{prefix}.apiKey", allow_none=True, non_empty=True)
    if not isinstance(entry.reasoningEffort, str) or not entry.reasoningEffort.strip():
        raise ConfigError(f"{prefix}.reasoningEffort must be a non-empty string")
    if entry.api not in PROVIDER_APIS:
        raise ConfigError(
            f"{prefix}.api must be one of {'/'.join(PROVIDER_APIS)}, got {entry.api!r}"
        )


@dataclass(frozen=True)
class ProviderEntry:
    """One named provider stored in ``Config.providers`` (no model: models point here)."""

    baseUrl: str = DEFAULT_PROVIDER_BASE_URL
    apiKeyEnv: str = DEFAULT_PROVIDER_API_KEY_ENV
    apiKey: str | None = field(default=None, repr=False)
    reasoningEffort: str = DEFAULT_PROVIDER_REASONING_EFFORT
    api: str = DEFAULT_PROVIDER_API

    def __post_init__(self) -> None:
        _provider_fields(self, "provider")

    def api_key(self) -> str:
        return _resolve_api_key(self.apiKey, self.apiKeyEnv)


@dataclass(frozen=True)
class ProviderCfg:
    """Resolved connection for one turn: a provider entry + the chosen model."""

    baseUrl: str = DEFAULT_PROVIDER_BASE_URL
    apiKeyEnv: str = DEFAULT_PROVIDER_API_KEY_ENV
    # apiKey before model: settings UI flow is key → fetch models → pick model.
    apiKey: str | None = field(default=None, repr=False)
    reasoningEffort: str = DEFAULT_PROVIDER_REASONING_EFFORT
    api: str = DEFAULT_PROVIDER_API
    model: str = DEFAULT_PROVIDER_MODEL

    def __post_init__(self) -> None:
        _provider_fields(self, "provider")
        _text(self.model, "provider.model")

    def api_key(self) -> str:
        return _resolve_api_key(self.apiKey, self.apiKeyEnv)
