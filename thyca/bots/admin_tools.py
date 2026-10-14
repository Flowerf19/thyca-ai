"""Thyca-only bot administration tools (BE-5).

``bot_create | bot_pause | bot_delete | bot_restore | bot_edit_card |
bot_delegate | bot_stop`` are registered on the shared tool registry, but
the bot runtime filters every ``bot_*`` name out of child-bot snapshots
(master §5: only Thyca's registry carries them), and ``hidden_tools``
enforcement backs that at the gateway. Each handler re-checks that the
in-flight caller is Thyca (or the user: no bot bound), so a leaked spec
can never be used by a child bot.
"""
from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from thyca.bots.bus import new_span_id, new_trace_id
from thyca.bots.card import (
    AVATAR_VARIANTS,
    BotCard,
    BotMemoryPolicy,
    BotModel,
    with_baseline_tools,
)
from thyca.bots.context import current_bot_turn_context
from thyca.memory.identity import current_bot_id
from thyca.tools.registry import ToolRegistry, ToolSpec

ADMIN_TOOL_NAMES = (
    "bot_create",
    "bot_pause",
    "bot_delete",
    "bot_restore",
    "bot_edit_card",
    "bot_delegate",
    "bot_stop",
)


class AdminDeps:
    """Everything admin handlers need (wired once by ChatApp)."""

    def __init__(self, bots, runtime, facade_fn, dm_post=None, card_post=None) -> None:
        self.bots = bots
        self.runtime = runtime
        self.facade_fn = facade_fn
        #: ``(bot_id, text, current_room_id) -> dict`` landing a message
        #: in the bot's own DM (ChatApp owns DM resolution + room writes).
        self.dm_post = dm_post
        #: ``(bot_id, card, current_room_id, content) -> dict`` landing a
        #: structured question/error card in the current room (ChatApp
        #: owns room membership/DM-privacy gates + transcript writes).
        self.card_post = card_post


def _caller() -> str | None:
    return current_bot_id()


def _deny_child() -> str | None:
    caller = _caller()
    if caller is not None and caller != "thyca":
        return f"denied: bot administration is Thyca-only (caller: {caller})"
    return None


def register_admin_tools(registry: ToolRegistry, deps: AdminDeps) -> None:
    for spec in (
        _create_spec(deps),
        _pause_spec(deps),
        _delete_spec(deps),
        _restore_spec(deps),
        _edit_card_spec(deps),
        _delegate_spec(deps),
        _stop_spec(deps),
        _send_spec(deps),
        _fetch_spec(deps),
        _task_open_spec(deps),
        _message_user_spec(deps),
        _ask_user_spec(deps),
        _report_error_spec(deps),
    ):
        if registry.get(spec.name) is None:
            registry.register(spec)


async def _send_and_deliver(
    deps: AdminDeps,
    *,
    sender: str,
    recipient: str,
    kind: str,
    payload: str,
    room: str = "",
    trace_id: str | None = None,
    refs: tuple[str, ...] = (),
) -> str:
    """Enqueue one envelope and, in a live turn, run the recipient now.

    Delivery is the production path: the recipient bot's own AgentLoop
    turn runs (bounded by the bus per-trace hop limit applied at send),
    its attributed result lands in the room transcript via ChatApp's
    deliverer, and its reply is returned here for the sender's flow.
    Outside a live turn (no deliverer/context) the envelope stays queued
    for the recipient's next turn, reported honestly as ``queued``."""
    ctx = current_bot_turn_context()
    session_id = room or (ctx.room_id if ctx is not None else "")
    envelope = deps.runtime.send(
        sender=sender,
        recipient=recipient,
        kind=kind,
        session_id=session_id,
        payload=payload,
        trace_id=trace_id or (ctx.trace_id if ctx is not None else None),
        parent_span_id=ctx.span_id if ctx is not None else None,
        refs=refs,
    )
    reply = await deps.runtime.deliver(envelope)
    out: dict = {
        "ok": True,
        "to": recipient,
        "trace_id": envelope.trace_id,
        "span_id": envelope.span_id,
    }
    if reply is not None:
        out["delivered"] = True
        out["reply"] = reply
    else:
        out["queued"] = deps.runtime._bus.pending(recipient)
    return json.dumps(out, ensure_ascii=False)


def _send_spec(deps: AdminDeps) -> ToolSpec:
    async def handler(args: dict) -> str:
        # Sender identity comes only from the in-flight turn context /
        # bot ContextVar — never from arguments (there is no sender arg).
        ctx = current_bot_turn_context()
        sender = ctx.sender_id if ctx is not None else current_bot_id()
        if sender is None:
            return "denied: bot_send requires an in-flight bot turn"
        recipient = args.get("bot_id")
        message = args.get("message")
        if not isinstance(recipient, str) or not recipient.strip():
            return "missing argument: bot_id"
        if not isinstance(message, str) or not message.strip():
            return "missing argument: message"
        kind = args.get("kind") if isinstance(args.get("kind"), str) else "message"
        raw_refs = args.get("refs")
        refs = (
            tuple(ref for ref in raw_refs if isinstance(ref, str) and ref.strip())
            if isinstance(raw_refs, list)
            else ()
        )
        try:
            card = deps.bots.require(recipient)
            if card.paused:
                return f"bot_send failed: bot is paused: {recipient}"
            return await _send_and_deliver(
                deps, sender=sender, recipient=recipient, kind=kind,
                payload=message.strip(), refs=refs,
            )
        except Exception as exc:
            return f"bot_send failed: {exc}"

    return ToolSpec(
        name="bot_send",
        description=(
            "Send a message/task to another bot and get its reply. The "
            "recipient bot runs its own turn immediately; its reply is "
            "returned to you (in a shared room it is also recorded under "
            "its name; in a private DM it is not shown there — summarize "
            "it yourself). "
            "Your identity as sender is taken from your turn, never from "
            "arguments. kind: delegate|message|help_request (default message). "
            "Large content should travel by reference: pass refs (file paths, "
            "memory entry ids, span ids) and keep message short — bodies over "
            "1000 chars are excerpted in the recipient's prompt, with the "
            "full text fetchable via bot_fetch."
        ),
        parameters={
            "type": "object",
            "properties": {
                "bot_id": {"type": "string"},
                "message": {"type": "string"},
                "kind": {"type": "string"},
                "refs": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["bot_id", "message"],
            "additionalProperties": False,
        },
        handler=handler,
        # parallel_safe: no gateway resource lock is held across the
        # nested recipient turn, so multi-hop bot->bot cannot deadlock
        # on the send tool's own lock.
        parallel_safe=True,
    )


def _fetch_spec(deps: AdminDeps) -> ToolSpec:
    async def handler(args: dict) -> str:
        # Authorization (context review): the caller — from the in-flight
        # turn context / bot ContextVar, never from arguments — must be
        # the envelope's sender or recipient (recorded at Bus.send).
        # Anyone else gets an honest denial with no content, exactly as
        # if they had guessed a span id belonging to another pair.
        ctx = current_bot_turn_context()
        caller = ctx.sender_id if ctx is not None else current_bot_id()
        if caller is None:
            return "denied: bot_fetch requires an in-flight bot turn"
        span_id = args.get("span_id")
        if not isinstance(span_id, str) or not span_id.strip():
            return "missing argument: span_id"
        span_id = span_id.strip()
        parties = deps.runtime.artifact_parties(span_id)
        if parties is None:
            return json.dumps(
                {"ok": False, "span_id": span_id,
                 "error": "artifact not found (unknown or evicted)"},
                ensure_ascii=False,
            )
        if caller not in parties:
            return json.dumps(
                {"ok": False, "span_id": span_id,
                 "error": "denied: artifact was not sent by or to this bot"},
                ensure_ascii=False,
            )
        body = deps.runtime.fetch_artifact(span_id, caller=caller)
        if body is None:  # evicted between the two lookups
            return json.dumps(
                {"ok": False, "span_id": span_id,
                 "error": "artifact not found (unknown or evicted)"},
                ensure_ascii=False,
            )
        return json.dumps(
            {"ok": True, "span_id": span_id, "content": body},
            ensure_ascii=False,
        )

    return ToolSpec(
        name="bot_fetch",
        description=(
            "Fetch the full body of an A2A envelope by span_id. Envelope "
            "bodies in your prompt are excerpts (max 1000 chars); when an "
            "envelope is marked truncated, call this with its span_id to "
            "read the complete text. Content sent as refs (file paths / "
            "memory ids) should be read with read/memory_get instead."
        ),
        parameters={
            "type": "object",
            "properties": {"span_id": {"type": "string"}},
            "required": ["span_id"],
            "additionalProperties": False,
        },
        handler=handler,
        parallel_safe=True,
    )


def _message_user_spec(deps: AdminDeps) -> ToolSpec:
    async def handler(args: dict) -> str:
        # Identity from the in-flight turn only: a bot can post solely
        # into its own DM, under its own name (no bot_id argument).
        ctx = current_bot_turn_context()
        sender = ctx.sender_id if ctx is not None else current_bot_id()
        if sender is None:
            return "denied: message_user requires an in-flight bot turn"
        message = args.get("message")
        if not isinstance(message, str) or not message.strip():
            return "missing argument: message"
        if deps.dm_post is None:
            return "message_user failed: DM posting is unavailable here"
        try:
            result = deps.dm_post(
                sender, message.strip(),
                ctx.room_id if ctx is not None else "",
            )
        except Exception as exc:
            return f"message_user failed: {exc}"
        return json.dumps(result, ensure_ascii=False)

    return ToolSpec(
        name="message_user",
        description=(
            "Post a message to the user in YOUR OWN private DM window, "
            "under your name. Use this when you are asked to message the "
            "user privately/directly, or when something should be "
            "discussed with the user privately instead of in the current "
            "room. The message does not appear in the current room. If "
            "you are already talking in your own DM with the user, just "
            "reply normally instead."
        ),
        parameters={
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
            "additionalProperties": False,
        },
        handler=handler,
        parallel_safe=True,
    )


def _clean_card_choices(raw) -> list[str] | None:
    """2–5 short, unique chip labels (FE contract for question cards).

    Accepts plain strings or {id,label} objects; only the visible label
    is kept because a chip click returns the label as the user's next
    message."""
    if not isinstance(raw, list):
        return None
    labels: list[str] = []
    for item in raw:
        label = item if isinstance(item, str) else (
            item.get("label") or item.get("id") if isinstance(item, dict) else None)
        if not isinstance(label, str):
            return None
        label = label.strip()
        if not label or len(label) > 120 or label in labels:
            return None
        labels.append(label)
    return labels if 2 <= len(labels) <= 5 else None


def _clean_card_progress(raw) -> list[dict] | None:
    """Progress steps for an error card: label + done|stuck|todo."""
    if not isinstance(raw, list) or not raw or len(raw) > 10:
        return None
    steps: list[dict] = []
    for item in raw:
        if isinstance(item, str):
            label, state = item.strip(), "todo"
        elif isinstance(item, dict):
            label = item.get("label")
            state = item.get("state", "todo")
            if not isinstance(label, str) or state not in ("done", "stuck", "todo"):
                return None
            label = label.strip()
        else:
            return None
        if not label or len(label) > 200:
            return None
        steps.append({"label": label, "state": state})
    return steps


def _sender_and_room() -> tuple[str | None, str]:
    ctx = current_bot_turn_context()
    sender = ctx.sender_id if ctx is not None else current_bot_id()
    return sender, (ctx.room_id if ctx is not None else "")


def _ask_user_spec(deps: AdminDeps) -> ToolSpec:
    async def handler(args: dict) -> str:
        sender, room_id = _sender_and_room()
        if sender is None:
            return "denied: ask_user requires an in-flight bot turn"
        title = args.get("title")
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 200:
            return "missing argument: title"
        subtitle = args.get("subtitle")
        if subtitle is not None and (not isinstance(subtitle, str) or len(subtitle.strip()) > 500):
            return "invalid argument: subtitle"
        options = _clean_card_choices(args.get("options"))
        if options is None:
            return "invalid argument: options (need 2-5 short unique labels)"
        if deps.card_post is None:
            return "ask_user failed: card posting is unavailable here"
        card = {"type": "question", "title": title.strip(), "options": options}
        if isinstance(subtitle, str) and subtitle.strip():
            card["subtitle"] = subtitle.strip()
        try:
            result = deps.card_post(sender, card, room_id, "")
        except Exception as exc:
            return f"ask_user failed: {exc}"
        if isinstance(result, dict) and result.get("ok"):
            result = {**result, "waiting_for_user": True}
        return json.dumps(result, ensure_ascii=False)

    return ToolSpec(
        name="ask_user",
        description=(
            "Ask the user ONE multiple-choice question as a clickable "
            "card in the current room, then wait for their answer (the "
            "clicked label arrives as the next user message). Use when a "
            "decision is needed before you can continue — for example "
            "bot-creation choices or command permission (options like "
            "\"Hỏi trước\", \"Cho phép\", \"Không\"). Give one clear "
            "title and 2-5 short options. After this call, end your turn "
            "with at most one brief line; do not repeat the options as "
            "plain text. For free-form questions, just ask normally."
        ),
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "subtitle": {"type": "string"},
                "options": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["title", "options"],
            "additionalProperties": False,
        },
        handler=handler,
        parallel_safe=True,
    )


def _report_error_spec(deps: AdminDeps) -> ToolSpec:
    async def handler(args: dict) -> str:
        sender, room_id = _sender_and_room()
        if sender is None:
            return "denied: report_error requires an in-flight bot turn"
        summary = args.get("summary")
        if summary is not None and (not isinstance(summary, str) or len(summary.strip()) > 2000):
            return "invalid argument: summary"
        hint = args.get("hint")
        if hint is not None and (not isinstance(hint, str) or len(hint.strip()) > 500):
            return "invalid argument: hint"
        progress = _clean_card_progress(args.get("progress"))
        if progress is None:
            return "invalid argument: progress (need steps with label + done|stuck|todo)"
        choices = _clean_card_choices(args.get("choices"))
        if choices is None:
            return "invalid argument: choices (need 2-5 short unique labels)"
        if deps.card_post is None:
            return "report_error failed: card posting is unavailable here"
        card = {"type": "error", "progress": progress, "choices": choices}
        if isinstance(hint, str) and hint.strip():
            card["hint"] = hint.strip()
        try:
            result = deps.card_post(
                sender, card, room_id,
                summary.strip() if isinstance(summary, str) else "",
            )
        except Exception as exc:
            return f"report_error failed: {exc}"
        if isinstance(result, dict) and result.get("ok"):
            result = {**result, "waiting_for_user": True}
        return json.dumps(result, ensure_ascii=False)

    return ToolSpec(
        name="report_error",
        description=(
            "Report that you are blocked mid-task as a structured error "
            "card in the current room: an honest progress checklist "
            "(mark finished steps done, the blocked step stuck, the rest "
            "todo) plus 2-5 concrete choices for how to continue (for "
            "example \"Thử lại, chờ lâu hơn\", \"Giao bot khác\", "
            "\"Bỏ qua bước này\", \"Để Thyca tự quyết\"). The user's "
            "clicked choice arrives as the next user message. Use only "
            "when you genuinely cannot proceed without the user's "
            "decision — recoverable hiccups should just be retried or "
            "mentioned in your reply."
        ),
        parameters={
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "progress": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "state": {"type": "string", "enum": ["done", "stuck", "todo"]},
                        },
                        "required": ["label", "state"],
                        "additionalProperties": False,
                    },
                },
                "choices": {"type": "array", "items": {"type": "string"}},
                "hint": {"type": "string"},
            },
            "required": ["progress", "choices"],
            "additionalProperties": False,
        },
        handler=handler,
        parallel_safe=True,
    )


def _task_open_spec(deps: AdminDeps) -> ToolSpec:
    async def handler(args: dict) -> str:
        ctx = current_bot_turn_context()
        sender = ctx.sender_id if ctx is not None else current_bot_id()
        if sender is None:
            return "denied: task_open requires an in-flight bot turn"
        title = args.get("title") or args.get("task")
        if not isinstance(title, str) or not title.strip():
            return "missing argument: title"
        # The event is this tool call itself, recorded in the bot's
        # internal transcript: the task view (thyca.bots.tasks) derives
        # the open task from it. No mutable task store is written. The
        # returned key (a fresh span id, echoed as ``span_id``/``key``/
        # ``task_key``) links the task: the ack result in the transcript
        # carries it, and a later result/error event whose
        # ``parent_span_id`` is this key closes the task (a status/step
        # event with the same parent updates its status instead).
        key = new_span_id()
        return json.dumps(
            {"ok": True, "task": title.strip(), "status": "open",
             "key": key, "span_id": key, "task_key": key,
             "bot_id": sender,
             "room": ctx.room_id if ctx is not None else ""},
            ensure_ascii=False,
        )

    return ToolSpec(
        name="task_open",
        description=(
            "Open a task that outlives this turn (work born in plain chat, "
            "not a delegation). Records a task_open event in your own "
            "transcript; your open-task view in later prompts lists it "
            "until a result/error event linked to the returned key "
            "(span_id) closes it. Args: title, optional room."
        ),
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "room": {"type": "string"},
            },
            "required": ["title"],
            "additionalProperties": False,
        },
        handler=handler,
        parallel_safe=True,
    )


def _str_list_arg(args: dict, key: str) -> tuple[str, ...]:
    value = args.get(key)
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or any(not isinstance(v, str) for v in value):
        raise ValueError(f"{key} must be a list of strings")
    return tuple(value)


def _memory_arg(args: dict) -> BotMemoryPolicy:
    value = args.get("memory")
    if value is None:
        return BotMemoryPolicy()
    if not isinstance(value, dict):
        raise ValueError("memory must be an object {read, write, propose_team}")
    fields = {}
    for key in ("read", "write", "propose_team"):
        field_value = value.get(key, True)
        if not isinstance(field_value, bool):
            raise ValueError("memory must be an object {read, write, propose_team}")
        fields[key] = field_value
    return BotMemoryPolicy(**fields)


def _create_spec(deps: AdminDeps) -> ToolSpec:
    async def handler(args: dict) -> str:
        denied = _deny_child()
        if denied:
            return denied
        try:
            explicit_tools = _str_list_arg(args, "tools")
            hidden_tools = _str_list_arg(args, "hidden_tools")
            skills = _str_list_arg(args, "skills")
            memory = _memory_arg(args)
        except ValueError as exc:
            return f"bot_create failed: {exc}"
        schedule = args.get("schedule")
        if schedule is not None and not isinstance(schedule, str):
            return "bot_create failed: schedule must be a string"
        avatar = args.get("avatar")
        if avatar == "star":
            return "bot_create failed: avatar 'star' is reserved for Thyca"
        if avatar is not None and avatar not in AVATAR_VARIANTS:
            return (
                "bot_create failed: invalid avatar (want one of "
                + ", ".join(AVATAR_VARIANTS) + ")"
            )
        try:
            card = BotCard(
                bot_id=args["bot_id"],
                name=args["bot_id"],
                role=args.get("role", "Trợ lý"),
                description=args.get("description", ""),
                # No explicit tint → least-used team tint, so twins are
                # distinguishable by color instead of all rendering accent.
                tint=args.get("tint") or deps.bots.suggest_tint(),
                # No explicit avatar → least-used note shape, persisted
                # on the card like the suggested tint.
                avatar=avatar or deps.bots.suggest_avatar(),
                model=BotModel(
                    id=args.get("model", "default"),
                    thinking=args.get("thinking") if isinstance(args.get("thinking"), str) else None,
                ),
                skills=skills,
                # Every created bot can immediately talk to peers and use
                # its memory; explicit tools union on top. hidden_tools,
                # bash and memory policies still win at filter time.
                tools=with_baseline_tools(explicit_tools),
                hidden_tools=hidden_tools,
                bash=args.get("bash", "ask"),
                memory=memory,
                schedule=schedule or None,
                persona=args.get("persona", ""),
            )
        except KeyError as exc:
            return f"missing argument: {exc}"
        if not card.description:
            return "missing argument: description"
        if card.bash not in ("ask", "allow", "deny"):
            return "invalid bash (ask|allow|deny)"
        try:
            created = deps.bots.create(card)
        except Exception as exc:
            return f"bot_create failed: {exc}"
        return json.dumps(
            {"ok": True, "bot_id": created.bot_id, "tools": list(created.tools)},
            ensure_ascii=False,
        )

    return ToolSpec(
        name="bot_create",
        description=(
            "Create a new team bot (Thyca only). Needs bot_id (a-z, 0-9, hyphens), "
            "role, description; optional model ('default' keeps Settings), "
            "thinking, tint (theme token), avatar (star|hollow|single-flag|"
            "double-flag|dotted|fermata|plain), bash (ask|allow|deny), skills, tools, "
            "hidden_tools, memory {read,write,propose_team}, schedule, persona. "
            "Every created bot also receives the baseline tools (bot_send, "
            "message_user, ask_user, report_error, memory_remember, "
            "memory_search, memory_get, "
            "memory_propose_team) "
            "unioned with `tools`; hidden_tools still blocks them."
        ),
        parameters={
            "type": "object",
            "properties": {
                "bot_id": {"type": "string"},
                "role": {"type": "string"},
                "description": {"type": "string"},
                "model": {"type": "string"},
                "thinking": {"type": "string"},
                "tint": {"type": "string"},
                "avatar": {"type": "string"},
                "bash": {"type": "string"},
                "skills": {"type": "array", "items": {"type": "string"}},
                "tools": {"type": "array", "items": {"type": "string"}},
                "hidden_tools": {"type": "array", "items": {"type": "string"}},
                "memory": {"type": "object"},
                "schedule": {"type": "string"},
                "persona": {"type": "string"},
            },
            "required": ["bot_id"],
            "additionalProperties": False,
        },
        handler=handler,
        parallel_safe=False,
    )


def _pause_spec(deps: AdminDeps) -> ToolSpec:
    async def handler(args: dict) -> str:
        denied = _deny_child()
        if denied:
            return denied
        try:
            deps.bots.set_paused(args["bot_id"], True)
        except Exception as exc:
            return f"bot_pause failed: {exc}"
        return json.dumps({"ok": True, "bot_id": args["bot_id"], "status": "paused"})

    return ToolSpec(
        name="bot_pause",
        description="Pause a bot (Thyca only). Paused bots leave rooms' rosters readable but never answer.",
        parameters={
            "type": "object",
            "properties": {"bot_id": {"type": "string"}},
            "required": ["bot_id"],
            "additionalProperties": False,
        },
        handler=handler,
        parallel_safe=False,
    )


def _restore_spec(deps: AdminDeps) -> ToolSpec:
    async def handler(args: dict) -> str:
        denied = _deny_child()
        if denied:
            return denied
        try:
            deps.bots.set_paused(args["bot_id"], False)
        except Exception as exc:
            return f"bot_restore failed: {exc}"
        return json.dumps({"ok": True, "bot_id": args["bot_id"], "status": "active"})

    return ToolSpec(
        name="bot_restore",
        description="Restore a paused bot (Thyca only).",
        parameters={
            "type": "object",
            "properties": {"bot_id": {"type": "string"}},
            "required": ["bot_id"],
            "additionalProperties": False,
        },
        handler=handler,
        parallel_safe=False,
    )


def _delete_spec(deps: AdminDeps) -> ToolSpec:
    async def handler(args: dict) -> str:
        denied = _deny_child()
        if denied:
            return denied
        bot_id = args["bot_id"]
        try:
            removed = deps.facade_fn().forget_bot_entries(bot_id)
            deps.bots.delete(bot_id)
        except Exception as exc:
            return f"bot_delete failed: {exc}"
        return json.dumps(
            {"ok": True, "bot_id": bot_id, "private_memories_removed": removed},
            ensure_ascii=False,
        )

    return ToolSpec(
        name="bot_delete",
        description=(
            "Delete a bot (Thyca only): its card, private skills/sessions, and "
            "private memories go; team-promoted memories stay with the team."
        ),
        parameters={
            "type": "object",
            "properties": {"bot_id": {"type": "string"}},
            "required": ["bot_id"],
            "additionalProperties": False,
        },
        handler=handler,
        parallel_safe=False,
    )


_EDITABLE_FIELDS = (
    "role", "description", "tint", "avatar", "model", "thinking",
    "skills", "tools", "hidden_tools", "bash", "schedule", "persona",
)


def apply_card_edit(card: BotCard, patch: dict) -> BotCard:
    """Pure card patch used by the tool and the create flow."""
    unknown = [key for key in patch if key not in _EDITABLE_FIELDS]
    if unknown:
        raise ValueError(f"unknown card field: {unknown[0]}")
    updated = card
    if "role" in patch:
        updated = replace(updated, role=str(patch["role"]))
    if "description" in patch:
        updated = replace(updated, description=str(patch["description"]))
    if "tint" in patch:
        updated = replace(updated, tint=str(patch["tint"]))
    if "avatar" in patch:
        if patch["avatar"] == "star" and not card.system:
            raise ValueError("avatar 'star' is reserved for Thyca")
        if patch["avatar"] not in AVATAR_VARIANTS:
            raise ValueError(
                "invalid avatar (want one of " + ", ".join(AVATAR_VARIANTS) + ")"
            )
        updated = replace(updated, avatar=str(patch["avatar"]))
    if "model" in patch or "thinking" in patch:
        model_id = str(patch.get("model", card.model.id if card.model else "default"))
        thinking = patch.get("thinking", card.model.thinking if card.model else None)
        updated = replace(updated, model=BotModel(id=model_id, thinking=thinking))
    for key in ("skills", "tools", "hidden_tools"):
        if key in patch:
            value = patch[key]
            if not isinstance(value, list):
                raise ValueError(f"{key} must be a list of strings")
            updated = replace(updated, **{key: tuple(value)})
    if "bash" in patch:
        if patch["bash"] not in ("ask", "allow", "deny"):
            raise ValueError("invalid bash (ask|allow|deny)")
        updated = replace(updated, bash=patch["bash"])
    if "schedule" in patch:
        schedule = patch["schedule"]
        updated = replace(updated, schedule=schedule if schedule else None)
    if "persona" in patch:
        updated = replace(updated, persona=str(patch["persona"]))
    return updated


def _edit_card_spec(deps: AdminDeps) -> ToolSpec:
    async def handler(args: dict) -> str:
        denied = _deny_child()
        if denied:
            return denied
        bot_id = args["bot_id"]
        patch = {k: v for k, v in args.items() if k != "bot_id" and v is not None}
        try:
            card = deps.bots.require(bot_id)
            updated = apply_card_edit(card, patch)
            deps.bots.update_card(bot_id, updated)
        except Exception as exc:
            return f"bot_edit_card failed: {exc}"
        return json.dumps({"ok": True, "bot_id": bot_id}, ensure_ascii=False)

    return ToolSpec(
        name="bot_edit_card",
        description=(
            "Edit a bot's Agent Card (Thyca only; bots cannot self-edit). "
            "Patch any of: role, description, tint, avatar, model, thinking, skills, "
            "tools, hidden_tools, bash, schedule, persona."
        ),
        parameters={
            "type": "object",
            "properties": {
                "bot_id": {"type": "string"},
                "role": {"type": "string"},
                "description": {"type": "string"},
                "tint": {"type": "string"},
                "avatar": {"type": "string"},
                "model": {"type": "string"},
                "thinking": {"type": "string"},
                "skills": {"type": "array"},
                "tools": {"type": "array"},
                "hidden_tools": {"type": "array"},
                "bash": {"type": "string"},
                "schedule": {"type": "string"},
                "persona": {"type": "string"},
            },
            "required": ["bot_id"],
            "additionalProperties": False,
        },
        handler=handler,
        parallel_safe=False,
    )


def _delegate_spec(deps: AdminDeps) -> ToolSpec:
    async def handler(args: dict) -> str:
        denied = _deny_child()
        if denied:
            return denied
        bot_id = args["bot_id"]
        task = args.get("task", "")
        if not isinstance(task, str) or not task.strip():
            return "missing argument: task"
        try:
            card = deps.bots.require(bot_id)
            if card.paused:
                return f"bot_delegate failed: bot is paused: {bot_id}"
        except Exception as exc:
            return f"bot_delegate failed: {exc}"
        room = args.get("room") if isinstance(args.get("room"), str) else ""
        ctx = current_bot_turn_context()
        sender = ctx.sender_id if ctx is not None else "thyca"
        try:
            return await _send_and_deliver(
                deps, sender=sender, recipient=bot_id, kind="delegate",
                payload=task.strip(), room=room,
                trace_id=args.get("trace_id") if isinstance(args.get("trace_id"), str) else None,
            )
        except Exception as exc:
            return f"bot_delegate failed: {exc}"

    return ToolSpec(
        name="bot_delegate",
        description=(
            "Give a task to a bot (Thyca only). The recipient bot runs its "
            "own turn immediately and its reply is returned to you and "
            "recorded in the room under its name; returns the trace_id too."
        ),
        parameters={
            "type": "object",
            "properties": {
                "bot_id": {"type": "string"},
                "task": {"type": "string"},
                "room": {"type": "string"},
                "trace_id": {"type": "string"},
            },
            "required": ["bot_id", "task"],
            "additionalProperties": False,
        },
        handler=handler,
        # No gateway lock held across the nested recipient turn.
        parallel_safe=True,
    )


def _stop_spec(deps: AdminDeps) -> ToolSpec:
    async def handler(args: dict) -> str:
        denied = _deny_child()
        if denied:
            return denied
        bot_id = args["bot_id"]
        try:
            deps.bots.require(bot_id)
        except Exception as exc:
            return f"bot_stop failed: {exc}"
        pending = deps.runtime._bus.drain(bot_id)
        return json.dumps(
            {"ok": True, "bot_id": bot_id, "queued_dropped": len(pending)},
            ensure_ascii=False,
        )

    return ToolSpec(
        name="bot_stop",
        description=(
            "Stop a bot's queued work (Thyca only): drops its pending A2A "
            "mailbox. (Turns run synchronously in v1; in-flight room turns "
            "are cancelled via the session cancel endpoint.)"
        ),
        parameters={
            "type": "object",
            "properties": {"bot_id": {"type": "string"}},
            "required": ["bot_id"],
            "additionalProperties": False,
        },
        handler=handler,
        parallel_safe=False,
    )


#: ``bot_*`` names a child bot may hold: peer send/fetch (CBE-3).
# ``task_open`` has no ``bot_`` prefix and passes the check naturally.
CHILD_BOT_TOOLS = frozenset({"bot_send", "bot_fetch"})


def visible_for_bot(schemas: list[dict] | None, bot_id: str | None) -> list[dict] | None:
    """Tool snapshot for one caller: children never see admin ``bot_*``
    tools; ``bot_send``/``bot_fetch`` (peer send/fetch, identity from
    ContextVar) are the ``bot_*`` names a child may see."""
    if not schemas or bot_id is None or bot_id == "thyca":
        return schemas
    return [
        schema
        for schema in schemas
        if schema.get("function", {}).get("name") in CHILD_BOT_TOOLS
        or not str(schema.get("function", {}).get("name", "")).startswith("bot_")
    ]
