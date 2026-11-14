from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from mcp.types import Tool

_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
_MODEL_NAME_MAX = 64
_MAX_ERROR_CHARS = 200


@dataclass(frozen=True)
class StartupDiagnostic:
    server: str
    ok: bool
    message: str


def model_name(server: str, tool: str) -> str:
    return f"{server}__{tool}"


def join_text_blocks(content: list[Any]) -> str:
    parts: list[str] = []
    for block in content:
        kind = getattr(block, "type", None)
        if kind is None and isinstance(block, dict):
            kind = block.get("type")
        if kind != "text":
            continue
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def is_object_schema(schema: object) -> bool:
    def is_schema(candidate: object) -> bool:
        if not isinstance(candidate, dict):
            return False
        properties = candidate.get("properties")
        if properties is not None and (
            not isinstance(properties, dict)
            or any(not is_schema(value) for value in properties.values())
        ):
            return False
        required = candidate.get("required")
        if required is not None and (
            not isinstance(required, list)
            or any(not isinstance(name, str) for name in required)
        ):
            return False
        additional = candidate.get("additionalProperties", True)
        return isinstance(additional, bool) or is_schema(additional)

    return isinstance(schema, dict) and schema.get("type") == "object" and is_schema(schema)


def tool_error(
    server: str, tool: Tool, seen: set[str] | None = None
) -> str | None:
    tool_name = getattr(tool, "name", None)
    if not isinstance(tool_name, str) or _TOOL_NAME.fullmatch(tool_name) is None:
        return "tool name must match [A-Za-z0-9_-]+"
    name = model_name(server, tool_name)
    if len(name) > _MODEL_NAME_MAX:
        return "generated tool name is longer than 64 characters"
    if seen is not None and name in seen:
        return "generated tool name is already registered"
    if not is_object_schema(getattr(tool, "inputSchema", None)):
        return "input schema must be an object schema"
    description = getattr(tool, "description", None) or tool_name
    if not isinstance(description, str) or not description:
        return "tool description must be a non-empty string"
    return None


def tool_names(server: str, tools: list[Tool] | None) -> list[str]:
    if not tools:
        return []
    return [
        model_name(server, getattr(tool, "name", "unknown")) for tool in tools
    ]


def validate_tools(
    server: str, tools: list[Tool], seen: set[str]
) -> list[StartupDiagnostic]:
    diags: list[StartupDiagnostic] = []
    for tool in tools:
        error = tool_error(server, tool, seen)
        if error is None:
            seen.add(model_name(server, tool.name))
            continue
        raw_name = getattr(tool, "name", "unknown")
        diags.append(
            StartupDiagnostic(server, False, f"MCP tool {raw_name!r} skipped: {error}")
        )
    return diags


def unwrap_exception(exc: BaseException) -> BaseException:
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


def summarize_error(exc: BaseException) -> str:
    leaf = unwrap_exception(exc)
    detail = " ".join(str(leaf).split())
    text = f"{type(leaf).__name__}: {detail}" if detail else type(leaf).__name__
    if len(text) <= _MAX_ERROR_CHARS:
        return text
    return text[: _MAX_ERROR_CHARS - 3].rstrip() + "..."


def format_mcp_status(failed: Mapping[str, str]) -> str:
    """Render failed MCP server diagnostics for the agent system prompt."""
    if not failed:
        return ""
    lines = [
        "Configured MCP servers that failed to start (their tools are unavailable):",
    ]
    for server, reason in failed.items():
        clean = " ".join(str(reason).split())[:_MAX_ERROR_CHARS]
        lines.append(f"- {server}: {clean or 'startup failed'}")
    lines.append(
        "If a task relies on one of these servers, inform the user clearly instead of fabricating or retrying missing tools."
    )
    return "\n".join(lines)
