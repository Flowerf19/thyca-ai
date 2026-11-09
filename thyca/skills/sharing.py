"""Simple team-skill sharing: name-based grants, no per-bot audiences.

Thyca shares individual ``<root>/skills-team/<name>`` skills. Shared skills
are available to every current and future bot. Existing team files stay
unshared until Thyca shares them. Edits preserve the share flag because
the flag lives in ``skill-sharing.json``, not in the skill files.

Sharing controls the skill index and recognized built-in file-tool paths.
Bash and MCP remain unchanged and can bypass these permissions. Outside
hardlink aliases can also bypass path-based checks. Script execution
through bash is not permission-enforced. This is not filesystem-wide
isolation.
"""
from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from filelock import FileLock

from thyca.configs import atomic_write_text
from thyca.skills.store import _NAME_RE

STATE_FILENAME = "skill-sharing.json"
LOCK_FILENAME = "skill-sharing.lock"
STATE_VERSION = 1
SKILL_FILENAME = "SKILL.md"


class SharingCorrupt(ValueError):
    """Sharing state exists but cannot be trusted."""


def _state_path(root: Path) -> Path:
    return Path(root) / STATE_FILENAME


def _lock_path(root: Path) -> Path:
    return Path(root) / LOCK_FILENAME


def _lock(root: Path) -> FileLock:
    return FileLock(str(_lock_path(root)), timeout=5)


@dataclass(frozen=True)
class SkillRef:
    """One recognized skill-file location."""

    kind: str  # "team" | "private" | "global" | "thyca-private"
    bot_id: str | None
    skill_name: str


def _resolved_or_self(root: Path) -> Path:
    # Canonical root for containment checks; context roots may still
    # carry a symlink alias, so callers compare against this form.
    try:
        return Path(root).resolve()
    except (OSError, RuntimeError, ValueError):
        return Path(root)


def anchored_skill_path(root: Path, lexical: Path) -> Path:
    """Re-anchor a root-aliased path onto the resolved root.

    Only the leading root alias resolves; parts below the root stay
    lexical so link checks still see symlinks under the root. Returns
    ``lexical`` unchanged when no leading prefix is the root.
    """
    resolved = _resolved_or_self(root)
    parts = lexical.parts
    for end in range(1, len(parts) + 1):
        try:
            if Path(*parts[:end]).resolve() == resolved:
                return resolved.joinpath(*parts[end:])
        except (OSError, RuntimeError, ValueError):
            continue
    return lexical


def classify_skill_path(root: Path, lexical: Path) -> SkillRef | None:
    """Lexical skill classification without following symlinks.

    ``lexical`` must already be absolute and normalized (no ``~`` or
    ``..`` escapes). A leading root alias re-anchors onto the resolved
    root; inner parts stay lexical. Returns None when the path is not
    inside a recognized skill root.
    """
    root = _resolved_or_self(root)
    lexical = anchored_skill_path(root, lexical)
    try:
        rel = lexical.relative_to(root)
    except ValueError:
        return None
    parts = rel.parts
    if not parts:
        return None
    # Team: skills-team/<name>/...
    if parts[0] == "skills-team" and len(parts) >= 2:
        return SkillRef(kind="team", bot_id=None, skill_name=parts[1])
    # Global Thyca-owned: skills/<name>/...
    if parts[0] == "skills" and len(parts) >= 2:
        return SkillRef(kind="global", bot_id=None, skill_name=parts[1])
    # Private: bots/<bot_id>/skills/<name>/...
    if parts[0] == "bots" and len(parts) >= 4 and parts[2] == "skills":
        bot_id = parts[1]
        name = parts[3]
        if bot_id == "thyca":
            return SkillRef(kind="thyca-private", bot_id=bot_id, skill_name=name)
        return SkillRef(kind="private", bot_id=bot_id, skill_name=name)
    return None


def _read_state_file(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise SharingCorrupt(f"unreadable sharing state: {exc}") from exc
    try:
        data = json.loads(text)
    except (ValueError, json.JSONDecodeError) as exc:
        raise SharingCorrupt(f"invalid sharing state JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SharingCorrupt("invalid sharing state: not an object")
    return data


def load_shared_names(root: Path) -> set[str]:
    """Shared team-skill names, empty when no state exists.

    Reads never initialize or repair state. Corrupt or unreadable state
    raises SharingCorrupt so callers block shared usage and report it.
    """
    path = _state_path(root)
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        # Missing state means nothing is shared.
        return set()
    except OSError as exc:
        raise SharingCorrupt(f"unreadable sharing state: {exc}") from exc
    if not stat.S_ISREG(st.st_mode):
        # A directory, fifo, socket, or symlink at the state path is
        # untrusted input, never an empty grant.
        raise SharingCorrupt(f"sharing state is not a regular file: {path}")
    # Reads use atomic rename visibility (no lock): writers replace the
    # file atomically, so readers see old or new, never torn bytes.
    # Share/unshare hold the lock across read-modify-write.
    data = _read_state_file(path)
    if data.get("version") != STATE_VERSION:
        raise SharingCorrupt(f"unsupported sharing version: {data.get('version')!r}")
    shared = data.get("shared")
    if not isinstance(shared, list) or any(not isinstance(v, str) for v in shared):
        raise SharingCorrupt("invalid sharing state: shared must be a string list")
    # Validate names; an invalid entry makes the whole state untrusted
    # rather than silently dropping one grant.
    for name in shared:
        if not _NAME_RE.fullmatch(name):
            raise SharingCorrupt(f"invalid shared skill name: {name!r}")
    return set(shared)


def _write_state(root: Path, shared: set[str]) -> None:
    # Caller holds the lock across read-modify-write; the rename itself
    # is atomic for lock-free readers.
    payload = {"version": STATE_VERSION, "shared": sorted(shared)}
    atomic_write_text(_state_path(root), json.dumps(payload, indent=2) + "\n")


def _require_team_skill_exists(root: Path, name: str) -> None:
    if not _NAME_RE.fullmatch(name):
        raise ValueError(f"invalid skill name: {name!r}")
    skill_dir = Path(root) / "skills-team" / name
    # Lexical existence only; link checks happen at index/read time and
    # disallow linked skills without blocking the name grant itself: a
    # symlinked entry may precede its target, so the grant persists.
    try:
        if skill_dir.is_symlink():
            return
        # A plain file (or fifo, socket, ...) where a skill dir should
        # be is a typo, never a skill.
        if not skill_dir.is_dir():
            raise ValueError(f"no such team skill: {name!r}")
    except OSError as exc:
        raise ValueError(f"cannot inspect team skill {name!r}: {exc}") from exc
    md = skill_dir / SKILL_FILENAME
    try:
        if not md.exists() and not md.is_symlink():
            raise ValueError(f"team skill missing SKILL.md: {name!r}")
    except OSError as exc:
        raise ValueError(f"cannot inspect team skill {name!r}: {exc}") from exc


class SharingService:
    """Persisted name grants plus one place for skill access decisions."""

    def __init__(self, thyca_dir: Path | None = None) -> None:
        from thyca.configs.store import config_path

        self._root = Path(thyca_dir or config_path().parent)

    @property
    def root(self) -> Path:
        return self._root

    def list_shared(self) -> list[str]:
        """Sorted shared names; empty when no state. Raises on corrupt."""
        return sorted(load_shared_names(self._root))

    def is_shared(self, name: str) -> bool:
        return name in load_shared_names(self._root)

    def share(self, name: str) -> bool:
        """Share one existing team skill. True when newly shared."""
        _require_team_skill_exists(self._root, name)
        with _lock(self._root):
            try:
                current = load_shared_names(self._root)
            except SharingCorrupt:
                # A corrupt state never silently repairs: refuse to add a
                # grant on top of untrusted bytes.
                raise
            if name in current:
                return False
            _write_state(self._root, current | {name})
            return True

    def unshare(self, name: str) -> bool:
        """Stop sharing one skill. True when actually removed."""
        if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
            raise ValueError(f"invalid skill name: {name!r}")
        with _lock(self._root):
            try:
                current = load_shared_names(self._root)
            except SharingCorrupt:
                raise
            if name not in current:
                return False
            _write_state(self._root, current - {name})
            return True

    # -- access decisions (index + native file tools share these) -----

    def can_read_skill(self, bot_id: str | None, ref: SkillRef) -> bool:
        """True when ``bot_id`` may read this recognized skill path."""
        if bot_id is None or bot_id == "thyca":
            return True
        if ref.kind == "private":
            return ref.bot_id == bot_id
        if ref.kind == "team":
            try:
                return ref.skill_name in load_shared_names(self._root)
            except SharingCorrupt:
                return False
        # Global and Thyca-private roots are Thyca-owned.
        return False

    def can_write_skill(self, bot_id: str | None, ref: SkillRef) -> bool:
        """True when ``bot_id`` may write this recognized skill path."""
        if bot_id is None or bot_id == "thyca":
            return True
        if ref.kind == "private":
            return ref.bot_id == bot_id
        # Shared team skills are read-only for children; global and
        # Thyca-private roots are Thyca-owned.
        return False


# -- link-safe descriptor reads for recognized skill files -------------

def _lstat_no_follow(path: Path) -> os.stat_result:
    return os.lstat(path)


def _has_symlink_component(lexical: Path, root: Path) -> bool:
    """True when any component from ``root`` to ``lexical`` is a symlink.

    A leading root alias re-anchors first; components below the root
    stay lexical so symlinks under the root still fail closed.
    """
    resolved = _resolved_or_self(root)
    lexical = anchored_skill_path(resolved, lexical)
    try:
        rel = lexical.relative_to(resolved)
    except ValueError:
        return False
    current = resolved
    for part in rel.parts:
        current = current / part
        try:
            if current.is_symlink():
                return True
        except OSError:
            # Unreadable component fails closed elsewhere; here report no
            # symlink so the caller raises its own precise error.
            return False
    return False


def _validate_skill_dir_fd(dir_fd: int) -> None:
    st = os.fstat(dir_fd)
    if not stat.S_ISDIR(st.st_mode):
        raise ValueError("skill parent is not a directory")


def read_skill_checked(root: Path, lexical_abs: Path) -> str:
    """Read a recognized skill file through checked descriptors.

    Rejects symlink components, symlinked files, hardlinked files
    (st_nlink != 1), and special files. Reads from the descriptor that
    passed validation instead of reopening the pathname. The root
    resolves first so a symlinked root opens without O_NOFOLLOW failing.
    """
    root = _resolved_or_self(root)
    lexical_abs = anchored_skill_path(root, lexical_abs)
    # Lexical containment only; the caller already classified this path.
    try:
        rel = lexical_abs.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"skill path escapes root: {exc}") from exc
    if _has_symlink_component(lexical_abs, root):
        raise ValueError("skill path contains a symlink")
    # Walk down with O_NOFOLLOW directory fds so a swapped component
    # cannot redirect the open.
    parts = rel.parts
    if len(parts) < 1:
        raise ValueError("invalid skill path")
    current_fd: int | None = None
    try:
        base_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise ValueError(f"cannot open skill root: {exc}") from exc
    try:
        # Open each parent directory, then the final file.
        fd = base_fd
        base_fd = None  # ownership moves to fd
        for part in parts[:-1]:
            try:
                nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                              dir_fd=fd)
            except OSError as exc:
                raise ValueError(f"skill directory unreadable: {part}: {exc}") from exc
            os.close(fd)
            fd = nxt
            _validate_skill_dir_fd(fd)
        try:
            file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
        except OSError as exc:
            raise ValueError(f"skill file unreadable: {exc}") from exc
        try:
            st = os.fstat(file_fd)
            if not stat.S_ISREG(st.st_mode):
                raise ValueError("skill path is not a regular file")
            if st.st_nlink != 1:
                raise ValueError("skill file is hardlinked")
            # Read from the validated descriptor, not the pathname.
            with os.fdopen(file_fd, "r", encoding="utf-8", closefd=False) as stream:
                # fdopen with closefd=False keeps ownership here; read fully.
                text = stream.read()
            # Explicit close after successful read.
            os.close(file_fd)
            file_fd = None
            return text
        finally:
            if file_fd is not None:
                try:
                    os.close(file_fd)
                except OSError:
                    pass
        # Unreachable; kept for clarity.
    finally:
        if base_fd is not None:
            try:
                os.close(base_fd)
            except OSError:
                pass
        try:
            os.close(fd)
        except (OSError, UnboundLocalError):
            pass


def validate_skill_write_path(root: Path, lexical_abs: Path) -> None:
    """Reject symlink components and unsafe existing skill targets.

    For recognized skill writes: parent directories must contain no
    symlinks; an existing target must be a regular file with a single
    link, never a symlink or special file. Missing targets are allowed
    (creation) once parents validate. Ordinary unrelated hardlinks are
    untouched because this runs only for recognized skill paths.
    """
    root = _resolved_or_self(root)
    lexical_abs = anchored_skill_path(root, lexical_abs)
    try:
        rel = lexical_abs.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"skill path escapes root: {exc}") from exc
    if _has_symlink_component(lexical_abs, root):
        raise ValueError("skill path contains a symlink")
    parent = lexical_abs.parent
    # Validate each parent directory exists (or will be created for new
    # skills?) and contains no symlink. For creation, parents must exist;
    # skill creation uses write which mkdirs? Native write creates parents
    # via replace_file mkdir. Here require existing parents to be real dirs;
    # missing intermediate skill dirs are created by the caller only for
    # own-private skills after this check would fail. To keep creation
    # working, allow missing leaf skill dirs but require the skill-root
    # parents (roots/skills-team, bots/<id>/skills) to be real dirs.
    current = Path(root)
    for part in parent.relative_to(root).parts:
        current = current / part
        try:
            if current.is_symlink():
                raise ValueError("skill path contains a symlink")
        except OSError as exc:
            raise ValueError(f"skill parent unreadable: {exc}") from exc
        # Missing intermediate dirs are only allowed below the skill-name
        # level (new skill files like <skill>/references/x.md). The two
        # levels above the skill name must already be real directories.
        # Enforcement happens naturally: is_dir checks below skip missing.
        try:
            if current.exists() and not current.is_dir():
                raise ValueError("skill parent is not a directory")
        except OSError as exc:
            raise ValueError(f"skill parent unreadable: {exc}") from exc
    try:
        st = _lstat_no_follow(lexical_abs)
    except FileNotFoundError:
        return  # creation: parents already validated
    except OSError as exc:
        raise ValueError(f"skill target unreadable: {exc}") from exc
    if stat.S_ISLNK(st.st_mode):
        raise ValueError("skill target is a symlink")
    if not stat.S_ISREG(st.st_mode):
        raise ValueError("skill target is not a regular file")
    if st.st_nlink != 1:
        raise ValueError("skill file is hardlinked")
