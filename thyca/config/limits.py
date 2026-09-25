"""Global agent-limits entity."""
from __future__ import annotations

from dataclasses import dataclass

from .defaults import (
    DEFAULT_LIMITS_CONTEXT_TOKENS,
    DEFAULT_LIMITS_HOT_TAIL_KB,
    DEFAULT_LIMITS_LOOP_MAX,
    DEFAULT_LIMITS_SOFT_TIMEOUT_S,
    LIMIT_RANGES,
)
from .errors import ConfigError
from .validation import _integer


@dataclass(frozen=True)
class LimitsCfg:
    loopMax: int = DEFAULT_LIMITS_LOOP_MAX
    hotTailKB: int = DEFAULT_LIMITS_HOT_TAIL_KB
    contextTokens: int = DEFAULT_LIMITS_CONTEXT_TOKENS
    softTimeoutS: int = DEFAULT_LIMITS_SOFT_TIMEOUT_S

    def __post_init__(self) -> None:
        for field_name in ("loopMax", "hotTailKB", "contextTokens", "softTimeoutS"):
            lower, upper = LIMIT_RANGES[field_name]
            name = f"limits.{field_name}"
            value = getattr(self, field_name)
            _integer(value, name)
            if not lower <= value <= upper:
                raise ConfigError(f"{name} must be {lower}..{upper}, got {value}")
