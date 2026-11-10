"""Wire contract for one chat conversation: payload shape + public error mapping.

Shared by the layers on both sides of it: :class:`~thyca.app.chat_app.ChatApp`
builds payloads with it, and the HTTP handlers map failures with it. Keeping
it here (rather than in either) is what stops ``serve.py`` from growing a
second copy of the field list and keeps the app layer free of HTTP statuses.

No sockets, no I/O policy — the conversation rules live in
``thyca/conversations/runtime``.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from thyca.configs import Config
from thyca.core.protocol import Message, ToolCall
from thyca.skills.skill_event import skill_name_for_call
from . import (
    ConversationRuntime,
    ConversationBusy,
    ConversationCorrupt,
    ConversationError,
    ConversationNotFound,
)
from .ask_remember import ask_remember
from .compaction import calibration_factor, context_usage


_SIDECAR_KINDS = frozenset({"compaction"})


def is_sidecar_message(message: Message) -> bool:
    """True for meta-only sidecar rows (compaction cost records).

    Every surface that must keep a sidecar out of the model feed or out
    of turn outcomes uses this, so a compaction summary call is recorded
    for Trace/cost without ever being sent back to
    a provider (strict providers reject its null content) or counted as
    a reply.
    """
    return (message.meta or {}).get("kind") in _SIDECAR_KINDS


# Resolve the listing stamp for one conversation without inventing a file.
def updated_at(conversation: ConversationRuntime) -> str:
    if conversation.messages:
        return conversation.messages[-1].ts
    # Store-backed doors have no transcript file: an empty door reports
    # its stored creation stamp, exactly like the read adapter listing.
    # An unknown door is not found; no file stamp is ever consulted.
    try:
        from thyca.conversations.store.runtime import is_runtime_ready

        root = conversation.path.parent.parent
        if is_runtime_ready(root):
            from thyca.conversations.store.admin import AdminStore

            items, _more = AdminStore(root).list(limit=5000)
            for row in items:
                if row.ref.id == conversation.conversation_id:
                    return row.created_at
    except Exception:
        pass
    raise ConversationNotFound(conversation.path)


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


def conversation_summary(conversation: ConversationRuntime) -> dict:
    return {
        "conversation_id": conversation.conversation_id,
        "updated_at": updated_at(conversation),
        "message_count": len(conversation.messages),
        # Turns the way Trace counts them: one per user message. Raw
        # message_count would read as inflated by tool traffic.
        "turns": len(turn_slices(conversation.messages)),
        # Team room participants (BE-3, contract §6): legacy conversations
        # without a bots meta line read as Thyca alone.
        "bots": list(conversation.bots) if conversation.bots else ["thyca"],
        # Bot main window (CBE-5): bot id whose fixed DM this is, else null.
        "dm": conversation.dm_bot,
        # User-facing channel model (phase 2B): exactly dm or group.
        "kind": "dm" if conversation.dm_bot else "group",
        "channel": "webui",
        "window_id": conversation.conversation_id,
    }


def conversation_detail(
    conversation: ConversationRuntime,
    cfg: Config,
    *,
    skills_root: Path | None = None,
    running: bool | None = None,
    started_at: str | None = None,
    context: dict | None = None,
) -> dict:
    """Wire payload for one conversation.

    ``running`` is passed in when the caller already knows the answer (the turn
    itself); otherwise the caller supplies ``started_at`` for the conversation it is
    asking about, or leaves both unset for "not running".

    ``context`` is the caller's overhead-aware usage snapshot (hot prompt +
    tools schema measured where they live). Without it this falls back to a
    transcript-only lower bound so pure wire callers stay I/O-free.
    """
    if context is None:
        context = context_usage(
            conversation.messages,
            cfg.effective_limits().contextTokens,
            calibration=calibration_factor(conversation.messages, model=cfg.provider.model),
        )
    detail = {
        "conversation_id": conversation.conversation_id,
        "model": cfg.provider.model,
        "bots": list(conversation.bots) if conversation.bots else ["thyca"],
        "dm": conversation.dm_bot,
        "kind": "dm" if conversation.dm_bot else "group",
        "channel": "webui",
        "window_id": conversation.conversation_id,
        "messages": [message_dict(item, skills_root) for item in conversation.messages],
        "ask_remember": ask_remember(conversation.messages, datetime.now(UTC)),
        "context": context,
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
    empty/None), with reasoning included only on Trace detail.
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
    if include_args and message.reasoning:
        payload["reasoning"] = message.reasoning
    if include_args and message.reasoning_details:
        payload["reasoning_details"] = message.reasoning_details
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


def conversation_http_error(exc: Exception) -> tuple[int, str] | None:
    """Public ``(status, message)`` for a conversation failure, or None if unknown.

    The one conversation→HTTP table: delete and the read paths all map
    through here.
    """
    if isinstance(exc, ConversationNotFound):
        return 404, "conversation not found"
    if isinstance(exc, ConversationBusy):
        return 409, "conversation busy"
    if isinstance(exc, ConversationCorrupt):
        return 503, "conversation unreadable"
    if isinstance(exc, ConversationError):
        return 503, "conversation unavailable"
    return None


def delete_error(exc: Exception) -> tuple[int, str] | None:
    """Public ``(status, message)`` for a failed delete, or None if unknown."""
    return conversation_http_error(exc)
