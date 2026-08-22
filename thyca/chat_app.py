"""Loopback chat: list/create sessions and run one AgentLoop turn."""
from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from thyca.agent.act import Act
from thyca.agent.assemble import Assemble
from thyca.agent.loop import AgentLoop
from thyca.agent.observe import Observe
from thyca.agent.think import LLMPort, Think
from thyca.config import Config
from thyca.llm.llm_factory import ConnectFactory
from thyca.memory.active import ActiveMemory
from thyca.protocol import Message
from thyca.sessions import Session, SessionManager
from thyca.tools.builtin import register_file_tools
from thyca.tools.memory import MemoryFacade
from thyca.tools.memory_tools import register_memory_tools
from thyca.tools.path_guard import PathGuard
from thyca.tools.registry import ToolRegistry

TITLE_MAX = 48
TEXT_MAX = 4000


class ChatApp:
    def __init__(self, root: Path, cfg: Config, connect: LLMPort | None = None) -> None:
        self._root = root
        self._cfg = cfg
        self._connect = connect
        self._sessions = SessionManager(
            root / "sessions",
            limits=cfg.limits,
            timezone_name=cfg.timeline.timezone,
        )
        self._memory = ActiveMemory(
            root,
            tail_kb=cfg.limits.hotTailKB,
            timezone_name=cfg.timeline.timezone,
        )
        self._zone = ZoneInfo(cfg.timeline.timezone)
        self._state = self._memory.open_session(datetime.now(self._zone))
        registry = ToolRegistry()
        register_file_tools(registry, PathGuard(root))
        register_memory_tools(
            registry, MemoryFacade(root, timezone_name=cfg.timeline.timezone)
        )
        self._tools = registry.to_openai_schema()
        self._act = Act(registry)
        self._turn_lock = threading.Lock()

    def list_payload(self) -> dict:
        sessions = [self._session_summary(item) for item in self._sessions.list_sessions()]
        return {"model": self._cfg.provider.model, "sessions": sessions}

    def get_payload(self, session_id: str) -> dict:
        session = self._sessions.store.load(session_id)
        return self._session_detail(session)

    def create(self) -> dict:
        with self._turn_lock:
            session = self._sessions.create()
            return self._session_detail(session)

    def turn(self, session_id: str, text: str) -> dict:
        if not isinstance(text, str):
            raise ValueError("text must be a string")
        cleaned = text.strip()
        if not cleaned:
            raise ValueError("empty")
        if len(cleaned) > TEXT_MAX:
            raise ValueError("too long")
        with self._turn_lock:
            return asyncio.run(self._run_turn(session_id, cleaned))

    async def _run_turn(self, session_id: str, text: str) -> dict:
        connect = self._connect or ConnectFactory.create("openai_chat", self._cfg.provider)
        owns = self._connect is None
        try:
            self._sessions.load(session_id)
            loop = AgentLoop(
                sessions=self._sessions,
                assemble=Assemble(),
                think=Think(connect),
                act=self._act,
                observe=Observe(self._sessions),
                loop_max=self._cfg.limits.loopMax,
                tools=self._tools,
            )
            hot = self._memory.refresh(self._state, datetime.now(self._zone))
            reply = await loop.run(text, hot=hot)
            return {**self._session_detail(self._sessions.current), "reply": reply}
        finally:
            if owns:
                close = getattr(connect, "aclose", None)
                if close is not None:
                    await close()

    def _session_summary(self, session: Session) -> dict:
        return {
            "id": session.id,
            "title": session_title(session.messages),
            "updated_at": _updated_at(session),
            "message_count": len(session.messages),
        }

    def _session_detail(self, session: Session) -> dict:
        return {
            "id": session.id,
            "title": session_title(session.messages),
            "model": self._cfg.provider.model,
            "messages": [_message_dict(item) for item in session.messages],
        }


def session_title(messages: list[Message]) -> str:
    for message in messages:
        if message.role == "user" and message.content and message.content.strip():
            line = message.content.strip().splitlines()[0]
            if len(line) > TITLE_MAX:
                return line[: TITLE_MAX - 1] + "…"
            return line
    return "Phiên trống"


def _updated_at(session: Session) -> str:
    if session.messages:
        return session.messages[-1].ts
    stamp = datetime.fromtimestamp(session.path.stat().st_mtime, timezone.utc)
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def _message_dict(message: Message) -> dict:
    payload: dict = {
        "role": message.role,
        "content": message.content,
        "ts": message.ts,
    }
    if message.tool_calls:
        payload["tool_calls"] = [
            {"id": call.id, "name": call.name} for call in message.tool_calls
        ]
    if message.tool_call_id is not None:
        payload["tool_call_id"] = message.tool_call_id
    return payload


