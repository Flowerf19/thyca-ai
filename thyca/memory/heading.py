"""Session heading grammar for the whole memory stack.

A session line is ``## HH:mm — title`` plus an optional machine comment.
``parse_heading`` returns None only when the line is not a session heading.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

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


def utc_now(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


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


def parse_heading(line: str) -> HeadingMeta | None:
    match = _HEADING_RE.match(line.rstrip("\r\n"))
    if match is None:
        return None
    time, title, comment = match.group(1), match.group(2).strip(), match.group(3) or ""
    if not title:
        return None
    entry_id, importance, expires_at = _parse_comment(comment)
    return HeadingMeta(time, title, entry_id, importance, expires_at)


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
    payload = f"{path}\0{title}\0{occurrence}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:8]


def resolve_entry_id(meta: HeadingMeta, path: str, occurrence: int) -> str:
    if meta.entry_id is not None:
        return meta.entry_id
    return legacy_entry_id(path, meta.title, occurrence)


def is_visible(expires_at: str | None, now: datetime | None = None) -> bool:
    if not expires_at:
        return True
    return expires_at > format_ts(now)


def is_expired(expires_at: str | None, now: datetime | None = None) -> bool:
    return bool(expires_at) and not is_visible(expires_at, now)


def _parse_comment(comment: str) -> tuple[str | None, int, str | None]:
    raw = comment.strip()
    if not raw:
        return None, DEFAULT_IMPORTANCE, None
    if raw.startswith("thyca"):
        rest = raw[5:].lstrip()
        if rest.startswith("{"):
            return _from_json(rest)
    return _from_legacy(raw)


def _from_json(blob: str) -> tuple[str | None, int, str | None]:
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return None, DEFAULT_IMPORTANCE, None
    if not isinstance(data, dict):
        return None, DEFAULT_IMPORTANCE, None
    entry = data.get("id")
    entry_id = entry if isinstance(entry, str) and ENTRY_ID_RE.fullmatch(entry) else None
    importance = _coerce_imp(data.get("imp"))
    exp = data.get("exp")
    expires_at = exp if isinstance(exp, str) and EXP_RE.fullmatch(exp) else None
    return entry_id, importance, expires_at


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
