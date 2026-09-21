"""Per-provider secrets in ``auth.json`` (next to ``config.json``).

``config.json`` never stores secrets: on save, provider keys split out here;
on load, they merge back into memory. Inline keys left in ``config.json``
(legacy or hand edits) lose to ``auth.json`` and are stripped on next save.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .errors import ConfigError

if TYPE_CHECKING:
    from .root import Config

AUTH_FILENAME = "auth.json"


def auth_path_for(config_path: Path) -> Path:
    return config_path.parent / AUTH_FILENAME


def load_auth_keys(auth_path: Path) -> dict[str, str | None]:
    """Provider id -> key material; {} when missing."""
    try:
        raw = json.loads(auth_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as error:
        raise ConfigError(f"{AUTH_FILENAME} is not valid JSON: {error} ({auth_path})") from error
    except OSError as error:
        raise ConfigError(f"cannot read {auth_path}: {error}") from error
    if not isinstance(raw, dict):
        raise ConfigError(f"{AUTH_FILENAME} must be a JSON object, got {type(raw).__name__}")
    providers = raw.get("providers", {})
    if not isinstance(providers, dict):
        raise ConfigError(f"{AUTH_FILENAME} providers must be an object")
    keys: dict[str, str | None] = {}
    for pid, entry in providers.items():
        if not isinstance(pid, str) or not pid:
            raise ConfigError(f"{AUTH_FILENAME} providers keys must be non-empty strings")
        if not isinstance(entry, dict):
            raise ConfigError(f"{AUTH_FILENAME} providers[{pid!r}] must be an object")
        key = entry.get("apiKey")
        if key is not None and (not isinstance(key, str) or not key.strip()):
            raise ConfigError(
                f"{AUTH_FILENAME} providers[{pid!r}].apiKey must be a non-empty string"
            )
        keys[pid] = key
    return keys


def merge_auth_keys(config: Config, keys: dict[str, str | None]) -> Config:
    """Overlay auth keys onto in-memory entries (auth wins over inline)."""
    providers = dict(config.providers)
    changed = False
    for pid, key in keys.items():
        if key is None or pid not in providers:
            continue
        if providers[pid].apiKey != key:
            providers[pid] = replace(providers[pid], apiKey=key)
            changed = True
    return replace(config, providers=providers) if changed else config


def auth_to_dict(config: Config) -> dict[str, Any]:
    """Wire form of ``auth.json``: only providers carrying key material."""
    return {
        "providers": {
            pid: {"apiKey": entry.apiKey}
            for pid, entry in config.providers.items()
            if entry.apiKey
        }
    }
