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
from thyca.memories.ledger_common import (
    DuplicateCandidateError,
    is_strict_label_overlap,
    label_key,
)
from thyca.memories.task_ledger import match_open_tasks
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
        "always scan KNOWN HABIT LABELS by meaning and reuse an exact existing label when matching.\n"
        "6. TASKS: Always scan OPEN MEMORY TASKS by meaning first and pass that exact task id "
        "to memory_task for genuine progress, decisions, or done/cancelled closure; omit task "
        "only for a genuinely new multi-step goal. Never create a new task for work already in "
        "TODAY'S CLOSED TASKS. NEVER attach stable facts to an open task.\n\n"
        f"{_open_tasks_block(open_tasks)}\n\n"
        f"{_today_closed_tasks_block(today_entries)}\n\n"
        f"{_today_notes_block(today_entries)}\n\n"
        f"{_known_habits_block(known_habits)}\n\n"
        f"TRANSCRIPT:\n{transcript}"
    )


def _format_task_conflict_message(
    open_candidates: list[dict], closed_candidates: list[dict]
) -> str:
    lines: list[str] = []
    for entry in open_candidates[:3]:
        summary = str(entry.get("summary") or "")[:120]
        line = (
            f"- [OPEN] {entry['task_id']}: {entry.get('title', '')} "
            f"(last_day={entry.get('last_day', '?')})"
        )
        if summary:
            line += f" — {summary}"
        lines.append(line)
    for entry in closed_candidates[:3]:
        status = entry.get("status") or "done"
        body = " ".join(str(entry.get("body") or "").split())[:120]
        line = f"- [CLOSED:{status}] {entry.get('task', '?')}: {entry.get('title', '')}"
        if body:
            line += f" — {body}"
        lines.append(line)
    return (
        "Possible duplicate of existing task(s):\n"
        + "\n".join(lines)
        + "\nPass task='<task_id>' to update an OPEN task, omit the call if already "
        "covered by a CLOSED task, or retry with force_new=true if this is a distinct new task."
    )


def _format_habit_conflict_message(labels: list[str]) -> str:
    lines = [f"- habit={label!r}" for label in labels[:3]]
    return (
        "Possible duplicate of existing habit(s):\n"
        + "\n".join(lines)
        + "\nReuse the exact existing habit label to reinforce it, or retry with "
        "force_new=true if this is a distinct habit."
    )


def _conflict_resolution_prompt(conflicts: list[dict[str, object]]) -> str:
    blocks: list[str] = []
    for index, item in enumerate(conflicts, start=1):
        call = item["call"]
        name = getattr(call, "name", "")
        raw_args = getattr(call, "arguments", {})
        args_json = json.dumps(raw_args, ensure_ascii=False)
        blocks.append(f"{index}. Proposed call: {name}({args_json})\n   Warning:\n   {item['message']}")
    return (
        "Some of your proposed memory tool calls may duplicate existing open/closed tasks "
        "or known habits.\n"
        "Resolve ONLY the items listed below (all other calls were already applied):\n"
        "- If it belongs to an OPEN task, call memory_task with that exact task='<task_id>'.\n"
        "- If it matches an existing habit, call memory_note with that exact habit label.\n"
        "- If it is genuinely a distinct new task or habit, call the tool again with force_new=true.\n"
        "- If it is already covered by a CLOSED task or duplicate, call no tool for it.\n\n"
        + "\n\n".join(blocks)
    )


async def apply_memory_tool_calls(
    registry: ToolRegistry,
    calls: list[ToolCall],
    *,
    bot_id: str = "thyca",
    open_tasks=(),
    today_entries=(),
    known_habits=(),
    chat: str | None = None,
    collect_conflicts: list[dict[str, object]] | None = None,
) -> dict:
    """Execute sidecar ``memory_note`` / ``memory_task`` tool calls under ``bot_id``."""
    catalog = [entry for entry in (open_tasks or ()) if isinstance(entry, dict)]
    known = {str(entry["task_id"]): entry for entry in catalog if entry.get("task_id")}
    open_by_title: dict[str, list[str]] = {}
    for entry in catalog:
        tid = entry.get("task_id")
        title = str(entry.get("title", "")).strip()
        if tid and title:
            key = label_key(title)
            if key:
                open_by_title.setdefault(key, []).append(str(tid))
    closed_today_entries = [
        entry
        for entry in (today_entries or ())
        if isinstance(entry, dict)
        and entry.get("task")
        and entry.get("status") in ("done", "cancelled")
        and str(entry.get("title", "")).strip()
    ]
    closed_today_titles = {
        label_key(str(entry.get("title", "")))
        for entry in closed_today_entries
    } - {""}
    known_habit_list = [
        " ".join(str(label).split())
        for label in (known_habits or ())
        if isinstance(label, str) and label.strip()
    ]
    known_habit_keys = {label_key(label): label for label in known_habit_list if label_key(label)}
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
                    key = label_key(raw_topic)
                    if args.get("force_new") is not True:
                        if key in closed_today_titles:
                            continue
                        matched_open_ids = open_by_title.get(key, [])
                        if len(matched_open_ids) == 1:
                            task_id = matched_open_ids[0]
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
                        else:
                            exact_open, similar_open = match_open_tasks(raw_topic, catalog)
                            open_candidates = exact_open if len(exact_open) > 1 else similar_open
                            closed_candidates = [
                                entry
                                for entry in closed_today_entries
                                if is_strict_label_overlap(
                                    raw_topic,
                                    str(entry.get("title") or ""),
                                    candidate_extra=str(entry.get("body") or ""),
                                )
                            ]
                            if open_candidates or closed_candidates:
                                if collect_conflicts is not None:
                                    collect_conflicts.append(
                                        {
                                            "call": call,
                                            "message": _format_task_conflict_message(
                                                open_candidates, closed_candidates
                                            ),
                                        }
                                    )
                                continue
            elif (
                name == "memory_note"
                and args.get("memory_id") is None
                and isinstance(args.get("habit"), str)
                and args["habit"].strip()
                and args.get("force_new") is not True
            ):
                raw_habit = args["habit"].strip()
                h_key = label_key(raw_habit)
                if h_key not in known_habit_keys:
                    similar_labels = [
                        label
                        for label in known_habit_list
                        if is_strict_label_overlap(raw_habit, label)
                    ]
                    if similar_labels:
                        if collect_conflicts is not None:
                            collect_conflicts.append(
                                {
                                    "call": call,
                                    "message": _format_habit_conflict_message(similar_labels),
                                }
                            )
                        continue

            try:
                raw_out = await spec.handler(args)
            except DuplicateCandidateError as exc:
                if collect_conflicts is not None:
                    collect_conflicts.append({"call": call, "message": str(exc)})
                continue
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
                        topic_clean = args["topic"].strip()
                        key = label_key(topic_clean)
                        minted[key] = out_tid
                        new_entry = {
                            "task_id": out_tid,
                            "title": topic_clean,
                            "latest_memory_id": mid,
                            "last_day": (
                                mid.split("#", 1)[0]
                                if isinstance(mid, str) and "#" in mid
                                else "?"
                            ),
                            "summary": str(args.get("summary") or ""),
                        }
                        known[out_tid] = new_entry
                        catalog.append(new_entry)
                        if key:
                            open_by_title.setdefault(key, []).append(out_tid)
                    if isinstance(mid, str) and mid:
                        last_written_task[out_tid] = mid
            elif name == "memory_note" and isinstance(args.get("habit"), str) and args["habit"].strip():
                h_label = " ".join(args["habit"].split())
                h_key = label_key(h_label)
                if h_key and h_key not in known_habit_keys:
                    known_habit_keys[h_key] = h_label
                    known_habit_list.append(h_label)
    finally:
        reset_chat_conversation(chat_tok)
        reset_bot_id(bot_tok)
    return {"written": written}


def _merge_round2_open_tasks(open_tasks, facade, bot_id: str) -> list[dict]:
    merged: dict[str, dict] = {}
    for entry in open_tasks or ():
        if isinstance(entry, dict) and entry.get("task_id"):
            merged[str(entry["task_id"])] = entry
    token = bind_bot_id(bot_id)
    try:
        live = facade.tasks(
            status="open",
            limit=50,
            allow_own_cross_window=False,
        ).get("tasks", [])
    finally:
        reset_bot_id(token)
    for entry in live:
        if isinstance(entry, dict) and entry.get("task_id"):
            merged[str(entry["task_id"])] = entry
    return list(merged.values())


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
    """Tool-calling extraction using built-in memory_note/memory_task with 1-round conflict check."""
    registry = sidecar_memory_tools(facade)
    tools = registry.to_openai_schema()
    prompt = extraction_prompt(
        transcript,
        open_tasks=open_tasks,
        today_entries=today_entries,
        known_habits=known_habits,
    )
    try:
        reply = await connect.chat(
            [Message(role="user", content=prompt)],
            tools,
        )
    except Exception as exc:
        if strict:
            raise ExtractionError(f"extractor chat failed: {type(exc).__name__}") from exc
        return {"written": []}
    calls = list(getattr(reply, "tool_calls", None) or ())
    if not calls:
        return {"written": []}
    conflicts: list[dict[str, object]] = []
    first = await apply_memory_tool_calls(
        registry,
        calls,
        bot_id=bot_id,
        open_tasks=open_tasks,
        today_entries=today_entries,
        known_habits=known_habits,
        chat=chat,
        collect_conflicts=conflicts,
    )
    written = list(first["written"])
    if not conflicts:
        return {"written": written}
    resolve_prompt = _conflict_resolution_prompt(conflicts)
    try:
        followup = await connect.chat(
            [Message(role="user", content=resolve_prompt)],
            tools,
        )
    except Exception as exc:
        if strict:
            raise ExtractionError(f"extractor conflict chat failed: {type(exc).__name__}") from exc
        return {"written": written}
    followup_calls = list(getattr(followup, "tool_calls", None) or ())
    if not followup_calls:
        return {"written": written}
    round2_open_tasks = _merge_round2_open_tasks(open_tasks, facade, bot_id)
    second = await apply_memory_tool_calls(
        registry,
        followup_calls,
        bot_id=bot_id,
        open_tasks=round2_open_tasks,
        today_entries=today_entries,
        known_habits=known_habits,
        chat=chat,
        collect_conflicts=None,
    )
    for mid in second["written"]:
        if mid not in written:
            written.append(mid)
    return {"written": written}
