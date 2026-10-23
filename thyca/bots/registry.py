"""Bot registry: ``~/.thyca/bots/<bot-id>/`` directories + TWIN.md cards.

Backend owns the registry (master plan §2): each twin bot is a folder
with a ``TWIN.md`` card (parsed by :mod:`thyca.bots.card`; legacy
``BOT.md`` cards stay readable), a private ``skills/`` dir, and a
private ``sessions/`` dir. Team-shared skills live in
``~/.thyca/skills-team/`` (seeded from packaged defaults).
"""
from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

from filelock import FileLock

from thyca.bots.card import (
    CARD_FILENAME,
    CHILD_AVATAR_VARIANTS,
    LEGACY_CARD_FILENAME,
    TEAM_TINTS,
    BotCard,
    BotCardDoc,
    BotModel,
    effective_avatar,
    parse_bot_card,
    read_bot_file,
    render_bot_card,
)
from thyca.bots.settings import (
    SettingsError,
    lock_path_for,
    read_settings,
    settings_path as _settings_path,
    strip_card_model_text,
    write_settings,
)
from thyca.config import atomic_write_text
from thyca.config.store import config_path
from thyca.skills.store import _NAME_RE, NAME_MAX, SkillStore

SYSTEM_BOT_ID = "thyca"

_PACKAGED_BOTS = Path(__file__).resolve().parents[1] / "seeds" / "bots"
_PACKAGED_TEAM_SKILLS = Path(__file__).resolve().parents[1] / "seeds" / "skills-team"

__all__ = ["BotRegistry", "CARD_FILENAME", "RegistryError", "SYSTEM_BOT_ID"]


class RegistryError(RuntimeError):
    """Bot registry could not read or mutate a bot entry."""


class BotRegistry:
    """CRUD over bot directories. No LLM, no turns, no I/O policy."""

    def __init__(self, thyca_dir: Path | None = None) -> None:
        # Single source for the home default: config_path().parent.
        self.thyca_dir = Path(thyca_dir or config_path().parent)

    @property
    def root(self) -> Path:
        return self.thyca_dir / "bots"

    @property
    def team_skills_root(self) -> Path:
        return self.thyca_dir / "skills-team"

    def bot_dir(self, bot_id: str) -> Path:
        return self.root / bot_id

    def bot_user_text(self, bot_id: str) -> str:
        """This bot's own USER.md, or "" when missing/unreadable (phase 1E).

        The sweep writes it; prompts read it. Never raises for a missing
        file — symlinks are treated as absent like the memory read policy."""
        path = self.bot_dir(bot_id) / "USER.md"
        try:
            if not path.is_file() or path.is_symlink():
                return ""
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def card_path(self, bot_id: str) -> Path:
        """Write target for the card (always the canonical ``TWIN.md``)."""
        return self.bot_dir(bot_id) / CARD_FILENAME

    def existing_card_path(self, bot_id: str) -> Path | None:
        """The card file to read: ``TWIN.md`` wins, legacy ``BOT.md``
        remains readable for directories created before the rename."""
        for name in (CARD_FILENAME, LEGACY_CARD_FILENAME):
            path = self.bot_dir(bot_id) / name
            if path.is_file():
                return path
        return None

    def _write_card(self, bot_id: str, card: BotCard) -> None:
        """Write the canonical card and drop any legacy copy, so the two
        names can never disagree about one bot's identity.

        The model always lives in ``settings.json`` now: the persisted
        card is written without it (settings win on every read). The
        caller holds the bot lock; the write is atomic (0600)."""
        target = self.card_path(bot_id)
        try:
            if target.is_symlink():
                raise SettingsError("bot settings unavailable")
            atomic_write_text(
                target, render_bot_card(replace(card, model=None)), mode=0o600
            )
        except SettingsError:
            raise
        except OSError as exc:
            raise RegistryError(f"cannot write card: {exc}") from None
        legacy = self.bot_dir(bot_id) / LEGACY_CARD_FILENAME
        try:
            if legacy.is_file() and not legacy.is_symlink():
                legacy.unlink()
        except OSError:
            pass

    def _real_bot_dir_or_none(self, bot_id: str) -> Path | None:
        """The bot directory when it is a real directory, else None.

        Never follows symlinks: a symlinked (or missing) bot dir means
        there is no readable bot here."""
        bot_dir = self.bot_dir(bot_id)
        try:
            if bot_dir.is_symlink() or not bot_dir.is_dir():
                return None
        except OSError:
            return None
        return bot_dir

    def _bot_lock(self, bot_id: str) -> FileLock:
        """Per-bot cross-instance lock for settings/card writes.

        The lock file lives at ``bots/<id>.lock`` and is never unlinked:
        unlinking splits waiters across inodes, so the inode stays stable
        for the bot's lifetime (even across delete).
        """
        return FileLock(str(lock_path_for(self.thyca_dir, bot_id)), timeout=5)

    def _persist_settings_first(self, bot_id: str, card: BotCard) -> BotModel:
        """Persist a card's model to ``settings.json`` before any TWIN write.

        Settings always win on read, so a later TWIN rewrite can never
        erase the model first: the settings write lands before the card
        write, and a settings failure leaves the legacy TWIN untouched."""
        model = card.model if card.model is not None else BotModel(id="default")
        try:
            return write_settings(self.thyca_dir, bot_id, model)
        except SettingsError as exc:
            raise RegistryError(str(exc)) from None

    def ensure_thyca(self) -> BotCard:
        """Seed the Thyca system card on first run; return the loaded card.

        Settings are seeded only for a newly created system bot — an
        existing card is never migrated by a read."""
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o700)
        self.team_skills_root.mkdir(parents=True, exist_ok=True)
        self.team_skills_root.chmod(0o700)
        self._ensure_team_skill_defaults()
        seeded = self.existing_card_path(SYSTEM_BOT_ID) is None
        if seeded:
            target = self.card_path(SYSTEM_BOT_ID)
            template = _PACKAGED_BOTS / SYSTEM_BOT_ID / CARD_FILENAME
            if not template.is_file():
                raise RegistryError("packaged Thyca card missing")
            dest_dir = self.bot_dir(SYSTEM_BOT_ID)
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(template, target)
            target.chmod(0o600)
        doc = self.get(SYSTEM_BOT_ID)
        if not doc.ok or doc.card is None:
            raise RegistryError(f"Thyca system card invalid: {doc.error}")
        if seeded:
            # First run only: seed settings from the template card, then
            # strip the model key so the canonical TWIN holds no duplicate.
            with self._bot_lock(SYSTEM_BOT_ID):
                self._persist_settings_first(SYSTEM_BOT_ID, doc.card)
                self._strip_legacy_model(SYSTEM_BOT_ID)
            doc = self.get(SYSTEM_BOT_ID)
            if not doc.ok or doc.card is None:
                raise RegistryError(f"Thyca system card invalid: {doc.error}")
        return doc.card

    def list(self) -> list[BotCardDoc]:
        if not self.root.is_dir():
            return []
        docs = [self.get(entry.name) for entry in self.root.iterdir() if entry.is_dir()]
        docs.sort(key=lambda doc: doc.card.bot_id if doc.card else "")
        return docs

    def get(self, bot_id: str) -> BotCardDoc:
        if not isinstance(bot_id, str) or not _NAME_RE.fullmatch(
            bot_id
        ) or len(bot_id) > NAME_MAX:
            return BotCardDoc(None, ok=False, error="invalid bot id")
        if self._real_bot_dir_or_none(bot_id) is None:
            return BotCardDoc(None, ok=False, error="unknown bot")
        path = self.existing_card_path(bot_id)
        if path is None:
            return BotCardDoc(None, ok=False, error="unknown bot")
        try:
            if path.is_symlink():
                return BotCardDoc(None, ok=False, error="invalid bot card")
        except OSError:
            return BotCardDoc(None, ok=False, error="invalid bot card")
        doc = read_bot_file(path, bot_id)
        if not doc.ok or doc.card is None:
            return doc
        return self._hydrate(doc.card)

    def require(self, bot_id: str) -> BotCard:
        doc = self.get(bot_id)
        if not doc.ok or doc.card is None:
            raise RegistryError(f"unknown or invalid bot: {bot_id} ({doc.error})")
        return doc.card

    def exists(self, bot_id: str) -> bool:
        if not isinstance(bot_id, str) or not _NAME_RE.fullmatch(
            bot_id
        ) or len(bot_id) > NAME_MAX:
            return False
        return self.existing_card_path(bot_id) is not None

    def _hydrate(self, card: BotCard) -> BotCardDoc:
        """Overlay ``settings.json`` on a parsed card (settings always win).

        A missing file falls back to the legacy card model with no
        writes; an unreadable or invalid file marks the bot invalid
        (the roster skips it, like any other broken card)."""
        try:
            settings = read_settings(self.thyca_dir, card.bot_id)
        except SettingsError as exc:
            return BotCardDoc(None, ok=False, error=f"invalid bot settings: {exc}")
        if settings is None:
            return BotCardDoc(card, ok=True)
        return BotCardDoc(replace(card, model=settings), ok=True)

    def settings_path(self, bot_id: str) -> Path:
        """Validated path of a bot's ``settings.json`` (no I/O)."""
        try:
            return _settings_path(self.thyca_dir, bot_id)
        except SettingsError as exc:
            raise RegistryError(str(exc)) from None

    def suggest_tint(self) -> str:
        """Least-used display tint among existing cards (FE palette).

        Creation without an explicit tint used to hard-default every
        bot to ``accent``, so the whole team rendered one color. The
        suggested value is still just a card field — explicit tints
        always win, and the FE falls back by hash for unknown values."""
        counts = {tint: 0 for tint in TEAM_TINTS}
        for doc in self.list():
            if doc.card is not None and doc.card.tint in counts:
                counts[doc.card.tint] += 1
        return min(TEAM_TINTS, key=lambda tint: counts[tint])

    def suggest_avatar(self) -> str:
        """Least-used child avatar variant among existing cards.

        Mirrors :meth:`suggest_tint`: creation without an explicit
        avatar persists the suggestion, so twins stay distinguishable
        instead of every new bot rendering the same note shape."""
        counts = {variant: 0 for variant in CHILD_AVATAR_VARIANTS}
        for doc in self.list():
            if doc.card is not None and not doc.card.system:
                variant = effective_avatar(doc.card)
                if variant in counts:
                    counts[variant] += 1
        return min(CHILD_AVATAR_VARIANTS, key=lambda variant: counts[variant])

    def create(self, card: BotCard) -> BotCard:
        """Write a new bot directory from a validated card.

        Settings persist first, then the canonical TWIN (without the
        model key); the returned card is hydrated from settings."""
        doc = parse_bot_card(render_bot_card(card), card.bot_id)
        if not doc.ok or doc.card is None:
            raise RegistryError(f"invalid card: {doc.error}")
        if self.exists(card.bot_id):
            raise RegistryError(f"bot already exists: {card.bot_id}")
        dest = self.bot_dir(card.bot_id)
        dest.mkdir(parents=True, exist_ok=True)
        dest.chmod(0o700)
        with self._bot_lock(card.bot_id):
            self._persist_settings_first(card.bot_id, doc.card)
            self._write_card(card.bot_id, doc.card)
            (dest / "skills").mkdir(exist_ok=True)
            (dest / "sessions").mkdir(exist_ok=True)
        return self.require(card.bot_id)

    def update_card(
        self, bot_id: str, card: BotCard, *, model_changed: bool = True
    ) -> BotCard:
        """Replace the card of an existing bot (bots cannot self-edit —
        callers enforce who may invoke this; see admin tools).

        Everything (re-read, validation, settings persist, card write)
        happens inside the bot lock so a concurrent settings save cannot
        be clobbered. ``model_changed=False`` keeps the freshly loaded
        model for identity-only edits built from a stale snapshot."""
        with self._bot_lock(bot_id):
            current = self.require(bot_id)
            if (current.system or bot_id == "thyca") and card.name != current.name:
                raise RegistryError("cannot rename the system bot")
            if card.bot_id != bot_id:
                raise RegistryError("card id must match bot id")
            if current.system and not card.system:
                raise RegistryError("cannot unset system on the Thyca card")
            if not model_changed:
                card = replace(card, model=current.model)
            doc = parse_bot_card(render_bot_card(card), bot_id)
            if not doc.ok or doc.card is None:
                raise RegistryError(f"invalid card: {doc.error}")
            # Preserve the paused flag unless the update explicitly changes it:
            # pause/restore own that transition.
            if card.paused != current.paused:
                raise RegistryError("use pause/restore to change paused state")
            self._persist_settings_first(bot_id, doc.card)
            self._write_card(bot_id, doc.card)
        return self.require(bot_id)

    def update_settings(self, bot_id: str, model: BotModel) -> BotCard:
        """Replace one bot's model settings (the dedicated edit API).

        Writes ``settings.json`` first, then strips only the frontmatter
        ``model`` key from the card — persona, policies, and unknown
        metadata survive. Never migrates on read; never recreates a
        missing bot (existence is re-checked inside the lock). A strip
        failure raises (the card stays intact, settings stay
        authoritative) instead of returning a silently duplicated 200."""
        with self._bot_lock(bot_id):
            self.require(bot_id)
            try:
                write_settings(self.thyca_dir, bot_id, model)
            except SettingsError as exc:
                raise RegistryError(str(exc)) from None
            self._strip_legacy_model(bot_id)
        return self.require(bot_id)

    def _strip_legacy_model(self, bot_id: str) -> None:
        """Drop a stale frontmatter ``model`` key after settings landed.

        Raises on any read/parse/write failure (the card is left intact
        and settings stay authoritative — no rollback, no delete). Never
        follows a symlinked card target."""
        path = self.existing_card_path(bot_id)
        if path is None:
            return
        try:
            if path.is_symlink():
                raise SettingsError("bot settings unavailable")
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise RegistryError("bot settings unavailable") from exc
        try:
            stripped = strip_card_model_text(text)
        except SettingsError as exc:
            raise RegistryError("bot settings unavailable") from exc
        if stripped == text:
            return
        target = self.card_path(bot_id)
        try:
            if target.is_symlink():
                raise SettingsError("bot settings unavailable")
            atomic_write_text(target, stripped, mode=0o600)
        except SettingsError:
            raise
        except OSError as exc:
            raise RegistryError("bot settings unavailable") from exc
        legacy = self.bot_dir(bot_id) / LEGACY_CARD_FILENAME
        try:
            if target != path and legacy.is_file() and not legacy.is_symlink():
                legacy.unlink()
        except OSError:
            pass

    def set_paused(self, bot_id: str, paused: bool) -> BotCard:
        # The full read-modify-write happens inside the lock so a
        # concurrent settings save cannot be overwritten with a stale model.
        with self._bot_lock(bot_id):
            card = self.require(bot_id)
            if card.system and paused:
                raise RegistryError("the Thyca system bot cannot be paused")
            updated = replace(card, paused=paused)
            self._persist_settings_first(bot_id, updated)
            self._write_card(bot_id, updated)
        return self.require(bot_id)

    def delete(self, bot_id: str) -> None:
        """Remove the bot directory (card + private skills + private sessions).

        Team-promoted memory stays with the team (facade owns entries);
        private memory removal is a separate facade call the admin tool makes.

        Holds the bot lock across require + rmtree so a concurrent
        settings save cannot resurrect the directory; the lock inode
        itself is retained (never unlinked).
        """
        with self._bot_lock(bot_id):
            card = self.require(bot_id)
            if card.system:
                raise RegistryError("the Thyca system bot cannot be deleted")
            shutil.rmtree(self.bot_dir(bot_id))

    def team_index_text(self) -> str:
        """``name — role`` lines for every valid card (CBE-2).

        Injected into every bot's prompt like the skills index, so bots know
        whom to call. Prompt-facing only: long card descriptions stay on
        the card / public API and never ride every prompt. The id is the
        name (folder grammar), so one field serves both.
        """
        lines = []
        for doc in self.list():
            if doc.ok and doc.card is not None and not doc.card.paused:
                lines.append(f"- {doc.card.name} — {doc.card.role}")
            elif not doc.ok:
                lines.append(f"- {(doc.card.bot_id if doc.card else '?')} (TWIN.md invalid: {doc.error})")
        return "\n".join(lines)

    def skill_index_text(self, bot_id: str) -> str:
        """Private skills + team skills for one bot; private wins on clash."""
        proto = SkillStore(self.thyca_dir)
        private = self.bot_dir(bot_id) / "skills"
        seen: dict[str, str] = {}
        order: list[str] = []
        for path, owned in ((private, True), (self.team_skills_root, False)):
            if not path.is_dir():
                continue
            metas = [proto._meta(entry) for entry in path.iterdir() if entry.is_dir()]
            metas.sort(key=lambda meta: meta.name)
            for meta in metas:
                if meta.name in seen and not owned:
                    continue
                if meta.name not in seen:
                    order.append(meta.name)
                if meta.ok:
                    description = meta.description
                    if len(description) > 256:
                        description = description[:256].rstrip() + "…"
                    seen[meta.name] = f"- {meta.name} — {description}"
                else:
                    seen[meta.name] = f"- {meta.name} (SKILL.md invalid: {meta.error})"
        return "\n".join(seen[name] for name in order)

    def public_list(self) -> list[dict]:
        """GET /api/bots payload: public card fields + derived status."""
        out = []
        for doc in self.list():
            if not doc.ok or doc.card is None:
                continue
            card = doc.card
            out.append(
                {
                    "id": card.bot_id,
                    "name": card.name,
                    "role": card.role,
                    "description": card.description,
                    "tint": card.tint,
                    "avatar": effective_avatar(card),
                    "status": "paused" if card.paused else "active",
                    "system": card.system,
                    # Read-only drawer specs (FE normalizeBots already
                    # carries these display fields when supplied).
                    "model": (
                        {"id": card.model.id, "thinking": card.model.thinking}
                        if card.model is not None else None
                    ),
                    "bash": card.bash,
                    "skills": list(card.skills),
                    "schedule": card.schedule,
                }
            )
        return out

    def _ensure_team_skill_defaults(self) -> None:
        if not _PACKAGED_TEAM_SKILLS.is_dir():
            return
        for packaged in sorted(_PACKAGED_TEAM_SKILLS.iterdir()):
            template = packaged / "SKILL.md"
            if not packaged.is_dir() or not template.is_file():
                continue
            target = self.team_skills_root / packaged.name / "SKILL.md"
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(template, target)
            target.chmod(0o600)
