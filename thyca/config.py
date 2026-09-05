"""Load and persist the single Thyca config file: ``~/.thyca/config.json``.

This module owns config-file I/O. Other services receive a config slice, and
secrets are read from the environment only when ``api_key()`` is called.
"""
from __future__ import annotations

import json
import math
import os
import pathlib
import re
import warnings
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from filelock import FileLock, Timeout as FileLockTimeout


class ConfigError(RuntimeError):
    """Config is malformed, unavailable, or cannot be written safely."""


DEFAULT_PROVIDER_BASE_URL = "https://api.openai.com/v1"
DEFAULT_PROVIDER_API_KEY_ENV = "THYCA_TOKEN"
DEFAULT_PROVIDER_MODEL = "gpt-4o-mini"
REASONING_EFFORTS = ("low", "medium", "high")
DEFAULT_PROVIDER_REASONING_EFFORT = "high"
DEFAULT_TIMELINE_TIMEZONE = "Asia/Ho_Chi_Minh"


def _system_timezone() -> str:
    """Best-effort IANA zone of the host; falls back to the default."""
    try:
        link = pathlib.Path("/etc/localtime")
        if link.is_symlink():
            target = link.resolve()
            if "zoneinfo" in target.parts:
                name = "/".join(target.parts[target.parts.index("zoneinfo") + 1 :])
                ZoneInfo(name)
                return name
    except (OSError, ZoneInfoNotFoundError, ValueError):
        pass
    return DEFAULT_TIMELINE_TIMEZONE
DEFAULT_LIMITS_LOOP_MAX = 200
DEFAULT_LIMITS_HOT_TAIL_KB = 4
DEFAULT_LIMITS_CONTEXT_TOKENS = 272_000
DEFAULT_LIMITS_CONTEXT_TOKENS_MAX = 2_000_000


def _text(
    value: object, name: str, *, allow_none: bool = False, non_empty: bool = True
) -> None:
    if value is None and allow_none:
        return
    if not isinstance(value, str):
        raise ConfigError(f"{name} must be a string, got {type(value).__name__}")
    if non_empty and not value.strip():
        raise ConfigError(f"{name} must be non-empty")


def _integer(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{name} must be an integer, got {type(value).__name__}")


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{name} must be a number, got {type(value).__name__}")
    num = float(value)
    if not math.isfinite(num):
        raise ConfigError(f"{name} must be finite, got {value!r}")
    if num < 0:
        raise ConfigError(f"{name} must be >= 0, got {value!r}")
    return num


@dataclass(frozen=True)
class ProviderCfg:
    baseUrl: str = DEFAULT_PROVIDER_BASE_URL
    apiKeyEnv: str = DEFAULT_PROVIDER_API_KEY_ENV
    # apiKey before model: settings UI flow is key → fetch models → pick model.
    apiKey: str | None = field(default=None, repr=False)
    reasoningEffort: str = DEFAULT_PROVIDER_REASONING_EFFORT
    model: str = DEFAULT_PROVIDER_MODEL

    def __post_init__(self) -> None:
        for value, name in (
            (self.baseUrl, "provider.baseUrl"),
            (self.apiKeyEnv, "provider.apiKeyEnv"),
            (self.model, "provider.model"),
        ):
            _text(value, name)
        _text(self.apiKey, "provider.apiKey", allow_none=True, non_empty=True)
        if self.reasoningEffort not in REASONING_EFFORTS:
            raise ConfigError(
                "provider.reasoningEffort must be one of "
                f"{'/'.join(REASONING_EFFORTS)}, got {self.reasoningEffort!r}"
            )

    def api_key(self) -> str:
        if self.apiKey:
            return self.apiKey
        value = os.environ.get(self.apiKeyEnv, "")
        if not value:
            raise ConfigError(
                f"{self.apiKeyEnv} not set — export {self.apiKeyEnv} "
                "or set provider.apiKey in config.json"
            )
        return value


@dataclass(frozen=True)
class McpServerCfg:
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.command, str) or not self.command.strip():
            raise ConfigError("mcpServers[].command must be a non-empty string")
        if not isinstance(self.args, list) or any(not isinstance(arg, str) for arg in self.args):
            raise ConfigError("mcpServers[].args must be a list of strings")
        if not isinstance(self.env, dict) or any(
            not isinstance(key, str) or not isinstance(value, str) for key, value in self.env.items()
        ):
            raise ConfigError("mcpServers[].env must map strings to strings")


@dataclass(frozen=True)
class PricingCfg:
    input: float = 0.0
    cache: float = 0.0
    output: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "input", _number(self.input, "pricing[].input"))
        object.__setattr__(self, "cache", _number(self.cache, "pricing[].cache"))
        object.__setattr__(self, "output", _number(self.output, "pricing[].output"))


@dataclass(frozen=True)
class ModelCfg:
    """A model the user registered: optional provider override + token prices.

    ``baseUrl`` empty means "use provider.baseUrl"; a non-empty value points
    the model at another OpenAI-compatible endpoint (multi-provider).
    Prices are USD / 1M tokens. Limits/reasoning empty or None inherit the
    global provider/limits values.
    """

    baseUrl: str = ""
    input: float = 0.0
    cache: float = 0.0
    output: float = 0.0
    reasoningEffort: str = ""
    loopMax: int | None = None
    hotTailKB: int | None = None
    contextTokens: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.baseUrl, str):
            raise ConfigError("models[].baseUrl must be a string")
        if self.baseUrl and not self.baseUrl.startswith(("http://", "https://")):
            raise ConfigError(
                f"models[].baseUrl must start with http:// or https://: {self.baseUrl!r}"
            )
        object.__setattr__(self, "input", _number(self.input, "models[].input"))
        object.__setattr__(self, "cache", _number(self.cache, "models[].cache"))
        object.__setattr__(self, "output", _number(self.output, "models[].output"))
        if not isinstance(self.reasoningEffort, str):
            raise ConfigError("models[].reasoningEffort must be a string")
        if self.reasoningEffort and self.reasoningEffort not in REASONING_EFFORTS:
            raise ConfigError(
                "models[].reasoningEffort must be one of "
                f"{'/'.join(REASONING_EFFORTS)}, got {self.reasoningEffort!r}"
            )
        for value, name, lower, upper in (
            (self.loopMax, "models[].loopMax", 1, 200),
            (self.hotTailKB, "models[].hotTailKB", 1, 64),
            (self.contextTokens, "models[].contextTokens", 1000, DEFAULT_LIMITS_CONTEXT_TOKENS_MAX),
        ):
            if value is None:
                continue
            _integer(value, name)
            if not lower <= value <= upper:
                raise ConfigError(f"{name} must be {lower}..{upper}, got {value}")


@dataclass(frozen=True)
class TimelineCfg:
    timezone: str = DEFAULT_TIMELINE_TIMEZONE

    def __post_init__(self) -> None:
        _text(self.timezone, "timeline.timezone")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError, KeyError) as error:
            raise ConfigError(
                f"timeline.timezone is not a valid IANA timezone: {self.timezone!r}"
            ) from error


@dataclass(frozen=True)
class LimitsCfg:
    loopMax: int = DEFAULT_LIMITS_LOOP_MAX
    hotTailKB: int = DEFAULT_LIMITS_HOT_TAIL_KB
    contextTokens: int = DEFAULT_LIMITS_CONTEXT_TOKENS

    def __post_init__(self) -> None:
        for value, name, lower, upper in (
            (self.loopMax, "limits.loopMax", 1, 200),
            (self.hotTailKB, "limits.hotTailKB", 1, 64),
            (self.contextTokens, "limits.contextTokens", 1000, DEFAULT_LIMITS_CONTEXT_TOKENS_MAX),
        ):
            _integer(value, name)
            if not lower <= value <= upper:
                raise ConfigError(f"{name} must be {lower}..{upper}, got {value}")


@dataclass(frozen=True)
class Config:
    provider: ProviderCfg = field(default_factory=ProviderCfg)
    mcpServers: dict[str, McpServerCfg] = field(default_factory=dict)
    timeline: TimelineCfg = field(default_factory=TimelineCfg)
    limits: LimitsCfg = field(default_factory=LimitsCfg)
    models: dict[str, ModelCfg] = field(default_factory=dict)
    pricing: dict[str, PricingCfg] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "provider": asdict(self.provider),
            "mcpServers": {name: asdict(server) for name, server in self.mcpServers.items()},
            "timeline": asdict(self.timeline),
            "limits": asdict(self.limits),
        }
        if self.models:
            payload["models"] = {name: _model_to_dict(cfg) for name, cfg in self.models.items()}
        if self.pricing:
            payload["pricing"] = {name: asdict(cfg) for name, cfg in self.pricing.items()}
        return payload

    def effective_pricing(self) -> dict[str, PricingCfg]:
        """Pricing overlay for cost tracing: user models win over pricing."""
        merged = dict(self.pricing)
        for name, model in self.models.items():
            merged[name] = PricingCfg(input=model.input, cache=model.cache, output=model.output)
        return merged

    def effective_provider(self) -> ProviderCfg:
        """Provider slice for the active model (per-model reasoning if set)."""
        registered = self.models.get(self.provider.model)
        if registered is None or not registered.reasoningEffort:
            return self.provider
        return replace(self.provider, reasoningEffort=registered.reasoningEffort)

    def effective_limits(self) -> LimitsCfg:
        """Limits for the active model; unset fields inherit the global block."""
        registered = self.models.get(self.provider.model)
        if registered is None:
            return self.limits
        return LimitsCfg(
            loopMax=self.limits.loopMax if registered.loopMax is None else registered.loopMax,
            hotTailKB=self.limits.hotTailKB if registered.hotTailKB is None else registered.hotTailKB,
            contextTokens=(
                self.limits.contextTokens
                if registered.contextTokens is None
                else registered.contextTokens
            ),
        )


def config_path() -> Path:
    return Path.home() / ".thyca" / "config.json"


def default_config() -> Config:
    """Defaults for a fresh config; timezone follows the host system."""
    return Config(timeline=TimelineCfg(timezone=_system_timezone()))


def _fields(
    raw: Any, name: str, names: tuple[str, ...], *, null_means_default: bool = False
) -> dict[str, Any]:
    if raw is None and null_means_default:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{name} must be an object, got {type(raw).__name__}")
    return {key: raw[key] for key in names if key in raw}


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
            if msg.startswith("mcpServers[]."):
                msg = msg[len("mcpServers[].") :]
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
        result[name] = ModelCfg(
            baseUrl=value.get("baseUrl", ""),
            input=_number(value.get("input", 0), f"models[{name!r}].input"),
            cache=_number(value.get("cache", 0), f"models[{name!r}].cache"),
            output=_number(value.get("output", 0), f"models[{name!r}].output"),
            reasoningEffort=value.get("reasoningEffort") or "",
            loopMax=_optional_int(value.get("loopMax"), f"models[{name!r}].loopMax"),
            hotTailKB=_optional_int(value.get("hotTailKB"), f"models[{name!r}].hotTailKB"),
            contextTokens=_optional_int(
                value.get("contextTokens"), f"models[{name!r}].contextTokens"
            ),
        )
    return result


def _optional_int(value: object, name: str) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    _integer(value, name)
    return int(value)


def _model_to_dict(cfg: ModelCfg) -> dict[str, Any]:
    data: dict[str, Any] = {
        "baseUrl": cfg.baseUrl,
        "input": cfg.input,
        "cache": cfg.cache,
        "output": cfg.output,
    }
    if cfg.reasoningEffort:
        data["reasoningEffort"] = cfg.reasoningEffort
    if cfg.loopMax is not None:
        data["loopMax"] = cfg.loopMax
    if cfg.hotTailKB is not None:
        data["hotTailKB"] = cfg.hotTailKB
    if cfg.contextTokens is not None:
        data["contextTokens"] = cfg.contextTokens
    return data


def _parse_dict(raw: dict[str, Any]) -> Config:
    config = Config(
        provider=ProviderCfg(
            **_fields(
                raw.get("provider", {}),
                "provider",
                ("baseUrl", "apiKeyEnv", "model", "reasoningEffort", "apiKey"),
            )
        ),
        mcpServers=_parse_mcp_servers(raw.get("mcpServers")),
        timeline=TimelineCfg(
            **_fields(
                raw.get("timeline"),
                "timeline",
                ("timezone",),
                null_means_default=True,
            )
        ),
        limits=LimitsCfg(
            **_fields(
                raw.get("limits"),
                "limits",
                ("loopMax", "hotTailKB", "contextTokens"),
                null_means_default=True,
            )
        ),
        models=_parse_models(raw.get("models")),
        pricing=_parse_pricing(raw.get("pricing")),
    )
    # Legacy migration: pricing-only entries become registered models so the
    # settings UI can edit them. pricing stays for older consumers.
    if config.pricing and not config.models:
        config = Config(
            provider=config.provider,
            mcpServers=config.mcpServers,
            timeline=config.timeline,
            limits=config.limits,
            models={
                name: ModelCfg(input=p.input, cache=p.cache, output=p.output)
                for name, p in config.pricing.items()
            },
            pricing=config.pricing,
        )
    return config


GUIDE_NAME = "read_after_config.md"


def write_config_guide() -> Path | None:
    """Copy the packaged agent config guide into ~/.thyca (best effort).

    Returns the written path, or None when the guide is not packaged
    (source checkout) or cannot be written.
    """
    packaged = Path(__file__).resolve().parent / GUIDE_NAME
    if not packaged.is_file():
        return None
    try:
        directory = ensure_thyca_dir()
        target = directory / GUIDE_NAME
        if not target.exists():
            target.write_text(packaged.read_text(encoding="utf-8"), encoding="utf-8")
            target.chmod(0o600)
        return target
    except OSError:
        return None


def ensure_thyca_dir() -> Path:
    """Ensure ``~/.thyca`` exists with mode 0700.

    Both ``mkdir`` and ``chmod`` failures are wrapped as :class:`ConfigError`
    (intentional tightening vs old code that only wrapped ``chmod``).
    """
    directory = config_path().parent
    try:
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o700)
    except OSError as error:
        raise ConfigError(f"cannot secure {directory} with mode 0700: {error}") from error
    return directory


def load(path: Path | None = None) -> Config:
    """Load a config, creating its default file if it is missing."""
    target = path or config_path()
    if not target.exists():
        return ensure_default(target)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ConfigError(f"config.json is not valid JSON: {error} ({target})") from error
    except OSError as error:
        raise ConfigError(f"cannot read {target}: {error}") from error
    if not isinstance(raw, dict):
        raise ConfigError(f"config.json must be a JSON object, got {type(raw).__name__}")
    return _parse_dict(raw)


def ensure_default(path: Path | None = None) -> Config:
    target = path or config_path()
    if target.exists():
        return load(target)
    config = default_config()
    save(config, target)
    write_config_guide()
    return config


def save(
    config: Config | None = None, path: Path | None = None, **kwargs: Any
) -> None:
    """Write atomically under a lock; never fall back to an unlocked write.

    Accepts legacy ``cfg`` keyword for backward compat: ``save(cfg=...)``.
    ``temporary`` cleanup is intentionally outside the lock so the stale
    ``.tmp`` is removed even when the lock cannot be acquired.
    """
    # Backward-compat alias: old signature was save(cfg, path)
    if "cfg" in kwargs:
        if config is not None:
            raise TypeError("save() got multiple values for config/cfg")
        warnings.warn("save(cfg=...) is deprecated, use save(config=...)", DeprecationWarning, stacklevel=2)
        config = kwargs.pop("cfg")
    if kwargs:
        raise TypeError(f"save() got unexpected keyword arguments: {', '.join(kwargs)}")
    if config is None:
        raise TypeError("save() missing required argument: 'config'")
    target = path or config_path()
    temporary = target.with_suffix(target.suffix + ".tmp")
    lock_path = target.with_suffix(target.suffix + ".lock")

    try:
        if target == config_path():
            ensure_thyca_dir()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(lock_path), timeout=5):
            temporary.write_text(
                json.dumps(config.to_dict(), indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            temporary.chmod(0o600)
            temporary.replace(target)
            target.chmod(0o600)
    except FileLockTimeout as error:
        raise ConfigError(f"timed out waiting for config lock: {lock_path}") from error
    except ConfigError:
        raise
    except OSError as error:
        raise ConfigError(f"cannot write {target}: {error}") from error
    except RuntimeError as error:
        # FileLock acquire failures surface as RuntimeError (e.g. flock unsupported)
        raise ConfigError(f"cannot lock {target}: {error}") from error
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Backward-compat shims — deprecated but re-exported to avoid ImportError
# for external consumers that imported deprecated helpers.
# ---------------------------------------------------------------------------

THYCA_DIR_NAME = ".thyca"
CONFIG_FILENAME = "config.json"


def thyca_dir() -> Path:
    warnings.warn("thyca_dir() is deprecated, use config_path().parent", DeprecationWarning, stacklevel=2)
    return config_path().parent


def _lock_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".lock")


def default_dict() -> dict[str, Any]:
    warnings.warn("default_dict() is deprecated, use default_config().to_dict()", DeprecationWarning, stacklevel=2)
    return default_config().to_dict()
