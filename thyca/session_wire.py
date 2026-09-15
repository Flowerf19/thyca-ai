"""Wire contract for one chat session: payload shape + public error mapping.

Shared by the layers on both sides of it: :class:`~thyca.chat_app.ChatApp`
builds payloads with it, and the HTTP handlers map failures with it. Keeping
it here (rather than in either) is what stops ``serve.py`` from growing a
second copy of the field list and keeps the app layer free of HTTP statuses.

No sockets, no I/O policy — the session rules live in
``thyca/sessions``.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from thyca.agent.skill_event import skill_name_for_call
from thyca.config import Config
from thyca.protocol import Message, ToolCall
from thyca.sessions import (
    Session,
    SessionBusy,
    SessionCorrupt,
    SessionError,
    SessionNotFound,
)
from thyca.sessions.ask_remember import ask_remember
from thyca.sessions.title import display_title


def session_title(session: Session) -> str:
    return display_title(session)


def updated_at(session: Session) -> str:
    if session.messages:
        return session.messages[-1].ts
    stamp = datetime.fromtimestamp(session.path.stat().st_mtime, UTC)
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def session_summary(session: Session) -> dict:
    return {
        "id": session.id,
        "title": session_title(session),
        "updated_at": updated_at(session),
        "message_count": len(session.messages),
        # Turns the way Trace counts them: one per user message. Raw
        # message_count would read as inflated by tool traffic.
        "turns": sum(1 for item in session.messages if item.role == "user"),
    }


def session_detail(
    session: Session,
    cfg: Config,
    *,
    skills_root: Path | None = None,
    running: bool | None = None,
    started_at: str | None = None,
) -> dict:
    """Wire payload for one session.

    ``running`` is passed in when the caller already knows the answer (the turn
    itself); otherwise the caller supplies ``started_at`` for the session it is
    asking about, or leaves both unset for "not running".
    """
    detail = {
        "id": session.id,
        "title": session_title(session),
        "model": cfg.provider.model,
        "messages": [message_dict(item, skills_root) for item in session.messages],
        "ask_remember": ask_remember(session.messages, datetime.now(UTC)),
    }
    # A turn in flight is invisible on disk until it finishes writing, so
    # the client is told here: it can wait instead of guessing.
    resolved = running if running is not None else started_at is not None
    detail["running"] = resolved
    if started_at is not None:
        detail["started_at"] = started_at
    return detail


def message_dict(message: Message, skills_root: Path | None = None) -> dict:
    payload: dict = {
        "role": message.role,
        "content": message.content,
        "ts": message.ts,
    }
    if message.tool_calls:
        payload["tool_calls"] = [
            tool_call_dict(call, skills_root) for call in message.tool_calls
        ]
    if message.tool_call_id is not None:
        payload["tool_call_id"] = message.tool_call_id
    if message.meta is not None:
        payload["meta"] = dict(message.meta)
    return payload


def tool_call_dict(call: ToolCall, skills_root: Path | None) -> dict:
    """Wire payload for one call: id, name, and the skill it loaded.

    Replay keeps skill identity so a reloaded transcript can label a skill
    load as a skill instead of a bare ``read``. Arguments stay out of this
    payload; the Trace screen is where input JSON belongs.
    """
    entry = {"id": call.id, "name": call.name}
    skill = skill_name_for_call(call, skills_root)
    if skill is not None:
        entry["skill"] = skill
    return entry


def rename_error(exc: Exception) -> tuple[int, str] | None:
    """Public ``(status, message)`` for a failed rename, or None if unknown."""
    if isinstance(exc, SessionNotFound):
        return 404, "session not found"
    if isinstance(exc, ValueError):
        return 400, "invalid title"
    if isinstance(exc, SessionCorrupt):
        return 503, "session unreadable"
    if isinstance(exc, SessionError):
        return 503, "session unavailable"
    return None


def delete_error(exc: Exception) -> tuple[int, str] | None:
    """Public ``(status, message)`` for a failed delete, or None if unknown."""
    if isinstance(exc, SessionNotFound):
        return 404, "session not found"
    if isinstance(exc, SessionBusy):
        return 409, "session busy"
    if isinstance(exc, SessionError):
        return 503, "session unavailable"
    return None
