"""ChatApp.turn event sink — TASK-004 verification."""
from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool
from test_serve_chat import FakeLLM, ScriptedLLM, _chat

from thyca.agent.events import TurnEvent
from thyca.app.chat_app import ChatApp
from thyca.config import McpServerCfg, default_config, load, save
from thyca.llm.llm_base import ChatReply, LLMError
from thyca.memory.active import ActiveMemory
from thyca.memory.heading import parse_heading
from thyca.core.protocol import Message, ToolCall
from thyca.sessions import SessionBusy, SessionManager
from thyca.sessions.title import fallback_title
from thyca.tools.mcp import MCPProcess


def _types(events: list[TurnEvent]) -> list[str]:
    return [event.type for event in events]


def _naming_pairs(events: list[TurnEvent]) -> list[tuple[str, bool | None]]:
    return [
        (event.type, event.updated)
        for event in events
        if event.type.startswith("session.naming")
    ]


def test_second_turn_emits_naming_pairs_and_persists_title(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [
            ChatReply(content="pong"),
            ChatReply(content="pong again"),
            ChatReply(content='"Cà phê với Hòa."'),
        ]
    )
    app = _chat(tmp_path, llm)
    created = app.create()
    first_events: list[TurnEvent] = []
    try:
        first = app.turn(created["id"], "alo", event_sink=first_events.append)
        assert first["reply"] == "pong"
        assert first["title"] == fallback_title(created["id"])
        assert _naming_pairs(first_events) == []
        assert len(llm.requests) == 1
        events: list[TurnEvent] = []
        turned = app.turn(created["id"], "kể thêm", event_sink=events.append)
        assert turned["reply"] == "pong again"
        assert turned["title"] == "Cà phê với Hòa"
        assert events[-2].type == "session.naming.started"
        assert events[-1].type == "session.naming.finished"
        assert _naming_pairs(events) == [("session.naming.started", None), ("session.naming.finished", True)]
        assert len(llm.requests) == 3
        session = SessionManager(tmp_path / "sessions").load(created["id"])
        assert session.title == "Cà phê với Hòa"
        assert session.naming_attempted is True
        assert [(item.role, item.content) for item in session.messages] == [
            ("user", "alo"),
            ("assistant", "pong"),
            ("user", "kể thêm"),
            ("assistant", "pong again"),
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
            ChatReply(content="again"),
            ChatReply(content='"Cà phê với Hòa."'),
            ChatReply(content="third"),
        ]
    )
    app = _chat(tmp_path, llm)
    created = app.create()
    try:
        first = app.turn(created["id"], "alo")
        assert first["title"] == fallback_title(created["id"])
        second = app.turn(created["id"], "thêm")
        assert second["title"] == "Cà phê với Hòa"
        events: list[TurnEvent] = []
        third = app.turn(created["id"], "nữa", event_sink=events.append)
        assert third["reply"] == "third"
        assert third["title"] == "Cà phê với Hòa"
        assert _naming_pairs(events) == []
        assert len(llm.requests) == 4
    finally:
        app.shutdown()


def test_naming_llm_error_keeps_turn_and_reports_updated_false(tmp_path: Path) -> None:
    class TitleBoom(FakeLLM):
        async def chat(self, messages, tools=None):
            # The naming call is the standalone one without tools; every
            # turn call carries the tool schema.
            if tools is None:
                self.requests.append(list(messages))
                self.tools.append(tools)
                raise LLMError("title failed")
            return await super().chat(messages, tools)

    llm = TitleBoom(ChatReply(content="pong"))
    app = _chat(tmp_path, llm)
    created = app.create()
    events: list[TurnEvent] = []
    try:
        first = app.turn(created["id"], "alo")
        assert first["title"] == fallback_title(created["id"])
        turned = app.turn(created["id"], "thêm", event_sink=events.append)
        assert turned["reply"] == "pong"
        assert turned["title"] == fallback_title(created["id"])
        assert _naming_pairs(events) == [("session.naming.started", None), ("session.naming.finished", False)]
        session = SessionManager(tmp_path / "sessions").load(created["id"])
        assert session.title is None
        assert session.naming_attempted is True
        assert [(item.role, item.content) for item in session.messages] == [
            ("user", "alo"),
            ("assistant", "pong"),
            ("user", "thêm"),
            ("assistant", "pong"),
        ]
        # The failed attempt is still the one attempt: no retry next turn.
        retry_events: list[TurnEvent] = []
        third = app.turn(created["id"], "nữa", event_sink=retry_events.append)
        assert third["title"] == fallback_title(created["id"])
        assert _naming_pairs(retry_events) == []
        assert len(llm.requests) == 4
    finally:
        app.shutdown()


def test_naming_rejected_echo_reports_updated_false_turn_succeeds(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [
            ChatReply(content="pong"),
            ChatReply(content="pong again"),
            ChatReply(content="alo"),
            ChatReply(content="third reply"),
        ]
    )
    app = _chat(tmp_path, llm)
    created = app.create()
    events: list[TurnEvent] = []
    try:
        app.turn(created["id"], "alo")
        turned = app.turn(created["id"], "thêm", event_sink=events.append)
        assert turned["reply"] == "pong again"
        assert turned["title"] == fallback_title(created["id"])
        assert _naming_pairs(events) == [("session.naming.started", None), ("session.naming.finished", False)]
        session = SessionManager(tmp_path / "sessions").load(created["id"])
        assert session.title is None
        assert session.naming_attempted is True
        # Rejected proposal consumes the attempt: the next turn adds one
        # request only, with no naming pair.
        retry_events: list[TurnEvent] = []
        app.turn(created["id"], "nữa", event_sink=retry_events.append)
        assert _naming_pairs(retry_events) == []
        assert len(llm.requests) == 4
    finally:
        app.shutdown()


def test_naming_empty_title_reports_updated_false_turn_succeeds(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [
            ChatReply(content="pong"),
            ChatReply(content="pong again"),
            ChatReply(content="  "),
        ]
    )
    app = _chat(tmp_path, llm)
    created = app.create()
    events: list[TurnEvent] = []
    try:
        app.turn(created["id"], "alo")
        turned = app.turn(created["id"], "thêm", event_sink=events.append)
        assert turned["reply"] == "pong again"
        assert _naming_pairs(events) == [("session.naming.started", None), ("session.naming.finished", False)]
        session = SessionManager(tmp_path / "sessions").load(created["id"])
        assert session.title is None
        assert session.naming_attempted is True
    finally:
        app.shutdown()


def test_naming_meta_persists_usage_cost_and_counts_as_request(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [
            ChatReply(
                content="pong",
                model="gpt-4o-mini",
                usage={
                    "prompt_tokens": 5,
                    "cached_tokens": 0,
                    "completion_tokens": 2,
                    "total_tokens": 7,
                },
            ),
            ChatReply(
                content="pong again",
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
        app.turn(created["id"], "thêm")
        session = SessionManager(tmp_path / "sessions").load(created["id"])
        naming = [m for m in session.messages if (m.meta or {}).get("kind") == "naming"]
        assert len(naming) == 1
        meta = naming[0].meta
        assert meta["model"] == "gpt-4o-mini"
        assert meta["usage"]["total_tokens"] == 47
        assert isinstance(meta["cost_usd"], float) and meta["cost_usd"] > 0

        # trace aggregates: naming là một llm call → requests = 2, usage/cost gộp cả naming
        from thyca.serve.trace import turns_from_session

        turns = turns_from_session(session)
        assert len(turns) == 2
        assert turns[0].requests == 1
        assert turns[1].requests == 2
        assert turns[1].total_tokens == 60
        assert turns[1].status == "completed"
    finally:
        app.shutdown()


def test_naming_meta_does_not_flip_failed_turn_status(tmp_path: Path) -> None:
    from thyca.serve.trace import turns_from_session

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


def test_failed_turn_does_not_advance_naming_threshold(tmp_path: Path) -> None:
    class Flaky:
        def __init__(self) -> None:
            self.requests: list = []
            self.calls = 0

        async def chat(self, messages, tools=None):
            self.requests.append(list(messages))
            self.calls += 1
            if self.calls == 2:
                raise LLMError("provider HTTP 500: boom")
            if tools is None:
                return ChatReply(content="Tên sau lỗi")
            return ChatReply(content=f"reply-{self.calls}")

    llm = Flaky()
    app = _chat(tmp_path, llm)
    created = app.create()
    try:
        app.turn(created["id"], "một")
        with pytest.raises(LLMError):
            app.turn(created["id"], "hai")
        events: list[TurnEvent] = []
        third = app.turn(created["id"], "ba", event_sink=events.append)
        # Only the first and third turns completed: naming fires here.
        assert third["title"] == "Tên sau lỗi"
        assert _naming_pairs(events) == [
            ("session.naming.started", None),
            ("session.naming.finished", True),
        ]
        session = SessionManager(tmp_path / "sessions").load(created["id"])
        assert session.naming_attempted is True
    finally:
        app.shutdown()


def test_manual_rename_between_turns_wins_over_naming(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [
            ChatReply(content="pong"),
            ChatReply(content="pong again"),
        ]
    )
    app = _chat(tmp_path, llm)
    created = app.create()
    try:
        app.turn(created["id"], "alo")
        assert app.rename_session(created["id"], "Tên tôi tự đặt") == "Tên tôi tự đặt"
        events: list[TurnEvent] = []
        second = app.turn(created["id"], "thêm", event_sink=events.append)
        assert second["title"] == "Tên tôi tự đặt"
        assert _naming_pairs(events) == []
        assert len(llm.requests) == 2
    finally:
        app.shutdown()


def test_naming_request_carries_both_turns_context(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [
            ChatReply(content="quý mèo trả lời"),
            ChatReply(content="quý chó trả lời"),
            ChatReply(content="Chuyện thú cưng"),
        ]
    )
    app = _chat(tmp_path, llm)
    created = app.create()
    try:
        app.turn(created["id"], "chuyện con mèo")
        second = app.turn(created["id"], "chuyện con chó")
        assert second["title"] == "Chuyện thú cưng"
        assert len(llm.requests) == 3
        naming_call = llm.requests[2]
        assert naming_call[0].role == "system"
        context = naming_call[1].content or ""
        assert "chuyện con mèo" in context
        assert "quý mèo trả lời" in context
        assert "chuyện con chó" in context
        assert "quý chó trả lời" in context
    finally:
        app.shutdown()


def test_missing_naming_prompt_consumes_attempt_without_model_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import thyca.sessions.title as title_mod

    monkeypatch.setattr(title_mod, "_INSTRUCTION_CACHE", None)
    monkeypatch.setattr(title_mod, "_PROMPTS_DIR", tmp_path / "absent-prompts")
    llm = ScriptedLLM(
        [
            ChatReply(content="pong"),
            ChatReply(content="pong again"),
            ChatReply(content="third"),
        ]
    )
    app = _chat(tmp_path, llm)
    created = app.create()
    try:
        app.turn(created["id"], "alo")
        events: list[TurnEvent] = []
        turned = app.turn(created["id"], "thêm", event_sink=events.append)
        assert turned["title"] == fallback_title(created["id"])
        assert _naming_pairs(events) == [
            ("session.naming.started", None),
            ("session.naming.finished", False),
        ]
        # Exactly the two turn calls: the sidecar never rang the model.
        assert len(llm.requests) == 2
        assert SessionManager(tmp_path / "sessions").load(created["id"]).naming_attempted is True
        third_events: list[TurnEvent] = []
        app.turn(created["id"], "nữa", event_sink=third_events.append)
        assert _naming_pairs(third_events) == []
        assert len(llm.requests) == 3
    finally:
        app.shutdown()


def test_naming_unexpected_provider_error_never_fails_turn(tmp_path: Path) -> None:
    class ProviderBoom(FakeLLM):
        async def chat(self, messages, tools=None):
            if tools is None:
                self.requests.append(list(messages))
                self.tools.append(tools)
                raise RuntimeError("provider exploded")
            return await super().chat(messages, tools)

    app = _chat(tmp_path, ProviderBoom(ChatReply(content="pong")))
    created = app.create()
    events: list[TurnEvent] = []
    try:
        app.turn(created["id"], "alo")
        turned = app.turn(created["id"], "thêm", event_sink=events.append)
        assert turned["reply"] == "pong"
        assert turned["title"] == fallback_title(created["id"])
        assert _naming_pairs(events) == [
            ("session.naming.started", None),
            ("session.naming.finished", False),
        ]
        session = SessionManager(tmp_path / "sessions").load(created["id"])
        assert session.title is None
        assert session.naming_attempted is True
        assert all((item.meta or {}).get("kind") != "naming" for item in session.messages)
    finally:
        app.shutdown()


def test_naming_store_error_never_fails_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from thyca.sessions import SessionError

    def boom(self, title: str):
        raise SessionError("disk gone")

    monkeypatch.setattr(SessionManager, "set_title_if_missing", boom)
    llm = ScriptedLLM(
        [
            ChatReply(content="pong"),
            ChatReply(content="pong again"),
            ChatReply(content="Tên hay"),
        ]
    )
    app = _chat(tmp_path, llm)
    created = app.create()
    events: list[TurnEvent] = []
    try:
        app.turn(created["id"], "alo")
        turned = app.turn(created["id"], "thêm", event_sink=events.append)
        assert turned["reply"] == "pong again"
        assert turned["title"] == fallback_title(created["id"])
        assert _naming_pairs(events) == [
            ("session.naming.started", None),
            ("session.naming.finished", False),
        ]
        assert SessionManager(tmp_path / "sessions").load(created["id"]).naming_attempted is True
    finally:
        app.shutdown()


async def test_naming_cancelled_pairs_finished_and_keeps_flag(tmp_path: Path) -> None:
    from thyca.app.naming import _name_if_needed
    from thyca.config import default_config

    manager = SessionManager(tmp_path)
    session = manager.create()
    manager.append(Message(role="user", content="alo", ts="2026-01-01T00:00:00Z"))
    manager.append(Message(role="assistant", content="pong", ts="2026-01-01T00:00:01Z"))
    manager.append(Message(role="user", content="thêm", ts="2026-01-01T00:00:02Z"))
    manager.append(Message(role="assistant", content="pong", ts="2026-01-01T00:00:03Z"))

    class Cancelled:
        async def chat(self, messages, tools=None):
            raise asyncio.CancelledError()

    events: list[TurnEvent] = []
    with pytest.raises(asyncio.CancelledError):
        await _name_if_needed(Cancelled(), manager, default_config(), events.append)
    assert _naming_pairs(events) == [
        ("session.naming.started", None),
        ("session.naming.finished", False),
    ]
    assert SessionManager(tmp_path).load(session.id).naming_attempted is True


async def test_naming_mark_failure_skips_model_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from thyca.app.naming import _name_if_needed
    from thyca.config import default_config
    from thyca.sessions import SessionError

    manager = SessionManager(tmp_path)
    manager.create()
    manager.append(Message(role="user", content="alo", ts="2026-01-01T00:00:00Z"))
    manager.append(Message(role="assistant", content="pong", ts="2026-01-01T00:00:01Z"))
    manager.append(Message(role="user", content="thêm", ts="2026-01-01T00:00:02Z"))
    manager.append(Message(role="assistant", content="pong", ts="2026-01-01T00:00:03Z"))

    def boom(self):
        raise SessionError("disk gone")

    monkeypatch.setattr(SessionManager, "mark_naming_attempted", boom)

    class Spy:
        def __init__(self) -> None:
            self.calls = 0

        async def chat(self, messages, tools=None):
            self.calls += 1
            return ChatReply(content="Tên hay")

    spy = Spy()
    events: list[TurnEvent] = []
    assert await _name_if_needed(spy, manager, default_config(), events.append) is False
    assert events == []
    assert spy.calls == 0


def test_memory_remember_injects_turn_session_id(tmp_path: Path) -> None:
    class RememberLLM:
        def __init__(self) -> None:
            self.calls = 0

        async def chat(self, messages, tools=None):
            self.calls += 1
            if self.calls == 1:
                return ChatReply(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="remember-1",
                            name="memory_remember",
                            arguments={"topic": "linked", "summary": "linked-token"},
                        )
                    ],
                )
            return ChatReply(content="done")

    app = _chat(tmp_path, RememberLLM())
    created = app.create()
    try:
        app.turn(created["id"], "remember this")
        daily = next((tmp_path / "memory").glob("*.md"))
        meta = next(
            parsed
            for line in daily.read_text(encoding="utf-8").splitlines()
            if (parsed := parse_heading(line))
        )
        assert meta.chat == created["id"]
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

    def gated_refresh(self, state, now, *args, **kwargs):
        claimed.set()
        release.wait(5)
        return real_refresh(self, state, now, *args, **kwargs)

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


def test_corrupt_config_file_falls_back_to_passed_cfg(tmp_path: Path) -> None:
    """A corrupt on-disk config.json must not break init when a good cfg was passed."""
    from test_serve_chat import FakeLLM

    from thyca.app.chat_app import ChatApp
    from thyca.config import default_config
    from thyca.llm.llm_base import ChatReply

    (tmp_path / "config.json").write_text("{not valid json", encoding="utf-8")
    cfg = default_config()
    app = ChatApp(tmp_path, cfg, connect=FakeLLM(ChatReply(content="pong")))
    try:
        assert app.default_model() == cfg.defaultModel
    finally:
        app.shutdown()


def test_cancel_past_wait_reports_turn_in_flight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import thyca.app.chat_app as chat_mod
    from thyca.app.chat_app import TurnInFlight

    monkeypatch.setattr(chat_mod, "_CANCEL_WAIT_S", 0.05)
    release = threading.Event()

    class BlockingLLM:
        async def chat(self, messages, tools=None):
            await asyncio.to_thread(release.wait, 10)
            return ChatReply(content="late")

    app = _chat(tmp_path, BlockingLLM())
    try:
        created = app.create()
        errors: list[BaseException] = []

        def run() -> None:
            try:
                app.turn(created["id"], "go")
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        deadline = time.monotonic() + 5
        while created["id"] not in app.running_sessions():
            assert time.monotonic() < deadline, "turn never started"
            time.sleep(0.01)
        with pytest.raises(TurnInFlight):
            app.cancel(created["id"])
        # The cancel was still requested: the worker ends cancelled (or
        # finishes once released) — either way the hub is released.
        release.set()
        worker.join(timeout=10)
        assert not worker.is_alive()
        from thyca.app.chat_app import TurnCancelled

        assert all(isinstance(exc, TurnCancelled) for exc in errors)
        assert created["id"] not in app.running_sessions()
    finally:
        release.set()
        app.shutdown()


def test_cancel_turn_in_flight_maps_to_409() -> None:
    from thyca.app.chat_app import TurnInFlight
    from thyca.serve.sessions_api import session_turn_cancel

    class Handler:
        def __init__(self) -> None:
            self.sent: tuple[int, dict] | None = None

        def _read_body(self) -> bytes:
            return b""

        def _json(self, status: int, payload: dict) -> None:
            self.sent = (status, payload)

    class BusyApp:
        def cancel(self, session_id: str) -> None:
            raise TurnInFlight("turn still in flight")

    handler = Handler()
    session_turn_cancel(handler, BusyApp(), "s-id")
    assert handler.sent == (409, {"error": "turn in flight"})


def test_naming_usage_mutate_after_does_not_leak(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from thyca.app.naming import _record_naming
    from thyca.config import default_config
    from thyca.sessions import SessionManager

    manager = SessionManager(tmp_path / "sessions")
    manager.create()
    usage = {"prompt_tokens": 10, "completion_tokens": 5}
    _record_naming(
        SimpleNamespace(usage=usage, model="m-test"), 7, manager, default_config()
    )
    usage["prompt_tokens"] = 999
    usage["injected"] = True
    stored = manager.current.messages[-1]
    assert stored.meta is not None
    assert stored.meta["usage"] == {"prompt_tokens": 10, "completion_tokens": 5}


def test_turn_refreshes_live_gateway_soft_timeout(tmp_path: Path) -> None:
    """TASK-034: a saved softTimeoutS applies to the next turn, no restart."""
    from dataclasses import replace

    from thyca.config import load, save

    app = _chat(tmp_path, FakeLLM(ChatReply(content="pong")))
    created = app.create()
    try:
        assert app._gateway._soft_timeout_s == 60  # default at construction
        cfg = load(tmp_path / "config.json")
        save(replace(cfg, limits=replace(cfg.limits, softTimeoutS=5)), tmp_path / "config.json")
        app.turn(created["id"], "alo")
        assert app._gateway._soft_timeout_s == 5
    finally:
        app.shutdown()


# Moved from test_b4_unification.py / test_b4_p2.py (B4 batch).
import pytest
from pathlib import Path

def test_x2_turn_options_single_shape_check() -> None:
    from thyca.app.chat_app import InvalidTurnOption
    from thyca.app.turn_options import MODEL_MAX, validate_turn_options

    assert MODEL_MAX == 200
    assert validate_turn_options({"model": "m", "effort": "e", "retry": True}) == (
        "m",
        "e",
        True,
    )
    with pytest.raises(InvalidTurnOption):
        validate_turn_options({"model": "a\nb"})


def test_x20_single_registry_lifecycle() -> None:
    import asyncio as aio

    from thyca.app.loop_turns import _LoopTurns
    from thyca.serve.turn_state import TurnState
    from thyca.sessions import SessionBusy, SessionError

    loop = aio.new_event_loop()
    try:
        turns = TurnState()
        view = _LoopTurns(loop, turns)
        hub = turns.claim("s")
        assert turns.hub("s") is hub
        assert turns.job("s") is None
        job = view.begin("s")
        assert turns.job("s") is job
        with pytest.raises(SessionBusy):
            turns.claim("s")
        assert view.request_cancel("missing") is False
        assert view.request_cancel("s") is True
        other = view.begin("s")  # re-begin overwrites, like before
        assert turns.job("s") is other
        view.end("s", job)  # mismatch: pinned job stays
        assert turns.job("s") is other
        view.end("s", other)
        assert turns.job("s") is None
        turns.release("s")
        assert turns.hub("s") is None
        with pytest.raises(SessionError):
            view.begin("never-claimed")
    finally:
        loop.close()


def test_x21_shared_loop_wiring(tmp_path: Path) -> None:
    from thyca.agent.act import Act
    from thyca.agent.assemble import Assemble
    from thyca.agent.loop import AgentLoop
    from thyca.agent.observe import Observe
    from thyca.agent.think import Think
    from thyca.app.toolchain import build_agent_loop
    from thyca.sessions import SessionManager

    sessions = SessionManager(tmp_path)
    sessions.create()

    async def fake_chat(messages, tools=None):
        from thyca.llm.llm_base import ChatReply

        return ChatReply(content="x")

    loop = build_agent_loop(
        sessions=sessions,
        connect=fake_chat,  # type: ignore[arg-type]
        act=Act(dispatcher=None),  # type: ignore[arg-type]
        tools=[],
        loop_max=3,
        model="m",
        pricing=None,
    )
    assert isinstance(loop, AgentLoop)
    assert isinstance(loop._assemble, Assemble)
    assert isinstance(loop._think, Think)
    assert isinstance(loop._observe, Observe)


def test_x24_naming_meta_matches_sidecar_shape() -> None:
    from types import SimpleNamespace

    from thyca.agent.meta import assistant_meta, naming_meta
    from thyca.agent.stage import Stage
    from thyca.config import default_config
    from thyca.llm.llm_base import ChatReply

    cfg = default_config()
    reply = ChatReply(
        content="x",
        model="gpt-4o-mini",
        usage={"prompt_tokens": 100, "completion_tokens": 10},
    )
    meta = naming_meta(reply, 5, cfg)
    assert list(meta) == ["kind", "latency_ms", "model", "usage", "cost_usd"]
    assert meta["kind"] == "naming"
    assert meta["usage"] == {"prompt_tokens": 100, "completion_tokens": 10}
    assert meta["usage"] is not reply.usage
    assert meta["cost_usd"] == pytest.approx(0.000021)
    assert naming_meta(SimpleNamespace(model=None, usage=None), -3, cfg) == {
        "kind": "naming",
        "latency_ms": 0,
        "model": "gpt-4o-mini",
    }
    stage = Stage()
    stage.reply = reply
    assert isinstance(assistant_meta(stage), dict)


# Moved from tests/test_b2_contracts.py (B2 batch).
from thyca.app.chat_app import InvalidTurnOption, InvalidTurnText
from thyca.app.turn_options import _clean_turn_text, overlay_turn_cfg
from thyca.config import Config, ModelCfg

def test_f5_turn_effort_override_is_model_aware() -> None:
    """Fails pre-fix: per-turn junk effort sailed through overlay."""
    cfg = Config(
        models={"m": ModelCfg(reasoningEfforts=("minimal", "medium"))},
        defaultModel="m",
    )
    assert overlay_turn_cfg(cfg, "m", "minimal").defaultModel == "m"
    with pytest.raises(InvalidTurnOption, match="allows minimal/medium"):
        overlay_turn_cfg(cfg, "m", "ultra")
    plain = default_config()
    with pytest.raises(InvalidTurnOption, match="use low/high/max"):
        overlay_turn_cfg(plain, None, "ultra")
    assert overlay_turn_cfg(plain, None, "low").defaultModel == plain.defaultModel


def test_f10_clean_turn_text_reasons() -> None:
    """Fails pre-fix: plain ValueError, and 'text must be a string' wording."""
    with pytest.raises(InvalidTurnText, match="^invalid$"):
        _clean_turn_text(5, retry=False)
    with pytest.raises(InvalidTurnText, match="^empty$"):
        _clean_turn_text("   ", retry=False)
    with pytest.raises(InvalidTurnText, match="^too long$"):
        _clean_turn_text("x" * 4001, retry=False)
    assert _clean_turn_text("  hi  ", retry=False) == "hi"
    assert _clean_turn_text(123, retry=True) == ""


class _TavilySession:
    """Fake MCP child exposing one `search` tool."""

    def __init__(self) -> None:
        self.initialized = False

    async def initialize(self) -> object:
        self.initialized = True
        return object()

    async def list_tools(self) -> ListToolsResult:
        return ListToolsResult(
            tools=[Tool(name="search", inputSchema={"type": "object", "properties": {}})]
        )

    async def call_tool(self, name, arguments=None, read_timeout_seconds=None, **kwargs):
        return CallToolResult(
            content=[TextContent(type="text", text="ok")], isError=False
        )


def _tavily_factory(started: list):
    def make(name: str, cfg: McpServerCfg) -> MCPProcess:
        started.append((name, dict(cfg.env)))
        return MCPProcess(
            name, cfg.command, list(cfg.args), dict(cfg.env), session=_TavilySession()
        )

    return make


def _chat_with_mcp(tmp_path: Path, connect, factory) -> ChatApp:
    save(default_config(), tmp_path / "config.json")
    return ChatApp(
        tmp_path, load(tmp_path / "config.json"), connect=connect, mcp_factory=factory
    )


def _saw_tool(llm, name: str) -> bool:
    for tools in llm.tools:
        if tools and any(item["function"]["name"] == name for item in tools):
            return True
    return False


def _save_mcp(tmp_path: Path, servers: dict) -> None:
    cfg = load(tmp_path / "config.json")
    cfg.mcpServers.clear()
    cfg.mcpServers.update(servers)
    save(cfg, tmp_path / "config.json")


def test_turn_hot_reloads_added_mcp_server(tmp_path: Path) -> None:
    started: list = []
    llm = FakeLLM(ChatReply(content="pong"))
    app = _chat_with_mcp(tmp_path, llm, _tavily_factory(started))
    try:
        created = app.create()
        app.turn(created["id"], "hi")
        assert not _saw_tool(llm, "tavily__search")
        _save_mcp(tmp_path, {"tavily": McpServerCfg(command="tavily")})
        app.turn(created["id"], "search now")
        assert _saw_tool(llm, "tavily__search")
        assert started == [("tavily", {})]
    finally:
        app.shutdown()


def test_turn_unloads_removed_mcp_server(tmp_path: Path) -> None:
    started: list = []
    llm = FakeLLM(ChatReply(content="pong"))
    save(default_config(), tmp_path / "config.json")
    _save_mcp(tmp_path, {"tavily": McpServerCfg(command="tavily")})
    app = ChatApp(
        tmp_path,
        load(tmp_path / "config.json"),
        connect=llm,
        mcp_factory=_tavily_factory(started),
    )
    try:
        created = app.create()
        app.turn(created["id"], "hi")
        assert _saw_tool(llm, "tavily__search")
        seen_before = len(llm.tools)
        _save_mcp(tmp_path, {})
        app.turn(created["id"], "again")
        for tools in llm.tools[seen_before:]:
            if tools:
                assert all(t["function"]["name"] != "tavily__search" for t in tools)
    finally:
        app.shutdown()


def test_turn_respawns_mcp_server_on_env_change(tmp_path: Path) -> None:
    started: list = []
    llm = FakeLLM(ChatReply(content="pong"))
    app = _chat_with_mcp(tmp_path, llm, _tavily_factory(started))
    try:
        created = app.create()
        _save_mcp(tmp_path, {"t": McpServerCfg(command="t", env={"K": "1"})})
        app.turn(created["id"], "hi")
        assert _saw_tool(llm, "t__search")
        _save_mcp(tmp_path, {"t": McpServerCfg(command="t", env={"K": "2"})})
        app.turn(created["id"], "again")
        assert started == [("t", {"K": "1"}), ("t", {"K": "2"})]
        assert _saw_tool(llm, "t__search")
    finally:
        app.shutdown()


def test_turn_completes_when_mcp_spawn_fails(tmp_path: Path) -> None:
    class BoomSession(_TavilySession):
        async def initialize(self) -> object:
            raise RuntimeError("child exited")

    def boom_factory(name: str, cfg: McpServerCfg) -> MCPProcess:
        return MCPProcess(
            name, cfg.command, list(cfg.args), dict(cfg.env), session=BoomSession()
        )

    llm = FakeLLM(ChatReply(content="pong"))
    app = _chat_with_mcp(tmp_path, llm, boom_factory)
    try:
        created = app.create()
        _save_mcp(tmp_path, {"bad": McpServerCfg(command="bad")})
        turned = app.turn(created["id"], "hi")
        assert turned["reply"] == "pong"
        assert not _saw_tool(llm, "bad__search")
    finally:
        app.shutdown()


def test_turn_injects_session_scoped_today(tmp_path: Path) -> None:
    llm = FakeLLM(ChatReply(content="pong"))
    app = _chat(tmp_path, llm)
    try:
        created = app.create()
        sid = created["id"]
        (day_file,) = list((tmp_path / "memory").glob("*.md"))
        day = day_file.stem
        day_file.write_text(
            f"# {day}\n"
            f"## 10:00 — own-note <!-- thyca {{\"id\":\"aaaa1111\",\"imp\":3,\"chat\":\"{sid}\"}} -->\n"
            "- body OWN-UNIQUE detail\n"
            "## 11:00 — foreign-note <!-- thyca {\"id\":\"bbbb2222\",\"imp\":3,\"chat\":\"OTHER-SID\"} -->\n"
            "- body FOREIGN-UNIQUE detail\n",
            encoding="utf-8",
        )
        app.turn(sid, "hi")
        system = next(
            m.content for msgs in llm.requests for m in msgs if m.role == "system"
        )
        assert "OWN-UNIQUE" in system
        assert "FOREIGN-UNIQUE" not in system
        assert "foreign-note" in system  # title survives in the index
        assert f"{day}#bbbb2222" in system  # pullable id
        assert "<today_elsewhere>" in system
    finally:
        app.shutdown()
