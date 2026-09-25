"""Live thinking deltas. Content on purpose — not a TurnEvent."""
from __future__ import annotations

from dataclasses import dataclass

from .delta import _Delta


@dataclass(frozen=True)
class ThinkingDelta(_Delta):
    TYPE = "llm.thinking"
