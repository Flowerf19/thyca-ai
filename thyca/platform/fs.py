"""OS filesystem capabilities and descriptor-pinned trusted path opening."""
from __future__ import annotations

import os
import stat
import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from .paths import STICKY_TMP_ANCESTORS

OVERFLOW_UID = 65534
MAX_ANCESTOR_SYMLINKS = 40
_SUPPORTS_DIR_FD = os.open in getattr(os, "supports_dir_fd", set())


class TrustedParentError(OSError):
    """Raised when an ancestor path fails security or capability checks."""


@dataclass(frozen=True)
class FsCapabilities:
    """Runtime filesystem primitives available on the current OS."""

    platform: str
    supports_dir_fd: bool
    supports_o_nofollow: bool
    supports_o_directory: bool
    supports_posix_uid: bool


def fs_capabilities() -> FsCapabilities:
    """Inspect OS filesystem capabilities for the active Python runtime."""
    is_posix = os.name == "posix"
    return FsCapabilities(
        platform=sys.platform,
        supports_dir_fd=is_posix and _SUPPORTS_DIR_FD,
        supports_o_nofollow=hasattr(os, "O_NOFOLLOW"),
        supports_o_directory=hasattr(os, "O_DIRECTORY"),
        supports_posix_uid=is_posix and hasattr(os, "geteuid"),
    )


def same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    """Return whether two stat results refer to the same device and inode."""
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def trusted_uids() -> tuple[int, ...]:
    """Return UIDs allowed to own trusted path ancestors.

    Includes 0 (root), the effective UID, and 65534 (kernel overflow UID for
    unmapped root-owned ancestors inside user namespaces).
    """
    return (0, os.geteuid(), OVERFLOW_UID)


@dataclass
class TrustedParent:
    """Descriptor-pinned parent directory returned by :func:`open_trusted_parent`."""

    parent_fd: int | None
    leaf_name: str
    resolved_path: Path

    def release_fd(self) -> int:
        """Transfer ownership of ``parent_fd`` to the caller."""
        if self.parent_fd is None:
            raise TrustedParentError("parent descriptor already released")
        fd = self.parent_fd
        self.parent_fd = None
        return fd

    def close(self) -> None:
        """Close ``parent_fd`` if still owned by this handle."""
        if self.parent_fd is not None:
            fd = self.parent_fd
            self.parent_fd = None
            os.close(fd)

    def __enter__(self) -> TrustedParent:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def _check_ancestor_stat(
    info: os.stat_result,
    path: str,
    *,
    allowed_uids: tuple[int, ...],
    sticky_tmp_ancestors: tuple[str, ...],
) -> None:
    if not stat.S_ISDIR(info.st_mode) or info.st_uid not in allowed_uids:
        raise TrustedParentError(f"untrusted ancestor directory: {path}")
    permissions = stat.S_IMODE(info.st_mode)
    if permissions & 0o022:
        sticky_tmp = (
            info.st_uid in (0, OVERFLOW_UID)
            and bool(info.st_mode & stat.S_ISVTX)
            and bool(permissions & 0o002)
            and path in sticky_tmp_ancestors
        )
        if not sticky_tmp:
            raise TrustedParentError(f"writable ancestor directory: {path}")


def _close_trail(trail: list[tuple[int, str]], *, keep_root: bool = False) -> None:
    limit = 1 if keep_root else 0
    while len(trail) > limit:
        open_fd, _ = trail.pop()
        try:
            os.close(open_fd)
        except OSError:
            pass


def _read_trusted_symlink(
    cur_fd: int,
    part: str,
    path_info: os.stat_result,
    *,
    allowed_uids: tuple[int, ...],
) -> str:
    # Allow traversing system/user ancestor symlinks (e.g. /home -> var/home on
    # Fedora Atomic/Bazzite or /tmp -> private/tmp on macOS) only when owned by
    # root, the current user, or the unmapped overflow UID 65534.
    if path_info.st_uid not in allowed_uids:
        raise TrustedParentError(f"untrusted ancestor symlink owner: {part}")
    try:
        target = os.readlink(part, dir_fd=cur_fd)
    except OSError as exc:
        raise TrustedParentError(f"cannot read ancestor symlink: {part}") from exc
    if not target:
        raise TrustedParentError(f"empty ancestor symlink: {part}")
    return target


def _open_ancestor_dir(
    cur_fd: int,
    cur_path: str,
    part: str,
    path_info: os.stat_result,
    *,
    allowed_uids: tuple[int, ...],
    sticky_tmp_ancestors: tuple[str, ...],
) -> tuple[int, str]:
    if not stat.S_ISDIR(path_info.st_mode):
        raise TrustedParentError(f"ancestor is not a directory: {part}")
    try:
        next_fd = os.open(
            part,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=cur_fd,
        )
    except OSError as exc:
        raise TrustedParentError(f"cannot open ancestor directory: {part}") from exc
    try:
        fd_info = os.fstat(next_fd)
        if not same_inode(path_info, fd_info):
            raise TrustedParentError(f"ancestor changed while opening: {part}")
        next_path = f"/{part}" if cur_path == "/" else f"{cur_path}/{part}"
        _check_ancestor_stat(
            fd_info,
            next_path,
            allowed_uids=allowed_uids,
            sticky_tmp_ancestors=sticky_tmp_ancestors,
        )
    except BaseException:
        os.close(next_fd)
        raise
    return next_fd, next_path


def open_trusted_parent(
    root: Path | str,
    *,
    sticky_tmp_ancestors: tuple[str, ...] = STICKY_TMP_ANCESTORS,
) -> TrustedParent:
    """Open and verify every ancestor directory of *root* via descriptor walk.

    Resolves trusted ancestor symlinks (for example ``/home -> var/home`` on
    immutable Linux distros) while enforcing ownership and non-writable
    ancestor permissions, returning a pinned ``parent_fd`` for the leaf entry.
    """
    caps = fs_capabilities()
    if not (
        caps.supports_dir_fd
        and caps.supports_o_nofollow
        and caps.supports_o_directory
        and caps.supports_posix_uid
    ):
        raise TrustedParentError(f"unsupported filesystem capabilities on {caps.platform}")

    raw = os.fspath(root)
    if not isinstance(raw, str) or not raw:
        raise TrustedParentError("empty path")
    initial = Path(raw)
    if any(part == ".." for part in initial.parts):
        raise TrustedParentError("parent traversal '..' is not allowed")
    absolute = Path(os.path.abspath(raw))
    if absolute == Path("/") or absolute.name in ("", ".", ".."):
        raise TrustedParentError("invalid store root path")

    allowed_uids = trusted_uids()
    try:
        root_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except OSError as exc:
        raise TrustedParentError("cannot open root directory") from exc

    trail: list[tuple[int, str]] = [(root_fd, "/")]
    pending: deque[str] = deque(absolute.parts[1:-1])
    symlinks_followed = 0
    try:
        _check_ancestor_stat(
            os.fstat(root_fd),
            "/",
            allowed_uids=allowed_uids,
            sticky_tmp_ancestors=sticky_tmp_ancestors,
        )
        while pending:
            part = pending.popleft()
            if not part or part == ".":
                continue
            if part == "..":
                if len(trail) > 1:
                    popped_fd, _ = trail.pop()
                    os.close(popped_fd)
                continue
            cur_fd, cur_path = trail[-1]
            try:
                path_info = os.stat(part, dir_fd=cur_fd, follow_symlinks=False)
            except OSError as exc:
                raise TrustedParentError(f"cannot stat ancestor: {part}") from exc
            if stat.S_ISLNK(path_info.st_mode):
                symlinks_followed += 1
                if symlinks_followed > MAX_ANCESTOR_SYMLINKS:
                    raise TrustedParentError("too many ancestor symlinks")
                target = _read_trusted_symlink(
                    cur_fd, part, path_info, allowed_uids=allowed_uids,
                )
                if target.startswith("/"):
                    _close_trail(trail, keep_root=True)
                target_parts = [seg for seg in target.split("/") if seg]
                pending.extendleft(reversed(target_parts))
                continue
            trail.append(
                _open_ancestor_dir(
                    cur_fd,
                    cur_path,
                    part,
                    path_info,
                    allowed_uids=allowed_uids,
                    sticky_tmp_ancestors=sticky_tmp_ancestors,
                )
            )
        parent_fd, parent_path = trail.pop()
        resolved_root = Path(parent_path) / absolute.name
        try:
            _close_trail(trail)
        except BaseException:
            os.close(parent_fd)
            raise
        return TrustedParent(
            parent_fd=parent_fd,
            leaf_name=absolute.name,
            resolved_path=resolved_root,
        )
    except BaseException:
        _close_trail(trail)
        raise
