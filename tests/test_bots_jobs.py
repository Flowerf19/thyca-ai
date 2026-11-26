"""BE-4: Thyca memory sidecar job (tool-driven extract -> apply, idle decision)."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from thyca.core.protocol import Message, ToolCall
from thyca.llms.llm_base import ChatReply
from thyca.memories import jobs
from thyca.memories.facade import MemoryFacade
from thyca.memories.heading import parse_heading
from thyca.tools.memory_tools import sidecar_memory_tools


def test_should_idle_sweep() -> None:
    assert jobs.should_idle_sweep("2026-08-01T10:00:00Z", "2026-08-01T10:16:00Z") is True
    assert jobs.should_idle_sweep("2026-08-01T10:00:00Z", "2026-08-01T10:14:59Z") is False
    assert jobs.should_idle_sweep("bogus", "2026-08-01T10:16:00Z") is False
    assert jobs.should_idle_sweep(
        "2026-08-01T10:00:00Z", "2026-08-01T10:15:00Z", idle_s=900
    ) is True


def test_extract_memories_passes_builtin_note_and_task_tools(tmp_path: Path) -> None:
    class FakeConnect:
        def __init__(self) -> None:
            self.seen_messages: list = []
            self.seen_tools: list = []

        async def chat(self, messages, tools=None):
            self.seen_messages.append(messages)
            self.seen_tools.append(tools)
            return ChatReply(
                content="",
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="memory_note",
                        arguments={"topic": "gift", "summary": "egg coffee"},
                    )
                ],
            )

    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    connect = FakeConnect()
    out = asyncio.run(
        jobs.extract_memories(
            connect, facade, "user likes egg coffee", bot_id="coder", chat="room-1"
        )
    )
    assert len(out["written"]) == 1
    assert connect.seen_messages and isinstance(connect.seen_messages[0][0], Message)
    tool_names = [t["function"]["name"] for t in (connect.seen_tools[0] or [])]
    assert tool_names == ["memory_note", "memory_task"]

    day, entry_id = out["written"][0].split("#")
    meta = next(
        parsed
        for line in (tmp_path / "memory" / f"{day}.md").read_text(encoding="utf-8").splitlines()
        if (parsed := parse_heading(line)) is not None and parsed.entry_id == entry_id
    )
    assert meta.bots == ("coder",)
    assert meta.chat == "room-1"


def test_extract_memories_failure_respects_strict_flag(tmp_path: Path) -> None:
    class BadConnect:
        async def chat(self, messages, tools=None):
            raise RuntimeError("provider down")

    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    assert asyncio.run(jobs.extract_memories(BadConnect(), facade, "hi")) == {"written": []}
    with pytest.raises(jobs.ExtractionError):
        asyncio.run(jobs.extract_memories(BadConnect(), facade, "hi", strict=True))


def test_apply_memory_tool_calls_ignores_invalid_and_unknown_tools(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    registry = sidecar_memory_tools(facade)
    out = asyncio.run(
        jobs.apply_memory_tool_calls(
            registry,
            [
                ToolCall(id="x1", name="memory_forget", arguments={"memory_id": "2026-01-01#deadbeef"}),
                ToolCall(id="x2", name="memory_note", arguments={"topic": "", "summary": "bad"}),
                ToolCall(
                    id="x3",
                    name="memory_task",
                    arguments={"topic": "hallucinated", "summary": "s", "task": "task-99999999"},
                ),
                ToolCall(
                    id="x4",
                    name="memory_note",
                    arguments={"topic": "OS", "summary": "Fedora Bazzite"},
                ),
            ],
            bot_id="march7th",
            chat="room-m7",
        )
    )
    assert len(out["written"]) == 1
    day, entry_id = out["written"][0].split("#")
    meta = next(
        parsed
        for line in (tmp_path / "memory" / f"{day}.md").read_text(encoding="utf-8").splitlines()
        if (parsed := parse_heading(line)) is not None and parsed.entry_id == entry_id
    )
    assert meta.bots == ("march7th",)
    assert meta.title == "OS"


def test_apply_memory_tool_calls_updates_existing_task_and_groups_duplicate_new_tasks(
    tmp_path: Path,
) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    first = facade.remember(
        "step one", "alpha", importance=5, now=datetime(2026, 11, 1, 12, tzinfo=UTC)
    )
    catalog = [
        {
            "task_id": "task-aaaaaaaa",
            "title": "Goal",
            "latest_memory_id": first,
            "last_day": "2026-11-01",
        }
    ]
    registry = sidecar_memory_tools(facade)
    out = asyncio.run(
        jobs.apply_memory_tool_calls(
            registry,
            [
                ToolCall(
                    id="t1",
                    name="memory_task",
                    arguments={
                        "topic": "step two",
                        "summary": "beta",
                        "content": "- done: step one\n- processing: step two",
                        "task": "task-aaaaaaaa",
                    },
                )
            ],
            open_tasks=catalog,
        )
    )
    assert out["written"] == [first]
    day = first.split("#")[0]
    text = (tmp_path / "memory" / f"{day}.md").read_text(encoding="utf-8")
    assert text.count("## ") == 1
    meta = parse_heading(text.splitlines()[1])
    assert meta is not None and meta.task == "task-aaaaaaaa" and meta.status == "open"
    assert "beta" in text

    out2 = asyncio.run(
        jobs.apply_memory_tool_calls(
            registry,
            [
                ToolCall(
                    id="n1",
                    name="memory_task",
                    arguments={"topic": "Fix  Sink ", "summary": "one"},
                ),
                ToolCall(
                    id="n2",
                    name="memory_task",
                    arguments={"topic": "fix sink", "summary": "two", "status": "done"},
                ),
            ],
        )
    )
    assert len(out2["written"]) == 1
    day2 = out2["written"][0].split("#")[0]
    metas = [
        m
        for line in (tmp_path / "memory" / f"{day2}.md").read_text(encoding="utf-8").splitlines()
        if (m := parse_heading(line)) is not None
    ]
    assert len(metas) == 1
    assert metas[0].task and metas[0].task.startswith("task-")
    assert metas[0].status == "done"


def test_apply_memory_tool_calls_can_cancel_open_task_and_revise_same_day_note_inplace(
    tmp_path: Path,
) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    tid = facade.new_task_id()
    sid_task = facade.remember("Old migration", "Planned", task=tid, status="open", importance=5)
    sid_note = facade.append_other_journal(
        "Quy trình bản tin", "Bản nháp 1: gửi lúc 20h", chat="room-m7"
    )
    assert sid_note is not None
    catalog = [
        {"task_id": tid, "title": "Old migration", "latest_memory_id": sid_task, "last_day": sid_task.split("#")[0]}
    ]
    registry = sidecar_memory_tools(facade)
    out = asyncio.run(
        jobs.apply_memory_tool_calls(
            registry,
            [
                ToolCall(
                    id="c1",
                    name="memory_task",
                    arguments={
                        "topic": "Old migration",
                        "summary": "User cancelled the old migration plan",
                        "task": tid,
                        "status": "cancelled",
                    },
                ),
                ToolCall(
                    id="c2",
                    name="memory_note",
                    arguments={
                        "memory_id": sid_note,
                        "topic": "Quy trình bản tin",
                        "summary": "Bản chốt: gửi 4 dòng lúc 21h tối thứ Sáu",
                        "content": "",
                    },
                ),
            ],
            open_tasks=catalog,
            chat="room-m7",
        )
    )
    assert sid_task in out["written"]
    assert sid_note in out["written"]
    assert facade.tasks(status="open")["tasks"] == []
    assert [t["task_id"] for t in facade.tasks(status="cancelled")["tasks"]] == [tid]
    day = sid_note.split("#")[0]
    daily = (tmp_path / "memory" / f"{day}.md").read_text(encoding="utf-8")
    assert "Bản chốt: gửi 4 dòng lúc 21h tối thứ Sáu" in daily
    assert "Bản nháp 1" not in daily


def test_apply_memory_tool_calls_dedupes_habits_and_groups_other_journal(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    registry = sidecar_memory_tools(facade)
    out1 = asyncio.run(
        jobs.apply_memory_tool_calls(
            registry,
            [
                ToolCall(
                    id="a1",
                    name="memory_note",
                    arguments={"topic": "OS", "summary": "Máy chạy Fedora Bazzite"},
                ),
                ToolCall(
                    id="a2",
                    name="memory_note",
                    arguments={
                        "topic": "Tea",
                        "summary": "Uống trà sáng",
                        "habit": "uong tra sang",
                    },
                ),
            ],
            bot_id="coder",
            chat="room-1",
        )
    )
    out2 = asyncio.run(
        jobs.apply_memory_tool_calls(
            registry,
            [
                ToolCall(
                    id="b1",
                    name="memory_note",
                    arguments={"topic": "OS", "summary": "May chay Fedora Bazzite"},
                ),
                ToolCall(
                    id="b2",
                    name="memory_note",
                    arguments={"topic": "CI", "summary": "CI dùng Fedora container"},
                ),
                ToolCall(
                    id="b3",
                    name="memory_note",
                    arguments={
                        "topic": "Tea",
                        "summary": "Khẳng định thích trà sáng",
                        "habit": "Uống trà sáng",
                        "explicit": True,
                    },
                ),
            ],
            bot_id="coder",
            chat="room-1",
        )
    )
    assert len(out1["written"]) == 2
    assert out2["written"][0] == out1["written"][0]
    assert out2["written"][2] == out1["written"][1]
    assert out2["written"][1] not in set(out1["written"])
    day = out1["written"][0].split("#")[0]
    text = (tmp_path / "memory" / f"{day}.md").read_text(encoding="utf-8")
    metas = [m for line in text.splitlines() if (m := parse_heading(line)) is not None]
    assert len(metas) == 3
    assert text.count("Fedora Bazzite") == 1
    assert "CI dùng Fedora container" in text
    habit_meta = next(m for m in metas if m.kind == "habit")
    assert habit_meta.habit_explicit is True


def test_extraction_prompt_carries_language_lock_noise_filter_and_delta_rules() -> None:
    open_tasks = [
        {
            "task_id": "task-aaaaaaaa",
            "title": "Fix sink",
            "latest_memory_id": "2026-11-16#deadbeef",
            "last_day": "2026-11-16",
            "summary": "Bought washer",
        }
    ]
    today_entries = [
        {
            "memory_id": "2026-11-17#11111111",
            "title": "Tea",
            "kind": "habit",
            "habit": "uong tra",
            "body": "- likes morning tea",
        },
        {
            "memory_id": "2026-11-17#22222222",
            "title": "OS",
            "kind": None,
            "task": None,
            "body": "- Fedora Bazzite",
        },
    ]
    prompt = jobs.extraction_prompt(
        "hello",
        open_tasks=open_tasks,
        today_entries=today_entries,
        known_habits=["uong tra", "tra loi ngan"],
    )
    assert "Bought washer" in prompt
    assert "TODAY'S NOTES:" in prompt
    assert "TODAY'S CLOSED TASKS:" in prompt
    assert "2026-11-17#11111111" in prompt
    assert "Fedora Bazzite" in prompt
    assert "KNOWN HABIT LABELS: uong tra, tra loi ngan" in prompt
    assert "NEVER attach stable facts" in prompt
    assert "LANGUAGE:" in prompt
    assert "TWIN.md" in prompt
    assert "web-search" in prompt
    assert "recalled/cited from memory" in prompt
    assert "memory_id" in prompt
    assert '"bots":' not in prompt


def test_apply_memory_tool_calls_skips_recreating_task_closed_today(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    tid = facade.new_task_id()
    sid = facade.remember(
        "Dựng pipeline backup Postgres lên S3",
        "Hoàn thành 100%",
        task=tid,
        kind="checkpoint",
        status="done",
        importance=5,
        chat="thyca",
    )
    today = facade.today_entries(chat="thyca", reader="thyca")
    prompt = jobs.extraction_prompt("User: đóng task", today_entries=today)
    assert tid in prompt
    assert "TODAY'S CLOSED TASKS (ALREADY DONE/CANCELLED" in prompt

    registry = sidecar_memory_tools(facade)
    out = asyncio.run(
        jobs.apply_memory_tool_calls(
            registry,
            [
                ToolCall(
                    id="dup-done",
                    name="memory_task",
                    arguments={
                        "topic": "Dựng pipeline backup Postgres lên S3",
                        "summary": "Đã hoàn thành backup",
                        "status": "done",
                    },
                )
            ],
            bot_id="thyca",
            open_tasks=[],
            today_entries=today,
            chat="thyca",
        )
    )
    assert out["written"] == []
    assert [t["task_id"] for t in facade.tasks()["tasks"]] == [tid]
    assert sid in facade.today_entries(chat="thyca", reader="thyca")[0]["memory_id"]


def test_transcript_tail_skips_sidecars_and_keeps_room_context() -> None:
    messages = [
        Message(
            role="assistant",
            content="x",
            ts="2026-08-01T10:00:00Z",
            meta={"kind": "compaction"},
        ),
        Message(role="user", content="remember this", ts="2026-08-01T10:01:00Z"),
        Message(
            role="user",
            content="Tin CÔNG KHAI",
            ts="2026-08-01T10:02:00Z",
            meta={"kind": "room_context", "room_cursor": 2, "room_fingerprint": "abc"},
        ),
    ]
    tail = jobs.transcript_tail(messages)
    assert "remember this" in tail
    assert "CÔNG KHAI" in tail
    assert tail.count("[assistant") == 0


def test_transcript_tail_after_cursor_emits_delta_only() -> None:
    m1 = Message(role="user", content="old day 1", ts="2026-11-20T10:00:00Z")
    m2 = Message(role="assistant", content="ack day 1", ts="2026-11-20T10:01:00Z")
    m3 = Message(role="user", content="old day 2", ts="2026-11-23T10:00:00Z")
    m4 = Message(role="assistant", content="ack day 2", ts="2026-11-23T10:01:00Z")
    cursor = jobs.latest_extraction_cursor([m1, m2, m3, m4])
    assert cursor is not None
    assert jobs.transcript_tail([m1, m2, m3, m4], after_cursor=cursor) == ""

    m5 = Message(role="user", content="brand new today", ts="2026-11-25T09:00:00Z")
    tail = jobs.transcript_tail([m1, m2, m3, m4, m5], after_cursor=cursor)
    assert "RECENT CONTEXT (ALREADY EXTRACTED — DO NOT RE-EXTRACT):" in tail
    assert "NEW TRANSCRIPT:" in tail
    ctx_part, new_part = tail.split("NEW TRANSCRIPT:", 1)
    assert "old day 1" not in tail
    assert "old day 2" in ctx_part and "ack day 2" in ctx_part
    assert "brand new today" in new_part
    assert "old day 2" not in new_part

    after_compact = jobs.transcript_tail([m5], after_cursor=cursor)
    assert "brand new today" in after_compact
    assert "RECENT CONTEXT" not in after_compact

    stale_only = jobs.transcript_tail([m1, m2], after_cursor=cursor)
    assert stale_only == ""
