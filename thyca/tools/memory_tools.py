from __future__ import annotations

import json
from contextvars import ContextVar
from dataclasses import asdict

from thyca.memories.archived import DATE_RE
from thyca.memories.facade import MemoryFacade
from thyca.memories.heading import (
    TASK_ID_RE,
    TASK_KINDS,
    TASK_STATUSES,
    validate_task_meta,
)
from thyca.memories.search import _absolute_proj
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
        is_new_task = bool(args.get("new_task"))
        if is_new_task and task is not None:
            raise ValueError(
                "new_task cannot be combined with task; omit new_task to update an "
                "existing task, or omit task to open a new one"
            )
        kind = args.get("kind")
        status = args.get("status")
        cont = args.get("cont")
        if is_new_task and cont is not None:
            raise ValueError(
                "cont cannot be combined with new_task; omit cont for a new task, "
                "or pass task=<existing task_id> with cont"
            )
        habit = args.get("habit")
        habit_explicit = args.get("habit_explicit") is True
        probe_task = "task-00000000" if is_new_task else task
        validate_task_meta(probe_task, cont, kind, status, habit, habit_explicit)
        if is_new_task:
            task = facade.new_task_id()
        chat = _chat_conversation_id.get()
        content = args.get("content") or ""
        proj = args.get("proj")
        if task is not None:
            if is_new_task or kind == "habit":
                sid = facade.remember(
                    args["topic"],
                    args["summary"],
                    content=content,
                    proj=proj,
                    chat=chat,
                    task=task,
                    cont=cont,
                    kind=kind,
                    status=status if status is not None else ("open" if is_new_task else None),
                    habit=habit,
                    habit_explicit=habit_explicit,
                )
            else:
                sid = facade.upsert_task_card(
                    args["topic"],
                    args["summary"],
                    content=content,
                    task=task,
                    status=status,
                    proj=proj,
                    chat=chat,
                    target_memory_id=cont,
                )
        elif kind == "habit":
            sid = facade.record_habit_entry(
                args["topic"],
                args["summary"],
                content=content,
                habit=habit,
                habit_explicit=habit_explicit,
                proj=proj,
                chat=chat,
            )
        else:
            sid = facade.append_other_journal(
                args["topic"],
                args["summary"],
                content=content,
                proj=proj,
                chat=chat,
            )
        return json.dumps({"memory_id": sid, "task_id": task}, ensure_ascii=False)

    return ToolSpec(
        name="memory_remember",
        description=(
            "Save L2 memory into daily file (not SOUL/USER/IDENTITY, secrets, or chitchat). "
            "Use memory_update to fix an existing entry by memory_id. "
            "Check <memory_tasks>/memory_tasks before opening a task: pass task to update "
            "in-place, or new_task=true once for a new goal. Only opener/coordinator/user "
            "closes status. kind='habit' dedupes/reinforces (habit_explicit=true if stated "
            "by user); kind='checkpoint' records state/next step; omit both for facts."
        ),
        parameters={
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Short heading title."},
                "summary": {"type": "string", "description": "Concise one-line fact or state."},
                "content": {"type": "string"},
                "proj": {
                    "type": "string",
                    "description": "Absolute project root path, or omit if general.",
                },
                "task": {
                    "type": "string",
                    "description": "Existing task id (task-<8 hex>); incompatible with new_task.",
                },
                "new_task": {
                    "type": "boolean",
                    "description": "Mint new task id; incompatible with task/cont.",
                },
                "cont": {
                    "type": "string",
                    "description": "Previous memory_id in same task (requires task).",
                },
                "kind": {
                    "type": "string",
                    "enum": list(TASK_KINDS),
                    "description": "'habit' (needs habit) or 'checkpoint' (needs task/new_task).",
                },
                "status": {
                    "type": "string",
                    "enum": list(TASK_STATUSES),
                    "description": "Task status (requires task/new_task); close only if authorized.",
                },
                "habit": {
                    "type": "string",
                    "description": "Short habit label (required with kind='habit').",
                },
                "habit_explicit": {
                    "type": "boolean",
                    "description": "True only when explicitly stated by user (with kind='habit').",
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
        query = args["query"]
        if not query.strip():
            raise ValueError("query must not be blank; pass 2-6 concise keywords")
        timeline_day = args.get("timeline_day")
        if timeline_day is not None and DATE_RE.fullmatch(timeline_day) is None:
            raise ValueError(
                f"timeline_day must be YYYY-MM-DD (closed past day), got {timeline_day!r}"
            )
        task = args.get("task")
        if task is not None and TASK_ID_RE.fullmatch(task) is None:
            raise ValueError(
                f"task must match task-<8 hex> (check memory_tasks for ids), got {task!r}"
            )
        proj = args.get("proj")
        if proj is not None:
            try:
                proj = _absolute_proj(proj)
            except ValueError as exc:
                raise ValueError(
                    "proj must be an absolute project root path (or omit proj), "
                    f"got {proj!r}"
                ) from exc
        # Proactive tool read: the caller may reuse its own memories
        # across origin windows (proactive-only owner exemption).
        result = facade.search(
            query,
            limit=_limit(args),
            timeline_day=timeline_day,
            proj=proj,
            chat=args.get("chat"),
            task=task,
            kind=args.get("kind"),
            status=args.get("status"),
            allow_own_cross_window=True,
        )
        return json.dumps(asdict(result), ensure_ascii=False)

    return ToolSpec(
        name="memory_search",
        description=(
            "Lexical search (FTS + trigram) over archived closed days and profile files "
            "(today is in <today>, or read <today_elsewhere> via memory_get). "
            "Pass 2-6 concise keywords, not full sentences. If has_more=true or snippet "
            "is truncated, call memory_get(memory_id=...). Retry once or admit miss if empty."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "2-6 concise keywords or entity names (not full questions).",
                },
                "limit": {"type": "integer"},
                "timeline_day": {"type": "string", "description": "Closed day YYYY-MM-DD."},
                "proj": {"type": "string", "description": "Exact absolute project root filter."},
                "chat": {"type": "string"},
                "task": {"type": "string", "description": "Filter by task-<8 hex>."},
                "kind": {"type": "string", "enum": list(TASK_KINDS)},
                "status": {"type": "string", "enum": list(TASK_STATUSES)},
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
        description="Most recently updated archived hits (today is in <today>/<today_elsewhere>).",
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
        description=(
            "Read full entry (memory_id) or one leaf (chunk_id); pass exactly one. "
            "Use for has_more=true hits or <today_elsewhere>/<memory_tasks> ids."
        ),
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
            "Delete one L2 memory entry by memory_id. Irreversible — require explicit "
            "user confirmation naming the target before calling."
        ),
        parameters={
            "type": "object",
            "properties": {"memory_id": {"type": "string"}},
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
        description="Extend expiry (optionally raise importance 1..5: 3d/7d/30d/90d/180d) by memory_id.",
        parameters={
            "type": "object",
            "properties": {
                "memory_id": {"type": "string"},
                "importance": {"type": "integer", "minimum": 1, "maximum": 5},
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
            "List task catalog (archived + today) by status/date range; returns task_id "
            "and latest_memory_id to continue/close without duplicates."
        ),
        parameters={
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": list(TASK_STATUSES)},
                "date_from": {"type": "string", "description": "Active >= YYYY-MM-DD."},
                "date_to": {"type": "string", "description": "Active <= YYYY-MM-DD."},
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
            "Edit an existing L2 entry in-place by memory_id (avoids duplicates). "
            "Read via memory_get first if not in <today>; omit unchanged fields; "
            "content requires summary; blank titles/summaries rejected."
        ),
        parameters={
            "type": "object",
            "properties": {
                "memory_id": {"type": "string"},
                "topic": {"type": "string"},
                "summary": {"type": "string"},
                "content": {"type": "string"},
                "proj": {"type": "string", "description": "Absolute project root."},
                "task": {"type": "string", "description": "Task id (task-<8 hex>)."},
                "cont": {"type": "string", "description": "Previous memory_id in task."},
                "kind": {"type": "string", "enum": list(TASK_KINDS)},
                "status": {"type": "string", "enum": list(TASK_STATUSES)},
                "habit": {"type": "string"},
                "habit_explicit": {"type": "boolean"},
            },
            "required": ["memory_id"],
            "additionalProperties": False,
        },
        handler=handler,
        parallel_safe=False,
        resource_key=_daily_key,
    )
