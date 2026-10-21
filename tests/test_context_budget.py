from __future__ import annotations

import pytest

from thyca.agent.context_budget import checkpoint_state, format_context_budget
from thyca.core.protocol import Message, ToolCall


def _usage(ratio: float) -> dict:
    return {
        "estimated_tokens": round(ratio * 100_000),
        "context_tokens": 100_000,
        "ratio": ratio,
    }


@pytest.mark.parametrize(
    ("ratio", "state"),
    [(0.42, "roomy"), (0.83, "prepare"), (0.92, "mandatory"), (0.97, "backstop")],
)
def test_format_context_budget_states(ratio: float, state: str) -> None:
    text = format_context_budget(_usage(ratio), checkpoint="none")
    assert f"used={round(ratio * 100)}%" in text
    assert f"state={state}" in text
    assert "checkpoint=none" in text
    assert "kind='checkpoint'" in text


def test_format_context_budget_recorded_and_stale() -> None:
    recorded = format_context_budget(_usage(0.83), checkpoint="recorded")
    assert "checkpoint=recorded" in recorded
    assert "update it only when" in recorded
    stale = format_context_budget(_usage(0.92), checkpoint="stale")
    assert "checkpoint=stale" in stale
    assert "Refresh the checkpoint now" in stale


def test_format_context_budget_without_write_tool() -> None:
    text = format_context_budget(_usage(0.83), checkpoint="none", can_write=False)
    assert "unavailable for this bot" in text
    assert "call memory_remember" not in text


def test_format_context_budget_without_read_tool() -> None:
    text = format_context_budget(_usage(0.83), checkpoint="none", can_read=False)
    assert "search memory first" not in text
    assert "already in context" in text
    assert "kind='checkpoint'" in text


def _write_pair(
    *,
    prompt_tokens: int,
    kind: str | None = "checkpoint",
    task: str | None = "task-1234abcd",
    new_task: bool = False,
    tool_name: str = "memory_remember",
    is_error: bool = False,
) -> list[Message]:
    args: dict = {"topic": "Goal", "summary": "Done A; next B"}
    if kind is not None:
        args["kind"] = kind
    if task is not None:
        args["task"] = task
    if new_task:
        args["new_task"] = True
    return [
        Message(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="call-1", name=tool_name, arguments=args)],
            meta={"usage": {"prompt_tokens": prompt_tokens}},
        ),
        Message(
            role="tool",
            content='{"session_id": "2026-10-19#abc", "task_id": "task-1234abcd"}',
            tool_call_id="call-1",
            meta={"is_error": True} if is_error else None,
        ),
    ]


def _state(messages: list[Message], ratio: float) -> str:
    return checkpoint_state(
        messages, context_tokens=100_000, current_ratio=ratio
    )


def test_checkpoint_freshness_follows_band_escalation() -> None:
    marker = Message(
        role="system", content="[compaction: omitted 2 messages/1 turns]"
    )
    roomy_write = [marker, *_write_pair(prompt_tokens=42_000)]
    assert _state(roomy_write, 0.42) == "recorded"
    assert _state(roomy_write, 0.83) == "stale"
    prepare_write = [marker, *_write_pair(prompt_tokens=83_000)]
    assert _state(prepare_write, 0.83) == "recorded"
    assert _state(prepare_write, 0.92) == "stale"


def test_checkpoint_requires_explicit_checkpoint_kind_and_task() -> None:
    assert _state(_write_pair(prompt_tokens=83_000, kind="habit"), 0.83) == "none"
    assert _state(_write_pair(prompt_tokens=83_000, kind=None), 0.83) == "none"
    assert _state(_write_pair(prompt_tokens=83_000, task=None), 0.83) == "none"
    assert (
        _state(_write_pair(prompt_tokens=83_000, task=None, new_task=True), 0.83)
        == "recorded"
    )
    assert (
        _state(
            _write_pair(prompt_tokens=83_000, tool_name="memory_update"), 0.83
        )
        == "none"
    )


def test_checkpoint_not_recorded_for_error_or_old_segment() -> None:
    assert _state(_write_pair(prompt_tokens=83_000, is_error=True), 0.83) == "none"
    old_segment = [
        *_write_pair(prompt_tokens=83_000),
        Message(role="system", content="[compaction: omitted 2 messages/1 turns]"),
    ]
    assert _state(old_segment, 0.83) == "none"
