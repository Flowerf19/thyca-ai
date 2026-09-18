from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from thyca.protocol import ToolCall
from thyca.tools.builtin import register_file_tools
from thyca.tools.builtin.background import BackgroundProcs
from thyca.tools.path_guard import PathGuard
from thyca.tools.registry import ToolRegistry


def _registry(root: Path, background: BackgroundProcs | None) -> ToolRegistry:
    registry = ToolRegistry()
    register_file_tools(registry, PathGuard(root), background)
    return registry


async def _call(registry: ToolRegistry, name: str, **args):
    return await registry.dispatch(ToolCall(id="t1", name=name, arguments=args))


@pytest.mark.asyncio
async def test_background_start_then_read_done(tmp_path: Path) -> None:
    registry = _registry(tmp_path, BackgroundProcs())
    started = await _call(registry, "bash", command="echo hi", background=True)
    assert not started.is_error
    assert started.content.startswith("started: bg")
    bid = started.content.splitlines()[0].removeprefix("started: ")
    read = await _call(registry, "bash_read", id=bid, wait=5)
    assert not read.is_error
    assert "status: done" in read.content
    assert "exit: 0" in read.content
    assert "hi" in read.content


@pytest.mark.asyncio
async def test_read_reports_running_before_exit(tmp_path: Path) -> None:
    registry = _registry(tmp_path, BackgroundProcs())
    started = await _call(
        registry, "bash", command="sleep 0.5; echo fin", background=True
    )
    bid = started.content.splitlines()[0].removeprefix("started: ")
    running = await _call(registry, "bash_read", id=bid)
    assert "status: running" in running.content
    done = await _call(registry, "bash_read", id=bid, wait=5)
    assert "status: done" in done.content
    assert "fin" in done.content


@pytest.mark.asyncio
async def test_unknown_id_lists_known(tmp_path: Path) -> None:
    registry = _registry(tmp_path, BackgroundProcs())
    missing = await _call(registry, "bash_read", id="bg99")
    assert missing.is_error
    assert "known: none" in missing.content
    started = await _call(registry, "bash", command="echo hi", background=True)
    started_id = started.content.splitlines()[0].removeprefix("started: ")
    missing = await _call(registry, "bash_read", id="bg99")
    assert missing.is_error
    assert f"known: {started_id}" in missing.content


@pytest.mark.asyncio
async def test_background_timeout_kills_group(tmp_path: Path) -> None:
    marker = tmp_path / "still-running"
    registry = _registry(tmp_path, BackgroundProcs())
    started = await _call(
        registry,
        "bash",
        command=f"sleep 5; echo alive > '{marker}'",
        background=True,
        timeout=1,
    )
    bid = started.content.splitlines()[0].removeprefix("started: ")
    read = await _call(registry, "bash_read", id=bid, wait=10)
    assert "status: done" in read.content
    assert "timed_out: true" in read.content
    await asyncio.sleep(0.1)
    assert not marker.exists()


@pytest.mark.asyncio
async def test_shell_exit_does_not_wait_for_pipe_holders(tmp_path: Path) -> None:
    """Shell exits at once but a child keeps stdout open: must report done
    after the drain grace, not after the child exits nor the timeout."""
    registry = _registry(tmp_path, BackgroundProcs())
    started = await _call(
        registry,
        "bash",
        command="echo ok; sleep 30 &",
        background=True,
        timeout=60,
    )
    bid = started.content.splitlines()[0].removeprefix("started: ")
    read = await _call(registry, "bash_read", id=bid, wait=10)
    assert "status: done" in read.content
    assert "exit: 0" in read.content
    assert "timed_out" not in read.content
    assert "ok" in read.content


@pytest.mark.asyncio
async def test_wait_beyond_cap_returns_when_done(tmp_path: Path) -> None:
    """wait is clamped to 60s but must still return as soon as the proc ends."""
    registry = _registry(tmp_path, BackgroundProcs())
    started = await _call(
        registry, "bash", command="sleep 1; echo fin", background=True
    )
    bid = started.content.splitlines()[0].removeprefix("started: ")
    read = await _call(registry, "bash_read", id=bid, wait=1000)
    assert "status: done" in read.content
    assert "fin" in read.content


@pytest.mark.asyncio
async def test_background_without_manager_is_error(tmp_path: Path) -> None:
    registry = _registry(tmp_path, None)
    result = await _call(registry, "bash", command="echo hi", background=True)
    assert result.is_error
    assert "background is not available" in result.content


@pytest.mark.asyncio
async def test_background_rejects_non_boolean(tmp_path: Path) -> None:
    registry = _registry(tmp_path, BackgroundProcs())
    result = await _call(registry, "bash", command="echo hi", background="false")
    assert result.is_error
    assert "background must be a boolean" in result.content


@pytest.mark.asyncio
async def test_kill_all_stops_running_procs(tmp_path: Path) -> None:
    procs = BackgroundProcs()
    registry = _registry(tmp_path, procs)
    started = await _call(registry, "bash", command="sleep 30", background=True)
    bid = started.content.splitlines()[0].removeprefix("started: ")
    await procs.kill_all()
    read = await _call(registry, "bash_read", id=bid)
    assert "status: done" in read.content
