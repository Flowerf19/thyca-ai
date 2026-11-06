"""Live reply (content) deltas. Like thinking deltas — on purpose not a
TurnEvent, so they ride the same queue and hub without turn semantics."""
from __future__ import annotations

from dataclasses import dataclass

from .delta import _Delta


@dataclass(frozen=True)
class ContentDelta(_Delta):
    TYPE = "llm.content"
