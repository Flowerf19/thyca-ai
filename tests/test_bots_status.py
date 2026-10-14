"""Agent Status: live runtime snapshots merged into Agent Cards.

The tracker is the backend source for the avatar states; these tests
pin the state machine (turn phases, card latches, terminal expiry,
multi-room aggregation) and the GET /api/bots payload shape without
touching the frozen contract file.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from thyca.agent.events import TurnEvent
from thyca.agent.thinking import ThinkingDelta
from thyca.app.chat_app import ChatApp
from thyca.bots.card import BotCard
from thyca.bots.status import AgentStatusTracker
from thyca.config import default_config, load, save
from thyca.core.protocol import ToolCall
from thyca.llm.llm_base import ChatReply

T0 = datetime(2026, 10, 14, 3, 0, 0, tzinfo=UTC)


def _at(seconds: int) -> datetime:
    return T0 + timedelta(seconds=seconds)


def test_tracker_turn_phases_and_done_latch() -> None:
    tracker = AgentStatusTracker()
    tracker.begin_turn("coder", "room-1", trace_id="tr-1", span_id="sp-1", at=_at(0))
    snap = tracker.snapshot("coder", at=_at(0))
    assert snap["state"] == "running"
    assert snap["active_turns"] == 1
    assert snap["room_id"] == "room-1"

    tracker.observe_event(
        "coder", "sp-1", TurnEvent(type="llm.started", round=1, bot_id="coder"), at=_at(1)
    )
    assert tracker.snapshot("coder", at=_at(1))["state"] == "thinking"

    tracker.observe_event(
        "coder", "sp-1",
        TurnEvent(type="tool.started", round=1, call_id="c1", name="read", bot_id="coder"),
        at=_at(2),
    )
    snap = tracker.snapshot("coder", at=_at(2))
    assert snap["state"] == "running"
    assert snap["current_action"] == {"kind": "tool", "name": "read", "call_id": "c1"}

    tracker.observe_event(
        "coder", "sp-1",
        TurnEvent(type="tool.finished", round=1, call_id="c1", name="read", ok=True, bot_id="coder"),
        at=_at(3),
    )
    assert tracker.snapshot("coder", at=_at(3))["current_action"] is None

    assert tracker.finish_turn("coder", "sp-1", "done", at=_at(4)) is True
    snap = tracker.snapshot("coder", at=_at(4))
    assert snap["state"] == "done"
    assert snap["last_result"]["outcome"] == "done"
    # Done is a brief latch: a minute later the bot is simply idle, but
    # the last result remains available for context renderers.
    snap = tracker.snapshot("coder", at=_at(65))
    assert snap["state"] == "idle"
    assert snap["last_result"]["outcome"] == "done"
    # Finishing twice must not resurrect or extend the latch.
    assert tracker.finish_turn("coder", "sp-1", "done", at=_at(66)) is False


def test_tracker_ignores_stale_and_foreign_events() -> None:
    tracker = AgentStatusTracker()
    tracker.begin_turn("coder", "room-1", span_id="sp-1", at=_at(0))
    tracker.observe_event(
        "coder", "sp-old", TurnEvent(type="llm.started", round=1, bot_id="coder"), at=_at(1)
    )
    tracker.observe_event(
        "coder", "sp-1", TurnEvent(type="llm.started", round=1, bot_id="researcher"), at=_at(1)
    )
    snap = tracker.snapshot("coder", at=_at(1))
    assert snap["state"] == "running"  # neither event moved the phase


def test_tracker_card_latches_waiting_and_error() -> None:
    tracker = AgentStatusTracker()
    tracker.begin_turn("coder", "room-1", span_id="sp-1", at=_at(0))
    tracker.note_card_posted("coder", "room-1", "question", at=_at(1))
    # Still mid-turn: the bot has not finished asking yet.
    assert tracker.snapshot("coder", at=_at(1))["state"] == "running"
    tracker.finish_turn("coder", "sp-1", "done", at=_at(2))
    snap = tracker.snapshot("coder", at=_at(2))
    assert snap["state"] == "waiting"
    assert snap["waiting_on"] == {"kind": "user"}

    # A user reply in that room resolves the latch.
    tracker.note_user_message("room-1", at=_at(3))
    assert tracker.snapshot("coder", at=_at(3))["state"] == "idle"

    tracker.begin_turn("coder", "room-1", span_id="sp-2", at=_at(4))
    tracker.note_card_posted("coder", "room-1", "error", at=_at(5))
    tracker.finish_turn("coder", "sp-2", "done", at=_at(6))
    snap = tracker.snapshot("coder", at=_at(6))
    assert snap["state"] == "error"
    assert snap["waiting_on"] == {"kind": "user"}


def test_tracker_foreign_finish_keeps_turn_and_forget_room_clears() -> None:
    tracker = AgentStatusTracker()
    tracker.begin_turn("coder", "room-1", span_id="sp-1", at=_at(0))
    # A finish stamped for another bot must not pop coder's turn.
    assert tracker.finish_turn("researcher", "sp-1", "done", at=_at(1)) is False
    assert tracker.snapshot("coder", at=_at(1))["active_turns"] == 1
    tracker.note_card_posted("coder", "room-1", "question", at=_at(2))
    tracker.finish_turn("coder", "sp-1", "done", at=_at(3))
    assert tracker.snapshot("coder", at=_at(3))["state"] == "waiting"
    tracker.forget_room("room-1")
    assert tracker.snapshot("coder", at=_at(3))["state"] == "idle"


def test_tracker_failure_and_paused_override() -> None:
    tracker = AgentStatusTracker()
    tracker.begin_turn("coder", "room-1", span_id="sp-1", at=_at(0))
    tracker.finish_turn("coder", "sp-1", "error", code="LLMError", at=_at(1))
    snap = tracker.snapshot("coder", at=_at(1))
    assert snap["state"] == "error"
    assert snap["last_result"] == {
        "outcome": "error", "at": "2026-10-14T03:00:01Z", "code": "LLMError",
    }
    # Paused always reads offline, whatever the latch says.
    assert tracker.snapshot("coder", paused=True, at=_at(1))["state"] == "offline"
    assert tracker.snapshot("nobody", at=_at(1))["state"] == "idle"


def test_tracker_aggregates_parallel_rooms() -> None:
    tracker = AgentStatusTracker()
    tracker.begin_turn("coder", "room-1", span_id="sp-1", at=_at(0))
    tracker.begin_turn("coder", "room-2", span_id="sp-2", at=_at(1))
    tracker.observe_event(
        "coder", "sp-2", ThinkingDelta(round=1, delta="...", bot_id="coder"), at=_at(2)
    )
    snap = tracker.snapshot("coder", at=_at(2))
    assert snap["state"] == "thinking"
    assert snap["active_turns"] == 2
    assert snap["room_id"] == "room-2"
    tracker.finish_turn("coder", "sp-2", "done", at=_at(3))
    snap = tracker.snapshot("coder", at=_at(3))
    assert snap["state"] == "running"  # room-1 still active; done must not win
    assert snap["active_turns"] == 1


def _chat(tmp_path: Path, connect) -> ChatApp:
    save(default_config(), tmp_path / "config.json")
    return ChatApp(tmp_path, load(tmp_path / "config.json"), connect=connect)


class _ReplyLLM:
    async def chat(self, messages, tools=None):
        return ChatReply(
            content="xong rồi", model="m",
            usage={"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
        )


def test_bots_list_merges_agent_status(tmp_path: Path) -> None:
    app = _chat(tmp_path, _ReplyLLM())
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="P"))
        app._bots.set_paused("coder", True)
        cards = {card["id"]: card for card in app.bots_list()}
        assert cards["thyca"]["agent_status"]["state"] == "idle"
        assert cards["coder"]["agent_status"]["state"] == "offline"
        # Legacy lifecycle field stays untouched for existing consumers.
        assert cards["coder"]["status"] == "paused"
    finally:
        app.shutdown()


def test_completed_bot_turn_reports_done_then_idle(tmp_path: Path) -> None:
    app = _chat(tmp_path, _ReplyLLM())
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="P"))
        room = app.create(bots=["coder"])["id"]
        detail = app.turn(room, "làm giúp mình")
        assert detail["reply"] == "xong rồi"
        cards = {card["id"]: card for card in app.bots_list()}
        status = cards["coder"]["agent_status"]
        assert status["state"] == "done"
        assert status["last_result"]["outcome"] == "done"
    finally:
        app.shutdown()


class _AskLLM:
    async def chat(self, messages, tools=None):
        tool_text = "\n".join(
            str(m.content or "") for m in messages if m.role == "tool"
        )
        usage = {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6}
        if tool_text:
            return ChatReply(content="Bạn chọn giúp mình nhé.", model="m", usage=usage)
        return ChatReply(content=None, model="m", tool_calls=[
            ToolCall(id="t-ask", name="ask_user", arguments={
                "title": "Chọn một mức?",
                "options": ["A", "B"],
            }),
        ])


class _ReportLLM:
    async def chat(self, messages, tools=None):
        tool_text = "\n".join(
            str(m.content or "") for m in messages if m.role == "tool"
        )
        usage = {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6}
        if tool_text:
            return ChatReply(content="coder stopped", model="m", usage=usage)
        return ChatReply(content=None, model="m", tool_calls=[
            ToolCall(id="t-err", name="report_error", arguments={
                "summary": "Bị chặn.",
                "progress": [{"label": "Kiểm tra", "state": "stuck"}],
                "choices": ["Thử lại", "Bỏ qua"],
            }),
        ])


def test_report_error_turn_reports_error(tmp_path: Path) -> None:
    app = _chat(tmp_path, _ReportLLM())
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="P"))
        room = app.create(bots=["coder"])["id"]
        app.turn(room, "làm giúp mình")
        cards = {card["id"]: card for card in app.bots_list()}
        status = cards["coder"]["agent_status"]
        assert status["state"] == "error"
        assert status["waiting_on"] == {"kind": "user"}
    finally:
        app.shutdown()


class _FailLLM:
    async def chat(self, messages, tools=None):
        raise RuntimeError("provider down")


def test_failed_bot_turn_reports_error(tmp_path: Path) -> None:
    app = _chat(tmp_path, _FailLLM())
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="P"))
        room = app.create(bots=["coder"])["id"]
        with pytest.raises(Exception):
            app.turn(room, "làm giúp mình")
        cards = {card["id"]: card for card in app.bots_list()}
        status = cards["coder"]["agent_status"]
        assert status["state"] == "error"
        assert status["last_result"]["outcome"] == "error"
    finally:
        app.shutdown()


def test_ask_user_turn_reports_waiting(tmp_path: Path) -> None:
    app = _chat(tmp_path, _AskLLM())
    try:
        room = app.create(bots=["thyca"])["id"]
        app.turn(room, "hỏi giúp mình")
        cards = {card["id"]: card for card in app.bots_list()}
        status = cards["thyca"]["agent_status"]
        assert status["state"] == "waiting"
        assert status["waiting_on"] == {"kind": "user"}
        # The user's answer resolves the latch (the reply turn itself
        # then completes as done, which is the newer state).
        app.turn(room, "A")
        cards = {card["id"]: card for card in app.bots_list()}
        assert cards["thyca"]["agent_status"]["state"] == "done"
    finally:
        app.shutdown()
