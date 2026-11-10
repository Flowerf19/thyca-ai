"""Group conversation tools for Thyca (group_delete)."""
from __future__ import annotations

import json

from thyca.bots.context import current_bot_turn_context
from thyca.tools.registry import ToolSpec

from .deps import AdminDeps, _deny_child


# Delete one group from its explicit group id.
def _group_delete_spec(deps: AdminDeps) -> ToolSpec:
    async def handler(args: dict) -> str:
        denied = _deny_child()
        if denied:
            return denied
        group_id = args.get("group_id")
        if group_id is None or group_id == "":
            ctx = current_bot_turn_context()
            group_id = ctx.room_id if ctx is not None else None
            if not group_id:
                return "group_delete failed: missing argument: group_id"
        if not isinstance(group_id, str) or not group_id.strip():
            return "group_delete failed: invalid group_id"
        group_id = group_id.strip()
        if getattr(deps, "delete_group", None) is None:
            return "group_delete failed: group deletion is unavailable here"
        try:
            deps.delete_group(group_id)
        except ValueError as exc:
            return f"group_delete failed: {exc}"
        except Exception as exc:
            return f"group_delete failed: {exc}"
        return json.dumps({"ok": True, "group_id": group_id}, ensure_ascii=False)

    return ToolSpec(
        name="group_delete",
        description=(
            "Delete one group conversation (Thyca only). Needs group_id "
            "(a group's id); when omitted it deletes the current room, "
            "unless that room is a DM, which is always refused."
        ),
        parameters={
            "type": "object",
            "properties": {"group_id": {"type": "string"}},
            "required": [],
            "additionalProperties": False,
        },
        handler=handler,
        parallel_safe=False,
    )
