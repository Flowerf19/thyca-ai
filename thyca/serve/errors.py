"""Turn error mapping + turn body parsing (split from bridge.py).

Pure mapping: exceptions in, public ``(status, code, message)`` out. Never
leaks stack/path/secret — unexpected errors always map to the constant
``chat unavailable``. Shared by ``/turn`` and ``/turn/stream`` so the two
cannot drift.
"""
from __future__ import annotations

from thyca.llm.llm_base import LLMError
from thyca.sessions import SessionBusy, SessionCorrupt, SessionError, SessionNotFound


def _with_json(handler, fn, *, error: str = "invalid body"):
    """Run ``fn(payload)`` with the parsed JSON body; 400 when unreadable.

    The one malformed-body preamble for every JSON endpoint. ``/turn/stream``
    keeps its legacy message via ``error="invalid text"``."""
    try:
        payload = handler._read_json()
    except ValueError:
        handler._json(400, {"error": error})
        return None
    return fn(payload)


def parse_turn_body(payload: dict) -> tuple[str, str | None, str | None, bool]:
    """Shared by /turn and /turn/stream. Extra keys ignored."""
    # Local import: thyca.app.chat_app imports thyca.serve.turn_state, so a
    # top-level import here would cycle (M7-TASK-013).
    from thyca.app.turn_options import _clean_turn_text, validate_turn_options

    model, effort, retry = validate_turn_options(payload)
    if retry:
        text = payload.get("text")
        return text if isinstance(text, str) else "", model, effort, True
    # Empty/over-long text 400s here now instead of in ChatApp.turn; the HTTP
    # shape is identical (400 invalid text + reason either way).
    return _clean_turn_text(payload.get("text"), retry=False), model, effort, False


def public_turn_error(exc: Exception) -> tuple[int, str, str]:
    """Map a turn exception to a public ``(status, code, message)``.

    Shared by ``/turn`` and ``/turn/stream`` so the two cannot drift. Never
    leaks stack/path/secret: unexpected errors and :class:`ConfigError` always
    map to the constant ``chat unavailable``; :class:`LLMError` keeps its
    provider-redacted/capped text.
    """
    # Local import: same cycle as parse_turn_body (M7-TASK-013).
    from thyca.app.chat_app import InvalidTurnOption, InvalidTurnText, TurnCancelled

    if isinstance(exc, InvalidTurnOption):
        message = str(exc)
        return 400, message.replace(" ", "_"), message
    if isinstance(exc, TurnCancelled):
        return 200, "cancelled", "cancelled"
    if isinstance(exc, InvalidTurnText):
        # Known client error: 400 with the reason (empty/too long/invalid).
        return 400, "invalid_text", f"invalid text: {exc}"
    if isinstance(exc, ValueError):
        # Unexpected ValueError is a bug, never a client error: 500. The
        # caller logs the public line; the message stays constant.
        return 500, "internal_error", "chat unavailable"
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
