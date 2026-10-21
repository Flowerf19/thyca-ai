"""Per-bot habit sweep (phase 1E): habit observations → bot USER.md.

Each bot records habit observations (``kind="habit"`` + label) in the
daily log. When one label reaches 3 distinct days — or a single
user-stated observation (``habit_explicit``) — a generalized line lands
in that bot's own ``USER.md`` under an auto-managed ``## Habits`` block.
Only the bot's own file is touched; the shared ``USER.md`` aggregation
(≥2 bots) is a later phase.
"""
from __future__ import annotations

from pathlib import Path

from thyca.memory.chunk import Chunker
from thyca.memory.heading import format_ts, is_visible, utc_now

AUTO_START = "<!-- habits:auto:start -->"
AUTO_END = "<!-- habits:auto:end -->"

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
    from thyca.memory.archive_store import decode_bots
    from thyca.memory.heading import read_text_file
    from thyca.memory.identity import current_bot_id

    reader = current_bot_id()
    now_ts = format_ts(utc_now(now))
    by_session: dict[str, dict] = {}
    for row in facade.archive.store.habit_rows(now_ts, reader=reader):
        if not row["day"]:
            continue  # sweep counts distinct daily days; dateless rows skip.
        by_session[str(row["session_id"])] = {
            "session_id": str(row["session_id"]),
            "timeline_day": str(row["day"]),
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
            if chunk.kind != "habit" or chunk.session_id in by_session:
                continue
            seen_leaves.setdefault(chunk.session_id, []).append(chunk)
        for session_id, leaves in seen_leaves.items():
            first = leaves[0]
            if not is_visible(first.expires_at, now):
                continue
            owners = first.bots
            if reader is not None and reader != "thyca":
                if owners is not None and "team" not in owners and reader not in owners:
                    continue
            if not first.habit:
                continue
            # Explicit-owner filtering happens once below for both paths.
            by_session[session_id] = {
                "session_id": session_id,
                "timeline_day": today,
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


def sweep_bot_habits(facade, bot_id: str, bot_user_path, now=None) -> list[str]:
    """Append newly-qualified habit labels to one bot's USER.md.

    A label group qualifies with one explicit observation or distinct
    days ≥ 3; the displayed label is the newest observation's raw label.
    Append-only inside the auto block: existing lines (even from expired
    observations) stay, only missing keys are added. Idempotent.
    Returns the added labels.
    """
    from thyca.config import atomic_write_text

    observations = habit_observations(facade, bot_id, now=now)
    groups: dict[str, list[dict]] = {}
    for obs in observations:
        collapsed = " ".join(obs["habit"].split())
        if not collapsed or "<!--" in collapsed:
            continue  # cannot render safely as a "- label" line.
        obs = {**obs, "habit": collapsed}
        groups.setdefault(Chunker.normalize(collapsed), []).append(obs)
    wanted: dict[str, str] = {}
    for key, members in groups.items():
        days = {m["timeline_day"] for m in members}
        if not any(m["habit_explicit"] for m in members) and len(days) < 3:
            continue
        newest = max(members, key=lambda m: m["timeline_day"])
        wanted[key] = newest["habit"]
    if not wanted:
        return []
    path = Path(bot_user_path)
    # Serialize the read-modify-write against memory mutations on the
    # same file (same per-path lock the writer uses).
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
        head, sep, tail = text.partition(AUTO_START)
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
    return {Chunker.normalize(label) for label in parse_bot_habits(text)}


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
            continue  # never adopt a marker line as a habit label.
        if stripped.startswith("- "):
            label = stripped[2:].strip()
            if label and not label.startswith("<!--"):
                labels.append(label)
    return labels


def strip_auto_habits(text: str) -> str:
    """Remove ONLY the exact auto-generated habits block.

    Returns the manual profile text unchanged around it. A well-formed
    block drops through AUTO_END; a start marker without its end hides
    the rest of the file (the tail is auto-generated garbage, not user
    content). No file rewrite — projection-time stripping only."""
    if not isinstance(text, str) or AUTO_START not in text:
        return text if isinstance(text, str) else ""
    head, _, tail = text.partition(AUTO_START)
    if AUTO_END not in tail:
        return head.rstrip("\n") + ("\n" if head.endswith("\n") else "")
    _, _, rest = tail.partition(AUTO_END)
    return head + rest
