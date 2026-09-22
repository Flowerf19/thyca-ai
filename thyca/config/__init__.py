"""Config package for the single Thyca config file (``~/.thyca/config.json``).

Layout (SOLID split of the old single module):

- :mod:`errors`, :mod:`defaults`, :mod:`validation`, :mod:`secrets` — shared kernel
- :mod:`providers`, :mod:`mcp`, :mod:`pricing`, :mod:`models`, :mod:`timeline`,
  :mod:`limits` — one entity per file
- :mod:`root` — the ``Config`` aggregate (wire form + resolution policy)
- :mod:`parsing` — raw-dict parsing and legacy migration
- :mod:`store` — file I/O (load / atomic save / defaults / guide)
- :mod:`compat` — deprecated shims

Import-compatible with the old ``thyca/config.py``: ``from thyca.config import
Config, load, save, ...`` keeps working.
"""
from __future__ import annotations

from .compat import CONFIG_FILENAME, THYCA_DIR_NAME, _lock_path, default_dict, thyca_dir
from .defaults import (
    DEFAULT_LIMITS_CONTEXT_TOKENS,
    DEFAULT_LIMITS_CONTEXT_TOKENS_MAX,
    DEFAULT_LIMITS_HOT_TAIL_KB,
    DEFAULT_LIMITS_LOOP_MAX,
    DEFAULT_PROVIDER_API,
    DEFAULT_PROVIDER_API_KEY_ENV,
    DEFAULT_PROVIDER_BASE_URL,
    DEFAULT_PROVIDER_ID,
    DEFAULT_PROVIDER_MODEL,
    DEFAULT_PROVIDER_REASONING_EFFORT,
    DEFAULT_TIMELINE_TIMEZONE,
    PROVIDER_APIS,
    REASONING_EFFORTS,
)
from .errors import ConfigError
from .limits import LimitsCfg
from .mcp import McpServerCfg
from .models import ModelCfg, _model_to_dict
from .parsing import _parse_dict
from .pricing import PricingCfg
from .providers import ProviderCfg, ProviderEntry
from .root import Config
from .schema import config_schema
from .store import (
    GUIDE_NAME,
    FileLock,
    config_path,
    default_config,
    ensure_default,
    ensure_thyca_dir,
    load,
    save,
    write_config_guide,
)
from .timeline import TimelineCfg

__all__ = [
    "CONFIG_FILENAME",
    "DEFAULT_LIMITS_CONTEXT_TOKENS",
    "DEFAULT_LIMITS_CONTEXT_TOKENS_MAX",
    "DEFAULT_LIMITS_HOT_TAIL_KB",
    "DEFAULT_LIMITS_LOOP_MAX",
    "DEFAULT_PROVIDER_API",
    "DEFAULT_PROVIDER_API_KEY_ENV",
    "DEFAULT_PROVIDER_BASE_URL",
    "DEFAULT_PROVIDER_ID",
    "DEFAULT_PROVIDER_MODEL",
    "DEFAULT_PROVIDER_REASONING_EFFORT",
    "DEFAULT_TIMELINE_TIMEZONE",
    "GUIDE_NAME",
    "PROVIDER_APIS",
    "REASONING_EFFORTS",
    "THYCA_DIR_NAME",
    "Config",
    "ConfigError",
    "FileLock",
    "LimitsCfg",
    "McpServerCfg",
    "ModelCfg",
    "PricingCfg",
    "ProviderCfg",
    "ProviderEntry",
    "config_schema",
    "TimelineCfg",
    "_lock_path",
    "_model_to_dict",
    "_parse_dict",
    "config_path",
    "default_config",
    "default_dict",
    "ensure_default",
    "ensure_thyca_dir",
    "load",
    "save",
    "thyca_dir",
    "write_config_guide",
]
