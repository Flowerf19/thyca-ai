"""Agent-callable session tools (``compact``).

Turn-scoped: the handler reads the in-flight turn's ``BotTurnContext``
(the runtime binds a compact callback per turn) instead of holding app
deps at registration — the same pattern A2A tools use for sender/room.
Outside a bot turn (CLI, no context) the tool answers honestly instead
of pretending.
"""
from __future__ import annotations

from thyca.bots.context import current_bot_turn_context
from thyca.tools.registry import ToolSpec

COMPACT_TOOL = "compact"


def compact_spec() -> ToolSpec:
    async def handler(args: dict) -> str:
        ctx = current_bot_turn_context()
        if ctx is None or ctx.compact is None:
            return "compact unavailable in this context"
        try:
            result = await ctx.compact()
        except Exception as exc:
            return f"compact failed: {type(exc).__name__}"
        if result.get("compacted"):
            summary = result.get("summary") or "excerpt"
            return f"compacted: summary={summary}"
        return "no compaction needed (context under limit)"

    return ToolSpec(
        name=COMPACT_TOOL,
        description=(
            "Compact the current session now: summarize what happened and "
            "keep the tail, freeing context for later turns. Call this only "
            "AFTER you have written the durable facts/checkpoints to memory "
            "(memory_remember with kind='checkpoint' where a task is "
            "involved), so nothing important is lost. Safe to call any "
            "time; never deletes your memory entries."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        handler=handler,
        parallel_safe=False,
    )


def register_session_tools(registry) -> None:
    """Register session tools on a shared registry (idempotent)."""
    if registry.get(COMPACT_TOOL) is None:
        registry.register(compact_spec())