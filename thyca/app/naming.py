"""Session-naming sidecar: title proposal + meta-only record (split from chat_app, M8)."""
from __future__ import annotations

import asyncio
from time import perf_counter

from thyca.agent.events import EventSink, TurnEvent, emit_event
from thyca.agent.meta import naming_meta
from thyca.agent.think import LLMPort
from thyca.config import Config
from thyca.core.protocol import Message, utc_now_ts
from thyca.sessions import SessionManager
from thyca.sessions.title import NAMING_TURNS, completed_turn_count, propose_title
from thyca.sessions.wire import session_title  # noqa: F401 — canonical alias, re-exported


async def _name_if_needed(
    connect: LLMPort,
    sessions: SessionManager,
    cfg: Config,
    event_sink: EventSink | None = None,
) -> bool:
    # The user may have named the notebook from the sidebar while this turn
    # was running. Re-read the title from disk so the model does not
    # overwrite a name that landed mid-turn.
    sessions.refresh_title()
    session = sessions.current
    if session.title:
        return False
    # One shot, even when the single attempt fails: a fallback title is not a
    # reason to spend another LLM call, on this turn or after a reopen.
    if session.naming_attempted:
        return False
    if completed_turn_count(session.messages) < NAMING_TURNS:
        return False
    # The attempt is consumed BEFORE any network call, so a crash, a
    # cancellation, or a failed proposal can never spend a second LLM call
    # on it. When the flag itself cannot persist, no model call is made.
    # Single-process assumption: same-session turns are serialized by the
    # claim lock and the flag file is the cross-reload record; two
    # processes sharing a sessions dir could still double-fire.
    try:
        sessions.mark_naming_attempted()
    except Exception:
        return False
    emit_event(event_sink, TurnEvent(type="session.naming.started"))
    updated = False
    captured: dict = {}

    async def spy(messages, tools=None):
        reply = await connect.chat(messages, tools)
        captured["reply"] = reply
        return reply

    started = perf_counter()
    try:
        try:
            cleaned = await propose_title(spy, session)
        except asyncio.CancelledError:
            raise
        except Exception:
            cleaned = None
        latency_ms = int((perf_counter() - started) * 1000)
        if cleaned is not None:
            try:
                stored = sessions.set_title_if_missing(cleaned)
            except Exception:
                stored = None
            if stored is not None:
                updated = True
                try:
                    _record_naming(captured.get("reply"), latency_ms, sessions, cfg)
                except Exception:
                    pass  # title already stored; the meta record is best-effort
    finally:
        # Paired with started on every path, including cancellation: the
        # flag was pre-persisted, so the attempt stays consumed on reload.
        emit_event(
            event_sink, TurnEvent(type="session.naming.finished", updated=updated)
        )
    return updated


def _record_naming(
    reply: object, latency_ms: int, sessions: SessionManager, cfg: Config
) -> None:
    """Persist the naming LLM call as a meta-only assistant message (TASK-009)."""
    sessions.append(
        Message(
            role="assistant",
            content=None,
            ts=utc_now_ts(),
            meta=naming_meta(reply, latency_ms, cfg),
        )
    )
