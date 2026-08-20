from __future__ import annotations

from thyca.tools.builtin.write import replace_file
from thyca.tools.path_guard import PathGuard
from thyca.tools.registry import ToolSpec

_PARAMETERS = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "edits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "oldText": {"type": "string"},
                    "newText": {"type": "string"},
                },
                "required": ["oldText", "newText"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["path", "edits"],
    "additionalProperties": False,
}


def apply_edits(text: str, edits: object) -> str:
    if not isinstance(edits, list) or not edits:
        raise ValueError("edits must be a non-empty list")
    spans: list[tuple[int, int, str]] = []
    for item in edits:
        if not isinstance(item, dict):
            raise ValueError("each edit must be an object")
        old = item.get("oldText")
        new = item.get("newText")
        if not isinstance(old, str) or not isinstance(new, str):
            raise ValueError("oldText and newText must be strings")
        start = text.find(old)
        if start == -1:
            raise ValueError("oldText not found")
        if text.find(old, start + 1) != -1:
            raise ValueError("oldText matches more than once")
        spans.append((start, start + len(old), new))
    spans.sort(key=lambda span: span[0])
    for previous, current in zip(spans, spans[1:]):
        if previous[1] > current[0]:
            raise ValueError("oldText regions overlap")
    parts: list[str] = []
    cursor = 0
    for start, end, new in spans:
        parts.append(text[cursor:start])
        parts.append(new)
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts)


def edit_spec(guard: PathGuard) -> ToolSpec:
    async def handler(args: dict) -> str:
        path = guard.deny_write(str(args["path"]))
        if not path.is_file():
            raise FileNotFoundError(f"not a file: {path}")
        updated = apply_edits(path.read_text(encoding="utf-8"), args.get("edits"))
        replace_file(path, updated)
        return f"edited {path}"

    return ToolSpec(
        name="edit",
        description=(
            "Replace unique oldText spans in a UTF-8 file. "
            "Same write denylist as write. Mismatch writes nothing."
        ),
        parameters=_PARAMETERS,
        handler=handler,
        parallel_safe=False,
        resource_key=lambda args: str(guard.resolve(str(args["path"]))),
    )
