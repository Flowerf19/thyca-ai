"""Agent-callable conversation tools (``compact``).

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
        drop_prior = args.get("drop_prior") is True
        try:
            result = await ctx.compact(drop_prior=drop_prior)
        except Exception as exc:
            return f"compact failed: {type(exc).__name__}"
        if result.get("compacted"):
            summary = result.get("summary") or "excerpt"
            return f"compacted: summary={summary}"
        return "no compaction needed (context under limit)"

    return ToolSpec(
        name=COMPACT_TOOL,
        description=(
            "Compact the current conversation now: summarize older turns and "
            "keep the recent tail, freeing context for later turns. Call this "
            "only AFTER saving durable facts/checkpoints to memory (via "
            "memory_task for open work or memory_note for facts). Set "
            "drop_prior=true when switching topics or after finishing a task "
            "to discard stale earlier compaction summaries instead of carrying "
            "them forward. Safe to call any time; never deletes memory entries."
        ),
        parameters={
            "type": "object",
            "properties": {
                "drop_prior": {
                    "type": "boolean",
                    "description": (
                        "Discard stale earlier compaction summaries instead of "
                        "merging them into the new summary. Use when switching "
                        "topics or after closing a task (once saved to memory)."
                    ),
                },
            },
            "additionalProperties": False,
        },
        handler=handler,
        parallel_safe=False,
    )


def register_conversation_tools(registry) -> None:
    """Register conversation tools on a shared registry (idempotent)."""
    if registry.get(COMPACT_TOOL) is None:
        registry.register(compact_spec())
