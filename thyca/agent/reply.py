"""Live reply (content) deltas. Like thinking deltas — on purpose not a
TurnEvent, so they ride the same queue and hub without turn semantics."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ContentDelta:
    round: int
    delta: str

    def __post_init__(self) -> None:
        if isinstance(self.round, bool) or not isinstance(self.round, int) or self.round < 1:
            raise ValueError("round must be an integer >= 1")
        if not isinstance(self.delta, str) or not self.delta:
            raise ValueError("delta must be a non-empty string")

    def to_dict(self) -> dict:
        return {"type": "llm.content", "round": self.round, "delta": self.delta}
