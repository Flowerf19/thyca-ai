"""Provider entities: stored entries and resolved per-turn connections."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from .defaults import (
    DEFAULT_PROVIDER_API,
    DEFAULT_PROVIDER_API_KEY_ENV,
    DEFAULT_PROVIDER_BASE_URL,
    DEFAULT_PROVIDER_MODEL,
    DEFAULT_PROVIDER_REASONING_EFFORT,
    PROVIDER_APIS,
    REASONING_EFFORTS,
)
from .errors import ConfigError
from .secrets import _resolve_api_key
from .validation import _text

if TYPE_CHECKING:
    from .models import ModelCfg


def _provider_to_dict(entry: ProviderEntry) -> dict[str, Any]:
    """Wire form without secrets: keys live in auth.json, never config.json."""
    data = asdict(entry)
    data.pop("apiKey", None)
    return data


def _check_shared(
    baseUrl: str,
    apiKeyEnv: str,
    apiKey: str | None,
    reasoningEffort: str,
    api: str,
) -> None:
    """The one field check for stored entries and resolved connections."""
    _text(baseUrl, "provider.baseUrl")
    _text(apiKeyEnv, "provider.apiKeyEnv")
    _text(apiKey, "provider.apiKey", allow_none=True, non_empty=True)
    # STRICT like ModelCfg: a non-http URL or junk effort never worked, so
    # fail at parse, not in the HTTP layer mid-turn.
    if not baseUrl.startswith(("http://", "https://")):
        raise ConfigError(
            f"provider.baseUrl must start with http:// or https://: {baseUrl!r}"
        )
    if not isinstance(reasoningEffort, str) or not reasoningEffort.strip():
        raise ConfigError("provider.reasoningEffort must be a non-empty string")
    if api not in PROVIDER_APIS:
        raise ConfigError(
            f"provider.api must be one of {'/'.join(PROVIDER_APIS)}, got {api!r}"
        )


def _check_global_effort(effort: str, name: str) -> None:
    """One global-level check for stored entries and context-free connections."""
    if effort not in REASONING_EFFORTS:
        raise ConfigError(
            f"{name} must be one of {'/'.join(REASONING_EFFORTS)} "
            f"(or declare models[].reasoningEfforts), got {effort!r}"
        )


def _check_effort_set(efforts: tuple[str, ...], effort: str) -> None:
    """Own-set shape + membership; the global check applies only when empty."""
    if not isinstance(efforts, tuple) or not all(
        isinstance(level, str) and level.strip() for level in efforts
    ):
        raise ConfigError(
            "provider.reasoningEfforts must be a tuple of non-empty strings"
        )
    if len(set(efforts)) != len(efforts):
        raise ConfigError("provider.reasoningEfforts must not contain duplicates")
    if efforts:
        if effort not in efforts:
            raise ConfigError(
                f"provider.reasoningEffort {effort!r} is not in "
                f"provider.reasoningEfforts ({'/'.join(efforts)})"
            )
    else:
        _check_global_effort(effort, "provider.reasoningEffort")


class _ApiKeyMixin:
    """Shared secret resolution for stored entries and connections."""

    apiKey: str | None
    apiKeyEnv: str

    def api_key(self) -> str:
        return _resolve_api_key(self.apiKey, self.apiKeyEnv)


@dataclass(frozen=True)
class ProviderEntry(_ApiKeyMixin):
    """One named provider stored in ``Config.providers`` (no model: models point here)."""

    baseUrl: str = DEFAULT_PROVIDER_BASE_URL
    apiKeyEnv: str = DEFAULT_PROVIDER_API_KEY_ENV
    apiKey: str | None = field(default=None, repr=False)
    reasoningEffort: str = DEFAULT_PROVIDER_REASONING_EFFORT
    api: str = DEFAULT_PROVIDER_API

    def __post_init__(self) -> None:
        _check_shared(
            self.baseUrl, self.apiKeyEnv, self.apiKey, self.reasoningEffort, self.api
        )
        # Stored entries are model-agnostic: custom levels live on models.
        _check_global_effort(self.reasoningEffort, "provider.reasoningEffort")


@dataclass(frozen=True)
class ProviderCfg(_ApiKeyMixin):
    """Resolved connection for one turn: a provider entry + the chosen model.

    ``reasoningEfforts`` is the chosen model's own set (empty when it declares
    none): the global check applies only then, mirroring :class:`ModelCfg`.
    """

    baseUrl: str = DEFAULT_PROVIDER_BASE_URL
    apiKeyEnv: str = DEFAULT_PROVIDER_API_KEY_ENV
    # apiKey before model: settings UI flow is key → fetch models → pick model.
    apiKey: str | None = field(default=None, repr=False)
    reasoningEffort: str = DEFAULT_PROVIDER_REASONING_EFFORT
    api: str = DEFAULT_PROVIDER_API
    model: str = DEFAULT_PROVIDER_MODEL
    reasoningEfforts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _check_shared(
            self.baseUrl, self.apiKeyEnv, self.apiKey, self.reasoningEffort, self.api
        )
        _text(self.model, "provider.model")
        _check_effort_set(self.reasoningEfforts, self.reasoningEffort)


def _base_url_for(entry_base_url: str, model_cfg: ModelCfg | None) -> str:
    """Legacy per-model endpoint wins over its provider's URL (0.8.2 hatch)."""
    if model_cfg is not None and model_cfg.baseUrl:
        return model_cfg.baseUrl
    return entry_base_url


def resolve(
    entry: ProviderEntry, model_id: str, model_cfg: ModelCfg | None = None
) -> ProviderCfg:
    """Pure resolution: a stored entry + model overrides → frozen connection.

    A model's own reasoningEffort wins; its own set governs only that
    override (an inherited entry effort stays on the global check).
    """
    effort = entry.reasoningEffort
    own_set: tuple[str, ...] = ()
    if model_cfg is not None and model_cfg.reasoningEffort:
        effort = model_cfg.reasoningEffort
        own_set = model_cfg.reasoningEfforts
    return ProviderCfg(
        baseUrl=_base_url_for(entry.baseUrl, model_cfg),
        apiKeyEnv=entry.apiKeyEnv,
        apiKey=entry.apiKey,
        reasoningEffort=effort,
        api=entry.api,
        model=model_id,
        reasoningEfforts=own_set,
    )
