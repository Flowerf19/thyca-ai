from __future__ import annotations

import os
from pathlib import Path


def absolutize(value: str) -> Path:
    """~-expand + lexically normalize, without touching the filesystem.

    Shared by :meth:`PathGuard.resolve` (which then anchors relative paths
    and resolves symlinks) and ``MemoryFacade`` proj handling (which requires
    absolute): ``/a/../b`` and ``/b`` are one identity in both."""
    return Path(os.path.normpath(os.path.expanduser(value)))


class PathDenied(ValueError):
    """write/edit target is L2, session, or sqlite."""


class PathGuard:
    def __init__(self, thyca_dir: Path | None = None) -> None:
        self.thyca_dir = Path(thyca_dir or Path.home() / ".thyca").resolve()

    def resolve(self, path: str) -> Path:
        if not isinstance(path, str) or not path.strip():
            raise ValueError("path must be a non-empty string")
        raw = absolutize(path)
        if not raw.is_absolute():
            raw = Path.cwd() / raw
        return raw.resolve()

    def key(self, args: dict) -> str:
        """Lock identity for one file-tool call: the resolved path string.

        The one ``resource_key`` for the read/write/edit specs, so same-path
        calls always serialize on one identity."""
        return str(self.resolve(args["path"]))

    def deny_write(self, path: str) -> Path:
        target = self.resolve(path)
        if self._blocked(target):
            raise PathDenied(f"write denied: {target}")
        return target

    def _blocked(self, target: Path) -> bool:
        root = self.thyca_dir
        try:
            target.relative_to(root)
        except ValueError:
            return False
        if target == root / "MEMORY.md":
            return True
        if target.name.startswith("memory.sqlite"):
            return True
        for folder in (root / "sessions", root / "memory"):
            try:
                target.relative_to(folder)
            except ValueError:
                continue
            return True
        return False
