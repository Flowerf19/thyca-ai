"""Platform path conventions and XDG/temp directory seams."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

STICKY_TMP_ANCESTORS: tuple[str, ...] = (
    "/tmp",
    "/private/tmp",
    "/var/tmp",
    "/private/var/tmp",
    "/dev/shm",
)


def default_thyca_dir() -> Path:
    """Return the default Thyca state root (~/.thyca)."""
    return Path.home() / ".thyca"


def temp_dir() -> Path:
    """Return the active OS temporary directory."""
    return Path(tempfile.gettempdir())


def _xdg_dir(env_var: str, fallback_relative: str) -> Path:
    raw = os.environ.get(env_var, "")
    if raw and os.path.isabs(raw):
        return Path(raw)
    return Path.home() / fallback_relative


def xdg_config_home() -> Path:
    """Return XDG_CONFIG_HOME or ~/.config."""
    return _xdg_dir("XDG_CONFIG_HOME", ".config")


def xdg_data_home() -> Path:
    """Return XDG_DATA_HOME or ~/.local/share."""
    return _xdg_dir("XDG_DATA_HOME", ".local/share")


def xdg_state_home() -> Path:
    """Return XDG_STATE_HOME or ~/.local/state."""
    return _xdg_dir("XDG_STATE_HOME", ".local/state")


def xdg_cache_home() -> Path:
    """Return XDG_CACHE_HOME or ~/.cache."""
    return _xdg_dir("XDG_CACHE_HOME", ".cache")
