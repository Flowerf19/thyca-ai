"""ChatApp.turn event sink — TASK-004 verification."""
from __future__ import annotations

from pathlib import Path

from thyca.agent.events import TurnEvent
from thyca.llm.llm_base import ChatReply, LLMError
from thyca.sessions import SessionManager
from thyca.sessions.title import fallback_title

from test_serve_chat import FakeLLM, ScriptedLLM, _chat


def _types(events: list[TurnEvent]) -> list[str]:
    return [event.type for event in events]


def _naming_pairs(events: list[TurnEvent]) -> list[tuple[str, bool | None]]:
    return [
        (event.type, event.updated)
        for event in events
        if event.type.startswith("session.naming")
    ]


def test_first_turn_emits_naming_pairs_and_persists_title(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [
            ChatReply(content="pong"),
            ChatReply(content='"Cà phê với Hòa."'),
        ]
    )
    app = _chat(tmp_path, llm)
    created = app.create()
    events: list[TurnEvent] = []
    try:
        turned = app.turn(created["id"], "alo", event_sink=events.append)
        assert turned["reply"] == "pong"
        assert turned["title"] == "Cà phê với Hòa"
        assert events[-2].type == "session.naming.started"
        assert events[-1].type == "session.naming.finished"
        assert _naming_pairs(events) == [("session.naming.started", None), ("session.naming.finished", True)]
        assert len(llm.requests) == 2
        session = SessionManager(tmp_path / "sessions").load(created["id"])
        assert session.title == "Cà phê với Hòa"
        assert [(item.role, item.content) for item in session.messages] == [
            ("user", "alo"),
            ("assistant", "pong"),
        ]
    finally:
        app.shutdown()


def test_titled_session_emits_no_naming_events(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [
            ChatReply(content="pong"),
            ChatReply(content='"Cà phê với Hòa."'),
            ChatReply(content="again"),
        ]
    )
    app = _chat(tmp_path, llm)
    created = app.create()
    try:
        first = app.turn(created["id"], "alo")
        assert first["title"] == "Cà phê với Hòa"
        events: list[TurnEvent] = []
        second = app.turn(created["id"], "thêm", event_sink=events.append)
        assert second["reply"] == "again"
        assert second["title"] == "Cà phê với Hòa"
        assert _naming_pairs(events) == []
        assert len(llm.requests) == 3
    finally:
        app.shutdown()


def test_naming_llm_error_keeps_turn_and_reports_updated_false(tmp_path: Path) -> None:
    class TitleBoom(FakeLLM):
        async def chat(self, messages, tools=None):
            if len(self.requests) >= 1:
                self.requests.append(list(messages))
                raise LLMError("title failed")
            return await super().chat(messages, tools)

    app = _chat(tmp_path, TitleBoom(ChatReply(content="pong")))
    created = app.create()
    events: list[TurnEvent] = []
    try:
        turned = app.turn(created["id"], "alo", event_sink=events.append)
        assert turned["reply"] == "pong"
        assert turned["title"] == fallback_title(created["id"])
        assert _naming_pairs(events) == [("session.naming.started", None), ("session.naming.finished", False)]
        session = SessionManager(tmp_path / "sessions").load(created["id"])
        assert session.title is None
        assert [(item.role, item.content) for item in session.messages] == [
            ("user", "alo"),
            ("assistant", "pong"),
        ]
    finally:
        app.shutdown()


def test_naming_rejected_echo_reports_updated_false_turn_succeeds(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [
            ChatReply(content="pong"),
            ChatReply(content="alo"),
        ]
    )
    app = _chat(tmp_path, llm)
    created = app.create()
    events: list[TurnEvent] = []
    try:
        turned = app.turn(created["id"], "alo", event_sink=events.append)
        assert turned["reply"] == "pong"
        assert turned["title"] == fallback_title(created["id"])
        assert _naming_pairs(events) == [("session.naming.started", None), ("session.naming.finished", False)]
        session = SessionManager(tmp_path / "sessions").load(created["id"])
        assert session.title is None
    finally:
        app.shutdown()


def test_naming_empty_title_reports_updated_false_turn_succeeds(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [
            ChatReply(content="pong"),
            ChatReply(content="  "),
        ]
    )
    app = _chat(tmp_path, llm)
    created = app.create()
    events: list[TurnEvent] = []
    try:
        turned = app.turn(created["id"], "alo", event_sink=events.append)
        assert turned["reply"] == "pong"
        assert _naming_pairs(events) == [("session.naming.started", None), ("session.naming.finished", False)]
        session = SessionManager(tmp_path / "sessions").load(created["id"])
        assert session.title is None
    finally:
        app.shutdown()


def test_turn_without_sink_still_works(tmp_path: Path) -> None:
    llm = FakeLLM(ChatReply(content="pong"))
    app = _chat(tmp_path, llm)
    created = app.create()
    try:
        turned = app.turn(created["id"], "alo")
        assert turned["reply"] == "pong"
        assert turned["title"] == fallback_title(created["id"])
    finally:
        app.shutdown()


def test_sink_not_stored_on_app(tmp_path: Path) -> None:
    app = _chat(tmp_path, FakeLLM(ChatReply(content="pong")))
    created = app.create()

    def sink(_event: TurnEvent) -> None:
        pass

    try:
        app.turn(created["id"], "alo", event_sink=sink)
        values = list(app.__dict__.values())
        assert all(value is not sink for value in values)
    finally:
        app.shutdown()
