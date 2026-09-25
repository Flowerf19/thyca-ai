"""Shared base for live LLM deltas (content + thinking).

Both ride the event queue and hub without turn semantics; only the ``type``
tag differs. Validation and wire shape live here so the two cannot drift.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True)
class _Delta:
    TYPE: ClassVar[str] = ""
    round: int = 0
    delta: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.round, bool) or not isinstance(self.round, int) or self.round < 1:
            raise ValueError("round must be an integer >= 1")
        if not isinstance(self.delta, str) or not self.delta:
            raise ValueError("delta must be a non-empty string")

    def to_dict(self) -> dict:
        return {"type": self.TYPE, "round": self.round, "delta": self.delta}
