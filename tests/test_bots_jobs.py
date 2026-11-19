"""BE-4: Thyca memory sidecar job (extract → apply, idle decision)."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from thyca.core.protocol import Message
from thyca.memories import jobs
from thyca.memories.facade import MemoryFacade


def test_should_idle_sweep() -> None:
    assert jobs.should_idle_sweep("2026-08-01T10:00:00Z", "2026-08-01T10:16:00Z") is True
    assert jobs.should_idle_sweep("2026-08-01T10:00:00Z", "2026-08-01T10:14:59Z") is False
    assert jobs.should_idle_sweep("bogus", "2026-08-01T10:16:00Z") is False
    assert jobs.should_idle_sweep("2026-08-01T10:00:00Z", "2026-08-01T10:15:00Z",
                                  idle_s=900) is True


def test_parse_extraction_drops_invalid_items() -> None:
    items = jobs.parse_extraction(
        '[{"topic": "t", "summary": "s", "importance": 9, "bots": ["coder"]}, '
        '{"topic": "", "summary": "x"}, "nope"]'
    )
    assert len(items) == 1
    assert items[0].importance == 3
    assert items[0].bots == ("coder",)
    assert jobs.parse_extraction("not json") == []
    assert jobs.parse_extraction('{"topic": "t"}') == []


def test_apply_extracted_writes_private(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    out = jobs.apply_extracted(
        facade,
        [jobs.ExtractedMemory(topic="gift", summary="egg coffee",
                              bots=("coder",))],
    )
    assert len(out["written"]) == 1
    assert "proposed" not in out
    # Written private, never team-direct (read the heading: today is unindexed).
    sid = out["written"][0]
    day = sid.split("#")[0]
    owners = None
    for line in (tmp_path / "memory" / f"{day}.md").read_text(encoding="utf-8").splitlines():
        from thyca.memories.heading import parse_heading as _parse

        meta = _parse(line)
        if meta is not None and meta.entry_id == sid.split("#")[1]:
            owners = meta.bots
    assert owners == ("coder",)


def test_apply_extracted_stamps_origin_chat(tmp_path: Path) -> None:
    from thyca.memories.heading import parse_heading

    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    out = jobs.apply_extracted(
        facade,
        [jobs.ExtractedMemory(topic="origin", summary="origin-token",
                              bots=("coder",))],
        chat="2026-10-19T14-14-58_b12b",
    )
    sid = out["written"][0]
    day, entry_id = sid.split("#")
    meta = next(
        parsed
        for line in (tmp_path / "memory" / f"{day}.md").read_text(
            encoding="utf-8").splitlines()
        if (parsed := parse_heading(line)) is not None
        and parsed.entry_id == entry_id
    )
    assert meta.chat == "2026-10-19T14-14-58_b12b"


def test_extract_from_transcript_uses_model_json(tmp_path: Path) -> None:
    import asyncio
    import json

    class Reply:
        def __init__(self, content: str) -> None:
            self.content = content

    class FakeConnect:
        def __init__(self) -> None:
            self.seen: list = []

        async def chat(self, messages, tools=None):
            self.seen.append(messages)
            return Reply(json.dumps([{"topic": "t", "summary": "s"}]))

    connect = FakeConnect()
    items = asyncio.run(jobs.extract_from_transcript(connect, "m", "hello"))
    assert len(items) == 1
    assert connect.seen and isinstance(connect.seen[0][0], Message)


def test_extract_failure_is_empty_not_loud() -> None:
    import asyncio

    class BadConnect:
        async def chat(self, messages, tools=None):
            raise RuntimeError("provider down")

    assert asyncio.run(jobs.extract_from_transcript(BadConnect(), "m", "hi")) == []


def test_transcript_tail_skips_sidecars() -> None:
    messages = [
        Message(role="assistant", content="x", ts="2026-08-01T10:00:00Z",
                meta={"kind": "compaction"}),
        Message(role="user", content="remember this", ts="2026-08-01T10:01:00Z"),
    ]
    tail = jobs.transcript_tail(messages)
    assert "remember this" in tail
    assert tail.count("[assistant") == 0


def test_transcript_tail_treats_naming_as_ordinary_text() -> None:
    messages = [
        Message(role="assistant", content="x", ts="2026-08-01T10:00:00Z",
                meta={"kind": "naming"}),
        Message(role="user", content="remember this", ts="2026-08-01T10:01:00Z"),
    ]
    tail = jobs.transcript_tail(messages)
    assert "remember this" in tail
    assert tail.count("[assistant") == 1


# --- Phase 1C: task/habit linkage -------------------------------------------

def _open_catalog():
    return [{"task_id": "task-aaaaaaaa", "title": "Fix sink",
             "latest_memory_id": "2026-09-01#deadbeef", "last_day": "2026-09-01"}]


def test_parse_extraction_keeps_all_six_side_fields() -> None:
    import json as _json

    items = jobs.parse_extraction(_json.dumps([{
        "topic": "t", "summary": "s", "task": "task-aaaaaaaa",
        "cont": "2026-09-01#deadbeef", "kind": "habit",
        "status": "open", "habit": "uong tra buoi sang",
    }]))
    assert len(items) == 1
    item = items[0]
    assert (item.task, item.cont, item.kind, item.status, item.habit) == (
        "task-aaaaaaaa", "2026-09-01#deadbeef", "habit", "open", "uong tra buoi sang")
    assert item.new_task is None


def test_parse_extraction_sanitizes_side_fields_but_keeps_memory() -> None:
    import json as _json

    items = jobs.parse_extraction(_json.dumps([
        {"topic": "t", "summary": "s", "task": "bogus",
         "status": "eventually", "kind": "project",
         "habit": "  ", "cont": 42},
        {"topic": "t2", "summary": "s2", "task": "task-bbbbbbbb",
         "new_task": "Also new"},
        {"topic": "t3", "summary": "s3", "kind": "habit"},
        {"topic": "t4", "summary": "s4", "task": "task-cccccccc",
         "kind": "checkpoint"},
    ]))
    assert [(i.topic, i.task, i.new_task, i.kind, i.status, i.habit) for i in items] == [
        ("t", None, None, None, None, None),
        ("t2", None, None, None, None, None),
        ("t3", None, None, None, None, None),
        ("t4", "task-cccccccc", None, None, None, None),
    ]


def test_extract_prompt_lists_open_tasks() -> None:
    import asyncio
    import json as _json

    seen: list = []

    class FakeConnect:
        async def chat(self, messages, tools=None):
            seen.append(messages)
            return type("R", (), {"content": "[]"})()

    asyncio.run(jobs.extract_from_transcript(
        FakeConnect(), "m", "hello", open_tasks=_open_catalog()))
    prompt = seen[0][0].content
    assert "OPEN MEMORY TASKS" in prompt
    assert "task-aaaaaaaa" in prompt
    assert "2026-09-01#deadbeef" in prompt


def test_extract_prompt_empty_catalog_block() -> None:
    import asyncio

    seen: list = []

    class FakeConnect:
        async def chat(self, messages, tools=None):
            seen.append(messages)
            return type("R", (), {"content": "[]"})()

    asyncio.run(jobs.extract_from_transcript(FakeConnect(), "m", "hello"))
    assert "OPEN MEMORY TASKS: none." in seen[0][0].content


def test_apply_continues_open_task_with_latest_cont(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    first = facade.remember(
        "step one", "alpha", importance=5,
        now=datetime(2026, 11, 1, 12, tzinfo=UTC),
    )
    catalog = [{"task_id": "task-aaaaaaaa", "title": "Goal",
                "latest_memory_id": first, "last_day": "2026-11-01"}]
    out = jobs.apply_extracted(
        facade,
        [jobs.ExtractedMemory(topic="step two", summary="beta",
                              content="- done: step one\n- processing: step two",
                              task="task-aaaaaaaa")],
        open_tasks=catalog,
    )
    from thyca.memories.heading import parse_heading as _parse

    assert out["written"] == [first]
    day = first.split("#")[0]
    text = (tmp_path / "memory" / f"{day}.md").read_text(encoding="utf-8")
    assert text.count("## ") == 1
    meta = _parse(text.splitlines()[1])
    assert meta is not None and meta.task == "task-aaaaaaaa"
    assert meta.kind == "checkpoint"
    assert meta.status == "open"
    assert "beta" in text


def test_apply_groups_new_task_titles_and_chains_cont(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    out = jobs.apply_extracted(
        facade,
        [jobs.ExtractedMemory(topic="Fix sink", summary="one", new_task="Fix  Sink "),
         jobs.ExtractedMemory(topic="progress", summary="two", new_task="fix sink",
                              status="done")],
    )
    assert len(out["written"]) == 1
    from thyca.memories.heading import parse_heading as _parse

    day = out["written"][0].split("#")[0]
    text = (tmp_path / "memory" / f"{day}.md").read_text(encoding="utf-8")
    metas = [_parse(line) for line in text.splitlines() if _parse(line)]
    assert len(metas) == 1
    assert metas[0].task and metas[0].task.startswith("task-")
    assert metas[0].title == "Fix sink"
    assert metas[0].status == "done"
    assert "two" in text


def test_apply_drops_task_outside_catalog_but_keeps_habit(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    out = jobs.apply_extracted(
        facade,
        [jobs.ExtractedMemory(topic="t", summary="s", task="task-99999999",
                              status="done", cont="2026-09-01#deadbeef",
                              kind="habit", habit="uong tra")],
        open_tasks=_open_catalog(),
    )
    from thyca.memories.heading import parse_heading as _parse

    day = out["written"][0].split("#")[0]
    meta = _parse(open(tmp_path / "memory" / f"{day}.md",
                       encoding="utf-8").read().splitlines()[-2])
    assert meta is not None
    assert meta.task is None and meta.status is None and meta.cont is None
    assert (meta.kind, meta.habit) == ("habit", "uong tra")


def test_apply_done_status_and_habit_land_in_store(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    first = facade.remember("step one", "alpha", now=datetime(2026, 9, 1, 12, tzinfo=UTC))
    catalog = [{"task_id": "task-aaaaaaaa", "title": "Goal",
                "latest_memory_id": first, "last_day": "2026-09-01"}]
    out = jobs.apply_extracted(
        facade,
        [jobs.ExtractedMemory(topic="step two", summary="beta",
                              task="task-aaaaaaaa", status="done",
                              kind="habit", habit="review hang ngay")],
        open_tasks=catalog,
    )
    # Today is unindexed by design: assert the heading on disk plus the
    # live catalog (which merges today's file).
    from thyca.memories.heading import parse_heading as _parse

    day = out["written"][0].split("#")[0]
    meta = _parse(open(tmp_path / "memory" / f"{day}.md",
                       encoding="utf-8").read().splitlines()[-2])
    assert meta is not None
    assert (meta.task, meta.status, meta.kind, meta.habit) == (
        "task-aaaaaaaa", "done", "habit", "review hang ngay")
    by_id = {t["task_id"]: t for t in facade.tasks()["tasks"]}
    assert by_id["task-aaaaaaaa"]["status"] == "done"


def test_transcript_tail_treats_room_context_rows_as_ordinary_text() -> None:
    # Canonical-only turns do not persist synthetic room_context rows; the
    # transcript extractor no longer carries the old kind-based skip.
    tail = jobs.transcript_tail([
        Message(role="user", content="hi", ts="2026-10-16T10:00:00Z"),
        Message(role="user", content="Tin CÔNG KHAI", ts="2026-10-16T10:01:00Z",
                meta={"kind": "room_context", "room_cursor": 2,
                      "room_fingerprint": "abc"}),
    ])
    assert "hi" in tail
    assert "CÔNG KHAI" in tail


def test_extract_prompt_carries_today_notes_and_delta_rules() -> None:
    import asyncio

    seen: list = []

    class FakeConnect:
        async def chat(self, messages, tools=None):
            seen.append(messages)
            return type("R", (), {"content": "[]"})()

    open_tasks = [{
        "task_id": "task-aaaaaaaa",
        "title": "Fix sink",
        "latest_memory_id": "2026-11-16#deadbeef",
        "last_day": "2026-11-16",
        "summary": "Bought washer",
    }]
    today_entries = [
        {"memory_id": "2026-11-17#11111111", "title": "Tea", "kind": "habit",
         "habit": "uong tra", "body": "- likes morning tea"},
        {"memory_id": "2026-11-17#22222222", "title": "OS", "kind": None,
         "task": None, "body": "- Fedora Bazzite"},
    ]
    asyncio.run(jobs.extract_from_transcript(
        FakeConnect(), "m", "hello", open_tasks=open_tasks,
        today_entries=today_entries, known_habits=["uong tra", "tra loi ngan"],
    ))
    prompt = seen[0][0].content
    assert "Bought washer" in prompt
    assert "TODAY'S NOTES:" in prompt
    assert "2026-11-17#11111111" in prompt
    assert "Fedora Bazzite" in prompt
    assert "KNOWN HABIT LABELS: uong tra, tra loi ngan" in prompt
    assert "NEVER attach stable facts" in prompt
    assert "cancelled" in prompt


def test_apply_extracted_dedupes_habits_and_groups_other_journal(tmp_path: Path) -> None:
    from thyca.memories.heading import parse_heading as _parse

    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    out1 = jobs.apply_extracted(
        facade,
        [
            jobs.ExtractedMemory(topic="OS", summary="Máy chạy Fedora Bazzite"),
            jobs.ExtractedMemory(topic="Tea", summary="Uống trà sáng",
                                 kind="habit", habit="uong tra sang"),
        ],
        default_bot="coder",
        chat="room-1",
    )
    out2 = jobs.apply_extracted(
        facade,
        [
            jobs.ExtractedMemory(topic="OS", summary="May chay Fedora Bazzite"),
            jobs.ExtractedMemory(topic="CI", summary="CI dùng Fedora container"),
            jobs.ExtractedMemory(topic="Tea", summary="Khẳng định thích trà sáng",
                                 kind="habit", habit="Uống trà sáng", habit_explicit=True),
        ],
        default_bot="coder",
        chat="room-1",
    )
    assert len(out1["written"]) == 2
    assert set(out2["written"]) == set(out1["written"])
    day = out1["written"][0].split("#")[0]
    text = (tmp_path / "memory" / f"{day}.md").read_text(encoding="utf-8")
    metas = [_parse(line) for line in text.splitlines() if _parse(line)]
    assert len(metas) == 2
    assert text.count("Fedora Bazzite") == 1
    assert "CI dùng Fedora container" in text
    habit_meta = next(m for m in metas if m.kind == "habit")
    assert habit_meta.habit_explicit is True


def test_apply_extracted_can_cancel_open_task(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    tid = facade.new_task_id()
    sid = facade.remember("Old migration", "Planned", task=tid, status="open", importance=5)
    catalog = [{"task_id": tid, "title": "Old migration",
                "latest_memory_id": sid, "last_day": sid.split("#")[0]}]
    out = jobs.apply_extracted(
        facade,
        [jobs.ExtractedMemory(
            topic="Old migration",
            summary="User cancelled the old migration plan",
            task=tid,
            status="cancelled",
        )],
        open_tasks=catalog,
    )
    assert out["written"] == [sid]
    assert facade.tasks(status="open")["tasks"] == []
    assert [t["task_id"] for t in facade.tasks(status="cancelled")["tasks"]] == [tid]


def test_parse_extraction_strips_markdown_code_fence() -> None:
    fenced_json = '```json\n[{"topic": "OS", "summary": "Fedora Bazzite"}]\n```'
    fenced_plain = '```\n[{"topic": "OS", "summary": "Fedora Bazzite"}]\n```'
    for raw in (fenced_json, fenced_plain):
        items = jobs.parse_extraction(raw)
        assert len(items) == 1
        assert items[0].topic == "OS"
        assert items[0].summary == "Fedora Bazzite"
