"""Shared error type for the config package."""
from __future__ import annotations


class ConfigError(RuntimeError):
    """Config is malformed, unavailable, or cannot be written safely."""
