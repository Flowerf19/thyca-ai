"""Per-bot model settings endpoints (split from sessions_api).

``GET /api/bots/<id>/settings`` reads one bot's settings plus the resolved
effective connection; ``POST`` full-replaces ``{"model": {...}}``. Broad
``PATCH``/``DELETE`` on ``/api/bots/<id>`` stay removed (404 in routes).
"""
from __future__ import annotations

import sys
import traceback
from typing import TYPE_CHECKING

from thyca.bots.registry import RegistryError
from thyca.bots.settings import SettingsError
from thyca.serve.errors import _with_json
from thyca.serve.sessions_api import _missing_chat

if TYPE_CHECKING:
    from thyca.app.chat_app import ChatApp


def bot_settings_get(handler, app: ChatApp | None, bot_id: str) -> None:
    """``GET /api/bots/<id>/settings`` — settings + effective connection."""
    if _missing_chat(handler, app):
        return
    try:
        from thyca.app.bot_settings import BotSettingsError

        try:
            payload = app.bot_settings(bot_id)
        except BotSettingsError as exc:
            handler._json(400, {"error": str(exc)})
            return
    except RegistryError:
        handler._json(404, {"error": "bot not found"})
        return
    except SettingsError:
        handler._json(503, {"error": "bot settings unavailable"})
        return
    except Exception:
        traceback.print_exc(file=sys.stderr)
        handler._json(503, {"error": "bot settings unavailable"})
        return
    handler._json(200, payload)


def bot_settings_post(handler, app: ChatApp | None, bot_id: str) -> None:
    """``POST /api/bots/<id>/settings`` — full model replacement."""
    if _missing_chat(handler, app):
        return

    def run(payload: dict) -> None:
        from thyca.app.bot_settings import BotSettingsError

        try:
            body = app.update_bot_settings(bot_id, payload)
        except BotSettingsError as exc:
            handler._json(400, {"error": str(exc)})
            return
        except RegistryError:
            handler._json(404, {"error": "bot not found"})
            return
        except SettingsError:
            handler._json(503, {"error": "bot settings unavailable"})
            return
        except Exception:
            traceback.print_exc(file=sys.stderr)
            handler._json(503, {"error": "bot settings unavailable"})
            return
        handler._json(200, body)

    _with_json(handler, run)
