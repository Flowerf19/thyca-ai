"""GOAL-004: honest output recovery after shrink (regression).

Execution pointers must come from gateway metadata / original calls, never
from quoted ids in output text. A plain delta poll shrinks to a pointer
that recovers via offset/limit; stale or foreign ids get honest text.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from thyca.agent.act import _build_result
from thyca.agent.meta import tool_message
from thyca.agent.shrink import estimate_wire_tokens, shrink_stage_messages
from thyca.core.protocol import Message, ToolCall, ToolResult
from thyca.tools.builtin import register_file_tools
from thyca.tools.gateway import ToolGateway
from thyca.tools.gateway.background import BackgroundProcs
from thyca.tools.gateway.gateway import tool_kill_spec, tool_read_spec
from thyca.tools.path_guard import PathGuard
from thyca.tools.registry import ToolRegistry
from thyca.tools.task_store import TaskStore


def _stack(
    root: Path, background: BackgroundProcs | None, soft: int = 60
) -> ToolGateway:
    store = TaskStore()
    registry = ToolRegistry()
    register_file_tools(registry, PathGuard(root), background)
    gateway = ToolGateway(registry, store, background, soft_timeout_s=soft)
    registry.register(tool_read_spec(gateway))
    registry.register(tool_kill_spec(gateway))
    return gateway


def _tool(content: str, meta: dict | None = None) -> Message:
    return Message(role="tool", content=content, tool_call_id="c1", meta=meta)


def _big(diff: str = "x", size: int = 3000) -> str:
    return diff * size


@pytest.mark.asyncio
async def test_plain_poll_recovers_via_paging_not_rerun(tmp_path: Path) -> None:
    gateway = _stack(tmp_path, BackgroundProcs())
    started = await gateway.submit(
        ToolCall(
            id="t0",
            name="bash",
            arguments={
                "command": "python3 -c \"print('0123456789abcdef'*190)\"",
                "background": True,
            },
        )
    )
    assert not started.is_error
    exec_id = started.content.splitlines()[0].removeprefix("started: ")
    assert exec_id.startswith("exec")
    # Full Act -> meta path, like Observe: trusted metadata must survive it.
    polled = await gateway.submit(
        ToolCall(id="c1", name="tool_read", arguments={"id": exec_id, "wait": 10})
    )
    assert not polled.is_error
    assert len(polled.content) > 2500
    assert 'tool_read id="' not in polled.content  # plain poll: no ID footer
    result = _build_result(
        ToolCall(id="c1", name="tool_read", arguments={"id": exec_id}), polled
    )
    stored = tool_message(result, round_no=2)
    assert (stored.meta or {}).get("exec_id") == exec_id
    # Shrink promises the retained execution, not a rerun.
    estimate = estimate_wire_tokens([stored])
    context = int(estimate / 0.85)
    shrunk, count, _, _ = shrink_stage_messages(
        [stored],
        current_round=9,
        context_tokens=context,
        live_exec_ids=set(gateway.executions),
    )
    assert count == 1
    assert f'tool_read id="{exec_id}"' in (shrunk[0].content or "")
    # A plain re-poll consumes nothing new; offset/limit recovers bytes.
    again = await gateway.submit(
        ToolCall(id="c2", name="tool_read", arguments={"id": exec_id})
    )
    assert "(no new output)" in again.content
    paged = await gateway.submit(
        ToolCall(
            id="c3",
            name="tool_read",
            arguments={"id": exec_id, "offset": 0, "limit": 5},
        )
    )
    assert "0123456789abcdef" in paged.content


def test_quoted_exec_id_in_output_is_not_a_reference() -> None:
    content = _big() + '\nsee tool_read id="exec77" in the manual example'
    messages = [_tool(content)]
    estimate = estimate_wire_tokens(messages)
    context = int(estimate / 0.85)
    shrunk, count, _, _ = shrink_stage_messages(
        messages, current_round=9, context_tokens=context
    )
    assert count == 1
    assert 'id="exec77"' not in (shrunk[0].content or "")
    assert "re-run the tool if needed" in (shrunk[0].content or "")


def test_evicted_execution_is_not_promised() -> None:
    content = _big() + '\nread more: tool_read id="exec999"'
    message = _tool(content, {"round": 2, "exec_id": "exec999"})
    estimate = estimate_wire_tokens([message])
    context = int(estimate / 0.85)
    shrunk, count, _, _ = shrink_stage_messages(
        [message],
        current_round=9,
        context_tokens=context,
        live_exec_ids={"exec1"},
    )
    assert count == 1
    assert 'id="exec999"' not in (shrunk[0].content or "")


def test_history_exec_ref_is_conservative() -> None:
    content = _big() + '\nread more: tool_read id="exec9"'
    history = _tool(content, {"round": 1, "exec_id": "exec9"})
    own = _tool(_big("o", 2500), None)
    messages = [history, own]
    estimate = estimate_wire_tokens(messages)
    context = int(estimate / 0.85)
    # exec9 is still retained, but history ids are stale by rule.
    shrunk, count, _, _ = shrink_stage_messages(
        messages,
        current_round=9,
        context_tokens=context,
        run_start=1,
        live_exec_ids={"exec9"},
    )
    assert count == 2
    assert 'id="exec9"' not in (shrunk[0].content or "")
    assert "re-run the tool if needed" in (shrunk[0].content or "")


@pytest.mark.asyncio
async def test_tracked_result_carries_exec_ref(tmp_path: Path) -> None:
    async def slow(args: dict) -> str:
        await asyncio.sleep(30)
        return "late"

    from thyca.tools.registry import ToolSpec

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="slow",
            description="slow",
            parameters={"type": "object", "properties": {}},
            handler=slow,
            parallel_safe=True,
        )
    )
    gateway = ToolGateway(registry, TaskStore(), None, soft_timeout_s=0)
    result = await gateway.submit(ToolCall(id="s1", name="slow", arguments={}))
    assert not result.is_error
    assert result.exec_ref == "exec1"
    assert "still running: exec1" in result.content
    await gateway.shutdown()


def test_evicted_poll_gets_honest_text_not_delta() -> None:
    call = ToolCall(id="c1", name="tool_read", arguments={"id": "exec999"})
    assistant = Message(role="assistant", content="", tool_calls=[call])
    tool_msg = _tool(_big(), {"round": 2, "exec_id": "exec999"})
    messages = [assistant, tool_msg]
    estimate = estimate_wire_tokens(messages)
    context = int(estimate / 0.85)
    shrunk, count, _, _ = shrink_stage_messages(
        messages,
        current_round=9,
        context_tokens=context,
        live_exec_ids={"exec1"},
    )
    assert count == 1
    text = shrunk[1].content or ""
    assert "execution no longer retained" in text
    assert "re-read retained output" not in text
    assert 'id="exec999"' not in text


def test_error_poll_does_not_promise_reread() -> None:
    call = ToolCall(id="c1", name="tool_read", arguments={"id": "exec1"})
    assistant = Message(role="assistant", content="", tool_calls=[call])
    tool_msg = _tool(_big("e"), {"round": 2, "is_error": True})
    messages = [assistant, tool_msg]
    estimate = estimate_wire_tokens(messages)
    context = int(estimate / 0.85)
    shrunk, count, _, _ = shrink_stage_messages(
        messages,
        current_round=9,
        context_tokens=context,
        live_exec_ids={"exec1"},
    )
    assert count == 1
    text = shrunk[1].content or ""
    assert "re-read retained output" not in text
    assert "re-run the tool if needed" in text


def test_unknown_live_set_prefers_delta_over_bare_pointer() -> None:
    call = ToolCall(id="c1", name="tool_read", arguments={"id": "exec5"})
    assistant = Message(role="assistant", content="", tool_calls=[call])
    tool_msg = _tool(_big(), {"round": 2})
    messages = [assistant, tool_msg]
    estimate = estimate_wire_tokens(messages)
    context = int(estimate / 0.85)
    shrunk, count, _, _ = shrink_stage_messages(
        messages, current_round=9, context_tokens=context
    )
    assert count == 1
    text = shrunk[1].content or ""
    assert 'id="exec5"' not in text
    assert "re-read retained output" in text


def test_build_result_and_tool_message_preserve_exec_ref() -> None:
    dispatched = ToolResult(
        tool_call_id="exec1", name="tool_read", content=_big(), exec_ref="exec1"
    )
    result = _build_result(
        ToolCall(id="c1", name="tool_read", arguments={"id": "exec1"}), dispatched
    )
    assert result.exec_ref == "exec1"
    stored = tool_message(result, round_no=2)
    assert (stored.meta or {}).get("exec_id") == "exec1"
    assert stored.tool_call_id == "c1"
