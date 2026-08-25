from __future__ import annotations

import asyncio
import os
import subprocess
import time
from pathlib import Path

import pytest

from thyca.protocol import ToolCall
from thyca.tools.builtin import register_file_tools
from thyca.tools.builtin.bash import kill_process_group, parse_timeout, select_shell
from thyca.tools.path_guard import PathGuard
from thyca.tools.registry import ToolRegistry


def _registry(root: Path) -> ToolRegistry:
    registry = ToolRegistry()
    register_file_tools(registry, PathGuard(root))
    return registry


async def _bash(registry: ToolRegistry, command: str, **extra):
    return await registry.dispatch(
        ToolCall(id="b1", name="bash", arguments={"command": command, **extra})
    )


def test_select_shell_is_posix_bash() -> None:
    shell = select_shell()
    assert shell.endswith("bash")
    assert os.path.isfile(shell)


@pytest.mark.asyncio
async def test_echo_ok(tmp_path: Path) -> None:
    result = await _bash(_registry(tmp_path), "echo ok")
    assert not result.is_error
    assert "exit: 0" in result.content
    assert "ok" in result.content
    assert "timed_out" not in result.content


@pytest.mark.asyncio
async def test_nonzero_exit_is_not_dispatch_error(tmp_path: Path) -> None:
    result = await _bash(_registry(tmp_path), "false")
    assert not result.is_error
    assert "exit: 1" in result.content


@pytest.mark.asyncio
async def test_timeout_kills_group(tmp_path: Path) -> None:
    marker = tmp_path / "still-running"
    result = await _bash(
        _registry(tmp_path),
        f"sleep 5; echo alive > '{marker}'",
        timeout=1,
    )
    assert not result.is_error
    assert "timed_out: true" in result.content
    assert "exit: 124" in result.content
    await asyncio.sleep(0.2)
    assert not marker.exists()


def test_parse_timeout() -> None:
    assert parse_timeout(None) == 30
    assert parse_timeout(121) == 121
    assert parse_timeout(30.0) == 30
    with pytest.raises(ValueError):
        parse_timeout(0)
    with pytest.raises(ValueError):
        parse_timeout(-1)
    with pytest.raises(ValueError):
        parse_timeout(1.5)
    with pytest.raises(ValueError):
        parse_timeout(True)


@pytest.mark.asyncio
async def test_output_capped_keeps_tail(tmp_path: Path) -> None:
    result = await _bash(_registry(tmp_path), "python3 -c 'print(\"A\" * 40000 + \"TAIL\")'")
    assert not result.is_error
    assert len(result.content.encode("utf-8")) <= 32_768
    assert result.content.endswith("TAIL\n")


@pytest.mark.asyncio
async def test_schema_and_missing_command(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    names = [item["function"]["name"] for item in registry.to_openai_schema()]
    assert "bash" in names
    missing = await registry.dispatch(ToolCall(id="b1", name="bash", arguments={}))
    assert missing.is_error
    assert "missing argument" in missing.content


@pytest.mark.asyncio
async def test_two_bash_serialize(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    stamp = tmp_path / "order.txt"

    async def one(tag: str) -> None:
        await _bash(registry, f"echo {tag} >> '{stamp}'; sleep 0.15")

    start = time.monotonic()
    await asyncio.gather(one("a"), one("b"))
    elapsed = time.monotonic() - start
    assert elapsed >= 0.3
    assert stamp.read_text(encoding="utf-8").count("\n") == 2


def test_kill_process_group_reaps_child() -> None:
    child = subprocess.Popen(["sleep", "30"], start_new_session=True)
    kill_process_group(child.pid)
    assert child.wait(timeout=2) is not None
