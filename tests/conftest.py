from __future__ import annotations

import functools
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_FLATPAK_CHROMIUM_IDS = ("com.google.Chrome", "org.chromium.Chromium")


def _can_launch(binary: str) -> bool:
    try:
        result = subprocess.run(
            [binary, "--version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=25,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


@functools.lru_cache(maxsize=1)
def chromium_binary() -> str | None:
    env_bin = os.environ.get("THYCA_CHROME")
    if env_bin:
        return env_bin if _can_launch(env_bin) else None
    for name in ("google-chrome", "chromium", "chromium-browser", *_FLATPAK_CHROMIUM_IDS):
        found = shutil.which(name)
        if found and _can_launch(found):
            return found
    home = Path.home()
    for base in (home / ".local/share/flatpak/exports/bin", Path("/var/lib/flatpak/exports/bin")):
        for app_id in _FLATPAK_CHROMIUM_IDS:
            candidate = base / app_id
            if candidate.is_file() and os.access(candidate, os.X_OK) and _can_launch(str(candidate)):
                return str(candidate)
    return None


def chromium_env(chrome: str) -> dict[str, str]:
    if "flatpak" in chrome:
        return dict(os.environ)
    return dict(os.environ, HOME="/tmp/sandbox-home")


def cleanup_chromium_profile(chrome: str, user_data: Path) -> None:
    if "flatpak" not in chrome:
        return
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime_dir:
        return
    try:
        rel = user_data.resolve().relative_to(Path("/tmp").resolve())
    except ValueError:
        return
    flatpak_tmp = Path(runtime_dir) / ".flatpak" / Path(chrome).name / "tmp" / rel
    if flatpak_tmp.exists():
        shutil.rmtree(flatpak_tmp, ignore_errors=True)


@pytest.fixture(scope="session")
def node() -> str:
    binary = shutil.which("node")
    if not binary:
        pytest.skip("node binary not found")
    return binary
