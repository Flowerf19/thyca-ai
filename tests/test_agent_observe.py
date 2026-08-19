from __future__ import annotations

from pathlib import Path

import pytest

from thyca.agent.observe import Observe
from thyca.agent.stage import Stage
from thyca.agent.think import ChatReply
from thyca.protocol import Message, ToolCall, ToolResult
from thyca.sessions import SessionManager


def test_observe_reorders_results_and_extends_stage(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    manager.create()
    first = ToolCall(id="first", name="one")
    second = ToolCall(id="second", name="two")
    stage = Stage(
        messages=[Message(role="user", content="go", ts="2026-01-01T00:00:00Z")],
        reply=ChatReply(content=None, tool_calls=[first, second]),
        results=[
            ToolResult(tool_call_id="second", name="two", content="2"),
            ToolResult(tool_call_id="first", name="one", content="1"),
        ],
    )
    # persist user first so session isn't only the tool round
    manager.append(stage.messages[0])

    Observe(manager).observe(stage)

    assert [m.role for m in stage.messages] == ["user", "assistant", "tool", "tool"]
    assert [m.tool_call_id for m in stage.messages[2:]] == ["first", "second"]
    assert [r.tool_call_id for r in stage.results] == ["first", "second"]
    assert [m.role for m in manager.current.messages] == ["user", "assistant", "tool", "tool"]


@pytest.mark.parametrize(
    "results",
    [
        [ToolResult(tool_call_id="other", name="tool", content="result")],
        [],
        [
            ToolResult(tool_call_id="first", name="tool", content="one"),
            ToolResult(tool_call_id="first", name="tool", content="two"),
        ],
    ],
)
def test_observe_rejects_invalid_result_ids(
    tmp_path: Path, results: list[ToolResult]
) -> None:
    manager = SessionManager(tmp_path)
    manager.create()
    call = ToolCall(id="first", name="tool")
    stage = Stage(
        reply=ChatReply(content=None, tool_calls=[call]),
        results=results,
    )

    with pytest.raises(ValueError):
        Observe(manager).observe(stage)

    assert manager.current.messages == []


def test_loop_limit_persists_exact_text(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    manager.create()
    stage = Stage()

    assert Observe(manager).loop_limit(stage) == "loop limit reached"
    assert manager.current.messages[-1].content == "loop limit reached"
    assert stage.messages[-1].content == "loop limit reached"


def test_compact_delegates_to_session_manager(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    manager.create()
    called: list[bool] = []

    def compact() -> bool:
        called.append(True)
        return True

    manager.compact_if_needed = compact  # type: ignore[method-assign]

    assert Observe(manager).compact() is True
    assert called == [True]
