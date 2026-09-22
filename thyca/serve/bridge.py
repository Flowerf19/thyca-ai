"""Bridge between HTTP handlers and one ChatApp turn (split from serve.py).

The HTTP bridge for ``/api/sessions*``: the NDJSON stream (queue adapter for
turn events, the worker thread that produces exactly one terminal item, the
public turn-error mapping shared by ``/turn`` and ``/turn/stream``) plus the
session read/rename/delete endpoints.

Handlers are accessed only through their ``_json`` / ``_read_json`` /
``_read_body`` / ``_stream_headers`` / ``wfile`` surface, so this module never
imports ``serve.py`` — which is what keeps ``serve.py`` a router.
"""
from __future__ import annotations

import json
import queue
import re
import sys
import threading
import traceback

from thyca.agent.events import TurnEvent
from thyca.agent.thinking import ThinkingDelta
from thyca.agent.reply import ContentDelta
from thyca.app.chat_app import ChatApp, InvalidTurnOption, SessionIdle, TurnCancelled
from thyca.config import Config
from thyca.llm.llm_base import LLMError
from thyca.sessions.wire import delete_error, rename_error
from thyca.sessions import SessionBusy, SessionCorrupt, SessionError, SessionNotFound
from thyca.serve.turn_state import TurnHub

SENTINEL = object()
# Same grammar the session routes use: a timestamp id and four hex chars.
SESSION_RE = re.compile(r"^/api/sessions/(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}_[0-9a-f]{4})$")
_MODEL_MAX = 200


def parse_turn_body(payload: dict) -> tuple[str, str | None, str | None, bool]:
    """Shared by /turn and /turn/stream. Extra keys ignored."""
    retry = payload.get("retry") is True
    if "model" in payload:
        model = payload["model"]
        if (
            not isinstance(model, str)
            or not model
            or len(model) > _MODEL_MAX
            or "\n" in model
            or "\r" in model
            or not model.strip()
        ):
            raise InvalidTurnOption("invalid model")
    else:
        model = None
    if "effort" in payload:
        if not isinstance(payload["effort"], str) or not payload["effort"].strip():
            raise InvalidTurnOption("invalid effort")
        effort = payload["effort"]
    else:
        effort = None
    if retry:
        text = payload.get("text")
        return text if isinstance(text, str) else "", model, effort, True
    text = payload.get("text")
    if not isinstance(text, str):
        raise ValueError("invalid text")
    return text, model, effort, False


def public_turn_error(exc: Exception) -> tuple[int, str, str]:
    """Map a turn exception to a public ``(status, code, message)``.

    Shared by ``/turn`` and ``/turn/stream`` so the two cannot drift. Never
    leaks stack/path/secret: unexpected errors and :class:`ConfigError` always
    map to the constant ``chat unavailable``; :class:`LLMError` keeps its
    provider-redacted/capped text.
    """
    if isinstance(exc, InvalidTurnOption):
        message = str(exc)
        return 400, message.replace(" ", "_"), message
    if isinstance(exc, TurnCancelled):
        return 200, "cancelled", "cancelled"
    if isinstance(exc, ValueError):
        return 400, "invalid_text", "invalid text"
    if isinstance(exc, SessionBusy):
        return 409, "session_busy", "session busy"
    if isinstance(exc, SessionNotFound):
        return 404, "session_not_found", "session not found"
    if isinstance(exc, SessionCorrupt):
        return 503, "session_unreadable", "session unreadable"
    if isinstance(exc, SessionError):
        return 503, "session_unavailable", "session unavailable"
    if isinstance(exc, LLMError):
        return 503, "llm_error", str(exc)
    return 503, "chat_unavailable", "chat unavailable"


def bridge_sink(queue_: queue.Queue, state: dict):
    """Queue adapter for ``ChatApp.turn(event_sink=...)``.

    Never raises and never blocks AgentLoop: once the client disconnected
    the sink drops events without touching the queue.
    """
    def sink(event: TurnEvent) -> None:
        if state["disconnected"]:
            return
        try:
            queue_.put(event)
        except Exception:
            pass
    return sink


def bridge_worker(
    app: ChatApp,
    session_id: str,
    text: str,
    queue_: queue.Queue,
    state: dict,
    model: str | None = None,
    effort: str | None = None,
    retry: bool = False,
) -> None:
    """Run one turn, queueing events plus exactly one terminal item.

    The sentinel lands in ``finally`` so the handler cannot hang even if
    completion enqueue/serialization fails. The first queued item before
    ``turn.accepted`` (or the sentinel) is a pre-accept error.
    """
    sink = bridge_sink(queue_, state)
    try:
        try:
            detail = app.turn(
                session_id,
                text,
                event_sink=sink,
                model=model,
                effort=effort,
                retry=retry,
            )
        except TurnCancelled:
            queue_.put(("cancelled", None))
        except Exception as exc:
            queue_.put(("failed", exc))
        else:
            queue_.put(("completed", detail))
    finally:
        queue_.put(SENTINEL)


def write_line(wfile, line: dict) -> None:
    wfile.write(json.dumps(line, ensure_ascii=False).encode("utf-8") + b"\n")
    wfile.flush()


def _stream_end(item: object) -> bool:
    return item is SENTINEL or item is TurnHub.SENTINEL


def _log_turn_failure(
    app: ChatApp | None, session_id: str, model: str | None, exc: Exception
) -> None:
    """One stderr line per failed turn (lands in serve.log under --daemon).

    Never raises: logging must not break the error response itself. The
    message is the public redacted/capped text, never a key or traceback.
    """
    try:
        resolved = model or (app.default_model() if app is not None else "?")
        provider_id = app.provider_id_for(model) if app is not None else "?"
    except Exception:
        resolved, provider_id = model or "?", "?"
    _status, code, message = public_turn_error(exc)
    print(
        f"turn failed session={session_id} model={resolved} "
        f"provider={provider_id} code={code} msg={message}",
        file=sys.stderr,
        flush=True,
    )


def pump_stream(
    handler,
    items: queue.Queue,
    state: dict,
    *,
    app: ChatApp | None = None,
    session_id: str = "?",
    model: str | None = None,
) -> None:
    """Write NDJSON from *items* until a sentinel, or HTTP JSON on pre-accept.

    ``handler`` is the BaseHTTPRequestHandler — accessed only through its
    ``_stream_headers`` / ``_json`` / ``wfile`` surface, so this module never
    imports serve.py.
    """
    try:
        first = items.get()
        if isinstance(first, TurnEvent):
            if first.type == "turn.accepted":
                handler._stream_headers()
                write_line(handler.wfile, first.to_dict())
            else:
                _log_turn_failure(app, session_id, model, RuntimeError("no turn.accepted"))
                handler._json(503, {"error": "chat unavailable"})
                return
        elif _stream_end(first):
            _log_turn_failure(app, session_id, model, RuntimeError("empty turn queue"))
            handler._json(503, {"error": "chat unavailable"})
            return
        else:
            # Pre-accept exception: same HTTP error as /turn, no NDJSON.
            _type, exc = first
            if _type == "cancelled":
                handler._stream_headers()
                write_line(handler.wfile, {"type": "turn.cancelled"})
                return
            status, _code, message = public_turn_error(exc)
            _log_turn_failure(app, session_id, model, exc)
            handler._json(status, {"error": message})
            return
        terminal = False
        while True:
            item = items.get()
            if _stream_end(item):
                break
            if terminal:
                continue
            if isinstance(item, (TurnEvent, ThinkingDelta, ContentDelta)):
                write_line(handler.wfile, item.to_dict())
                continue
            _kind, value = item
            if _kind == "completed":
                write_line(
                    handler.wfile, {"type": "turn.completed", "detail": value}
                )
            elif _kind == "cancelled":
                write_line(handler.wfile, {"type": "turn.cancelled"})
            else:
                _code, _message = public_turn_error(value)[1:]
                _log_turn_failure(app, session_id, model, value)
                write_line(
                    handler.wfile, {"type": "turn.failed", "code": _code, "message": _message}
                )
            terminal = True
        if not terminal and not state["disconnected"]:
            # Sentinel with no terminal item: write the constant public
            # failure so the client never sees a stream without a terminal.
            _log_turn_failure(app, session_id, model, RuntimeError("missing terminal"))
            write_line(
                handler.wfile,
                {
                    "type": "turn.failed",
                    "code": "chat_unavailable",
                    "message": "chat unavailable",
                },
            )
    except (BrokenPipeError, ConnectionResetError):
        # Client left: drop further events, let persist finish.
        state["disconnected"] = True


def stream_turn(
    handler,
    app: ChatApp,
    session_id: str,
    text: str,
    model: str | None = None,
    effort: str | None = None,
    retry: bool = False,
) -> None:
    """Pump one turn as NDJSON: headers, events, exactly one terminal item."""
    if not isinstance(text, str):
        handler._json(400, {"error": "invalid text"})
        return
    items: queue.Queue = queue.Queue()
    state = {"disconnected": False}
    worker = threading.Thread(
        target=bridge_worker,
        args=(app, session_id, text, items, state),
        kwargs={"model": model, "effort": effort, "retry": retry},
        daemon=True,
        name="thyca-turn-stream",
    )
    worker.start()
    try:
        pump_stream(handler, items, state, app=app, session_id=session_id, model=model)
    finally:
        # The worker is a daemon and only this session's turn was ever at
        # stake, so persistence completes regardless of the client. A live
        # stream waits for the terminal item; an abandoned one only
        # parks this handler thread briefly.
        worker.join(timeout=5 if state["disconnected"] else 60)



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
