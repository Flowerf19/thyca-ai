from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from thyca.protocol import Message


@dataclass(frozen=True)
class Session:
    id: str
    path: Path
    messages: list[Message]
