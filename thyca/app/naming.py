"""Session-naming sidecar: title proposal + meta-only record (split from chat_app, M8)."""
from __future__ import annotations

from time import perf_counter

from thyca.agent.events import EventSink, TurnEvent, emit_event
from thyca.agent.think import LLMPort
from thyca.config import Config
from thyca.core.protocol import Message, utc_now_ts
from thyca.llm.llm_base import LLMError
from thyca.llm.pricing import cost_for
from thyca.sessions import Session, SessionManager
from thyca.sessions.title import display_title, propose_title


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
    usage = getattr(reply, "usage", None)
    model = (getattr(reply, "model", None) or cfg.provider.model or "").strip() or None
    meta: dict = {"kind": "naming", "latency_ms": max(0, latency_ms)}
    if model:
        meta["model"] = model
    if isinstance(usage, dict) and usage:
        meta["usage"] = usage
    if model:
        price = cost_for(
            model,
            usage if isinstance(usage, dict) else None,
            cfg.effective_pricing() or None,
        )
        if price is not None:
            meta["cost_usd"] = price
    sessions.append(Message(role="assistant", content=None, ts=utc_now_ts(), meta=meta))


def session_title(session: Session) -> str:
    """Display title for one session (thin alias over the payload module)."""
    return display_title(session)
