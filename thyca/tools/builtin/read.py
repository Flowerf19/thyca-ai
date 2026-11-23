from __future__ import annotations

from thyca.skills.sharing import read_skill_checked, skill_refs_match
from thyca.tools.path_guard import PathDenied, PathGuard
from thyca.tools.registry import ToolSpec

_PARAMETERS = {
    "type": "object",
    "properties": {"path": {"type": "string"}},
    "required": ["path"],
    "additionalProperties": False,
}


def read_spec(guard: PathGuard) -> ToolSpec:
    async def handler(args: dict) -> str:

        try:
            lexical = guard._lexical_abs(args["path"])
            resolved = guard.resolve(args["path"])
            ref_lex, ref_res = guard._skill_refs_for(lexical, resolved)
        except ValueError:
            ref_lex, ref_res = None, None
            lexical = None
        if ref_lex is not None or ref_res is not None:
            if not skill_refs_match(ref_lex, ref_res):

                raise PathDenied(f"read denied: {args['path']}")
            guard.deny_read(args["path"])

            try:
                assert lexical is not None
                return read_skill_checked(guard.thyca_dir, lexical)
            except ValueError as exc:

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
