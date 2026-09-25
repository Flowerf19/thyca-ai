"""Session heading grammar for the whole memory stack.

A session line is ``## HH:mm — title`` plus an optional machine comment.
``parse_heading`` returns None only when the line is not a session heading.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TTL_DAYS = {1: 3, 2: 7, 3: 30, 4: 90, 5: 180}
DEFAULT_IMPORTANCE = 3
ENTRY_ID_RE = re.compile(r"^[0-9a-f]{8}$")
EXP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

_HEADING_RE = re.compile(
    r"^##\s+(\d{2}:\d{2})\s*[—\-]\s+(.+?)(?:\s*<!--\s*(.*?)\s*-->)?\s*$"
)
_LEGACY_ID = re.compile(r"(?:^|\s)thyca:([0-9a-f]{8})(?:\s|$)")
_LEGACY_ATTR = re.compile(r"\b(imp|exp)=(\S+)")


@dataclass(frozen=True)
class HeadingMeta:
    time: str
    title: str
    entry_id: str | None
    importance: int = DEFAULT_IMPORTANCE
    expires_at: str | None = None
    proj: str | None = None
    chat: str | None = None


def utc_now(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(UTC)
    if now.tzinfo is None:
        return now.replace(tzinfo=UTC)
    return now.astimezone(UTC)


def day(now: datetime | None, tz: ZoneInfo) -> str:
    """Calendar day of ``now`` in ``tz`` (naive reads as wall time in ``tz``).

    The one day computation for active + archived so day boundaries cannot
    drift between the prompt window and the index."""
    moment = now or datetime.now(tz)
    aware = moment.replace(tzinfo=tz) if moment.tzinfo is None else moment.astimezone(tz)
    return aware.date().isoformat()


def read_text_file(path: Path) -> str | None:
    """UTF-8 text of ``path``, or None when missing/not-a-file/a symlink.

    The one memory read policy: symlinks are never followed (treated as
    absent), and undecodable/unreadable files raise (UnicodeDecodeError /
    OSError) for callers to map to their typed errors (F18)."""
    if not path.is_file() or path.is_symlink():
        return None
    return path.read_text(encoding="utf-8")


def format_ts(now: datetime) -> str:
    return utc_now(now).strftime("%Y-%m-%dT%H:%M:%SZ")


def expiry_ts(importance: int, now: datetime | None = None) -> str:
    if importance not in TTL_DAYS:
        raise ValueError(f"importance must be 1..5, got {importance}")
    return format_ts(utc_now(now) + timedelta(days=TTL_DAYS[importance]))


def new_entry_id() -> str:
    return secrets.token_hex(4)


def is_session_heading(line: str) -> bool:
    return parse_heading(line) is not None


def iter_session_blocks(
    lines: list[str], path: str
) -> Iterator[tuple[HeadingMeta, str, int, int]]:
    """Yield ``(meta, entry_id, start, end)`` for each session block in order.

    The single scan every markdown mutation/chunk site shares, so duplicate
    explicit ids resolve identically everywhere: callers act on the first
    match and copy the rest verbatim.
    """
    seen: dict[str, int] = {}
    index = 0
    total = len(lines)
    while index < total:
        meta = parse_heading(lines[index])
        if meta is None:
            index += 1
            continue
        end = index + 1
        while end < total and parse_heading(lines[end]) is None:
            end += 1
        seen[meta.title] = seen.get(meta.title, 0) + 1
        yield meta, resolve_entry_id(meta, path, seen[meta.title]), index, end
        index = end


def format_body(summary: str, content: str = "") -> list[str]:
    """One ``- summary`` line plus every content line indented two spaces."""
    lines = [f"- {summary.strip()}"]
    if content:
        lines.extend(f"  {line}" for line in content.splitlines())
    return lines


def parse_heading(line: str) -> HeadingMeta | None:
    match = _HEADING_RE.match(line.rstrip("\r\n"))
    if match is None:
        return None
    time, title, comment = match.group(1), match.group(2).strip(), match.group(3) or ""
    if not title:
        return None
    entry_id, importance, expires_at, proj, chat = _parse_comment(comment)
    return HeadingMeta(time, title, entry_id, importance, expires_at, proj, chat)


def render_heading(meta: HeadingMeta) -> str:
    if meta.entry_id is None or ENTRY_ID_RE.fullmatch(meta.entry_id) is None:
        raise ValueError(f"render requires 8-hex entry_id, got {meta.entry_id!r}")
    if meta.importance not in TTL_DAYS:
        raise ValueError(f"importance must be 1..5, got {meta.importance}")
    payload: dict[str, object] = {"id": meta.entry_id, "imp": meta.importance}
    if meta.expires_at:
        if EXP_RE.fullmatch(meta.expires_at) is None:
            raise ValueError(f"invalid expires_at {meta.expires_at!r}")
        payload["exp"] = meta.expires_at
    if meta.proj:
        payload["proj"] = meta.proj
    if meta.chat:
        payload["chat"] = meta.chat
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"## {meta.time} — {meta.title} <!-- thyca {body} -->\n"


def strip_comment(line: str) -> str:
    meta = parse_heading(line)
    if meta is None:
        return line.rstrip("\r\n")
    return f"## {meta.time} — {meta.title}"


def strip_heading_comments(text: str) -> str:
    lines = text.splitlines()
    ended = text.endswith("\n")
    out = [strip_comment(line) if is_session_heading(line) else line for line in lines]
    result = "\n".join(out)
    if ended and (result or text):
        result += "\n"
    return result


def session_id(prefix: str, entry_id: str) -> str:
    return f"{prefix}#{entry_id}"


def legacy_entry_id(path: str, title: str, occurrence: int) -> str:
    payload = f"{path}\0{title}\0{occurrence}".encode()
    return hashlib.sha256(payload).hexdigest()[:8]


def resolve_entry_id(meta: HeadingMeta, path: str, occurrence: int) -> str:
    if meta.entry_id is not None:
        return meta.entry_id
    return legacy_entry_id(path, meta.title, occurrence)


#: SQL twin of is_visible(): visible rows have no forgotten stamp and no
#: expiry in the past. The one predicate every archive_store query shares —
#: column names stay unqualified (no archive join has a second table with
#: these columns), so all seven call sites embed it verbatim.
VISIBLE_SQL = "forgotten_at IS NULL AND (expires_at IS NULL OR expires_at > ?)"


def is_visible(expires_at: str | None, now: datetime | None = None) -> bool:
    if not expires_at:
        return True
    return expires_at > format_ts(now)


def is_expired(expires_at: str | None, now: datetime | None = None) -> bool:
    return bool(expires_at) and not is_visible(expires_at, now)


def _parse_comment(comment: str) -> tuple[str | None, int, str | None, str | None, str | None]:
    raw = comment.strip()
    if not raw:
        return None, DEFAULT_IMPORTANCE, None, None, None
    if raw.startswith("thyca"):
        rest = raw[5:].lstrip()
        if rest.startswith("{"):
            return _from_json(rest)
    return (*_from_legacy(raw), None, None)


def _from_json(blob: str) -> tuple[str | None, int, str | None, str | None, str | None]:
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return None, DEFAULT_IMPORTANCE, None, None, None
    if not isinstance(data, dict):
        return None, DEFAULT_IMPORTANCE, None, None, None
    entry = data.get("id")
    entry_id = entry if isinstance(entry, str) and ENTRY_ID_RE.fullmatch(entry) else None
    importance = _coerce_imp(data.get("imp"))
    exp = data.get("exp")
    expires_at = exp if isinstance(exp, str) and EXP_RE.fullmatch(exp) else None
    proj = data.get("proj")
    chat = data.get("chat")
    proj = proj if isinstance(proj, str) and proj else None
    chat = chat if isinstance(chat, str) and chat else None
    return entry_id, importance, expires_at, proj, chat


def _from_legacy(raw: str) -> tuple[str | None, int, str | None]:
    id_match = _LEGACY_ID.search(raw)
    attrs = dict(_LEGACY_ATTR.findall(raw))
    entry_id = id_match.group(1) if id_match else None
    importance = _coerce_imp(int(attrs["imp"]) if "imp" in attrs and attrs["imp"].isdigit() else None)
    exp = attrs.get("exp")
    expires_at = exp if exp and EXP_RE.fullmatch(exp) else None
    return entry_id, importance, expires_at


def _coerce_imp(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return DEFAULT_IMPORTANCE
    if value not in TTL_DAYS:
        return DEFAULT_IMPORTANCE
    return value
