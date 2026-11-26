"""Thyca memory sidecar job (BE-4): tool-driven delta extraction.

Not a separate bot: a hook surface the app layer calls before compaction and
when a session idles. Uses the exact same built-in ``memory_note`` and
``memory_task`` tool schemas, descriptions, and write handlers as live turns
(single source of truth, single round, no parallel JSON schema).
"""
from __future__ import annotations

import hashlib
import json

from thyca.core.protocol import Message, ToolCall, parse_ts
from thyca.memories.identity import bind_bot_id, reset_bot_id
from thyca.tools.memory_tools import (
    bind_chat_conversation,
    reset_chat_conversation,
    sidecar_memory_tools,
)
from thyca.tools.registry import ToolRegistry

IDLE_SWEEP_SECONDS = 15 * 60
_CONTEXT_LINE_MAX_CHARS = 400
_SIDECAR_TOOLS = ("memory_note", "memory_task")


class ExtractionError(RuntimeError):
    """Memory extraction failed (provider error)."""


def should_idle_sweep(last_activity_ts: str, now_ts: str, idle_s: int = IDLE_SWEEP_SECONDS) -> bool:
    """Pure idle decision: True when the session quieted past ``idle_s``."""

    try:
        last = parse_ts(last_activity_ts)
        now = parse_ts(now_ts)
    except ValueError:
        return False
    return (now - last).total_seconds() >= idle_s


def _extractable_messages(messages: list[Message]) -> list[Message]:
    rows: list[Message] = []
    for message in messages:
        if message.role not in ("user", "assistant"):
            continue
        if (message.meta or {}).get("kind") == "compaction":
            continue
        if (message.content or "").strip():
            rows.append(message)
    return rows


def _row_cursor(rows: list[Message], idx: int) -> tuple[str, str]:
    msg = rows[idx]
    prev = rows[idx - 1] if idx > 0 else None
    span = str((msg.meta or {}).get("span_id") or "")
    parts = [
        prev.role if prev is not None else "",
        prev.ts or "" if prev is not None else "",
        prev.content or "" if prev is not None else "",
        msg.role or "",
        msg.ts or "",
        msg.content or "",
        span,
    ]
    digest = hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:16]
    return (msg.ts or "", digest)


def latest_extraction_cursor(messages: list[Message]) -> tuple[str, str] | None:
    """Watermark of the newest extractable user/assistant message, or None."""
    rows = _extractable_messages(messages)
    return _row_cursor(rows, len(rows) - 1) if rows else None


def _split_after_cursor(
    rows: list[Message], after_cursor: tuple[str, str]
) -> tuple[list[Message], list[Message]]:
    for idx in range(len(rows) - 1, -1, -1):
        if _row_cursor(rows, idx) == after_cursor:
            return rows[: idx + 1], rows[idx + 1 :]
    cursor_ts = after_cursor[0]
    if not cursor_ts:
        return [], rows
    try:
        cursor_dt = parse_ts(cursor_ts)
    except ValueError:
        return [], rows
    cut = -1
    for idx, msg in enumerate(rows):
        if not msg.ts:
            continue
        try:
            if parse_ts(msg.ts) <= cursor_dt:
                cut = idx
        except ValueError:
            continue
    if cut < 0:
        return [], rows
    return rows[: cut + 1], rows[cut + 1 :]


def transcript_tail(
    messages: list[Message],
    max_chars: int = 12000,
    *,
    after_cursor: tuple[str, str] | None = None,
    context_messages: int = 2,
) -> str:
    """Newest-first-capped text of user/assistant rows for extraction.

    When ``after_cursor`` is supplied, only messages after that watermark are
    emitted as extractable delta (empty string when nothing new arrived), with
    up to ``context_messages`` preceding rows tagged as read-only context.
    """
    rows = _extractable_messages(messages)
    if after_cursor is None:
        out = "\n".join(f"[{m.role} {m.ts}] {m.content}" for m in rows)
        return out if len(out) <= max_chars else out[-max_chars:]
    prior, delta = _split_after_cursor(rows, after_cursor)
    if not delta:
        return ""
    ctx_block = ""
    if prior and context_messages > 0:
        ctx_lines: list[str] = []
        for msg in prior[-context_messages:]:
            text = (msg.content or "").strip()
            if len(text) > _CONTEXT_LINE_MAX_CHARS:
                text = text[: _CONTEXT_LINE_MAX_CHARS - 1].rstrip() + "…"
            ctx_lines.append(f"[{msg.role} {msg.ts}] {text}")
        ctx_block = (
            "RECENT CONTEXT (ALREADY EXTRACTED — DO NOT RE-EXTRACT):\n"
            + "\n".join(ctx_lines)
            + "\n\nNEW TRANSCRIPT:\n"
        )
    delta_budget = max(512, max_chars - len(ctx_block))
    delta_text = "\n".join(f"[{m.role} {m.ts}] {m.content}" for m in delta)
    if len(delta_text) > delta_budget:
        delta_text = delta_text[-delta_budget:]
    return ctx_block + delta_text


def _open_tasks_block(open_tasks) -> str:
    """Prompt block listing open memory tasks for task-linking."""
    lines = []
    for entry in open_tasks or ():
        if not isinstance(entry, dict):
            continue
        task_id = entry.get("task_id")
        if not task_id:
            continue
        summary = " ".join(str(entry.get("summary", "") or "").split())
        if len(summary) > 160:
            summary = summary[:159].rstrip() + "…"
        line = (
            f"- {task_id} — {entry.get('title', '')} "
            f"(latest entry {entry.get('latest_memory_id', '?')}, "
            f"last active {entry.get('last_day', '?')})"
        )
        if summary:
            line += f" — {summary}"
        lines.append(line)
    if not lines:
        return "OPEN MEMORY TASKS: none."
    return "OPEN MEMORY TASKS:\n" + "\n".join(lines)


def _today_notes_block(today_entries) -> str:
    """Prompt block listing today's existing habit/other notes for delta extraction."""
    lines = []
    for entry in today_entries or ():
        if not isinstance(entry, dict) or entry.get("task") is not None:
            continue
        mid = entry.get("memory_id", "?")
        kind = entry.get("kind")
        habit = entry.get("habit")
        tag = f"habit: {habit}" if kind == "habit" and habit else "other"
        title = str(entry.get("title", "")).strip()
        body = " ".join(str(entry.get("body", "")).split())
        if len(body) > 250:
            body = body[:249].rstrip() + "…"
        lines.append(
            f"- [{mid}] ({tag}) {title}: {body}" if body else f"- [{mid}] ({tag}) {title}"
        )
    if not lines:
        return "TODAY'S NOTES: none."
    return "TODAY'S NOTES:\n" + "\n".join(lines)


def _today_closed_tasks_block(today_entries) -> str:
    """Prompt block listing tasks already closed/cancelled today so sidecar does not recreate them."""
    lines = []
    for entry in today_entries or ():
        if not isinstance(entry, dict) or not entry.get("task"):
            continue
        status = entry.get("status")
        if status not in ("done", "cancelled"):
            continue
        tid = entry.get("task")
        title = str(entry.get("title", "")).strip()
        body = " ".join(str(entry.get("body", "")).split())
        if len(body) > 160:
            body = body[:159].rstrip() + "…"
        line = f"- {tid} ({status}) — {title}"
        if body:
            line += f": {body}"
        lines.append(line)
    if not lines:
        return "TODAY'S CLOSED TASKS: none."
    return "TODAY'S CLOSED TASKS (ALREADY DONE/CANCELLED — DO NOT RE-CREATE):\n" + "\n".join(lines)


def _known_habits_block(known_habits) -> str:
    """Prompt block listing known habit labels so extraction reuses canonical labels."""
    labels = [
        " ".join(str(label).split())
        for label in (known_habits or ())
        if isinstance(label, str) and label.strip()
    ]
    if not labels:
        return "KNOWN HABIT LABELS: none."
    return "KNOWN HABIT LABELS: " + ", ".join(labels)


def extraction_prompt(
    transcript: str,
    *,
    open_tasks=(),
    today_entries=(),
    known_habits=(),
) -> str:
    """Build the single-turn tool-calling prompt for sidecar memory extraction."""
    return (
        "You extract durable memories from a chat transcript by calling "
        "memory_note and/or memory_task tools (call no tools when nothing is worth keeping).\n"
        "RULES:\n"
        "1. LANGUAGE: Write every tool argument (topic, summary, content, habit) in the "
        "exact primary language of the transcript (never translate Vietnamese to English).\n"
        "2. DELTA ONLY: Do NOT re-record facts, habits, or task progress already covered "
        "in OPEN MEMORY TASKS, TODAY'S CLOSED TASKS, or TODAY'S NOTES. If RECENT CONTEXT "
        "(ALREADY EXTRACTED) is shown, use it only to resolve references and extract ONLY "
        "from NEW TRANSCRIPT.\n"
        "3. REVISE IN-PLACE: If the transcript revises a same-day draft or workflow already "
        "in TODAY'S NOTES, call memory_note with that entry's memory_id to update it in-place "
        "instead of creating a duplicate.\n"
        "4. SKIP NOISE: Never save static bot persona/lore (TWIN.md/SOUL.md), team roster, "
        "default model/runtime configs, bot self-introductions, small talk, volatile "
        "web-search/news/benchmark/price dumps, or past archived facts that the assistant "
        "merely recalled/cited from memory to answer a lookup question. Keep entries concise; "
        "group related facts.\n"
        "5. HABITS: Use memory_note(habit=..., explicit=...) for recurring preferences, "
        "formatting rules (e.g. URL wrapping, plain-text layout), or workflow conventions; "
        "reuse an exact label from KNOWN HABIT LABELS when matching.\n"
        "6. TASKS: Use memory_task with an existing task id from OPEN MEMORY TASKS for "
        "genuine progress, decisions, or done/cancelled closure; omit task only for a new "
        "multi-step goal. Never create a new task for work already in TODAY'S CLOSED TASKS. "
        "NEVER attach stable facts to an open task.\n\n"
        f"{_open_tasks_block(open_tasks)}\n\n"
        f"{_today_closed_tasks_block(today_entries)}\n\n"
        f"{_today_notes_block(today_entries)}\n\n"
        f"{_known_habits_block(known_habits)}\n\n"
        f"TRANSCRIPT:\n{transcript}"
    )


async def apply_memory_tool_calls(
    registry: ToolRegistry,
    calls: list[ToolCall],
    *,
    bot_id: str = "thyca",
    open_tasks=(),
    today_entries=(),
    chat: str | None = None,
) -> dict:
    """Execute sidecar ``memory_note`` / ``memory_task`` tool calls under ``bot_id``."""
    catalog = [entry for entry in (open_tasks or ()) if isinstance(entry, dict)]
    known = {str(entry["task_id"]): entry for entry in catalog if entry.get("task_id")}
    open_by_title = {
        " ".join(str(entry.get("title", "")).strip().split()).casefold(): str(entry["task_id"])
        for entry in catalog
        if entry.get("task_id") and str(entry.get("title", "")).strip()
    }
    closed_today_titles = {
        " ".join(str(entry.get("title", "")).strip().split()).casefold()
        for entry in (today_entries or ())
        if isinstance(entry, dict)
        and entry.get("task")
        and entry.get("status") in ("done", "cancelled")
        and str(entry.get("title", "")).strip()
    }
    minted: dict[str, str] = {}
    last_written_task: dict[str, str] = {}
    written: list[str] = []

    bot_tok = bind_bot_id(bot_id)
    chat_tok = bind_chat_conversation(chat)
    try:
        for call in calls:
            name = getattr(call, "name", None)
            if name not in _SIDECAR_TOOLS:
                continue
            spec = registry.get(name)
            if spec is None:
                continue
            raw_args = getattr(call, "arguments", None)
            if not isinstance(raw_args, dict):
                continue
            args = dict(raw_args)

            if name == "memory_task":
                task_id = args.get("task")
                if task_id is not None:
                    if not isinstance(task_id, str) or task_id not in known:
                        continue
                    if not args.get("memory_id"):
                        target = last_written_task.get(task_id) or known[task_id].get(
                            "latest_memory_id"
                        )
                        if isinstance(target, str) and target.strip():
                            args["memory_id"] = target.strip()
                else:
                    raw_topic = args.get("topic")
                    if not isinstance(raw_topic, str) or not raw_topic.strip():
                        continue
                    key = " ".join(raw_topic.strip().split()).casefold()
                    if key in closed_today_titles:
                        continue
                    if key in open_by_title:
                        task_id = open_by_title[key]
                        args["task"] = task_id
                        target = last_written_task.get(task_id) or known[task_id].get(
                            "latest_memory_id"
                        )
                        if isinstance(target, str) and target.strip():
                            args["memory_id"] = target.strip()
                    elif key in minted:
                        task_id = minted[key]
                        args["task"] = task_id
                        target = last_written_task.get(task_id)
                        if target:
                            args["memory_id"] = target

            try:
                raw_out = await spec.handler(args)
            except ValueError:
                continue

            try:
                payload = json.loads(raw_out) if isinstance(raw_out, str) else {}
            except ValueError:
                payload = {}
            if not isinstance(payload, dict):
                continue
            mid = payload.get("memory_id")
            if isinstance(mid, str) and mid and mid not in written:
                written.append(mid)
            if name == "memory_task":
                out_tid = payload.get("task_id")
                if isinstance(out_tid, str) and out_tid:
                    if payload.get("created") and isinstance(args.get("topic"), str):
                        key = " ".join(args["topic"].strip().split()).casefold()
                        minted[key] = out_tid
                        known[out_tid] = {"task_id": out_tid, "latest_memory_id": mid}
                    if isinstance(mid, str) and mid:
                        last_written_task[out_tid] = mid
    finally:
        reset_chat_conversation(chat_tok)
        reset_bot_id(bot_tok)
    return {"written": written}


async def extract_memories(
    connect,
    facade,
    transcript: str,
    *,
    bot_id: str = "thyca",
    open_tasks=(),
    today_entries=(),
    known_habits=(),
    chat: str | None = None,
    strict: bool = False,
) -> dict:
    """Single-round tool-calling extraction using built-in memory_note/memory_task."""
    registry = sidecar_memory_tools(facade)
    prompt = extraction_prompt(
        transcript,
        open_tasks=open_tasks,
        today_entries=today_entries,
        known_habits=known_habits,
    )
    try:
        reply = await connect.chat(
            [Message(role="user", content=prompt)],
            registry.to_openai_schema(),
        )
    except Exception as exc:
        if strict:
            raise ExtractionError(f"extractor chat failed: {type(exc).__name__}") from exc
        return {"written": []}
    calls = list(getattr(reply, "tool_calls", None) or ())
    if not calls:
        return {"written": []}
    return await apply_memory_tool_calls(
        registry,
        calls,
        bot_id=bot_id,
        open_tasks=open_tasks,
        today_entries=today_entries,
        chat=chat,
    )
