from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from thyca.core.protocol import Message


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
    # The automatic naming step ran its one attempt (success or failure).
    # Old sessions without the meta key load as False and stay eligible.
    naming_attempted: bool = False
