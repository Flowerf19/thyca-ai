from __future__ import annotations

import os
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
    # Safe ordinary-file replace: the implicit "<name>.tmp" must never
    # follow a symlink/hardlink or overwrite a preexisting user temp.
    # Exclusive no-follow creation fails closed when the temp already
    # exists (symlink, hardlink, or regular file); a preexisting temp is
    # never deleted or overwritten. Only the temp created by this call is
    # removed on pre-publication failure.
    if not isinstance(content, str):
        raise ValueError("content must be str")
    try:
        data = content.encode("utf-8")
    except (UnicodeEncodeError, ValueError) as exc:
        raise ValueError(f"content is not encodable: {exc}") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
        )
    except OSError as exc:
        raise ValueError(f"cannot create temp (preexisting temp is never overwritten): {exc}") from exc
    try:
        view = memoryview(data)
        while view:
            n = os.write(fd, view)
            view = view[n:]
        os.fsync(fd)
    except BaseException as exc:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            temporary.unlink()
        except OSError:
            pass
        raise ValueError(f"temp write failed: {exc}") from exc
    try:
        os.close(fd)
    except OSError as exc:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise ValueError(f"temp close failed: {exc}") from exc
    try:
        temporary.replace(path)
    except BaseException as exc:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def write_spec(guard: PathGuard) -> ToolSpec:
    async def handler(args: dict) -> str:
        # Schema types arrive pre-checked by the registry (X3).
        # The skill decision mirrors the guard (lexical + resolved) so an
        # alias cannot select the ordinary-file writer. Recognized skill
        # writes use the pinned descriptor writer instead of reopening an
        # unchecked parent or temp file.
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

                raise PathDenied(f"write denied: {args['path']}")
            path = guard.deny_write(args["path"])
            from thyca.skills.sharing import write_skill_checked

            assert lexical is not None
            try:
                write_skill_checked(guard.thyca_dir, lexical, args["content"])
            except ValueError as exc:
                from thyca.tools.path_guard import PathDenied

                raise PathDenied(f"write denied: {path}: {exc}") from exc
            return f"wrote {path}"
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
