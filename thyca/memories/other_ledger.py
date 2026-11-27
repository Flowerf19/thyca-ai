"""Other-journal ledger: topic-scoped append and polarity-aware fuzzy dedupe."""
from __future__ import annotations

import re
from datetime import datetime

from rapidfuzz import fuzz

from thyca.memories.archive_store import decode_bots
from thyca.memories.heading import (
    DEFAULT_IMPORTANCE,
    HeadingMeta,
    format_ts,
    is_visible,
    memory_id,
    utc_now,
)
from thyca.memories.identity import current_bot_id
from thyca.memories.ledger_common import (
    locate_visible_block,
    norm_words,
    resolve_owners,
    today_blocks,
    validate_entry_fields,
)
from thyca.memories.search import _absolute_proj

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
_OTHER_FUZZY_MIN_CHARS = 12
_OTHER_FUZZY_RATIO = 88
_OTHER_FUZZY_MIN_LEN_RATIO = 0.55


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
