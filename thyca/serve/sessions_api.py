"""Session read/turn/rename/delete endpoints (split from bridge.py).

Functions take the handler duck-typed (``_json`` / ``_read_json`` /
``_read_body`` / ``wfile``) plus the chat app, so this module never imports
the handler class. ``ChatApp`` is only an annotation (``TYPE_CHECKING``);
the turn exceptions from ``thyca.app.chat_app`` are imported lazily —
that module imports ``thyca.serve.turn_state``, so a top-level import here
would cycle (M7-TASK-013).
"""
from __future__ import annotations

import re
import sys
import traceback
from typing import TYPE_CHECKING

from thyca.serve.bridge import _log_turn_failure, pump_stream, stream_turn
from thyca.serve.errors import parse_turn_body, public_turn_error
from thyca.sessions import SessionCorrupt, SessionError, SessionNotFound
from thyca.sessions.wire import delete_error, rename_error

if TYPE_CHECKING:
    from thyca.app.chat_app import ChatApp

# Same grammar the session routes use: a timestamp id and four hex chars.
SESSION_RE = re.compile(r"^/api/sessions/(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}_[0-9a-f]{4})$")


def _sessions_error(handler, exc: Exception) -> None:
    """Map a read-path session failure to its public HTTP error."""
    if isinstance(exc, SessionNotFound):
        handler._json(404, {"error": "session not found"})
    elif isinstance(exc, SessionCorrupt):
        handler._json(503, {"error": "session unreadable"})
    elif isinstance(exc, SessionError):
        handler._json(503, {"error": "session unavailable"})
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
        handler._read_body()
    except ValueError:
        handler._json(400, {"error": "invalid body"})
        return
    try:
        handler._json(200, app.create())
    except Exception as exc:
        _sessions_error(handler, exc)


def session_turn(handler, app: ChatApp | None, session_id: str) -> None:
    from thyca.app.chat_app import InvalidTurnOption, TurnCancelled

    if _missing_chat(handler, app):
        return
    try:
        payload = handler._read_json()
    except ValueError:
        handler._json(400, {"error": "invalid body"})
        return
    try:
        text, model, effort, retry = parse_turn_body(payload)
    except InvalidTurnOption as exc:
        handler._json(400, {"error": str(exc)})
        return
    except ValueError:
        handler._json(400, {"error": "invalid text"})
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


def session_turn_stream(handler, app: ChatApp | None, session_id: str) -> None:
    from thyca.app.chat_app import InvalidTurnOption

    if _missing_chat(handler, app):
        return
    try:
        payload = handler._read_json()
    except ValueError:
        handler._json(400, {"error": "invalid text"})
        return
    try:
        text, model, effort, retry = parse_turn_body(payload)
    except InvalidTurnOption as exc:
        handler._json(400, {"error": str(exc)})
        return
    except ValueError:
        handler._json(400, {"error": "invalid text"})
        return
    stream_turn(
        handler, app, session_id, text, model=model, effort=effort, retry=retry
    )


def session_turn_cancel(handler, app: ChatApp | None, session_id: str) -> None:
    from thyca.app.chat_app import SessionIdle

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


def session_rename(handler, app: ChatApp | None, path: str) -> None:
    """``PATCH /api/sessions/<id>`` — store a title the user typed."""
    if _missing_chat(handler, app):
        return
    match = SESSION_RE.fullmatch(path)
    if not match:
        handler._json(404, {"error": "session not found"})
        return
    try:
        payload = handler._read_json()
    except ValueError:
        handler._json(400, {"error": "invalid body"})
        return
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
