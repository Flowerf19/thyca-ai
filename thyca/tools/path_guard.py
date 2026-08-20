from __future__ import annotations

from pathlib import Path


class PathDenied(ValueError):
    """write/edit target is L2, session, config, or sqlite."""


class PathGuard:
    def __init__(self, thyca_dir: Path | None = None) -> None:
        self.thyca_dir = Path(thyca_dir or Path.home() / ".thyca").resolve()

    def resolve(self, path: str) -> Path:
        if not isinstance(path, str) or not path.strip():
            raise ValueError("path must be a non-empty string")
        raw = Path(path).expanduser()
        if not raw.is_absolute():
            raw = Path.cwd() / raw
        return raw.resolve()

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
        if target == root / "config.json" or target == root / "MEMORY.md":
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
