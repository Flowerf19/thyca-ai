"""Backward-compat shims — deprecated but re-exported to avoid ImportError
for external consumers that imported deprecated helpers.
"""
from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

from .store import config_path, default_config

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
