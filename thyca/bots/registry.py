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

from thyca.bots.card import (
    CARD_FILENAME,
    LEGACY_CARD_FILENAME,
    TEAM_TINTS,
    BotCard,
    BotCardDoc,
    parse_bot_card,
    read_bot_file,
    render_bot_card,
)
from thyca.config.store import config_path
from thyca.skills.store import SkillStore

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
        names can never disagree about one bot's identity."""
        target = self.card_path(bot_id)
        target.write_text(render_bot_card(card), encoding="utf-8")
        target.chmod(0o600)
        legacy = self.bot_dir(bot_id) / LEGACY_CARD_FILENAME
        if legacy.is_file():
            legacy.unlink()

    def ensure_thyca(self) -> BotCard:
        """Seed the Thyca system card on first run; return the loaded card."""
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o700)
        self.team_skills_root.mkdir(parents=True, exist_ok=True)
        self.team_skills_root.chmod(0o700)
        self._ensure_team_skill_defaults()
        if self.existing_card_path(SYSTEM_BOT_ID) is None:
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
        return doc.card

    def list(self) -> list[BotCardDoc]:
        if not self.root.is_dir():
            return []
        docs = [self.get(entry.name) for entry in self.root.iterdir() if entry.is_dir()]
        docs.sort(key=lambda doc: doc.card.bot_id if doc.card else "")
        return docs

    def get(self, bot_id: str) -> BotCardDoc:
        path = self.existing_card_path(bot_id)
        if path is None:
            return BotCardDoc(None, ok=False, error="unknown bot")
        return read_bot_file(path, bot_id)

    def require(self, bot_id: str) -> BotCard:
        doc = self.get(bot_id)
        if not doc.ok or doc.card is None:
            raise RegistryError(f"unknown or invalid bot: {bot_id} ({doc.error})")
        return doc.card

    def exists(self, bot_id: str) -> bool:
        return self.existing_card_path(bot_id) is not None

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

    def create(self, card: BotCard) -> BotCard:
        """Write a new bot directory from a validated card."""
        doc = parse_bot_card(render_bot_card(card), card.bot_id)
        if not doc.ok or doc.card is None:
            raise RegistryError(f"invalid card: {doc.error}")
        if self.exists(card.bot_id):
            raise RegistryError(f"bot already exists: {card.bot_id}")
        dest = self.bot_dir(card.bot_id)
        dest.mkdir(parents=True, exist_ok=True)
        dest.chmod(0o700)
        self._write_card(card.bot_id, card)
        (dest / "skills").mkdir(exist_ok=True)
        (dest / "sessions").mkdir(exist_ok=True)
        return doc.card

    def update_card(self, bot_id: str, card: BotCard) -> BotCard:
        """Replace the card of an existing bot (bots cannot self-edit —
        callers enforce who may invoke this; see admin tools)."""
        current = self.require(bot_id)
        if current.system and card.bot_id != bot_id:
            raise RegistryError("cannot rename the system bot")
        if card.bot_id != bot_id:
            raise RegistryError("card name must match bot id")
        if current.system and not card.system:
            raise RegistryError("cannot unset system on the Thyca card")
        doc = parse_bot_card(render_bot_card(card), bot_id)
        if not doc.ok or doc.card is None:
            raise RegistryError(f"invalid card: {doc.error}")
        # Preserve the paused flag unless the update explicitly changes it:
        # pause/restore own that transition.
        if card.paused != current.paused:
            raise RegistryError("use pause/restore to change paused state")
        self._write_card(bot_id, card)
        return doc.card

    def set_paused(self, bot_id: str, paused: bool) -> BotCard:
        card = self.require(bot_id)
        if card.system and paused:
            raise RegistryError("the Thyca system bot cannot be paused")
        updated = replace(card, paused=paused)
        self._write_card(bot_id, updated)
        return updated

    def delete(self, bot_id: str) -> None:
        """Remove the bot directory (card + private skills + private sessions).

        Team-promoted memory stays with the team (facade owns entries);
        private memory removal is a separate facade call the admin tool makes.
        """
        card = self.require(bot_id)
        if card.system:
            raise RegistryError("the Thyca system bot cannot be deleted")
        shutil.rmtree(self.bot_dir(bot_id), ignore_errors=True)

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
                    "status": "paused" if card.paused else "active",
                    "system": card.system,
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
