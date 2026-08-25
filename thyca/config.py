"""Load and persist the single Thyca config file: ``~/.thyca/config.json``.

This module owns config-file I/O. Other services receive a config slice, and
secrets are read from the environment only when ``api_key()`` is called.
"""
from __future__ import annotations

import json
import os
import re
import warnings
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from filelock import FileLock, Timeout as FileLockTimeout


class ConfigError(RuntimeError):
    """Config is malformed, unavailable, or cannot be written safely."""


DEFAULT_PROVIDER_BASE_URL = "https://api.openai.com/v1"
DEFAULT_PROVIDER_API_KEY_ENV = "OPENAI_API_KEY"
DEFAULT_PROVIDER_MODEL = "gpt-4o-mini"
DEFAULT_TIMELINE_TIMEZONE = "Asia/Ho_Chi_Minh"
DEFAULT_LIMITS_LOOP_MAX = 200
DEFAULT_LIMITS_HOT_TAIL_KB = 4
DEFAULT_LIMITS_CONTEXT_TOKENS = 32000


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


@dataclass(frozen=True)
class ProviderCfg:
    baseUrl: str = DEFAULT_PROVIDER_BASE_URL
    apiKeyEnv: str = DEFAULT_PROVIDER_API_KEY_ENV
    model: str = DEFAULT_PROVIDER_MODEL
    apiKey: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        for value, name in (
            (self.baseUrl, "provider.baseUrl"),
            (self.apiKeyEnv, "provider.apiKeyEnv"),
            (self.model, "provider.model"),
        ):
            _text(value, name)
        _text(self.apiKey, "provider.apiKey", allow_none=True, non_empty=True)

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
            (self.contextTokens, "limits.contextTokens", 1000, 200_000),
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": asdict(self.provider),
            "mcpServers": {name: asdict(server) for name, server in self.mcpServers.items()},
            "timeline": asdict(self.timeline),
            "limits": asdict(self.limits),
        }


def config_path() -> Path:
    return Path.home() / ".thyca" / "config.json"


def default_config() -> Config:
    return Config()


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


def _parse_dict(raw: dict[str, Any]) -> Config:
    return Config(
        provider=ProviderCfg(
            **_fields(
                raw.get("provider", {}),
                "provider",
                ("baseUrl", "apiKeyEnv", "model", "apiKey"),
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
    )


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
    except Exception as error:
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
