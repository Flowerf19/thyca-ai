"""Canonical wire types: Message, ToolCall (and ToolResult alias).

Session depends on this module (TASK-309a). No external dependencies.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

_ROLE_OPTIONS = ("user", "assistant", "tool", "system")

# meta cap 4096 bytes when serialized
META_CAP_BYTES = 4096

# ts format YYYY-MM-DDTHH:mm:ssZ strict UTC
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def utc_now_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_ts(ts: str) -> None:
    if not isinstance(ts, str) or not _TS_RE.match(ts):
        raise ValueError(f"ts must be ISO-8601 UTC YYYY-MM-DDTHH:mm:ssZ, got {ts!r}")
    # also validate datetime parseable
    try:
        datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as e:
        raise ValueError(f"invalid ts {ts!r}: {e}") from e


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
        _validate_ts(self.ts)
        if self.meta is not None:
            if not isinstance(self.meta, dict):
                raise ValueError("meta must be dict or None")
            # cap 4096 bytes when serialized
            meta_json = json.dumps(self.meta, ensure_ascii=False)
            if len(meta_json.encode("utf-8")) > META_CAP_BYTES:
                raise ValueError(f"meta exceeds {META_CAP_BYTES} bytes when serialized")

    def to_canonical_dict(self) -> dict:
        """Canonical dict for JSONL per spec. Deterministic key order via sort in dumps."""
        d: dict = {"role": self.role, "ts": self.ts}
        # content: include even if None? spec says content str|None, tool-call assistant may have null.
        # For canonical we include content key if not None or role assistant with tool_calls
        # Keep explicit to make loader validation clear.
        if self.content is not None or self.role in ("assistant", "tool", "system", "user"):
            # Preserve None explicitly for assistant tool-call case
            d["content"] = self.content
        if self.tool_calls is not None:
            d["tool_calls"] = [tc.to_dict() for tc in self.tool_calls]
        if self.tool_call_id is not None:
            d["tool_call_id"] = self.tool_call_id
        if self.meta is not None:
            # re-check cap at serialization time as well
            meta_json = json.dumps(self.meta, ensure_ascii=False)
            if len(meta_json.encode("utf-8")) > META_CAP_BYTES:
                raise ValueError(f"meta exceeds {META_CAP_BYTES} bytes")
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
        return cls(
            role=role,
            content=content,
            tool_calls=tool_calls,
            tool_call_id=tool_call_id,
            ts=ts,
            meta=meta,
        )

    @classmethod
    def from_json_line(cls, line: str) -> Message:
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as e:
            raise e
        return cls.from_dict(raw)
