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

from thyca.bots.bus import new_trace_id
from thyca.bots.card import BotCard, BotMemoryPolicy, BotModel, with_baseline_tools
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

    def __init__(self, bots, runtime, facade_fn) -> None:
        self.bots = bots
        self.runtime = runtime
        self.facade_fn = facade_fn


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
        try:
            card = deps.bots.require(recipient)
            if card.paused:
                return f"bot_send failed: bot is paused: {recipient}"
            return await _send_and_deliver(
                deps, sender=sender, recipient=recipient, kind=kind,
                payload=message.strip(),
            )
        except Exception as exc:
            return f"bot_send failed: {exc}"

    return ToolSpec(
        name="bot_send",
        description=(
            "Send a message/task to another bot and get its reply. The "
            "recipient bot runs its own turn immediately; its reply is "
            "returned to you and recorded in the room under its name. "
            "Your identity as sender is taken from your turn, never from "
            "arguments. kind: delegate|message|help_request (default message)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "bot_id": {"type": "string"},
                "message": {"type": "string"},
                "kind": {"type": "string"},
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
        try:
            card = BotCard(
                bot_id=args["bot_id"],
                name=args["bot_id"],
                role=args.get("role", "Trợ lý"),
                description=args.get("description", ""),
                tint=args.get("tint", "accent"),
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
            "thinking, tint (theme token), bash (ask|allow|deny), skills, tools, "
            "hidden_tools, memory {read,write,propose_team}, schedule, persona. "
            "Every created bot also receives the baseline tools (bot_send, "
            "memory_remember, memory_search, memory_get, memory_propose_team) "
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
    "role", "description", "tint", "model", "thinking",
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
            "Patch any of: role, description, tint, model, thinking, skills, "
            "tools, hidden_tools, bash, schedule, persona."
        ),
        parameters={
            "type": "object",
            "properties": {
                "bot_id": {"type": "string"},
                "role": {"type": "string"},
                "description": {"type": "string"},
                "tint": {"type": "string"},
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


def visible_for_bot(schemas: list[dict] | None, bot_id: str | None) -> list[dict] | None:
    """Tool snapshot for one caller: children never see admin ``bot_*``
    tools; ``bot_send`` (peer send, sender from ContextVar) is the one
    ``bot_*`` name a child may see."""
    if not schemas or bot_id is None or bot_id == "thyca":
        return schemas
    return [
        schema
        for schema in schemas
        if schema.get("function", {}).get("name") == "bot_send"
        or not str(schema.get("function", {}).get("name", "")).startswith("bot_")
    ]
