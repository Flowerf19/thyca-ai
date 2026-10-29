"""Bot lifecycle tools: create / pause / delete / restore / edit_card / stop."""
from __future__ import annotations

import json
from dataclasses import replace

from thyca.bots.card import (
    AVATAR_VARIANTS,
    BotCard,
    BotMemoryPolicy,
    BotModel,
    with_baseline_tools,
)
from thyca.tools.registry import ToolSpec

from .deps import AdminDeps, _deny_child


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
        raise ValueError("memory must be an object {read, write}")
    fields = {}
    for key in ("read", "write"):
        field_value = value.get(key, True)
        if not isinstance(field_value, bool):
            raise ValueError("memory must be an object {read, write}")
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
        # Persistent model assignment participates in the model coordination
        # protocol: validate against the freshly loaded config inside the
        # lock, then create (lock order: coordination → per-bot).
        try:
            from thyca.app.bot_settings import (
                BotSettingsError,
                check_model_against_config,
            )
            from thyca.config import ConfigError, default_config, load
            from thyca.config.coordination import CoordinationError, hold

            with hold(deps.bots.thyca_dir):
                cfg_path = deps.bots.thyca_dir / "config.json"
                live = load(cfg_path) if cfg_path.exists() else default_config()
                check_model_against_config(live, card.model)
                created = deps.bots.create(card)
        except BotSettingsError as exc:
            return f"bot_create failed: {exc}"
        except (ConfigError, CoordinationError):
            return "bot_create failed: configuration unavailable, retry later"
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
            "hidden_tools, memory {read,write}, schedule, persona. "
            "Every created bot also receives the baseline tools (bot_send, "
            "message_user, ask_user, report_error, memory_remember, "
            "memory_search, memory_get) "
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
    """Pure card patch used by the bot_create/bot_edit_card tools."""
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
        # Whether the model changes is decided by the patch keys alone, so
        # no card read is needed up front.
        model_changed = ("model" in patch or "thinking" in patch)
        try:
            # A model change reads the card, applies the patch, validates
            # against the fresh config, and persists — all inside the
            # coordination lock. Reading outside it would let a concurrent
            # model assignment slip in between, and the stale patched
            # model would then overwrite the newer one. Identity-only edits
            # skip validation so legacy bots stay editable.
            if model_changed:
                from thyca.app.bot_settings import (
                    BotSettingsError,
                    check_model_against_config,
                )
                from thyca.config import ConfigError, default_config, load
                from thyca.config.coordination import CoordinationError, hold

                try:
                    with hold(deps.bots.thyca_dir):
                        card = deps.bots.require(bot_id)
                        updated = apply_card_edit(card, patch)
                        cfg_path = deps.bots.thyca_dir / "config.json"
                        live = load(cfg_path) if cfg_path.exists() else default_config()
                        # A model-less legacy card has model None: same
                        # "default" fallback the settings write itself uses.
                        current = updated.model if updated.model is not None else BotModel(id="default")
                        check_model_against_config(live, current)
                        deps.bots.update_card(
                            bot_id, updated, model_changed=True,
                        )
                except BotSettingsError as exc:
                    return f"bot_edit_card failed: {exc}"
                except (ConfigError, CoordinationError):
                    return "bot_edit_card failed: configuration unavailable, retry later"
            else:
                card = deps.bots.require(bot_id)
                updated = apply_card_edit(card, patch)
                deps.bots.update_card(
                    bot_id, updated, model_changed=False,
                )
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
