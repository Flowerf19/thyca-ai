"""Per-turn options + turn text validation (split from chat_app, M8)."""
from __future__ import annotations

from dataclasses import replace

from thyca.config import REASONING_EFFORTS, Config

TEXT_MAX = 4000
MODEL_MAX = 200


class InvalidTurnOption(ValueError):
    """Per-turn model/effort/retry the HTTP layer reports as 400."""


class InvalidTurnText(ValueError):
    """Turn text the client must fix; the message is the reason.

    Reasons are exactly ``empty`` / ``too long`` / ``invalid`` (non-string).
    Serve maps this to 400 with the reason; any other ValueError is a bug.
    """


def validate_turn_options(payload: dict) -> tuple[str | None, str | None, bool]:
    """Model/effort/retry shape from a turn payload (no cfg membership).

    The one shape check serve + app share; membership (model in cfg.models)
    stays in :func:`overlay_turn_cfg`, which runs at turn time against the
    live config. Raises :class:`InvalidTurnOption`."""
    retry = payload.get("retry") is True
    if "model" in payload:
        model = payload["model"]
        if (
            not isinstance(model, str)
            or not model
            or len(model) > MODEL_MAX
            or "\n" in model
            or "\r" in model
            or not model.strip()
        ):
            raise InvalidTurnOption("invalid model")
    else:
        model = None
    if "effort" in payload:
        if not isinstance(payload["effort"], str) or not payload["effort"].strip():
            raise InvalidTurnOption("invalid effort")
        effort = payload["effort"]
    else:
        effort = None
    return model, effort, retry


def overlay_turn_cfg(cfg: Config, model: str | None, effort: str | None) -> Config:
    chosen = cfg.defaultModel if model is None else model
    if chosen != cfg.defaultModel and chosen not in cfg.models:
        raise InvalidTurnOption("invalid model")
    if effort is not None:
        if not isinstance(effort, str) or not effort.strip():
            raise InvalidTurnOption("invalid effort")
        # Model-aware membership (F5): the model's own set governs when it
        # declares one, else the global set. InvalidTurnOption (400), never a
        # ConfigError leak from the resolve below.
        registered = cfg.models.get(chosen)
        own_set = registered.reasoningEfforts if registered is not None else ()
        allowed = own_set or REASONING_EFFORTS
        if effort not in allowed:
            if own_set:
                raise InvalidTurnOption(
                    f"invalid effort {effort!r}: model {chosen!r} allows "
                    f"{'/'.join(allowed)}"
                )
            raise InvalidTurnOption(
                f"invalid effort {effort!r}: use {'/'.join(allowed)} "
                "(or declare models[].reasoningEfforts)"
            )
    return replace(cfg, defaultModel=chosen)


def _clean_turn_text(text: object, *, retry: bool) -> str:
    if retry:
        return ""
    if not isinstance(text, str):
        raise InvalidTurnText("invalid")
    cleaned = text.strip()
    if not cleaned:
        raise InvalidTurnText("empty")
    if len(cleaned) > TEXT_MAX:
        raise InvalidTurnText("too long")
    return cleaned
