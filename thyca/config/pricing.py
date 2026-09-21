"""Token-price entity (USD / 1M tokens)."""
from __future__ import annotations

from dataclasses import dataclass

from .validation import _number


@dataclass(frozen=True)
class PricingCfg:
    input: float = 0.0
    cache: float = 0.0
    output: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "input", _number(self.input, "pricing[].input"))
        object.__setattr__(self, "cache", _number(self.cache, "pricing[].cache"))
        object.__setattr__(self, "output", _number(self.output, "pricing[].output"))
