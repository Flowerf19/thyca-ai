from __future__ import annotations

import asyncio
import os
import subprocess
import time
from pathlib import Path

import pytest

from thyca.core.protocol import ToolCall
from thyca.tools.builtin import register_file_tools
from thyca.tools.builtin.bash import kill_process_group, parse_timeout, select_shell
from thyca.tools.gateway import ToolGateway
from thyca.tools.path_guard import PathGuard
from thyca.tools.registry import ToolRegistry
from thyca.tools.task_store import TaskStore


def _gateway(root: Path) -> ToolGateway:
    registry = ToolRegistry()
    register_file_tools(registry, PathGuard(root))
    return ToolGateway(registry, TaskStore())


async def _bash(gateway: ToolGateway, command: str, **extra):
    return await gateway.submit(
        ToolCall(id="b1", name="bash", arguments={"command": command, **extra})
    )


def test_select_shell_is_posix_bash() -> None:
    shell = select_shell()
    assert shell.endswith("bash")
    assert os.path.isfile(shell)


@pytest.mark.asyncio
async def test_echo_ok(tmp_path: Path) -> None:
    result = await _bash(_gateway(tmp_path), "echo ok")
    assert not result.is_error
    assert "exit: 0" in result.content
    assert "ok" in result.content
    assert "timed_out" not in result.content


@pytest.mark.asyncio
async def test_nonzero_exit_is_not_submit_error(tmp_path: Path) -> None:
    result = await _bash(_gateway(tmp_path), "false")
    assert not result.is_error
    assert "exit: 1" in result.content


@pytest.mark.asyncio
async def test_timeout_kills_group(tmp_path: Path) -> None:
    marker = tmp_path / "still-running"
    result = await _bash(
        _gateway(tmp_path),
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
async def test_output_capped_head_tail_with_marker(tmp_path: Path) -> None:
    result = await _bash(_gateway(tmp_path), "python3 -c 'print(\"A\" * 40000 + \"TAIL\")'")
    assert not result.is_error
    first, rest = result.content.split("\n", 1)
    assert first == "exit: 0"
    ahead, marker, tail = rest.split("\n", 2)
    assert ahead == "A" * 8184
    assert marker.startswith("[... clipped 7245 bytes")
    assert "full output was not retained" in marker
    assert tail.endswith("TAIL\n")
    assert len(("exit: 0\n" + ahead + tail).encode("utf-8")) == 32_768


@pytest.mark.asyncio
async def test_schema_and_missing_command(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_file_tools(registry, PathGuard(tmp_path))
    gateway = ToolGateway(registry, TaskStore())
    names = [item["function"]["name"] for item in registry.to_openai_schema()]
    assert "bash" in names
    missing = await gateway.submit(ToolCall(id="b1", name="bash", arguments={}))
    assert missing.is_error
    assert "missing argument" in missing.content


@pytest.mark.asyncio
async def test_two_bash_serialize(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)
    stamp = tmp_path / "order.txt"

    async def one(tag: str) -> None:
        await _bash(gateway, f"echo {tag} >> '{stamp}'; sleep 0.15")

    start = time.monotonic()
    await asyncio.gather(one("a"), one("b"))
    elapsed = time.monotonic() - start
    assert elapsed >= 0.3
    assert stamp.read_text(encoding="utf-8").count("\n") == 2


def test_kill_process_group_reaps_child() -> None:
    child = subprocess.Popen(["sleep", "30"], start_new_session=True)
    kill_process_group(child.pid)
    assert child.wait(timeout=2) is not None


@pytest.mark.parametrize(
    "command",
    [
        "reboot",
        "/sbin/reboot",
        "sudo reboot",
        "sudo systemctl reboot",
        "echo hi; reboot",
        "echo hi && sudo shutdown -h now",
        "systemctl poweroff",
        "systemctl --no-block kexec",
        "pkill -f thyca",
        "killall thyca-ai",
        "/home/f/.local/bin/thyca --serve --stop",
        "thyca --stop",
        "FOO=1 reboot",
        "echo $(reboot)",
        "echo hi & reboot",
        "sleep 1 & thyca --serve --stop",
        "timeout 5s reboot",
    ],
)
def test_self_kill_target_refused(command: str) -> None:
    from thyca.tools.builtin.bash import _self_kill_target

    assert _self_kill_target(command) is not None


@pytest.mark.parametrize(
    "command",
    [
        "echo reboot",
        "grep -r reboot .",
        "git init",
        "thyca --serve --daemon",
        "thyca --version",
        "systemctl status thyca",
        "pgrep -af 'thyca --serve'",
        "pkill myapp",
        "kill 12345",
        "echo done",
        "echo a & echo b",
        "ls 2>&1 | head",
    ],
)
def test_self_kill_target_allowed(command: str) -> None:
    from thyca.tools.builtin.bash import _self_kill_target

    assert _self_kill_target(command) is None


@pytest.mark.asyncio
async def test_refused_command_is_tool_error_foreground_and_background(
    tmp_path: Path,
) -> None:
    gateway = _gateway(tmp_path)
    refused = await _bash(gateway, "thyca --serve --stop")
    assert refused.is_error
    assert "Refused" in refused.content
    assert "next turn" in refused.content
    # Guard runs before the background split, so this refuses even though
    # this gateway has no background support.
    bg = await _bash(gateway, "reboot", background=True)
    assert bg.is_error
    assert "Refused" in bg.content


@pytest.mark.asyncio
async def test_echo_reboot_still_runs(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)
    result = await _bash(gateway, "echo reboot")
    assert not result.is_error
    assert "reboot" in result.content
