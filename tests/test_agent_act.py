from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

from thyca.agent.act import Act
from thyca.agent.events import TurnEvent
from thyca.agent.stage import Stage
from thyca.agent.think import ChatReply
from thyca.protocol import ToolCall, ToolResult


@dataclass
class FakeDispatcher:
    result: ToolResult | None = None
    error: Exception | None = None
    calls: list[ToolCall] = field(default_factory=list)

    async def dispatch(self, call: ToolCall) -> ToolResult:
        self.calls.append(call)
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


def _stage(call: ToolCall) -> Stage:
    return Stage(reply=ChatReply(content=None, tool_calls=[call]))


def _stage_round(call: ToolCall) -> Stage:
    stage = _stage(call)
    stage.round = 1
    return stage


def test_parse_error_skips_dispatch() -> None:
    dispatcher = FakeDispatcher()
    call = ToolCall(id="call-1", name="weather", parse_error="invalid arguments")

    results = asyncio.run(Act(dispatcher).act(_stage(call)))

    assert dispatcher.calls == []
    assert results == [
        ToolResult(
            tool_call_id="call-1",
            name="weather",
            content="invalid arguments",
            is_error=True,
        )
    ]


def _event_pairs(events: list[TurnEvent]) -> list[tuple[str, bool | None]]:
    return [(event.type, event.ok) for event in events]


def test_parse_error_emits_started_then_finished_ok_false() -> None:
    dispatcher = FakeDispatcher()
    call = ToolCall(id="call-1", name="weather", parse_error="invalid arguments")
    events: list[TurnEvent] = []

    results = asyncio.run(Act(dispatcher).act(_stage_round(call), event_sink=events.append))

    assert _event_pairs(events) == [
        ("tool.started", None),
        ("tool.finished", False),
    ]
    started, finished = events
    assert (started.type, started.round, started.call_id, started.name) == (
        "tool.started",
        1,
        "call-1",
        "weather",
    )
    assert (finished.round, finished.call_id, finished.name) == (1, "call-1", "weather")
    assert results[0].is_error is True


def test_happy_path_emits_started_then_finished_ok_true() -> None:
    dispatcher = FakeDispatcher(
        result=ToolResult(tool_call_id="call-1", name="weather", content="sunny")
    )
    call = ToolCall(id="call-1", name="weather")
    events: list[TurnEvent] = []

    results = asyncio.run(Act(dispatcher).act(_stage_round(call), event_sink=events.append))

    assert _event_pairs(events) == [
        ("tool.started", None),
        ("tool.finished", True),
    ]
    assert results[0].is_error is False


def test_dispatch_exception_emits_started_then_finished_ok_false() -> None:
    dispatcher = FakeDispatcher(error=RuntimeError("tool unavailable"))
    call = ToolCall(id="call-1", name="weather")
    events: list[TurnEvent] = []

    asyncio.run(Act(dispatcher).act(_stage_round(call), event_sink=events.append))

    assert _event_pairs(events) == [
        ("tool.started", None),
        ("tool.finished", False),
    ]


def test_dispatcher_error_result_emits_finished_ok_false() -> None:
    dispatcher = FakeDispatcher(
        result=ToolResult(
            tool_call_id="call-1", name="weather", content="rate limited", is_error=True
        )
    )
    call = ToolCall(id="call-1", name="weather")
    events: list[TurnEvent] = []

    asyncio.run(Act(dispatcher).act(_stage_round(call), event_sink=events.append))

    assert _event_pairs(events) == [
        ("tool.started", None),
        ("tool.finished", False),
    ]


def test_sink_raise_still_returns_same_tool_result() -> None:
    dispatcher = FakeDispatcher(
        result=ToolResult(tool_call_id="call-1", name="weather", content="sunny")
    )
    call = ToolCall(id="call-1", name="weather")

    def boom(_event: TurnEvent) -> None:
        raise RuntimeError("sink exploded")

    results = asyncio.run(Act(dispatcher).act(_stage_round(call), event_sink=boom))

    assert results[0].tool_call_id == "call-1"
    assert results[0].content == "sunny"
    assert results[0].is_error is False


def test_no_sink_emits_no_events() -> None:
    dispatcher = FakeDispatcher(
        result=ToolResult(tool_call_id="call-1", name="weather", content="sunny")
    )
    call = ToolCall(id="call-1", name="weather")
    stage = _stage(call)
    stage.round = 0

    asyncio.run(Act(dispatcher).act(stage))

    assert dispatcher.calls == [call]
    assert stage.results[0].is_error is False


def test_parallel_finished_in_completion_order() -> None:
    async def fast(_call: ToolCall) -> ToolResult:
        return ToolResult(tool_call_id="call-1", name="fast", content="two")

    async def slow(_call: ToolCall) -> ToolResult:
        await asyncio.sleep(0.02)
        return ToolResult(tool_call_id="call-1", name="slow", content="one")

    class TwoDispatcher:
        async def dispatch(self, call: ToolCall) -> ToolResult:
            if call.name == "fast":
                return await fast(call)
            return await slow(call)

    slow_call = ToolCall(id="slow", name="slow")
    fast_call = ToolCall(id="fast", name="fast")
    stage = Stage(reply=ChatReply(content=None, tool_calls=[slow_call, fast_call]))
    stage.round = 1
    events: list[TurnEvent] = []

    asyncio.run(Act(TwoDispatcher()).act(stage, event_sink=events.append))

    assert [(event.type, event.name) for event in events] == [
        ("tool.started", "slow"),
        ("tool.started", "fast"),
        ("tool.finished", "fast"),
        ("tool.finished", "slow"),
    ]
    assert [event.round for event in events] == [1, 1, 1, 1]


def test_happy_path_preserves_call_identity() -> None:
    dispatcher = FakeDispatcher(
        result=ToolResult(tool_call_id="wrong-id", name="wrong-name", content="sunny")
    )
    call = ToolCall(id="call-1", name="weather")
    stage = _stage(call)

    asyncio.run(Act(dispatcher).act(stage))

    assert dispatcher.calls == [call]
    result = stage.results[0]
    assert result.content == "sunny"
    assert result.is_error is False
    assert result.tool_call_id == call.id
    assert result.name == call.name


def test_dispatch_exception_becomes_error_result() -> None:
    dispatcher = FakeDispatcher(error=RuntimeError("tool unavailable"))
    call = ToolCall(id="call-1", name="weather")
    stage = _stage(call)

    asyncio.run(Act(dispatcher).act(stage))

    result = stage.results[0]
    assert result.tool_call_id == call.id
    assert result.name == call.name
    assert result.content == "tool unavailable"
    assert result.is_error is True


def _skill_call(tmp_path: Path, path: str | None = "skills") -> ToolCall:
    skill = tmp_path / "skills" / "create-skill"
    skill.mkdir(parents=True, exist_ok=True)
    target = skill / "SKILL.md"
    target.write_text("---\nname: create-skill\n---\n")
    args = {"path": str(target)} if path == "skills" else {"path": path}
    return ToolCall(id="call-1", name="read", arguments=args)


def test_skill_read_emits_skill_events_not_tool(tmp_path: Path) -> None:
    dispatcher = FakeDispatcher(
        result=ToolResult(tool_call_id="call-1", name="read", content="---")
    )
    events: list[TurnEvent] = []

    asyncio.run(
        Act(dispatcher, skills_root=tmp_path / "skills").act(
            _stage_round(_skill_call(tmp_path)), event_sink=events.append
        )
    )

    assert [(event.type, event.name, event.ok) for event in events] == [
        ("skill.started", "create-skill", None),
        ("skill.finished", "create-skill", True),
    ]


def test_plain_read_keeps_tool_events(tmp_path: Path) -> None:
    dispatcher = FakeDispatcher(
        result=ToolResult(tool_call_id="call-1", name="read", content="text")
    )
    plain = ToolCall(
        id="call-1", name="read", arguments={"path": str(tmp_path / "notes.md")}
    )
    events: list[TurnEvent] = []

    asyncio.run(
        Act(dispatcher, skills_root=tmp_path / "skills").act(
            _stage_round(plain), event_sink=events.append
        )
    )

    assert [event.type for event in events] == ["tool.started", "tool.finished"]


def test_parse_error_read_keeps_tool_events(tmp_path: Path) -> None:
    dispatcher = FakeDispatcher()
    call = ToolCall(
        id="call-1", name="read", arguments={}, parse_error="invalid arguments"
    )
    events: list[TurnEvent] = []

    asyncio.run(
        Act(dispatcher, skills_root=tmp_path / "skills").act(
            _stage_round(call), event_sink=events.append
        )
    )

    assert [event.type for event in events] == ["tool.started", "tool.finished"]


def test_skill_read_outside_root_keeps_tool_events(tmp_path: Path) -> None:
    dispatcher = FakeDispatcher(
        result=ToolResult(tool_call_id="call-1", name="read", content="text")
    )
    outside = ToolCall(
        id="call-1", name="read", arguments={"path": str(tmp_path / "other.md")}
    )
    events: list[TurnEvent] = []

    asyncio.run(
        Act(dispatcher, skills_root=tmp_path / "skills").act(
            _stage_round(outside), event_sink=events.append
        )
    )

    assert [event.type for event in events] == ["tool.started", "tool.finished"]


def test_skill_finished_error_emits_ok_false(tmp_path: Path) -> None:
    dispatcher = FakeDispatcher(error=FileNotFoundError("not a file"))
    events: list[TurnEvent] = []

    asyncio.run(
        Act(dispatcher, skills_root=tmp_path / "skills").act(
            _stage_round(_skill_call(tmp_path)), event_sink=events.append
        )
    )

    assert [(event.type, event.name, event.ok) for event in events] == [
        ("skill.started", "create-skill", None),
        ("skill.finished", "create-skill", False),
    ]


def test_skill_event_sink_raise_still_returns_result(tmp_path: Path) -> None:
    dispatcher = FakeDispatcher(
        result=ToolResult(tool_call_id="call-1", name="read", content="---")
    )

    def boom(_event: TurnEvent) -> None:
        raise RuntimeError("sink exploded")

    results = asyncio.run(
        Act(dispatcher, skills_root=tmp_path / "skills").act(
            _stage_round(_skill_call(tmp_path)), event_sink=boom
        )
    )

    assert results[0].content == "---"
    assert results[0].is_error is False


def test_skill_dir_write_edit_bash_stay_tool_events(tmp_path: Path) -> None:
    skill_file = tmp_path / "skills" / "create-skill" / "SKILL.md"
    skill_file.parent.mkdir(parents=True, exist_ok=True)
    skill_file.write_text("---\nname: create-skill\n---\n")
    events: list[TurnEvent] = []
    for tool, args in (
        ("write", {"path": str(skill_file), "content": "x"}),
        ("edit", {"path": str(skill_file)}),
        ("bash", {"command": f"cat {skill_file}"}),
    ):
        dispatcher = FakeDispatcher(
            result=ToolResult(tool_call_id="call-1", name=tool, content="ok")
        )
        asyncio.run(
            Act(dispatcher, skills_root=tmp_path / "skills").act(
                _stage_round(ToolCall(id="call-1", name=tool, arguments=args)),
                event_sink=events.append,
            )
        )
    assert [event.type for event in events] == [
        "tool.started", "tool.finished",
    ] * 3
    assert all(event.name in {"write", "edit", "bash"} for event in events)


def test_no_skills_root_keeps_tool_events_for_skill_path(tmp_path: Path) -> None:
    dispatcher = FakeDispatcher(
        result=ToolResult(tool_call_id="call-1", name="read", content="---")
    )
    events: list[TurnEvent] = []

    asyncio.run(
        Act(dispatcher).act(
            _stage_round(_skill_call(tmp_path)), event_sink=events.append
        )
    )

    assert [event.type for event in events] == ["tool.started", "tool.finished"]
    assert events[0].name == "read"


def test_skill_events_wire_never_carries_path(tmp_path: Path) -> None:
    dispatcher = FakeDispatcher(
        result=ToolResult(tool_call_id="call-1", name="read", content="---")
    )
    events: list[TurnEvent] = []

    asyncio.run(
        Act(dispatcher, skills_root=tmp_path / "skills").act(
            _stage_round(_skill_call(tmp_path)), event_sink=events.append
        )
    )

    skill_path = str(tmp_path / "skills" / "create-skill" / "SKILL.md")
    for event in events:
        payload = event.to_dict()
        assert set(payload) <= {"type", "round", "call_id", "name", "ok"}
        assert skill_path not in json.dumps(payload)
        assert "create-skill/SKILL.md" not in json.dumps(payload)
