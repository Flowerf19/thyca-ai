"""Run a POSIX shell command. Windows hooks are intentionally empty."""
from __future__ import annotations

import asyncio
import os
import shutil
import signal
import sys
from typing import TYPE_CHECKING

from thyca.tools.registry import ToolSpec

if TYPE_CHECKING:
    from thyca.tools.builtin.background import BackgroundProcs

_TIMEOUT_DEFAULT = 30
_TIMEOUT_BACKGROUND_DEFAULT = 1800
_SOFT_DEFAULT = 60


def select_shell() -> str:
    if sys.platform == "win32":
        raise NotImplementedError("bash is not supported on Windows")
    found = shutil.which("bash")
    if found:
        return found
    if os.path.isfile("/bin/bash"):
        return "/bin/bash"
    raise FileNotFoundError("bash not found")


def kill_process_group(pid: int) -> None:
    if sys.platform == "win32":
        raise NotImplementedError("bash is not supported on Windows")
    os.killpg(pid, signal.SIGKILL)


def bash_spec(background: BackgroundProcs | None = None) -> ToolSpec:
    async def handler(args: dict) -> str:
        command = args.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ValueError("command must be a non-empty string")
        raw_bg = args.get("background")
        if raw_bg is not None and not isinstance(raw_bg, bool):
            raise ValueError("background must be a boolean")
        if raw_bg:
            if background is None:
                raise ValueError("background is not available in this context")
            raw = args.get("timeout")
            timeout = _TIMEOUT_BACKGROUND_DEFAULT if raw is None else parse_timeout(raw)
            bid = await background.start(command, timeout, os.getcwd())
            return (
                f"started: {bid}\n"
                f"running in background (timeout {timeout}s). "
                "Poll progress and the result with bash_read."
            )
        if background is not None:
            # Auto-escalate: quick commands return like foreground; a command
            # still running after the soft window moves to background instead
            # of blocking the turn.
            raw = args.get("timeout")
            hard = _TIMEOUT_BACKGROUND_DEFAULT if raw is None else parse_timeout(raw)
            return await background.start_and_wait(
                command, hard, os.getcwd(), _SOFT_DEFAULT
            )
        return await _run(command, parse_timeout(args.get("timeout")))

    return ToolSpec(
        name="bash",
        description=(
            "Run a POSIX shell command on this machine (no sandbox). "
            "cwd is the process working directory. Commands that finish within "
            "~60 seconds return their result directly. A still-running command "
            "then automatically moves to background and the tool returns "
            "'still running: bg<N>' — poll progress and the result with bash_read "
            "instead of re-running it. Use background: true when you know from "
            "the start the command is long (builds, OCR, servers): the id comes "
            "back immediately. timeout is the hard cap that kills the process "
            "group; backgrounded commands default to 1800 seconds."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "integer"},
                "background": {"type": "boolean"},
            },
            "required": ["command"],
            "additionalProperties": False,
        },
        handler=handler,
        parallel_safe=False,
        resource_key=lambda _args: "bash",
    )


def parse_timeout(raw: object) -> int:
    if raw is None:
        return _TIMEOUT_DEFAULT
    if isinstance(raw, bool):
        raise ValueError("timeout must be a positive integer")
    if isinstance(raw, float) and raw.is_integer():
        raw = int(raw)
    if not isinstance(raw, int) or raw < 1:
        raise ValueError("timeout must be a positive integer")
    return raw


async def _run(command: str, timeout: int) -> str:
    proc = await asyncio.create_subprocess_exec(
        select_shell(),
        "-c",
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=os.getcwd(),
        start_new_session=True,
    )
    timed_out = False
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        timed_out = True
        if proc.pid:
            try:
                kill_process_group(proc.pid)
            except ProcessLookupError:
                pass
        out, _ = await proc.communicate()
    text = (out or b"").decode("utf-8", errors="replace")
    if timed_out:
        return f"exit: 124\ntimed_out: true\n{text}"
    code = 124 if proc.returncode is None else proc.returncode
    return f"exit: {code}\n{text}"
