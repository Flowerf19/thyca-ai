"""Session read/turn/rename/delete endpoints (split from bridge.py).

Functions take the handler duck-typed (``_json`` / ``_read_json`` /
``_read_body`` / ``wfile``) plus the chat app, so this module never imports
the handler class. ``ChatApp`` is only an annotation (``TYPE_CHECKING``);
the turn exceptions from ``thyca.app.chat_app`` are imported lazily —
that module imports ``thyca.serve.turn_state``, so a top-level import here
would cycle (M7-TASK-013).
"""
from __future__ import annotations

import json
import re
import sys
import traceback
from typing import TYPE_CHECKING
from urllib.parse import parse_qs

from thyca.bots.registry import RegistryError

from thyca.serve.bridge import _log_turn_failure, pump_stream, stream_turn
from thyca.serve.errors import _with_json, parse_turn_body, public_turn_error
from thyca.sessions.store import SESSION_ID_PATTERN
from thyca.sessions.wire import delete_error, rename_error, session_http_error

if TYPE_CHECKING:
    from thyca.app.chat_app import ChatApp

# Same grammar the session routes use, composed from the sessions-owned pattern.
SESSION_RE = re.compile(rf"^/api/sessions/({SESSION_ID_PATTERN})$")


def _sessions_error(handler, exc: Exception) -> None:
    """Map a read-path session failure to its public HTTP error."""
    mapped = session_http_error(exc)
    if mapped is not None:
        handler._json(mapped[0], {"error": mapped[1]})
    else:
        traceback.print_exc(file=sys.stderr)
        handler._json(503, {"error": "chat unavailable"})


def _missing_chat(handler, app: ChatApp | None) -> bool:
    """True when the server was started without a chat app (404 already sent)."""
    if app is None:
        handler._json(404, {"error": "chat unavailable"})
        return True
    return False


def session_list(handler, app: ChatApp | None) -> None:
    if _missing_chat(handler, app):
        return
    try:
        handler._json(200, app.list_payload())
    except Exception as exc:
        _sessions_error(handler, exc)


def session_get(handler, app: ChatApp | None, session_id: str) -> None:
    if _missing_chat(handler, app):
        return
    try:
        handler._json(200, app.get_payload(session_id))
    except Exception as exc:
        _sessions_error(handler, exc)


def session_create(handler, app: ChatApp | None) -> None:
    if _missing_chat(handler, app):
        return
    try:
        raw = handler._read_body()
    except ValueError:
        handler._json(400, {"error": "invalid body"})
        return
    bots = None
    if raw.strip():
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            handler._json(400, {"error": "invalid body"})
            return
        if not isinstance(payload, dict):
            handler._json(400, {"error": "invalid body"})
            return
        if "bots" in payload:
            bots = payload["bots"]
    try:
        handler._json(200, app.create(bots=bots))
    except ValueError:
        handler._json(400, {"error": "invalid bots"})
    except RegistryError as exc:
        handler._json(404, {"error": str(exc)})
    except Exception as exc:
        _sessions_error(handler, exc)


def session_bots(handler, app: ChatApp | None, session_id: str) -> None:
    """``POST /api/sessions/<id>/bots`` — add/remove one room participant."""
    if _missing_chat(handler, app):
        return

    def run(payload: dict) -> None:
        bot_id = payload.get("bot_id")
        action = payload.get("action")
        if not isinstance(bot_id, str) or action not in ("add", "remove"):
            handler._json(400, {"error": "invalid body"})
            return
        try:
            bots = app.set_room_bots(session_id, bot_id, action)
        except ValueError:
            handler._json(400, {"error": "invalid body"})
        except RegistryError as exc:
            handler._json(404, {"error": str(exc)})
        except Exception as exc:
            _sessions_error(handler, exc)
        else:
            handler._json(200, {"ok": True, "id": session_id, "bots": bots})

    _with_json(handler, run)


def bots_list(handler, app: ChatApp | None) -> None:
    """``GET /api/bots`` — public card list (contract §6)."""
    if _missing_chat(handler, app):
        return
    try:
        handler._json(200, {"bots": app.bots_list()})
    except Exception as exc:
        _sessions_error(handler, exc)


def bot_dm(handler, app: ChatApp | None, bot_id: str) -> None:
    """``GET /api/bots/<bot_id>/dm`` — the bot's fixed DM (CBE-5).

    Deterministic find-or-create: repeated calls return the same session
    id. The payload is the session detail (``dm`` = bot id, ``bots`` =
    [bot_id]) so the FE can open the main window directly. Unknown or
    paused bots read as 404, matching the room-bots route style."""
    if _missing_chat(handler, app):
        return
    try:
        handler._json(200, app.bot_dm(bot_id))
    except RegistryError as exc:
        handler._json(404, {"error": str(exc)})
    except Exception as exc:
        _sessions_error(handler, exc)


def bot_operations(
    handler, app: ChatApp | None, bot_id: str, session_id: str, query: str,
) -> None:
    """Read one bot's operational view in one exact room."""
    from thyca.app.turn_options import InvalidTurnOption, validate_turn_options
    from thyca.bots.timeline import TimelineError
    from thyca.sessions import SessionCorrupt, SessionError, SessionNotFound

    if _missing_chat(handler, app):
        return
    try:
        params = parse_qs(
            query, keep_blank_values=True, strict_parsing=True, max_num_fields=2,
        )
    except ValueError:
        handler._json(400, {"error": "invalid query"})
        return
    if set(params) - {"model", "effort"} or any(
        len(values) != 1 for values in params.values()
    ):
        handler._json(400, {"error": "invalid query"})
        return
    try:
        model, effort, _ = validate_turn_options({
            key: values[0] for key, values in params.items()
        })
    except InvalidTurnOption:
        handler._json(400, {"error": "invalid model or effort"})
        return
    try:
        payload = app.bot_operations(
            session_id, bot_id, model=model, effort=effort,
        )
    except InvalidTurnOption:
        handler._json(400, {"error": "invalid model or effort"})
    except RegistryError:
        handler._json(404, {"error": "bot or room not found"})
    except SessionNotFound:
        handler._json(404, {"error": "session not found"})
    except (TimelineError, FileNotFoundError, SessionCorrupt, SessionError, OSError):
        handler._json(503, {"error": "bot information unavailable"})
    except Exception:
        traceback.print_exc(file=sys.stderr)
        handler._json(503, {"error": "bot information unavailable"})
    else:
        handler._json(200, payload)


def session_turn(handler, app: ChatApp | None, session_id: str) -> None:
    from thyca.app.chat_app import InvalidTurnOption, InvalidTurnText, TurnCancelled

    if _missing_chat(handler, app):
        return

    def run(payload: dict) -> None:
        try:
            text, model, effort, retry = parse_turn_body(payload)
        except InvalidTurnOption as exc:
            handler._json(400, {"error": str(exc)})
            return
        except InvalidTurnText as exc:
            _status, _code, message = public_turn_error(exc)
            handler._json(_status, {"error": message})
            return
        except ValueError as exc:
            # Unreachable from the typed parse above; a bug here 500s.
            _log_turn_failure(app, session_id, None, exc)
            handler._json(500, {"error": "chat unavailable"})
            return
        try:
            handler._json(
                200,
                app.turn(session_id, text, model=model, effort=effort, retry=retry),
            )
        except TurnCancelled:
            handler._json(200, {"cancelled": True})
        except Exception as exc:
            status, _code, message = public_turn_error(exc)
            _log_turn_failure(app, session_id, model, exc)
            handler._json(status, {"error": message})

    _with_json(handler, run)


def session_turn_stream(handler, app: ChatApp | None, session_id: str) -> None:
    from thyca.app.chat_app import InvalidTurnOption, InvalidTurnText

    if _missing_chat(handler, app):
        return

    def run(payload: dict) -> None:
        try:
            text, model, effort, retry = parse_turn_body(payload)
        except InvalidTurnOption as exc:
            handler._json(400, {"error": str(exc)})
            return
        except InvalidTurnText as exc:
            _status, _code, message = public_turn_error(exc)
            handler._json(_status, {"error": message})
            return
        except ValueError as exc:
            # Unreachable from the typed parse above; a bug here 500s.
            _log_turn_failure(app, session_id, None, exc)
            handler._json(500, {"error": "chat unavailable"})
            return
        stream_turn(
            handler, app, session_id, text, model=model, effort=effort, retry=retry
        )

    _with_json(handler, run, error="invalid text")


def session_turn_cancel(handler, app: ChatApp | None, session_id: str) -> None:
    from thyca.app.chat_app import SessionIdle, TurnInFlight

    if _missing_chat(handler, app):
        return
    try:
        handler._read_body()
    except ValueError:
        handler._json(400, {"error": "invalid body"})
        return
    try:
        app.cancel(session_id)
    except SessionIdle:
        handler._json(409, {"error": "session idle"})
    except TurnInFlight:
        handler._json(409, {"error": "turn in flight"})
    except Exception as exc:
        _sessions_error(handler, exc)
    else:
        handler._json(200, {"ok": True})


def session_turn_follow(handler, app: ChatApp | None, session_id: str) -> None:
    """``GET /turn/stream`` — replay + tail the in-flight turn, if any."""
    if _missing_chat(handler, app):
        return
    try:
        hub = app.follow_hub(session_id)
    except Exception as exc:
        _sessions_error(handler, exc)
        return
    if hub is None:
        handler._json(409, {"error": "session idle"})
        return
    items = hub.subscribe()
    try:
        # A follower never learns the turn's model override: log unknown
        # rather than the default, which would misattribute the failure.
        pump_stream(
            handler, items, {"disconnected": False}, app=app, session_id=session_id,
            model="?",
        )
    finally:
        hub.drop(items)


def session_compact(handler, app: ChatApp | None, session_id: str) -> None:
    """``POST /api/sessions/<id>/compact`` — manual Gọn phiên.

    Body is ``{}``, ``{"model": "..."}`` (the composer's per-turn
    override, so the tail budget matches the model on screen), and/or
    ``{"instructions": "..."}`` (keep/drop focus for the summarizer,
    capped like the prompt policy). Returns the fresh session detail
    plus a ``compaction`` summary.
    """
    from thyca.app.chat_app import InvalidTurnOption
    from thyca.sessions.summary import INSTRUCTIONS_MAX_CHARS

    if _missing_chat(handler, app):
        return

    def run(payload: dict) -> None:
        model = payload.get("model")
        if model is not None and (
            not isinstance(model, str) or not model.strip()
        ):
            handler._json(400, {"error": "invalid model"})
            return
        instructions = payload.get("instructions")
        if instructions is not None and (
            not isinstance(instructions, str)
            or len(instructions) > INSTRUCTIONS_MAX_CHARS
        ):
            handler._json(400, {"error": "invalid instructions"})
            return
        if isinstance(instructions, str) and not instructions.strip():
            instructions = None
        try:
            handler._json(
                200,
                app.compact_session(session_id, model=model, instructions=instructions),
            )
        except InvalidTurnOption as exc:
            handler._json(400, {"error": str(exc)})
        except Exception as exc:
            _sessions_error(handler, exc)

    _with_json(handler, run)


def session_rename(handler, app: ChatApp | None, path: str) -> None:
    """``PATCH /api/sessions/<id>`` — store a title the user typed."""
    if _missing_chat(handler, app):
        return
    match = SESSION_RE.fullmatch(path)
    if not match:
        handler._json(404, {"error": "session not found"})
        return

    def run(payload: dict) -> None:
        title = payload.get("title")
        if not isinstance(title, str):
            handler._json(400, {"error": "invalid title"})
            return
        try:
            stored = app.rename_session(match.group(1), title)
        except Exception as exc:
            mapped = rename_error(exc)
            if mapped is None:
                _sessions_error(handler, exc)
                return
            handler._json(mapped[0], {"error": mapped[1]})
        else:
            handler._json(200, {"ok": True, "id": match.group(1), "title": stored})

    _with_json(handler, run)


def session_delete(handler, app: ChatApp | None, path: str) -> None:
    """``DELETE /api/sessions/<id>`` — drop the notebook, keep memory."""
    if _missing_chat(handler, app):
        return
    match = SESSION_RE.fullmatch(path)
    if not match:
        handler._json(404, {"error": "session not found"})
        return
    try:
        app.delete_session(match.group(1))
    except Exception as exc:
        mapped = delete_error(exc)
        if mapped is None:
            _sessions_error(handler, exc)
            return
        handler._json(mapped[0], {"error": mapped[1]})
    else:
        handler._json(200, {"ok": True, "id": match.group(1)})


_PRIVATE_FOLLOWUP_RE = re.compile(
    rf"^/api/sessions/({SESSION_ID_PATTERN})/private-followup$")


def session_private_followup(handler, app, session_id: str) -> None:
    """POST /api/sessions/<dm>/private-followup — native consent card.

    Body: {pending_id, revision, choice: approve|keep_private|cancel}.
    All real work lives in ``PrivateFollowupService`` — this handler only
    parses safe types and maps typed service errors to fixed codes."""
    if _missing_chat(handler, app):
        return
    from thyca.app.private_followups import (
        ConsentError,
        PrivateFollowupService,
    )

    def run(payload: dict) -> None:
        try:
            out = PrivateFollowupService(app).decide(
                session_id,
                payload.get("pending_id"),
                payload.get("revision"),
                payload.get("choice"),
            )
        except ConsentError as exc:
            handler._json(exc.status, {"error": str(exc)})
            return
        except Exception:
            traceback.print_exc(file=sys.stderr)
            handler._json(503, {"error": "chat unavailable"})
            return
        handler._json(200, out)

    _with_json(handler, run)
