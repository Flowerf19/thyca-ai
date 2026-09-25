from __future__ import annotations

from pathlib import Path

from thyca.tools.path_guard import PathGuard
from thyca.tools.registry import ToolSpec

_PARAMETERS = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "content": {"type": "string"},
    },
    "required": ["path", "content"],
    "additionalProperties": False,
}


def replace_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def write_spec(guard: PathGuard) -> ToolSpec:
    async def handler(args: dict) -> str:
        # Schema types arrive pre-checked by the registry (X3).
        path = guard.deny_write(args["path"])
        replace_file(path, args["content"])
        return f"wrote {path}"

    return ToolSpec(
        name="write",
        description=(
            "Write a UTF-8 text file (replace). "
            "Denied: L2 daily, leftover MEMORY.md, sessions, sqlite. "
            "Allowed: SOUL.md, IDENTITY.md, USER.md, config.json, and paths outside those."
        ),
        parameters=_PARAMETERS,
        handler=handler,
        parallel_safe=False,
        resource_key=guard.key,
    )
