"""Scalar validation helpers for config entities and parsing."""
from __future__ import annotations

import math

from .errors import ConfigError


def _text(
    value: object, name: str, *, allow_none: bool = False, non_empty: bool = True
) -> None:
    if value is None and allow_none:
        return
    if not isinstance(value, str):
        raise ConfigError(f"{name} must be a string, got {type(value).__name__}")
    if non_empty and not value.strip():
        raise ConfigError(f"{name} must be non-empty")


def _integer(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{name} must be an integer, got {type(value).__name__}")


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{name} must be a number, got {type(value).__name__}")
    num = float(value)
    if not math.isfinite(num):
        raise ConfigError(f"{name} must be finite, got {value!r}")
    if num < 0:
        raise ConfigError(f"{name} must be >= 0, got {value!r}")
    return num
