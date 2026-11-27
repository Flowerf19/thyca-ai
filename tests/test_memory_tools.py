from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from thyca.conversations.runtime import ConversationManager
from thyca.conversations.runtime.store import ConversationStore
from thyca.conversations.store.runtime import ensure_runtime_store
from thyca.core.channel import WindowRef
from thyca.core.protocol import ToolCall
from thyca.memories import jobs
from thyca.memories.facade import MemoryFacade
from thyca.memories.heading import parse_heading
from thyca.memories.identity import bind_bot_id, reset_bot_id
from thyca.memories.visibility import MemoryView, bound_memory_view
from thyca.tools.gateway import ToolGateway
from thyca.tools.memory_tools import (
    bind_chat_conversation,
    register_memory_tools,
    reset_chat_conversation,
)
from thyca.tools.registry import ToolRegistry
from thyca.tools.task_store import TaskStore


@pytest.mark.asyncio
async def test_memory_note_and_get_roundtrip(tmp_path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    registry = ToolRegistry()
    register_memory_tools(registry, facade)
    assert {s["function"]["name"] for s in registry.to_openai_schema()} == {
        "memory_search",
        "memory_get",
        "memory_note",
        "memory_task",
        "memory_forget",
    }
    gateway = ToolGateway(registry, TaskStore())
    noted = await gateway.submit(
        ToolCall(
            id="r1",
            name="memory_note",
            arguments={"topic": "cafe", "summary": "cafedenunique"},
        )
    )
    assert not noted.is_error
    payload = json.loads(noted.content)
    assert payload["memory_id"][10] == "#"
    got = await gateway.submit(
        ToolCall(id="g1", name="memory_get", arguments={"id": payload["memory_id"]})
    )
    assert not got.is_error
    assert "cafedenunique" in got.content


@pytest.mark.asyncio
async def test_memory_note_rejects_soul_target(tmp_path) -> None:
    registry = ToolRegistry()
    register_memory_tools(registry, MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh"))
    gateway = ToolGateway(registry, TaskStore())
    result = await gateway.submit(
        ToolCall(
            id="bad",
            name="memory_note",
            arguments={"topic": "me", "summary": "x", "target": "soul"},
        )
    )
    assert result.is_error
    assert "unexpected argument" in result.content


@pytest.mark.asyncio
async def test_memory_note_update_keeps_memory_id(tmp_path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    registry = ToolRegistry()
    register_memory_tools(registry, facade)
    gateway = ToolGateway(registry, TaskStore())
    noted = await gateway.submit(
        ToolCall(
            id="r1",
            name="memory_note",
            arguments={"topic": "cafe", "summary": "cafedenunique"},
        )
    )
    assert not noted.is_error
    sid = json.loads(noted.content)["memory_id"]
    updated = await gateway.submit(
        ToolCall(
            id="u1",
            name="memory_note",
            arguments={"memory_id": sid, "topic": "tra da"},
        )
    )
    assert not updated.is_error
    got = await gateway.submit(
        ToolCall(id="g1", name="memory_get", arguments={"id": sid})
    )
    assert not got.is_error
    assert "tra da" in got.content
    assert "cafedenunique" in got.content


@pytest.mark.asyncio
async def test_memory_search_returns_owner_dm_hit_in_group(tmp_path) -> None:
    tz = ZoneInfo("Asia/Ho_Chi_Minh")
    yesterday = (datetime.now(tz).date() - timedelta(days=1)).isoformat()
    ensure_runtime_store(tmp_path)
    ConversationManager(tmp_path / "sessions").ensure_dm("coder")
    store = ConversationStore(tmp_path / "sessions")
    store.ensure_dir()
    group_id = "group-" + "d" * 32
    store.append_bots(store.create(group_id), ["thyca", "coder"])

    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    facade.remember(
        "OWNERDM-GATEWAY-HIT", "OWNERDM-GATEWAY-BODY",
        now=datetime.fromisoformat(f"{yesterday}T12:00:00+07:00"),
        chat="coder", bots=["coder"])
    facade._refresh_index()
    registry = ToolRegistry()
    register_memory_tools(registry, facade)
    gateway = ToolGateway(registry, TaskStore())
    window = WindowRef(
        window_id=group_id, channel_id="webui", kind="group",
        participants=("thyca", "coder"))
    token = bind_bot_id("coder")
    try:
        with bound_memory_view(MemoryView(window, "coder", tmp_path)):
            result = await gateway.submit(
                ToolCall(id="s1", name="memory_search",
                         arguments={"query": "OWNERDM-GATEWAY-HIT"}))
    finally:
        reset_bot_id(token)
    assert not result.is_error
    assert "OWNERDM-GATEWAY-HIT" in result.content


@pytest.mark.asyncio
async def test_memory_search_tasks_scope_returns_owner_dm_task_in_group(tmp_path) -> None:
    tz = ZoneInfo("Asia/Ho_Chi_Minh")
    yesterday = (datetime.now(tz).date() - timedelta(days=1)).isoformat()
    ensure_runtime_store(tmp_path)
    ConversationManager(tmp_path / "sessions").ensure_dm("coder")
    store = ConversationStore(tmp_path / "sessions")
    store.ensure_dir()
    group_id = "group-" + "e" * 32
    store.append_bots(store.create(group_id), ["thyca", "coder"])

    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    tid = facade.new_task_id()
    facade.remember(
        "OWNERDM-GATEWAY-TASK", "OWNERDM-GATEWAY-TASK-BODY",
        task=tid, kind="checkpoint", status="open",
        now=datetime.fromisoformat(f"{yesterday}T12:00:00+07:00"),
        chat="coder", bots=["coder"])
    facade._refresh_index()
    registry = ToolRegistry()
    register_memory_tools(registry, facade)
    gateway = ToolGateway(registry, TaskStore())
    window = WindowRef(
        window_id=group_id, channel_id="webui", kind="group",
        participants=("thyca", "coder"))
    token = bind_bot_id("coder")
    try:
        with bound_memory_view(MemoryView(window, "coder", tmp_path)):
            result = await gateway.submit(
                ToolCall(id="r1", name="memory_search", arguments={"scope": "tasks"}))
    finally:
        reset_bot_id(token)
    assert not result.is_error
    assert tid in result.content


@pytest.mark.asyncio
async def test_memory_search_tasks_scope_filters_by_query_accent_insensitively(tmp_path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    tid_momo = facade.new_task_id()
    tid_nginx = facade.new_task_id()
    facade.remember(
        "Tích hợp thanh toán Momo",
        "Tạm hủy do chưa có giấy phép merchant",
        task=tid_momo,
        kind="checkpoint",
        status="cancelled",
    )
    facade.remember(
        "Cấu hình Nginx rate limit",
        "Chốt ngưỡng 30r/s burst 20",
        task=tid_nginx,
        kind="checkpoint",
        status="done",
    )
    registry = ToolRegistry()
    register_memory_tools(registry, facade)
    gateway = ToolGateway(registry, TaskStore())

    res = await gateway.submit(
        ToolCall(
            id="t1",
            name="memory_search",
            arguments={"scope": "tasks", "query": "tich hop momo"},
        )
    )
    assert not res.is_error
    payload = json.loads(res.content)
    assert [t["task_id"] for t in payload["tasks"]] == [tid_momo]

    res_partial = await gateway.submit(
        ToolCall(
            id="t2",
            name="memory_search",
            arguments={"scope": "tasks", "query": "momo merchant"},
        )
    )
    assert not res_partial.is_error
    assert [t["task_id"] for t in json.loads(res_partial.content)["tasks"]] == [tid_momo]


@pytest.mark.asyncio
async def test_memory_search_day_only_lists_entries_and_respects_visibility(tmp_path) -> None:
    tz = ZoneInfo("Asia/Ho_Chi_Minh")
    yesterday = (datetime.now(tz).date() - timedelta(days=1)).isoformat()
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    sid1 = facade.remember(
        "Alpha note",
        "First note yesterday",
        now=datetime.fromisoformat(f"{yesterday}T09:00:00+07:00"),
        bots=["thyca"],
    )
    sid2 = facade.remember(
        "Worker private",
        "Hidden from other bot",
        now=datetime.fromisoformat(f"{yesterday}T10:00:00+07:00"),
        bots=["worker-a"],
    )
    registry = ToolRegistry()
    register_memory_tools(registry, facade)
    gateway = ToolGateway(registry, TaskStore())

    tok_thyca = bind_bot_id("thyca")
    try:
        res_all = await gateway.submit(
            ToolCall(id="d1", name="memory_search", arguments={"day": yesterday})
        )
    finally:
        reset_bot_id(tok_thyca)
    assert not res_all.is_error
    hits_all = json.loads(res_all.content)["hits"]
    assert [h["memory_id"] for h in hits_all] == [sid1, sid2]

    tok_b = bind_bot_id("worker-b")
    try:
        res_b = await gateway.submit(
            ToolCall(id="d2", name="memory_search", arguments={"day": yesterday})
        )
    finally:
        reset_bot_id(tok_b)
    assert not res_b.is_error
    assert json.loads(res_b.content)["hits"] == []


@pytest.mark.asyncio
async def test_note_injects_bound_chat_and_rejects_chat_argument(tmp_path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    registry = ToolRegistry()
    register_memory_tools(registry, facade)
    gateway = ToolGateway(registry, TaskStore())
    token = bind_chat_conversation("2026-09-16T14-39-01_a1b2")
    try:
        noted = await gateway.submit(
            ToolCall(
                id="r1",
                name="memory_note",
                arguments={
                    "topic": "link",
                    "summary": "bound-chat-token",
                },
            )
        )
    finally:
        reset_chat_conversation(token)
    assert not noted.is_error
    sid = json.loads(noted.content)["memory_id"]
    daily = next((tmp_path / "memory").glob("*.md"))
    meta = next(
        m for line in daily.read_text(encoding="utf-8").splitlines() if (m := parse_heading(line))
    )
    assert meta.chat == "2026-09-16T14-39-01_a1b2"

    refused = await gateway.submit(
        ToolCall(
            id="u1",
            name="memory_note",
            arguments={"memory_id": sid, "chat": "nope"},
        )
    )
    assert refused.is_error
    assert "unexpected argument" in refused.content


@pytest.mark.asyncio
async def test_memory_note_routes_through_ledgers_and_prevents_duplicates(tmp_path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    registry = ToolRegistry()
    register_memory_tools(registry, facade)
    gateway = ToolGateway(registry, TaskStore())

    bot_tok = bind_bot_id("thyca")
    chat_tok = bind_chat_conversation("dm-thyca")
    try:
        h1 = await gateway.submit(ToolCall(
            id="h1", name="memory_note",
            arguments={"topic": "Tea", "summary": "Uong tra sang", "habit": "uong tra sang"},
        ))
        h2 = await gateway.submit(ToolCall(
            id="h2", name="memory_note",
            arguments={"topic": "Tea", "summary": "Uong tra sang lan 2", "habit": "uong tra sang"},
        ))
        o1 = await gateway.submit(ToolCall(
            id="o1", name="memory_note",
            arguments={"topic": "OS", "summary": "May chay Fedora Bazzite"},
        ))
        o2 = await gateway.submit(ToolCall(
            id="o2", name="memory_note",
            arguments={"topic": "OS", "summary": "May chay Fedora Bazzite"},
        ))
        p1 = await gateway.submit(ToolCall(
            id="p1", name="memory_note",
            arguments={"topic": "Repo", "summary": "Repo dung uv cho Python"},
        ))
        flushed = await jobs.apply_memory_tool_calls(
            registry,
            [
                ToolCall(
                    id="sidecar1",
                    name="memory_note",
                    arguments={"topic": "Repo", "summary": "Repo dung uv cho Python"},
                )
            ],
            bot_id="thyca",
            chat="dm-thyca",
        )
    finally:
        reset_chat_conversation(chat_tok)
        reset_bot_id(bot_tok)

    assert json.loads(h1.content)["memory_id"] == json.loads(h2.content)["memory_id"]
    assert json.loads(o1.content)["memory_id"] == json.loads(o2.content)["memory_id"]
    assert flushed["written"] == [json.loads(p1.content)["memory_id"]]

    daily = next((tmp_path / "memory").glob("*.md")).read_text(encoding="utf-8")
    headings = [line for line in daily.splitlines() if parse_heading(line) is not None]
    assert len(headings) == 3
    assert daily.count("May chay Fedora Bazzite") == 1
    assert daily.count("Repo dung uv cho Python") == 1
