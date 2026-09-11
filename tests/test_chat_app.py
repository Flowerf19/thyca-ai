"""ChatApp.turn event sink — TASK-004 verification."""
from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest
from test_serve_chat import FakeLLM, ScriptedLLM, _chat

from thyca.agent.events import TurnEvent
from thyca.llm.llm_base import ChatReply, LLMError
from thyca.memory.active import ActiveMemory
from thyca.protocol import Message
from thyca.sessions import SessionBusy, SessionManager
from thyca.sessions.title import fallback_title


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
            ("assistant", None),
        ]
        naming = session.messages[-1]
        assert naming.meta["kind"] == "naming"
        assert isinstance(naming.meta["latency_ms"], int)
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


def test_naming_meta_persists_usage_cost_and_counts_as_request(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [
            ChatReply(
                content="pong",
                model="gpt-4o-mini",
                usage={
                    "prompt_tokens": 10,
                    "cached_tokens": 2,
                    "completion_tokens": 3,
                    "total_tokens": 13,
                },
            ),
            ChatReply(
                content='"Tựa đề đẹp."',
                model="gpt-4o-mini",
                usage={
                    "prompt_tokens": 40,
                    "cached_tokens": 0,
                    "completion_tokens": 7,
                    "total_tokens": 47,
                },
            ),
        ]
    )
    app = _chat(tmp_path, llm)
    created = app.create()
    try:
        app.turn(created["id"], "alo")
        session = SessionManager(tmp_path / "sessions").load(created["id"])
        naming = [m for m in session.messages if (m.meta or {}).get("kind") == "naming"]
        assert len(naming) == 1
        meta = naming[0].meta
        assert meta["model"] == "gpt-4o-mini"
        assert meta["usage"]["total_tokens"] == 47
        assert isinstance(meta["cost_usd"], float) and meta["cost_usd"] > 0

        # trace aggregates: naming là một llm call → requests = 2, usage/cost gộp cả naming
        from thyca.trace import turns_from_session

        turns = turns_from_session(session)
        assert len(turns) == 1
        assert turns[0].requests == 2
        assert turns[0].total_tokens == 60
        assert turns[0].status == "completed"
    finally:
        app.shutdown()


def test_naming_meta_does_not_flip_failed_turn_status(tmp_path: Path) -> None:
    from thyca.trace import turns_from_session

    session = SessionManager(tmp_path / "sessions")
    session.create()
    session.append(Message(role="user", content="alo", ts="2026-08-26T09:12:00Z"))
    session.append(
        Message(
            role="assistant",
            content="loop limit reached",
            ts="2026-08-26T09:12:05Z",
            meta={"kind": "llm", "round": 1, "finish_reason": "stop"},
        )
    )
    session.append(
        Message(
            role="assistant",
            content=None,
            ts="2026-08-26T09:12:06Z",
            meta={"kind": "naming", "latency_ms": 300},
        )
    )
    turns = turns_from_session(session.current)
    assert len(turns) == 1
    assert turns[0].status == "loop_limit"


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


def test_two_sessions_run_turns_in_parallel(tmp_path: Path) -> None:
    """A turn in flight owns only its own session — it blocks no other one."""
    first_started = threading.Event()
    release = threading.Event()

    class Gated:
        async def chat(self, messages, tools=None):
            text = messages[-1].content
            if text == "slow":
                first_started.set()
                await asyncio.to_thread(release.wait)
            return ChatReply(content=f"reply:{text}")

    # One connect per turn is what the factory does; the injected one is
    # shared, so make each chat call independent instead of stateful.
    app = _chat(tmp_path, Gated())
    slow_id = app.create()["id"]
    errors: list[BaseException] = []
    slow: dict = {}

    def run_slow() -> None:
        try:
            slow.update(app.turn(slow_id, "slow"))
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=run_slow)
    worker.start()
    assert first_started.wait(2)
    try:
        # The second session is idle: its turn must complete while the first
        # one is still parked in the provider call.
        other_id = app.create()["id"]
        started_at = time.monotonic()
        quick = app.turn(other_id, "fast")
        elapsed = time.monotonic() - started_at
        assert quick["reply"] == "reply:fast"
        assert elapsed < 2, f"second session waited on the first turn: {elapsed:.2f}s"
    finally:
        release.set()
        worker.join(timeout=5)
    assert not errors
    assert slow["reply"] == "reply:slow"
    assert sorted(app.running_sessions()) == []
    # Each turn wrote only its own transcript: sharing one session holder would
    # have appended the slow reply into whichever session the fast turn loaded.
    store = SessionManager(tmp_path / "sessions")
    slow_messages = [(item.role, item.content) for item in store.load(slow_id).messages]
    fast_messages = [(item.role, item.content) for item in store.load(other_id).messages]
    assert slow_messages[:2] == [("user", "slow"), ("assistant", "reply:slow")]
    assert fast_messages[:2] == [("user", "fast"), ("assistant", "reply:fast")]
    assert not any("fast" in (content or "") for _role, content in slow_messages)
    assert not any("slow" in (content or "") for _role, content in fast_messages)
    app.shutdown()


def test_second_turn_on_same_session_is_busy(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()

    class Slow:
        async def chat(self, messages, tools=None):
            started.set()
            await asyncio.to_thread(release.wait)
            return ChatReply(content="late")

    app = _chat(tmp_path, Slow())
    session_id = app.create()["id"]
    errors: list[BaseException] = []

    def run_turn() -> None:
        try:
            app.turn(session_id, "first")
        except BaseException as exc:  # surfaced by the assert below
            errors.append(exc)

    worker = threading.Thread(target=run_turn)
    worker.start()
    assert started.wait(2)
    try:
        assert app.running_sessions()[session_id]
        detail = app.get_payload(session_id)
        assert detail["running"] is True
        assert detail["started_at"]
        with pytest.raises(SessionBusy):
            app.turn(session_id, "second")
    finally:
        release.set()
        worker.join(timeout=5)
    assert not errors
    assert app.running_sessions() == {}
    assert app.get_payload(session_id)["running"] is False
    app.shutdown()


def test_create_keeps_blank_session_with_running_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """create() prunes blank sessions except the one whose turn is in flight.

    A turn claims its session before writing anything, so an empty transcript
    on disk still belongs to a running turn.
    """
    claimed = threading.Event()
    release = threading.Event()
    real_refresh = ActiveMemory.refresh

    def gated_refresh(self, state, now):
        claimed.set()
        release.wait(5)
        return real_refresh(self, state, now)

    monkeypatch.setattr(ActiveMemory, "refresh", gated_refresh)
    app = _chat(tmp_path, FakeLLM(ChatReply(content="late")))
    running_id = app.create()["id"]
    assert app.get_payload(running_id)["messages"] == []
    errors: list[BaseException] = []

    def run_turn() -> None:
        try:
            app.turn(running_id, "hi")
        except BaseException as exc:  # surfaced by the assert below
            errors.append(exc)

    worker = threading.Thread(target=run_turn)
    worker.start()
    assert claimed.wait(2)
    try:
        fresh = app.create()
        assert fresh["id"] != running_id
        assert (tmp_path / "sessions" / f"{running_id}.jsonl").exists()
    finally:
        release.set()
        worker.join(timeout=5)
    assert not errors
    stored = SessionManager(tmp_path / "sessions").load(running_id)
    assert [(item.role, item.content) for item in stored.messages] == [
        ("user", "hi"),
        ("assistant", "late"),
    ]
    app.shutdown()


def test_turn_response_does_not_claim_to_be_running(tmp_path: Path) -> None:
    """The turn's own payload must not tell the client to wait for itself."""
    app = _chat(tmp_path, FakeLLM(ChatReply(content="pong")))
    created = app.create()
    try:
        turned = app.turn(created["id"], "alo")
        assert turned["running"] is False
        assert "started_at" not in turned
    finally:
        app.shutdown()
