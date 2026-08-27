"""Bridge between HTTP handlers and one ChatApp turn (split from serve.py).

The NDJSON stream bridge: queue adapter for turn events, the worker thread
that produces exactly one terminal item, and the public turn-error mapping
shared by ``/turn`` and ``/turn/stream``.
"""
from __future__ import annotations

import json
import queue
import threading

from thyca.agent.events import TurnEvent
from thyca.chat_app import ChatApp
from thyca.llm.llm_base import LLMError
from thyca.sessions import SessionCorrupt, SessionError, SessionNotFound

SENTINEL = object()


def public_turn_error(exc: Exception) -> tuple[int, str, str]:
    """Map a turn exception to a public ``(status, code, message)``.

    Shared by ``/turn`` and ``/turn/stream`` so the two cannot drift. Never
    leaks stack/path/secret: unexpected errors and :class:`ConfigError` always
    map to the constant ``chat unavailable``; :class:`LLMError` keeps its
    provider-redacted/capped text.
    """
    if isinstance(exc, ValueError):
        return 400, "invalid_text", "invalid text"
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
) -> None:
    """Run one turn, queueing events plus exactly one terminal item.

    The sentinel lands in ``finally`` so the handler cannot hang even if
    completion enqueue/serialization fails. The first queued item before
    ``turn.accepted`` (or the sentinel) is a pre-accept error.
    """
    sink = bridge_sink(queue_, state)
    try:
        try:
            detail = app.turn(session_id, text, event_sink=sink)
        except Exception as exc:
            queue_.put(("failed", exc))
        else:
            queue_.put(("completed", detail))
    finally:
        queue_.put(SENTINEL)


def write_line(wfile, line: dict) -> None:
    wfile.write(json.dumps(line, ensure_ascii=False).encode("utf-8") + b"\n")
    wfile.flush()


def stream_turn(handler, app: ChatApp, session_id: str, text: str) -> None:
    """Pump one turn as NDJSON: headers, events, exactly one terminal item.

    ``handler`` is the BaseHTTPRequestHandler — accessed only through its
    ``_stream_headers`` / ``_json`` / ``wfile`` surface, so this module never
    imports serve.py.
    """
    if not isinstance(text, str):
        handler._json(400, {"error": "invalid text"})
        return
    items: queue.Queue = queue.Queue()
    state = {"disconnected": False}
    worker = threading.Thread(
        target=bridge_worker,
        args=(app, session_id, text, items, state),
        daemon=True,
        name="thyca-turn-stream",
    )
    worker.start()
    try:
        first = items.get()
        if isinstance(first, TurnEvent):
            if first.type == "turn.accepted":
                handler._stream_headers()
                write_line(handler.wfile, first.to_dict())
            else:
                handler._json(503, {"error": "chat unavailable"})
                return
        elif first is SENTINEL:
            handler._json(503, {"error": "chat unavailable"})
            return
        else:
            # Pre-accept exception: same HTTP error as /turn, no NDJSON.
            _type, exc = first
            status, _code, message = public_turn_error(exc)
            handler._json(status, {"error": message})
            return
        terminal = False
        while True:
            item = items.get()
            if item is SENTINEL:
                break
            if terminal:
                continue
            if isinstance(item, TurnEvent):
                write_line(handler.wfile, item.to_dict())
                continue
            _kind, value = item
            if _kind == "completed":
                write_line(
                    handler.wfile, {"type": "turn.completed", "detail": value}
                )
            else:
                _code, _message = public_turn_error(value)[1:]
                write_line(
                    handler.wfile, {"type": "turn.failed", "code": _code, "message": _message}
                )
            terminal = True
        if not terminal and not state["disconnected"]:
            # Sentinel with no terminal item: write the constant public
            # failure so the client never sees a stream without a terminal.
            write_line(
                handler.wfile,
                {
                    "type": "turn.failed",
                    "code": "chat_unavailable",
                    "message": "chat unavailable",
                },
            )
            terminal = True
    except (BrokenPipeError, ConnectionResetError):
        # Client left: drop further events, let persist finish.
        state["disconnected"] = True
    finally:
        # The worker is a daemon and ChatApp serializes turns behind
        # _turn_lock, so persistence completes regardless. A live
        # stream waits for the terminal item; an abandoned one only
        # parks this handler thread briefly.
        worker.join(timeout=5 if state["disconnected"] else 60)

