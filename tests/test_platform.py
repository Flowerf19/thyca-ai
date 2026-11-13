"""Tests for thyca.platform path seams and trusted parent opening."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from thyca.platform import (
    FsCapabilities,
    TrustedParentError,
    default_thyca_dir,
    fs_capabilities,
    open_trusted_parent,
    temp_dir,
    xdg_cache_home,
    xdg_config_home,
    xdg_data_home,
    xdg_state_home,
)


def test_fs_capabilities_reports_linux_posix_support() -> None:
    caps = fs_capabilities()
    assert isinstance(caps, FsCapabilities)
    assert caps.supports_dir_fd is True
    assert caps.supports_o_nofollow is True
    assert caps.supports_o_directory is True
    assert caps.supports_posix_uid is True


def test_path_helpers_default_and_xdg_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(fake_home))
    assert default_thyca_dir() == fake_home / ".thyca"

    for env_name in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.delenv(env_name, raising=False)
    assert xdg_config_home() == fake_home / ".config"
    assert xdg_data_home() == fake_home / ".local" / "share"
    assert xdg_state_home() == fake_home / ".local" / "state"
    assert xdg_cache_home() == fake_home / ".cache"

    # Relative XDG paths must be ignored per XDG Base Directory specification.
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative/cfg")
    assert xdg_config_home() == fake_home / ".config"

    abs_data = tmp_path / "xdg-data"
    monkeypatch.setenv("XDG_DATA_HOME", str(abs_data))
    assert xdg_data_home() == abs_data
    assert temp_dir().is_dir()


def test_open_trusted_parent_resolves_symlinks_and_closes_fd(tmp_path: Path) -> None:
    real_user = tmp_path / "var" / "home" / "user"
    real_user.mkdir(parents=True)
    (tmp_path / "home").symlink_to(Path("var/home"))

    target_root = tmp_path / "home" / "user" / ".thyca"
    with open_trusted_parent(target_root) as trusted:
        assert trusted.leaf_name == ".thyca"
        assert trusted.resolved_path == real_user.resolve() / ".thyca"
        assert trusted.parent_fd is not None
        fd_num = trusted.parent_fd
        assert os.fstat(fd_num).st_ino == real_user.stat().st_ino
    with pytest.raises(OSError):
        os.fstat(fd_num)


def test_open_trusted_parent_rejects_invalid_and_unsupported_environments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    for bad in (Path("/"), Path(".."), Path("a/../b"), ""):
        with pytest.raises(TrustedParentError):
            open_trusted_parent(bad)

    monkeypatch.setattr(
        "thyca.platform.fs.fs_capabilities",
        lambda: FsCapabilities(
            platform="win32",
            supports_dir_fd=False,
            supports_o_nofollow=False,
            supports_o_directory=False,
            supports_posix_uid=False,
        ),
    )
    with pytest.raises(TrustedParentError):
        open_trusted_parent(tmp_path / "vault")
