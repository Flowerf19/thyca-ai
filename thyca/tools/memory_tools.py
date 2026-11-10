from __future__ import annotations

import json
from contextvars import ContextVar
from dataclasses import asdict

from thyca.memories.facade import MemoryFacade
from thyca.tools.registry import ToolRegistry, ToolSpec

_chat_conversation_id: ContextVar[str | None] = ContextVar("thyca_memory_chat", default=None)


def bind_chat_conversation(conversation_id: str | None):
    """Pin the in-flight chat conversation id for memory_remember to inject."""
    return _chat_conversation_id.set(conversation_id)


def reset_chat_conversation(token) -> None:
    _chat_conversation_id.reset(token)


def _limit(args: dict) -> int:
    """Graceful limit: absent/None → default, explicit values pass through.

    An explicit ``0`` (or negative) reaches the facade, which clamps it
    with a warning — so the public clamp warning survives the gateway.
    Only missing/None or unparseable input falls back to the default.
    """
    if "limit" not in args or args.get("limit") is None:
        return 5
    try:
        return int(args["limit"])
    except (TypeError, ValueError):
        return 5


def _daily_key(_args: dict) -> str:
    """Shared resource key: every daily mutation serializes on one lock."""
    return "memory:daily"


def register_memory_tools(
    registry: ToolRegistry,
    facade: MemoryFacade,
) -> None:
    registry.register(_remember_spec(facade))
    registry.register(_search_spec(facade))
    registry.register(_recent_spec(facade))
    registry.register(_get_spec(facade))
    registry.register(_forget_spec(facade))
    registry.register(_reinforce_spec(facade))
    registry.register(_update_spec(facade))
    registry.register(_tasks_spec(facade))


def _remember_spec(facade: MemoryFacade) -> ToolSpec:
    async def handler(args: dict) -> str:
        # Schema types arrive pre-checked by the registry (X3).
        task = args.get("task")
        if args.get("new_task"):
            if task:
                raise ValueError("new_task cannot be combined with task")
            task = facade.new_task_id()
        sid = facade.remember(
            args["topic"],
            args["summary"],
            content=args.get("content") or "",
            proj=args.get("proj"),
            chat=_chat_conversation_id.get(),
            task=task,
            cont=args.get("cont"),
            kind=args.get("kind"),
            status=args.get("status"),
            habit=args.get("habit"),
            habit_explicit=args.get("habit_explicit") is True,
        )
        return json.dumps({"memory_id": sid, "task_id": task}, ensure_ascii=False)

    return ToolSpec(
        name="memory_remember",
        description=(
            "Append an L2 memory heading+bullet to today's daily file. "
            "Do not use this for SOUL/USER/IDENTITY — write/edit those files. "
            "Before opening a new task, check the open-task catalog "
            "(memory_tasks) or search to reuse an existing task id: pass it "
            "as task to continue, or new_task=true to mint a fresh one. "
            "Only set status='done' when the whole task is finished, not "
            "when your own part is done — and only the bot coordinating "
            "or opening the task (or the user) closes it; everyone else "
            "leaves it open. kind='habit' is only for repeated "
            "habit/preference observations, with habit as a short label. "
            "kind='checkpoint' is only for a task checkpoint recording "
            "the current state and next step."
        ),
        parameters={
            "type": "object",
            "properties": {
                "topic": {"type": "string"},
                "summary": {"type": "string"},
                "content": {"type": "string"},
                "proj": {
                    "type": "string",
                    "description": (
                        "Absolute path of the project root this memory belongs to, "
                        "e.g. /home/flowerf/Projects/thyca-ai or /home/flowerf/.thyca. "
                        "Use the real repo/workdir root, never a short name or "
                        "relative path. Omit for general or user-level memories "
                        "that belong to no project."
                    ),
                },
                "task": {
                    "type": "string",
                    "description": (
                        "Existing task id (task-<8 hex>) to continue. "
                        "Cannot be combined with new_task."
                    ),
                },
                "new_task": {
                    "type": "boolean",
                    "description": (
                        "Mint a fresh system task id for a new goal. "
                        "Cannot be combined with task."
                    ),
                },
                "cont": {
                    "type": "string",
                    "description": (
                        "Previous memory id in the same task. "
                        "Requires task."
                    ),
                },
                "kind": {
                    "type": "string",
                    "description": (
                        "Memory kind: 'habit' for a repeated habit/preference "
                        "observation, or 'checkpoint' for a task checkpoint "
                        "(requires task or new_task)."
                    ),
                },
                "status": {
                    "type": "string",
                    "description": (
                        "Task status 'open' or 'done'. Requires task. Only set "
                        "'done' when the whole task is finished, and only if "
                        "you coordinate or opened the task (or the user asks)."
                    ),
                },
                "habit": {
                    "type": "string",
                    "description": (
                        "Short habit label. Required with kind='habit', "
                        "forbidden without kind."
                    ),
                },
                "habit_explicit": {
                    "type": "boolean",
                    "description": (
                        "True only when the user explicitly states a lasting "
                        "habit/preference ('I usually/always/like...'), not "
                        "inferred from events. Requires kind='habit'."
                    ),
                },
            },
            "required": ["topic", "summary"],
            "additionalProperties": False,
        },
        handler=handler,
        parallel_safe=False,
        resource_key=_daily_key,
    )


def _search_spec(facade: MemoryFacade) -> ToolSpec:
    async def handler(args: dict) -> str:
        # Proactive tool read: the caller may reuse its own memories
        # across origin windows (proactive-only owner exemption).
        result = facade.search(
            args["query"],
            limit=_limit(args),
            timeline_day=args.get("timeline_day"),
            proj=args.get("proj"),
            chat=args.get("chat"),
            task=args.get("task"),
            kind=args.get("kind"),
            status=args.get("status"),
            allow_own_cross_window=True,
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
                "proj": {
                    "type": "string",
                    "description": (
                        "Filter to leaves saved with this exact project root path "
                        "(absolute path, as passed to memory_remember's proj)."
                    ),
                },
                "chat": {
                    "type": "string",
                    "description": "Filter to leaves saved during this chat session id.",
                },
                "task": {
                    "type": "string",
                    "description": "Filter to leaves in this task (task-<8 hex>).",
                },
                "kind": {
                    "type": "string",
                    "description": "Filter to leaves of this kind ('habit', 'checkpoint').",
                },
                "status": {
                    "type": "string",
                    "description": "Filter to leaves with this task status ('open', 'done').",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        handler=handler,
        parallel_safe=True,
    )


def _recent_spec(facade: MemoryFacade) -> ToolSpec:
    async def handler(args: dict) -> str:
        # Proactive tool read: same owner exemption as memory_search.
        hits = facade.recent(limit=_limit(args), allow_own_cross_window=True)
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
        # Proactive tool read: same owner exemption as memory_search.
        return facade.get(
            chunk_id=args.get("chunk_id"),
            memory_id=args.get("memory_id"),
            allow_own_cross_window=True,
        )

    return ToolSpec(
        name="memory_get",
        description="Read a memory leaf by exactly one of chunk_id, memory_id.",
        parameters={
            "type": "object",
            "properties": {
                "chunk_id": {"type": "string"},
                "memory_id": {"type": "string"},
            },
            "additionalProperties": False,
        },
        handler=handler,
        parallel_safe=True,
    )


def _forget_spec(facade: MemoryFacade) -> ToolSpec:
    async def handler(args: dict) -> str:
        facade.forget(args["memory_id"])
        return "forgotten"

    return ToolSpec(
        name="memory_forget",
        description=(
            "Delete one L2 memory leaf (heading+bullet) from its daily file by memory_id. "
            "Irreversible — confirm with the user before calling."
        ),
        parameters={
            "type": "object",
            "properties": {
                "memory_id": {"type": "string"},
            },
            "required": ["memory_id"],
            "additionalProperties": False,
        },
        handler=handler,
        parallel_safe=False,
        resource_key=_daily_key,
    )


def _reinforce_spec(facade: MemoryFacade) -> ToolSpec:
    async def handler(args: dict) -> str:
        importance = args.get("importance")
        expires = facade.reinforce(
            args["memory_id"],
            importance=int(importance) if importance is not None else None,
        )
        return json.dumps({"memory_id": args["memory_id"], "expires_at": expires}, ensure_ascii=False)

    return ToolSpec(
        name="memory_reinforce",
        description="Extend a memory leaf's expiry (optionally raise importance) by memory_id.",
        parameters={
            "type": "object",
            "properties": {
                "memory_id": {"type": "string"},
                "importance": {"type": "integer"},
            },
            "required": ["memory_id"],
            "additionalProperties": False,
        },
        handler=handler,
        parallel_safe=False,
        resource_key=_daily_key,
    )


def _tasks_spec(facade: MemoryFacade) -> ToolSpec:
    async def handler(args: dict) -> str:
        # Proactive tool read: same owner exemption as memory_search.
        raw_limit = args.get("limit")
        catalog = facade.tasks(
            status=args.get("status"),
            date_from=args.get("date_from"),
            date_to=args.get("date_to"),
            limit=20 if raw_limit is None else raw_limit,
            allow_own_cross_window=True,
        )
        return json.dumps(catalog, ensure_ascii=False)

    return ToolSpec(
        name="memory_tasks",
        description=(
            "Browse the task catalog by status and/or active date range, "
            "instead of keyword search for questions like "
            "'what tasks were open last week'. Only tasks with entries "
            "you may read appear."
        ),
        parameters={
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Filter to tasks with this status ('open', 'done').",
                },
                "date_from": {
                    "type": "string",
                    "description": "Only tasks active on or after YYYY-MM-DD (inclusive).",
                },
                "date_to": {
                    "type": "string",
                    "description": "Only tasks active on or before YYYY-MM-DD (inclusive).",
                },
                "limit": {"type": "integer"},
            },
            "additionalProperties": False,
        },
        handler=handler,
        parallel_safe=True,
    )


def _update_spec(facade: MemoryFacade) -> ToolSpec:
    async def handler(args: dict) -> str:
        # Schema types arrive pre-checked by the registry (X3): present
        # values are str, so only the strip lives here as domain validation.
        topic = args.get("topic")
        summary = args.get("summary")
        facade.update(
            args["memory_id"],
            topic=topic.strip() if topic is not None else None,
            summary=summary.strip() if summary is not None else None,
            content=args.get("content"),
            proj=args.get("proj"),
            task=args.get("task"),
            cont=args.get("cont"),
            kind=args.get("kind"),
            status=args.get("status"),
            habit=args.get("habit"),
            habit_explicit=args.get("habit_explicit") or False,
        )
        return "updated"

    return ToolSpec(
        name="memory_update",
        description=(
            "Edit one L2 memory leaf's title and/or body by memory_id. "
            "The leaf's id stays stable; the search index is rebuilt after the edit. "
            "content requires summary; blank titles/summaries are rejected."
        ),
        parameters={
            "type": "object",
            "properties": {
                "memory_id": {"type": "string"},
                "topic": {"type": "string"},
                "summary": {"type": "string"},
                "content": {"type": "string"},
                "proj": {
                    "type": "string",
                    "description": (
                        "New project root path (absolute) for this memory. "
                        "Omit to keep the current value."
                    ),
                },
                "task": {
                    "type": "string",
                    "description": "Task id (task-<8 hex>). Omit to keep the current value.",
                },
                "cont": {
                    "type": "string",
                    "description": (
                        "Previous memory id in the same task. "
                        "Omit to keep the current value."
                    ),
                },
                "kind": {
                    "type": "string",
                    "description": "Memory kind ('habit', 'checkpoint'). Omit to keep.",
                },
                "status": {
                    "type": "string",
                    "description": (
                        "Task status 'open' or 'done'. Only 'done' when the "
                        "whole task is finished, and only by its coordinator "
                        "or opener (or the user). Omit to keep."
                    ),
                },
                "habit": {
                    "type": "string",
                    "description": (
                        "Short habit label (required with kind='habit'). "
                        "Omit to keep."
                    ),
                },
                "habit_explicit": {
                    "type": "boolean",
                    "description": (
                        "True marks a user-stated habit (requires kind='habit'). "
                        "Omit to keep."
                    ),
                },
            },
            "required": ["memory_id"],
            "additionalProperties": False,
        },
        handler=handler,
        parallel_safe=False,
        resource_key=_daily_key,
    )
