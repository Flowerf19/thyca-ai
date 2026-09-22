"""NDJSON turn streaming for ``/api/sessions*`` (split from serve.py).

The queue adapter for turn events, the worker thread that produces exactly
one terminal item, and the stream pump. Turn-error mapping and body parsing
live in ``errors`` (re-exported here so ``thyca.serve.bridge`` keeps its
import surface); session read/rename/delete endpoints live in
``sessions_api``.

Handlers are accessed only through their ``_json`` / ``_read_json`` /
``_read_body`` / ``_stream_headers`` / ``wfile`` surface, so this module never
imports ``routes`` — which is what keeps ``routes`` a router. ``ChatApp`` is
only an annotation (``TYPE_CHECKING``); the turn exception is imported
lazily — ``thyca.app.chat_app`` imports ``thyca.serve.turn_state``, so a
top-level import here would cycle (M7-TASK-013).
"""
from __future__ import annotations

import json
import queue
import sys
import threading
from typing import TYPE_CHECKING

from thyca.agent.events import TurnEvent
from thyca.agent.reply import ContentDelta
from thyca.agent.thinking import ThinkingDelta
from thyca.serve.errors import parse_turn_body, public_turn_error
from thyca.serve.turn_state import TurnHub

if TYPE_CHECKING:
    from thyca.app.chat_app import ChatApp

__all__ = [
    "SENTINEL",
    "bridge_sink",
    "bridge_worker",
    "parse_turn_body",
    "public_turn_error",
    "pump_stream",
    "stream_turn",
    "write_line",
]

SENTINEL = object()


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
    # Local import: thyca.app.chat_app imports thyca.serve.turn_state, so a
    # top-level import here would cycle (M7-TASK-013).
    from thyca.app.chat_app import TurnCancelled

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
    imports routes.
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
