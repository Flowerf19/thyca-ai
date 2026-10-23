"""Per-bot model settings ownership (served at ``/api/bots/<id>/settings``).

Thin ownership layer over the registry + config: validation here raises
:class:`BotSettingsError` with safe constant messages (the HTTP layer maps
them to 400); unknown bots raise :class:`RegistryError` (404); unreadable
or unwritable settings raise :class:`SettingsError` (503).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from thyca.bots.card import BotModel
from thyca.bots.registry import RegistryError
from thyca.bots.settings import SettingsError

if TYPE_CHECKING:
    from thyca.bots.registry import BotRegistry
    from thyca.config import Config


class BotSettingsError(ValueError):
    """A settings payload the client must fix; the message is the reason."""


def _configured_model(card) -> BotModel:
    if card.model is not None:
        return card.model
    return BotModel(id="default", thinking=None)


def _resolve_effective(cfg: Config, card, configured: BotModel) -> dict:
    """Resolved model/provider/effort a turn with these settings would use."""
    from thyca.app.turn_options import resolve_bot_cfg, resolve_bot_provider

    resolved_cfg = resolve_bot_cfg(cfg, card)
    provider = resolve_bot_provider(resolved_cfg, card)
    return {
        "model": resolved_cfg.defaultModel,
        "provider": resolved_cfg.provider_id_for(resolved_cfg.defaultModel),
        "thinking": provider.reasoningEffort,
    }


def get_bot_settings(bots: BotRegistry, cfg: Config, bot_id: str) -> dict:
    """Read one bot's settings plus the resolved effective connection."""
    if not bots.exists(bot_id):
        raise RegistryError(f"unknown or invalid bot: {bot_id} (unknown bot)")
    doc = bots.get(bot_id)
    if not doc.ok or doc.card is None:
        # The card exists but cannot be read (corrupt TWIN or settings):
        # report unavailability, never a silent fallback.
        raise SettingsError("bot settings unavailable")
    card = doc.card
    configured = _configured_model(card)
    return {
        "bot_id": bot_id,
        "model": {"id": configured.id, "thinking": configured.thinking},
        "effective": _resolve_effective(cfg, card, configured),
    }


def update_bot_settings(
    bots: BotRegistry, cfg: Config, bot_id: str, payload: dict
) -> dict:
    """Full-replacement update of one bot's model settings."""
    model = _clean_payload(payload)
    _check_against_config(cfg, model)
    if not bots.exists(bot_id):
        raise RegistryError(f"unknown or invalid bot: {bot_id} (unknown bot)")
    try:
        card = bots.update_settings(bot_id, model)
    except RegistryError:
        # The bot existed a moment ago: the card or settings underneath
        # are unreadable, so report unavailability, not "not found".
        raise SettingsError("bot settings unavailable") from None
    except SettingsError as exc:
        raise SettingsError(str(exc)) from None
    configured = _configured_model(card)
    return {
        "bot_id": bot_id,
        "model": {"id": configured.id, "thinking": configured.thinking},
        "effective": _resolve_effective(cfg, card, configured),
    }


def _clean_payload(payload: object) -> BotModel:
    if not isinstance(payload, dict) or set(payload) != {"model"}:
        raise BotSettingsError("invalid body")
    raw = payload["model"]
    if not isinstance(raw, dict) or "id" not in raw:
        raise BotSettingsError("invalid body")
    if any(key not in ("id", "thinking") for key in raw):
        raise BotSettingsError("invalid body")
    from thyca.bots.settings import validate_model_id, validate_thinking

    try:
        model_id = validate_model_id(raw["id"])
    except SettingsError:
        raise BotSettingsError("invalid model") from None
    try:
        thinking = validate_thinking(raw.get("thinking"))
    except SettingsError:
        raise BotSettingsError("invalid thinking") from None
    return BotModel(id=model_id, thinking=thinking)


def _check_against_config(cfg: Config, model: BotModel) -> None:
    """Membership of the model id and the effort against the live config."""
    if model.id != "default" and model.id != cfg.defaultModel and model.id not in cfg.models:
        raise BotSettingsError("unknown model")
    if model.thinking is None:
        return
    effective_id = cfg.defaultModel if model.id == "default" else model.id
    registered = cfg.models.get(effective_id)
    own_set = registered.reasoningEfforts if registered is not None else ()
    from thyca.config import REASONING_EFFORTS

    allowed = own_set or REASONING_EFFORTS
    # Safe constant only: never echo the raw value or the configured set
    # (config contents stay server-side).
    if model.thinking not in allowed:
        raise BotSettingsError("invalid thinking")
