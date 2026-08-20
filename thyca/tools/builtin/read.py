from __future__ import annotations

from thyca.tools.path_guard import PathGuard
from thyca.tools.registry import ToolSpec

_PARAMETERS = {
    "type": "object",
    "properties": {"path": {"type": "string"}},
    "required": ["path"],
    "additionalProperties": False,
}


def read_spec(guard: PathGuard) -> ToolSpec:
    async def handler(args: dict) -> str:
        path = guard.resolve(str(args["path"]))
        if not path.is_file():
            raise FileNotFoundError(f"not a file: {path}")
        return path.read_text(encoding="utf-8")

    return ToolSpec(
        name="read",
        description="Read a UTF-8 text file. Allowed to read L2 memory files.",
        parameters=_PARAMETERS,
        handler=handler,
        parallel_safe=True,
        resource_key=lambda args: str(guard.resolve(str(args["path"]))),
    )
