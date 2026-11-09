from __future__ import annotations

import os
from pathlib import Path

from thyca.memories.identity import current_bot_id


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

    def _lexical_abs(self, path: str) -> Path:
        # Lexical absolute path without following symlinks, for skill-root
        # classification and symlink-component checks.
        raw = absolutize(path)
        if not raw.is_absolute():
            raw = Path.cwd() / raw
        return Path(os.path.normpath(str(raw)))

    def _skill_refs_for(self, lexical: Path, resolved: Path):
        # Lexical and resolved skill identities; a mismatch is an alias.
        from thyca.skills.sharing import skill_refs_for

        return skill_refs_for(self.thyca_dir, lexical, resolved)

    def _deny_reserved_sharing(self, lexical: Path, target: Path) -> None:
        # Reserved sharing files are never writable via native tools, on any
        # path identity including aliases. Both spellings are checked so a
        # symlink cannot dodge the reservation in either direction.
        from thyca.skills.sharing import is_reserved_sharing_file

        if is_reserved_sharing_file(self.thyca_dir, lexical) or is_reserved_sharing_file(self.thyca_dir, target):
            raise PathDenied(f"write denied: {target}")

    def deny_write(self, path: str) -> Path:
        target = self.resolve(path)
        try:
            lexical = self._lexical_abs(path)
        except ValueError:
            lexical = target
        # Reserved sharing state (plus lock and exact publication temp) is
        # managed only through the Thyca tool, never via native file tools.
        # Both lexical and resolved identities are checked so aliases cannot
        # dodge the reservation.
        self._deny_reserved_sharing(lexical, target)
        # Recognized skill writes use the sharing gate plus link checks on
        # both identities; a lexical/resolved mismatch is an alias into or
        # out of the skill roots and fails closed. Ordinary team-control
        # rules still apply to non-skill paths.
        from thyca.skills.sharing import SharingService, skill_refs_match, validate_skill_write_path

        ref_lex, ref_res = self._skill_refs_for(lexical, target)
        if not skill_refs_match(ref_lex, ref_res):
            raise PathDenied(f"write denied: {target}")
        ref = ref_lex if ref_lex is not None else ref_res
        if ref is not None:
            svc = SharingService(self.thyca_dir)
            if not svc.can_write_skill(current_bot_id(), ref):
                raise PathDenied(f"write denied: {target}")
            try:
                validate_skill_write_path(self.thyca_dir, lexical)
            except ValueError as exc:
                raise PathDenied(f"write denied: {target}: {exc}") from exc
            # The resolved destination must validate too; otherwise an
            # outer alias could pass the lexical check and land inside.
            try:
                validate_skill_write_path(self.thyca_dir, target)
            except ValueError as exc:
                raise PathDenied(f"write denied: {target}: {exc}") from exc
            return target
        if self._blocked(target):
            raise PathDenied(f"write denied: {target}")
        return target

    def deny_read(self, path: str) -> Path:
        """Resolve a read target, refusing bot-sensitive files to child bots."""
        target = self.resolve(path)
        try:
            lexical = self._lexical_abs(path)
        except ValueError:
            lexical = target
        # Lexical and resolved skill identities must agree; any alias into
        # or out of the skill roots fails closed before authorization.
        from thyca.skills.sharing import SharingService, _has_symlink_component, skill_refs_match

        ref_lex, ref_res = self._skill_refs_for(lexical, target)
        if not skill_refs_match(ref_lex, ref_res):
            raise PathDenied(f"read denied: {target}")
        ref = ref_lex if ref_lex is not None else ref_res
        if ref is not None:
            svc = SharingService(self.thyca_dir)
            if not svc.can_read_skill(current_bot_id(), ref):
                raise PathDenied(f"read denied: {target}")
            # Link safety before any content read: symlink components,
            # symlinked files, hardlinked files, and special files fail.
            # Both identities are checked so an outer alias cannot dodge the
            # component scan and the resolved file cannot be a swapped link.
            try:
                if _has_symlink_component(lexical, self.thyca_dir):
                    raise PathDenied(f"read denied: {target}")
                if _has_symlink_component(target, self.thyca_dir):
                    raise PathDenied(f"read denied: {target}")
                for candidate in (lexical, target):
                    try:
                        st = os.lstat(candidate)
                    except FileNotFoundError:
                        continue
                    import stat as _stat

                    if not _stat.S_ISREG(st.st_mode):
                        raise PathDenied(f"read denied: {target}")
                    if st.st_nlink != 1:
                        raise PathDenied(f"read denied: {target}")
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise PathDenied(f"read denied: {target}: {exc}") from exc
            if self._group_sensitive(target):
                raise PathDenied(f"read denied: {target}")
            return target
        if self._bot_sensitive(target):
            raise PathDenied(f"read denied: {target}")
        if self._group_sensitive(target):
            raise PathDenied(f"read denied: {target}")
        return target

    def _group_sensitive(self, target: Path) -> bool:
        """Raw private state paths denied in group turns and pinned private turns.

        Root memory daily files, room transcripts, per-bot workspace
        private state (private_followups), raw sqlite indexes and user
        profile files bypass the visibility-filtered facade, so a group
        turn reads them only through memory_get/search — never from disk.
        A private invocation with pinned denied origins keeps the same
        raw-file denial; its filtered memory tools still allow the bot's
        own entries. No bound view, CLI and DM windows keep the old behavior."""
        from thyca.memories.visibility import current_memory_view

        view = current_memory_view()
        if view is None:
            return False
        is_group = getattr(view.window, "kind", None) == "group"
        denied = getattr(view, "denied_origins", frozenset())
        # Pinned private denial keeps the raw-file restriction when the
        # window is absent; otherwise preserve DM and unbound behavior.
        if not is_group and not denied:
            return False
        root = self.thyca_dir
        try:
            target.relative_to(root)
        except ValueError:
            return False
        for folder in (root / "memory", root / "sessions"):
            try:
                target.relative_to(folder)
            except ValueError:
                continue
            return True
        if target.name.startswith("memory.sqlite"):
            return True
        if target == root / "USER.md":
            return True
        bots_dir = root / "bots"
        try:
            rel = target.relative_to(bots_dir)
        except ValueError:
            return False
        parts = rel.parts
        if len(parts) >= 2 and parts[-1] == "USER.md":
            return True
        if len(parts) >= 2 and parts[1] == "workspace":
            return True
        return False

    def _bot_sensitive(self, target: Path) -> bool:
        """Team-control files a non-system bot must never touch.

        A child bot that could edit ``bots/**`` (any TWIN.md), the live
        ``config.json``, or the shared ``skills-team/**`` store could
        escalate its own card, and the runtime re-reads the card on every
        turn. Room/internal transcripts under ``sessions/`` are denied too
        (phase 2A): a bot reads room history only through the curated
        room-context delta, never by opening transcript files — this also
        keeps other bots' DMs unreadable from disk. The user (no bot
        bound) and the Thyca system bot keep full access; every other
        in-flight bot is denied, read and write."""
        bot_id = current_bot_id()
        if bot_id is None or bot_id == "thyca":
            return False
        root = self.thyca_dir
        if target == root / "config.json":
            return True
        for folder in (root / "bots", root / "skills-team", root / "sessions"):
            try:
                target.relative_to(folder)
            except ValueError:
                continue
            return True
        return False

    def _blocked(self, target: Path) -> bool:
        if self._bot_sensitive(target):
            return True
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
