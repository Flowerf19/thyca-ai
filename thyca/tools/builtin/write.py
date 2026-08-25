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
        content = args.get("content")
        if not isinstance(content, str):
            raise ValueError("content must be a string")
        path = guard.deny_write(str(args["path"]))
        replace_file(path, content)
        return f"wrote {path}"

    return ToolSpec(
        name="write",
        description=(
            "Write a UTF-8 text file (replace). "
            "Denied: L2 daily, sessions, config, sqlite. "
            "Allowed: SOUL.md, IDENTITY.md, USER.md, and paths outside those."
        ),
        parameters=_PARAMETERS,
        handler=handler,
        parallel_safe=False,
        resource_key=lambda args: str(guard.resolve(str(args["path"]))),
    )
