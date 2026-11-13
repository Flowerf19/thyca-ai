"""Platform path conventions and descriptor-safe filesystem primitives."""
from __future__ import annotations

from .fs import (
    MAX_ANCESTOR_SYMLINKS,
    OVERFLOW_UID,
    FsCapabilities,
    TrustedParent,
    TrustedParentError,
    fs_capabilities,
    open_trusted_parent,
    same_inode,
    trusted_uids,
)
from .paths import (
    STICKY_TMP_ANCESTORS,
    default_thyca_dir,
    temp_dir,
    xdg_cache_home,
    xdg_config_home,
    xdg_data_home,
    xdg_state_home,
)

__all__ = [
    "FsCapabilities",
    "MAX_ANCESTOR_SYMLINKS",
    "OVERFLOW_UID",
    "STICKY_TMP_ANCESTORS",
    "TrustedParent",
    "TrustedParentError",
    "default_thyca_dir",
    "fs_capabilities",
    "open_trusted_parent",
    "same_inode",
    "temp_dir",
    "trusted_uids",
    "xdg_cache_home",
    "xdg_config_home",
    "xdg_data_home",
    "xdg_state_home",
]
