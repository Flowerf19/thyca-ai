"""Bridge between HTTP handlers and one ChatApp turn (split from serve.py).

The NDJSON stream bridge: queue adapter for turn events, the worker thread
that produces exactly one terminal item, and the public turn-error mapping
shared by ``/turn`` and ``/turn/stream``.
"""
from __future__ import annotations

import json
import queue

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