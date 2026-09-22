"""Turn error mapping + turn body parsing (split from bridge.py).

Pure mapping: exceptions in, public ``(status, code, message)`` out. Never
leaks stack/path/secret — unexpected errors always map to the constant
``chat unavailable``. Shared by ``/turn`` and ``/turn/stream`` so the two
cannot drift.
"""
from __future__ import annotations

from thyca.llm.llm_base import LLMError
from thyca.sessions import SessionBusy, SessionCorrupt, SessionError, SessionNotFound

_MODEL_MAX = 200


def parse_turn_body(payload: dict) -> tuple[str, str | None, str | None, bool]:
    """Shared by /turn and /turn/stream. Extra keys ignored."""
    # Local import: thyca.app.chat_app imports thyca.serve.turn_state, so a
    # top-level import here would cycle (M7-TASK-013).
    from thyca.app.chat_app import InvalidTurnOption

    retry = payload.get("retry") is True
    if "model" in payload:
        model = payload["model"]
        if (
            not isinstance(model, str)
            or not model
            or len(model) > _MODEL_MAX
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
    if retry:
        text = payload.get("text")
        return text if isinstance(text, str) else "", model, effort, True
    text = payload.get("text")
    if not isinstance(text, str):
        raise ValueError("invalid text")
    return text, model, effort, False


def public_turn_error(exc: Exception) -> tuple[int, str, str]:
    """Map a turn exception to a public ``(status, code, message)``.

    Shared by ``/turn`` and ``/turn/stream`` so the two cannot drift. Never
    leaks stack/path/secret: unexpected errors and :class:`ConfigError` always
    map to the constant ``chat unavailable``; :class:`LLMError` keeps its
    provider-redacted/capped text.
    """
    # Local import: same cycle as parse_turn_body (M7-TASK-013).
    from thyca.app.chat_app import InvalidTurnOption, TurnCancelled

    if isinstance(exc, InvalidTurnOption):
        message = str(exc)
        return 400, message.replace(" ", "_"), message
    if isinstance(exc, TurnCancelled):
        return 200, "cancelled", "cancelled"
    if isinstance(exc, ValueError):
        return 400, "invalid_text", "invalid text"
    if isinstance(exc, SessionBusy):
        return 409, "session_busy", "session busy"
    if isinstance(exc, SessionNotFound):
        return 404, "session_not_found", "session not found"
    if isinstance(exc, SessionCorrupt):
        return 503, "session_unreadable", "session unreadable"
    if isinstance(exc, SessionError):
        return 503, "session_unavailable", "session unavailable"
    if isinstance(exc, LLMError):
        return 503, "llm_error", str(exc)
    return 503, "chat_unavailable", "chat unavailable"
