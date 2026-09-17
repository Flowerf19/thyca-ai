"""Live thinking deltas. Content on purpose — not a TurnEvent."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ThinkingDelta:
    round: int
    delta: str

    def __post_init__(self) -> None:
        if isinstance(self.round, bool) or not isinstance(self.round, int) or self.round < 1:
            raise ValueError("round must be an integer >= 1")
        if not isinstance(self.delta, str) or not self.delta:
            raise ValueError("delta must be a non-empty string")

    def to_dict(self) -> dict:
        return {"type": "llm.thinking", "round": self.round, "delta": self.delta}
