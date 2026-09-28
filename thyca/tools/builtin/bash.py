"""Run a POSIX shell command. Windows hooks are intentionally empty."""
from __future__ import annotations

import asyncio
import os
import re
import shlex
import shutil
import signal
import sys
from typing import TYPE_CHECKING

from thyca.tools.gateway.execution import (
    Detached,
    _int_arg,
    current_execution,
    render_exit,
)
from thyca.tools.registry import ToolSpec

if TYPE_CHECKING:
    from thyca.tools.gateway.background import BackgroundProcs

_TIMEOUT_DEFAULT = 30
_TIMEOUT_BACKGROUND_DEFAULT = 1800

# The agent must never kill the serve process hosting its own turn: stopping
# serve mid-turn (e.g. `thyca --serve --stop` after editing mcpServers)
# SIGTERMs the parent, so the turn never completes. Config edits already
# apply on the next turn; a human restarts serve outside chat when needed.
_SELF_KILL_BINARIES = frozenset(
    {"reboot", "shutdown", "poweroff", "halt", "init", "telinit"}
)
_SYSTEMCTL_ACTIONS = frozenset({"reboot", "poweroff", "halt", "kexec"})
_KILL_THYCA_BINARIES = frozenset({"pkill", "killall"})  # pgrep stays allowed: read-only diagnostics
_WRAPPER_BINARIES = frozenset({"sudo", "env", "nohup", "nice", "timeout", "command"})
_SEGMENT_SPLIT = re.compile(r"&&|\|\||[;|`\n()&]+")
_DURATION_RE = re.compile(r"\d+[smhd]?")  # timeout/nice durations: 5, 5s, 2m ...


def _self_kill_target(command: str) -> str | None:
    """Self-kill attempt in ``command``, or None when allowed.

    Each `;`/`&`/`&&`/`||`/`|`-separated segment is judged by its first token
    (after sudo/env-style wrappers), so `echo reboot` stays allowed while
    `sudo reboot` and `cmd; reboot` are refused. `kill <pid>` by raw pid is
    intentionally not covered (the handler has no serve-root context), and
    nested-exec wrappers beyond sudo/env/timeout (e.g. `bash -c`, `sudo -u`)
    are best-effort: the guard targets accidental self-kills, not evasion.
    """
    for segment in _SEGMENT_SPLIT.split(command):
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError:
            tokens = segment.split()
        idx = 0
        wrapped = False
        while idx < len(tokens):
            token = tokens[idx]
            base = token.rsplit("/", 1)[-1]
            if base in _WRAPPER_BINARIES or "=" in token:
                wrapped = True
                idx += 1
                continue
            if wrapped and (
                token.startswith("-")
                or token.isnumeric()
                or _DURATION_RE.fullmatch(token) is not None
            ):
                idx += 1
                continue
            break
        if idx >= len(tokens):
            continue
        base = tokens[idx].rsplit("/", 1)[-1]
        rest = tokens[idx + 1 :]
        if base in _SELF_KILL_BINARIES:
            return base
        if base == "thyca" and "--stop" in rest:
            return "thyca --stop"
        if base == "systemctl" and any(arg in _SYSTEMCTL_ACTIONS for arg in rest):
            return f"systemctl {next(arg for arg in rest if arg in _SYSTEMCTL_ACTIONS)}"
        if base in _KILL_THYCA_BINARIES and any("thyca" in arg.lower() for arg in rest):
            return f"{base} thyca"
    return None


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
        # Schema types arrive pre-checked by the registry (X3); only the
        # non-blank command rule stays here as domain validation.
        command = args["command"]
        if not command.strip():
            raise ValueError("command must be a non-empty string")
        target = _self_kill_target(command)
        if target is not None:
            raise ValueError(
                f"Refused: {target} would stop Thyca itself or reboot the host. "
                "mcpServers/model edits apply automatically on the next turn; "
                "restart serve manually outside chat if truly needed."
            )
        raw_bg = args.get("background")
        execution = current_execution()
        eid = execution.id if execution is not None else None
        if raw_bg:
            if background is None:
                raise ValueError("background is not available in this context")
            raw = args.get("timeout")
            timeout = _TIMEOUT_BACKGROUND_DEFAULT if raw is None else parse_timeout(raw)
            bid = await background.start(command, timeout, os.getcwd(), id=eid)
            message = (
                f"started: {bid}\n"
                f"running in background (timeout {timeout}s). "
                "Poll progress and the result with tool_read."
            )
            if execution is None:
                return message
            entry = background.get(bid)
            assert entry is not None
            return Detached(entry, message)
        if background is not None:
            # The gateway owns the soft timeout now: wait for the proc up to
            # the hard cap; slow commands become tracked executions there.
            raw = args.get("timeout")
            hard = _TIMEOUT_BACKGROUND_DEFAULT if raw is None else parse_timeout(raw)
            bid = await background.start(command, hard, os.getcwd(), id=eid)
            entry = background.get(bid)
            assert entry is not None
            if execution is not None:
                execution.attach_proc(entry)
            await entry.done.wait()
            return entry.render_plain()
        return await _run(command, parse_timeout(args.get("timeout")))

    return ToolSpec(
        name="bash",
        description=(
            "Run a POSIX shell command on this machine (no sandbox). "
            "cwd is the process working directory. Commands that finish within "
            "~60 seconds return their result directly. A still-running command "
            "then automatically moves to background and the tool returns "
            "'still running: exec<N>' — poll progress and the result with tool_read "
            "instead of re-running it. Use background: true when you know from "
            "the start the command is long (builds, OCR, servers): the id comes "
            "back immediately. timeout is the hard cap that kills the process "
            "group; backgrounded commands default to 1800 seconds. Commands "
            "that would stop Thyca itself or reboot the host (reboot, "
            "shutdown, thyca --stop, ...) are refused."
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
    return _int_arg(raw, "timeout", 1, "positive integer")


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
        return render_exit(124, text, True)
    return render_exit(proc.returncode, text, False)
