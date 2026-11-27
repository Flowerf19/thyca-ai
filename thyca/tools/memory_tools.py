from __future__ import annotations

import json
import re
from contextvars import ContextVar
from dataclasses import asdict

from thyca.memories.archived import DATE_RE
from thyca.memories.chunk import Chunker
from thyca.memories.facade import MemoryFacade
from thyca.memories.habits import sweep_bot_habits
from thyca.memories.heading import (
    TASK_ID_RE,
    TASK_STATUSES,
)
from thyca.memories.identity import current_bot_id
from thyca.memories.search import list_day_entries
from thyca.tools.registry import ToolRegistry, ToolSpec

_chat_conversation_id: ContextVar[str | None] = ContextVar("thyca_memory_chat", default=None)
_SEARCH_SCOPES = ("notes", "tasks")
_TASK_QUERY_TOKEN_RE = re.compile(r"[^\w]+", re.UNICODE)


def bind_chat_conversation(conversation_id: str | None):
    """Pin the in-flight chat conversation id for memory writes to inject."""
    return _chat_conversation_id.set(conversation_id)


def reset_chat_conversation(token) -> None:
    _chat_conversation_id.reset(token)


def _limit(args: dict, *, default: int = 5) -> int:
    """Graceful limit: absent/None -> default, explicit values pass through.

    An explicit ``0`` (or negative) reaches the facade, which clamps it
    with a warning so the public clamp warning survives the gateway.
    """
    if "limit" not in args or args.get("limit") is None:
        return default
    try:
        return int(args["limit"])
    except (TypeError, ValueError):
        return default


def _daily_key(_args: dict) -> str:
    """Shared resource key: every daily mutation serializes on one lock."""
    return "memory:daily"


def _sweep_explicit_habit(facade: MemoryFacade) -> None:
    bot_id = current_bot_id()
    if not bot_id:
        return
    bot_dir = facade.thyca_dir / "bots" / bot_id
    if not bot_dir.is_dir():
        return
    try:
        sweep_bot_habits(facade, bot_id, bot_dir / "USER.md")
    except Exception:
        pass


def _is_chunk_id(identifier: str) -> bool:
    parts = identifier.split("#")
    return len(parts) == 3 and parts[2].isdigit()


def _filter_tasks_by_query(tasks: list[dict], query: str) -> list[dict]:
    query_norm = Chunker.normalize(query)
    tokens = [tok for tok in _TASK_QUERY_TOKEN_RE.split(query_norm) if len(tok) >= 2]
    if not query_norm:
        return tasks
    strict_matches: list[dict] = []
    partial_matches: list[dict] = []
    for entry in tasks:
        hay = Chunker.normalize(
            f"{entry.get('title', '')} {entry.get('summary', '')} {entry.get('task_id', '')}"
        )
        if query_norm in hay or (tokens and all(tok in hay for tok in tokens)):
            strict_matches.append(entry)
        elif tokens and any(tok in hay for tok in tokens):
            partial_matches.append(entry)
    return strict_matches or partial_matches


def register_memory_tools(
    registry: ToolRegistry,
    facade: MemoryFacade,
) -> None:
    registry.register(_search_spec(facade))
    registry.register(_get_spec(facade))
    registry.register(_note_spec(facade))
    registry.register(_task_spec(facade))
    registry.register(_forget_spec(facade))


def sidecar_memory_tools(facade: MemoryFacade) -> ToolRegistry:
    """Minimal write-only registry (memory_note + memory_task) for sidecar extraction."""
    registry = ToolRegistry()
    registry.register(_note_spec(facade))
    registry.register(_task_spec(facade))
    return registry


def _search_spec(facade: MemoryFacade) -> ToolSpec:
    async def handler(args: dict) -> str:
        raw_scope = args.get("scope")
        if raw_scope is not None and raw_scope not in _SEARCH_SCOPES:
            raise ValueError(f"scope must be one of {list(_SEARCH_SCOPES)}, got {raw_scope!r}")
        day = args.get("day")
        if day is not None and (not isinstance(day, str) or DATE_RE.fullmatch(day) is None):
            raise ValueError(f"day must be YYYY-MM-DD (today or past day), got {day!r}")
        task = args.get("task")
        if task is not None and (not isinstance(task, str) or TASK_ID_RE.fullmatch(task) is None):
            raise ValueError(
                f"task must match task-<8 hex> (use scope='tasks' to list ids), got {task!r}"
            )
        status = args.get("status")
        if status is not None and status not in TASK_STATUSES:
            raise ValueError(f"status must be one of {list(TASK_STATUSES)}, got {status!r}")

        raw_query = args.get("query")
        query_text = raw_query.strip() if isinstance(raw_query, str) else ""
        scope = raw_scope or ("tasks" if not query_text and status is not None else "notes")
        if scope == "tasks":
            requested_limit = _limit(args, default=20)
            fetch_limit = max(requested_limit, 50) if query_text else requested_limit
            catalog = facade.tasks(
                status=status,
                date_from=day,
                limit=fetch_limit,
                allow_own_cross_window=True,
            )
            if query_text:
                filtered = _filter_tasks_by_query(catalog.get("tasks", []), query_text)
                cap = max(1, min(requested_limit, 50))
                catalog = {**catalog, "tasks": filtered[:cap]}
            return json.dumps(catalog, ensure_ascii=False)
        if not query_text:
            if day is not None:
                listed = list_day_entries(
                    facade.archive,
                    day,
                    limit=_limit(args, default=5),
                    reader=current_bot_id(),
                    task=task,
                    status=status,
                    allow_own_cross_window=True,
                )
                return json.dumps(asdict(listed), ensure_ascii=False)
            raise ValueError(
                "query must not be blank when scope='notes' without day; pass 2-6 specific "
                "keywords, pass day='YYYY-MM-DD' to list a day, or set scope='tasks'"
            )
        result = facade.search(
            raw_query,
            limit=_limit(args, default=5),
            timeline_day=day,
            task=task,
            status=status,
            allow_own_cross_window=True,
        )
        return json.dumps(asdict(result), ensure_ascii=False)

    return ToolSpec(
        name="memory_search",
        description=(
            "Search archived days, today's daily file, and profile files (scope='notes', default), "
            "list one day's entries by passing day='YYYY-MM-DD' without query, or list/filter the "
            "task catalog including dormant >7d and done/cancelled tasks (scope='tasks'). Skip if "
            "the answer is already in <today> or <memory_tasks>. For keyword search, pass 2-6 "
            "specific keywords (never generic words like 'note' or 'memory'); if empty, retry at "
            "most once with alternate keywords then admit miss. For older/closed tasks, call "
            "scope='tasks' first (optional query filters titles/summaries). Only call memory_get "
            "on 1-3 relevant hits where has_more=true or snippet is truncated; use snippet "
            "directly when has_more=false."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "2-6 specific keywords (for scope='notes', omit only when listing a "
                        "specific day; optional title/summary filter for scope='tasks')."
                    ),
                },
                "scope": {
                    "type": "string",
                    "enum": list(_SEARCH_SCOPES),
                    "description": "'notes' (default) searches text; 'tasks' lists/filters tasks.",
                },
                "status": {
                    "type": "string",
                    "enum": list(TASK_STATUSES),
                    "description": "Filter by task status (open/done/cancelled).",
                },
                "task": {
                    "type": "string",
                    "description": "Filter notes by task-<8 hex>.",
                },
                "day": {
                    "type": "string",
                    "description": "YYYY-MM-DD (exact day for scope='notes'; active >= day for tasks).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (notes: default 5, max 10; tasks: default 20).",
                },
            },
            "additionalProperties": False,
        },
        handler=handler,
        parallel_safe=True,
    )


def _get_spec(facade: MemoryFacade) -> ToolSpec:
    async def handler(args: dict) -> str:
        raw_id = args.get("id")
        if not isinstance(raw_id, str) or not raw_id.strip():
            raise ValueError("id must be a non-empty memory_id or chunk_id")
        clean = raw_id.strip()
        if _is_chunk_id(clean):
            return facade.get(chunk_id=clean, allow_own_cross_window=True)
        return facade.get(memory_id=clean, allow_own_cross_window=True)

    return ToolSpec(
        name="memory_get",
        description=(
            "Read a full memory entry (memory_id) or one leaf (chunk_id) by id. "
            "Use selectively (1-3 promising ids) when a search hit has has_more=true or a "
            "truncated snippet, or to inspect a <today_elsewhere> or task id. Do not call for "
            "notes already complete in <today>, when has_more=false, or to re-verify a write "
            "that just succeeded."
        ),
        parameters={
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "memory_id (e.g. 2026-11-26#abcd1234, canonical#user) or chunk_id.",
                },
            },
            "required": ["id"],
            "additionalProperties": False,
        },
        handler=handler,
        parallel_safe=True,
    )


def _note_spec(facade: MemoryFacade) -> ToolSpec:
    async def handler(args: dict) -> str:
        memory_id = args.get("memory_id")
        topic = args.get("topic")
        summary = args.get("summary")
        content = args.get("content")
        habit = args.get("habit")
        explicit = args.get("explicit") is True
        if habit is not None and (not isinstance(habit, str) or not habit.strip()):
            raise ValueError("habit must be a non-empty string")
        clean_habit = habit.strip() if isinstance(habit, str) else None
        if explicit and clean_habit is None and not memory_id:
            raise ValueError("explicit=true requires habit")

        if memory_id is not None:
            if not isinstance(memory_id, str) or not memory_id.strip():
                raise ValueError("memory_id must be a non-empty string")
            facade.update(
                memory_id.strip(),
                topic=topic.strip() if isinstance(topic, str) else None,
                summary=summary.strip() if isinstance(summary, str) else None,
                content=content,
                kind="habit" if clean_habit is not None else None,
                habit=clean_habit,
                habit_explicit=explicit,
            )
            if explicit and clean_habit is not None:
                _sweep_explicit_habit(facade)
            return json.dumps({"memory_id": memory_id.strip(), "updated": True}, ensure_ascii=False)

        if not isinstance(topic, str) or not topic.strip():
            raise ValueError("topic is required when creating a new note")
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("summary is required when creating a new note")
        chat = _chat_conversation_id.get()
        body = content or ""
        if clean_habit is not None:
            sid = facade.record_habit_entry(
                topic.strip(),
                summary.strip(),
                content=body,
                habit=clean_habit,
                habit_explicit=explicit,
                chat=chat,
            )
            if explicit:
                _sweep_explicit_habit(facade)
        else:
            sid = facade.append_other_journal(
                topic.strip(),
                summary.strip(),
                content=body,
                chat=chat,
            )
        return json.dumps({"memory_id": sid}, ensure_ascii=False)

    return ToolSpec(
        name="memory_note",
        description=(
            "Save or edit a concise daily L2 fact or recurring habit in the conversation's "
            "primary language (not multi-step tasks, static bot persona/team lore, volatile "
            "web-search/news/ranking dumps, secrets, speculation, or chitchat). Omit memory_id "
            "to create/dedupe; pass memory_id to fix an error or revise a same-day draft/workflow "
            "in-place (when facts change over days, create a new note to preserve history). "
            "summary is the main one-line bullet — omit content unless adding brief extra bullets "
            "(never repeat summary or paste long articles). Set habit (and explicit=true when "
            "user stated it directly) for recurring preferences, formatting rules, or workflow "
            "conventions."
        ),
        parameters={
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "Short heading title (required when creating; omit on edit unless renaming).",
                },
                "summary": {
                    "type": "string",
                    "description": "Concise one-line fact written as the main bullet (required when creating or with content).",
                },
                "content": {
                    "type": "string",
                    "description": "Optional extra details beyond summary; never duplicate summary here (pass '' with summary to clear old details).",
                },
                "habit": {
                    "type": "string",
                    "description": "Short habit label (2-6 words) to record/reinforce a recurring preference.",
                },
                "explicit": {
                    "type": "boolean",
                    "description": "True only when explicitly stated by the user (requires habit).",
                },
                "memory_id": {
                    "type": "string",
                    "description": "Existing memory_id to edit in-place; omit to create.",
                },
            },
            "additionalProperties": False,
        },
        handler=handler,
        parallel_safe=False,
        resource_key=_daily_key,
    )


def _task_spec(facade: MemoryFacade) -> ToolSpec:
    async def handler(args: dict) -> str:
        task = args.get("task")
        if task is not None and (not isinstance(task, str) or TASK_ID_RE.fullmatch(task) is None):
            raise ValueError(
                f"task must match task-<8 hex> (use memory_search(scope='tasks') for ids), got {task!r}"
            )
        status = args.get("status")
        if status is not None and status not in TASK_STATUSES:
            raise ValueError(f"status must be one of {list(TASK_STATUSES)}, got {status!r}")
        summary = args.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("summary must be a non-empty string")
        topic = args.get("topic")
        content = args.get("content") or ""
        target_id = args.get("memory_id")
        if target_id is not None and (not isinstance(target_id, str) or not target_id.strip()):
            raise ValueError("memory_id must be a non-empty string")
        chat = _chat_conversation_id.get()

        if task is None:
            if not isinstance(topic, str) or not topic.strip():
                raise ValueError("topic is required when opening a new task")
            if target_id is not None:
                raise ValueError("memory_id requires task; omit memory_id when opening a new task")
            new_tid = facade.new_task_id()
            sid = facade.remember(
                topic.strip(),
                summary.strip(),
                content=content,
                chat=chat,
                task=new_tid,
                kind="checkpoint",
                status=status or "open",
            )
            return json.dumps(
                {"memory_id": sid, "task_id": new_tid, "created": True},
                ensure_ascii=False,
            )

        effective_topic = topic.strip() if isinstance(topic, str) and topic.strip() else "Update"
        sid = facade.upsert_task_card(
            effective_topic,
            summary.strip(),
            content=content,
            task=task,
            status=status,
            chat=chat,
            target_memory_id=target_id.strip() if isinstance(target_id, str) else None,
        )
        return json.dumps(
            {"memory_id": sid, "task_id": task, "created": False},
            ensure_ascii=False,
        )

    return ToolSpec(
        name="memory_task",
        description=(
            "Open, update, close, cancel, or reopen a multi-step L2 task card (not for one-turn "
            "Q&A or hypothetical ideas). Check <memory_tasks> first; only call "
            "memory_search(scope='tasks') if the user references an older/closed task missing "
            "from <memory_tasks>. Omit task to open a new task; pass task=<task_id> to update "
            "in-place or change status (open/done/cancelled). Use content for structured delta "
            "(- done / - processing / - next / - decisions) without repeating summary. Close as "
            "done on completion (including natural acks) or cancelled when abandoned; never close "
            "merely on a topic switch. Only opener/coordinator/user may close a task."
        ),
        parameters={
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "Short task title (required when opening a new task; omit on update unless renaming).",
                },
                "summary": {
                    "type": "string",
                    "description": "Concise current state or outcome written as the main bullet.",
                },
                "content": {
                    "type": "string",
                    "description": "Structured delta (- done / - processing / - next / - decisions); do not duplicate summary.",
                },
                "task": {
                    "type": "string",
                    "description": "Existing task-<8 hex> to update/close/reopen; omit to open a new task.",
                },
                "status": {
                    "type": "string",
                    "enum": list(TASK_STATUSES),
                    "description": "open (default on new), done when finished, cancelled when abandoned.",
                },
                "memory_id": {
                    "type": "string",
                    "description": "Optional target card memory_id when updating a specific card of task.",
                },
            },
            "required": ["summary"],
            "additionalProperties": False,
        },
        handler=handler,
        parallel_safe=False,
        resource_key=_daily_key,
    )


def _forget_spec(facade: MemoryFacade) -> ToolSpec:
    async def handler(args: dict) -> str:
        facade.forget(args["memory_id"])
        return "forgotten"

    return ToolSpec(
        name="memory_forget",
        description=(
            "Permanently delete one L2 memory entry by memory_id. Irreversible: require explicit "
            "user confirmation naming the target before calling. Never call for vague bulk-delete "
            "requests (ask the user to confirm specific targets first), and do not use to edit "
            "USER.md profile entries."
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
