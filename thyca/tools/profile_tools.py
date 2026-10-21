"""Thyca-only profile tool (``profile_update``): write side of the
shared-profile aggregation.

The scheduled aggregation turn decides the full desired line set and
calls this tool; the safe file mechanics live in
:mod:`thyca.memory.profile`. Child bots are refused at the handler (and
the name is not baseline, so their cards do not carry it either).
"""
from __future__ import annotations

import json

from thyca.memory.identity import current_bot_id
from thyca.memory.profile import update_profile_block
from thyca.tools.registry import ToolSpec

PROFILE_UPDATE_TOOL = "profile_update"


def profile_update_spec(facade) -> ToolSpec:
    async def handler(args: dict) -> str:
        caller = current_bot_id()
        if caller is not None and caller != "thyca":
            return f"denied: profile_update is Thyca-only (caller: {caller})"
        lines = args.get("lines")
        if not isinstance(lines, list) or not all(
            isinstance(line, str) for line in lines
        ):
            return "invalid lines: want a list of strings"
        try:
            added, removed = update_profile_block(
                facade.thyca_dir / "USER.md",
                lines,
                lock_for=facade.writer.lock_for,
            )
        except OSError as exc:
            return f"profile update failed: {type(exc).__name__}"
        return json.dumps(
            {"ok": True, "added": added, "removed": removed},
            ensure_ascii=False,
        )

    return ToolSpec(
        name=PROFILE_UPDATE_TOOL,
        description=(
            "Replace the auto-managed profile block in the shared USER.md "
            "with the FULL desired line list (never a diff). This is the "
            "aggregated user profile every bot reads; manual user content "
            "outside the block is never touched. Only Thyca (the bot "
            "manager) may call this."
        ),
        parameters={
            "type": "object",
            "properties": {
                "lines": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Full desired profile lines, one durable fact per "
                        "line; scoped facts keep their qualifier "
                        "(e.g. 'Discord: trả lời ngắn gọn')."
                    ),
                },
            },
            "required": ["lines"],
            "additionalProperties": False,
        },
        handler=handler,
        parallel_safe=False,
    )


def register_profile_tools(registry, facade) -> None:
    """Register profile tools on a shared registry (idempotent)."""
    if registry.get(PROFILE_UPDATE_TOOL) is None:
        registry.register(profile_update_spec(facade))