from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from thyca.core.protocol import ToolCall
from thyca.tools.builtin import register_file_tools
from thyca.tools.gateway.background import BackgroundProcs
from thyca.tools.gateway import ToolGateway
from thyca.tools.gateway.gateway import tool_kill_spec, tool_read_spec
from thyca.tools.path_guard import PathGuard
from thyca.tools.registry import ToolRegistry
from thyca.tools.task_store import TaskStore


def _stack(
    root: Path, background: BackgroundProcs | None, soft: int = 60
) -> tuple[ToolGateway, BackgroundProcs | None]:
    store = TaskStore()
    registry = ToolRegistry()
    register_file_tools(registry, PathGuard(root), background)
    gateway = ToolGateway(registry, store, background, soft_timeout_s=soft)
    registry.register(tool_read_spec(gateway))
    registry.register(tool_kill_spec(gateway))
    return gateway, background


async def _call(gateway: ToolGateway, name: str, **args):
    return await gateway.submit(ToolCall(id="t1", name=name, arguments=args))


@pytest.mark.asyncio
async def test_background_start_then_read_done(tmp_path: Path) -> None:
    gateway, _procs = _stack(tmp_path, BackgroundProcs())
    started = await _call(gateway, "bash", command="echo hi", background=True)
    assert not started.is_error
    assert started.content.startswith("started: exec1\n")
    assert "tool_read" in started.content
    read = await gateway.read("exec1", wait=5)
    assert not read.is_error
    assert "status: done" in read.content
    assert "exit: 0" in read.content
    assert "hi" in read.content


@pytest.mark.asyncio
async def test_read_reports_running_before_exit(tmp_path: Path) -> None:
    gateway, _procs = _stack(tmp_path, BackgroundProcs())
    started = await _call(
        gateway, "bash", command="sleep 2; echo fin", background=True
    )
    bid = started.content.splitlines()[0].removeprefix("started: ")
    running = await gateway.read(bid)
    assert running.content.startswith("status: running")
    done = await gateway.read(bid, wait=5)
    assert "status: done" in done.content
    assert "fin" in done.content


@pytest.mark.asyncio
async def test_second_read_after_done_reports_no_new_output(tmp_path: Path) -> None:
    gateway, _procs = _stack(tmp_path, BackgroundProcs())
    await _call(gateway, "bash", command="echo hi", background=True)
    first = await gateway.read("exec1", wait=5)
    assert "hi" in first.content
    second = await gateway.read("exec1")
    assert "status: done" in second.content
    assert "(no new output)" in second.content


@pytest.mark.asyncio
async def test_unknown_id_lists_known(tmp_path: Path) -> None:
    gateway, procs = _stack(tmp_path, BackgroundProcs())
    assert procs is not None
    with pytest.raises(ValueError, match=r"unknown execution id: exec99 \(known: none\)"):
        await gateway.read("exec99")
    with pytest.raises(ValueError, match=r"unknown background id: bg99 \(known: none\)"):
        await procs.read("bg99", 0)
    await _call(gateway, "bash", command="echo hi", background=True)
    with pytest.raises(ValueError, match=r"known: exec1"):
        await gateway.read("exec99")
    with pytest.raises(ValueError, match=r"known: exec1"):
        await procs.read("bg99", 0)


@pytest.mark.asyncio
async def test_background_timeout_kills_group(tmp_path: Path) -> None:
    marker = tmp_path / "still-running"
    gateway, _procs = _stack(tmp_path, BackgroundProcs())
    await _call(
        gateway,
        "bash",
        command=f"sleep 5; echo alive > '{marker}'",
        background=True,
        timeout=1,
    )
    read = await gateway.read("exec1", wait=10)
    assert "status: done" in read.content
    assert "timed_out: true" in read.content
    await asyncio.sleep(0.1)
    assert not marker.exists()


@pytest.mark.asyncio
async def test_shell_exit_does_not_wait_for_pipe_holders(tmp_path: Path) -> None:
    """Shell exits at once but a child keeps stdout open: must report done
    after the drain grace, not after the child exits nor the timeout."""
    gateway, _procs = _stack(tmp_path, BackgroundProcs())
    await _call(
        gateway,
        "bash",
        command="echo ok; sleep 30 &",
        background=True,
        timeout=60,
    )
    read = await gateway.read("exec1", wait=10)
    assert "status: done" in read.content
    assert "exit: 0" in read.content
    assert "timed_out" not in read.content
    assert "ok" in read.content


@pytest.mark.asyncio
async def test_wait_beyond_cap_returns_when_done(tmp_path: Path) -> None:
    """wait is clamped to 60s but must still return as soon as the proc ends."""
    gateway, _procs = _stack(tmp_path, BackgroundProcs())
    await _call(gateway, "bash", command="sleep 1; echo fin", background=True)
    read = await gateway.read("exec1", wait=1000)
    assert "status: done" in read.content
    assert "fin" in read.content


@pytest.mark.asyncio
async def test_fast_command_returns_foreground_result(tmp_path: Path) -> None:
    gateway, procs = _stack(tmp_path, BackgroundProcs())
    assert procs is not None
    result = await _call(gateway, "bash", command="echo hi")
    assert not result.is_error
    assert result.content.startswith("exit: 0\n")
    assert "hi" in result.content
    assert "still running" not in result.content
    assert "status:" not in result.content
    assert gateway.executions == {}
    assert procs.known_ids() == []


@pytest.mark.asyncio
async def test_slow_command_tracks_under_gateway_soft_timeout(tmp_path: Path) -> None:
    gateway, _procs = _stack(tmp_path, BackgroundProcs(), soft=1)
    result = await _call(gateway, "bash", command="sleep 2; echo fin")
    assert result.content.startswith("still running: exec1\n")
    assert "poll the result with tool_read" in result.content
    read = await gateway.read("exec1", wait=10)
    assert "status: done" in read.content
    assert "fin" in read.content


@pytest.mark.asyncio
async def test_explicit_small_timeout_still_kills(tmp_path: Path) -> None:
    marker = tmp_path / "still-running"
    gateway, _procs = _stack(tmp_path, BackgroundProcs())
    result = await _call(
        gateway,
        "bash",
        command=f"sleep 5; echo alive > '{marker}'",
        timeout=1,
    )
    assert not result.is_error
    assert result.content.startswith("exit:")
    assert "timed_out: true" in result.content
    await asyncio.sleep(0.1)
    assert not marker.exists()


@pytest.mark.asyncio
async def test_background_without_manager_is_error(tmp_path: Path) -> None:
    gateway, _procs = _stack(tmp_path, None)
    result = await _call(gateway, "bash", command="echo hi", background=True)
    assert result.is_error
    assert "background is not available" in result.content


@pytest.mark.asyncio
async def test_background_rejects_non_boolean(tmp_path: Path) -> None:
    gateway, _procs = _stack(tmp_path, BackgroundProcs())
    result = await _call(gateway, "bash", command="echo hi", background="false")
    assert result.is_error
    # F7-strict: the registry rejects mistyped args before the handler runs.
    assert "argument 'background' must be boolean, got string" in result.content


@pytest.mark.asyncio
async def test_kill_all_stops_running_procs(tmp_path: Path) -> None:
    procs = BackgroundProcs()
    gateway, _ = _stack(tmp_path, procs)
    await _call(gateway, "bash", command="sleep 30", background=True)
    await procs.kill_all()
    await asyncio.sleep(0)  # let the settle callback fire
    read = await gateway.read("exec1")
    assert "status: done" in read.content
    # kill_all sets killed like kill(): the execution settles failed, not done.
    assert read.is_error
    assert gateway.executions["exec1"].status == "failed"


@pytest.mark.asyncio
async def test_gateway_kill_stops_one_proc(tmp_path: Path) -> None:
    gateway, procs = _stack(tmp_path, BackgroundProcs())
    assert procs is not None
    await _call(gateway, "bash", command="sleep 30", background=True)
    await _call(gateway, "bash", command="sleep 30", background=True)
    assert await gateway.kill("exec1") == "killed exec1"
    execution = gateway.executions["exec1"]
    assert execution.status == "failed"
    assert execution.error is True
    victim = procs.get("exec1")
    assert victim is not None and victim.done.is_set()
    assert victim.proc.returncode is not None
    survivor = procs.get("exec2")
    assert survivor is not None and not survivor.done.is_set()
    assert gateway.executions["exec2"].status == "running"
    await gateway.kill("exec2")
    with pytest.raises(ValueError, match="unknown execution id"):
        await gateway.kill("exec99")


@pytest.mark.asyncio
async def test_proc_paging_and_gap(tmp_path: Path) -> None:
    gateway, _procs = _stack(tmp_path, BackgroundProcs())
    await _call(
        gateway,
        "bash",
        command="python3 -c 'for i in range(500): print(f\"line{i}\")'",
        background=True,
    )
    done = await gateway.read("exec1", wait=10)
    assert "status: done" in done.content
    page = await gateway.read("exec1", offset=10, limit=5)
    assert "[page: lines 11-15 of 500 retained]" in page.content
    gap = await gateway.read("exec1", offset=500, limit=5)
    assert "(gap: line offset 500 is past the 500 retained lines)" in gap.content


@pytest.mark.asyncio
async def test_on_exit_fires_for_proc(tmp_path: Path) -> None:
    gateway, _procs = _stack(tmp_path, BackgroundProcs())
    await _call(gateway, "bash", command="echo hi", background=True)
    seen: list[tuple[str, str]] = []
    gateway.executions["exec1"].on_exit(lambda e: seen.append((e.id, e.status)))
    read = await gateway.read("exec1", wait=5)
    assert "status: done" in read.content
    await asyncio.sleep(0)
    assert seen == [("exec1", "done")]


@pytest.mark.asyncio
async def test_tool_read_and_kill_cover_procs_via_tool(tmp_path: Path) -> None:
    gateway, _procs = _stack(tmp_path, BackgroundProcs())
    started = await _call(gateway, "bash", command="echo hi", background=True)
    bid = started.content.splitlines()[0].removeprefix("started: ")
    read = await _call(gateway, "tool_read", id=bid, wait=5)
    assert not read.is_error
    assert "status: done" in read.content
    assert "hi" in read.content
    proc = await _call(gateway, "bash", command="sleep 30", background=True)
    pid = proc.content.splitlines()[0].removeprefix("started: ")
    killed = await _call(gateway, "tool_kill", id=pid)
    assert not killed.is_error
    assert killed.content == f"killed {pid}"
    assert gateway.executions[pid].status == "failed"
