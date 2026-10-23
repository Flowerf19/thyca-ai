"""Per-bot model settings (``bots/<id>/settings.json``).

``TWIN.md`` owns identity/persona/policies; this file owns only the model
selection (``{"model": {"id": ..., "thinking": ...}}``) so the Settings page
and admin tools can edit it without rewriting the persona body. Reads never
migrate: a missing file means "fall back to the legacy card model", while a
present-but-invalid file is a hard error (no silent fallback).
"""
from __future__ import annotations

import json
import unicodedata
from pathlib import Path

from thyca.bots.card import BotModel
from thyca.config import atomic_write_text
from thyca.skills.store import _NAME_RE, NAME_MAX

SETTINGS_FILENAME = "settings.json"

#: Same shape bounds as per-turn model options (turn_options.MODEL_MAX).
MODEL_ID_MAX = 200

__all__ = [
    "SETTINGS_FILENAME",
    "SettingsError",
    "lock_path_for",
    "read_settings",
    "settings_path",
    "strip_card_model_text",
    "validate_model_id",
    "validate_thinking",
    "write_settings",
]


class SettingsError(RuntimeError):
    """Bot settings could not be read or written (safe message, no paths)."""


def _check_bot_id(bot_id: object) -> str:
    if (
        not isinstance(bot_id, str)
        or not _NAME_RE.fullmatch(bot_id)
        or len(bot_id) > NAME_MAX
    ):
        raise SettingsError("invalid bot id")
    return bot_id


def _check_controls(value: str, what: str) -> str:
    """Reject control characters (Unicode Cc, incl. DEL) in the original."""
    if any(unicodedata.category(char) == "Cc" for char in value):
        raise SettingsError(what)
    return value


def validate_model_id(model_id: object) -> str:
    """A registered model id or ``default`` (inherit the global default).

    Same shape bounds as per-turn model options: non-empty, max 200
    chars, no control characters (checked before stripping, so padding
    tricks cannot smuggle them in), stripped.
    """
    if not isinstance(model_id, str):
        raise SettingsError("invalid model")
    if not model_id or len(model_id) > MODEL_ID_MAX:
        raise SettingsError("invalid model")
    _check_controls(model_id, "invalid model")
    cleaned = model_id.strip()
    if not cleaned:
        raise SettingsError("invalid model")
    return cleaned


def validate_thinking(thinking: object) -> str | None:
    """Reasoning effort override, or None for inheritance.

    Missing (None) means "inherit the model's/provider's default"; an
    explicit value must be a bounded non-empty string (max 200 chars, no
    control characters — membership against the effective model is checked
    by the caller, which knows the config).
    """
    if thinking is None:
        return None
    if not isinstance(thinking, str):
        raise SettingsError("invalid thinking")
    if not thinking or len(thinking) > MODEL_ID_MAX:
        raise SettingsError("invalid thinking")
    _check_controls(thinking, "invalid thinking")
    cleaned = thinking.strip()
    if not cleaned:
        raise SettingsError("invalid thinking")
    return cleaned


def settings_path(root: Path, bot_id: str) -> Path:
    """Validated path of a bot's settings file (no filesystem access)."""
    _check_bot_id(bot_id)
    return Path(root) / "bots" / bot_id / SETTINGS_FILENAME


def lock_path_for(root: Path, bot_id: str) -> Path:
    """Per-bot cross-instance lock (serializes settings/card writes)."""
    _check_bot_id(bot_id)
    return Path(root) / "bots" / f"{bot_id}.lock"


def _real_bot_dir(root: Path, bot_id: str) -> Path:
    """The bot directory, or :class:`SettingsError` when it is missing.

    Never creates directories: saving settings for a missing or deleted
    bot is an error, not a recreation. Symlinked bot dirs are rejected
    (settings must live in real per-bot state, like the memory policy).
    """
    _check_bot_id(bot_id)
    bots_root = Path(root) / "bots"
    bot_dir = bots_root / bot_id
    try:
        if bot_dir.is_symlink() or not bot_dir.is_dir():
            raise SettingsError("unknown bot")
        # Grammar already pins [a-z0-9-], but resolve anyway so a
        # non-canonical spelling can never escape the bots root.
        if bots_root.resolve() not in bot_dir.resolve().parents:
            raise SettingsError("unknown bot")
    except OSError:
        raise SettingsError("unknown bot") from None
    return bot_dir


def read_settings(root: Path, bot_id: str) -> BotModel | None:
    """The persisted model, or None when the bot has no settings file.

    Raises :class:`SettingsError` for unreadable, malformed, or invalid
    files — callers fall back to the legacy card model only when the
    file is missing, never on corrupt content.
    """
    bot_dir = _real_bot_dir(root, bot_id)
    target = bot_dir / SETTINGS_FILENAME
    try:
        if target.is_symlink():
            raise SettingsError("bot settings unavailable")
        if not target.exists():
            # Only a truly absent entry falls back to the legacy card
            # model. A directory (or any other non-regular entry) at this
            # path is corruption, never a fallback.
            return None
        if not target.is_file():
            raise SettingsError("bot settings unavailable")
        raw = target.read_text(encoding="utf-8")
    except SettingsError:
        raise
    except (OSError, UnicodeDecodeError):
        raise SettingsError("bot settings unavailable") from None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise SettingsError("bot settings unavailable") from None
    if not isinstance(data, dict):
        raise SettingsError("bot settings unavailable")
    model = data.get("model")
    if not isinstance(model, dict):
        raise SettingsError("bot settings unavailable")
    unknown = [key for key in model if key not in ("id", "thinking")]
    if unknown or "id" not in model:
        raise SettingsError("bot settings unavailable")
    if set(data) != {"model"}:
        raise SettingsError("bot settings unavailable")
    try:
        model_id = validate_model_id(model["id"])
        thinking = validate_thinking(model.get("thinking"))
    except SettingsError:
        raise SettingsError("bot settings unavailable") from None
    return BotModel(id=model_id, thinking=thinking)


def write_settings(root: Path, bot_id: str, model: BotModel) -> BotModel:
    """Atomically persist one bot's model settings (0600).

    The caller must already hold the bot lock; like every
    ``atomic_write_text`` caller, the lock lives with the caller and this
    function never acquires it. Never creates the bot directory.
    """
    if not isinstance(model, BotModel):
        raise SettingsError("invalid model")
    model_id = validate_model_id(model.id)
    thinking = validate_thinking(model.thinking)
    normalized = BotModel(id=model_id, thinking=thinking)
    bot_dir = _real_bot_dir(root, bot_id)
    target = bot_dir / SETTINGS_FILENAME
    try:
        if target.is_symlink():
            raise SettingsError("bot settings unavailable")
    except OSError:
        raise SettingsError("bot settings unavailable") from None
    payload = {"model": {"id": normalized.id, "thinking": normalized.thinking}}
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    try:
        atomic_write_text(target, text, mode=0o600)
    except SettingsError:
        raise
    except Exception:
        raise SettingsError("bot settings unavailable") from None
    return normalized


def strip_card_model_text(text: str) -> str:
    """Remove only the frontmatter ``model`` key, preserving everything else.

    Unknown frontmatter values and the exact persona body survive; when the
    card carries no ``model`` key the text is returned unchanged. Raises
    :class:`SettingsError` when the frontmatter cannot be parsed (the
    caller must leave the card file untouched then, so a legacy model is
    never erased by a failed migration).
    """
    from thyca.skills.store import _FM_RE

    import yaml

    match = _FM_RE.match(text)
    if match is None:
        raise SettingsError("bot settings unavailable")
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        raise SettingsError("bot settings unavailable") from None
    if not isinstance(data, dict) or "model" not in data:
        return text
    del data["model"]
    front = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    return f"---\n{front}---\n{text[match.end():]}"
