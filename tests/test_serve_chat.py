"""Loopback chat HTTP — webui-chat-backend."""
from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass, field, replace
from io import StringIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from thyca.agents.events import TurnEvent
from thyca.serve.bridge import SENTINEL
from thyca.app.chat_app import ChatApp
from thyca.configs import ModelCfg, ProviderEntry, load, save
from seeded_config import seeded_config
from thyca.llms.llm_base import ChatReply, LLMError
from thyca.core.protocol import Message, ToolCall
from thyca.serve import ServeError, default_webui, make_server
from thyca.conversations.runtime import ConversationRuntime, ConversationBusy, ConversationManager
from thyca.tools.mcp import StartupDiagnostic
from thyca.memories.facade import MemoryFacade

WEBUI = default_webui()


@dataclass
class FakeLLM:
    reply: ChatReply
    requests: list[list[Message]] = field(default_factory=list)
    tools: list = field(default_factory=list)

    async def chat(self, messages: list[Message], tools: list | None = None) -> ChatReply:
        self.requests.append(list(messages))
        self.tools.append(tools)
        return self.reply


@dataclass
class ScriptedLLM:
    replies: list[ChatReply]
    requests: list[list[Message]] = field(default_factory=list)
    tools: list = field(default_factory=list)

    async def chat(self, messages: list[Message], tools: list | None = None) -> ChatReply:
        self.requests.append(list(messages))
        self.tools.append(tools)
        if not self.replies:
            raise LLMError("no scripted reply")
        return self.replies.pop(0)


def _url(httpd, path: str) -> str:
    port = httpd.server_address[1]
    return f"http://127.0.0.1:{port}{path}"


def _chat(tmp_path: Path, connect=None) -> ChatApp:
    save(seeded_config(provider_id="default"), tmp_path / "config.json")
    return ChatApp(tmp_path, load(tmp_path / "config.json"), connect=connect)


def test_chat_app_mcp_diagnostic_includes_server_name(
    tmp_path: Path, monkeypatch
) -> None:
    class FakeManager:
        async def spawn_all(self, servers):
            return [StartupDiagnostic("remote", False, "failed to start")]

        def tool_specs(self):
            return []

        async def shutdown(self) -> None:
            return

    stderr = StringIO()
    monkeypatch.setattr("thyca.app.chat_app.MCPManager", FakeManager)
    monkeypatch.setattr("thyca.app.chat_app.sys.stderr", stderr)
    app = _chat(tmp_path, FakeLLM(ChatReply(content="unused")))
    try:
        assert "remote: failed to start" in stderr.getvalue()
    finally:
        app.shutdown()


def _start(tmp_path: Path, chat: ChatApp | None = None):
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    httpd = make_server(host="127.0.0.1", port=0, webui=WEBUI, facade=facade, chat=chat)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


def _stop(httpd, thread: threading.Thread) -> None:
    httpd.shutdown()
    thread.join(timeout=2)
    httpd.server_close()


def _json(httpd, path: str, *, method: str = "GET", data: bytes | None = None) -> dict:
    headers = {"Content-Type": "application/json"} if data is not None else {}
    request = Request(_url(httpd, path), data=data, method=method, headers=headers)
    with urlopen(request, timeout=5) as response:
        assert response.headers.get_content_type() == "application/json"
        return json.loads(response.read().decode("utf-8"))


def _json_result(httpd, path: str) -> tuple[int, dict]:
    try:
        return 200, _json(httpd, path)
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_bot_operations_endpoint_scope_options_and_read_only(tmp_path: Path) -> None:
    from thyca.bots.card import BotCard

    app = _chat(tmp_path)
    try:
        app._bots.create(BotCard(
            bot_id="coder", name="Coder", role="Coder", description="Codes.",
            persona="You code.",
        ))
        dm = app.create()
        group = app.create(bots=["thyca", "coder"])
        timeline = tmp_path / "bots" / "coder" / "workspace" / "timeline.jsonl"
        assert not timeline.exists()
        httpd, thread = _start(tmp_path, app)
        try:
            options = urlencode({
                "model": app._current_cfg().defaultModel,
                "effort": app._current_cfg().provider.reasoningEffort,
            })
            status, payload = _json_result(
                httpd,
                f"/api/bots/coder/groups/{group["conversation_id"]}/operations?{options}",
            )
            assert status == 200
            assert set(payload) == {
                "bot_id", "conversation_id", "model", "context", "last_turn",
                "conversation", "rates", "trace",
            }
            assert payload["bot_id"] == "coder" and payload["conversation_id"] == group["conversation_id"]
            assert payload["model"]["scope"] == "next_turn_configuration"
            assert payload["model"]["id"] == app._current_cfg().defaultModel
            assert payload["model"]["reasoning_effort"] == app._current_cfg().provider.reasoningEffort
            assert payload["context"]["estimated"] is True
            assert payload["last_turn"] is None
            assert payload["conversation"]["requests"] == 0
            assert payload["conversation"]["cost_usd"] is None
            assert all(value is None for value in payload["conversation"]["usage"].values())
            forbidden = {"apiKey", "baseURL", "messages", "prompts", "reasoning_details", "arguments"}
            def keys(value):
                if isinstance(value, dict):
                    for key, child in value.items():
                        yield key
                        yield from keys(child)
                elif isinstance(value, list):
                    for child in value:
                        yield from keys(child)
            assert forbidden.isdisjoint(keys(payload))
            assert not timeline.exists()
            assert app._runtime.workspaces.store.get_binding("coder", group["conversation_id"]) is None

            for query in (
                "?unknown=value", "?model=one&model=two", "?effort=",
                "?model=bogus", "?effort=unknown",
                f"?model={'x' * 201}",
            ):
                status, body = _json_result(
                    httpd,
                    f"/api/bots/coder/groups/{group["conversation_id"]}/operations{query}",
                )
                assert status == 400, body
            for bot_id, conversation_id in (
                ("ghost", group["conversation_id"]), ("coder", f"group-{'0' * 32}"),
            ):
                status, body = _json_result(
                    httpd,
                    f"/api/bots/{bot_id}/groups/{conversation_id}/operations",
                )
                assert status == 404
                if bot_id == "coder":
                    assert body == {"error": "conversation not found"}
            assert not timeline.exists()
            assert app._runtime.workspaces.store.get_binding("coder", group["conversation_id"]) is None
        finally:
            _stop(httpd, thread)
    finally:
        app.shutdown()


def test_bot_operations_measurement_failure_is_sanitized_503(tmp_path: Path) -> None:
    from thyca.bots.card import BotCard

    app = _chat(tmp_path)
    try:
        app._bots.create(BotCard(
            bot_id="coder", name="Coder", role="Coder", description="Codes.",
            persona="You code.",
        ))
        room = app.create(bots=["thyca", "coder"])

        def broken_peek(*args, **kwargs):
            raise OSError(f"provider failure at {tmp_path}/private.conf")

        app._memory.peek = broken_peek
        httpd, thread = _start(tmp_path, app)
        try:
            status, body = _json_result(
                httpd, f"/api/bots/coder/groups/{room["conversation_id"]}/operations",
            )
            assert status == 503
            assert body == {"error": "bot information unavailable"}
            assert str(tmp_path) not in json.dumps(body)
            assert not (tmp_path / "bots" / "coder" / "workspace" / "timeline.jsonl").exists()
        finally:
            _stop(httpd, thread)
    finally:
        app.shutdown()


def test_bot_operations_missing_timeline_is_503_without_repair(tmp_path: Path) -> None:
    from thyca.bots.card import BotCard

    app = _chat(tmp_path)
    try:
        app._bots.create(BotCard(
            bot_id="coder", name="Coder", role="Coder", description="Codes.",
            persona="You code.",
        ))
        group = app.create(bots=["thyca", "coder"])
        app._runtime.internal_conversation("coder", group["conversation_id"])
        timeline = tmp_path / "bots" / "coder" / "workspace" / "timeline.jsonl"
        assert timeline.is_file()
        timeline.unlink()
        httpd, thread = _start(tmp_path, app)
        try:
            status, body = _json_result(
                httpd, f"/api/bots/coder/groups/{group["conversation_id"]}/operations",
            )
            assert status == 503
            assert body == {"error": "bot information unavailable"}
            assert str(tmp_path) not in json.dumps(body)
            assert not timeline.exists()
        finally:
            _stop(httpd, thread)
    finally:
        app.shutdown()


def test_bot_room_tasks_live_calls_exact_window_and_read_only(tmp_path: Path) -> None:
    """GET bots/<id>/groups/<sid>/tasks lists live tool/skill calls plus
    open runtime tasks for exactly that bot+room: no memory tasks, no
    completed work, no private content — and reads create no bindings
    or timeline files."""
    from thyca.agents.events import TurnEvent
    from thyca.bots.card import BotCard

    app = _chat(tmp_path)
    try:
        app._bots.create(BotCard(
            bot_id="coder", name="Coder", role="Coder", description="Codes.",
            persona="You code.",
        ))
        # Blank DMs survive the group-create prune; the reverse order
        # would discard the blank group (discard_empty).
        dm = app.create()
        group = app.create(bots=["thyca", "coder"])
        httpd, thread = _start(tmp_path, app)
        try:
            idle = _json(httpd, f"/api/bots/coder/groups/{group["conversation_id"]}/tasks")
            assert idle == {
                "bot_id": "coder", "room_id": group["conversation_id"], "state": "idle",
                "phase": None, "since": None, "updated_at": None,
                "calls": [], "tasks": [], "waiting_on": None,
            }
            # Simulate an in-flight turn: two concurrent same-name calls
            # (one tool, one skill) plus a call in another room.
            tracker = app._agent_status
            tracker.begin_turn("coder", group["conversation_id"], span_id="sp-1")
            tracker.observe_event("coder", "sp-1", TurnEvent(
                type="tool.started", round=1, call_id="c1", name="read",
                bot_id="coder"))
            tracker.observe_event("coder", "sp-1", TurnEvent(
                type="skill.started", round=1, call_id="c1", name="read",
                bot_id="coder"))
            tracker.begin_turn("coder", dm["conversation_id"], span_id="sp-2")
            tracker.observe_event("coder", "sp-2", TurnEvent(
                type="tool.started", round=1, call_id="d1", name="other",
                bot_id="coder"))
            live = _json(httpd, f"/api/bots/coder/groups/{group["conversation_id"]}/tasks")
            assert live["state"] == "running" and live["room_id"] == group["conversation_id"]
            assert live["calls"] == [
                {"kind": "skill", "name": "read", "active": 1},
                {"kind": "tool", "name": "read", "active": 1},
            ]
            assert live["waiting_on"] is None
            # The dm-room "other" call does not leak into this room's
            # exact payload; the DM owner sees none of coder's calls.
            assert _json(httpd, f"/api/bots/thyca/groups/{group["conversation_id"]}/tasks")["calls"] == []
            assert _json(httpd, "/api/bots/thyca/dm/tasks")["calls"] == []
            # A DM addressed as a group never resolves: 404, not a leak.
            status, body = _json_result(
                httpd, f"/api/bots/coder/groups/group-{'0' * 32}/tasks")
            assert status == 404 and body == {"error": "conversation not found"}
            # One finish removes one colliding call; the second clears it.
            tracker.observe_event("coder", "sp-1", TurnEvent(
                type="tool.finished", round=1, call_id="c1", name="read",
                ok=True, bot_id="coder"))
            assert _json(httpd, f"/api/bots/coder/groups/{group["conversation_id"]}/tasks")["calls"] == [
                {"kind": "skill", "name": "read", "active": 1}
            ]
            # Read-only: no workspace binding or timeline file was created.
            assert app._runtime.workspaces.store.get_binding("coder", group["conversation_id"]) is None
            assert not (tmp_path / "bots" / "coder" / "workspace" / "timeline.jsonl").exists()
            # Membership and shape errors follow the operations conventions.
            status, body = _json_result(httpd, f"/api/bots/ghost/groups/{group["conversation_id"]}/tasks")
            assert status == 404, body
            # A well-formed but missing group id reaches the handler.
            missing = f"group-{'0' * 32}"
            status, body = _json_result(httpd, f"/api/bots/coder/groups/{missing}/tasks")
            assert status == 404 and body == {"error": "conversation not found"}
            status, body = _json_result(
                httpd, f"/api/bots/coder/groups/group-{'0' * 32}/tasks")
            assert status == 404 and body == {"error": "conversation not found"}
            status, body = _json_result(
                httpd, f"/api/bots/coder/groups/{group["conversation_id"]}/tasks?scope=x")
            assert status == 400 and body == {"error": "invalid query"}
        finally:
            _stop(httpd, thread)
    finally:
        app.shutdown()


def test_bot_room_tasks_corrupt_conversation_is_unavailable_not_empty(tmp_path: Path) -> None:
    """A corrupt store door reads as 503 (unavailable), never as an empty
    task list: unreadable state must not imply 'no tasks'."""
    import sqlite3

    from thyca.bots.card import BotCard

    app = _chat(tmp_path)
    try:
        app._bots.create(BotCard(
            bot_id="coder", name="Coder", role="Coder", description="Codes.",
            persona="You code.",
        ))
        dm = app.create()
        group = app.create(bots=["thyca", "coder"])
        httpd, thread = _start(tmp_path, app)
        try:
            # Corrupt the group door's stored metadata in place; the
            # store then refuses that door instead of reading it empty.
            conn = sqlite3.connect(tmp_path / "conversations.db")
            try:
                conn.execute(
                    "UPDATE conversations SET metadata=? WHERE kind='group' AND id=?",
                    ("{not valid json", group["conversation_id"]),
                )
                conn.commit()
            finally:
                conn.close()
            status, body = _json_result(httpd, f"/api/bots/coder/groups/{group["conversation_id"]}/tasks")
            assert status == 503 and body == {"error": "bot information unavailable"}
            # The untouched room still reads normally (idle, exact window).
            idle = _json(httpd, "/api/bots/thyca/dm/tasks")
            assert idle["calls"] == [] and idle["state"] == "idle"
        finally:
            _stop(httpd, thread)
    finally:
        app.shutdown()


def _seed_room(tmp_path: Path, conversation_id: str, messages: list) -> None:
    from thyca.conversations.runtime import ConversationManager

    manager = ConversationManager(tmp_path / "sessions")
    manager.load(conversation_id)
    for message in messages:
        manager.append(message)


def _bind_journal(app, bot_id: str, room_id: str, *, prefix: str = "ev"):
    """Bind a bot window and return (window, journal writer) using the same
    canonical journal APIs the runtime turn loop writes through."""
    from thyca.bots.timeline import WorkspaceTimeline

    room = app._conversations.store.load(room_id)
    window = app._webui.window_for(room)
    app._runtime.workspaces.store.bind_window(bot_id, window)
    timeline = WorkspaceTimeline(app._root, bot_id)
    state = {"n": 0}

    def append(message) -> None:
        state["n"] += 1
        timeline.append_message(window, message, event_id=f"{prefix}-{state['n']}")

    return window, timeline, append


def test_bot_room_tasks_pocket_lists_runtime_units_not_tool_calls(tmp_path: Path) -> None:
    """The pocket lists runtime units (task_open/delegate + pending +
    completion records) for the exact window: starting a tool/skill
    creates no task row, and the serializer carries no memory-catalog
    links."""
    from thyca.agents.events import TurnEvent
    from thyca.bots.card import BotCard
    from thyca.core.protocol import Message, ToolCall

    app = _chat(tmp_path)
    try:
        app._bots.create(BotCard(
            bot_id="coder", name="Coder", role="Coder", description="Codes.",
            persona="You code.",
        ))
        dm = app.create()
        group = app.create(bots=["thyca", "coder"])
        _, _, journal = _bind_journal(app, "coder", group["conversation_id"])
        for message in [
            # task_open survives its ack (keyed by the returned span).
            Message(role="assistant", content="",
                    tool_calls=[ToolCall(id="t1", name="task_open",
                                         arguments={"title": "port parser"})]),
            Message(role="tool", content=json.dumps({"ok": True, "span_id": "sp-t1"}),
                    tool_call_id="t1"),
            # Open delegate waits on its recipient.
            Message(role="assistant", content="",
                    tool_calls=[ToolCall(id="d1", name="bot_delegate",
                                         arguments={"bot_id": "thyca",
                                                    "task": "review diff"})]),
            # Queued delegate stays open as queued.
            Message(role="assistant", content="",
                    tool_calls=[ToolCall(id="d2", name="bot_send",
                                         arguments={"bot_id": "thyca", "kind": "delegate",
                                                    "message": "migrate notes"})]),
            Message(role="tool", content=json.dumps({"ok": True, "queued": 1}),
                    tool_call_id="d2"),
            # A failed task_open never opened a task.
            Message(role="assistant", content="",
                    tool_calls=[ToolCall(id="t9", name="task_open",
                                         arguments={"title": "doomed"})]),
            Message(role="tool", content="boom", tool_call_id="t9",
                    meta={"is_error": True}),
        ]:
            journal(message)
        httpd, thread = _start(tmp_path, app)
        try:
            # Merely starting a tool creates a call, never a task row.
            tracker = app._agent_status
            tracker.begin_turn("coder", group["conversation_id"], span_id="sp-1")
            tracker.observe_event("coder", "sp-1", TurnEvent(
                type="tool.started", round=1, call_id="c1", name="read",
                bot_id="coder"))
            payload = _json(httpd, f"/api/bots/coder/groups/{group["conversation_id"]}/tasks")
            assert payload["calls"] == [{"kind": "tool", "name": "read", "active": 1}]
            assert payload["tasks"] == [
                {"title": "port parser", "status": "open", "waiting_on": None},
                {"title": "review diff", "status": "delegated", "waiting_on": "thyca"},
                {"title": "migrate notes", "status": "queued", "waiting_on": "thyca"},
            ]
            for task in payload["tasks"]:
                assert set(task) == {"title", "status", "waiting_on"}
            assert "read" not in [task["title"] for task in payload["tasks"]]
            # Read-only: the pocket read wrote nothing — the journal is
            # byte-identical before and after, and no binding was created
            # for a bot that never ran here.
            journal_path = tmp_path / "bots" / "coder" / "workspace" / "timeline.jsonl"
            digest = journal_path.read_bytes()
            again = _json(httpd, f"/api/bots/coder/groups/{group["conversation_id"]}/tasks")
            assert again["tasks"] == payload["tasks"]
            assert journal_path.read_bytes() == digest
            assert app._runtime.workspaces.store.get_binding("thyca", dm["conversation_id"]) is None
        finally:
            _stop(httpd, thread)
    finally:
        app.shutdown()


def test_bot_room_tasks_completion_records_close_tasks(tmp_path: Path) -> None:
    """Result/error records linked to an open task close it: the pocket
    shows only still-open units, never completed work."""
    from thyca.bots.card import BotCard
    from thyca.core.protocol import Message, ToolCall

    app = _chat(tmp_path)
    try:
        app._bots.create(BotCard(
            bot_id="coder", name="Coder", role="Coder", description="Codes.",
            persona="You code.",
        ))
        dm = app.create()
        group = app.create(bots=["thyca", "coder"])
        _, _, journal = _bind_journal(app, "coder", group["conversation_id"])
        for message in [
            Message(role="assistant", content="",
                    tool_calls=[ToolCall(id="t1", name="task_open",
                                         arguments={"title": "port parser"})]),
            Message(role="tool", content=json.dumps({"ok": True, "span_id": "sp-t1"}),
                    tool_call_id="t1"),
            Message(role="assistant", content="",
                    tool_calls=[ToolCall(id="d1", name="bot_delegate",
                                         arguments={"bot_id": "thyca",
                                                    "task": "review diff"})]),
        ]:
            journal(message)
        httpd, thread = _start(tmp_path, app)
        try:
            before = _json(httpd, f"/api/bots/coder/groups/{group["conversation_id"]}/tasks")
            assert [task["title"] for task in before["tasks"]] == ["port parser", "review diff"]
            for message in [
                # Linked result event closes the task_open unit.
                Message(role="assistant", content="done",
                        meta={"kind": "result", "span_id": "sp-9",
                              "parent_span_id": "sp-t1"}),
                # Plain reply closes the delegate.
                Message(role="tool", content=json.dumps({"ok": True, "reply": "looks good"}),
                        tool_call_id="d1"),
            ]:
                journal(message)
            after = _json(httpd, f"/api/bots/coder/groups/{group["conversation_id"]}/tasks")
            assert after["tasks"] == []
        finally:
            _stop(httpd, thread)
    finally:
        app.shutdown()


def test_bot_room_tasks_pending_envelopes_are_window_exact(tmp_path: Path) -> None:
    """Queued envelopes owed in this window open pocket tasks; envelopes
    for other windows never leak in."""
    from thyca.bots.card import BotCard
    from thyca.core.protocol import Message

    app = _chat(tmp_path)
    try:
        app._bots.create(BotCard(
            bot_id="coder", name="Coder", role="Coder", description="Codes.",
            persona="You code.",
        ))
        dm = app.create()
        first = app.create(bots=["thyca", "coder"])
        # Keep the first group non-blank so the second create does not
        # prune it (discard_empty).
        _seed_room(tmp_path, first["conversation_id"], [Message(role="user", content="hello")])
        second = app.create(bots=["thyca", "coder"])
        bus = app._runtime._bus
        for bot_id in ("thyca", "coder"):
            try:
                bus.register(bot_id)
            except Exception:
                pass
        bus.send(sender="thyca", recipient="coder", kind="message",
                 conversation_id=first["conversation_id"], payload="triage inbox")
        bus.send(sender="thyca", recipient="coder", kind="message",
                 conversation_id=second["conversation_id"], payload="other window work")
        httpd, thread = _start(tmp_path, app)
        try:
            here = _json(httpd, f"/api/bots/coder/groups/{first["conversation_id"]}/tasks")
            assert here["tasks"] == [
                {"title": "triage inbox", "status": "message", "waiting_on": None}
            ]
            there = _json(httpd, f"/api/bots/coder/groups/{second["conversation_id"]}/tasks")
            assert there["tasks"] == [
                {"title": "other window work", "status": "message", "waiting_on": None}
            ]
        finally:
            _stop(httpd, thread)
    finally:
        app.shutdown()


def test_bot_room_tasks_never_shows_other_bots_or_private_rows(tmp_path: Path) -> None:
    """Pocket attribution is per-bot canonical projection: a third bot's
    journal work, A2A bus rows and private-follow-up card rows in the
    shared room transcript never appear under this bot."""
    from thyca.bots.card import BotCard
    from thyca.core.protocol import Message, ToolCall

    app = _chat(tmp_path)
    try:
        for bot_id, name in (("coder", "Coder"), ("helper", "Helper")):
            app._bots.create(BotCard(
                bot_id=bot_id, name=name, role="R", description="D.",
                persona="You work.",
            ))
        dm = app.create()
        group = app.create(bots=["thyca", "coder", "helper"])
        # Coder's own projection is empty.
        _bind_journal(app, "coder", group["conversation_id"], prefix="coder")
        # Helper's real work lives only in helper's journal.
        _, _, helper_journal = _bind_journal(app, "helper", group["conversation_id"], prefix="helper")
        helper_journal(Message(
            role="assistant", content="",
            tool_calls=[ToolCall(id="h1", name="task_open",
                                 arguments={"title": "helper secret work"})]))
        helper_journal(Message(
            role="tool", content=json.dumps({"ok": True, "span_id": "sp-h1"}),
            tool_call_id="h1"))
        # Shared room rows: an A2A bus delegate row and a private-follow-up
        # card row carrying question/answer/title text.
        _seed_room(tmp_path, group["conversation_id"], [
            Message(role="assistant", content="",
                    meta={"kind": "delegate", "bus_event": True, "bot_id": "helper",
                          "recipient": "coder", "span_id": "sp-bus",
                          "parent_span_id": "sp-x", "room": group["conversation_id"]}),
            Message(role="assistant", content="",
                    meta={"kind": "card", "bot_id": "coder", "span_id": "sp-card",
                          "card": {"type": "question", "title": "Xác nhận riêng tư",
                                   "private_followup": {
                                       "pending_id": "pf-1", "revision": 3,
                                       "question": "Mật khẩu của bạn là gì?",
                                       "answer": "đáp án bí mật"}}}),
        ])
        httpd, thread = _start(tmp_path, app)
        try:
            coder_tasks = _json(httpd, f"/api/bots/coder/groups/{group["conversation_id"]}/tasks")
            assert coder_tasks["tasks"] == []
            raw = json.dumps(coder_tasks)
            for secret in ("helper secret work", "Xác nhận riêng tư",
                           "Mật khẩu của bạn là gì?", "đáp án bí mật", "pf-1"):
                assert secret not in raw
            # Positive control: helper's own pocket shows helper's work.
            helper_tasks = _json(httpd, f"/api/bots/helper/groups/{group["conversation_id"]}/tasks")
            assert helper_tasks["tasks"] == [
                {"title": "helper secret work", "status": "open", "waiting_on": None}
            ]
        finally:
            _stop(httpd, thread)
    finally:
        app.shutdown()


def test_bot_room_tasks_uses_projected_view_across_compaction(tmp_path: Path) -> None:
    """A view_reset basis omits pre-compaction rows from the bot's current
    projected view: work omitted there must not resurrect in the pocket,
    while tail work still lists."""
    from thyca.bots.card import BotCard
    from thyca.core.protocol import Message, ToolCall

    app = _chat(tmp_path)
    try:
        app._bots.create(BotCard(
            bot_id="coder", name="Coder", role="Coder", description="Codes.",
            persona="You code.",
        ))
        dm = app.create()
        group = app.create(bots=["thyca", "coder"])
        window, timeline, journal = _bind_journal(app, "coder", group["conversation_id"])
        journal(Message(role="assistant", content="",
                        tool_calls=[ToolCall(id="t-old", name="task_open",
                                             arguments={"title": "old compacted work"})]))
        journal(Message(role="tool",
                        content=json.dumps({"ok": True, "span_id": "sp-old"}),
                        tool_call_id="t-old"))
        # Compaction: the new basis carries only the tail; the old open
        # task is omitted from the current projected view.
        tail = Message(role="assistant", content="",
                       tool_calls=[ToolCall(id="t-new", name="task_open",
                                            arguments={"title": "current work"})])
        timeline.replace_view(window, [tail], event_id="compact-1")
        httpd, thread = _start(tmp_path, app)
        try:
            payload = _json(httpd, f"/api/bots/coder/groups/{group["conversation_id"]}/tasks")
            # The ack for t-new never arrived, yet the unit is open by its
            # call id; the compacted-away unit does not resurrect.
            assert payload["tasks"] == [
                {"title": "current work", "status": "open", "waiting_on": None}
            ]
        finally:
            _stop(httpd, thread)
    finally:
        app.shutdown()


def test_bot_room_tasks_unreadable_journal_is_unavailable(tmp_path: Path) -> None:
    """A bound bot whose journal cannot be read (corrupt or missing) is
    503 unavailable — never an empty list implying 'no tasks'."""
    from thyca.bots.card import BotCard
    from thyca.core.protocol import Message, ToolCall

    app = _chat(tmp_path)
    try:
        app._bots.create(BotCard(
            bot_id="coder", name="Coder", role="Coder", description="Codes.",
            persona="You code.",
        ))
        dm = app.create()
        group = app.create(bots=["thyca", "coder"])
        _, _, journal = _bind_journal(app, "coder", group["conversation_id"])
        journal(Message(role="assistant", content="",
                        tool_calls=[ToolCall(id="t1", name="task_open",
                                             arguments={"title": "real work"})]))
        path = tmp_path / "bots" / "coder" / "workspace" / "timeline.jsonl"
        assert path.is_file()
        httpd, thread = _start(tmp_path, app)
        try:
            assert _json(httpd, f"/api/bots/coder/groups/{group["conversation_id"]}/tasks")["tasks"] == [
                {"title": "real work", "status": "open", "waiting_on": None}
            ]
            with path.open("a", encoding="utf-8") as fh:
                fh.write("{torn json\n")
            status, body = _json_result(httpd, f"/api/bots/coder/groups/{group["conversation_id"]}/tasks")
            assert status == 503 and body == {"error": "bot information unavailable"}
            path.unlink()
            status, body = _json_result(httpd, f"/api/bots/coder/groups/{group["conversation_id"]}/tasks")
            assert status == 503 and body == {"error": "bot information unavailable"}
        finally:
            _stop(httpd, thread)
    finally:
        app.shutdown()


def test_bot_room_tasks_same_call_id_across_windows_and_names(tmp_path: Path) -> None:
    """Sanitized-id collisions associate correctly: same id in two windows
    stays per-window, and a same-id different-name finish removes only its
    own call, out of order."""
    from thyca.agents.events import TurnEvent
    from thyca.bots.card import BotCard

    app = _chat(tmp_path)
    try:
        app._bots.create(BotCard(
            bot_id="coder", name="Coder", role="Coder", description="Codes.",
            persona="You code.",
        ))
        dm = app.create()
        first = app.create(bots=["thyca", "coder"])
        _seed_room(tmp_path, first["conversation_id"], [Message(role="user", content="hello")])
        second = app.create(bots=["thyca", "coder"])
        tracker = app._agent_status
        tracker.begin_turn("coder", first["conversation_id"], span_id="sp-a")
        tracker.observe_event("coder", "sp-a", TurnEvent(
            type="tool.started", round=1, call_id="c1", name="read", bot_id="coder"))
        tracker.begin_turn("coder", second["conversation_id"], span_id="sp-b")
        tracker.observe_event("coder", "sp-b", TurnEvent(
            type="tool.started", round=1, call_id="c1", name="write", bot_id="coder"))
        # Same-id different-name collision in the first window.
        tracker.observe_event("coder", "sp-a", TurnEvent(
            type="tool.started", round=1, call_id="c1", name="write", bot_id="coder"))
        httpd, thread = _start(tmp_path, app)
        try:
            assert _json(httpd, f"/api/bots/coder/groups/{first["conversation_id"]}/tasks")["calls"] == [
                {"kind": "tool", "name": "write", "active": 1},
                {"kind": "tool", "name": "read", "active": 1},
            ]
            assert _json(httpd, f"/api/bots/coder/groups/{second["conversation_id"]}/tasks")["calls"] == [
                {"kind": "tool", "name": "write", "active": 1},
            ]
            # Out of order: the write finish removes only the write call.
            tracker.observe_event("coder", "sp-a", TurnEvent(
                type="tool.finished", round=1, call_id="c1", name="write",
                ok=True, bot_id="coder"))
            assert _json(httpd, f"/api/bots/coder/groups/{first["conversation_id"]}/tasks")["calls"] == [
                {"kind": "tool", "name": "read", "active": 1},
            ]
            # A named finish with no match removes nothing.
            tracker.observe_event("coder", "sp-a", TurnEvent(
                type="tool.finished", round=1, call_id="c1", name="nope",
                ok=True, bot_id="coder"))
            assert _json(httpd, f"/api/bots/coder/groups/{first["conversation_id"]}/tasks")["calls"] == [
                {"kind": "tool", "name": "read", "active": 1},
            ]
            # The other window is untouched throughout.
            assert _json(httpd, f"/api/bots/coder/groups/{second["conversation_id"]}/tasks")["calls"] == [
                {"kind": "tool", "name": "write", "active": 1},
            ]
        finally:
            _stop(httpd, thread)
    finally:
        app.shutdown()


def test_bot_room_tasks_real_turn_wires_exact_conversation(tmp_path: Path) -> None:
    """A real in-flight turn is visible under its own conversation id only."""
    from thyca.bots.card import BotCard

    started = threading.Event()
    release = threading.Event()

    class Slow:
        async def chat(self, messages, tools=None):
            started.set()
            await asyncio.to_thread(release.wait)
            return ChatReply(content="late")

    app = _chat(tmp_path, Slow())
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Codes.", persona="You code."))
        # coder's own DM: the turn responder is coder itself.
        first = app.bot_dm("coder")["conversation_id"]
        errors: list[BaseException] = []

        def run_turn() -> None:
            try:
                app.turn(first, "hi")
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        worker = threading.Thread(target=run_turn)
        httpd, thread = _start(tmp_path, app)
        try:
            worker.start()
            assert started.wait(5)
            live = _json(httpd, "/api/bots/coder/dm/tasks")
            assert live["room_id"] == first
            assert live["state"] in ("thinking", "running")
            assert live["calls"] == []
        finally:
            release.set()
            worker.join(timeout=10)
            _stop(httpd, thread)
        assert not errors
    finally:
        app.shutdown()


def test_list_empty_without_and_with_chat(tmp_path: Path) -> None:
    httpd, thread = _start(tmp_path)
    try:
        try:
            urlopen(_url(httpd, "/api/conversations"), timeout=2)
        except HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("expected 404 without chat")
    finally:
        _stop(httpd, thread)

    httpd, thread = _start(tmp_path, _chat(tmp_path, FakeLLM(ChatReply(content="x"))))
    try:
        payload = _json(httpd, "/api/conversations")
        assert payload["conversations"] == []
        assert payload["model"]
    finally:
        _stop(httpd, thread)


def test_create_list_get_and_turn(tmp_path: Path) -> None:
    llm = FakeLLM(ChatReply(content="pong"))
    httpd, thread = _start(tmp_path, _chat(tmp_path, llm))
    try:
        created = _json(httpd, "/api/bots/thyca/dm")
        assert "title" not in created
        assert created["kind"] == "dm" and created["bot_id"] == "thyca"
        assert created["messages"] == []
        listed = _json(httpd, "/api/conversations")
        assert listed["conversations"] == []
        body = json.dumps({"text": "ping"}).encode("utf-8")
        turned = _json(httpd, "/api/bots/thyca/dm/turn", method="POST", data=body)
        assert turned["reply"] == "pong"
        assert "title" not in turned
        roles = [(item["role"], item["content"]) for item in turned["messages"]]
        assert roles == [("user", "ping"), ("assistant", "pong")]
        loaded = _json(httpd, "/api/bots/thyca/dm")
        assert loaded["messages"] == turned["messages"]
        assert "title" not in loaded
        spoken = _json(httpd, "/api/conversations")
        assert [item["bot_id"] for item in spoken["conversations"]] == [created["bot_id"]]
        assert all("title" not in item for item in spoken["conversations"])
        conversation = ConversationManager(tmp_path / "sessions").load(created["bot_id"])
        assert [(item.role, item.content) for item in conversation.messages] == [
            ("user", "ping"),
            ("assistant", "pong"),
        ]
        assert llm.requests[0][-1].content == "ping"
        # One turn makes one model call.
        assert len(llm.requests) == 1
    finally:
        _stop(httpd, thread)


def test_turn_errors(tmp_path: Path) -> None:
    class Boom:
        async def chat(self, messages, tools=None):
            raise LLMError("provider HTTP 401: denied [redacted]")

    httpd, thread = _start(tmp_path, _chat(tmp_path, Boom()))
    try:
        created = _json(httpd, "/api/bots/thyca/dm")
        try:
            urlopen(
                Request(
                    _url(httpd, "/api/bots/thyca/dm/turn"),
                    data=b'{"text":""}',
                    method="POST",
                    headers={"Content-Type": "application/json"},
                ),
                timeout=2,
            )
        except HTTPError as exc:
            assert exc.code == 400
        else:
            raise AssertionError("expected 400")
        try:
            urlopen(
                Request(
                    _url(httpd, "/api/conversations/2026-01-01T00-00-00_ffff/turn"),
                    data=b'{"text":"hi"}',
                    method="POST",
                    headers={"Content-Type": "application/json"},
                ),
                timeout=2,
            )
        except HTTPError as exc:
            assert exc.code == 404
            assert "ffff" not in exc.read().decode("utf-8")
        else:
            raise AssertionError("expected 404")
        try:
            urlopen(
                Request(
                    _url(httpd, "/api/bots/thyca/dm/turn"),
                    data=b'{"text":"hi"}',
                    method="POST",
                    headers={"Content-Type": "application/json"},
                ),
                timeout=2,
            )
        except HTTPError as exc:
            assert exc.code == 503
            body = json.loads(exc.read().decode("utf-8"))
            assert body == {"error": "provider HTTP 401: denied [redacted]"}
        else:
            raise AssertionError("expected 503")
        try:
            urlopen(
                Request(_url(httpd, "/api/memory/stats"), method="POST", data=b"{}"),
                timeout=2,
            )
        except HTTPError as exc:
            assert exc.code == 405
        else:
            raise AssertionError("expected 405")
        try:
            urlopen(_url(httpd, "/api/conversations/../pyproject.toml"), timeout=2)
        except HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("expected 404")
        # Store-door corruption reads as a constant 503, never as empty
        # (same pattern as test_bot_room_tasks_corrupt_conversation_is_unavailable_not_empty).
        broken = ConversationManager(tmp_path / "sessions").create()
        import sqlite3 as _sqlite3

        _conn = _sqlite3.connect(tmp_path / "conversations.db")
        try:
            _conn.execute(
                "UPDATE conversations SET metadata=? WHERE kind='group' AND id=?",
                ("{not valid json", broken.conversation_id),
            )
            _conn.commit()
        finally:
            _conn.close()
        try:
            urlopen(_url(httpd, f"/api/groups/{broken.conversation_id}"), timeout=2)
        except HTTPError as exc:
            assert exc.code == 503
            _body = exc.read().decode("utf-8")
            assert json.loads(_body) == {"error": "conversation unreadable"}
            assert broken.conversation_id not in _body and "conversations.db" not in _body
        else:
            raise AssertionError("expected 503")
        # The healthy DM door still reads normally (local scope only).
        healthy = _json(httpd, "/api/bots/thyca/dm")
        assert healthy["bot_id"] == "thyca"
    finally:
        _stop(httpd, thread)


def test_create_during_in_flight_turn(tmp_path: Path) -> None:
    """create() must not wait on the LLM of an in-flight turn."""
    started = threading.Event()
    release = threading.Event()

    class Slow:
        async def chat(self, messages, tools=None):
            started.set()
            await asyncio.to_thread(release.wait)
            return ChatReply(content="late")

    app = _chat(tmp_path, Slow())
    from thyca.bots.card import BotCard

    app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                             description="Codes.", persona="You code."))
    first = app.create()["conversation_id"]
    errors: list[BaseException] = []
    result: dict = {}
    created: dict = {}

    def run_turn() -> None:
        try:
            result.update(app.turn(first, "hi"))
        except BaseException as exc:
            errors.append(exc)

    def run_create() -> None:
        try:
            created.update(app.create())
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=run_turn)
    worker.start()
    assert started.wait(2)
    maker = threading.Thread(target=run_create)
    maker.start()
    maker.join(2)
    assert not maker.is_alive(), "create blocked by in-flight turn lock"
    # Phase 2B: the no-bots create is the same DM, so the concurrent
    # create opens a group instead — still blank and non-blocking.
    group = app.create(bots=["thyca", "coder"])
    assert group["conversation_id"] != first
    created.update(group)
    assert created["messages"] == []
    # Turn conversation must still receive the reply after create ran mid-flight.
    release.set()
    worker.join(timeout=5)
    assert not errors
    assert result["reply"] == "late"
    assert result["conversation_id"] == first
    stored = ConversationManager(tmp_path / "sessions").load(first)
    assert [(item.role, item.content) for item in stored.messages] == [
        ("user", "hi"),
        ("assistant", "late"),
    ]
    app.shutdown()


def test_chat_app_shutdown_idempotent(tmp_path: Path) -> None:
    app = _chat(tmp_path, FakeLLM(ChatReply(content="x")))
    created = app.create()
    payload = app.turn(created["conversation_id"], "hi")
    assert payload["reply"] == "x"
    app.shutdown()
    app.shutdown()


def test_wire_carries_identity_not_title(tmp_path: Path) -> None:
    from seeded_config import seeded_config

    from thyca.conversations.runtime.wire import conversation_detail, conversation_summary

    path = tmp_path / "2026-08-24T10-56-24_abcd.jsonl"
    path.write_text("", encoding="utf-8")
    empty = ConversationRuntime("2026-08-24T10-56-24_abcd", path, [])
    spoken = ConversationRuntime(
        "2026-08-24T10-56-24_abcd",
        path,
        [Message(role="user", content="alo", ts="2026-08-24T03:56:24Z")],
    )
    assert "title" not in conversation_summary(spoken)
    detail = conversation_detail(empty, seeded_config())
    assert "title" not in detail
    assert detail["messages"] == []


def test_turns_carry_no_title(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [
            ChatReply(content="pong"),
            ChatReply(content="again"),
        ]
    )
    app = _chat(tmp_path, llm)
    created = app.create()
    first = app.turn(created["conversation_id"], "alo")
    assert "title" not in first
    assert first["reply"] == "pong"
    second = app.turn(created["conversation_id"], "thêm")
    assert "title" not in second
    assert second["reply"] == "again"
    # Only the two turn calls.
    assert len(llm.requests) == 2
    listed = app.list_payload()
    assert "title" not in listed["conversations"][0]
    app.shutdown()


def _stream(httpd, path: str, *, data: bytes, timeout: float = 10):
    request = Request(
        _url(httpd, path),
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    return urlopen(request, timeout=timeout)


def _stream_lines(response) -> list[dict]:
    return [json.loads(raw.decode("utf-8")) for raw in response if raw]


def test_stream_slow_turn_first_line_arrives_before_release(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()

    class Slow:
        async def chat(self, messages, tools=None):
            started.set()
            await asyncio.to_thread(release.wait)
            return ChatReply(content="late")

    httpd, thread = _start(tmp_path, _chat(tmp_path, Slow()))
    try:
        created = _json(httpd, "/api/bots/thyca/dm")
        response = _stream(
            httpd,
            "/api/bots/thyca/dm/turn/stream",
            data=b'{"text":"hello stream"}',
        )
        first = json.loads(response.readline().decode("utf-8"))
        assert response.status == 200
        assert first == {"type": "turn.accepted", "bot_id": "thyca"}
        # Headers + first line went out while the Slow LLM is still blocked.
        assert release.is_set() is False
        release.set()
        lines = _stream_lines(response)
        types = [item["type"] for item in lines]
        assert types == [
            "llm.started",
            "llm.finished",
            "turn.completed",
        ]
        assert lines[0]["round"] == 1
        assert lines[1] == {"type": "llm.finished", "round": 1, "tool_count": 0, "bot_id": "thyca"}
        completed = lines[-1]
        assert completed["type"] == "turn.completed"
        detail = completed["detail"]
        assert detail["bot_id"] == created["bot_id"]
        assert detail["reply"] == "late"
        assert detail["model"]
        assert "title" not in detail
        assert [(item["role"], item["content"]) for item in detail["messages"]] == [
            ("user", "hello stream"),
            ("assistant", "late"),
        ]
    finally:
        _stop(httpd, thread)


def test_stream_disconnect_does_not_cancel_turn(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()

    class Slow:
        async def chat(self, messages, tools=None):
            started.set()
            await asyncio.to_thread(release.wait)
            return ChatReply(content="kept")

    httpd, thread = _start(tmp_path, _chat(tmp_path, Slow()))
    try:
        created = _json(httpd, "/api/bots/thyca/dm")
        response = _stream(
            httpd,
            "/api/bots/thyca/dm/turn/stream",
            data=b'{"text":"stay"}',
        )
        first = json.loads(response.readline().decode("utf-8"))
        assert first == {"type": "turn.accepted", "bot_id": "thyca"}
        response.close()
        release.set()
        stored = None
        deadline = time.time() + 5
        while time.time() < deadline:
            stored = ConversationManager(tmp_path / "sessions").load(created["bot_id"])
            if any(item.role == "assistant" and item.content == "kept" for item in stored.messages):
                break
            time.sleep(0.05)
        assert stored is not None
        assert [(item.role, item.content) for item in stored.messages[:2]] == [
            ("user", "stay"),
            ("assistant", "kept"),
        ]
    finally:
        _stop(httpd, thread)


def test_stream_pre_accept_errors_are_http_json(tmp_path: Path) -> None:
    httpd, thread = _start(tmp_path, _chat(tmp_path, FakeLLM(ChatReply(content="x"))))
    try:
        created = _json(httpd, "/api/bots/thyca/dm")
        try:
            _stream(
                httpd,
                "/api/bots/thyca/dm/turn/stream",
                data=b'{"text":""}',
            )
        except HTTPError as exc:
            assert exc.code == 400
            assert exc.headers.get_content_type() == "application/json"
            body = exc.read().decode("utf-8")
            assert json.loads(body) == {"error": "invalid text: empty"}
            assert "turn." not in body
        else:
            raise AssertionError("expected 400")
        try:
            _stream(
                httpd,
                "/api/bots/thyca/dm/turn/stream",
                data=b'{"text":5}',
            )
        except HTTPError as exc:
            assert exc.code == 400
            body = exc.read().decode("utf-8")
            assert json.loads(body) == {"error": "invalid text: invalid"}
        else:
            raise AssertionError("expected 400")
        try:
            _stream(
                httpd,
                f"/api/groups/group-{'0' * 32}/turn/stream",
                data=b'{"text":"hi"}',
            )
        except HTTPError as exc:
            assert exc.code == 404
            assert exc.headers.get_content_type() == "application/json"
            body = exc.read().decode("utf-8")
            assert json.loads(body) == {"error": "conversation not found"}
            assert "turn." not in body
        else:
            raise AssertionError("expected 404")
    finally:
        _stop(httpd, thread)


def test_stream_post_accept_llm_error_ends_with_single_failed(tmp_path: Path) -> None:
    class Boom:
        async def chat(self, messages, tools=None):
            raise LLMError("provider HTTP 401: denied [redacted]")

    httpd, thread = _start(tmp_path, _chat(tmp_path, Boom()))
    try:
        created = _json(httpd, "/api/bots/thyca/dm")
        response = _stream(
            httpd,
            "/api/bots/thyca/dm/turn/stream",
            data=b'{"text":"hi"}',
        )
        assert response.status == 200
        lines = _stream_lines(response)
        types = [item["type"] for item in lines]
        assert types[0] == "turn.accepted"
        assert "turn.completed" not in types
        failed = [item for item in lines if item["type"] == "turn.failed"]
        assert failed == [
            {
                "type": "turn.failed",
                "code": "llm_error",
                "message": "provider HTTP 401: denied [redacted]",
            }
        ]
        stored = ConversationManager(tmp_path / "sessions").load(created["bot_id"])
        # Hard-cut: failed turn leaves user + trailing system error marker
        # (append-only); the NDJSON single-failed-terminal contract above
        # is unchanged.
        assert [(item.role, item.content) for item in stored.messages] == [
            ("user", "hi"),
            ("system", "provider HTTP 401: denied [redacted]"),
        ]
    finally:
        _stop(httpd, thread)


def test_stream_headers_and_no_content_length(tmp_path: Path) -> None:
    httpd, thread = _start(tmp_path, _chat(tmp_path, FakeLLM(ChatReply(content="pong"))))
    try:
        created = _json(httpd, "/api/bots/thyca/dm")
        response = _stream(
            httpd,
            "/api/bots/thyca/dm/turn/stream",
            data=b'{"text":"ping"}',
        )
        assert response.status == 200
        assert response.headers.get_content_type() == "application/x-ndjson"
        cache = response.headers.get("Cache-Control", "")
        assert "no-store" in cache
        assert "no-transform" in cache
        assert response.headers.get("X-Content-Type-Options") == "nosniff"
        assert response.headers.get("Content-Length") is None
        lines = _stream_lines(response)
        assert lines[-1]["type"] == "turn.completed"
    finally:
        _stop(httpd, thread)


def test_stream_tool_events_ordered_and_clean(tmp_path: Path) -> None:
    call = ToolCall(
        id="call-1",
        name="bash",
        arguments={"cmd": "echo hi"},
        parse_error="invalid arguments",
    )
    llm = ScriptedLLM(
        [
            ChatReply(content=None, tool_calls=[call]),
            ChatReply(content="final answer"),
        ]
    )
    httpd, thread = _start(tmp_path, _chat(tmp_path, llm))
    try:
        created = _json(httpd, "/api/bots/thyca/dm")
        response = _stream(
            httpd,
            "/api/bots/thyca/dm/turn/stream",
            data=b'{"text":"run the bash tool now"}',
        )
        raws = list(response)
        lines = [json.loads(raw.decode("utf-8")) for raw in raws]
        assert [item["type"] for item in lines] == [
            "turn.accepted",
            "llm.started",
            "llm.finished",
            "tool.started",
            "tool.finished",
            "llm.started",
            "llm.finished",
            "turn.completed",
        ]
        assert lines[2] == {"type": "llm.finished", "round": 1, "tool_count": 1, "bot_id": "thyca"}
        invocation_id = lines[3]["invocation_id"]
        assert len(invocation_id) == 32 and all(c in "0123456789abcdef" for c in invocation_id)
        assert lines[3] == {
            "type": "tool.started",
            "round": 1,
            "call_id": "call-1",
            "invocation_id": invocation_id,
            "name": "bash",
            "bot_id": "thyca",
        }
        assert lines[4] == {
            "type": "tool.finished",
            "round": 1,
            "call_id": "call-1",
            "invocation_id": invocation_id,
            "name": "bash",
            "ok": False,
            "bot_id": "thyca",
        }
        # Intermediate lines never carry user text, tool args, results, or stacks.
        blob = b"".join(raws[:-1]).decode("utf-8")
        assert "run the bash tool now" not in blob
        assert "echo hi" not in blob
        assert "invalid arguments" not in blob
        assert "Traceback" not in blob
    finally:
        _stop(httpd, thread)


def test_stream_sentinel_without_terminal_writes_fallback_failure(
    tmp_path: Path, monkeypatch
) -> None:
    httpd, thread = _start(tmp_path, _chat(tmp_path, FakeLLM(ChatReply(content="pong"))))
    try:
        created = _json(httpd, "/api/bots/thyca/dm")

        def sentinel_only_worker(app, conversation_id, text, items, state, **_kwargs):
            items.put(TurnEvent(type="turn.accepted"))
            items.put(SENTINEL)

        monkeypatch.setattr("thyca.serve.bridge.bridge_worker", sentinel_only_worker)
        response = _stream(
            httpd,
            "/api/bots/thyca/dm/turn/stream",
            data=b'{"text":"hi"}',
        )
        assert response.status == 200
        lines = _stream_lines(response)
        assert [item["type"] for item in lines] == ["turn.accepted", "turn.failed"]
        assert lines[-1] == {
            "type": "turn.failed",
            "code": "chat_unavailable",
            "message": "chat unavailable",
        }
    finally:
        _stop(httpd, thread)



def test_chat_nav_opens_new_conversation() -> None:
    """The + button only opens the group picker — a conversation is POSTed when the
    dialog submits, and DM conversations are find-or-create through the bot."""
    sidebar = (WEBUI / "ui" / "share" / "sidebar.js").read_text(encoding="utf-8")
    chat = (WEBUI / "ui" / "chat" / "js" / "chat.js").read_text(encoding="utf-8")
    html = (WEBUI / "ui" / "chat" / "index.html").read_text(encoding="utf-8")

    assert 'id="team-shared-create"' in html
    assert 'id="group-dialog"' in html
    assert 'id="message-list"' in html
    assert 'id="composer"' in html

    # Clicking + fills and shows the dialog — it must not hit the network.
    # The button only exists in chat's static markup, so the shared sidebar
    # guards before binding it.
    assert "const teamSharedCreate=$('#team-shared-create');" in sidebar
    open_body = sidebar[sidebar.index("teamSharedCreate.onclick"):]
    open_body = open_body[: open_body.index("\n")]
    assert "$('#group-dialog').showModal()" in open_body
    assert "getJson" not in open_body
    # Submitting the form is what creates the conversation, then opens it.
    form_body = sidebar[sidebar.index("$('#group-form').onsubmit"):]
    assert "'/api/groups'" in form_body
    assert "method:'POST'" in form_body
    assert "loadConversation(groupKey(detail.group_id)" in form_body
    # Picking a bot goes through the find-or-create DM, not a blank create.
    assert "/api/bots/${encodeURIComponent(id)}/dm" in chat

    provider = (WEBUI / "ui" / "provider" / "js" / "provider.js").read_text(encoding="utf-8")
    assert "getJson('/api/config')" in provider
    assert "postJson('/api/config'" in provider
    assert "postJson('/api/onboarding/verify'" in provider
    assert "contextTokens" in provider


def test_create_prunes_previous_blank(tmp_path: Path) -> None:
    app = _chat(tmp_path, FakeLLM(ChatReply(content="x")))
    from thyca.bots.card import BotCard

    app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                             description="Codes.", persona="You code."))
    first = app.create()
    assert app.list_payload()["conversations"] == []
    # Phase 2B: repeated creates return the same DM singleton; a fresh
    # group still prunes the previous blank group.
    assert app.create()["conversation_id"] == first["conversation_id"]
    g1 = app.create(bots=["thyca", "coder"])
    g2 = app.create(bots=["thyca", "coder"])
    assert g1["conversation_id"] != g2["conversation_id"]
    # Hard cut: existence is the store door, never a JSONL file.
    from thyca.conversations.store.admin import AdminStore

    door_ids = {row.ref.id for row in AdminStore(tmp_path).list(limit=500)[0]}
    assert g1["conversation_id"] not in door_ids
    assert g2["conversation_id"] in door_ids
    assert first["conversation_id"] in door_ids
    app.shutdown()


def test_conversation_payload_includes_ask_remember(tmp_path: Path) -> None:
    app = _chat(tmp_path, FakeLLM(ChatReply(content="x")))
    created = app.create()
    assert created["ask_remember"] is False
    turned = app.turn(created["conversation_id"], "hi")
    assert turned["ask_remember"] is False
    loaded = app.get_payload(created["conversation_id"])
    assert loaded["ask_remember"] is False
    app.shutdown()


def test_conversation_payload_includes_context(tmp_path: Path) -> None:
    app = _chat(tmp_path, FakeLLM(ChatReply(content="x")))
    try:
        created = app.create()
        for payload in (created, app.turn(created["conversation_id"], "hi"), app.get_payload(created["conversation_id"])):
            ctx = payload["context"]
            assert ctx["estimated_tokens"] == ctx["transcript_tokens"] + ctx["overhead_tokens"]
            assert ctx["context_tokens"] > 0
            assert abs(ctx["ratio"] - ctx["estimated_tokens"] / ctx["context_tokens"]) < 1e-9
            # Hot prompt + tools schema are real turn overhead, not zero.
            assert ctx["overhead_tokens"] > 0
    finally:
        app.shutdown()


def _chat_small_context(tmp_path: Path) -> ChatApp:
    cfg = seeded_config()
    cfg = replace(cfg, limits=replace(cfg.limits, contextTokens=1000))
    save(cfg, tmp_path / "config.json")
    return ChatApp(tmp_path, load(tmp_path / "config.json"), connect=FakeLLM(ChatReply(content="x")))


def _seed_long_conversation(tmp_path: Path, conversation_id: str) -> None:
    manager = ConversationManager(tmp_path / "sessions")
    manager.load(conversation_id)
    for i in range(4):
        manager.append(Message(role="user", content="u" * 300 + str(i)))
        manager.append(Message(role="assistant", content="a" * 300))


def test_compact_conversation_skips_absent_canonical_context(tmp_path: Path) -> None:
    app = _chat_small_context(tmp_path)
    try:
        created = app.create()
        _seed_long_conversation(tmp_path, created["conversation_id"])
        before = app.get_payload(created["conversation_id"])
        result = app.compact_conversation(created["conversation_id"], instructions="keep the goal")
        assert result["compaction"]["compacted"] is False
        assert result["compaction"]["per_bot"]["thyca"] == {
            "compacted": False,
            "skipped": "no_existing_context",
        }
        assert result["messages"] == before["messages"]
        assert app._runtime.workspaces.store.get_binding(
            "thyca", created["conversation_id"]
        ) is None
    finally:
        app.shutdown()


def test_compact_conversation_refuses_mid_turn(tmp_path: Path) -> None:
    app = _chat(tmp_path, FakeLLM(ChatReply(content="x")))
    try:
        created = app.create()
        app._turns.claim(created["conversation_id"])
        try:
            app.compact_conversation(created["conversation_id"])
        except ConversationBusy:
            pass
        else:
            raise AssertionError("expected ConversationBusy")
        finally:
            app._turns.release(created["conversation_id"])
    finally:
        app.shutdown()


def _seed_canonical_long_conversation(app: ChatApp, conversation_id: str) -> int:
    room = app._conversations.store.load(conversation_id)
    window = app._webui.window_for(room)
    binding = app._runtime.workspaces.store.bind_window("thyca", window)
    store = app._runtime._canonical_store(
        "thyca", window, binding.internal_conversation_id
    )
    manager = ConversationManager(store=store)
    manager.load(binding.internal_conversation_id)
    for i in range(4):
        manager.append(Message(role="user", content="canonical user " + "u" * 300 + str(i)))
        manager.append(Message(role="assistant", content="canonical reply " + "a" * 300))
    return len(manager.current.messages)


def test_compact_over_http(tmp_path: Path) -> None:
    app = _chat_small_context(tmp_path)
    httpd, thread = _start(tmp_path, app)
    try:
        created = _json(httpd, "/api/bots/thyca/dm")
        _seed_long_conversation(tmp_path, created["bot_id"])
        public_before = app.get_payload(created["bot_id"])["messages"]
        canonical_before = _seed_canonical_long_conversation(app, created["bot_id"])
        compacted = _json(
            httpd, "/api/bots/thyca/dm/compact", method="POST", data=b"{}"
        )
        assert compacted["compaction"]["per_bot"]["thyca"]["compacted"] is True
        assert compacted["messages"] == public_before
        binding = app._runtime.workspaces.store.get_binding("thyca", created["bot_id"])
        canonical = app._runtime._canonical_store(
            "thyca", app._webui.window_for(app._conversations.store.load(created["bot_id"])),
            binding.internal_conversation_id,
        ).load(binding.internal_conversation_id)
        assert canonical_before > len(canonical.messages)
        assert "context" in compacted
        try:
            urlopen(
                Request(
                    _url(httpd, "/api/conversations/2026-01-01T00-00-00_ffff/compact"),
                    data=b"{}", method="POST", headers={"Content-Type": "application/json"},
                ), timeout=2,
            )
        except HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("expected 404")
        try:
            urlopen(
                Request(
                    _url(httpd, "/api/bots/thyca/dm/compact"),
                    data=b'{"model": "nope-not-registered"}', method="POST",
                    headers={"Content-Type": "application/json"},
                ), timeout=2,
            )
        except HTTPError as exc:
            assert exc.code == 400
        else:
            raise AssertionError("expected 400")
        try:
            urlopen(
                Request(
                    _url(httpd, "/api/bots/thyca/dm/compact"),
                    data=json.dumps({"instructions": "x" * 501}).encode("utf-8"),
                    method="POST", headers={"Content-Type": "application/json"},
                ), timeout=2,
            )
        except HTTPError as exc:
            assert exc.code == 400
            assert json.loads(exc.read().decode("utf-8")) == {"error": "invalid instructions"}
        else:
            raise AssertionError("expected 400")
    finally:
        _stop(httpd, thread)
        app.shutdown()


def test_chat_mobile_pick_pattern() -> None:
    """On mobile the bot/conversation list is an off-canvas overlay, not a separate
    pick screen: `.mobile-open` slides it in over the content, the backdrop
    or Esc closes it, and loading a conversation closes it too — the same shared
    wiring every page gets from share/shell-app.js. The header's
    .menu-button is the mobile-only trigger (hidden on desktop)."""
    html = (WEBUI / "ui" / "chat" / "index.html").read_text(encoding="utf-8")
    chat = (WEBUI / "ui" / "chat" / "js" / "chat.js").read_text(encoding="utf-8")
    shell_app = (WEBUI / "ui" / "share" / "shell-app.js").read_text(encoding="utf-8")
    sidebar_css = (WEBUI / "ui" / "share" / "sidebar.css").read_text(encoding="utf-8")
    base_css = (WEBUI / "ui" / "share" / "base.css").read_text(encoding="utf-8")

    assert 'class="sidebar" id="chat-sidebar"' in html
    assert 'id="mobile-overlay"' in html
    assert 'class="icon-button menu-button"' in html
    assert 'aria-controls="chat-sidebar"' in html
    # Hidden on desktop, revealed under the 850px breakpoint.
    assert ".menu-button{display:none}" in base_css
    assert ".menu-button{display:inline-flex}" in base_css
    assert "screen-shell" not in html
    # The pick-shell two-pane pattern is gone: under 850px the sidebar sits
    # translated off-canvas until .mobile-open slides it over the content.
    assert "max-width:850px" in sidebar_css
    assert ".sidebar.mobile-open" in sidebar_css
    assert "function openMobile" in shell_app
    assert "function closeMobile" in shell_app
    assert "mobile-open" in shell_app
    assert "$('#mobile-overlay').onclick = closeMobile" in shell_app
    # The menu button toggles the overlay on mobile; the wiring uses optional
    # chaining so it stays safe if a page ever drops the element.
    assert "$('.menu-button')" in shell_app
    assert "$('.menu-button')?.setAttribute" in shell_app
    assert "matchMedia('(max-width:850px)')" in shell_app
    # Opening a conversation (group row tap, deep link, drawer row) reveals the
    # conversation by closing the overlay.
    load_conversation = chat[chat.index("async function loadConversation") : chat.index("async function selectBot")]
    assert "closeMobile()" in load_conversation
    assert "pick-shell" not in html + chat + shell_app
    assert "is-picking" not in html + chat + shell_app



def test_retry_hook_emits_llm_retry_events(tmp_path: Path) -> None:
    """ChatApp wires OpenAIChat-style retry hooks to non-error TurnEvents."""

    class Recovering:
        def __init__(self) -> None:
            self._hook = None
            self.n = 0

        def set_retry_hook(self, hook) -> None:
            self._hook = hook

        async def chat(self, messages, tools=None):
            self.n += 1
            # Only the first (turn) call simulates a transient retry notify.
            if self.n == 1 and self._hook:
                self._hook(1, 3)
            return ChatReply(content="ok" if self.n == 1 else '"Tên phiên."')

    app = _chat(tmp_path, Recovering())
    created = app.create()
    events: list[TurnEvent] = []
    payload = app.turn(created["conversation_id"], "ping", event_sink=events.append)
    assert payload["reply"] == "ok"
    retries = [e for e in events if e.type == "llm.retry"]
    assert retries == [TurnEvent(type="llm.retry", attempt=1, max_attempts=3, bot_id="thyca")]
    app.shutdown()


def test_aborted_static_and_body_keep_server_quiet(tmp_path: Path, capsys) -> None:
    """Client ngắt mid-response/mid-body (đổi Trace↔Chat): im lặng, server sống."""
    import socket

    httpd, thread = _start(tmp_path, _chat(tmp_path, FakeLLM(ChatReply(content="x"))))
    try:
        port = httpd.server_address[1]
        # 1. GET đóng ngay sau request: wfile.flush() cuối handle_one_request
        #    dâng BrokenPipe lên handle_error (không qua _send).
        sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        try:
            sock.sendall(b"GET /api/conversations HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        finally:
            sock.close()
        # 2. POST khai Content-Length lớn rồi đóng giữa body.
        sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        try:
            sock.sendall(
                b"POST /api/conversations HTTP/1.1\r\nHost: x\r\n"
                b"Content-Length: 5000\r\nContent-Type: application/json\r\n"
                b"Connection: close\r\n\r\n{\"a\":"
            )
        finally:
            sock.close()
        # 3. Static file đóng ngay sau request.
        sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        try:
            sock.sendall(b"GET / HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        finally:
            sock.close()
        time.sleep(0.5)
        assert thread.is_alive()
        payload = _json(httpd, "/api/conversations")
        assert payload["conversations"] == []
        captured = capsys.readouterr()
        assert "Traceback" not in captured.err
        assert "BrokenPipeError" not in captured.err
    finally:
        _stop(httpd, thread)


def test_conversation_detail_tags_skill_loads(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    skill_md = skills_root / "create-skill"
    skill_md.mkdir(parents=True)
    skill_path = skill_md / "SKILL.md"
    skill_path.write_text("---\nname: create-skill\n---\nskill body\n", encoding="utf-8")
    notes_path = tmp_path / "notes.md"

    class ThinkingToolLLM:
        def __init__(self) -> None:
            self.round = 0

        async def chat(self, messages, tools=None, on_reasoning=None):
            self.round += 1
            thought = "I will read the skill" if self.round == 1 else "The skill is loaded"
            if on_reasoning:
                on_reasoning(thought)
            if self.round == 1:
                return ChatReply(
                    content=None,
                    reasoning=thought,
                    tool_calls=[
                        ToolCall(
                            id="call-1", name="read",
                            arguments={"path": str(skill_path)},
                        ),
                        ToolCall(
                            id="call-2", name="read",
                            arguments={"path": str(notes_path)},
                        ),
                    ],
                )
            return ChatReply(content="xong", reasoning=thought)

    httpd, thread = _start(tmp_path, _chat(tmp_path, ThinkingToolLLM()))
    try:
        created = _json(httpd, "/api/bots/thyca/dm")
        body = json.dumps({"text": "viết skill"}).encode("utf-8")
        stream = _stream(
            httpd, "/api/bots/thyca/dm/turn/stream", data=body
        )
        events = _stream_lines(stream)
        assert any(event["type"] == "llm.thinking" for event in events)

        detail = _json(httpd, "/api/bots/thyca/dm")
        assert all("tool_calls" not in item for item in detail["messages"])
        assert all("reasoning" not in item for item in detail["messages"])
        assert [item["content"] for item in detail["messages"] if item["role"] == "assistant"] == ["xong"]

        trace = _json(httpd, "/api/traces/dm/thyca/0")
        calls = next(item["tool_calls"] for item in trace["messages"] if item.get("tool_calls"))
        assert calls[0] == {
            "id": "call-1", "name": "read",
            "arguments": {"path": str(skill_path)}, "skill": "create-skill",
        }
        assert calls[1] == {
            "id": "call-2", "name": "read",
            "arguments": {"path": str(notes_path)},
        }
        assistant_call = next(
            item for item in trace["messages"]
            if item["role"] == "assistant" and item.get("tool_calls")
        )
        assert assistant_call["reasoning"] == "I will read the skill"
        tool_rows = [item for item in trace["messages"] if item["role"] == "tool"]
        assert {item["tool_call_id"] for item in tool_rows} == {"call-1", "call-2"}
        assert all(item["content"] for item in tool_rows)
        assert all(
            item["meta"]["span_id"] == assistant_call["meta"]["span_id"]
            for item in tool_rows
        )
        final = next(
            item for item in trace["messages"]
            if item["role"] == "assistant" and item.get("content") == "xong"
        )
        assert final["reasoning"] == "The skill is loaded"
    finally:
        _stop(httpd, thread)



def test_empty_submit_guard_returns_before_send() -> None:
    """Pin the empty/busy submit guard: the composer onsubmit handler
    returns before sendTurn — nothing hits the network when the text is
    blank or a turn is already running. The behavior itself executes in
    test_team_frontend.py::test_composer_submit_routes_mention_and_resets."""
    app = (WEBUI / "ui" / "chat" / "js" / "app.js").read_text(encoding="utf-8")
    submit = app[app.index("$('#composer').onsubmit"):]
    guard = submit[: submit.index("sendTurn(")]
    assert "if (!text || runs.has(currentConversation)) return;" in guard
    assert "fetch(" not in guard


def test_busy_conversation_reports_running_and_refuses_a_second_turn(tmp_path: Path) -> None:
    """A turn in flight is visible to the UI and refuses a second one."""
    release = threading.Event()

    class Slow:
        async def chat(self, messages, tools=None):
            await asyncio.to_thread(release.wait)
            return ChatReply(content="late")

    httpd, thread = _start(tmp_path, _chat(tmp_path, Slow()))
    try:
        created = _json(httpd, "/api/bots/thyca/dm")
        conversation_id = created["bot_id"]
        assert _json(httpd, "/api/bots/thyca/dm")["running"] is False
        response = _stream(
            httpd,
            "/api/bots/thyca/dm/turn/stream",
            data=b'{"text":"first"}',
        )
        assert json.loads(response.readline().decode("utf-8")) == {"type": "turn.accepted", "bot_id": "thyca"}
        try:
            # The reloaded page reads exactly this to know a turn is in flight.
            detail = _json(httpd, "/api/bots/thyca/dm")
            assert detail["running"] is True
            assert detail["started_at"]
            request = Request(
                _url(httpd, "/api/bots/thyca/dm/turn"),
                data=b'{"text":"second"}',
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            try:
                urlopen(request, timeout=5)
            except HTTPError as exc:
                assert exc.code == 409
                assert json.loads(exc.read().decode("utf-8")) == {"error": "conversation busy"}
            else:
                raise AssertionError("expected 409")
        finally:
            response.close()
            release.set()
        deadline = time.time() + 5
        while time.time() < deadline:
            if _json(httpd, "/api/bots/thyca/dm")["running"] is False:
                break
            time.sleep(0.05)
        assert _json(httpd, "/api/bots/thyca/dm")["running"] is False
    finally:
        _stop(httpd, thread)


def test_follow_stream_replays_and_tails_a_running_turn(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()

    class Slow:
        async def chat(self, messages, tools=None):
            started.set()
            await asyncio.to_thread(release.wait)
            return ChatReply(content="late")

    httpd, thread = _start(tmp_path, _chat(tmp_path, Slow()))
    try:
        created = _json(httpd, "/api/bots/thyca/dm")
        conversation_id = created["bot_id"]
        starter = _stream(
            httpd,
            "/api/bots/thyca/dm/turn/stream",
            data=b'{"text":"hello follow"}',
        )
        assert json.loads(starter.readline().decode("utf-8")) == {"type": "turn.accepted", "bot_id": "thyca"}
        assert started.wait(2)
        follower = urlopen(
            Request(
                _url(httpd, "/api/bots/thyca/dm/turn/stream"),
                method="GET",
            ),
            timeout=10,
        )
        assert follower.status == 200
        assert follower.headers.get_content_type() == "application/x-ndjson"
        assert json.loads(follower.readline().decode("utf-8")) == {"type": "turn.accepted", "bot_id": "thyca"}
        release.set()
        starter_types = [item["type"] for item in _stream_lines(starter)]
        follower_lines = _stream_lines(follower)
        follower_types = [item["type"] for item in follower_lines]
        assert starter_types[-1] == "turn.completed"
        assert follower_types[-1] == "turn.completed"
        assert "llm.started" in follower_types
        assert follower_lines[-1]["detail"]["reply"] == "late"
    finally:
        _stop(httpd, thread)


def test_stream_emits_thinking_deltas_and_persists(tmp_path: Path) -> None:
    class ThinkingLLM:
        async def chat(self, messages, tools=None, on_reasoning=None):
            if on_reasoning:
                on_reasoning("First I check")
                on_reasoning(" the chords")
            return ChatReply(content="yes", reasoning="First I check the chords")

    httpd, thread = _start(tmp_path, _chat(tmp_path, ThinkingLLM()))
    try:
        created = _json(httpd, "/api/bots/thyca/dm")
        response = _stream(
            httpd,
            "/api/bots/thyca/dm/turn/stream",
            data=b'{"text":"kalimba"}',
        )
        lines = _stream_lines(response)
        types = [item["type"] for item in lines]
        thinking = [item for item in lines if item["type"] == "llm.thinking"]
        assert types[0] == "turn.accepted"
        assert types[1] == "llm.started"
        assert thinking == [
            {"type": "llm.thinking", "round": 1, "delta": "First I check", "bot_id": "thyca"},
            {"type": "llm.thinking", "round": 1, "delta": " the chords", "bot_id": "thyca"},
        ]
        assert "llm.finished" in types
        assert types[-1] == "turn.completed"
        detail = _json(httpd, "/api/bots/thyca/dm")
        assistant = next(
            item for item in detail["messages"]
            if item["role"] == "assistant" and item.get("content") == "yes"
        )
        assert assistant["content"] == "yes"
        assert "reasoning" not in assistant
        trace = _json(httpd, "/api/traces/dm/thyca/0")
        execution = next(
            item for item in trace["messages"]
            if item["role"] == "assistant" and item.get("content") == "yes"
        )
        assert execution["reasoning"] == "First I check the chords"
    finally:
        _stop(httpd, thread)


def test_stream_emits_content_deltas_alongside_thinking(tmp_path: Path) -> None:
    class StreamingLLM:
        async def chat(self, messages, tools=None, on_reasoning=None, on_content=None):
            if on_reasoning:
                on_reasoning("I will greet")
            if on_content:
                on_content("Xin ")
                on_content("chào")
            return ChatReply(content="Xin chào", reasoning="I will greet")

    httpd, thread = _start(tmp_path, _chat(tmp_path, StreamingLLM()))
    try:
        created = _json(httpd, "/api/bots/thyca/dm")
        response = _stream(
            httpd,
            "/api/bots/thyca/dm/turn/stream",
            data=b'{"text":"kalimba"}',
        )
        lines = _stream_lines(response)
        content = [item for item in lines if item["type"] == "llm.content"]
        assert content == [
            {"type": "llm.content", "round": 1, "delta": "Xin ", "bot_id": "thyca"},
            {"type": "llm.content", "round": 1, "delta": "chào", "bot_id": "thyca"},
        ]
        assert any(item["type"] == "llm.thinking" for item in lines)
        assert lines[-1]["type"] == "turn.completed"
    finally:
        _stop(httpd, thread)


def test_follow_stream_idle_or_missing(tmp_path: Path) -> None:
    httpd, thread = _start(tmp_path, _chat(tmp_path, FakeLLM(ChatReply(content="x"))))
    try:
        created = _json(httpd, "/api/bots/thyca/dm")
        try:
            urlopen(
                Request(
                    _url(httpd, "/api/bots/thyca/dm/turn/stream"),
                    method="GET",
                ),
                timeout=5,
            )
        except HTTPError as exc:
            assert exc.code == 409
            assert json.loads(exc.read().decode("utf-8")) == {"error": "conversation idle"}
        else:
            raise AssertionError("expected 409")
        try:
            urlopen(
                Request(
                    _url(httpd, f"/api/groups/group-{'0' * 32}/turn/stream"),
                    method="GET",
                ),
                timeout=5,
            )
        except HTTPError as exc:
            assert exc.code == 404
            assert json.loads(exc.read().decode("utf-8")) == {"error": "conversation not found"}
        else:
            raise AssertionError("expected 404")
    finally:
        _stop(httpd, thread)


def test_follow_stream_survives_starter_disconnect(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()

    class Slow:
        async def chat(self, messages, tools=None):
            started.set()
            await asyncio.to_thread(release.wait)
            return ChatReply(content="kept")

    httpd, thread = _start(tmp_path, _chat(tmp_path, Slow()))
    try:
        created = _json(httpd, "/api/bots/thyca/dm")
        conversation_id = created["bot_id"]
        starter = _stream(
            httpd,
            "/api/bots/thyca/dm/turn/stream",
            data=b'{"text":"stay"}',
        )
        assert json.loads(starter.readline().decode("utf-8")) == {"type": "turn.accepted", "bot_id": "thyca"}
        assert started.wait(2)
        follower = urlopen(
            Request(
                _url(httpd, "/api/bots/thyca/dm/turn/stream"),
                method="GET",
            ),
            timeout=10,
        )
        assert json.loads(follower.readline().decode("utf-8")) == {"type": "turn.accepted", "bot_id": "thyca"}
        starter.close()
        release.set()
        lines = _stream_lines(follower)
        assert lines[-1]["type"] == "turn.completed"
        assert lines[-1]["detail"]["reply"] == "kept"
    finally:
        _stop(httpd, thread)




def test_conversation_summary_counts_turns_the_way_trace_does(tmp_path: Path) -> None:
    """One turn per user message — not the raw message count.

    Tool rounds inflate message_count (every assistant round and tool result
    is a line), which would read as "5 lượt" for a single question.
    """
    llm = FakeLLM(ChatReply(content="pong"))
    app = _chat(tmp_path, llm)
    try:
        created = app.create()
        app.turn(created["conversation_id"], "ping")
        app.turn(created["conversation_id"], "pong?")
        listed = app.list_payload()["conversations"]
        assert [item["turns"] for item in listed] == [2]
        assert listed[0]["message_count"] == 4
    finally:
        app.shutdown()


def test_delete_over_http_and_no_patch_route(tmp_path: Path) -> None:
    from thyca.bots.card import BotCard

    llm = FakeLLM(ChatReply(content="pong"))
    app = _chat(tmp_path, llm)
    app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                             description="Codes.", persona="You code."))
    httpd, thread = _start(tmp_path, app)
    try:
        created = _json(
            httpd, "/api/groups", method="POST",
            data=b'{"bots":["thyca","coder"]}',
        )
        assert "title" not in created
        sid = created["group_id"]
        body = json.dumps({"text": "ping"}).encode("utf-8")
        _json(httpd, f"/api/groups/{sid}/turn", method="POST", data=body)

        detail = _json(httpd, f"/api/groups/{sid}")
        assert "title" not in detail
        listed = _json(httpd, "/api/conversations")
        assert "title" not in listed["conversations"][0]

        # The stored conversation carries participants only (read back
        # through the runtime-aware load).
        stored = ConversationManager(tmp_path / "sessions").load(sid)
        assert stored.bots == ["thyca", "coder"]
        assert [(item.role, item.content) for item in stored.messages] == [
            ("user", "ping"),
            ("assistant", "pong"),
        ]

        # There is no rename route any longer: PATCH is unsupported.
        try:
            _json(httpd, f"/api/groups/{sid}", method="PATCH", data=b'{"x": 1}')
        except HTTPError as exc:
            assert exc.code == 501
        else:
            raise AssertionError("expected 501 for PATCH")

        deleted = _json(httpd, f"/api/groups/{sid}", method="DELETE")
        assert deleted == {"ok": True, "kind": "group", "group_id": sid}
        # Hard cut: existence is the store door, never a JSONL file.
        from thyca.conversations.store.admin import AdminStore

        assert sid not in {row.ref.id for row in AdminStore(tmp_path).list(limit=500)[0]}
        try:
            urlopen(_url(httpd, f"/api/groups/{sid}"), timeout=2)
        except HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("expected 404 after delete")
        # The conversation is gone; this test's DM was never created.
        assert _json(httpd, "/api/conversations")["conversations"] == []

        # Dropping a group is idempotent: a missing door is already the
        # wanted end state. A malformed id is still refused, and every
        # legacy conversation path stays 404.
        assert _json(httpd, f"/api/groups/{sid}", method="DELETE")["ok"] is True
        try:
            _json(
                httpd,
                f"/api/groups/{sid}",
                method="PATCH",
                data=b'{"x": 1}',
            )
        except HTTPError as exc:
            assert exc.code == 501
        else:
            raise AssertionError("expected 501 for PATCH on a missing group")
        for method, expected in (("PATCH", 501), ("DELETE", 404)):
            try:
                _json(httpd, "/api/conversations/not-an-id", method=method)
            except HTTPError as exc:
                assert exc.code == expected
                if expected == 404:
                    assert json.loads(exc.read().decode("utf-8")) == {
                        "error": "conversation not found"
                    }
            else:
                raise AssertionError("expected 501/404 for a legacy conversation path")
    finally:
        _stop(httpd, thread)
        app.shutdown()


def test_delete_refuses_a_conversation_mid_turn(tmp_path: Path) -> None:
    """A running turn is still appending: deleting under it is a 409."""
    started = threading.Event()
    release = threading.Event()

    class Slow:
        async def chat(self, messages, tools=None):
            started.set()
            await asyncio.to_thread(release.wait)
            return ChatReply(content="late")

    app = _chat(tmp_path, Slow())
    from thyca.bots.card import BotCard

    app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                             description="Codes.", persona="You code."))
    httpd, thread = _start(tmp_path, app)
    try:
        created = app.create(bots=["thyca", "coder"])
        sid = created["conversation_id"]

        worker = threading.Thread(target=lambda: app.turn(sid, "ping"), daemon=True)
        worker.start()
        assert started.wait(timeout=5)
        try:
            _json(httpd, f"/api/groups/{sid}", method="DELETE")
        except HTTPError as exc:
            assert exc.code == 409
        else:
            raise AssertionError("expected 409 for a running conversation")
        # Hard-cut: store-backed conversations have no JSONL file; existence is
        # the store door, not the filesystem.
        assert ConversationManager(tmp_path / "sessions").load(sid).conversation_id == sid
        release.set()
        worker.join(timeout=5)
        assert not worker.is_alive()
        assert ConversationManager(tmp_path / "sessions").load(sid).conversation_id == sid
        # Once the turn has landed the same call goes through.
        assert _json(httpd, f"/api/groups/{sid}", method="DELETE")["ok"] is True
    finally:
        release.set()
        _stop(httpd, thread)
        app.shutdown()



def test_membership_edit_midturn_persists(tmp_path: Path) -> None:
    """A participant edit during a turn survives; no naming call runs.

    The turn holds its own ConversationRuntime snapshot from load time; the sidebar
    edits membership while the turn runs. The edit persists, and the turn
    makes exactly one model call.
    """
    started = threading.Event()
    release = threading.Event()
    calls = {"n": 0}

    class Slow:
        async def chat(self, messages, tools=None):
            calls["n"] += 1
            if calls["n"] == 1:
                started.set()
                await asyncio.to_thread(release.wait)
            return ChatReply(content="pong")

    app = _chat(tmp_path, Slow())
    try:
        created = app.create()
        sid = created["conversation_id"]
        worker = threading.Thread(target=lambda: app.turn(sid, "ping"), daemon=True)
        worker.start()
        assert started.wait(timeout=5)

        # The sidebar re-affirms membership while the turn is still running.
        store = ConversationManager(tmp_path / "sessions").store
        store.append_bots(store.path_for(sid), ["thyca"])
        release.set()
        worker.join(timeout=5)
        assert not worker.is_alive()

        assert calls["n"] == 1
        stored = ConversationManager(tmp_path / "sessions").load(sid)
        assert stored.bots == ["thyca"]
        assert "title" not in app.get_payload(sid)
    finally:
        release.set()
        app.shutdown()


def test_delete_racing_a_claim_does_not_lose_the_transcript(tmp_path: Path) -> None:
    """The delete gate and the claim share one lock.

    Before: ``delete_conversation`` snapshotted the in-flight map and then unlinked,
    so a turn claiming in that window was not protected — the file went away
    under it and its next append re-created it truncated, dropping history.
    """
    llm = FakeLLM(ChatReply(content="pong"))
    app = _chat(tmp_path, llm)
    try:
        created = app.create()
        sid = created["conversation_id"]
        app.turn(sid, "câu hỏi đầu")
        # Hard cut: existence is the store door, never a JSONL file.
        from thyca.conversations.store.admin import AdminStore

        def door_exists() -> bool:
            return sid in {row.ref.id for row in AdminStore(tmp_path).list(limit=500)[0]}

        assert door_exists()

        # Hold a claim exactly as a live turn does, then ask the delete to run.
        app._turns.claim(sid)
        try:
            app.delete_conversation(sid)
        except ConversationBusy:
            pass
        else:
            raise AssertionError("delete must refuse a claimed conversation")
        assert door_exists()
        app._turns.release(sid)

        # Free again: the same call drops the notebook.
        app.delete_conversation(sid)
        assert not door_exists()
    finally:
        app.shutdown()


def test_group_delete_http_api_remains_wired_without_rename() -> None:
    """Removing chat UI controls does not remove the group delete API."""
    root = Path(__file__).resolve().parents[1]
    routes = (root / "thyca" / "serve" / "routes.py").read_text(encoding="utf-8")
    api = (root / "thyca" / "serve" / "conversations_api.py").read_text(encoding="utf-8")
    assert "group_delete(self, chat, match.group(1))" in routes
    assert "def group_delete(" in api
    assert "def do_PATCH" not in routes
    assert "def group_rename(" not in api
    assert "session_rename" not in routes
    assert "session_delete" not in routes



def _http_error(httpd, path: str, data: bytes, timeout: float = 5) -> tuple[int, dict]:
    request = Request(
        _url(httpd, path),
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        urlopen(request, timeout=timeout)
    except HTTPError as exc:
        body = json.loads(exc.read().decode("utf-8"))
        return exc.code, body
    raise AssertionError("expected HTTPError")


def test_stream_model_and_effort_reach_provider(tmp_path: Path, monkeypatch) -> None:
    captured: list = []

    class Spy:
        async def chat(self, messages, tools=None):
            return ChatReply(content="pong")

        async def aclose(self):
            return

    def create(kind, provider):
        captured.append(provider)
        return Spy()

    monkeypatch.setattr("thyca.app.chat_app.ConnectFactory.create", create)
    cfg = seeded_config()
    cfg = replace(
        cfg,
        models={"card-model": ModelCfg()},
        defaultModel="gpt-4o-mini",
    )
    save(cfg, tmp_path / "config.json")
    app = ChatApp(tmp_path, load(tmp_path / "config.json"))
    httpd, thread = _start(tmp_path, app)
    try:
        created = _json(httpd, "/api/bots/thyca/dm")
        path = "/api/bots/thyca/dm/turn/stream"
        omitted = _stream_lines(_stream(httpd, path, data=b'{"text":"omit"}'))
        assert omitted[-1]["type"] == "turn.completed"
        assert captured[0].model == "gpt-4o-mini"
        assert captured[0].reasoningEffort == "high"
        body = json.dumps(
            {"text": "override", "model": "card-model", "effort": "low"}
        ).encode("utf-8")
        overridden = _stream_lines(_stream(httpd, path, data=body))
        assert overridden[-1]["type"] == "turn.completed"
        assert captured[-1].model == "card-model"
        assert captured[-1].reasoningEffort == "low"
        card = replace(
            cfg,
            models={"card-model": ModelCfg(reasoningEffort="low")},
            defaultModel="gpt-4o-mini",
        )
        save(card, tmp_path / "config.json")
        inherited = _stream_lines(
            _stream(
                httpd,
                path,
                data=b'{"text":"card-effort","model":"card-model"}',
            )
        )
        assert inherited[-1]["type"] == "turn.completed"
        assert captured[-1].model == "card-model"
        assert captured[-1].reasoningEffort == "low"
        stored = ConversationManager(tmp_path / "sessions").load(created["bot_id"])
        assistants = [
            item
            for item in stored.messages
            if item.role == "assistant" and (item.meta or {}).get("kind") != "naming"
        ]
        assert assistants[-1].meta["model"] == "card-model"
        assert assistants[0].meta["model"] == "gpt-4o-mini"
    finally:
        _stop(httpd, thread)
        app.shutdown()


def test_stream_rejects_unknown_model_and_bad_effort(tmp_path: Path) -> None:
    httpd, thread = _start(tmp_path, _chat(tmp_path, FakeLLM(ChatReply(content="x"))))
    try:
        created = _json(httpd, "/api/bots/thyca/dm")
        path = "/api/bots/thyca/dm/turn/stream"
        code, body = _http_error(
            httpd, path, b'{"text":"hi","model":"not-configured"}'
        )
        assert code == 400
        assert body == {"error": "invalid model"}
        code, body = _http_error(httpd, path, b'{"text":"hi","effort":""}')
        assert code == 400
        assert body == {"error": "invalid effort"}
        code, body = _http_error(httpd, path, b'{"text":"hi","model":""}')
        assert code == 400
        assert body == {"error": "invalid model"}
        code, body = _http_error(
            httpd, path, json.dumps({"text": "hi", "model": "a" * 201}).encode()
        )
        assert code == 400
        assert body == {"error": "invalid model"}
        code, body = _http_error(
            httpd, path, json.dumps({"text": "hi", "model": "a\nb"}).encode()
        )
        assert code == 400
        assert body == {"error": "invalid model"}
    finally:
        _stop(httpd, thread)


def test_cancel_in_flight_turn(tmp_path: Path) -> None:
    started = threading.Event()
    gate = asyncio.Event()

    class Blocked:
        async def chat(self, messages, tools=None):
            started.set()
            await gate.wait()
            return ChatReply(content="should-not")

    httpd, thread = _start(tmp_path, _chat(tmp_path, Blocked()))
    try:
        created = _json(httpd, "/api/bots/thyca/dm")
        conversation_id = created["bot_id"]
        response = _stream(
            httpd,
            "/api/bots/thyca/dm/turn/stream",
            data=b'{"text":"hold"}',
        )
        first = json.loads(response.readline().decode("utf-8"))
        assert first == {"type": "turn.accepted", "bot_id": "thyca"}
        assert started.wait(2)
        follower = urlopen(
            Request(
                _url(httpd, "/api/bots/thyca/dm/turn/stream"),
                method="GET",
            ),
            timeout=10,
        )
        assert json.loads(follower.readline().decode("utf-8")) == {"type": "turn.accepted", "bot_id": "thyca"}
        cancelled = _json(
            httpd,
            "/api/bots/thyca/dm/turn/cancel",
            method="POST",
            data=b"",
        )
        assert cancelled == {"ok": True}
        lines = _stream_lines(response)
        types = [item["type"] for item in lines]
        assert types[-1] == "turn.cancelled"
        follower_types = [item["type"] for item in _stream_lines(follower)]
        assert follower_types[-1] == "turn.cancelled"
        assert "turn.completed" not in types
        assert "turn.failed" not in types
        detail = _json(httpd, "/api/bots/thyca/dm")
        assert detail["running"] is False
        stored = ConversationManager(tmp_path / "sessions").load(conversation_id)
        assert [(item.role, item.content) for item in stored.messages] == [
            ("user", "hold")
        ]
        code, body = _http_error(
            httpd, "/api/bots/thyca/dm/turn/cancel", b""
        )
        assert code == 409
        assert body == {"error": "conversation idle"}
    finally:
        _stop(httpd, thread)


def test_retry_last_user_turn(tmp_path: Path) -> None:
    from thyca.bots.card import BotCard

    llm = ScriptedLLM(
        [
            ChatReply(content="first"),
            ChatReply(content="second"),
        ]
    )
    app = _chat(tmp_path, llm)
    app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                             description="Codes.", persona="You code."))
    httpd, thread = _start(tmp_path, app)
    try:
        created = _json(httpd, "/api/bots/thyca/dm")
        conversation_id = created["bot_id"]
        path = "/api/bots/thyca/dm/turn/stream"
        first = _stream_lines(_stream(httpd, path, data=b'{"text":"hello"}'))
        assert first[-1]["type"] == "turn.completed"
        retried = _stream_lines(_stream(httpd, path, data=b'{"retry":true}'))
        assert retried[0] == {"type": "turn.accepted", "bot_id": "thyca"}
        assert retried[-1]["type"] == "turn.completed"
        stored = ConversationManager(tmp_path / "sessions").load(conversation_id)
        users = [item for item in stored.messages if item.role == "user"]
        assistants = [
            item
            for item in stored.messages
            if item.role == "assistant" and (item.meta or {}).get("kind") != "naming"
        ]
        assert [item.content for item in users] == ["hello"]
        assert assistants[-1].content == "second"
        think_users = [item.content for item in llm.requests[-1] if item.role == "user"]
        assert think_users == ["hello"]

        # A fresh group (not the DM singleton) has no user row: retry is 400.
        empty = _json(httpd, "/api/groups", method="POST",
                      data=b'{"bots":["thyca","coder"]}')
        code, body = _http_error(
            httpd, f"/api/groups/{empty['group_id']}/turn/stream", b'{"retry":true}'
        )
        assert code == 400
        assert body == {"error": "no user"}
    finally:
        _stop(httpd, thread)

    started = threading.Event()
    release = threading.Event()

    class Slow:
        async def chat(self, messages, tools=None):
            started.set()
            await asyncio.to_thread(release.wait)
            return ChatReply(content="late")

    httpd, thread = _start(tmp_path, _chat(tmp_path, Slow()))
    try:
        busy = _json(httpd, "/api/bots/thyca/dm")
        response = _stream(
            httpd,
            "/api/bots/thyca/dm/turn/stream",
            data=b'{"text":"first"}',
        )
        assert json.loads(response.readline().decode("utf-8")) == {"type": "turn.accepted", "bot_id": "thyca"}
        assert started.wait(2)
        try:
            code, body = _http_error(
                httpd,
                "/api/bots/thyca/dm/turn/stream",
                b'{"retry":true}',
            )
            assert code == 409
            assert body == {"error": "conversation busy"}
        finally:
            response.close()
            release.set()
    finally:
        _stop(httpd, thread)


def test_cancel_then_retry_keeps_one_user(tmp_path: Path) -> None:
    started = threading.Event()
    gate = asyncio.Event()

    class BlockThenReply:
        def __init__(self) -> None:
            self.requests: list = []
            self._blocked = True

        async def chat(self, messages, tools=None):
            self.requests.append(list(messages))
            if self._blocked:
                started.set()
                await gate.wait()
                return ChatReply(content="should-not")
            return ChatReply(content="retried")

    llm = BlockThenReply()
    httpd, thread = _start(tmp_path, _chat(tmp_path, llm))
    try:
        created = _json(httpd, "/api/bots/thyca/dm")
        conversation_id = created["bot_id"]
        path = "/api/bots/thyca/dm/turn/stream"
        response = _stream(httpd, path, data=b'{"text":"hold"}')
        assert json.loads(response.readline().decode("utf-8")) == {"type": "turn.accepted", "bot_id": "thyca"}
        assert started.wait(2)
        assert _json(httpd, f"{path.replace('/stream', '/cancel')}", method="POST", data=b"") == {
            "ok": True
        }
        assert _stream_lines(response)[-1]["type"] == "turn.cancelled"
        llm._blocked = False
        retried = _stream_lines(_stream(httpd, path, data=b'{"retry":true}'))
        assert retried[-1]["type"] == "turn.completed"
        stored = ConversationManager(tmp_path / "sessions").load(conversation_id)
        users = [item for item in stored.messages if item.role == "user"]
        assistants = [
            item
            for item in stored.messages
            if item.role == "assistant" and (item.meta or {}).get("kind") != "naming"
        ]
        assert [item.content for item in users] == ["hold"]
        assert assistants[-1].content == "retried"
        user_turns = [
            [item.content for item in req if item.role == "user"]
            for req in llm.requests
        ]
        assert user_turns.count(["hold"]) == 2
    finally:
        _stop(httpd, thread)


def _provider_stub(calls: list, reply_model: str):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b""
            body = json.loads(raw.decode("utf-8"))
            calls.append(
                    {
                        "path": self.path,
                        "auth": self.headers.get("Authorization"),
                        "model": body.get("model"),
                    }
                )
            payload = json.dumps(
                {
                    "model": reply_model,
                    "choices": [
                        {
                            "message": {"content": "stub-ok"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 5,
                        "completion_tokens": 2,
                        "total_tokens": 7,
                    },
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_turn_model_override_routes_to_its_provider(tmp_path: Path) -> None:
    calls_a: list = []
    calls_b: list = []
    stub_a, thread_a = _provider_stub(calls_a, "model-a")
    stub_b, thread_b = _provider_stub(calls_b, "model-b")
    try:
        base_a = f"http://127.0.0.1:{stub_a.server_address[1]}"
        base_b = f"http://127.0.0.1:{stub_b.server_address[1]}"
        cfg = seeded_config()
        cfg = replace(
            cfg,
            providers={
                "default": ProviderEntry(baseUrl=base_a, apiKey="sk-a"),
                "second": ProviderEntry(baseUrl=base_b, apiKey="sk-b"),
            },
            defaultProvider="default",
            defaultModel="model-a",
            models={"model-a": ModelCfg(), "model-b": ModelCfg(provider="second")},
        )
        save(cfg, tmp_path / "config.json")
        app = ChatApp(tmp_path, load(tmp_path / "config.json"))
        httpd, thread = _start(tmp_path, app)
        try:
            created = _json(httpd, "/api/bots/thyca/dm")
            path = "/api/bots/thyca/dm/turn/stream"
            lines_b = _stream_lines(
                _stream(httpd, path, data=b'{"text":"hi b","model":"model-b"}')
            )
            assert lines_b[-1]["type"] == "turn.completed"
            lines_a = _stream_lines(
                _stream(httpd, path, data=b'{"text":"hi a","model":"model-a"}')
            )
            assert lines_a[-1]["type"] == "turn.completed"
        finally:
            _stop(httpd, thread)
            app.shutdown()
        assert calls_b and all(
            call["auth"] == "Bearer sk-b" and call["model"] == "model-b"
            for call in calls_b
        )
        assert calls_a and all(
            call["auth"] == "Bearer sk-a" and call["model"] == "model-a"
            for call in calls_a
        )
    finally:
        stub_a.shutdown()
        thread_a.join(timeout=2)
        stub_a.server_close()
        stub_b.shutdown()
        thread_b.join(timeout=2)
        stub_b.server_close()


def test_failed_turn_logs_one_stderr_line(tmp_path: Path, capsys) -> None:
    class BoomLLM:
        async def chat(self, messages, tools=None):
            raise LLMError("provider HTTP 404: model gone")

        async def aclose(self):
            return

    app = _chat(tmp_path, BoomLLM())
    httpd, thread = _start(tmp_path, app)
    try:
        created = _json(httpd, "/api/bots/thyca/dm")
        path = "/api/bots/thyca/dm/turn/stream"
        lines = _stream_lines(_stream(httpd, path, data=b'{"text":"hi"}'))
        assert lines[-1]["type"] == "turn.failed"
        assert lines[-1]["code"] == "llm_error"
        assert "model gone" in lines[-1]["message"]
    finally:
        _stop(httpd, thread)
        app.shutdown()
    err = capsys.readouterr().err
    assert (
        f"turn failed conversation={created['bot_id']} model=gpt-4o-mini "
        "provider=default code=llm_error msg=provider HTTP 404: model gone" in err
    )


def test_stream_invalid_model_logs_preaccept_failure(tmp_path: Path, capsys) -> None:
    app = _chat(tmp_path, FakeLLM(ChatReply(content="unused")))
    httpd, thread = _start(tmp_path, app)
    try:
        created = _json(httpd, "/api/bots/thyca/dm")
        path = "/api/bots/thyca/dm/turn/stream"
        request = Request(
            _url(httpd, path),
            data=b'{"text":"hi","model":"ghost-model"}',
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            urlopen(request, timeout=5)
            raise AssertionError("expected HTTP 400")
        except HTTPError as exc:
            assert exc.code == 400
            assert json.loads(exc.read().decode()) == {"error": "invalid model"}
    finally:
        _stop(httpd, thread)
        app.shutdown()
    err = capsys.readouterr().err
    assert (
        f"turn failed conversation={created['bot_id']} model=ghost-model "
        "provider=default code=invalid_model msg=invalid model" in err
    )


def test_failed_turn_marks_transcript_and_trace(tmp_path: Path) -> None:
    class BoomLLM:
        async def chat(self, messages, tools=None):
            raise LLMError("provider HTTP 404: model gone")

        async def aclose(self):
            return

    app = _chat(tmp_path, BoomLLM())
    httpd, thread = _start(tmp_path, app)
    try:
        created = _json(httpd, "/api/bots/thyca/dm")
        path = "/api/bots/thyca/dm/turn/stream"
        lines = _stream_lines(_stream(httpd, path, data=b'{"text":"hi"}'))
        assert lines[-1]["type"] == "turn.failed"
        stored = ConversationManager(tmp_path / "sessions").load(created["bot_id"])
        # Hard-cut branch (2026-11-01): failures append a trailing system
        # marker (append-only) instead of stamping the user row in place.
        # Trace projects the marker back onto its turn, so failed turns
        # still read as failed without rewriting stored bytes.
        users = [item for item in stored.messages if item.role == "user"]
        assert (users[-1].meta or {}).get("error") is None
        markers = [
            item for item in stored.messages
            if item.role == "system" and isinstance((item.meta or {}).get("error"), dict)
        ]
        assert len(markers) == 1
        assert markers[-1].meta["error"] == {
            "code": "llm_error", "message": "provider HTTP 404: model gone"
        }
        traces = _json(httpd, "/api/traces")
        mine = [row for row in traces["traces"] if row["bot_id"] == created["bot_id"]]
        assert len(mine) == 1
        assert mine[0]["status"] == "failed"
        assert mine[0]["error"] == {
            "code": "llm_error",
            "message": "provider HTTP 404: model gone",
        }
    finally:
        _stop(httpd, thread)
        app.shutdown()


def test_retry_after_failure_strips_marker_on_success(tmp_path: Path) -> None:
    class FlakyLLM:
        def __init__(self) -> None:
            self.calls = 0

        async def chat(self, messages, tools=None):
            self.calls += 1
            if self.calls == 1:
                raise LLMError("boom-1")
            return ChatReply(content="recovered")

        async def aclose(self):
            return

    app = _chat(tmp_path, FlakyLLM())
    httpd, thread = _start(tmp_path, app)
    try:
        created = _json(httpd, "/api/bots/thyca/dm")
        path = "/api/bots/thyca/dm/turn/stream"
        failed = _stream_lines(_stream(httpd, path, data=b'{"text":"hi"}'))
        assert failed[-1]["type"] == "turn.failed"
        retried = _stream_lines(_stream(httpd, path, data=b'{"retry":true}'))
        assert retried[-1]["type"] == "turn.completed"
        stored = ConversationManager(tmp_path / "sessions").load(created["bot_id"])
        users = [item for item in stored.messages if item.role == "user"]
        assert (users[-1].meta or {}).get("error") is None
        assistants = [
            item
            for item in stored.messages
            if item.role == "assistant" and (item.meta or {}).get("kind") != "naming"
        ]
        assert assistants[-1].content == "recovered"
    finally:
        _stop(httpd, thread)
        app.shutdown()


def test_cancelled_turn_leaves_no_error_marker(tmp_path: Path) -> None:
    started = threading.Event()
    gate = asyncio.Event()

    class Blocked:
        async def chat(self, messages, tools=None):
            started.set()
            await gate.wait()
            return ChatReply(content="should-not")

    app = _chat(tmp_path, Blocked())
    httpd, thread = _start(tmp_path, app)
    try:
        created = _json(httpd, "/api/bots/thyca/dm")
        conversation_id = created["bot_id"]
        response = _stream(
            httpd, "/api/bots/thyca/dm/turn/stream", data=b'{"text":"hold"}'
        )
        assert json.loads(response.readline().decode("utf-8")) == {"type": "turn.accepted", "bot_id": "thyca"}
        assert started.wait(2)
        cancelled = _json(
            httpd, "/api/bots/thyca/dm/turn/cancel", method="POST", data=b""
        )
        assert cancelled == {"ok": True}
        assert _stream_lines(response)[-1]["type"] == "turn.cancelled"
        stored = ConversationManager(tmp_path / "sessions").load(conversation_id)
        assert [(item.role, item.content) for item in stored.messages] == [("user", "hold")]
        assert (stored.messages[0].meta or {}).get("error") is None
    finally:
        _stop(httpd, thread)
        app.shutdown()


def test_unexpected_turn_error_marks_chat_unavailable(tmp_path: Path) -> None:
    class BoomGeneric:
        async def chat(self, messages, tools=None):
            raise RuntimeError("boom-secret-path-/tmp/x")

        async def aclose(self):
            return

    app = _chat(tmp_path, BoomGeneric())
    httpd, thread = _start(tmp_path, app)
    try:
        created = _json(httpd, "/api/bots/thyca/dm")
        path = "/api/bots/thyca/dm/turn/stream"
        lines = _stream_lines(_stream(httpd, path, data=b'{"text":"hi"}'))
        assert lines[-1] == {
            "type": "turn.failed",
            "code": "chat_unavailable",
            "message": "chat unavailable",
        }
        stored = ConversationManager(tmp_path / "sessions").load(created["bot_id"])
        # Hard-cut: trailing system marker, not a user stamp (see above).
        users = [item for item in stored.messages if item.role == "user"]
        assert (users[-1].meta or {}).get("error") is None
        markers = [
            item for item in stored.messages
            if item.role == "system" and isinstance((item.meta or {}).get("error"), dict)
        ]
        assert markers[-1].meta["error"] == {
            "code": "chat_unavailable", "message": "chat unavailable"
        }
    finally:
        _stop(httpd, thread)
        app.shutdown()


def test_4000_emoji_turn_accepted_end_to_end(tmp_path: Path) -> None:
    llm = FakeLLM(ChatReply(content="pong"))
    app = _chat(tmp_path, llm)
    httpd, thread = _start(tmp_path, app)
    try:
        created = _json(httpd, "/api/bots/thyca/dm")
        text = "\U0001f600" * 4000
        assert len(text) == 4000
        body = json.dumps({"text": text}).encode("utf-8")
        assert len(body) > 16_384  # would trip the old fixed cap
        turned = _json(
            httpd, "/api/bots/thyca/dm/turn", method="POST", data=body
        )
        assert turned["reply"] == "pong"
        assert llm.requests[0][-1].content == text
    finally:
        _stop(httpd, thread)
        app.shutdown()


def test_orphan_group_over_http(tmp_path: Path) -> None:
    from thyca.bots.card import BotCard

    llm = FakeLLM(ChatReply(content="pong"))
    app = _chat(tmp_path, llm)
    app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder", description="Codes.", persona="You code."))
    app._bots.create(BotCard(bot_id="helper", name="helper", role="Helper", description="Helps.", persona="You help."))
    httpd, thread = _start(tmp_path, app)
    try:
        created = _json(httpd, "/api/groups", method="POST", data=b'{"bots":["coder","helper"]}')
        sid = created["group_id"]
        _json(httpd, f"/api/groups/{sid}/turn", method="POST", data=b'{"text":"ping"}')
        # Simulate an old orphan left in the database.
        app._bots.delete("coder")
        detail = _json(httpd, f"/api/groups/{sid}")
        assert detail["kind"] == "group" and detail["bots"] == ["coder", "helper"]
        assert "title" not in detail
        turned = _json(httpd, f"/api/groups/{sid}/turn", method="POST", data=b'{"text":"hello"}')
        assert turned["reply"] == "pong"
        removed = _json(httpd, f"/api/groups/{sid}/bots", method="POST", data=json.dumps({"bot_id": "coder", "action": "remove"}).encode("utf-8"))
        assert removed["bots"] == ["helper"]
        deleted = _json(httpd, f"/api/groups/{sid}", method="DELETE")
        assert deleted["ok"] is True
    finally:
        _stop(httpd, thread)
        app.shutdown()


def test_all_dead_group_turn_is_clear_over_http(tmp_path: Path) -> None:
    from thyca.bots.card import BotCard

    llm = FakeLLM(ChatReply(content="pong"))
    app = _chat(tmp_path, llm)
    app._bots.create(BotCard(bot_id="solo1", name="solo1", role="R", description="D.", persona="p"))
    app._bots.create(BotCard(bot_id="solo2", name="solo2", role="R", description="D.", persona="p"))
    httpd, thread = _start(tmp_path, app)
    try:
        created = _json(httpd, "/api/groups", method="POST", data=b'{"bots":["solo1","solo2"]}')
        sid = created["group_id"]
        _json(httpd, f"/api/groups/{sid}/turn", method="POST", data=b'{"text":"ping"}')
        app._bots.delete("solo1")
        app._bots.delete("solo2")
        detail = _json(httpd, f"/api/groups/{sid}")
        assert detail["kind"] == "group"
        try:
            _json(httpd, f"/api/groups/{sid}/turn", method="POST", data=b'{"text":"hello"}')
        except HTTPError as exc:
            assert exc.code == 409
            assert json.loads(exc.read().decode("utf-8")) == {"error": "conversation has no live participant"}
        else:
            raise AssertionError("expected 409 for all-dead turn")
        assert _json(httpd, f"/api/groups/{sid}", method="DELETE")["ok"] is True
    finally:
        _stop(httpd, thread)
        app.shutdown()


def test_chat_delete_group_button_wired() -> None:
    """Static wiring: header button, toggle, and DELETE flow exist."""
    root = Path(__file__).resolve().parents[1]
    html = (root / "thyca" / "webui" / "ui" / "chat" / "index.html").read_text()
    js = (root / "thyca" / "webui" / "ui" / "chat" / "js" / "chat.js").read_text()
    assert 'id="delete-group"' in html and 'class="quiet-button" id="delete-group"' in html
    for token in ("updateGroupDeleteButton", "deleteCurrentGroup", "convDetailUrl",
                  "refreshConversations", "renderGroups", 'method: \'DELETE\''):
        assert token in js, token
