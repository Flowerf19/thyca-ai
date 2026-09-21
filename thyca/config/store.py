"""Config-file I/O: load, atomic locked save, defaults, and the agent guide.

This module owns ``~/.thyca/config.json`` access. Other services receive a
config slice, and secrets resolve at call time (see :mod:`secrets`).
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any

from filelock import FileLock
from filelock import Timeout as FileLockTimeout

from .auth import auth_path_for, auth_to_dict, load_auth_keys, merge_auth_keys
from .errors import ConfigError
from .parsing import _parse_dict
from .root import Config
from .timeline import TimelineCfg, _system_timezone

GUIDE_NAME = "read_after_config.md"


def config_path() -> Path:
    return Path.home() / ".thyca" / "config.json"


def default_config() -> Config:
    """Defaults for a fresh config; timezone follows the host system."""
    return Config(timeline=TimelineCfg(timezone=_system_timezone()))


def write_config_guide() -> Path | None:
    """Copy the packaged agent config guide into ~/.thyca (best effort).

    Returns the written path, or None when the guide is not packaged
    (source checkout) or cannot be written.
    """
    # The guide ships beside the thyca package (thyca/read_after_config.md),
    # one level above this subpackage.
    packaged = Path(__file__).resolve().parent.parent / GUIDE_NAME
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
    return merge_auth_keys(_parse_dict(raw), load_auth_keys(auth_path_for(target)))


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
    auth_target = auth_path_for(target)
    temporary = target.with_suffix(target.suffix + ".tmp")
    auth_temporary = auth_target.with_suffix(auth_target.suffix + ".tmp")
    lock_path = target.with_suffix(target.suffix + ".lock")

    try:
        if target == config_path():
            ensure_thyca_dir()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(lock_path), timeout=5):
            # Auth first: a crash between the two writes still leaves the new
            # keys durable (auth wins on load).
            _atomic_write_json(auth_temporary, auth_target, auth_to_dict(config))
            _atomic_write_json(temporary, target, config.to_dict())
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
        for stale in (temporary, auth_temporary):
            try:
                stale.unlink(missing_ok=True)
            except OSError:
                pass


def _atomic_write_json(temporary: Path, target: Path, payload: dict[str, Any]) -> None:
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary.replace(target)
    target.chmod(0o600)
