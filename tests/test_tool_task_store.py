from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

import pytest

from thyca.core.protocol import ToolCall, ToolResult
from thyca.tools.builtin import register_file_tools
from thyca.tools.gateway import ToolGateway
from thyca.tools.gateway.gateway import tool_kill_spec, tool_read_spec
from thyca.tools.path_guard import PathGuard
from thyca.tools.registry import ToolRegistry, ToolSpec
from thyca.tools.task_store import TaskStore


def _gateway(tmp_path: Path, soft: int = 1) -> tuple[ToolGateway, ToolRegistry, TaskStore]:
    store = TaskStore()
    registry = ToolRegistry()
    register_file_tools(registry, PathGuard(tmp_path))
    gateway = ToolGateway(registry, store, soft_timeout_s=soft)
    registry.register(tool_read_spec(gateway))
    registry.register(tool_kill_spec(gateway))
    return gateway, registry, store


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


def _return_soon(value: str, delay: float = 0.0):
    async def coro_factory():
        if delay:
            await asyncio.sleep(delay)
        return value

    return coro_factory


async def _call(gateway: ToolGateway, name: str, **args):
    return await gateway.submit(ToolCall(id="t1", name=name, arguments=args))


@pytest.mark.asyncio
async def test_fast_handler_unchanged(tmp_path: Path) -> None:
    gateway, registry, _store = _gateway(tmp_path)
    registry.register(_slow_spec("quick", _return_soon("quick")))
    result = await _call(gateway, "quick")
    assert not result.is_error
    assert result.content == "quick"
    assert "still running" not in result.content
    assert gateway.executions == {}


@pytest.mark.asyncio
async def test_slow_handler_tracked_then_completes(tmp_path: Path) -> None:
    gateway, registry, _store = _gateway(tmp_path)
    registry.register(_slow_spec("slow", _return_soon("done!", delay=1.5)))
    result = await _call(gateway, "slow")
    assert not result.is_error
    assert result.content.startswith("still running: exec1\n")
    assert "poll the result with tool_read" in result.content
    read = await gateway.read("exec1", wait=5)
    assert not read.is_error
    assert read.content == "done!"


@pytest.mark.asyncio
async def test_tool_read_spec_takes_exec_ids(tmp_path: Path) -> None:
    gateway, registry, _store = _gateway(tmp_path)
    registry.register(_slow_spec("slow", _return_soon("done!", delay=1.5)))
    result = await _call(gateway, "slow")
    tid = result.content.splitlines()[0].removeprefix("still running: ")
    read = await _call(gateway, "tool_read", id=tid, wait=5)
    assert not read.is_error
    assert read.content == "done!"


@pytest.mark.asyncio
async def test_handler_error_after_tracking(tmp_path: Path) -> None:
    async def boom_factory():
        await asyncio.sleep(1.5)
        raise RuntimeError("boom")

    gateway, registry, _store = _gateway(tmp_path)
    registry.register(_slow_spec("slow", boom_factory))
    await _call(gateway, "slow")
    read = await gateway.read("exec1", wait=5)
    assert read.is_error
    assert "boom" in read.content
    direct = await _call(gateway, "tool_read", id="exec1")
    assert direct.is_error
    assert "boom" in direct.content


@pytest.mark.asyncio
async def test_tool_result_is_error_preserved(tmp_path: Path) -> None:
    async def bad_factory():
        await asyncio.sleep(1.5)
        return ToolResult(tool_call_id="x", name="slow", content="bad", is_error=True)

    gateway, registry, _store = _gateway(tmp_path)
    registry.register(_slow_spec("slow", bad_factory))
    await _call(gateway, "slow")
    read = await gateway.read("exec1", wait=5)
    assert read.is_error
    assert read.content == "bad"


@pytest.mark.asyncio
async def test_unknown_task_id_lists_known(tmp_path: Path) -> None:
    gateway, registry, store = _gateway(tmp_path)
    registry.register(_slow_spec("slow", _return_soon("done!", delay=1.5)))
    with pytest.raises(ValueError, match=r"unknown task id: task99 \(known: none\)"):
        await store.read("task99", 0)
    await _call(gateway, "slow")
    with pytest.raises(ValueError, match=r"unknown task id: task99 \(known: exec1\)"):
        await store.read("task99", 0)
    missing = await _call(gateway, "tool_read", id="task99")
    assert missing.is_error
    assert "known: exec1" in missing.content


@pytest.mark.asyncio
async def test_handler_raising_timeout_error_is_not_tracking(tmp_path: Path) -> None:
    async def timeout_factory():
        raise TimeoutError("inner timeout")

    gateway, registry, _store = _gateway(tmp_path)
    registry.register(_slow_spec("slow", timeout_factory))
    result = await _call(gateway, "slow")
    assert result.is_error
    assert "inner timeout" in result.content
    assert "still running" not in result.content


@pytest.mark.asyncio
async def test_cancelled_submit_leaves_no_store_entry(tmp_path: Path) -> None:
    """User cancels the turn mid-call: the handler still runs to completion,
    but its id was never exposed, so the store must not keep the entry."""
    gateway, registry, store = _gateway(tmp_path)
    registry.register(_slow_spec("slow", _return_soon("late", delay=1.5)))
    pending = asyncio.create_task(_call(gateway, "slow"))
    await asyncio.sleep(0.2)
    pending.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await pending
    await asyncio.sleep(1.6)
    assert gateway.executions == {}
    assert store.known_ids() == []


@pytest.mark.asyncio
async def test_settle_retains_first_megabyte_and_counts_overflow() -> None:
    store = TaskStore()

    async def huge() -> str:
        return "L\n" * 600_000

    task = asyncio.create_task(huge())
    entry = store.track(task)
    await asyncio.wait_for(entry.done.wait(), timeout=5)
    assert len(entry.content.encode("utf-8")) == 1_048_576
    assert entry.overflow == 151_424
    assert entry.is_error is False


@pytest.mark.asyncio
async def test_store_read_pages_and_reports_gap() -> None:
    store = TaskStore()

    async def lines() -> str:
        return "\n".join(f"line{i}" for i in range(10))

    task = asyncio.create_task(lines())
    entry = store.track(task)
    entry.exposed = True
    await asyncio.wait_for(entry.done.wait(), timeout=5)
    page = await store.read(entry.id, 0, offset=8, limit=5)
    assert page.content == "line8\nline9"
    gap = await store.read(entry.id, 0, offset=10, limit=5)
    assert "past the 10 retained lines" in gap.content


@pytest.mark.asyncio
async def test_store_kill_cancels_and_settles() -> None:
    store = TaskStore()

    async def slow() -> str:
        await asyncio.sleep(30)
        return "late"

    task = asyncio.create_task(slow())
    entry = store.track(task)
    await store.kill(entry.id)
    assert task.cancelled()
    assert entry.content == "task was cancelled"
    assert entry.is_error is True
    with pytest.raises(ValueError, match="unknown task id"):
        await store.kill("task99")


@pytest.mark.asyncio
async def test_tool_read_pages_and_kills_via_tool(tmp_path: Path) -> None:
    gateway, registry, _store = _gateway(tmp_path)
    content = "\n".join(f"line{i}" for i in range(50))
    registry.register(_slow_spec("slow", _return_soon(content, delay=1.5)))
    registry.register(_slow_spec("stuck", _return_soon("late", delay=30)))
    slow = await _call(gateway, "slow")
    tid = slow.content.splitlines()[0].removeprefix("still running: ")
    page = await _call(gateway, "tool_read", id=tid, wait=5, offset=10, limit=3)
    assert not page.is_error
    assert "line10\nline11\nline12" in page.content
    assert "[page: lines 11-13 of 50 retained]" in page.content
    stuck = await _call(gateway, "stuck")
    sid = stuck.content.splitlines()[0].removeprefix("still running: ")
    killed = await _call(gateway, "tool_kill", id=sid)
    assert not killed.is_error
    assert killed.content == f"killed {sid}"
    missing = await _call(gateway, "tool_kill", id="exec99")
    assert missing.is_error
    assert "unknown execution id" in missing.content
    blank = await _call(gateway, "tool_read", id="  ")
    assert blank.is_error
    assert "non-empty string" in blank.content
