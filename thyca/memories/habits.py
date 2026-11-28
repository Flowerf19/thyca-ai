"""Per-bot habit sweep (phase 1E): habit observations → bot USER.md.

Each bot records habit observations (``kind="habit"`` + label) in the
daily log. When one label reaches 3 distinct days — or a single
user-stated observation (``habit_explicit``) — a generalized line lands
in that bot's own ``USER.md`` under an auto-managed ``## Habits`` block.
Only the bot's own file is touched; the shared ``USER.md`` aggregation
(≥2 bots) is a later phase.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from thyca.configs import atomic_write_text
from thyca.memories.archive_store import decode_bots, encode_bots
from thyca.memories.heading import (
    HABIT_DEFAULT_IMPORTANCE,
    HeadingMeta,
    format_ts,
    is_visible,
    memory_id,
    read_text_file,
    utc_now,
    validate_task_meta,
)
from thyca.memories.identity import current_bot_id
from thyca.memories.ledger_common import (
    is_strict_label_overlap,
    label_key,
    locate_visible_block,
    resolve_owners,
    today_blocks,
    validate_entry_fields,
)
from thyca.memories.visibility import current_memory_view

AUTO_START = "<!-- habits:auto:start -->"
AUTO_END = "<!-- habits:auto:end -->"
PROFILE_AUTO_START = "<!-- profile:auto:start -->"
PROFILE_AUTO_END = "<!-- profile:auto:end -->"
OBS_DAY_RE = re.compile(r"^\s*-\s*(\d{4}-\d{2}-\d{2})\s*:", re.MULTILINE)

FRAME = (
    "# User — bot tự ghi\n"
    "<!-- Nội dung do bot tự suy luận từ ký ức của chính nó. "
    "Độ tin cậy thấp hơn USER.md chung; lời user hiện tại luôn thắng. -->\n"
    "## Habits\n"
    f"{AUTO_START}\n"
    f"{AUTO_END}\n"
)


def habit_observations(facade, bot_id: str | None = None, now=None) -> list[dict]:
    """Daily ``kind=\"habit\"`` entries attributable to one bot.

    Reader comes from the facade (``current_bot_id``), exactly like
    ``MemoryFacade.tasks``: the caller binds the bot first. On top of
    that visibility filter, an explicit ``bot_id`` keeps only entries
    whose owners contain it — legacy None and team-only rows are never
    any single bot's observation (rework: a team-visible explicit entry
    must not land in every bot's private file). A co-owned entry counts
    for each listed bot: both bots legitimately hold the same fact, so
    each sweep may record it (see also the aggregate ≥2-bot rule — the
    user decision counts bots holding the habit, not independent
    observations). Merges store rows with today's not-yet-indexed file,
    skips expired entries and store duplicates. One record per session.
    """

    if hasattr(facade, "_ensure_rollover_indexed"):
        facade._ensure_rollover_indexed(now)
    reader = current_bot_id()
    now_ts = format_ts(utc_now(now))
    by_session: dict[str, dict] = {}
    for row in facade.archive.store.habit_rows(now_ts, reader=reader):
        if not row["day"]:
            continue
        keys = tuple(row.keys())
        origin = row["chat"] if "chat" in keys else None
        if hasattr(facade, "_entry_readable") and not facade._entry_readable(
            origin, row["bots"], allow_own_cross_window=False
        ):
            continue
        day_str = str(row["day"])
        body_text = str(row["text_raw"] or "") if "text_raw" in keys else ""
        days = sorted({day_str, *OBS_DAY_RE.findall(body_text)})
        by_session[str(row["memory_id"])] = {
            "memory_id": str(row["memory_id"]),
            "timeline_day": day_str,
            "last_day": days[-1],
            "days": days,
            "count": len(days),
            "habit": str(row["habit"] or ""),
            "habit_explicit": bool(row["explicit"]),
            "bots": decode_bots(row["bots"]),
        }
    today = facade.archive.day(now)
    try:
        today_text = read_text_file(facade.thyca_dir / "memory" / f"{today}.md")
    except (OSError, UnicodeDecodeError):
        today_text = None
    if today_text:
        chunks = facade.archive.chunker.chunk_markdown(
            str(facade.thyca_dir / "memory" / f"{today}.md"),
            today_text,
            source_kind="daily",
            timeline_day=today,
        )
        seen_leaves: dict[str, list] = {}
        for chunk in chunks:
            if chunk.kind != "habit" or chunk.memory_id in by_session:
                continue
            if hasattr(facade, "_entry_readable") and not facade._entry_readable(
                getattr(chunk, "chat_session", None),
                encode_bots(chunk.bots),
                allow_own_cross_window=False,
            ):
                continue
            seen_leaves.setdefault(chunk.memory_id, []).append(chunk)
        for memory_id, leaves in seen_leaves.items():
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
            if not first.habit:
                continue
            body_text = "\n".join(leaf.text_raw for leaf in leaves)
            days = sorted({today, *OBS_DAY_RE.findall(body_text)})
            by_session[memory_id] = {
                "memory_id": memory_id,
                "timeline_day": today,
                "last_day": days[-1],
                "days": days,
                "count": len(days),
                "habit": first.habit,
                "habit_explicit": bool(first.habit_explicit),
                "bots": owners,
            }
    records = [by_session[key] for key in sorted(by_session)]
    if bot_id is None:
        return records
    return [
        record for record in records
        if record["bots"] is not None and bot_id in record["bots"]
    ]


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
    norm_habit = label_key(clean_habit)
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
            if label_key(meta.habit) != norm_habit:
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
                if label_key(row_habit) != norm_habit:
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


def sweep_bot_habits(facade, bot_id: str, bot_user_path, now=None) -> list[str]:
    """Append newly-qualified habit labels to one bot's USER.md.

    A label group qualifies with one explicit observation or distinct
    days ≥ 3; the displayed label is the newest observation's raw label.
    Append-only inside the auto block: existing lines (even from expired
    observations) stay, only missing keys are added. Idempotent.
    Returns the added labels.
    """

    observations = habit_observations(facade, bot_id, now=now)
    groups: dict[str, list[dict]] = {}
    for obs in observations:
        collapsed = " ".join(obs["habit"].split())
        if not collapsed or "<!--" in collapsed:
            continue
        obs = {**obs, "habit": collapsed}
        groups.setdefault(label_key(collapsed), []).append(obs)
    wanted: dict[str, str] = {}
    for key, members in groups.items():
        days = {
            d
            for m in members
            for d in m.get("days", (m["timeline_day"],))
        }
        if not any(m["habit_explicit"] for m in members) and len(days) < 3:
            continue
        newest = max(members, key=lambda m: m.get("last_day", m["timeline_day"]))
        wanted[key] = newest["habit"]
    if not wanted:
        return []
    path = Path(bot_user_path)
    with facade.writer.lock_for(path):
        try:
            text = path.read_text(encoding="utf-8") if path.is_file() else ""
        except OSError:
            return []
        if AUTO_START not in text or AUTO_END not in text:
            if not text:
                text = FRAME
            else:
                if not text.endswith("\n"):
                    text += "\n"
                text += f"\n## Habits\n{AUTO_START}\n{AUTO_END}\n"
        existing = _block_keys(text)
        fresh = [(key, label) for key, label in wanted.items() if key not in existing]
        if not fresh:
            return []
        lines = [f"- {label}" for _, label in sorted(fresh)]
        head, _sep, tail = text.partition(AUTO_START)
        block, _, rest = tail.partition(AUTO_END)
        if not block.endswith("\n"):
            block += "\n"
        new_text = head + AUTO_START + block + "".join(line + "\n" for line in lines) + AUTO_END + rest
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return []
        atomic_write_text(path, new_text)
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return [label for _, label in sorted(fresh)]


def _block_keys(text: str) -> set[str]:
    """Normalized labels already present in the auto block."""
    return {label_key(label) for label in parse_bot_habits(text)}


def parse_bot_habits(text: str) -> list[str]:
    """Raw habit labels in one bot USER file's auto-block.

    Only ``- label`` lines inside the markers; blanks, comments, and
    files without the block read as no habits.
    """
    if AUTO_START not in text or AUTO_END not in text:
        return []
    _, _, tail = text.partition(AUTO_START)
    block, _, _ = tail.partition(AUTO_END)
    labels = []
    for line in block.splitlines():
        stripped = line.strip()
        if AUTO_START in stripped or AUTO_END in stripped:
            continue
        if stripped.startswith("- "):
            label = stripped[2:].strip()
            if label and not label.startswith("<!--"):
                labels.append(label)
    return labels


def _strip_marker_block(text: str, start: str, end: str) -> str:
    if start not in text:
        return text
    head, _, tail = text.partition(start)
    if end not in tail:
        return head.rstrip("\n") + ("\n" if head.endswith("\n") else "")
    _, _, rest = tail.partition(end)
    return head + rest


def strip_auto_habits(text: str) -> str:
    """Remove auto-generated habits and profile blocks for projection-time safety.

    Returns the manual profile text unchanged around them. Well-formed
    blocks drop through their end marker; a start marker without its end
    hides the rest of the file (auto-generated tail, not user content).
    No file rewrite — projection-time stripping only."""
    if not isinstance(text, str):
        return ""
    out = _strip_marker_block(text, AUTO_START, AUTO_END)
    return _strip_marker_block(out, PROFILE_AUTO_START, PROFILE_AUTO_END)


def visible_habit_catalog(
    facade,
    bot_id: str | None = None,
    bot_user_path=None,
    now=None,
) -> list[dict]:
    """Distinct visible habit entries for ``bot_id`` (respects bound MemoryView)."""
    seen: dict[str, dict] = {}
    for obs in habit_observations(facade, bot_id, now=now):
        collapsed = " ".join(str(obs.get("habit") or "").split())
        if not collapsed or "<!--" in collapsed:
            continue
        key = label_key(collapsed)
        if key not in seen:
            seen[key] = {
                "habit": collapsed,
                "memory_id": obs.get("memory_id"),
                "count": obs.get("count", 1),
                "habit_explicit": bool(obs.get("habit_explicit")),
            }
    view = current_memory_view()
    if bot_user_path is not None and not (view is not None and view.is_group):
        path = Path(bot_user_path)
        try:
            text = path.read_text(encoding="utf-8") if path.is_file() else ""
        except OSError:
            text = ""
        for label in parse_bot_habits(text):
            collapsed = " ".join(label.split())
            if not collapsed or "<!--" in collapsed:
                continue
            key = label_key(collapsed)
            if key not in seen:
                seen[key] = {
                    "habit": collapsed,
                    "memory_id": None,
                    "count": 0,
                    "habit_explicit": True,
                }
    return list(seen.values())


def match_known_habits(
    habit: str, catalog: list[dict]
) -> tuple[list[dict], list[dict]]:
    """Return ``(exact_matches, similar_candidates)`` for ``habit``."""
    target = label_key(habit)
    if not target:
        return [], []
    exact: list[dict] = []
    similar: list[dict] = []
    for entry in catalog:
        label = str(entry.get("habit") or "").strip()
        if not label:
            continue
        if label_key(label) == target:
            exact.append(entry)
        elif is_strict_label_overlap(habit, label):
            similar.append(entry)
    return exact, similar


def known_habit_labels(
    facade,
    bot_id: str | None = None,
    bot_user_path=None,
    now=None,
) -> list[str]:
    """Distinct habit labels already recorded for ``bot_id`` across days."""
    return [
        entry["habit"]
        for entry in visible_habit_catalog(facade, bot_id, bot_user_path, now=now)
    ]
