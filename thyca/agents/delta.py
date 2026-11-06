"""Shared base for live LLM deltas (content + thinking).

Both ride the event queue and hub without turn semantics; only the ``type``
tag differs. Validation and wire shape live here so the two cannot drift.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar

_BOT_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass(frozen=True)
class _Delta:
    TYPE: ClassVar[str] = ""
    round: int = 0
    delta: str = ""
    bot_id: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.round, bool) or not isinstance(self.round, int) or self.round < 1:
            raise ValueError("round must be an integer >= 1")
        if not isinstance(self.delta, str) or not self.delta:
            raise ValueError("delta must be a non-empty string")
        if self.bot_id is not None and not (
            isinstance(self.bot_id, str)
            and len(self.bot_id) <= 64
            and _BOT_ID_RE.fullmatch(self.bot_id)
        ):
            # Attribution is telemetry: a bad stamp is dropped.
            object.__setattr__(self, "bot_id", None)

    def to_dict(self) -> dict:
        payload = {"type": self.TYPE, "round": self.round, "delta": self.delta}
        if self.bot_id is not None:
            payload["bot_id"] = self.bot_id
        return payload
