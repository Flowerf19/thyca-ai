"""Wire contract for one chat session: payload shape + public error mapping.

Shared by the layers on both sides of it: :class:`~thyca.app.chat_app.ChatApp`
builds payloads with it, and the HTTP handlers map failures with it. Keeping
it here (rather than in either) is what stops ``serve.py`` from growing a
second copy of the field list and keeps the app layer free of HTTP statuses.

No sockets, no I/O policy — the session rules live in
``thyca/sessions``.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from thyca.config import Config
from thyca.core.protocol import Message, ToolCall
from thyca.skills.skill_event import skill_name_for_call
from . import (
    Session,
    SessionBusy,
    SessionCorrupt,
    SessionError,
    SessionNotFound,
)
from .ask_remember import ask_remember
from .title import display_title


def session_title(session: Session) -> str:
    return display_title(session)


def is_naming_message(message: Message) -> bool:
    """True for transcript-only naming rows (``kind=naming`` meta, any role).

    The one predicate the model feed (assemble), the trace rollup, and the
    chat views share: naming rows are meta-only records that must never
    reach the model or count as turn outcomes.
    """
    return (message.meta or {}).get("kind") == "naming"


def updated_at(session: Session) -> str:
    if session.messages:
        return session.messages[-1].ts
    try:
        stamp = datetime.fromtimestamp(session.path.stat().st_mtime, UTC)
    except FileNotFoundError as exc:
        raise SessionNotFound(session.path) from exc
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def turn_slices(messages: list[Message]) -> list[list[Message]]:
    """Group messages into turns: one slice per user message.

    The one turn-grouping the wire ``turns`` count and the Trace rollup
    share, so the two can never drift: system rows (compaction markers) are
    excluded, rows before the first user message are dropped, and every user
    message opens exactly one slice."""
    slices: list[list[Message]] = []
    cur: list[Message] | None = None
    for item in messages:
        if item.role == "system":
            continue
        if item.role == "user":
            if cur is not None:
                slices.append(cur)
            cur = [item]
        elif cur is not None:
            cur.append(item)
    if cur is not None:
        slices.append(cur)
    return slices


def session_summary(session: Session) -> dict:
    return {
        "id": session.id,
        "title": session_title(session),
        "updated_at": updated_at(session),
        "message_count": len(session.messages),
        # Turns the way Trace counts them: one per user message. Raw
        # message_count would read as inflated by tool traffic.
        "turns": len(turn_slices(session.messages)),
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


def message_dict(
    message: Message, skills_root: Path | None = None, *, include_args: bool = False
) -> dict:
    """Wire payload for one message: role/content/ts plus call/id/meta shape.

    The one message-dict helper for the chat payload and the trace detail.
    ``include_args=True`` pins the trace shape: arguments inline, and
    ``tool_calls``/``tool_call_id``/``meta`` always present (possibly
    empty/None), with reasoning omitted — the ``/api/traces/<id>/<n>``
    contract.
    """
    payload: dict = {
        "role": message.role,
        "content": message.content,
        "ts": message.ts,
    }
    calls = message.tool_calls or []
    if calls or include_args:
        payload["tool_calls"] = [
            tool_call_dict(call, skills_root, include_args=include_args)
            for call in calls
        ]
    if message.tool_call_id is not None or include_args:
        payload["tool_call_id"] = message.tool_call_id
    if message.meta is not None or include_args:
        payload["meta"] = dict(message.meta) if message.meta is not None else None
    if message.reasoning and not include_args:
        payload["reasoning"] = message.reasoning
    return payload


def tool_call_dict(
    call: ToolCall, skills_root: Path | None, *, include_args: bool = False
) -> dict:
    """Wire payload for one call: id, name, and the skill it loaded.

    Replay keeps skill identity so a reloaded transcript can label a skill
    load as a skill instead of a bare ``read``. Arguments stay out of the
    chat payload; the Trace screen is where input JSON belongs, so trace
    callers pass ``include_args=True`` (which also carries parse_error).
    """
    entry: dict = {"id": call.id, "name": call.name}
    if include_args:
        entry["arguments"] = call.arguments
        if call.parse_error:
            entry["parse_error"] = call.parse_error
    skill = skill_name_for_call(call, skills_root)
    if skill is not None:
        entry["skill"] = skill
    return entry


def session_http_error(exc: Exception) -> tuple[int, str] | None:
    """Public ``(status, message)`` for a session failure, or None if unknown.

    The one session→HTTP table: rename, delete, and the read paths all map
    through here (rename adds its own invalid-title branch first).
    """
    if isinstance(exc, SessionNotFound):
        return 404, "session not found"
    if isinstance(exc, SessionBusy):
        return 409, "session busy"
    if isinstance(exc, SessionCorrupt):
        return 503, "session unreadable"
    if isinstance(exc, SessionError):
        return 503, "session unavailable"
    return None


def rename_error(exc: Exception) -> tuple[int, str] | None:
    """Public ``(status, message)`` for a failed rename, or None if unknown."""
    if isinstance(exc, ValueError):
        return 400, "invalid title"
    return session_http_error(exc)


def delete_error(exc: Exception) -> tuple[int, str] | None:
    """Public ``(status, message)`` for a failed delete, or None if unknown."""
    return session_http_error(exc)
