"""Channel-side tools (WS-4): ask_private + private_followup.

Identity and target windows come from the bound BotTurnContext —
never from tool arguments. The origin group sees only the fixed generic
status string; the raw answer never leaves the DM transcript (the store
keeps references, not bodies).
"""
from __future__ import annotations

import json

from thyca.bots.context import current_bot_turn_context
from thyca.bots.private_followups import (
    PendingConflict,
    PrivateFollowupError,
    PrivateFollowupStore,
)
from thyca.core.channel import WORK_REF_RE
from thyca.tools.registry import ToolRegistry, ToolSpec

GROUP_WAITING_STATUS = "Đang chờ thông tin riêng."


def _ctx():
    ctx = current_bot_turn_context()
    return ctx if ctx is not None and ctx.sender_id else None


def _owner_of(ctx, deps, work_id: str) -> str | None:
    """Task owner: registry record, else deterministic single-concrete
    adoption from the earliest source entry. None = fail closed."""
    store = getattr(deps.runtime.workspaces, "store", None)
    if store is None:
        return None
    owner = store.task_owner(ctx.sender_id, work_id)
    if owner is None:
        owner = store.task_owner_fallback(ctx.sender_id, work_id)
    return owner


def register_channel_tools(registry: ToolRegistry, deps) -> None:
    """ask_private + private_followup; registered beside admin tools."""
    _wrap_memory_remember(registry, deps)
    for spec in (_ask_private_spec(deps), _private_followup_spec(deps)):
        if registry.get(spec.name) is None:
            registry.register(spec)


def _wrap_memory_remember(registry: ToolRegistry, deps) -> None:
    """Capture task ownership on a successful memory_remember(new_task).

    Preserves the original spec name/schema/description — only the
    handler is wrapped so the canonical task id that new_task minted is
    bound to the trusted turn sender. Existing memory owner bytes are
    untouched."""
    original = registry.get("memory_remember")
    if original is None or getattr(original.handler, "_pf_wrapped", False):
        return

    async def handler(args: dict) -> str:
        result = await original.handler(args)
        if not args.get("new_task"):
            return result
        ctx = _ctx()
        if ctx is None:
            return result
        task_id = json.loads(result).get("task_id")
        if not (isinstance(task_id, str) and WORK_REF_RE.fullmatch(task_id)):
            return result
        store = getattr(deps.runtime.workspaces, "store", None)
        if store is not None:
            store.record_task_owner(ctx.sender_id, task_id, ctx.sender_id)
        return result

    handler._pf_wrapped = True
    registry.unregister(original.name)
    registry.register(
        ToolSpec(
            name=original.name,
            description=original.description,
            parameters=original.parameters,
            handler=handler,
            parallel_safe=getattr(original, "parallel_safe", True),
            resource_key=getattr(original, "resource_key", None),
        )
    )


def _ask_private_spec(deps) -> ToolSpec:
    async def handler(args: dict) -> str:
        ctx = _ctx()
        if ctx is None:
            return "denied: ask_private requires an in-flight bot turn"
        work_id = args.get("work_id")
        if not isinstance(work_id, str) or not WORK_REF_RE.fullmatch(work_id):
            return "invalid work_id: expected task-<8 hex>"
        question = args.get("question")
        if not isinstance(question, str) or not question.strip():
            return "missing argument: question"
        sender = ctx.sender_id
        origin = getattr(ctx, "room_id", "")
        window = getattr(ctx, "window", None)
        if getattr(window, "kind", None) != "group":
            return "denied: ask_private is only valid from a group window"
        if sender not in getattr(window, "participants", ()):
            return "denied: sender is not a participant of this group"
        owner = _owner_of(ctx, deps, work_id)
        if owner != sender:
            return (
                f"denied: task {work_id} is not owned by {sender}"
            )
        if deps.dm_post is None or deps.dm_resolve is None:
            return "ask_private failed: DM posting is unavailable here"
        dm_id = deps.dm_resolve(sender)
        if not dm_id:
            return "ask_private failed: could not resolve own DM"
        store = PrivateFollowupStore(ctx.thyca_dir)
        idem = f"{ctx.span_id}:{work_id}" if ctx.span_id else None
        try:
            pending = store.create(
                sender,
                work_id=work_id,
                origin_window=origin,
                dm_window=dm_id,
                question=question.strip(),
                idempotency_key=idem,
            )
        except PendingConflict as exc:
            return f"ask_private conflict: {exc}"
        except PrivateFollowupError as exc:
            return f"ask_private failed: {exc}"
        if pending.status != "open":
            return json.dumps({
                "ok": True, "pending_id": pending.pending_id,
                "status": pending.status,
            }, ensure_ascii=False)
        pf_meta = {"kind": "private_question",
                   "pending_id": pending.pending_id,
                   "work_id": work_id,
                   "pfq_marker": idem or pending.pending_id}
        try:
            if deps.channel_actions is not None:
                from thyca.core.channel import ChannelAction
                posted = deps.channel_actions.dispatch(
                    ChannelAction(
                        kind="ask_private", target_window=dm_id,
                        text=question.strip(), metadata=pf_meta,
                    ),
                    channel_id="webui", ctx=ctx,
                )
            else:
                posted = deps.dm_post(
                    sender, question.strip(), origin, meta=pf_meta)
        except Exception as exc:
            return f"ask_private failed: delivery failed: {exc}"
        if not isinstance(posted, dict) or posted.get("ok") is not True:
            err = posted.get("error") if isinstance(posted, dict) else None
            return f"ask_private failed: delivery refused: {err or posted}"
        was_open = pending.status == "open"
        if was_open:
            pending = store.mark_waiting(sender, pending.pending_id)
        if was_open and origin != pending.dm_window and deps.room_post is not None:
            try:
                deps.room_post(sender, GROUP_WAITING_STATUS, origin)
            except Exception:
                pass
        return json.dumps({
            "ok": True,
            "pending_id": pending.pending_id,
            "status": pending.status,
        }, ensure_ascii=False)

    return ToolSpec(
        name="ask_private",
        description=(
            "Ask the user for private information in your own DM for an "
            "existing memory task you own or coordinate. Use "
            "work_id=task-<8 hex>. The group receives only a waiting "
            "status. Private answers are not shareable by default. After "
            "the DM answer, use private_followup to stay ready, finish, "
            "cancel, or propose a redacted return to the origin group."
        ),
        parameters={
            "type": "object",
            "properties": {
                "work_id": {"type": "string"},
                "question": {"type": "string"},
            },
            "required": ["work_id", "question"],
            "additionalProperties": False,
        },
        handler=handler,
    )


def _private_followup_spec(deps) -> ToolSpec:
    async def handler(args: dict) -> str:
        ctx = _ctx()
        if ctx is None:
            return "denied: private_followup requires an in-flight bot turn"
        pending_id = args.get("pending_id")
        if not isinstance(pending_id, str) or not pending_id.strip():
            return "missing argument: pending_id"
        action = args.get("action")
        if action not in ("ready", "resume", "done", "cancel"):
            return "invalid action: expected ready|resume|done|cancel"
        store = PrivateFollowupStore(ctx.thyca_dir)
        sender = ctx.sender_id
        pending = store.get(sender, pending_id.strip())
        if pending is None or pending.bot_id != sender:
            return f"denied: unknown or foreign pending {pending_id}"
        current_window = getattr(ctx, "room_id", "")
        in_dm = current_window == pending.dm_window
        in_origin = current_window == pending.origin_window
        if action in ("ready", "resume", "done") and not in_dm:
            return "denied: this action is only allowed from your own DM"
        if action == "cancel" and not (in_dm or in_origin):
            return "denied: cancel only from your own DM or the origin room"
        redacted = args.get("redacted_text")
        if action == "resume" and (
            not isinstance(redacted, str) or not redacted.strip()
        ):
            return "missing argument: redacted_text"
        if action == "cancel":
            try:
                out = store.cancel(sender, pending_id.strip())
            except PrivateFollowupError as exc:
                return f"private_followup failed: {exc}"
            return json.dumps({
                "ok": True,
                "pending_id": out.pending_id,
                "status": out.status,
            }, ensure_ascii=False)
        idem = f"{ctx.span_id}:{pending_id}:{action}" if ctx.span_id else None
        try:
            out = store.decide(
                sender, pending_id.strip(), action=action,
                redacted=redacted.strip() if isinstance(redacted, str) else None,
                idempotency_key=idem,
            )
        except PrivateFollowupError as exc:
            return f"private_followup failed: {exc}"
        if action == "resume" and deps.dm_card_post is None:
            return "private_followup failed: preview card unavailable"
        if action == "resume":
            try:
                card_out = deps.dm_card_post(
                    sender,
                    {
                        "type": "question",
                        "private_followup": {
                            "pending_id": out.pending_id,
                            "revision": out.proposal_revision,
                        },
                        "title": "Cho phép gửi bản này về nhóm?",
                        "subtitle": out.proposed_redacted,
                        "options": [
                            {"id": "approve", "label": "Cho phép gửi bản này"},
                            {"id": "keep_private", "label": "Giữ riêng"},
                            {"id": "cancel", "label": "Huỷ yêu cầu"},
                        ],
                    },
                )
                if not isinstance(card_out, dict) or card_out.get("ok") is not True:
                    return "private_followup failed: preview card refused"
            except Exception as exc:
                return f"private_followup failed: {exc}"
        return json.dumps({
            "ok": True,
            "pending_id": out.pending_id,
            "status": out.status,
            "requested_outcome": out.requested_outcome,
            "revision": out.proposal_revision,
        }, ensure_ascii=False)

    return ToolSpec(
        name="private_followup",
        description=(
            "Resolve your own pending private follow-up. ready keeps the "
            "work ready without posting to the group; done or cancel "
            "closes it. resume requires a redacted_text proposal. The "
            "proposal stays in DM until the user explicitly approves that "
            "exact revision; never pass raw private answers as "
            "redacted_text. The runtime posts only the approved text to "
            "the original group. You cannot grant sharing consent "
            "through this tool."
        ),
        parameters={
            "type": "object",
            "properties": {
                "pending_id": {"type": "string"},
                "action": {
                    "type": "string",
                    "enum": ["ready", "resume", "done", "cancel"],
                },
                "redacted_text": {"type": "string"},
            },
            "required": ["pending_id", "action"],
            "additionalProperties": False,
        },
        handler=handler,
    )
