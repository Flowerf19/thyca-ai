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
from thyca.bots.card import BotCard, BotMemoryPolicy, BotModel
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
    ):
        if registry.get(spec.name) is None:
            registry.register(spec)


def _create_spec(deps: AdminDeps) -> ToolSpec:
    async def handler(args: dict) -> str:
        denied = _deny_child()
        if denied:
            return denied
        try:
            card = BotCard(
                bot_id=args["bot_id"],
                name=args["bot_id"],
                role=args.get("role", "Trợ lý"),
                description=args.get("description", ""),
                tint=args.get("tint", "accent"),
                model=BotModel(id=args.get("model", "default")),
                bash=args.get("bash", "ask"),
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
        return json.dumps({"ok": True, "bot_id": created.bot_id}, ensure_ascii=False)

    return ToolSpec(
        name="bot_create",
        description=(
            "Create a new team bot (Thyca only). Needs bot_id (a-z, 0-9, hyphens), "
            "role, description; optional model ('default' keeps Settings), "
            "tint (theme token), bash (ask|allow|deny), persona text."
        ),
        parameters={
            "type": "object",
            "properties": {
                "bot_id": {"type": "string"},
                "role": {"type": "string"},
                "description": {"type": "string"},
                "model": {"type": "string"},
                "tint": {"type": "string"},
                "bash": {"type": "string"},
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
            deps.bots.require(bot_id)
        except Exception as exc:
            return f"bot_delegate failed: {exc}"
        room = args.get("room") if isinstance(args.get("room"), str) else ""
        envelope = deps.runtime.send(
            sender="thyca",
            recipient=bot_id,
            kind="delegate",
            session_id=room or "",
            payload=task.strip(),
            trace_id=args.get("trace_id") if isinstance(args.get("trace_id"), str) else None,
        )
        return json.dumps(
            {
                "ok": True,
                "to": bot_id,
                "trace_id": envelope.trace_id,
                "queued": deps.runtime._bus.pending(bot_id),
            },
            ensure_ascii=False,
        )

    return ToolSpec(
        name="bot_delegate",
        description=(
            "Give a task to a bot (Thyca only). Queues a delegate envelope the "
            "bot reads at its next turn; returns the trace_id to follow it."
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
        parallel_safe=False,
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
    """Tool snapshot for one caller: children never see ``bot_*`` (BE-5)."""
    if not schemas or bot_id is None or bot_id == "thyca":
        return schemas
    return [
        schema
        for schema in schemas
        if not str(schema.get("function", {}).get("name", "")).startswith("bot_")
    ]
