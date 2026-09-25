"""Session-naming sidecar: title proposal + meta-only record (split from chat_app, M8)."""
from __future__ import annotations

from time import perf_counter

from thyca.agent.events import EventSink, TurnEvent, emit_event
from thyca.agent.meta import naming_meta
from thyca.agent.think import LLMPort
from thyca.config import Config
from thyca.core.protocol import Message, utc_now_ts
from thyca.llm.llm_base import LLMError
from thyca.sessions import SessionManager
from thyca.sessions.title import propose_title
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
    emit_event(event_sink, TurnEvent(type="session.naming.started"))
    updated = False
    captured: dict = {}

    async def spy(messages, tools=None):
        reply = await connect.chat(messages, tools)
        captured["reply"] = reply
        return reply

    started = perf_counter()
    try:
        cleaned = await propose_title(spy, session)
    except LLMError:
        cleaned = None
    latency_ms = int((perf_counter() - started) * 1000)
    if cleaned is not None:
        stored = sessions.set_title(cleaned)
        updated = stored is not None
        if updated:
            _record_naming(captured.get("reply"), latency_ms, sessions, cfg)
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
