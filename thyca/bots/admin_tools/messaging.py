"""User-facing message tools: message_user / ask_user / report_error / task_open."""
from __future__ import annotations

import json

from thyca.bots.bus import new_span_id
from thyca.bots.context import current_bot_turn_context
from thyca.core.channel import ChannelAction
from thyca.memory.identity import current_bot_id
from thyca.tools.registry import ToolSpec

from .deps import AdminDeps


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
            dm_id = deps.dm_resolve(sender) if getattr(
                deps, "dm_resolve", None) is not None else None
            if dm_id and deps.channel_actions is not None and ctx is not None:
                result = deps.channel_actions.dispatch(
                    ChannelAction(
                        kind="post", target_window=dm_id,
                        text=message.strip(),
                    ),
                    channel_id="webui", ctx=ctx,
                )
            else:
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
            if deps.channel_actions is not None:
                result = deps.channel_actions.dispatch(
                    ChannelAction(
                        kind="ask_choice", target_window=room_id,
                        text="", card=card,
                    ),
                    channel_id="webui",
                    ctx=current_bot_turn_context(),
                )
            else:
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
        memory_task_id = args.get("memory_task_id")
        if memory_task_id is not None:
            from thyca.core.channel import WORK_REF_RE

            if not isinstance(memory_task_id, str) or not WORK_REF_RE.fullmatch(
                memory_task_id
            ):
                return "invalid argument: memory_task_id (need task-<8 hex>)"
        key = new_span_id()
        ack = {"ok": True, "task": title.strip(), "status": "open",
               "key": key, "span_id": key, "task_key": key,
               "bot_id": sender,
               "room": ctx.room_id if ctx is not None else ""}
        if memory_task_id:
            ack["memory_task_id"] = memory_task_id
        return json.dumps(ack, ensure_ascii=False)

    return ToolSpec(
        name="task_open",
        description=(
            "Open a task that outlives this turn (work born in plain chat, "
            "not a delegation). Records a task_open event in your own "
            "transcript; your open-task view in later prompts lists it "
            "until a result/error event linked to the returned key "
            "(span_id) closes it. Args: title, optional room. "
            "Optionally link an existing memory task with memory_task_id "
            "(task-<8 hex>); never infer the link from a title."
        ),
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "room": {"type": "string"},
                "memory_task_id": {
                    "type": "string",
                    "pattern": "task-[0-9a-f]{8}",
                },
            },
            "required": ["title"],
            "additionalProperties": False,
        },
        handler=handler,
        parallel_safe=True,
    )
