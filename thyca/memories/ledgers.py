"""3-ledger L2 memory operations: Task cards, Habit observations, Other journal."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path

from rapidfuzz import fuzz

from thyca.memories.active import visible_to
from thyca.memories.archive_store import ArchiveError, decode_bots, encode_bots
from thyca.memories.chunk import Chunker
from thyca.memories.habits import OBS_DAY_RE
from thyca.memories.heading import (
    DEFAULT_IMPORTANCE,
    HABIT_DEFAULT_IMPORTANCE,
    TASK_DEFAULT_IMPORTANCE,
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
_OTHER_SUB_ENTRY_RE = re.compile(r"^\s+[-*]\s+\d{2}:\d{2}\s*[—\-]\s+")
_DIGIT_TOKEN_RE = re.compile(r"\b\w*\d\w*\b", re.UNICODE)
_ACCENT_WORD_RE = re.compile(r"\w+(?:'\w+)?", re.UNICODE)
_POLARITY_TOKENS = frozenset({
    "not", "no", "never", "without", "stopped", "removed", "off", "on",
    "enable", "disable", "enabled", "disabled", "true", "false",
    "dont", "don't", "doesnt", "doesn't", "didnt", "didn't",
    "cant", "can't", "cannot", "wont", "won't",
    "isnt", "isn't", "arent", "aren't", "wasnt", "wasn't", "werent", "weren't",
    "không", "chưa", "đừng", "ngừng", "ngưng", "dừng", "bỏ", "thôi",
    "tắt", "bật", "hết", "hủy",
})
TASK_UPDATED_DAY_RE = re.compile(
    r"^\s*-\s*updated:\s*(\d{4}-\d{2}-\d{2})\s*$", re.MULTILINE
)
_TASK_DECISION_LINE_RE = re.compile(r"^\s*-\s*decisions?\s*:", re.IGNORECASE)
HOT_TASK_MAX_AGE_DAYS = 7
HOT_TASK_PROMPT_LIMIT = 8
_OTHER_FUZZY_MIN_CHARS = 12
_OTHER_FUZZY_RATIO = 88
_OTHER_FUZZY_MIN_LEN_RATIO = 0.55


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


def record_habit_entry(
    facade,
    topic: str,
    summary: str,
    content: str = "",
    *,
    habit: str,
    habit_explicit: bool = False,
    importance: int | None = None,
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
    effective_importance = (
        HABIT_DEFAULT_IMPORTANCE if importance is None else importance
    )
    validate_entry_fields(topic, summary, content, effective_importance)
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
                [*body_lines, f"  - {today_str}: {obs_text}"]
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
                sid, importance=max(existing_meta.importance, effective_importance), now=now
            )
            facade._refresh_index(now)
            return sid
        return facade.remember(
            topic,
            summary,
            content=content,
            importance=effective_importance,
            now=now,
            proj=proj,
            chat=chat,
            bots=bots,
            kind="habit",
            habit=clean_habit,
            habit_explicit=habit_explicit,
        )


def _journal_entries(raw_lines: list[str]) -> list[tuple[str, str, str]]:
    """Group Other-journal body lines into ``(first_raw, content_raw, full_raw)`` triples."""
    groups: list[list[str]] = []
    for raw_line in raw_lines:
        if not raw_line.strip():
            continue
        if not groups or _OTHER_SUB_ENTRY_RE.match(raw_line) or raw_line.startswith(("- ", "* ")):
            groups.append([raw_line])
        else:
            groups[-1].append(raw_line)
    entries: list[tuple[str, str, str]] = []
    for group in groups:
        first_raw = _OTHER_LINE_PREFIX_RE.sub("", group[0]).strip()
        content_raw = " ".join(line.strip() for line in group[1:] if line.strip())
        full_raw = f"{first_raw} {content_raw}".strip() if content_raw else first_raw
        if first_raw or full_raw:
            entries.append((first_raw, content_raw, full_raw))
    return entries


def _polarity_conflict(inc_raw: str, cand_raw: str) -> bool:
    """True when raw forms disagree on a polarity token (accent folding can hide this)."""
    inc_acc = set(_ACCENT_WORD_RE.findall(inc_raw.casefold()))
    cand_acc = set(_ACCENT_WORD_RE.findall(cand_raw.casefold()))
    return bool((inc_acc ^ cand_acc) & _POLARITY_TOKENS)


def _entries_match(inc_raw: str, inc_norm: str, cand_raw: str, cand_norm: str) -> bool:
    """True when incoming entry duplicates candidate without clobbering updates/reversals."""
    if not inc_norm or not cand_norm:
        return False
    if _polarity_conflict(inc_raw, cand_raw):
        return False
    if inc_norm == cand_norm:
        return True
    if (
        len(inc_norm) < _OTHER_FUZZY_MIN_CHARS
        or len(cand_norm) < _OTHER_FUZZY_MIN_CHARS
    ):
        return False
    shorter = min(len(inc_norm), len(cand_norm))
    longer = max(len(inc_norm), len(cand_norm))
    if shorter < longer * _OTHER_FUZZY_MIN_LEN_RATIO:
        return False
    if _DIGIT_TOKEN_RE.findall(inc_norm) != _DIGIT_TOKEN_RE.findall(cand_norm):
        return False
    inc_order = list(dict.fromkeys(inc_norm.split()))
    cand_order = list(dict.fromkeys(cand_norm.split()))
    shared = set(inc_order) & set(cand_order)
    if [w for w in inc_order if w in shared] != [w for w in cand_order if w in shared]:
        return False
    return fuzz.token_set_ratio(inc_norm, cand_norm) >= _OTHER_FUZZY_RATIO


def _is_other_duplicate(
    incoming_raw: str,
    incoming_norm: str,
    raw_lines: list[str],
    *,
    incoming_content_raw: str = "",
) -> bool:
    """True when incoming entry matches an existing journal entry (exact or safe fuzzy)."""
    if not incoming_norm:
        return False
    inc_content_norm = norm_words(incoming_content_raw) if incoming_content_raw else ""
    for first_raw, cand_content_raw, full_raw in _journal_entries(raw_lines):
        full_norm = norm_words(full_raw)
        if incoming_norm == full_norm and not _polarity_conflict(incoming_raw, full_raw):
            return True
        if inc_content_norm:
            if cand_content_raw:
                if (
                    _entries_match(incoming_raw, incoming_norm, full_raw, full_norm)
                    and _entries_match(
                        incoming_content_raw,
                        inc_content_norm,
                        cand_content_raw,
                        norm_words(cand_content_raw),
                    )
                ):
                    return True
            else:
                if (
                    f" {inc_content_norm} " in f" {full_norm} "
                    and _entries_match(incoming_raw, incoming_norm, full_raw, full_norm)
                ):
                    return True
        else:
            if _entries_match(incoming_raw, incoming_norm, full_raw, full_norm):
                return True
            if (
                cand_content_raw
                and first_raw != full_raw
                and _entries_match(incoming_raw, incoming_norm, first_raw, norm_words(first_raw))
            ):
                return True
    return False


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
    """Append a non-task/non-habit fact to today's topic-scoped block or skip dupes."""
    validate_entry_fields(topic, summary, content, importance)
    normalized_proj = _absolute_proj(proj)
    clean_chat = chat.strip() if isinstance(chat, str) and chat.strip() else None
    owners = resolve_owners(bots, current_bot_id())
    moment = utc_now(now)
    hour = moment.astimezone(facade.archive.zone()).strftime("%H:%M")
    topic_clean = " ".join(topic.split())
    topic_norm = norm_words(topic_clean)
    summary_clean = " ".join(summary.split())
    content_lines = [line.strip() for line in content.splitlines() if line.strip()]
    incoming_content_raw = " ".join(content_lines)
    incoming_raw = " ".join([summary_clean, *content_lines])
    incoming_norm = norm_words(incoming_raw)
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
            ):
                continue
            if meta.proj != normalized_proj and not (
                normalized_proj is None and meta.proj is not None
            ):
                continue
            sid = memory_id(today_str, resolved)
            raw_body_lines = [
                line.rstrip("\r\n")
                for line in lines[start + 1 : end]
                if line.strip()
            ]
            if _is_other_duplicate(
                incoming_raw,
                incoming_norm,
                raw_body_lines,
                incoming_content_raw=incoming_content_raw,
            ):
                return sid
            if (
                meta.proj == normalized_proj
                and journal_target is None
                and norm_words(meta.title) == topic_norm
                and facade._may_mutate(sid, now)
            ):
                journal_target = (sid, meta, raw_body_lines)
        if incoming_norm:
            facade.archive.reindex(now)
            now_ts = format_ts(moment)
            for row in facade.archive.store.other_journal_rows(
                now_ts,
                reader=current_bot_id(),
                chat_session=clean_chat,
                project=normalized_proj,
            ):
                if decode_bots(row["bots"]) != owners:
                    continue
                sid = str(row["memory_id"])
                located = locate_visible_block(facade, sid, now)
                if located is None:
                    continue
                existing_meta, existing_lines = located
                matched_cross_day = any(
                    (
                        incoming_norm == norm_words(full_raw)
                        and not _polarity_conflict(incoming_raw, full_raw)
                    )
                    or (
                        not content_lines
                        and incoming_norm == norm_words(first_raw)
                        and not _polarity_conflict(incoming_raw, first_raw)
                    )
                    for first_raw, _cand_content_raw, full_raw in _journal_entries(existing_lines)
                )
                if not matched_cross_day:
                    continue
                if facade._may_mutate(sid, now):
                    facade.writer.reinforce(
                        sid,
                        importance=max(existing_meta.importance, importance),
                        now=now,
                    )
                    facade._refresh_index(now)
                return sid
        if journal_target is not None:
            sid, meta, existing_body = journal_target
            entry_line = f"  - {hour} — {summary_clean}"
            appended = [entry_line, *(f"    {line}" for line in content_lines)]
            facade.writer.update_memory(
                sid,
                time=hour,
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
            f"(+{dormant_count} older open tasks — call memory_tasks if user brings them up)"
        )
    return "\n".join(lines)
