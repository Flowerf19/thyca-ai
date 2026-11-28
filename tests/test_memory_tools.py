from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from thyca.bots.channel_tools import _wrap_memory_task
from thyca.bots.context import BotTurnContext, bind_bot_turn_context, reset_bot_turn_context
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


@pytest.mark.asyncio
async def test_memory_task_exact_normalized_dedupe_reuses_open_task(tmp_path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    registry = ToolRegistry()
    register_memory_tools(registry, facade)
    gateway = ToolGateway(registry, TaskStore())

    bot_tok = bind_bot_id("thyca")
    chat_tok = bind_chat_conversation("dm-thyca")
    try:
        first = await gateway.submit(
            ToolCall(
                id="t1",
                name="memory_task",
                arguments={"topic": "Triển khai CI/CD", "summary": "Bước 1: viết workflow"},
            )
        )
        assert not first.is_error
        first_payload = json.loads(first.content)
        assert first_payload["created"] is True

        second = await gateway.submit(
            ToolCall(
                id="t2",
                name="memory_task",
                arguments={"topic": "trien khai ci - cd", "summary": "Bước 2: xong test"},
            )
        )
        assert not second.is_error, second.content
        second_payload = json.loads(second.content)
        assert second_payload["created"] is False
        assert second_payload["matched_existing"] is True
        assert second_payload["task_id"] == first_payload["task_id"]
        assert first_payload["task_id"] in second_payload["note"]
    finally:
        reset_chat_conversation(chat_tok)
        reset_bot_id(bot_tok)

    open_tasks = facade.tasks(status="open")["tasks"]
    assert len(open_tasks) == 1
    assert open_tasks[0]["task_id"] == first_payload["task_id"]
    assert open_tasks[0]["title"] == "Triển khai CI/CD"
    assert "xong test" in open_tasks[0]["summary"]


@pytest.mark.asyncio
async def test_memory_task_near_duplicate_reminds_and_supports_force_new(tmp_path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    registry = ToolRegistry()
    register_memory_tools(registry, facade)
    gateway = ToolGateway(registry, TaskStore())

    bot_tok = bind_bot_id("thyca")
    chat_tok = bind_chat_conversation("dm-thyca")
    try:
        opened = await gateway.submit(
            ToolCall(
                id="t1",
                name="memory_task",
                arguments={
                    "topic": "Dựng pipeline backup Postgres lên S3",
                    "summary": "Đang cấu hình cron chạy đêm",
                },
            )
        )
        assert not opened.is_error
        tid = json.loads(opened.content)["task_id"]

        blocked = await gateway.submit(
            ToolCall(
                id="t2",
                name="memory_task",
                arguments={
                    "topic": "Backup Postgres lên S3",
                    "summary": "Đã xong script nén dump",
                },
            )
        )
        assert blocked.is_error
        assert tid in blocked.content
        assert "force_new=true" in blocked.content
        assert len(facade.tasks(status="open")["tasks"]) == 1

        updated = await gateway.submit(
            ToolCall(
                id="t3",
                name="memory_task",
                arguments={
                    "task": tid,
                    "summary": "Đã xong script nén dump",
                },
            )
        )
        assert not updated.is_error
        assert json.loads(updated.content)["task_id"] == tid
        assert len(facade.tasks(status="open")["tasks"]) == 1

        forced = await gateway.submit(
            ToolCall(
                id="t4",
                name="memory_task",
                arguments={
                    "topic": "Backup Postgres lên S3",
                    "summary": "Nhánh riêng cho cụm staging",
                    "force_new": True,
                },
            )
        )
        assert not forced.is_error
        assert json.loads(forced.content)["created"] is True
        assert json.loads(forced.content)["task_id"] != tid

        v1 = await gateway.submit(
            ToolCall(
                id="v1",
                name="memory_task",
                arguments={"topic": "Deploy API v1", "summary": "Đang deploy v1"},
            )
        )
        v2 = await gateway.submit(
            ToolCall(
                id="v2",
                name="memory_task",
                arguments={"topic": "Deploy API v2", "summary": "Đang deploy v2"},
            )
        )
        assert not v1.is_error and not v2.is_error
        assert json.loads(v1.content)["task_id"] != json.loads(v2.content)["task_id"]

        bug_login = await gateway.submit(
            ToolCall(
                id="b1",
                name="memory_task",
                arguments={"topic": "Sửa lỗi đăng nhập", "summary": "Fix OAuth"},
            )
        )
        bug_pay = await gateway.submit(
            ToolCall(
                id="b2",
                name="memory_task",
                arguments={"topic": "Sửa lỗi thanh toán", "summary": "Fix VNPAY"},
            )
        )
        assert not bug_login.is_error and not bug_pay.is_error
        assert json.loads(bug_login.content)["task_id"] != json.loads(bug_pay.content)["task_id"]
    finally:
        reset_chat_conversation(chat_tok)
        reset_bot_id(bot_tok)


@pytest.mark.asyncio
async def test_memory_task_and_habit_dedupe_respects_group_dm_isolation(tmp_path) -> None:
    ensure_runtime_store(tmp_path)
    ConversationManager(tmp_path / "sessions").ensure_dm("worker-a")
    store = ConversationStore(tmp_path / "sessions")
    store.ensure_dir()
    group_id = "group-" + "f" * 32
    store.append_bots(store.create(group_id), ["thyca", "worker-a"])

    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    registry = ToolRegistry()
    register_memory_tools(registry, facade)
    gateway = ToolGateway(registry, TaskStore())

    dm_window = WindowRef(
        window_id="worker-a",
        channel_id="webui",
        kind="dm",
        participants=("worker-a",),
    )
    group_window = WindowRef(
        window_id=group_id,
        channel_id="webui",
        kind="group",
        participants=("thyca", "worker-a"),
    )

    bot_tok = bind_bot_id("worker-a")
    try:
        dm_chat_tok = bind_chat_conversation("worker-a")
        try:
            with bound_memory_view(MemoryView(dm_window, "worker-a", tmp_path)):
                dm_task = await gateway.submit(
                    ToolCall(
                        id="dm1",
                        name="memory_task",
                        arguments={
                            "topic": "DM bí mật lương Q4",
                            "summary": "Thỏa thuận lương riêng",
                        },
                    )
                )
                assert not dm_task.is_error
                dm_tid = json.loads(dm_task.content)["task_id"]

                dm_habit = await gateway.submit(
                    ToolCall(
                        id="dm2",
                        name="memory_note",
                        arguments={
                            "topic": "Thói quen riêng",
                            "summary": "Uống trà thảo mộc tối muộn",
                            "habit": "uống trà thảo mộc tối muộn",
                            "explicit": True,
                        },
                    )
                )
                assert not dm_habit.is_error
        finally:
            reset_chat_conversation(dm_chat_tok)

        grp_chat_tok = bind_chat_conversation(group_id)
        try:
            with bound_memory_view(MemoryView(group_window, "worker-a", tmp_path)):
                grp_task = await gateway.submit(
                    ToolCall(
                        id="g1",
                        name="memory_task",
                        arguments={
                            "topic": "DM bí mật lương Q4",
                            "summary": "Nội dung công khai trong nhóm",
                        },
                    )
                )
                assert not grp_task.is_error
                grp_payload = json.loads(grp_task.content)
                assert grp_payload["created"] is True
                assert grp_payload["task_id"] != dm_tid
                assert dm_tid not in grp_task.content

                grp_habit = await gateway.submit(
                    ToolCall(
                        id="g2",
                        name="memory_note",
                        arguments={
                            "topic": "Thói quen nhóm",
                            "summary": "Trà thảo mộc",
                            "habit": "trà thảo mộc tối muộn",
                        },
                    )
                )
                assert not grp_habit.is_error
                assert "uống trà thảo mộc tối muộn" not in grp_habit.content
        finally:
            reset_chat_conversation(grp_chat_tok)
    finally:
        reset_bot_id(bot_tok)


@pytest.mark.asyncio
async def test_memory_note_habit_exact_punctuation_and_similar_reminder(tmp_path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    registry = ToolRegistry()
    register_memory_tools(registry, facade)
    gateway = ToolGateway(registry, TaskStore())

    bot_tok = bind_bot_id("thyca")
    chat_tok = bind_chat_conversation("dm-thyca")
    try:
        h1 = await gateway.submit(
            ToolCall(
                id="h1",
                name="memory_note",
                arguments={
                    "topic": "URL",
                    "summary": "Bọc URL trong code block",
                    "habit": "bọc URL, không link",
                },
            )
        )
        assert not h1.is_error
        sid1 = json.loads(h1.content)["memory_id"]

        h2 = await gateway.submit(
            ToolCall(
                id="h2",
                name="memory_note",
                arguments={
                    "topic": "URL",
                    "summary": "Lặp lại quy tắc URL",
                    "habit": "bọc URL không link",
                },
            )
        )
        assert not h2.is_error
        p2 = json.loads(h2.content)
        assert p2["memory_id"] == sid1
        assert p2["matched_existing"] is True

        h3 = await gateway.submit(
            ToolCall(
                id="h3",
                name="memory_note",
                arguments={
                    "topic": "Trà",
                    "summary": "Thích uống trà buổi sáng",
                    "habit": "uống trà buổi sáng",
                },
            )
        )
        assert not h3.is_error

        blocked = await gateway.submit(
            ToolCall(
                id="h4",
                name="memory_note",
                arguments={
                    "topic": "Trà",
                    "summary": "Uống trà sáng",
                    "habit": "trà buổi sáng",
                },
            )
        )
        assert blocked.is_error
        assert "uống trà buổi sáng" in blocked.content
        assert "force_new=true" in blocked.content

        forced = await gateway.submit(
            ToolCall(
                id="h5",
                name="memory_note",
                arguments={
                    "topic": "Trà",
                    "summary": "Nhãn riêng biệt",
                    "habit": "trà buổi sáng",
                    "force_new": True,
                },
            )
        )
        assert not forced.is_error
    finally:
        reset_chat_conversation(chat_tok)
        reset_bot_id(bot_tok)


@pytest.mark.asyncio
async def test_wrap_memory_task_skips_record_task_owner_on_deduped_reuse(tmp_path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    registry = ToolRegistry()
    register_memory_tools(registry, facade)
    recorded: list[tuple[str, str, str]] = []

    class FakeStore:
        def record_task_owner(self, bot_id: str, task_id: str, owner: str) -> None:
            recorded.append((bot_id, task_id, owner))

    deps = SimpleNamespace(runtime=SimpleNamespace(workspaces=SimpleNamespace(store=FakeStore())))
    _wrap_memory_task(registry, deps)
    gateway = ToolGateway(registry, TaskStore())

    bot_tok = bind_bot_id("thyca")
    chat_tok = bind_chat_conversation("dm-thyca")
    ctx_tok = bind_bot_turn_context(BotTurnContext(sender_id="thyca", room_id="dm-thyca"))
    try:
        r1 = await gateway.submit(
            ToolCall(
                id="w1",
                name="memory_task",
                arguments={"topic": "Triển khai CI/CD", "summary": "Mở đầu"},
            )
        )
        assert not r1.is_error
        tid = json.loads(r1.content)["task_id"]
        assert recorded == [("thyca", tid, "thyca")]

        r2 = await gateway.submit(
            ToolCall(
                id="w2",
                name="memory_task",
                arguments={"topic": "trien khai ci - cd", "summary": "Cập nhật"},
            )
        )
        assert not r2.is_error
        p2 = json.loads(r2.content)
        assert p2["created"] is False
        assert p2["matched_existing"] is True
        assert p2["task_id"] == tid
        assert recorded == [("thyca", tid, "thyca")]
    finally:
        reset_bot_turn_context(ctx_tok)
        reset_chat_conversation(chat_tok)
        reset_bot_id(bot_tok)
