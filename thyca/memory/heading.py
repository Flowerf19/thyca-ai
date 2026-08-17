"""Parse and write ``<!-- thyca:id imp= exp= -->`` heading comments."""
from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

TTL_DAYS = {1: 3, 2: 7, 3: 30, 4: 90, 5: 180}
DEFAULT_IMPORTANCE = 3

_HEADING_RE = re.compile(
    r"^(##\s+\d{2}:\d{2}\s*[—\-]\s+.+?)(?:\s*<!--\s*(.*?)\s*-->)?\s*$"
)
_THYCA_ID = re.compile(r"(?:^|\s)thyca:([0-9a-f]{8})(?:\s|$)")
_ATTR = re.compile(r"\b(imp|exp|forgotten)=(\S+)")  # forgotten= legacy only


@dataclass
class HeadingMeta:
    title_line: str
    entry_id: str
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


def parse_heading(line: str) -> HeadingMeta | None:
    match = _HEADING_RE.match(line.rstrip("\n"))
    if match is None:
        return None
    comment = match.group(2) or ""
    id_match = _THYCA_ID.search(comment)
    if id_match is None:
        return None
    attrs = dict(_ATTR.findall(comment))
    importance = int(attrs["imp"]) if "imp" in attrs else DEFAULT_IMPORTANCE
    return HeadingMeta(
        title_line=match.group(1).rstrip(),
        entry_id=id_match.group(1),
        importance=importance,
        expires_at=attrs.get("exp") or attrs.get("forgotten"),
    )


def render_heading(meta: HeadingMeta) -> str:
    parts = [f"thyca:{meta.entry_id}", f"imp={meta.importance}"]
    if meta.expires_at:
        parts.append(f"exp={meta.expires_at}")
    return f"{meta.title_line} <!-- {' '.join(parts)} -->\n"


def is_visible(expires_at: str | None, now: datetime | None = None) -> bool:
    if not expires_at:
        return True
    return expires_at > format_ts(now)


def is_expired(expires_at: str | None, now: datetime | None = None) -> bool:
    return bool(expires_at) and not is_visible(expires_at, now)
