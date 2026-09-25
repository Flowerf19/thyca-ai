"""Canonical wire types: Message, ToolCall (and ToolResult alias).

Session depends on this module (TASK-309a). No external dependencies.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

_ROLE_OPTIONS = ("user", "assistant", "tool", "system")

# meta cap 4096 bytes when serialized
META_CAP_BYTES = 4096

# tool result cap 32KB when dispatched
RESULT_CAP_BYTES = 32_768


def truncate_to_cap(raw: bytes, cap: int) -> tuple[bytes, bool]:
    """Clip bytes to ``cap`` at a UTF-8 boundary. Returns ``(kept, clipped)``.

    The one byte-cap kernel: gateway retention (via ``head_bytes``) and the
    streaming reasoning budget share it so cap semantics cannot drift. The
    skills index keeps its no-read ``stat()`` size check against the same
    ``RESULT_CAP_BYTES`` policy instead of calling this (reading every
    SKILL.md just to truncate would be worse)."""
    if len(raw) <= cap:
        return raw, False
    kept = raw[:cap]
    while kept:
        try:
            kept.decode("utf-8")
            return kept, True
        except UnicodeDecodeError:
            kept = kept[:-1]
    return kept, True


def estimate_tokens(text: str) -> int:
    """Shared char/4 token heuristic for session compaction + memory chunking."""
    return (len(text) + 3) // 4

# ts format YYYY-MM-DDTHH:mm:ssZ strict UTC
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def utc_now_ts() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(ts: str) -> datetime:
    """Parse a canonical ts string to an aware UTC datetime.

    The one ts parser: Message validation and ask_remember share it so the
    accepted grammar cannot drift."""
    if not isinstance(ts, str) or not _TS_RE.match(ts):
        raise ValueError(f"ts must be ISO-8601 UTC YYYY-MM-DDTHH:mm:ssZ, got {ts!r}")
    # also validate datetime parseable
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as e:
        raise ValueError(f"invalid ts {ts!r}: {e}") from e


def _validate_ts(ts: str) -> None:
    parse_ts(ts)


def _check_meta_cap(meta: dict) -> None:
    """Raise when the serialized meta exceeds the 4096-byte cap.

    The one meta-cap check shared by construction-time validation and
    serialization-time re-check."""
    meta_json = json.dumps(meta, ensure_ascii=False)
    if len(meta_json.encode("utf-8")) > META_CAP_BYTES:
        raise ValueError(f"meta exceeds {META_CAP_BYTES} bytes when serialized")


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict = field(default_factory=dict)
    parse_error: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("ToolCall.id must be non-empty string")
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("ToolCall.name must be non-empty string")
        if not isinstance(self.arguments, dict):
            raise ValueError("ToolCall.arguments must be dict")
        if self.parse_error is not None and not isinstance(self.parse_error, str):
            raise ValueError("ToolCall.parse_error must be str or None")

    def to_dict(self) -> dict:
        d: dict = {"id": self.id, "name": self.name, "arguments": self.arguments}
        if self.parse_error is not None:
            d["parse_error"] = self.parse_error
        return d

    @classmethod
    def from_dict(cls, raw: dict) -> ToolCall:
        if not isinstance(raw, dict):
            raise ValueError("ToolCall must be object")
        for k in ("id", "name", "arguments"):
            if k not in raw:
                raise ValueError(f"ToolCall missing {k!r}")
        return cls(
            id=raw["id"],
            name=raw["name"],
            arguments=raw["arguments"],
            parse_error=raw.get("parse_error"),
        )


# ToolResult is not a separate wire type in session JSONL, but useful alias.
# Session stores tool results as Message(role="tool", tool_call_id=..., content=...).
@dataclass(frozen=True)
class ToolResult:
    tool_call_id: str
    name: str
    content: str
    is_error: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.tool_call_id, str) or not self.tool_call_id:
            raise ValueError("ToolResult.tool_call_id must be non-empty string")


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant", "tool", "system"]
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    ts: str = field(default_factory=utc_now_ts)
    meta: dict | None = None
    reasoning: str | None = None
    reasoning_details: list[dict] | None = None

    def __post_init__(self) -> None:
        if self.role not in _ROLE_OPTIONS:
            raise ValueError(f"role must be one of {_ROLE_OPTIONS}, got {self.role!r}")
        if self.content is not None and not isinstance(self.content, str):
            raise ValueError("content must be str or None")
        if self.tool_calls is not None:
            if not isinstance(self.tool_calls, list):
                raise ValueError("tool_calls must be list or None")
            for tc in self.tool_calls:
                if not isinstance(tc, ToolCall):
                    raise ValueError("tool_calls entries must be ToolCall")
        if self.tool_call_id is not None and not isinstance(self.tool_call_id, str):
            raise ValueError("tool_call_id must be str or None")
        if self.reasoning is not None and not isinstance(self.reasoning, str):
            raise ValueError("reasoning must be str or None")
        if self.reasoning_details is not None:
            if not isinstance(self.reasoning_details, list):
                raise ValueError("reasoning_details must be list or None")
            # Provider signature blobs: validated for shape, never capped —
            # truncating them would invalidate the signature on round-trip.
            for detail in self.reasoning_details:
                if not isinstance(detail, dict):
                    raise ValueError("reasoning_details entries must be dict")
        _validate_ts(self.ts)
        if self.meta is not None:
            if not isinstance(self.meta, dict):
                raise ValueError("meta must be dict or None")
            _check_meta_cap(self.meta)

    def to_canonical_dict(self) -> dict:
        """Canonical dict for JSONL per spec. Deterministic key order via sort in dumps."""
        d: dict = {"role": self.role, "ts": self.ts}
        # content: include even if None? spec says content str|None, tool-call assistant may have null.
        # For canonical we include content key if not None or role assistant with tool_calls
        # Keep explicit to make loader validation clear.
        # Always present (None for assistant tool-call rows) so the loader
        # sees an explicit content key on every message.
        d["content"] = self.content
        if self.tool_calls is not None:
            d["tool_calls"] = [tc.to_dict() for tc in self.tool_calls]
        if self.tool_call_id is not None:
            d["tool_call_id"] = self.tool_call_id
        if self.reasoning:
            d["reasoning"] = self.reasoning
        if self.reasoning_details:
            d["reasoning_details"] = self.reasoning_details
        if self.meta is not None:
            # re-check cap at serialization time as well
            _check_meta_cap(self.meta)
            d["meta"] = self.meta
        return d

    def to_json_line(self) -> str:
        return json.dumps(self.to_canonical_dict(), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_dict(cls, raw: dict) -> Message:
        if not isinstance(raw, dict):
            raise ValueError("Message must be object")
        if "role" not in raw:
            raise ValueError("Message missing required 'role'")
        role = raw["role"]
        content = raw.get("content")
        # tool_calls parse
        tool_calls = None
        if "tool_calls" in raw and raw["tool_calls"] is not None:
            if not isinstance(raw["tool_calls"], list):
                raise ValueError("tool_calls must be list")
            tool_calls = [ToolCall.from_dict(tc) for tc in raw["tool_calls"]]
        tool_call_id = raw.get("tool_call_id")
        ts = raw.get("ts")
        if ts is None:
            raise ValueError("Message missing required 'ts'")
        meta = raw.get("meta")
        reasoning = raw.get("reasoning")
        return cls(
            role=role,
            content=content,
            tool_calls=tool_calls,
            tool_call_id=tool_call_id,
            ts=ts,
            meta=meta,
            reasoning=reasoning,
            reasoning_details=raw.get("reasoning_details"),
        )

    @classmethod
    def from_json_line(cls, line: str) -> Message:
        raw = json.loads(line)
        return cls.from_dict(raw)
