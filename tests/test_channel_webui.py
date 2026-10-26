"""WS-0/WS-2: WebUI channel flows the workspace refactor must keep.

Pins root DM identity/tool scope, child DM reply attribution, group
@mention routing, retry transcript shape, the WebUI adapter's window/
event metadata, and the per-bot turn lease (one active turn per bot
across windows, other bots unaffected, safe cancel/cycle behaviour).
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

import pytest

from thyca.app.chat_app import ChatApp
from thyca.app.loop_turns import TurnCancelled
from thyca.app.webui_channel import WebUIChannelAdapter
from thyca.bots.card import BotCard
from thyca.bots.workspace_store import WorkspaceStore
from thyca.config import load, save
from seeded_config import seeded_config
from thyca.core.protocol import Message, ToolCall, parse_ts
from thyca.llm.llm_base import ChatReply, LLMError
from thyca.serve.turn_state import TurnHub
from thyca.sessions import SessionManager
from thyca.sessions.store import SessionStore


def _chat(tmp_path: Path, connect=None) -> ChatApp:
    save(seeded_config(), tmp_path / "config.json")
    return ChatApp(tmp_path, load(tmp_path / "config.json"), connect=connect)


def _make_bot(app: ChatApp, bot_id: str, **kwargs) -> None:
    app._bots.create(BotCard(bot_id=bot_id, name=bot_id, role="Helper",
                             description="Helps.",
                             persona=f"PERSONA-{bot_id}", **kwargs))


class _ToolCaptureLLM:
    def __init__(self) -> None:
        self.tool_names: list[list[str]] = []
        self.calls = 0

    async def chat(self, messages, tools=None):
        self.calls += 1
        names = sorted(
            str((tool or {}).get("function", {}).get("name") or "")
            for tool in (tools or [])
        )
        self.tool_names.append(names)
        return ChatReply(content=f"reply-{self.calls}", model="m",
                         usage={"prompt_tokens": 5, "completion_tokens": 1,
                                "total_tokens": 6})


def test_root_dm_keeps_identity_and_full_tool_scope(tmp_path: Path) -> None:
    llm = _ToolCaptureLLM()
    app = _chat(tmp_path, connect=llm)
    try:
        _make_bot(app, "coder")
        dm = app.create()
        assert dm["dm"] == "thyca"
        assert dm["kind"] == "dm"
        detail = app.turn(dm["id"], "hello root")
        assert detail["reply"] == "reply-1"
        rows = detail["messages"]
        reply_rows = [row for row in rows
                      if row.get("role") == "assistant"
                      and row.get("content") == "reply-1"]
        assert reply_rows, "root reply must land in the DM window"
        meta = reply_rows[-1].get("meta") or {}
        assert meta.get("bot_id") in (None, "thyca")
        assert llm.tool_names, "root turn must pass a tool snapshot"
        assert "bot_create" in llm.tool_names[-1]
    finally:
        app.shutdown()


def test_child_dm_reply_recorded_with_bot_id_and_internal(tmp_path: Path) -> None:
    llm = _ToolCaptureLLM()
    app = _chat(tmp_path, connect=llm)
    try:
        _make_bot(app, "coder")
        dm = app.bot_dm("coder")
        detail = app.turn(dm["id"], "hi coder")
        reply_rows = [row for row in detail["messages"]
                      if isinstance(row.get("meta"), dict)
                      and row["meta"].get("bot_id") == "coder"]
        assert reply_rows, "child reply row must carry its bot_id in the room"
        assert reply_rows[-1]["content"] == "reply-1"
        store, session_id = _internal_store(app, "coder", dm["id"])
        internal_messages = store.load(session_id).messages
        assert any(row.role == "user" and "hi coder" in str(row.content)
                   for row in internal_messages)
        raw = json.loads(
            (tmp_path / "bots" / "coder" / "workspace" / "registry.json")
            .read_text(encoding="utf-8"))
        entry = raw["windows"][dm["id"]]
        assert entry.get("session") == session_id
        assert not (tmp_path / "bots" / "coder"
                    / "room_sessions.json").exists()
        assert llm.tool_names and "bot_create" not in llm.tool_names[-1]
    finally:
        app.shutdown()


def test_group_mention_runs_child_not_root(tmp_path: Path) -> None:
    llm = _ToolCaptureLLM()
    app = _chat(tmp_path, connect=llm)
    try:
        _make_bot(app, "coder")
        group = app.create(bots=["thyca", "coder"])
        detail = app.turn(group["id"], "hey @coder look at this")
        assert detail["reply"] == "reply-1"
        bot_rows = [row for row in detail["messages"]
                    if isinstance(row.get("meta"), dict)
                    and row["meta"].get("bot_id")]
        assert bot_rows and all(row["meta"]["bot_id"] == "coder"
                                for row in bot_rows)
        assert "bot_create" not in llm.tool_names[-1]
    finally:
        app.shutdown()


def test_retry_keeps_single_user_row_and_new_reply(tmp_path: Path) -> None:
    llm = _ToolCaptureLLM()
    app = _chat(tmp_path, connect=llm)
    try:
        dm = app.create()
        first = app.turn(dm["id"], "do it")
        assert first["reply"] == "reply-1"
        second = app.turn(dm["id"], retry=True)
        assert second["reply"] == "reply-2"
        user_rows = [row for row in second["messages"]
                     if row.get("role") == "user"]
        assert [row.get("content") for row in user_rows] == ["do it"]
        replies = [row.get("content") for row in second["messages"]
                   if row.get("role") == "assistant"
                   and str(row.get("content") or "").startswith("reply-")]
        assert replies == ["reply-2"]
    finally:
        app.shutdown()


def test_webui_adapter_derives_window_and_event_metadata(
        tmp_path: Path) -> None:
    app = _chat(tmp_path, connect=_ToolCaptureLLM())
    try:
        _make_bot(app, "coder")
        adapter = WebUIChannelAdapter()
        store = app._sessions.store
        dm_session = store.load(app.bot_dm("coder")["id"])
        dm_window = adapter.window_for(dm_session)
        assert dm_window.channel_id == "webui"
        assert dm_window.kind == "dm"
        assert dm_window.visibility == "private"
        assert dm_window.participants == ("coder",)
        group = app.create(bots=["thyca", "coder"])
        group_window = adapter.window_for(store.load(group["id"]))
        assert group_window.kind == "group"
        assert group_window.visibility == "public"
        assert group_window.participants == ("thyca", "coder")
        event = adapter.event_for(store.load(group["id"]), "hello")
        assert event.window_id == group["id"]
        assert event.actor.kind == "user" and event.actor.id == "user"
        assert event.visibility == "public"
        assert event.event_id and event.idempotency_key
        parse_ts(event.received_at)
        assert adapter.capabilities.can_dm
        assert adapter.capabilities.can_card
        assert adapter.capabilities.supports_streaming
    finally:
        app.shutdown()


def _internal_store(app, bot_id: str, room_id: str):
    """Canonical journal store + session id for a bot's room binding."""
    internal = app._runtime.internal_session(bot_id, room_id)
    window = app._runtime._window_for_room(room_id)
    assert window is not None, f"no window for room {room_id}"
    return app._runtime._canonical_store(
        bot_id, window, internal.id), internal.id


def test_adapter_binding_survives_restart(
        tmp_path: Path) -> None:
    llm = _ToolCaptureLLM()
    app = _chat(tmp_path, connect=llm)
    try:
        _make_bot(app, "coder")
        dm = app.bot_dm("coder")
        app.turn(dm["id"], "hi dm")
        internal = app._runtime.internal_session("coder", dm["id"])
        group = app.create(bots=["thyca", "coder"])
        app.turn(group["id"], "@coder hi grp")
        app.shutdown()
        store = WorkspaceStore(tmp_path)
        binding = store.get_binding("coder", dm["id"])
        assert binding is not None
        assert binding.internal_session_id == internal.id
        assert binding.window is not None
        assert binding.window.kind == "dm"
        assert binding.window.channel_id == "webui"
        assert binding.window.visibility == "private"
        assert binding.window.participants == ("coder",)
        grp = store.get_binding("coder", group["id"])
        assert grp is not None and grp.window is not None
        assert grp.window.kind == "group"
        assert grp.window.visibility == "public"
        assert grp.window.participants == ("thyca", "coder")
        sessions = SessionStore(tmp_path / "sessions")
        window = WebUIChannelAdapter().window_for(sessions.load(dm["id"]))
        assert binding.window == window
        reloaded = WorkspaceStore(tmp_path).get_binding("coder", dm["id"])
        assert reloaded is not None and reloaded.window == window
    finally:
        app.shutdown()


class _GatedLLM:
    """Coder waits on a threading gate; every other bot replies now."""

    def __init__(self) -> None:
        self.gate = threading.Event()
        self.calls = 0

    async def chat(self, messages, tools=None):
        self.calls += 1
        text = "\n".join(str(m.content or "") for m in messages)
        if "PERSONA-coder" in text:
            await asyncio.to_thread(self.gate.wait)
            return ChatReply(content="coder-reply", model="m")
        return ChatReply(content="quick-reply", model="m")


def _turn_in_thread(app: ChatApp, session_id: str, text: str,
                    results: list, **kwargs) -> threading.Thread:
    def run() -> None:
        try:
            results.append(("ok", app.turn(session_id, text, **kwargs)))
        except BaseException as error:
            results.append(("err", error))

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def _wait_for(predicate, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("timed out waiting for condition")


def test_same_bot_dm_and_group_turns_serialize(tmp_path: Path) -> None:
    llm = _GatedLLM()
    app = _chat(tmp_path, connect=llm)
    try:
        _make_bot(app, "coder")
        _make_bot(app, "helper")
        dm = app.bot_dm("coder")["id"]
        group = app.create(bots=["coder", "helper"])["id"]
        seed = SessionManager(store=app._sessions.store)
        seed.load(group)
        seed.append(Message(role="user", content="seed"))
        first: list = []
        t1 = _turn_in_thread(app, dm, "dm-first", first)
        _wait_for(lambda: llm.calls >= 1)
        queued: list = []
        t2 = _turn_in_thread(app, group, "@coder grp-second", queued)
        _wait_for(lambda: app._turns.hub(group) is not None)
        assert not queued
        assert all("grp-second" not in str(m.get("content"))
                   for m in app.get_payload(group)["messages"])
        other: list = []
        t3 = _turn_in_thread(app, app.bot_dm("helper")["id"],
                             "helper-now", other)
        t3.join(timeout=10)
        assert other and other[0][0] == "ok"
        llm.gate.set()
        t1.join(timeout=10)
        t2.join(timeout=10)
        assert first[0][0] == "ok" and queued[0][0] == "ok"
        contents = [str(m.get("content"))
                    for m in app.get_payload(group)["messages"]]
        assert any("grp-second" in content for content in contents)
    finally:
        llm.gate.set()
        app.shutdown()


def test_queued_turn_cancel_writes_nothing(tmp_path: Path) -> None:
    llm = _GatedLLM()
    app = _chat(tmp_path, connect=llm)
    try:
        _make_bot(app, "coder")
        dm = app.bot_dm("coder")["id"]
        group = app.create(bots=["thyca", "coder"])["id"]
        first: list = []
        t1 = _turn_in_thread(app, dm, "dm-first", first)
        _wait_for(lambda: llm.calls >= 1)
        queued: list = []
        t2 = _turn_in_thread(app, group, "@coder grp-cancel", queued)
        _wait_for(lambda: app._turns.hub(group) is not None)
        queue = app._turns.hub(group).subscribe()
        app.cancel(group)
        t2.join(timeout=10)
        assert queued and queued[0][0] == "err"
        assert isinstance(queued[0][1], TurnCancelled)
        items = []
        while not queue.empty():
            items.append(queue.get())
        terminal = [item for item in items if item is not TurnHub.SENTINEL]
        assert terminal == [("cancelled", None)]
        assert all("grp-cancel" not in str(m.get("content"))
                   for m in app.get_payload(group)["messages"])
        llm.gate.set()
        t1.join(timeout=10)
        assert first[0][0] == "ok"
    finally:
        llm.gate.set()
        app.shutdown()


def test_failed_turn_releases_lease_and_retry_works(tmp_path: Path) -> None:
    class FlakyLLM:
        def __init__(self) -> None:
            self.calls = 0

        async def chat(self, messages, tools=None):
            self.calls += 1
            if self.calls == 1:
                raise LLMError("provider down")
            return ChatReply(content="recovered", model="m")

    llm = FlakyLLM()
    app = _chat(tmp_path, connect=llm)
    try:
        _make_bot(app, "coder")
        dm = app.bot_dm("coder")["id"]
        with pytest.raises(LLMError):
            app.turn(dm, "hi coder")
        second = app.turn(dm, retry=True)
        assert second["reply"] == "recovered"
    finally:
        app.shutdown()


class _CrossLLM:
    """Responder turns of coder/helper send to each other via bot_send."""

    def __init__(self) -> None:
        self.barrier = threading.Barrier(2)

    async def chat(self, messages, tools=None):
        text = "\n".join(str(m.content or "") for m in messages)
        tool_text = "\n".join(
            str(m.content or "") for m in messages if m.role == "tool")
        if "PING-" in text and "PERSONA" in text:
            return ChatReply(content="pong", model="m")
        if "pong" in tool_text or "delivery failed" in tool_text:
            if "PERSONA-coder" in text:
                return ChatReply(content="coder done", model="m")
            return ChatReply(content="helper done", model="m")
        if "PERSONA-coder" in text:
            await asyncio.to_thread(self.barrier.wait, 10)
            return ChatReply(content=None, model="m", tool_calls=[
                ToolCall(id="c-send", name="bot_send", arguments={
                    "bot_id": "helper", "message": "PING-C2H"}),
            ])
        if "PERSONA-helper" in text:
            await asyncio.to_thread(self.barrier.wait, 10)
            return ChatReply(content=None, model="m", tool_calls=[
                ToolCall(id="h-send", name="bot_send", arguments={
                    "bot_id": "coder", "message": "PING-H2C"}),
            ])
        return ChatReply(content="root reply", model="m")


def test_turn_rejects_nonstr_text_before_any_write(tmp_path: Path) -> None:
    from thyca.app.chat_app import InvalidTurnText

    app = _chat(tmp_path, connect=_ToolCaptureLLM())
    try:
        dm = app.create()
        for bad in (None, 123):
            with pytest.raises(InvalidTurnText):
                app.turn(dm["id"], bad)
        with pytest.raises(InvalidTurnText):
            app.turn("ghost-session", None)
        assert app._turns.hub(dm["id"]) is None
        assert app.get_payload(dm["id"])["messages"] == []
    finally:
        app.shutdown()


class _PingPongLLM:
    """coder delegates to helper; helper's delivery tries sending back."""

    async def chat(self, messages, tools=None):
        text = "\n".join(str(m.content or "") for m in messages)
        tool_text = "\n".join(
            str(m.content or "") for m in messages if m.role == "tool")
        if "PING-TO-HELPER" in text:
            if "delivery failed" in tool_text:
                return ChatReply(content="helper done", model="m")
            return ChatReply(content=None, model="m", tool_calls=[
                ToolCall(id="h-send", name="bot_send", arguments={
                    "bot_id": "coder", "message": "PING-BACK"}),
            ])
        if "helper done" in tool_text or "delivery failed" in tool_text:
            return ChatReply(content="coder done", model="m")
        return ChatReply(content=None, model="m", tool_calls=[
            ToolCall(id="c-send", name="bot_send", arguments={
                "bot_id": "helper", "message": "PING-TO-HELPER"}),
        ])


def test_a2a_send_back_to_running_bot_conflicts_and_stays_queued(
        tmp_path: Path) -> None:
    app = _chat(tmp_path, connect=_PingPongLLM())
    try:
        _make_bot(app, "coder", tools=("bot_send",))
        _make_bot(app, "helper", tools=("bot_send",))
        group = app.create(bots=["coder", "helper"])["id"]
        detail = app.turn(group, "@coder go")
        assert detail["reply"] == "coder done"
        rows = detail["messages"]
        assert any(
            (m.get("meta") or {}).get("bot_id") == "helper"
            and m.get("content") == "helper done"
            for m in rows)
        assert app._runtime._bus.pending("coder") >= 1
        store, session_id = _internal_store(app, "coder", group)
        coder_internal = store.load(session_id).messages
        assert all("PING-BACK" not in str(m.content)
                   for m in coder_internal)
    finally:
        app.shutdown()


def test_bidirectional_a2a_conflict_neither_hangs_nor_double_runs(
        tmp_path: Path) -> None:
    llm = _CrossLLM()
    app = _chat(tmp_path, connect=llm)
    try:
        _make_bot(app, "coder", tools=("bot_send",))
        _make_bot(app, "helper", tools=("bot_send",))
        g1 = app.create(bots=["coder", "helper"])["id"]
        seed = SessionManager(store=app._sessions.store)
        seed.load(g1)
        seed.append(Message(role="user", content="seed-g1"))
        g2 = app.create(bots=["coder", "helper"])["id"]
        seed.load(g2)
        seed.append(Message(role="user", content="seed-g2"))
        first: list = []
        second: list = []
        t1 = _turn_in_thread(app, g1, "@coder go", first)
        t2 = _turn_in_thread(app, g2, "@helper go", second)
        t1.join(timeout=60)
        t2.join(timeout=60)
        assert not t1.is_alive() and not t2.is_alive()
        assert first[0][0] == "ok" and second[0][0] == "ok"
        replies = {first[0][1]["reply"], second[0][1]["reply"]}
        assert replies == {"coder done", "helper done"}
    finally:
        app.shutdown()
