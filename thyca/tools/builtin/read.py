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
        # The skill decision uses both lexical and resolved identities like
        # the guard, so an alias cannot select the ordinary-file handler.
        # Recognized reads use the checked descriptor instead of reopening
        # the pathname, so a swapped symlink cannot redirect.
        from thyca.skills.sharing import skill_refs_match

        try:
            lexical = guard._lexical_abs(args["path"])
            resolved = guard.resolve(args["path"])
            ref_lex, ref_res = guard._skill_refs_for(lexical, resolved)
        except ValueError:
            ref_lex, ref_res = None, None
            lexical = None
        if ref_lex is not None or ref_res is not None:
            if not skill_refs_match(ref_lex, ref_res):
                from thyca.tools.path_guard import PathDenied

                raise PathDenied(f"read denied: {args['path']}")
            # Authorization plus link checks already ran in deny_read;
            # re-validate and read from the descriptor that passed.
            guard.deny_read(args["path"])
            from thyca.skills.sharing import read_skill_checked

            try:
                assert lexical is not None
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
