"""Deterministic unit & AgentLoop tests for memory tool schemas, validation, and LLM behavior."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from thyca.agents.act import Act
from thyca.agents.assemble import Assemble
from thyca.agents.loop import AgentLoop
from thyca.agents.observe import Observe
from thyca.agents.think import ChatReply, Think
from thyca.bots.timeline import CanonicalConversationStore, WorkspaceTimeline
from thyca.conversations.runtime import ConversationManager
from thyca.conversations.store.runtime import ensure_runtime_store
from thyca.core.channel import WindowRef
from thyca.core.protocol import Message, ToolCall
from thyca.llms.openai_responses import _to_responses_tools
from thyca.llms.prompt_manager import PromptManager
from thyca.memories.active import ActiveMemory
from thyca.memories.facade import MemoryFacade
from thyca.memories.task_ledger import format_task_catalog
from thyca.tools.gateway import ToolGateway
from thyca.tools.memory_tools import register_memory_tools
from thyca.tools.registry import ToolRegistry, ToolSpec
from thyca.tools.task_store import TaskStore

TZ_NAME = "Asia/Ho_Chi_Minh"
TZ = ZoneInfo(TZ_NAME)


def _setup_gateway(tmp_path: Path) -> tuple[MemoryFacade, ToolRegistry, ToolGateway]:
    facade = MemoryFacade(tmp_path, timezone_name=TZ_NAME)
    registry = ToolRegistry()
    register_memory_tools(registry, facade)
    gateway = ToolGateway(registry, TaskStore())
    return facade, registry, gateway


def _canonical_manager(root: Path) -> ConversationManager:

    timeline = WorkspaceTimeline(root, "thyca")
    window = WindowRef(
        window_id="thyca",
        channel_id="webui",
        kind="dm",
        participants=("thyca",),
    )
    conversation_id = "2026-11-19T10-00-00_ab12"
    store = CanonicalConversationStore(
        root / "bots" / "thyca" / "sessions",
        timeline=timeline,
        window=window,
        conversation_id=conversation_id,
    )
    manager = ConversationManager(store=store)
    manager.load(conversation_id)
    return manager


@dataclass
class ScriptedConnect:
    """Offline fake LLM that runs scripted handlers over observed tool results."""

    step_fn: object
    calls: list[tuple[list[Message], list | None]] = field(default_factory=list)

    async def chat(self, messages: list[Message], tools: list | None = None) -> ChatReply:
        snapshot = list(messages)
        self.calls.append((snapshot, tools))
        return self.step_fn(snapshot, tools, len(self.calls))


def _make_loop(
    root: Path,
    gateway: ToolGateway,
    registry: ToolRegistry,
    connect: ScriptedConnect,
    *,
    loop_max: int = 5,
) -> AgentLoop:
    manager = _canonical_manager(root)
    return AgentLoop(
        conversations=manager,
        assemble=Assemble(),
        think=Think(connect),
        act=Act(gateway),
        observe=Observe(manager),
        loop_max=loop_max,
        tools=registry.to_openai_schema(),
        model="fake-memory-model",
    )


def test_registry_validates_enum_and_bool_strictness() -> None:
    async def noop(args: dict) -> str:
        return "ok"

    spec = ToolSpec(
        name="probe_enum",
        description="probe enum handling",
        parameters={
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["habit", "checkpoint"]},
                "level": {"type": "integer", "enum": [1, 2]},
                "loose_enum": {"enum": ["alpha", "beta"]},
                "empty_enum": {"type": "string", "enum": []},
                "bad_enum": {"type": "string", "enum": "not-a-list"},
            },
            "additionalProperties": False,
        },
        handler=noop,
    )
    registry = ToolRegistry()
    registry.register(spec)

    assert registry.validate_args(spec, {"kind": "habit"}) is None
    assert registry.validate_args(spec, {"level": 1}) is None
    assert registry.validate_args(spec, {"level": 2.0}) is None
    assert registry.validate_args(spec, {"loose_enum": "alpha"}) is None
    assert registry.validate_args(spec, {"empty_enum": "anything", "bad_enum": "x"}) is None

    kind_err = registry.validate_args(spec, {"kind": "other"})
    assert kind_err is not None
    assert "['habit', 'checkpoint']" in kind_err
    assert "'other'" in kind_err

    bool_err = registry.validate_args(spec, {"level": True})
    assert bool_err == "argument 'level' must be integer, got boolean"

    loose_err = registry.validate_args(spec, {"loose_enum": "gamma"})
    assert loose_err == "argument 'loose_enum' must be one of ['alpha', 'beta'], got 'gamma'"

    untyped_bool_spec = ToolSpec(
        name="untyped_num_enum",
        description="untyped enum",
        parameters={
            "type": "object",
            "properties": {"n": {"enum": [1, 2]}},
        },
        handler=noop,
    )
    assert registry.validate_args(untyped_bool_spec, {"n": True}) == (
        "argument 'n' must be one of [1, 2], got True"
    )


def test_memory_schemas_emit_enums_and_bounds_on_both_wire_formats(tmp_path: Path) -> None:
    _facade, registry, _gateway = _setup_gateway(tmp_path)
    chat_schemas = registry.to_openai_schema()
    by_name = {item["function"]["name"]: item["function"]["parameters"] for item in chat_schemas}
    assert set(by_name) == {
        "memory_search",
        "memory_get",
        "memory_note",
        "memory_task",
        "memory_forget",
    }

    for tool_name in ("memory_search", "memory_task"):
        props = by_name[tool_name]["properties"]
        assert props["status"]["enum"] == ["open", "done", "cancelled"], tool_name
    assert by_name["memory_search"]["properties"]["scope"]["enum"] == ["notes", "tasks"]

    responses_tools = _to_responses_tools(chat_schemas)
    resp_by_name = {item["name"]: item["parameters"] for item in responses_tools}
    assert resp_by_name["memory_search"]["properties"]["scope"]["enum"] == ["notes", "tasks"]
    assert resp_by_name["memory_task"]["properties"]["status"]["enum"] == [
        "open",
        "done",
        "cancelled",
    ]


@pytest.mark.asyncio
async def test_memory_tool_validation_contracts(tmp_path: Path) -> None:
    facade, _registry, gateway = _setup_gateway(tmp_path)

    bad_task_cases = [
        ({"topic": "g", "summary": "s", "memory_id": "2026-11-18#1234abcd"}, "memory_id requires task"),
        ({"summary": "s"}, "topic is required"),
        ({"topic": "g", "summary": "s", "status": "completed"}, "must be one of ['open', 'done', 'cancelled']"),
        ({"summary": "s", "task": "MyTask"}, "task-<8 hex>"),
    ]
    for args, expected_sub in bad_task_cases:
        res = await gateway.submit(ToolCall(id="bad_t", name="memory_task", arguments=args))
        assert res.is_error, args
        assert expected_sub in res.content, (args, res.content)

    bad_note_cases = [
        ({"topic": "t", "summary": "s", "habit": "   "}, "non-empty string"),
        ({"topic": "t", "summary": "s", "explicit": True}, "explicit=true requires habit"),
        ({"summary": "s"}, "topic is required"),
        ({"topic": "t"}, "summary is required"),
    ]
    for args, expected_sub in bad_note_cases:
        res = await gateway.submit(ToolCall(id="bad_n", name="memory_note", arguments=args))
        assert res.is_error, args
        assert expected_sub in res.content, (args, res.content)

    bad_search_cases = [
        ({"query": "   "}, "query must not be blank"),
        ({"query": "bazzite", "day": "yesterday"}, "YYYY-MM-DD"),
        ({"query": "bazzite", "task": "MyTask"}, "task-<8 hex>"),
        ({"query": "bazzite", "scope": "other"}, "['notes', 'tasks']"),
        ({"query": "bazzite", "status": "completed"}, "['open', 'done', 'cancelled']"),
    ]
    for args, expected_sub in bad_search_cases:
        res = await gateway.submit(ToolCall(id="bad_s", name="memory_search", arguments=args))
        assert res.is_error, args
        assert expected_sub in res.content, (args, res.content)

    clamped = await gateway.submit(
        ToolCall(id="lim0", name="memory_search", arguments={"query": "anything", "limit": 0})
    )
    assert not clamped.is_error
    assert json.loads(clamped.content)["warnings"] == ["limit clamped from 0 to 1"]
    assert facade.search("x", status="bogus").warnings == ["invalid status"]


def test_cross_day_other_journal_respects_polarity_conflict(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ_NAME)
    day1 = datetime(2026, 11, 10, 9, 0, tzinfo=TZ)
    day2 = datetime(2026, 11, 11, 9, 0, tzinfo=TZ)

    sid_yest = facade.append_other_journal("Python", "Dùng uv để chạy test", now=day1)
    assert sid_yest is not None

    sid_reversed = facade.append_other_journal("Python", "Đừng uv để chạy test", now=day2)
    assert sid_reversed is not None
    assert sid_reversed != sid_yest

    sid_day1_other = facade.append_other_journal("Docker", "Chạy postgres ở cổng 5432", now=day1)
    sid_day2_repeat = facade.append_other_journal("Docker", "Chạy postgres ở cổng 5432", now=day2)
    assert sid_day2_repeat == sid_day1_other


def test_agent_loop_self_recovers_from_search_enum_and_habit_and_update_errors(tmp_path: Path) -> None:
    facade, registry, gateway = _setup_gateway(tmp_path)
    yesterday = (datetime.now(TZ).date() - timedelta(days=1)).isoformat()
    facade.remember(
        "Fedora OS",
        "Bazzite work machine uses distrobox",
        now=datetime.fromisoformat(f"{yesterday}T10:00:00+07:00"),
    )
    facade.archive.reindex()

    def search_recovery(messages: list[Message], _tools, call_no: int) -> ChatReply:
        if call_no == 1:
            return ChatReply(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="s1",
                        name="memory_search",
                        arguments={"query": "Bazzite", "scope": "other"},
                    )
                ],
            )
        if call_no == 2:
            last_tool = messages[-1]
            assert last_tool.role == "tool"
            assert "['notes', 'tasks']" in (last_tool.content or "")
            return ChatReply(
                content=None,
                tool_calls=[ToolCall(id="s2", name="memory_search", arguments={"query": "Bazzite"})],
            )
        payload = json.loads(messages[-1].content or "{}")
        assert payload["hits"]
        return ChatReply(content="Máy chạy Fedora Bazzite với distrobox.")

    loop1 = _make_loop(tmp_path, gateway, registry, ScriptedConnect(search_recovery))
    reply1 = asyncio.run(loop1.run("Máy chạy OS gì?"))
    assert "Bazzite" in reply1

    def habit_and_update_recovery(messages: list[Message], _tools, call_no: int) -> ChatReply:
        if call_no == 1:
            return ChatReply(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="h1",
                        name="memory_note",
                        arguments={"topic": "Trà", "summary": "Uống trà ô long mỗi chiều", "explicit": True},
                    )
                ],
            )
        if call_no == 2:
            assert "explicit=true requires habit" in (messages[-1].content or "")
            return ChatReply(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="h2",
                        name="memory_note",
                        arguments={
                            "topic": "Trà",
                            "summary": "Uống trà ô long mỗi chiều",
                            "habit": "uống trà ô long",
                            "explicit": True,
                        },
                    )
                ],
            )
        if call_no == 3:
            sid = json.loads(messages[-1].content or "{}")["memory_id"]
            return ChatReply(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="u1",
                        name="memory_note",
                        arguments={"memory_id": sid, "content": "Pha lúc 15:00 không đường"},
                    )
                ],
            )
        if call_no == 4:
            assert "content requires summary" in (messages[-1].content or "")
            prev_calls = [
                tc for m in messages for tc in (m.tool_calls or []) if tc.name == "memory_note"
            ]
            sid = prev_calls[-1].arguments["memory_id"]
            return ChatReply(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="u2",
                        name="memory_note",
                        arguments={
                            "memory_id": sid,
                            "summary": "Uống trà ô long không đường mỗi chiều",
                            "content": "Pha lúc 15:00",
                        },
                    )
                ],
            )
        return ChatReply(content="Đã lưu và cập nhật thói quen trà ô long.")

    loop2 = _make_loop(tmp_path, gateway, registry, ScriptedConnect(habit_and_update_recovery), loop_max=6)
    reply2 = asyncio.run(loop2.run("Ghi giúp mình thói quen trà ô long lúc 15:00 không đường"))
    assert "trà ô long" in reply2
    entries = facade.today_entries()
    assert len(entries) == 1
    assert entries[0]["kind"] == "habit"
    assert entries[0]["habit"] == "uống trà ô long"
    assert entries[0]["habit_explicit"] is True
    assert "15:00" in entries[0]["body"]


def test_agent_loop_multichunk_search_followed_by_get_and_task_lifecycle(tmp_path: Path) -> None:
    yesterday = (datetime.now(TZ).date() - timedelta(days=1)).isoformat()
    (tmp_path / "memory").mkdir(parents=True, exist_ok=True)
    (tmp_path / "memory" / f"{yesterday}.md").write_text(
        f"# {yesterday}\n"
        '## 09:00 — OrionX service <!-- thyca {"id":"0a10a001","imp":4} -->\n'
        "- OrionX runs internal analytics pipelines across staging clusters.\n"
        "- gRPC port is 50443 and maintenance window starts at 02:30.\n",
        encoding="utf-8",
    )
    facade, registry, gateway = _setup_gateway(tmp_path)
    facade.archive.reindex()

    def search_then_get(messages: list[Message], _tools, call_no: int) -> ChatReply:
        if call_no == 1:
            return ChatReply(
                content=None,
                tool_calls=[ToolCall(id="s1", name="memory_search", arguments={"query": "OrionX"})],
            )
        if call_no == 2:
            payload = json.loads(messages[-1].content or "{}")
            hit = payload["hits"][0]
            assert hit["has_more"] is True
            assert hit["memory_leaf_count"] >= 2
            return ChatReply(
                content=None,
                tool_calls=[
                    ToolCall(id="g1", name="memory_get", arguments={"id": hit["memory_id"]})
                ],
            )
        full = messages[-1].content or ""
        assert "50443" in full and "02:30" in full
        return ChatReply(content="OrionX dùng cổng 50443, bảo trì lúc 02:30.")

    loop = _make_loop(tmp_path, gateway, registry, ScriptedConnect(search_then_get))
    ans = asyncio.run(loop.run("OrionX chạy cổng nào và bảo trì mấy giờ?"))
    assert "50443" in ans and "02:30" in ans

    state: dict[str, str] = {}

    def task_turns(messages: list[Message], _tools, _call_no: int) -> ChatReply:
        last = messages[-1]
        if last.role == "user" and "Tạo task" in (last.content or ""):
            return ChatReply(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="t1",
                        name="memory_task",
                        arguments={
                            "topic": "VNPAY webhook",
                            "summary": "Kiểm tra signature webhook VNPAY",
                        },
                    )
                ],
            )
        if last.role == "user" and "Cập nhật" in (last.content or ""):
            return ChatReply(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="t2",
                        name="memory_task",
                        arguments={
                            "topic": "VNPAY webhook",
                            "summary": "Xong signature; còn test timeout",
                            "task": state["task_id"],
                        },
                    )
                ],
            )
        if last.role == "user" and "Đóng task" in (last.content or ""):
            return ChatReply(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="t3",
                        name="memory_task",
                        arguments={
                            "summary": "Hoàn tất kiểm thử webhook VNPAY",
                            "task": state["task_id"],
                            "status": "done",
                        },
                    )
                ],
            )
        res = json.loads(last.content or "{}")
        state["task_id"] = res["task_id"]
        state["memory_id"] = res["memory_id"]
        return ChatReply(content=f"ok {state['task_id']}")

    loop_task = _make_loop(tmp_path, gateway, registry, ScriptedConnect(task_turns))
    asyncio.run(loop_task.run("Tạo task kiểm tra webhook VNPAY"))
    assert len(facade.tasks(status="open")["tasks"]) == 1
    asyncio.run(loop_task.run("Cập nhật task VNPAY: xong signature, còn timeout"))
    open_tasks = facade.tasks(status="open")["tasks"]
    assert len(open_tasks) == 1
    assert open_tasks[0]["entry_count"] == 1
    assert "timeout" in open_tasks[0]["summary"]
    asyncio.run(loop_task.run("Đóng task VNPAY vì xong hết rồi"))
    assert facade.tasks(status="open")["tasks"] == []
    done_tasks = facade.tasks(status="done")["tasks"]
    assert len(done_tasks) == 1
    assert done_tasks[0]["task_id"] == state["task_id"]


def test_active_prompt_renders_today_elsewhere_and_memory_tasks(tmp_path: Path) -> None:
    ensure_runtime_store(tmp_path)
    dm_here = ConversationManager(tmp_path / "sessions").ensure_dm("thyca")
    facade = MemoryFacade(tmp_path, timezone_name=TZ_NAME)
    now = datetime.now(TZ)

    sid_here = facade.remember("Local Redis", "Chạy cổng 6389", chat=dm_here.conversation_id, now=now)
    sid_else = facade.remember("Khách Kestrel", "Hợp đồng KST-774", chat="other-session-xyz", now=now)
    tid = facade.new_task_id()
    sid_task = facade.remember(
        "Triển khai CI",
        "Chờ review PR",
        task=tid,
        kind="checkpoint",
        status="open",
        chat=dm_here.conversation_id,
        now=now,
    )

    active = ActiveMemory(tmp_path, timezone_name=TZ_NAME)
    state = active.open_session(now)
    snap = active.refresh(state, now, conversation_id=dm_here.conversation_id)
    pm = PromptManager()

    snap = replace(snap, memory_tasks=format_task_catalog(facade.tasks(status="open", now=now)["tasks"]))
    prompt = pm.build(snap)

    assert "<today>" in prompt and sid_here in prompt and "6389" in prompt
    assert "<today_elsewhere>" in prompt and sid_else in prompt and "Khách Kestrel" in prompt
    assert "KST-774" not in snap.today_elsewhere
    assert "<memory_tasks>" in prompt and tid in prompt and sid_task in prompt
    assert "Never persist credentials or secrets." in prompt
