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
        # Schema types arrive pre-checked by the registry (X3): path is str.
        # Recognized skill reads use the checked descriptor instead of
        # reopening the pathname, so a swapped symlink cannot redirect.
        try:
            lexical = guard._lexical_abs(args["path"])
            ref = guard._skill_ref_for(lexical)
        except ValueError:
            ref = None
        if ref is not None:
            # Authorization plus link checks already ran in deny_read;
            # re-validate and read from the descriptor that passed.
            guard.deny_read(args["path"])
            from thyca.skills.sharing import read_skill_checked

            try:
                return read_skill_checked(guard.thyca_dir, lexical)
            except ValueError as exc:
                from thyca.tools.path_guard import PathDenied

                raise PathDenied(f"read denied: {exc}") from exc
        path = guard.deny_read(args["path"])
        if not path.is_file():
            raise FileNotFoundError(f"not a file: {path}")
        return path.read_text(encoding="utf-8")

    return ToolSpec(
        name="read",
        description="Read a UTF-8 text file. Allowed to read L2 memory files.",
        parameters=_PARAMETERS,
        handler=handler,
        parallel_safe=True,
        resource_key=guard.key,
    )
