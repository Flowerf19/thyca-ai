"""Raw-dict parsing plus legacy-shape migration into :class:`Config`."""
from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from .defaults import DEFAULT_PROVIDER_ID, DEFAULT_PROVIDER_MODEL
from .errors import ConfigError
from .limits import LimitsCfg
from .mcp import McpServerCfg
from .models import ModelCfg
from .pricing import PricingCfg
from .providers import ProviderCfg, ProviderEntry
from .root import Config
from .timeline import TimelineCfg
from .validation import _integer, _number, _optional_int, _text


# Known-key sets, shared by _fields parsing and _unknown_keys collection so
# the warn list cannot drift from what parsing accepts.
_TOP_LEVEL_KEYS = (
    "providers",
    "provider",
    "defaultProvider",
    "defaultModel",
    "mcpServers",
    "timeline",
    "limits",
    "models",
    "pricing",
)
_PROVIDER_ENTRY_KEYS = ("baseUrl", "apiKeyEnv", "reasoningEffort", "apiKey", "api")
_LEGACY_PROVIDER_KEYS = _PROVIDER_ENTRY_KEYS + ("model",)
_TIMELINE_KEYS = ("timezone",)
_LIMITS_KEYS = ("loopMax", "hotTailKB", "contextTokens", "softTimeoutS")
_MODEL_KEYS = (
    "provider",
    "baseUrl",
    "input",
    "cache",
    "output",
    "reasoningEffort",
    "reasoningEfforts",
    "loopMax",
    "hotTailKB",
    "contextTokens",
)
_MCP_SERVER_KEYS = ("command", "args", "env")
_PRICING_KEYS = ("input", "cache", "cached_input", "output")


def _fields(
    raw: Any, name: str, names: tuple[str, ...], *, null_means_default: bool = False
) -> dict[str, Any]:
    if raw is None and null_means_default:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{name} must be an object, got {type(raw).__name__}")
    return {key: raw[key] for key in names if key in raw}


def _unknown_in(mapping: Any, known: tuple[str, ...], prefix: str) -> list[str]:
    """Dotted paths (``prefix.key``) for keys parsing would ignore."""
    if not isinstance(mapping, dict):
        return []
    return [f"{prefix}.{key}" for key in mapping if key not in known]


def _unknown_keys(raw: dict[str, Any]) -> list[str]:
    """Every unknown key in a raw config, dotted and sorted.

    Pure collection for the load-time warning; parsing itself keeps ignoring
    them (newer-config-on-older-binary must not break). Runs after a
    successful parse, so shapes below are already validated.
    """
    found = [key for key in raw if key not in _TOP_LEVEL_KEYS]
    providers = raw.get("providers")
    if isinstance(providers, dict):
        for name, value in providers.items():
            found += _unknown_in(value, _PROVIDER_ENTRY_KEYS, f"providers.{name}")
    else:
        found += _unknown_in(raw.get("provider"), _LEGACY_PROVIDER_KEYS, "provider")
    for name, value in (raw.get("models") or {}).items():
        found += _unknown_in(value, _MODEL_KEYS, f"models.{name}")
    for name, value in (raw.get("mcpServers") or {}).items():
        found += _unknown_in(value, _MCP_SERVER_KEYS, f"mcpServers.{name}")
    for name, value in (raw.get("pricing") or {}).items():
        found += _unknown_in(value, _PRICING_KEYS, f"pricing.{name}")
    found += _unknown_in(raw.get("timeline"), _TIMELINE_KEYS, "timeline")
    found += _unknown_in(raw.get("limits"), _LIMITS_KEYS, "limits")
    return sorted(found)


def _parse_mcp_servers(raw: Any) -> dict[str, McpServerCfg]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"mcpServers must be an object, got {type(raw).__name__}")

    result: dict[str, McpServerCfg] = {}
    for name, value in raw.items():
        if not isinstance(name, str) or not name.strip():
            raise ConfigError("mcpServers keys must be non-empty strings")
        if re.fullmatch(r"[A-Za-z0-9_-]+", name) is None:
            raise ConfigError(
                f"mcpServers[{name!r}] must match [A-Za-z0-9_-]+"
            )
        if not isinstance(value, dict):
            raise ConfigError(f"mcpServers[{name!r}] must be an object")
        raw_command = value.get("command", "")
        command = raw_command.strip() if isinstance(raw_command, str) else raw_command
        raw_args = value.get("args", [])
        args = list(raw_args) if isinstance(raw_args, list) else raw_args
        raw_env = value.get("env", {})
        env = dict(raw_env) if isinstance(raw_env, dict) else raw_env
        try:
            result[name] = McpServerCfg(command=command, args=args, env=env)
        except ConfigError as exc:
            msg = str(exc)
            msg = msg.removeprefix("mcpServers[].")
            raise ConfigError(f"mcpServers[{name!r}].{msg}") from exc
    return result


def _parse_pricing(raw: Any) -> dict[str, PricingCfg]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"pricing must be an object, got {type(raw).__name__}")
    result: dict[str, PricingCfg] = {}
    for name, value in raw.items():
        if not isinstance(name, str) or not name.strip():
            raise ConfigError("pricing keys must be non-empty strings")
        if not isinstance(value, dict):
            raise ConfigError(f"pricing[{name!r}] must be an object")
        # support alias cached_input -> cache
        raw_input = value.get("input")
        raw_cache = value.get("cache")
        if raw_cache is None and "cached_input" in value:
            raw_cache = value.get("cached_input")
        raw_output = value.get("output")
        if raw_input is None:
            raise ConfigError(f"pricing[{name!r}].input is required")
        if raw_cache is None:
            raise ConfigError(f"pricing[{name!r}].cache is required")
        if raw_output is None:
            raise ConfigError(f"pricing[{name!r}].output is required")
        result[name] = PricingCfg(
            input=_number(raw_input, f"pricing[{name!r}].input"),
            cache=_number(raw_cache, f"pricing[{name!r}].cache"),
            output=_number(raw_output, f"pricing[{name!r}].output"),
        )
    return result


def _parse_models(raw: Any) -> dict[str, ModelCfg]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"models must be an object, got {type(raw).__name__}")
    result: dict[str, ModelCfg] = {}
    for name, value in raw.items():
        if not isinstance(name, str) or not name.strip():
            raise ConfigError("models keys must be non-empty strings")
        if not isinstance(value, dict):
            raise ConfigError(f"models[{name!r}] must be an object")
        # Falsy-but-present provider/reasoningEffort (0, False) must fail
        # type validation, not silently become "": only missing/None default.
        provider = value.get("provider")
        effort = value.get("reasoningEffort")
        result[name] = ModelCfg(
            provider="" if provider is None else provider,
            baseUrl=value.get("baseUrl", ""),
            input=_number(value.get("input", 0), f"models[{name!r}].input"),
            cache=_number(value.get("cache", 0), f"models[{name!r}].cache"),
            output=_number(value.get("output", 0), f"models[{name!r}].output"),
            reasoningEffort="" if effort is None else effort,
            reasoningEfforts=_parse_efforts(value.get("reasoningEfforts"), f"models[{name!r}].reasoningEfforts"),
            loopMax=_optional_int(value.get("loopMax"), f"models[{name!r}].loopMax"),
            hotTailKB=_optional_int(value.get("hotTailKB"), f"models[{name!r}].hotTailKB"),
            contextTokens=_optional_int(
                value.get("contextTokens"), f"models[{name!r}].contextTokens"
            ),
        )
    return result


def _parse_efforts(raw: Any, name: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not raw or not all(
        isinstance(level, str) and level.strip() for level in raw
    ):
        raise ConfigError(f"{name} must be a non-empty list of non-empty strings")
    if len(set(raw)) != len(raw):
        raise ConfigError(f"{name} must not contain duplicates")
    return tuple(raw)


def _parse_providers(raw: Any) -> dict[str, ProviderEntry]:
    if not isinstance(raw, dict):
        raise ConfigError(f"providers must be an object, got {type(raw).__name__}")
    if not raw:
        raise ConfigError("providers must not be empty")
    result: dict[str, ProviderEntry] = {}
    for name, value in raw.items():
        if not isinstance(name, str) or not name.strip():
            raise ConfigError("providers keys must be non-empty strings")
        if re.fullmatch(r"[A-Za-z0-9_-]+", name) is None:
            raise ConfigError(f"providers[{name!r}] must match [A-Za-z0-9_-]+")
        if not isinstance(value, dict):
            raise ConfigError(f"providers[{name!r}] must be an object")
        try:
            result[name] = ProviderEntry(
                **_fields(
                    value,
                    f"providers[{name!r}]",
                    _PROVIDER_ENTRY_KEYS,
                )
            )
        except ConfigError as exc:
            msg = str(exc).removeprefix("provider.")
            raise ConfigError(f"providers[{name!r}].{msg}") from exc
    return result


def _parse_provider_block(raw: dict[str, Any]) -> tuple[dict[str, ProviderEntry], str, str]:
    """New ``providers`` shape, or the legacy single ``provider`` block migrated.

    When both are present the new shape wins and the legacy block is ignored.
    """
    if raw.get("providers") is not None:
        providers = _parse_providers(raw.get("providers"))
        default_provider = raw.get("defaultProvider", DEFAULT_PROVIDER_ID)
        _text(default_provider, "defaultProvider")
        if default_provider not in providers:
            raise ConfigError(
                f"defaultProvider {default_provider!r} is not in providers"
            )
        default_model = raw.get("defaultModel", DEFAULT_PROVIDER_MODEL)
        _text(default_model, "defaultModel")
        return providers, default_provider, default_model
    legacy = ProviderCfg(
        **_fields(
            raw.get("provider", {}),
            "provider",
            _LEGACY_PROVIDER_KEYS,
        )
    )
    return (
        {
            DEFAULT_PROVIDER_ID: ProviderEntry(
                baseUrl=legacy.baseUrl,
                apiKeyEnv=legacy.apiKeyEnv,
                apiKey=legacy.apiKey,
                reasoningEffort=legacy.reasoningEffort,
                api=legacy.api,
            )
        },
        DEFAULT_PROVIDER_ID,
        legacy.model,
    )


def _migrate_pricing_to_models(config: Config) -> Config:
    """Legacy migration: pricing-only entries become registered models."""
    if config.pricing and not config.models:
        return replace(
            config,
            models={
                name: ModelCfg(input=p.input, cache=p.cache, output=p.output)
                for name, p in config.pricing.items()
            },
        )
    return config


def _parse_dict(raw: dict[str, Any]) -> Config:
    providers, default_provider, default_model = _parse_provider_block(raw)
    models = _parse_models(raw.get("models"))
    for name, model in models.items():
        if model.provider and model.provider not in providers:
            raise ConfigError(
                f"models[{name!r}].provider {model.provider!r} does not exist"
            )
    config = Config(
        providers=providers,
        defaultProvider=default_provider,
        defaultModel=default_model,
        mcpServers=_parse_mcp_servers(raw.get("mcpServers")),
        timeline=TimelineCfg(
            **_fields(
                raw.get("timeline"),
                "timeline",
                _TIMELINE_KEYS,
                null_means_default=True,
            )
        ),
        limits=LimitsCfg(
            **_fields(
                raw.get("limits"),
                "limits",
                _LIMITS_KEYS,
                null_means_default=True,
            )
        ),
        models=models,
        pricing=_parse_pricing(raw.get("pricing")),
    )
    # Legacy migration: pricing-only entries become registered models so the
    # settings UI can edit them. pricing stays for older consumers.
    return _migrate_pricing_to_models(config)
