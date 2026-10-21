"""CBE-4 (view): pure open-task derivation + task_open tool event."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from thyca.bots.admin_tools import AdminDeps, register_admin_tools
from thyca.bots.bus import Bus, Envelope
from thyca.bots.card import BotCard
from thyca.bots.context import BotTurnContext, bind_bot_turn_context, reset_bot_turn_context
from thyca.bots.registry import BotRegistry
from thyca.bots.runtime import BotRuntime
from thyca.bots.tasks import derive_open_tasks, format_task_index
from thyca.core.protocol import Message, ToolCall


def _msg(role: str, content: str = "", **kw) -> Message:
    return Message(role=role, content=content, ts="2026-08-01T10:00:00Z", **kw)


def test_delegate_open_then_delivered_result_closes() -> None:
    call = ToolCall(id="c1", name="bot_delegate",
                    arguments={"bot_id": "coder", "task": "write parser"})
    open_msgs = [_msg("assistant", "", tool_calls=[call])]
    tasks = derive_open_tasks(open_msgs, bot_id="thyca")
    assert len(tasks) == 1
    assert tasks[0].task == "write parser"
    assert tasks[0].waiting_on == "coder"

    closed = open_msgs + [
        _msg("tool", json.dumps({"ok": True, "reply": "done"}), tool_call_id="c1")
    ]
    assert derive_open_tasks(closed, bot_id="thyca") == []


def test_delegate_queued_result_stays_open_waiting() -> None:
    call = ToolCall(id="c1", name="bot_send",
                    arguments={"bot_id": "coder", "message": "do it", "kind": "delegate"})
    msgs = [
        _msg("assistant", "", tool_calls=[call]),
        _msg("tool", json.dumps({"ok": True, "queued": 1}), tool_call_id="c1"),
    ]
    tasks = derive_open_tasks(msgs, bot_id="thyca")
    assert len(tasks) == 1
    assert tasks[0].status == "queued"
    assert tasks[0].waiting_on == "coder"


def test_delegate_error_result_closes() -> None:
    call = ToolCall(id="c1", name="bot_delegate",
                    arguments={"bot_id": "coder", "task": "x"})
    msgs = [
        _msg("assistant", "", tool_calls=[call]),
        _msg("tool", "boom", tool_call_id="c1", meta={"is_error": True}),
    ]
    assert derive_open_tasks(msgs, bot_id="thyca") == []


def test_task_open_survives_its_ack_and_closes_on_result_event() -> None:
    call = ToolCall(id="t1", name="task_open", arguments={"title": "long research"})
    msgs = [
        _msg("assistant", "", tool_calls=[call]),
        _msg("tool", json.dumps({"ok": True, "status": "open"}), tool_call_id="t1"),
        _msg("assistant", "still working"),
    ]
    tasks = derive_open_tasks(msgs, bot_id="coder")
    assert [task.task for task in tasks] == ["long research"]
    assert tasks[0].status == "open"

    # Span-linked open/close via attributed meta rows.
    span_msgs = [
        _msg("assistant", "research the thing",
             meta={"kind": "task_open", "span_id": "sp_1", "room": "room9"}),
        _msg("assistant", "working",
             meta={"kind": "status", "span_id": "sp_2", "parent_span_id": "sp_1"}),
    ]
    span_tasks = derive_open_tasks(span_msgs, bot_id="coder")
    assert len(span_tasks) == 1 and span_tasks[0].room == "room9"
    closed = span_msgs + [
        _msg("assistant", "done",
             meta={"kind": "result", "span_id": "sp_3", "parent_span_id": "sp_1"})
    ]
    assert derive_open_tasks(closed, bot_id="coder") == []


def test_pending_envelopes_open_and_waiting() -> None:
    incoming = Envelope(
        sender="thyca", recipient="coder", kind="delegate", session_id="room1",
        trace_id="tr_1", parent_span_id=None, ts="2026-08-01T10:00:00Z",
        payload="build the thing", span_id="sp_in",
    )
    tasks = derive_open_tasks([], [incoming], bot_id="coder")
    assert len(tasks) == 1
    assert tasks[0].room == "room1"
    assert tasks[0].waiting_on is None

    outgoing = Envelope(
        sender="coder", recipient="helper", kind="message", session_id="room1",
        trace_id="tr_2", parent_span_id=None, ts="2026-08-01T10:00:00Z",
        payload="please check", span_id="sp_out",
    )
    tasks = derive_open_tasks([], [outgoing], bot_id="coder")
    assert len(tasks) == 1
    assert tasks[0].waiting_on == "helper"


def test_format_task_index_is_compact() -> None:
    tasks = derive_open_tasks(
        [_msg("assistant", "", tool_calls=[
            ToolCall(id="t1", name="task_open", arguments={"title": "job A"})])],
        bot_id="coder",
    )
    line = format_task_index(tasks)
    assert line == "- job A · open · -"
    assert len(line) < 60


def test_task_open_tool_records_event_args(tmp_path: Path) -> None:
    registry = BotRegistry(tmp_path)
    registry.ensure_thyca()
    bus = Bus()
    runtime = BotRuntime(tmp_path, registry, bus)
    from thyca.tools.registry import ToolRegistry

    tools = ToolRegistry()
    register_admin_tools(tools, AdminDeps(registry, runtime, lambda: None))
    spec = tools.get("task_open")
    assert spec is not None
    # Denied outside a turn; inside, returns the open event payload. The
    # tool call itself lands in the caller's transcript (AgentLoop), which
    # is what derive_open_tasks scans — no mutable task store involved.
    assert "denied" in asyncio.run(spec.handler({"title": "x"}))
    token = bind_bot_turn_context(BotTurnContext(sender_id="coder", room_id="r1"))
    try:
        out = json.loads(asyncio.run(spec.handler({"title": "long research"})))
    finally:
        reset_bot_turn_context(token)
    # The ack carries the task's linked key (a fresh span id) so a later
    # result/error event with parent_span_id == key can close it.
    assert out["ok"] is True and out["task"] == "long research"
    assert out["status"] == "open" and out["bot_id"] == "coder"
    assert out["room"] == "r1"
    assert out["span_id"].startswith("sp_")
    assert out["key"] == out["span_id"] == out["task_key"]
    assert BotCard(bot_id="x", name="x", role="r", description="d").bot_id == "x"
