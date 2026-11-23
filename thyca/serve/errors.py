"""Turn error mapping + turn body parsing (split from bridge.py).

Pure mapping: exceptions in, public ``(status, code, message)`` out. Never
leaks stack/path/secret — unexpected errors always map to the constant
``chat unavailable``. Shared by ``/turn`` and ``/turn/stream`` so the two
cannot drift.
"""
from __future__ import annotations

from thyca.conversations.runtime import (
    ConversationBusy,
    ConversationCorrupt,
    ConversationError,
    ConversationNotFound,
    NoLiveResponder,
)
from thyca.conversations.store.contracts import ConversationStoreError
from thyca.llms.llm_base import LLMError


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
    from thyca.app.turn_options import _clean_turn_text, validate_turn_options  # noqa: PLC0415 -- reason: serve/ lazy app import boundary

    model, effort, retry = validate_turn_options(payload)
    if retry:
        text = payload.get("text")
        return text if isinstance(text, str) else "", model, effort, True
    return _clean_turn_text(payload.get("text"), retry=False), model, effort, False


def public_turn_error(exc: Exception) -> tuple[int, str, str]:
    """Map a turn exception to a public ``(status, code, message)``.

    Shared by ``/turn`` and ``/turn/stream`` so the two cannot drift. Never
    leaks stack/path/secret: unexpected errors and :class:`ConfigError` always
    map to the constant ``chat unavailable``; :class:`LLMError` keeps its
    provider-redacted/capped text.
    """
    from thyca.app.chat_app import InvalidTurnOption, InvalidTurnText, TurnCancelled  # noqa: PLC0415 -- reason: serve/ lazy app import boundary

    if isinstance(exc, InvalidTurnOption):
        message = str(exc)
        return 400, message.replace(" ", "_"), message
    if isinstance(exc, TurnCancelled):
        return 200, "cancelled", "cancelled"
    if isinstance(exc, InvalidTurnText):
        return 400, "invalid_text", f"invalid text: {exc}"
    if isinstance(exc, ValueError):
        return 500, "internal_error", "chat unavailable"
    if isinstance(exc, NoLiveResponder):
        return 409, "no_live_participant", "conversation has no live participant"
    if isinstance(exc, ConversationBusy):
        return 409, "session_busy", "conversation busy"
    if isinstance(exc, ConversationNotFound):
        return 404, "conversation_not_found", "conversation not found"
    if isinstance(exc, ConversationCorrupt):
        return 503, "session_unreadable", "conversation unreadable"
    if isinstance(exc, ConversationError):
        return 503, "conversation_unavailable", "conversation unavailable"

    if isinstance(exc, ConversationStoreError):
        if getattr(exc, "scope", "global") == "local":
            return 404, "conversation_not_found", "conversation not found"
        return 503, "conversation_unavailable", "conversation unavailable"
    if isinstance(exc, LLMError):
        return 503, "llm_error", str(exc)
    return 503, "chat_unavailable", "chat unavailable"
