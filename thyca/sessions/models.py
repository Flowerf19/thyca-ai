from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from thyca.protocol import Message


@dataclass
class Session:
    id: str
    path: Path
    messages: list[Message]
    title: str | None = None
    # Who wrote ``title``: "user" (typed in the sidebar) or None/"agent".
    # A user title is displayed verbatim; the agent's naming policy only
    # filters what the model proposes.
    title_source: str | None = None
