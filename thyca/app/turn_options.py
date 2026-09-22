"""Per-turn options + turn text validation (split from chat_app, M8)."""
from __future__ import annotations

from dataclasses import replace

from thyca.config import Config

TEXT_MAX = 4000


class InvalidTurnOption(ValueError):
    """Per-turn model/effort/retry the HTTP layer reports as 400."""


def overlay_turn_cfg(cfg: Config, model: str | None, effort: str | None) -> Config:
    chosen = cfg.defaultModel if model is None else model
    if chosen != cfg.defaultModel and chosen not in cfg.models:
        raise InvalidTurnOption("invalid model")
    if effort is not None and (not isinstance(effort, str) or not effort.strip()):
        raise InvalidTurnOption("invalid effort")
    return replace(cfg, defaultModel=chosen)


def _clean_turn_text(text: object, *, retry: bool) -> str:
    if retry:
        return ""
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    cleaned = text.strip()
    if not cleaned:
        raise ValueError("empty")
    if len(cleaned) > TEXT_MAX:
        raise ValueError("too long")
    return cleaned
