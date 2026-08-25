from __future__ import annotations

import json
from dataclasses import asdict

from thyca.tools.memory import MemoryFacade
from thyca.tools.registry import ToolRegistry, ToolSpec


def register_memory_tools(registry: ToolRegistry, facade: MemoryFacade) -> None:
    registry.register(_remember_spec(facade))
    registry.register(_search_spec(facade))
    registry.register(_recent_spec(facade))
    registry.register(_get_spec(facade))


def _remember_spec(facade: MemoryFacade) -> ToolSpec:
    async def handler(args: dict) -> str:
        return facade.remember(
            str(args["topic"]),
            str(args["summary"]),
            content=str(args.get("content") or ""),
        )

    return ToolSpec(
        name="memory_remember",
        description=(
            "Append an L2 memory heading+bullet to today's daily file. "
            "Do not use this for SOUL/USER/IDENTITY — write/edit those files."
        ),
        parameters={
            "type": "object",
            "properties": {
                "topic": {"type": "string"},
                "summary": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["topic", "summary"],
            "additionalProperties": False,
        },
        handler=handler,
        parallel_safe=False,
        resource_key=lambda _args: "memory:daily",
    )


def _search_spec(facade: MemoryFacade) -> ToolSpec:
    async def handler(args: dict) -> str:
        result = facade.search(
            str(args["query"]),
            limit=int(args.get("limit") or 5),
            timeline_day=args.get("timeline_day"),
        )
        return json.dumps(asdict(result), ensure_ascii=False)

    return ToolSpec(
        name="memory_search",
        description="Lexical search (FTS + trigram) over archived memory leaves.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
                "timeline_day": {"type": "string"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        handler=handler,
        parallel_safe=True,
    )


def _recent_spec(facade: MemoryFacade) -> ToolSpec:
    async def handler(args: dict) -> str:
        hits = facade.recent(limit=int(args.get("limit") or 5))
        return json.dumps([asdict(hit) for hit in hits], ensure_ascii=False)

    return ToolSpec(
        name="memory_recent",
        description="Most recently updated archived memory hits.",
        parameters={
            "type": "object",
            "properties": {"limit": {"type": "integer"}},
            "additionalProperties": False,
        },
        handler=handler,
        parallel_safe=True,
    )


def _get_spec(facade: MemoryFacade) -> ToolSpec:
    async def handler(args: dict) -> str:
        return facade.get(
            chunk_id=args.get("chunk_id"),
            session_id=args.get("session_id"),
            path=args.get("path"),
        )

    return ToolSpec(
        name="memory_get",
        description="Read a memory leaf by chunk_id, session_id, or path.",
        parameters={
            "type": "object",
            "properties": {
                "chunk_id": {"type": "string"},
                "session_id": {"type": "string"},
                "path": {"type": "string"},
            },
            "additionalProperties": False,
        },
        handler=handler,
        parallel_safe=True,
    )
