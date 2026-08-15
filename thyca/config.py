"""Config — single source for ~/.thyca/config.json (TASK-302).

Only module that touches the config file on disk. Every other service receives
one injected config slice: `LLMClient(cfg.provider)`, `MCPManager(cfg.mcpServers)`.

File location is fixed: Path.home() / ".thyca" / "config.json".
Secrets never stored raw — only apiKeyEnv name, resolved via os.environ at call time.
Missing file -> ensure_default() writes a minimal default (Claude mcpServers-compat).
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from filelock import FileLock, Timeout as FileLockTimeout

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ConfigError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Dataclasses — frozen, validation in __post_init__
# ---------------------------------------------------------------------------

DEFAULT_PROVIDER_BASE_URL = "https://api.openai.com/v1"
DEFAULT_PROVIDER_API_KEY_ENV = "OPENAI_API_KEY"
DEFAULT_PROVIDER_MODEL = "gpt-4o-mini"

DEFAULT_EMBEDDING_PROVIDER = "local"  # local | openai
DEFAULT_EMBEDDING_MODEL = "harrier-q4"
DEFAULT_TIMELINE_TIMEZONE = "Asia/Ho_Chi_Minh"

DEFAULT_LIMITS_LOOP_MAX = 10
DEFAULT_LIMITS_HOT_TAIL_KB = 4
DEFAULT_LIMITS_CONTEXT_TOKENS = 32000


@dataclass(frozen=True)
class ProviderCfg:
    baseUrl: str = DEFAULT_PROVIDER_BASE_URL
    apiKeyEnv: str = DEFAULT_PROVIDER_API_KEY_ENV
    model: str = DEFAULT_PROVIDER_MODEL

    def __post_init__(self) -> None:
        if not self.baseUrl or not self.baseUrl.strip():
            raise ConfigError("provider.baseUrl must be non-empty")
        if not self.apiKeyEnv or not self.apiKeyEnv.strip():
            raise ConfigError("provider.apiKeyEnv must be non-empty")
        if not self.model or not self.model.strip():
            raise ConfigError("provider.model must be non-empty")

    def api_key(self) -> str:
        """Resolve secret at call time — never stored in JSON."""
        val = os.environ.get(self.apiKeyEnv, "")
        if not val:
            raise ConfigError(
                f"{self.apiKeyEnv} not set — export {self.apiKeyEnv} or set provider.apiKeyEnv to your env var"
            )
        return val


@dataclass(frozen=True)
class EmbeddingCfg:
    provider: str = DEFAULT_EMBEDDING_PROVIDER  # local | openai
    model: str = DEFAULT_EMBEDDING_MODEL
    baseUrl: str | None = None
    apiKeyEnv: str | None = None

    def __post_init__(self) -> None:
        if self.provider not in ("local", "openai"):
            raise ConfigError(f"embedding.provider must be 'local' or 'openai', got {self.provider!r}")
        if not self.model or not self.model.strip():
            raise ConfigError("embedding.model must be non-empty")
        if self.provider == "openai":
            if not self.baseUrl or not self.baseUrl.strip():
                raise ConfigError("embedding.baseUrl is required when embedding.provider is 'openai'")
            if not self.apiKeyEnv or not self.apiKeyEnv.strip():
                raise ConfigError("embedding.apiKeyEnv is required when embedding.provider is 'openai'")

    def api_key(self) -> str | None:
        if self.apiKeyEnv is None:
            return None
        val = os.environ.get(self.apiKeyEnv, "")
        if not val:
            raise ConfigError(f"{self.apiKeyEnv} not set")
        return val


@dataclass(frozen=True)
class McpServerCfg:
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.command or not self.command.strip():
            raise ConfigError("mcpServers[].command must be non-empty")


@dataclass(frozen=True)
class TimelineCfg:
    timezone: str = DEFAULT_TIMELINE_TIMEZONE

    def __post_init__(self) -> None:
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError, KeyError) as e:
            raise ConfigError(f"timeline.timezone is not a valid IANA timezone: {self.timezone!r}") from e


@dataclass(frozen=True)
class LimitsCfg:
    loopMax: int = DEFAULT_LIMITS_LOOP_MAX
    hotTailKB: int = DEFAULT_LIMITS_HOT_TAIL_KB
    contextTokens: int = DEFAULT_LIMITS_CONTEXT_TOKENS

    def __post_init__(self) -> None:
        if not (1 <= self.loopMax <= 20):
            raise ConfigError(f"limits.loopMax must be 1..20, got {self.loopMax}")
        if not (1 <= self.hotTailKB <= 64):
            raise ConfigError(f"limits.hotTailKB must be 1..64, got {self.hotTailKB}")
        if not (1000 <= self.contextTokens <= 200_000):
            raise ConfigError(f"limits.contextTokens must be 1000..200000, got {self.contextTokens}")


@dataclass(frozen=True)
class Config:
    provider: ProviderCfg = field(default_factory=ProviderCfg)
    embedding: EmbeddingCfg = field(default_factory=EmbeddingCfg)
    mcpServers: dict[str, McpServerCfg] = field(default_factory=dict)
    timeline: TimelineCfg = field(default_factory=TimelineCfg)
    limits: LimitsCfg = field(default_factory=LimitsCfg)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": asdict(self.provider),
            "embedding": asdict(self.embedding),
            "mcpServers": {k: asdict(v) for k, v in self.mcpServers.items()},
            "timeline": asdict(self.timeline),
            "limits": asdict(self.limits),
        }


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

THYCA_DIR_NAME = ".thyca"
CONFIG_FILENAME = "config.json"


def thyca_dir() -> Path:
    return Path.home() / THYCA_DIR_NAME


def config_path() -> Path:
    return thyca_dir() / CONFIG_FILENAME


def _lock_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".lock")


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def default_config() -> Config:
    return Config()


def default_dict() -> dict[str, Any]:
    return default_config().to_dict()


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _string_field(
    raw: dict[str, Any],
    key: str,
    default: str | None,
    field_name: str,
    *,
    optional: bool = False,
) -> str | None:
    value = raw.get(key, default)
    if optional and value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(f"{field_name} must be a string, got {type(value).__name__}")
    return value


def _parse_provider(raw: Any) -> ProviderCfg:
    if not isinstance(raw, dict):
        raise ConfigError(f"provider must be an object, got {type(raw).__name__}")
    return ProviderCfg(
        baseUrl=_string_field(raw, "baseUrl", DEFAULT_PROVIDER_BASE_URL, "provider.baseUrl"),
        apiKeyEnv=_string_field(
            raw, "apiKeyEnv", DEFAULT_PROVIDER_API_KEY_ENV, "provider.apiKeyEnv"
        ),
        model=_string_field(raw, "model", DEFAULT_PROVIDER_MODEL, "provider.model"),
    )


def _parse_embedding(raw: Any) -> EmbeddingCfg:
    if raw is None:
        return EmbeddingCfg()
    if not isinstance(raw, dict):
        raise ConfigError(f"embedding must be an object, got {type(raw).__name__}")
    return EmbeddingCfg(
        provider=_string_field(
            raw, "provider", DEFAULT_EMBEDDING_PROVIDER, "embedding.provider"
        ),
        model=_string_field(raw, "model", DEFAULT_EMBEDDING_MODEL, "embedding.model"),
        baseUrl=_string_field(
            raw, "baseUrl", None, "embedding.baseUrl", optional=True
        ),
        apiKeyEnv=_string_field(
            raw, "apiKeyEnv", None, "embedding.apiKeyEnv", optional=True
        ),
    )


def _parse_mcp_servers(raw: Any) -> dict[str, McpServerCfg]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"mcpServers must be an object, got {type(raw).__name__}")
    out: dict[str, McpServerCfg] = {}
    for name, cfg in raw.items():
        if not isinstance(name, str) or not name.strip():
            raise ConfigError("mcpServers keys must be non-empty strings")
        if not isinstance(cfg, dict):
            raise ConfigError(f"mcpServers[{name!r}] must be an object")
        command = cfg.get("command", "")
        if not isinstance(command, str) or not command.strip():
            raise ConfigError(f"mcpServers[{name!r}].command must be a non-empty string")
        args = cfg.get("args", [])
        if not isinstance(args, list) or any(not isinstance(arg, str) for arg in args):
            raise ConfigError(f"mcpServers[{name!r}].args must be a list of strings")
        env = cfg.get("env", {})
        if not isinstance(env, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in env.items()
        ):
            raise ConfigError(f"mcpServers[{name!r}].env must map strings to strings")
        out[name] = McpServerCfg(command=command.strip(), args=list(args), env=dict(env))
    return out


def _parse_timeline(raw: Any) -> TimelineCfg:
    if raw is None:
        return TimelineCfg()
    if not isinstance(raw, dict):
        raise ConfigError(f"timeline must be an object, got {type(raw).__name__}")
    return TimelineCfg(
        timezone=_string_field(raw, "timezone", DEFAULT_TIMELINE_TIMEZONE, "timeline.timezone")
    )


def _parse_limits(raw: Any) -> LimitsCfg:
    if raw is None:
        return LimitsCfg()
    if not isinstance(raw, dict):
        raise ConfigError(f"limits must be an object, got {type(raw).__name__}")

    def integer(name: str, default: int) -> int:
        value = raw.get(name, default)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"limits.{name} must be an integer, got {type(value).__name__}")
        return value

    return LimitsCfg(
        loopMax=integer("loopMax", DEFAULT_LIMITS_LOOP_MAX),
        hotTailKB=integer("hotTailKB", DEFAULT_LIMITS_HOT_TAIL_KB),
        contextTokens=integer("contextTokens", DEFAULT_LIMITS_CONTEXT_TOKENS),
    )


def _parse_dict(raw: dict[str, Any]) -> Config:
    return Config(
        provider=_parse_provider(raw.get("provider", {})),
        embedding=_parse_embedding(raw.get("embedding")),
        mcpServers=_parse_mcp_servers(raw.get("mcpServers")),
        timeline=_parse_timeline(raw.get("timeline")),
        limits=_parse_limits(raw.get("limits")),
    )


# ---------------------------------------------------------------------------
# IO — only place that touches ~/.thyca/config.json
# ---------------------------------------------------------------------------


def ensure_thyca_dir() -> Path:
    d = thyca_dir()
    d.mkdir(parents=True, exist_ok=True)
    try:
        d.chmod(0o700)
    except OSError as e:
        raise ConfigError(f"cannot secure {d} with mode 0700: {e}") from e
    return d


def load(path: Path | None = None) -> Config:
    """Load config from path (default ~/.thyca/config.json).
    If file missing, create default and return it (never crash on first run).
    """
    p = path or config_path()
    if not p.exists():
        return ensure_default(p)
    try:
        raw_text = p.read_text(encoding="utf-8")
        raw: Any = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise ConfigError(f"config.json is not valid JSON: {e} ({p})") from e
    except OSError as e:
        raise ConfigError(f"cannot read {p}: {e}") from e
    if not isinstance(raw, dict):
        raise ConfigError(f"config.json must be a JSON object, got {type(raw).__name__}")
    return _parse_dict(raw)


def ensure_default(path: Path | None = None) -> Config:
    """Ensure config file exists; if missing write default and return it.
    If exists, load and return.
    """
    p = path or config_path()
    if p.exists():
        return load(p)
    cfg = default_config()
    save(cfg, p)
    return cfg


def save(cfg: Config, path: Path | None = None) -> None:
    p = path or config_path()
    if p == config_path():
        ensure_thyca_dir()
    else:
        p.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(cfg.to_dict(), indent=2, ensure_ascii=False) + "\n"
    tmp = p.with_suffix(p.suffix + ".tmp")

    try:
        with FileLock(str(_lock_path(p)), timeout=5):
            try:
                tmp.write_text(data, encoding="utf-8")
                tmp.chmod(0o600)
                tmp.replace(p)
                p.chmod(0o600)
            finally:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
    except FileLockTimeout as e:
        raise ConfigError(f"timed out waiting for config lock: {_lock_path(p)}") from e
    except OSError as e:
        raise ConfigError(f"cannot write {p}: {e}") from e
    except ConfigError:
        raise
    except Exception as e:
        raise ConfigError(f"cannot lock {p}: {e}") from e
