"""Global agent-limits entity."""
from __future__ import annotations

from dataclasses import dataclass

from .defaults import (
    DEFAULT_LIMITS_CONTEXT_TOKENS,
    DEFAULT_LIMITS_CONTEXT_TOKENS_MAX,
    DEFAULT_LIMITS_HOT_TAIL_KB,
    DEFAULT_LIMITS_LOOP_MAX,
)
from .errors import ConfigError
from .validation import _integer


@dataclass(frozen=True)
class LimitsCfg:
    loopMax: int = DEFAULT_LIMITS_LOOP_MAX
    hotTailKB: int = DEFAULT_LIMITS_HOT_TAIL_KB
    contextTokens: int = DEFAULT_LIMITS_CONTEXT_TOKENS

    def __post_init__(self) -> None:
        for value, name, lower, upper in (
            (self.loopMax, "limits.loopMax", 1, 200),
            (self.hotTailKB, "limits.hotTailKB", 1, 64),
            (self.contextTokens, "limits.contextTokens", 1000, DEFAULT_LIMITS_CONTEXT_TOKENS_MAX),
        ):
            _integer(value, name)
            if not lower <= value <= upper:
                raise ConfigError(f"{name} must be {lower}..{upper}, got {value}")
