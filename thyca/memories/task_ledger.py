"""Task ledger operations: catalog building, in-place upserts, ownership, prompt formatting."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from thyca.memories.active import visible_to
from thyca.memories.archive_store import DATE_RE, ArchiveError, decode_bots, encode_bots
from thyca.memories.heading import (
    TASK_DEFAULT_IMPORTANCE,
    TASK_STATUSES,
    format_body,
    format_ts,
    is_visible,
    memory_id,
    read_text_file,
    utc_now,
    validate_task_meta,
)
from thyca.memories.identity import current_bot_id
from thyca.memories.ledger_common import (
    TASK_UPDATED_DAY_RE,
    extract_task_updated_day,
    first_summary_line,
    locate_visible_block,
    today_blocks,
    validate_entry_fields,
)
from thyca.memories.search import _absolute_proj

_GENERIC_TASK_TOPICS = frozenset(
    {"update", "progress", "checkpoint", "status", "task", "task update"}
)
_TASK_DECISION_LINE_RE = re.compile(r"^\s*-\s*decisions?\s*:", re.IGNORECASE)
HOT_TASK_MAX_AGE_DAYS = 7
HOT_TASK_PROMPT_LIMIT = 8


def build_task_catalog(
    facade,
    *,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 20,
    now: datetime | None = None,
    allow_own_cross_window: bool = False,
) -> dict:
    """Group visible task entries into a task catalog (phase 1A).

    Returns ``{"tasks": [...], "warnings": [...]}``. A child bot
    only sees entries it may read, so a task made solely of another
    bot's entries never appears for it."""
    warnings: list[str] = []
    if status is not None and status not in TASK_STATUSES:
        raise ValueError(
            f"status must be one of {list(TASK_STATUSES)}, got {status!r}"
        )
    for name, value in (("date_from", date_from), ("date_to", date_to)):
        if value is not None and (
            not isinstance(value, str) or DATE_RE.fullmatch(value) is None
        ):
            raise ValueError(f"{name} must be YYYY-MM-DD, got {value!r}")
    requested_limit = limit
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(limit, 50))
    if limit != requested_limit:
        warnings.append(f"limit clamped from {requested_limit} to {limit}")
    facade._ensure_rollover_indexed(now)
    now_ts = format_ts(utc_now(now))
    reader = current_bot_id()
    rows = facade.archive.store.task_memory_rows(now_ts, reader=reader)
    members_by_session: dict[str, dict] = {}
    for row in rows:
        keys = row.keys()
        origin = row["chat"] if "chat" in keys else None
        if not facade._entry_readable(
            origin, row["bots"], allow_own_cross_window=allow_own_cross_window
        ):
            continue
        row_text = str(row["text_raw"] or "") if "text_raw" in keys else None
        updated_day = extract_task_updated_day(row_text)
        row_last_day = (
            max(str(row["last_day"]), updated_day)
            if updated_day
            else str(row["last_day"])
        )
        member = {
            "task": str(row["task"]),
            "memory_id": str(row["memory_id"]),
            "first_day": str(row["first_day"]),
            "last_day": row_last_day,
            "first_line": int(row["first_line"]),
            "last_line": int(row["last_line"]),
            "title": str(row["title"]),
            "status": row["status"],
            "bots": row["bots"],
            "chat": origin,
        }
        if row_text is not None:
            member["summary"] = first_summary_line(row_text)
        members_by_session[str(row["memory_id"])] = member
    today = facade.archive.day(now)
    try:
        today_text = read_text_file(facade.thyca_dir / "memory" / f"{today}.md")
    except (OSError, UnicodeDecodeError):
        today_text = None
    if today_text:
        today_chunks = facade.archive.chunker.chunk_markdown(
            str(facade.thyca_dir / "memory" / f"{today}.md"),
            today_text,
            source_kind="daily",
            timeline_day=today,
        )
        by_session: dict[str, list] = {}
        for chunk in today_chunks:
            if chunk.task is None or chunk.memory_id in members_by_session:
                continue
            if not facade._entry_readable(
                getattr(chunk, "chat_session", None),
                encode_bots(chunk.bots),
                allow_own_cross_window=allow_own_cross_window,
            ):
                continue
            by_session.setdefault(chunk.memory_id, []).append(chunk)
        for sid, leaves in by_session.items():
            first = leaves[0]
            if not is_visible(first.expires_at, now):
                continue
            owners = first.bots
            if (
                reader is not None
                and reader != "thyca"
                and owners is not None
                and "team" not in owners
                and reader not in owners
            ):
                continue
            today_body = "\n".join(leaf.text_raw for leaf in leaves)
            updated_day = extract_task_updated_day(today_body)
            member_last_day = max(today, updated_day) if updated_day else today
            members_by_session[sid] = {
                "task": str(first.task),
                "memory_id": sid,
                "first_day": today,
                "last_day": member_last_day,
                "first_line": min(leaf.line_start for leaf in leaves),
                "last_line": max(leaf.line_start for leaf in leaves),
                "title": first.memory_title,
                "status": first.status,
                "bots": encode_bots(first.bots),
                "chat": getattr(first, "chat_session", None),
                "summary": first_summary_line(first.text_raw),
            }
    grouped: dict[str, list] = {}
    for member in members_by_session.values():
        grouped.setdefault(member["task"], []).append(member)
    catalog = []
    for task_id, members in grouped.items():
        ordered = sorted(
            members,
            key=lambda r: (str(r["first_day"]), int(r["first_line"])),
        )
        latest = max(
            members,
            key=lambda r: (str(r["last_day"]), str(r["first_day"]), int(r["last_line"])),
        )
        latest_status = "open"
        latest_memory_id = str(latest["memory_id"])
        for member in sorted(
            members,
            key=lambda r: (str(r["last_day"]), str(r["first_day"]), int(r["last_line"])),
            reverse=True,
        ):
            candidate = member["status"]
            if isinstance(candidate, str) and candidate:
                latest_status = candidate
                break
        if status is not None and latest_status != status:
            continue
        first_day = str(ordered[0]["first_day"])
        last_day = str(latest["last_day"])
        if date_from is not None and last_day < date_from:
            continue
        if date_to is not None and first_day > date_to:
            continue
        owners_set: set[str] = set()
        for member in members:
            decoded = decode_bots(member["bots"])
            if decoded:
                owners_set.update(decoded)
            else:
                owners_set.add("team")
        latest_summary = latest.get("summary")
        if latest_summary is None:
            mem_rows = facade.archive.store.get_memory(latest_memory_id, now_ts)
            latest_summary = (
                first_summary_line(str(mem_rows[0]["text_raw"]))
                if mem_rows
                else ""
            )
        catalog.append(
            {
                "task_id": task_id,
                "title": str(ordered[0]["title"]),
                "status": latest_status,
                "latest_memory_id": latest_memory_id,
                "entry_count": len(members),
                "first_day": first_day,
                "last_day": last_day,
                "bots": sorted(owners_set),
                "chat": latest.get("chat"),
                "summary": latest_summary,
            }
        )
    catalog.sort(key=lambda item: item["last_day"], reverse=True)
    return {"tasks": catalog[:limit], "warnings": warnings}


def _find_task_members(
    facade, task: str, now: datetime | None
) -> list[tuple[tuple[str, str, int], str]]:
    """All visible ``(sort_key, memory_id)`` entries for ``task`` without catalog cap."""
    facade.archive.reindex(now)
    now_ts = format_ts(utc_now(now))
    reader = current_bot_id()
    seen: dict[str, tuple[str, str, int]] = {}
    for row in facade.archive.store.task_memory_rows(now_ts, reader=reader):
        if str(row["task"]) != task:
            continue
        keys = row.keys()
        origin = row["chat"] if "chat" in keys else None
        if not facade._entry_readable(origin, row["bots"], allow_own_cross_window=True):
            continue
        row_text = str(row["text_raw"] or "") if "text_raw" in keys else None
        updated_day = extract_task_updated_day(row_text)
        row_last_day = (
            max(str(row["last_day"]), updated_day)
            if updated_day
            else str(row["last_day"])
        )
        sid = str(row["memory_id"])
        seen[sid] = (row_last_day, str(row["first_day"]), int(row["last_line"]))
    today_str, _path, lines, blocks = today_blocks(facade, now)
    for meta, resolved, start, end in blocks:
        if meta.task != task or not is_visible(meta.expires_at, now):
            continue
        if not visible_to(meta.bots, reader):
            continue
        if not facade._entry_readable(
            meta.chat, encode_bots(meta.bots), allow_own_cross_window=True
        ):
            continue
        sid = memory_id(today_str, resolved)
        if sid in seen:
            continue
        body_text = "".join(lines[start + 1 : end])
        updated_day = extract_task_updated_day(body_text)
        member_last_day = max(today_str, updated_day) if updated_day else today_str
        seen[sid] = (member_last_day, today_str, start + 1)
    return sorted(((key, sid) for sid, key in seen.items()), key=lambda pair: pair[0])


def check_task_close_allowed(facade, task: str | None, now: datetime | None) -> None:
    """Enforce that only the task opener, Thyca coordinator, or user may close a task."""
    if not task:
        return
    caller = current_bot_id()
    if caller in (None, "thyca"):
        return
    now_ts = format_ts(utc_now(now))
    openers: list[tuple[str, int, str, tuple[str, ...] | None]] = []
    for row in facade.archive.store.task_memory_rows(now_ts, reader=None):
        if str(row["task"]) != task:
            continue
        openers.append((
            str(row["first_day"]),
            int(row["first_line"]),
            str(row["memory_id"]),
            decode_bots(row["bots"]),
        ))
    today_str, _path, _lines, blocks = today_blocks(facade, now)
    for meta, resolved, start, _end in blocks:
        if meta.task == task and is_visible(meta.expires_at, now):
            openers.append((today_str, start + 1, memory_id(today_str, resolved), meta.bots))
    if not openers:
        return
    openers.sort(key=lambda item: (item[0], item[1], item[2]))
    opener_bots = openers[0][3]
    if opener_bots is not None and caller in opener_bots and "team" not in opener_bots:
        return
    raise ArchiveError(f"bot '{caller}' cannot close another owner's task: {task}")


def _merge_preserved_decisions(
    new_lines: list[str], existing_lines: list[str]
) -> list[str]:
    """Preserve prior ``- decisions: ...`` lines when an in-place update omits them."""
    if any(_TASK_DECISION_LINE_RE.match(line) for line in new_lines):
        return new_lines
    prior_decisions = [
        line for line in existing_lines if _TASK_DECISION_LINE_RE.match(line)
    ]
    if not prior_decisions or not new_lines:
        return new_lines
    return [new_lines[0], *prior_decisions, *new_lines[1:]]


def upsert_task_card(
    facade,
    topic: str,
    summary: str,
    content: str = "",
    *,
    task: str,
    status: str | None = None,
    importance: int | None = None,
    bots: list[str] | tuple[str, ...] | None = None,
    chat: str | None = None,
    proj: str | None = None,
    now: datetime | None = None,
    target_memory_id: str | None = None,
) -> str:
    """Update an existing task card in-place or create a new checkpoint card."""
    effective_importance = (
        TASK_DEFAULT_IMPORTANCE if importance is None else importance
    )
    validate_entry_fields(topic, summary, content, effective_importance)
    validate_task_meta(task, None, None, status, None)
    normalized_proj = _absolute_proj(proj)
    clean_chat = chat.strip() if isinstance(chat, str) and chat.strip() else None
    with facade.writer.mutation_lock():
        members = _find_task_members(facade, task, now)
        if target_memory_id is None and members:
            target_memory_id = members[-1][1]
        located = locate_visible_block(facade, target_memory_id, now)
        existing_meta = located[0] if located is not None else None
        existing_body = located[1] if located is not None else []
        if status in ("done", "cancelled"):
            check_task_close_allowed(facade, task, now)
        same_window = (
            existing_meta is not None
            and (
                clean_chat is None
                or existing_meta.chat is None
                or existing_meta.chat == clean_chat
            )
        )
        if (
            existing_meta is not None
            and target_memory_id is not None
            and existing_meta.task in (None, task)
            and same_window
            and facade._may_mutate(target_memory_id, now)
        ):
            moment = utc_now(now)
            hour = moment.astimezone(facade.archive.zone()).strftime("%H:%M")
            today_str = facade.archive.day(now)
            origin_day = target_memory_id.split("#", 1)[0]
            clean_topic = topic.strip()
            new_topic = (
                None
                if existing_meta.title
                and clean_topic.casefold() in _GENERIC_TASK_TOPICS
                else clean_topic
            )
            new_kind = None if existing_meta.kind == "habit" else "checkpoint"
            new_status = (
                status
                if status is not None
                else (existing_meta.status or "open")
            )
            body_lines = [
                line
                for line in format_body(summary, content)
                if not TASK_UPDATED_DAY_RE.match(line)
            ]
            body_lines = _merge_preserved_decisions(body_lines, existing_body)
            if today_str > origin_day:
                body_lines.append(f"  - updated: {today_str}")
            facade.writer.update_memory(
                target_memory_id,
                time=hour,
                topic=new_topic,
                body_lines=body_lines,
                proj=normalized_proj,
                task=task,
                kind=new_kind,
                status=new_status,
            )
            facade.writer.reinforce(
                target_memory_id,
                importance=max(existing_meta.importance, effective_importance),
                now=now,
            )
            facade._refresh_index(now)
            return target_memory_id
        closing_importance = effective_importance
        if status in ("done", "cancelled"):
            max_member_imp = existing_meta.importance if existing_meta is not None else 0
            for _key, member_sid in members:
                block = locate_visible_block(facade, member_sid, now)
                if block is not None:
                    max_member_imp = max(max_member_imp, block[0].importance)
            closing_importance = max(closing_importance, max_member_imp)
        cont = target_memory_id if existing_meta is not None else None
        return facade.remember(
            topic,
            summary,
            content=content,
            importance=closing_importance,
            now=now,
            proj=proj,
            chat=chat,
            bots=bots,
            task=task,
            cont=cont,
            kind="checkpoint",
            status=status or "open",
        )


def format_task_catalog(
    entries: list[dict],
    *,
    limit: int = 20,
    reference_day: str | None = None,
    max_age_days: int = HOT_TASK_MAX_AGE_DAYS,
) -> str:
    """Render open memory tasks as prompt index lines (phase 1C).

    One line per hot open task: ``- <task_id>: <title ≤80 chars> · last active
    <last_day> · bots <a, b> [<latest_memory_id>] — <summary>``.
    Closed (done/cancelled) rows are omitted. When ``reference_day`` is given,
    open tasks idle longer than ``max_age_days`` (or beyond ``limit``) are
    folded into a single footer line so dormant tasks never bloat the prompt.
    """
    cap = max(0, limit)
    cutoff_day: str | None = None
    if reference_day:
        try:
            ref = date.fromisoformat(reference_day)
            cutoff_day = (ref - timedelta(days=max(0, max_age_days))).isoformat()
        except ValueError:
            cutoff_day = None
    lines: list[str] = []
    dormant_count = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("status") in ("done", "cancelled"):
            continue
        last_day = str(entry.get("last_day") or "")
        if cutoff_day is not None and last_day and last_day < cutoff_day:
            dormant_count += 1
            continue
        if len(lines) >= cap:
            dormant_count += 1
            continue
        title = str(entry.get("title", ""))
        if len(title) > 80:
            title = title[:79].rstrip() + "…"
        bots = entry.get("bots") or []
        line = (
            f"- {entry.get('task_id', '?')}: {title} · "
            f"last active {entry.get('last_day', '?')} · "
            f"bots {', '.join(str(b) for b in bots)}"
        )
        latest_id = entry.get("latest_memory_id")
        if isinstance(latest_id, str) and latest_id:
            line += f" [{latest_id}]"
        summary = " ".join(str(entry.get("summary") or "").split())
        if summary:
            if len(summary) > 120:
                summary = summary[:119].rstrip() + "…"
            line += f" — {summary}"
        lines.append(line)
    if dormant_count > 0:
        lines.append(
            f"(+{dormant_count} older open tasks — call memory_search(scope='tasks') if user brings them up)"
        )
    return "\n".join(lines)
