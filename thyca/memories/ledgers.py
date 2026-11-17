"""3-ledger L2 memory operations: Task cards, Habit observations, Other journal."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from rapidfuzz import fuzz

from thyca.memories.active import visible_to
from thyca.memories.archive_store import ArchiveError, decode_bots
from thyca.memories.chunk import Chunker
from thyca.memories.habits import OBS_DAY_RE
from thyca.memories.heading import (
    DEFAULT_IMPORTANCE,
    TTL_DAYS,
    HeadingMeta,
    format_body,
    format_ts,
    is_visible,
    iter_session_blocks,
    memory_id,
    read_text_file,
    utc_now,
    validate_task_meta,
)
from thyca.memories.identity import current_bot_id
from thyca.memories.search import _absolute_proj

_GENERIC_TASK_TOPICS = frozenset(
    {"update", "progress", "checkpoint", "status", "task", "task update"}
)
_WORD_SEP_RE = re.compile(r"[^\w]+", re.UNICODE)
_OTHER_LINE_PREFIX_RE = re.compile(
    r"^\s*[-*]\s+(?:\d{2}:\d{2}\s*[—\-]\s+(?:[^:]{1,80}:\s+)?)?"
)
TASK_UPDATED_DAY_RE = re.compile(
    r"^\s*-\s*updated:\s*(\d{4}-\d{2}-\d{2})\s*$", re.MULTILINE
)
_OTHER_FUZZY_MIN_CHARS = 12
_OTHER_FUZZY_RATIO = 88
_OTHER_FUZZY_MIN_LEN_RATIO = 0.55
_OTHER_TITLE_CAP = 80


def validate_entry_fields(
    topic: object, summary: object, content: object, importance: int
) -> None:
    """Validate common memory entry fields shared across all 3 ledgers."""
    if not isinstance(topic, str) or not topic.strip():
        raise ValueError("topic must be a non-empty string")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("summary must be a non-empty string")
    if not isinstance(content, str):
        raise ValueError("content must be a string")
    if importance not in TTL_DAYS:
        raise ValueError(f"importance must be 1..5, got {importance}")


def resolve_owners(
    bots: list[str] | tuple[str, ...] | None, reader: str | None
) -> tuple[str, ...] | None:
    """Owner list for a new entry (BE-4).

    Default is ``[self]`` for a bot turn, legacy ``None`` (team) for the
    user. An explicit list is validated; a child bot may never claim
    ``"team"`` — promotion goes through the candidate queue."""
    if bots is None:
        if reader is None:
            return None
        return (reader,)
    if not isinstance(bots, (list, tuple)) or not bots:
        raise ValueError("bots must be a non-empty list of bot ids")
    cleaned = []
    for item in bots:
        if not isinstance(item, str) or not item or len(item) > 64:
            raise ValueError("bots must be a non-empty list of bot ids")
        if item not in cleaned:
            cleaned.append(item)
    if reader is not None and reader != "thyca" and "team" in cleaned:
        raise ArchiveError(f"bot '{reader}' cannot write team memory directly")
    return tuple(cleaned)


def first_summary_line(text: str) -> str:
    """Return the first non-empty body line stripped of any leading ``- ``."""
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return first[2:].strip() if first.startswith("- ") else first


def norm_words(text: str) -> str:
    """Normalize accents, case, and punctuation into space-separated words."""
    return " ".join(_WORD_SEP_RE.sub(" ", Chunker.normalize(text)).split())


def extract_task_updated_day(text: str | None) -> str | None:
    """Latest ``updated: YYYY-MM-DD`` stamp recorded inside a task card body."""
    if not text:
        return None
    matches = TASK_UPDATED_DAY_RE.findall(text)
    return max(matches) if matches else None


def today_blocks(
    facade, now: datetime | None
) -> tuple[str, Path, list[str], list[tuple[HeadingMeta, str, int, int]]]:
    """Read and parse today's daily file blocks."""
    today_str = facade.archive.day(now)
    path = facade.thyca_dir / "memory" / f"{today_str}.md"
    try:
        text = read_text_file(path)
    except OSError:
        return today_str, path, [], []
    except UnicodeDecodeError as exc:
        raise ArchiveError(f"memory file is not valid UTF-8: {path}") from exc
    if not text:
        return today_str, path, [], []
    lines = text.splitlines(keepends=True)
    return today_str, path, lines, list(iter_session_blocks(lines, str(path)))


def locate_visible_block(
    facade, memory_id_str: str | None, now: datetime | None
) -> tuple[HeadingMeta, list[str]] | None:
    """Locate one visible memory block on disk and return ``(meta, body_lines)``."""
    if not memory_id_str:
        return None
    try:
        path, entry = facade.writer.locate(memory_id_str)
        text = read_text_file(path)
    except (ArchiveError, OSError, UnicodeDecodeError):
        return None
    if not text:
        return None
    lines = text.splitlines(keepends=True)
    for meta, resolved, start, end in iter_session_blocks(lines, str(path)):
        if resolved != entry:
            continue
        if not is_visible(meta.expires_at, now):
            return None
        body = [
            line.rstrip("\r\n")
            for line in lines[start + 1 : end]
            if line.strip()
        ]
        return meta, body
    return None


def today_entries(
    facade,
    *,
    now: datetime | None = None,
    chat: str | None = None,
    reader: str | None = None,
) -> list[dict]:
    """Visible entries written today for prompt/delta inspection."""
    effective_reader = current_bot_id() if reader is None else reader
    today_str, _path, lines, blocks = today_blocks(facade, now)
    entries: list[dict] = []
    for meta, resolved, start, end in blocks:
        if not is_visible(meta.expires_at, now):
            continue
        if not visible_to(meta.bots, effective_reader):
            continue
        if not facade._entry_readable(
            meta.chat, meta.bots, allow_own_cross_window=True
        ):
            continue
        if chat is not None and meta.chat != chat:
            continue
        entries.append(
            {
                "memory_id": memory_id(today_str, resolved),
                "title": meta.title,
                "body": "".join(lines[start + 1 : end]).strip(),
                "bots": list(meta.bots) if meta.bots is not None else None,
                "chat": meta.chat,
                "proj": meta.proj,
                "task": meta.task,
                "kind": meta.kind,
                "status": meta.status,
                "habit": meta.habit,
                "habit_explicit": bool(meta.habit_explicit),
            }
        )
    return entries


def upsert_task_card(
    facade,
    topic: str,
    summary: str,
    content: str = "",
    *,
    task: str,
    status: str | None = None,
    importance: int = DEFAULT_IMPORTANCE,
    bots: list[str] | tuple[str, ...] | None = None,
    chat: str | None = None,
    proj: str | None = None,
    now: datetime | None = None,
    target_memory_id: str | None = None,
) -> str:
    """Update an existing task card in-place or create a new checkpoint card."""
    validate_entry_fields(topic, summary, content, importance)
    validate_task_meta(task, None, None, status, None)
    normalized_proj = _absolute_proj(proj)
    clean_chat = chat.strip() if isinstance(chat, str) and chat.strip() else None
    with facade.writer.mutation_lock():
        if target_memory_id is None:
            facade.archive.reindex(now)
            for item in facade.tasks(
                now=now, limit=50, allow_own_cross_window=True
            )["tasks"]:
                if item["task_id"] == task:
                    target_memory_id = str(item["latest_memory_id"])
                    break
        located = locate_visible_block(facade, target_memory_id, now)
        existing_meta = located[0] if located is not None else None
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
                importance=max(existing_meta.importance, importance),
                now=now,
            )
            facade._refresh_index(now)
            return target_memory_id
        cont = target_memory_id if existing_meta is not None else None
        return facade.remember(
            topic,
            summary,
            content=content,
            importance=importance,
            now=now,
            proj=proj,
            chat=chat,
            bots=bots,
            task=task,
            cont=cont,
            kind="checkpoint",
            status=status or "open",
        )


def record_habit_entry(
    facade,
    topic: str,
    summary: str,
    content: str = "",
    *,
    habit: str,
    habit_explicit: bool = False,
    importance: int = DEFAULT_IMPORTANCE,
    bots: list[str] | tuple[str, ...] | None = None,
    chat: str | None = None,
    proj: str | None = None,
    now: datetime | None = None,
) -> str:
    """Record a habit observation or reinforce an existing habit across days.

    If a matching habit (same normalized label and owner set) already exists
    today or in an earlier active daily file, renews its TTL instead of
    minting a new ``##`` block, upgrades ``habit_explicit`` if stated
    directly, and appends one ``YYYY-MM-DD`` observation line when seen on a
    new calendar day. Cross-window repeats record only the canonical label to
    prevent DM text from leaking into group-origin blocks.
    """
    validate_entry_fields(topic, summary, content, importance)
    validate_task_meta(None, None, "habit", None, habit, habit_explicit)
    clean_habit = " ".join(habit.split())
    norm_habit = Chunker.normalize(clean_habit)
    clean_chat = chat.strip() if isinstance(chat, str) and chat.strip() else None
    owners = resolve_owners(bots, current_bot_id())
    with facade.writer.mutation_lock():
        today_str, _path, lines, blocks = today_blocks(facade, now)
        match: tuple[str, HeadingMeta, list[str]] | None = None
        for meta, resolved, start, end in blocks:
            if not is_visible(meta.expires_at, now):
                continue
            if meta.kind != "habit" or not meta.habit or meta.bots != owners:
                continue
            if Chunker.normalize(" ".join(meta.habit.split())) != norm_habit:
                continue
            sid = memory_id(today_str, resolved)
            if not facade._may_mutate(sid, now):
                continue
            body = [
                line.rstrip("\r\n")
                for line in lines[start + 1 : end]
                if line.strip()
            ]
            match = (sid, meta, body)
            break
        if match is None:
            facade.archive.reindex(now)
            now_ts = format_ts(utc_now(now))
            for row in facade.archive.store.habit_rows(
                now_ts, reader=current_bot_id()
            ):
                if decode_bots(row["bots"]) != owners:
                    continue
                row_habit = str(row["habit"] or "")
                if not row_habit:
                    continue
                if Chunker.normalize(" ".join(row_habit.split())) != norm_habit:
                    continue
                sid = str(row["memory_id"])
                if not facade._may_mutate(sid, now):
                    continue
                located = locate_visible_block(facade, sid, now)
                if located is not None:
                    match = (sid, located[0], located[1])
                    break
        if match is not None:
            sid, existing_meta, body_lines = match
            existing_days = {
                sid.split("#", 1)[0],
                *OBS_DAY_RE.findall("\n".join(body_lines)),
            }
            summary_clean = " ".join(summary.split())
            cross_window = (
                clean_chat is not None
                and existing_meta.chat is not None
                and existing_meta.chat != clean_chat
            )
            obs_text = clean_habit if cross_window else summary_clean
            new_body = (
                body_lines + [f"  - {today_str}: {obs_text}"]
                if today_str not in existing_days
                else None
            )
            upgrade_explicit = (
                True
                if (habit_explicit and not existing_meta.habit_explicit)
                else None
            )
            if new_body is not None or upgrade_explicit is not None:
                facade.writer.update_memory(
                    sid,
                    body_lines=new_body,
                    habit_explicit=upgrade_explicit,
                )
            facade.writer.reinforce(
                sid, importance=max(existing_meta.importance, importance), now=now
            )
            facade._refresh_index(now)
            return sid
        return facade.remember(
            topic,
            summary,
            content=content,
            importance=importance,
            now=now,
            proj=proj,
            chat=chat,
            bots=bots,
            kind="habit",
            habit=clean_habit,
            habit_explicit=habit_explicit,
        )


def _is_other_duplicate(incoming_norm: str, raw_lines: list[str], body_text: str) -> bool:
    """True when ``incoming_norm`` matches existing text exactly or via fuzzy synonym."""
    if not incoming_norm:
        return False
    existing_norm = norm_words(body_text)
    if f" {incoming_norm} " in f" {existing_norm} ":
        return True
    if len(incoming_norm) < _OTHER_FUZZY_MIN_CHARS:
        return False
    for raw_line in raw_lines:
        stripped = _OTHER_LINE_PREFIX_RE.sub("", raw_line).strip()
        line_norm = norm_words(stripped)
        if len(line_norm) < _OTHER_FUZZY_MIN_CHARS:
            continue
        shorter = min(len(incoming_norm), len(line_norm))
        longer = max(len(incoming_norm), len(line_norm))
        if shorter < longer * _OTHER_FUZZY_MIN_LEN_RATIO:
            continue
        if fuzz.token_set_ratio(incoming_norm, line_norm) >= _OTHER_FUZZY_RATIO:
            return True
    return False


def _merged_other_title(existing_title: str, topic_clean: str) -> str | None:
    """Append a new topic into today's journal heading (capped at 80 chars)."""
    if not topic_clean or existing_title.endswith("…"):
        return None
    topic_norm = norm_words(topic_clean)
    title_norm = norm_words(existing_title)
    if topic_norm and f" {topic_norm} " in f" {title_norm} ":
        return None
    merged = f"{existing_title} · {topic_clean}"
    if len(merged) > _OTHER_TITLE_CAP:
        merged = merged[: _OTHER_TITLE_CAP - 1].rstrip(" ·") + "…"
    return merged


def append_other_journal(
    facade,
    topic: str,
    summary: str,
    content: str = "",
    *,
    importance: int = DEFAULT_IMPORTANCE,
    bots: list[str] | tuple[str, ...] | None = None,
    chat: str | None = None,
    proj: str | None = None,
    now: datetime | None = None,
) -> str | None:
    """Append a non-task/non-habit fact to today's scoped journal or skip dupes."""
    validate_entry_fields(topic, summary, content, importance)
    normalized_proj = _absolute_proj(proj)
    clean_chat = chat.strip() if isinstance(chat, str) and chat.strip() else None
    owners = resolve_owners(bots, current_bot_id())
    moment = utc_now(now)
    hour = moment.astimezone(facade.archive.zone()).strftime("%H:%M")
    summary_clean = " ".join(summary.split())
    content_lines = [line.strip() for line in content.splitlines() if line.strip()]
    incoming_norm = norm_words(" ".join([summary_clean, *content_lines]))
    with facade.writer.mutation_lock():
        today_str, _path, lines, blocks = today_blocks(facade, now)
        journal_target: tuple[str, HeadingMeta, list[str]] | None = None
        for meta, resolved, start, end in blocks:
            if not is_visible(meta.expires_at, now):
                continue
            if (
                meta.task is not None
                or meta.kind is not None
                or meta.bots != owners
                or meta.chat != clean_chat
                or meta.proj != normalized_proj
            ):
                continue
            sid = memory_id(today_str, resolved)
            raw_body_lines = [
                line.rstrip("\r\n")
                for line in lines[start + 1 : end]
                if line.strip()
            ]
            body_text = "".join(lines[start + 1 : end])
            if _is_other_duplicate(incoming_norm, raw_body_lines, body_text):
                return sid
            if journal_target is None and facade._may_mutate(sid, now):
                journal_target = (sid, meta, raw_body_lines)
        if journal_target is not None:
            sid, meta, existing_body = journal_target
            topic_clean = " ".join(topic.split())
            if norm_words(topic_clean) == norm_words(summary_clean):
                entry_line = f"  - {hour} — {summary_clean}"
            else:
                entry_line = f"  - {hour} — {topic_clean}: {summary_clean}"
            appended = [entry_line, *(f"    {line}" for line in content_lines)]
            merged_title = _merged_other_title(meta.title, topic_clean)
            facade.writer.update_memory(
                sid,
                time=hour,
                topic=merged_title,
                body_lines=existing_body + appended,
            )
            if importance > meta.importance:
                facade.writer.reinforce(sid, importance=importance, now=now)
            facade._refresh_index(now)
            return sid
        return facade.remember(
            topic,
            summary,
            content=content,
            importance=importance,
            now=now,
            proj=proj,
            chat=chat,
            bots=bots,
        )


def format_task_catalog(entries: list[dict], *, limit: int = 20) -> str:
    """Render open memory tasks as prompt index lines (phase 1C).

    One line per task: ``- <task_id>: <title ≤80 chars> · last active
    <last_day> · bots <a, b> [<latest_memory_id>] — <summary>``.
    Closed (done/cancelled) rows are omitted. At most ``limit`` lines.
    """
    cap = max(0, limit)
    lines = []
    for entry in entries:
        if len(lines) >= cap:
            break
        if not isinstance(entry, dict):
            continue
        if entry.get("status") in ("done", "cancelled"):
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
    return "\n".join(lines)
