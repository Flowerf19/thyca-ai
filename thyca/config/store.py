"""Config-file I/O: load, atomic locked save, defaults, and the agent guide.

This module owns ``~/.thyca/config.json`` access. Other services receive a
config slice, and secrets resolve at call time (see :mod:`secrets`).
"""
from __future__ import annotations

import json
import os
import warnings
from pathlib import Path
from typing import Any

from filelock import FileLock
from filelock import Timeout as FileLockTimeout

from .auth import auth_path_for, load_auth_keys, merge_auth_keys
from .errors import ConfigError
from .parsing import _parse_dict, _unknown_keys
from .root import Config
from .timeline import TimelineCfg, _system_timezone

GUIDE_NAME = "read_before_config.md"


def config_path() -> Path:
    return Path.home() / ".thyca" / "config.json"


def _lock_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".lock")


def default_config() -> Config:
    """Defaults for a fresh config; timezone follows the host system."""
    return Config(timeline=TimelineCfg(timezone=_system_timezone()))


def write_config_guide() -> Path | None:
    """Copy the packaged agent config guide into ~/.thyca (best effort).

    Returns the written path, or None when the guide is not packaged
    (source checkout) or cannot be written.
    """
    # The guide ships in the factory seeds box (thyca/seeds/guides/).
    packaged = Path(__file__).resolve().parents[1] / "seeds" / "guides" / GUIDE_NAME
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
    config = _parse_dict(raw)
    # Forward-compat: unknown keys stay ignored (never reject), but warn once
    # per load so typos surface. warnings.warn dedups repeats for free.
    unknown = _unknown_keys(raw)
    if unknown:
        warnings.warn(
            f"ignoring unknown config keys: {', '.join(unknown)}",
            stacklevel=2,
        )
    return merge_auth_keys(config, load_auth_keys(auth_path_for(target)))


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
    temporary = _tmp_path(target)
    auth_temporary = _tmp_path(auth_target)
    lock_path = _lock_path(target)

    try:
        # Compare resolved: a symlink or non-canonical spelling of the live
        # config must still get the 0700 tightening, not a plain mkdir.
        if target.resolve() == config_path().resolve():
            ensure_thyca_dir()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(lock_path), timeout=5):
            # Auth first: a crash between the two writes still leaves the new
            # keys durable (auth wins on load). Keyless entries keep the live
            # key on disk: a save that never saw the secret must not wipe it.
            live = load_auth_keys(auth_target)
            providers = {}
            for pid, entry in config.providers.items():
                key = entry.apiKey or live.get(pid)
                if key:
                    providers[pid] = {"apiKey": key}
            _atomic_write_json(auth_target, {"providers": providers})
            _atomic_write_json(target, config.to_dict())
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


def _tmp_path(target: Path) -> Path:
    return target.with_name(target.name + ".tmp")


def atomic_write_text(target: Path, text: str, *, mode: int = 0o600) -> None:
    """Atomically replace ``target`` with ``text``: stale tmp unlinked, fresh
    tmp created 0600 (O_EXCL defeats the umask window), fsynced, replaced.

    The one atomic write for config + memory: every caller already holds its
    lock, so unlink-then-O_EXCL is safe. fsync lands the bytes before the
    rename; the post-replace chmod keeps pre-existing wider targets tight."""
    temporary = _tmp_path(target)
    temporary.unlink(missing_ok=True)
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), mode)  # defeat exotic umasks too
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(target)
    except BaseException:
        # A failed write never leaves a partial tmp behind; the next write
        # would unlink it first anyway, but cleanup belongs to the failure.
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    target.chmod(mode)


def _atomic_write_json(target: Path, payload: dict[str, Any]) -> None:
    atomic_write_text(target, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
