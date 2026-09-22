from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

import pytest

from thyca.core.protocol import ToolCall, ToolResult
from thyca.tools.builtin import register_file_tools
from thyca.tools.path_guard import PathGuard
from thyca.tools.registry import ToolRegistry, ToolSpec
from thyca.tools.task_store import TaskStore, tool_read_spec


def _registry(tmp_path: Path, soft: int = 1) -> ToolRegistry:
    store = TaskStore()
    registry = ToolRegistry(tasks=store, soft_timeout_s=soft)
    register_file_tools(registry, PathGuard(tmp_path))
    registry.register(tool_read_spec(store))
    return registry


def _slow_spec(name: str, coro_factory) -> ToolSpec:
    async def handler(args: dict):
        return await coro_factory()

    return ToolSpec(
        name=name,
        description="slow test tool",
        parameters={"type": "object", "properties": {}},
        handler=handler,
        parallel_safe=True,
    )


async def _call(registry: ToolRegistry, name: str, **args):
    return await registry.dispatch(ToolCall(id="t1", name=name, arguments=args))


@pytest.mark.asyncio
async def test_fast_handler_unchanged(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.register(_slow_spec("quick", _return_soon("quick")))
    result = await _call(registry, "quick")
    assert not result.is_error
    assert result.content == "quick"
    assert "still running" not in result.content


def _return_soon(value: str, delay: float = 0.0):
    async def coro_factory():
        if delay:
            await asyncio.sleep(delay)
        return value

    return coro_factory


@pytest.mark.asyncio
async def test_slow_handler_escalates_then_completes(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.register(_slow_spec("slow", _return_soon("done!", delay=1.5)))
    result = await _call(registry, "slow")
    assert not result.is_error
    assert result.content.startswith("still running: task")
    tid = result.content.splitlines()[0].removeprefix("still running: ")
    read = await _call(registry, "tool_read", id=tid, wait=5)
    assert not read.is_error
    assert read.content == "done!"


@pytest.mark.asyncio
async def test_handler_error_after_escalation(tmp_path: Path) -> None:
    async def boom_factory():
        await asyncio.sleep(1.5)
        raise RuntimeError("boom")

    registry = _registry(tmp_path)
    registry.register(_slow_spec("slow", boom_factory))
    result = await _call(registry, "slow")
    assert not result.is_error
    tid = result.content.splitlines()[0].removeprefix("still running: ")
    read = await _call(registry, "tool_read", id=tid, wait=5)
    assert read.is_error
    assert "boom" in read.content


@pytest.mark.asyncio
async def test_tool_result_is_error_preserved(tmp_path: Path) -> None:
    async def bad_factory():
        await asyncio.sleep(1.5)
        return ToolResult(tool_call_id="x", name="slow", content="bad", is_error=True)

    registry = _registry(tmp_path)
    registry.register(_slow_spec("slow", bad_factory))
    result = await _call(registry, "slow")
    tid = result.content.splitlines()[0].removeprefix("still running: ")
    read = await _call(registry, "tool_read", id=tid, wait=5)
    assert read.is_error
    assert read.content == "bad"


@pytest.mark.asyncio
async def test_unknown_task_id_lists_known(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.register(_slow_spec("slow", _return_soon("done!", delay=1.5)))
    missing = await _call(registry, "tool_read", id="task99")
    assert missing.is_error
    assert "known: none" in missing.content
    await _call(registry, "slow")
    missing = await _call(registry, "tool_read", id="task99")
    assert missing.is_error
    assert "known: task1" in missing.content


@pytest.mark.asyncio
async def test_handler_raising_timeout_error_is_not_escalation(tmp_path: Path) -> None:
    async def timeout_factory():
        raise TimeoutError("inner timeout")

    registry = _registry(tmp_path)
    registry.register(_slow_spec("slow", timeout_factory))
    result = await _call(registry, "slow")
    assert result.is_error
    assert "inner timeout" in result.content
    assert "still running" not in result.content


@pytest.mark.asyncio
async def test_cancelled_call_leaves_no_store_entry(tmp_path: Path) -> None:
    """User cancels the turn mid-call: the handler still runs to completion,
    but its id was never exposed, so the store must not keep the entry."""
    registry = _registry(tmp_path)
    registry.register(_slow_spec("slow", _return_soon("late", delay=1.5)))
    pending = asyncio.create_task(_call(registry, "slow"))
    await asyncio.sleep(0.2)
    pending.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await pending
    await asyncio.sleep(1.6)
    read = await _call(registry, "tool_read", id="task99")
    assert read.is_error
    assert "known: none" in read.content


@pytest.mark.asyncio
async def test_bash_opts_out_of_generic_escalation(tmp_path: Path) -> None:
    registry = _registry(tmp_path, soft=1)
    # register_file_tools already registered bash with escalates=True
    result = await _call(registry, "bash", command="sleep 2; echo slowdone")
    assert not result.is_error
    assert "exit: 0" in result.content
    assert "slowdone" in result.content
    assert "still running" not in result.content
