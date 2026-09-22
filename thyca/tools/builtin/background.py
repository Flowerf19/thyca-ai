"""Background shell processes started by the bash tool.

One manager per app (daemon or one CLI run). It owns the asyncio
subprocesses, their bounded output tails, and their lifetime; the
``bash_read`` tool polls it. Handlers run on the app's own event loop,
so no thread locking is needed.
"""
from __future__ import annotations

import asyncio
import itertools
import time

from thyca.core.protocol import RESULT_CAP_BYTES
from thyca.tools.builtin.bash import kill_process_group, select_shell
from thyca.tools.registry import ToolSpec

_READ_WAIT_MAX_S = 60
_DRAIN_GRACE_S = 5.0
# asyncio Process.wait() resolves on pipe disconnect, not process exit (a
# surviving child can hold stdout forever), so shell exit is detected by
# polling returncode instead.
_EXIT_POLL_S = 0.05


class _BgProc:
    """One background process: output tail, timeout kill, done signal."""

    __slots__ = ("buf", "cap", "done", "id", "proc", "task", "timed_out")

    def __init__(self, bid: str, proc: asyncio.subprocess.Process, cap: int) -> None:
        self.id = bid
        self.proc = proc
        self.buf = bytearray()
        self.cap = cap
        self.done = asyncio.Event()
        self.timed_out = False
        self.task: asyncio.Task | None = None

    async def run(self, timeout: int) -> None:
        proc = self.proc
        assert proc.stdout is not None
        try:
            drain = asyncio.create_task(self._drain(proc.stdout))
            deadline = time.monotonic() + timeout
            while proc.returncode is None:
                if time.monotonic() >= deadline:
                    self.timed_out = True
                    try:
                        kill_process_group(proc.pid)
                    except ProcessLookupError:
                        pass
                    break
                await asyncio.sleep(_EXIT_POLL_S)
            # After exit (or kill), let the pipe drain briefly: normal children
            # hit EOF at once, but a grandchild that kept stdout (setsid'd
            # daemon) must not hold the proc open past this grace.
            if not drain.done():
                try:
                    await asyncio.wait_for(asyncio.shield(drain), timeout=_DRAIN_GRACE_S)
                except TimeoutError:
                    drain.cancel()
        finally:
            self.done.set()

    async def _drain(self, stdout: asyncio.StreamReader) -> None:
        while chunk := await stdout.read(65536):
            self.buf.extend(chunk)
            if len(self.buf) > self.cap:
                del self.buf[: len(self.buf) - self.cap]

    def kill(self) -> None:
        if self.proc.returncode is None:
            try:
                kill_process_group(self.proc.pid)
            except ProcessLookupError:
                pass

    def render(self) -> str:
        text = bytes(self.buf).decode("utf-8", errors="replace")
        if not self.done.is_set():
            return f"status: running\n{text}"
        code = 124 if self.proc.returncode is None else self.proc.returncode
        head = f"status: done\nexit: {code}"
        if self.timed_out:
            head += "\ntimed_out: true"
        return f"{head}\n{text}"

    def render_plain(self) -> str:
        """Foreground-shaped result, for commands that finished quickly."""
        text = bytes(self.buf).decode("utf-8", errors="replace")
        code = 124 if self.proc.returncode is None else self.proc.returncode
        head = f"exit: {code}"
        if self.timed_out:
            head += "\ntimed_out: true"
        return f"{head}\n{text}"


class BackgroundProcs:
    """Registry of background bash processes, keyed by ``bg<N>`` ids."""

    def __init__(self, cap: int = RESULT_CAP_BYTES) -> None:
        self._procs: dict[str, _BgProc] = {}
        self._counter = itertools.count(1)
        self._cap = cap

    async def start(self, command: str, timeout: int, cwd: str) -> str:
        bid = f"bg{next(self._counter)}"
        proc = await asyncio.create_subprocess_exec(
            select_shell(),
            "-c",
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
            start_new_session=True,
        )
        entry = _BgProc(bid, proc, self._cap)
        entry.task = asyncio.create_task(entry.run(timeout))
        self._procs[bid] = entry
        return bid

    async def start_and_wait(
        self, command: str, timeout: int, cwd: str, soft_s: int
    ) -> str:
        """Run like foreground for quick commands: return the plain result when
        the proc finishes within soft_s, else hand back the background id."""
        bid = await self.start(command, timeout, cwd)
        entry = self._procs[bid]
        # When the hard cap is within the soft window there is nothing to
        # escalate: the run task kills the proc at the cap, so wait past it.
        wait_s = soft_s if soft_s < timeout else timeout + _DRAIN_GRACE_S
        try:
            await asyncio.wait_for(entry.done.wait(), timeout=wait_s)
        except TimeoutError:
            return (
                f"still running: {bid}\n"
                f"moved to background (hard timeout {timeout}s). "
                "Poll progress and the result with bash_read."
            )
        return entry.render_plain()

    def known_ids(self) -> list[str]:
        return list(self._procs)

    async def read(self, bid: str, wait_s: int) -> str:
        entry = self._procs.get(bid)
        if entry is None:
            known = ", ".join(self.known_ids()) or "none"
            raise ValueError(f"unknown background id: {bid} (known: {known})")
        if wait_s > 0 and not entry.done.is_set():
            try:
                await asyncio.wait_for(entry.done.wait(), timeout=wait_s)
            except TimeoutError:
                pass
        return entry.render()

    async def kill_all(self) -> None:
        pending: list[asyncio.Task] = []
        for entry in list(self._procs.values()):
            entry.kill()
            if entry.task is not None and not entry.task.done():
                pending.append(entry.task)
        if pending:
            await asyncio.wait(pending, timeout=_DRAIN_GRACE_S)


def bash_read_spec(background: BackgroundProcs | None) -> ToolSpec:
    async def handler(args: dict) -> str:
        if background is None:
            raise ValueError("no background process manager in this context")
        bid = args.get("id")
        if not isinstance(bid, str) or not bid.strip():
            raise ValueError("id must be a non-empty string")
        raw_wait = args.get("wait")
        # Kept inline (not bash.parse_timeout): wait allows 0 and clamps to
        # _READ_WAIT_MAX_S instead of requiring a positive integer.
        if raw_wait is None:
            wait_s = 0
        elif isinstance(raw_wait, bool) or not isinstance(raw_wait, int) or raw_wait < 0:
            raise ValueError("wait must be a non-negative integer")
        else:
            wait_s = min(raw_wait, _READ_WAIT_MAX_S)
        return await background.read(bid, wait_s)

    return ToolSpec(
        name="bash_read",
        description=(
            "Read the status and output-so-far of a background bash process "
            "started with the bash tool's background option. wait: seconds to "
            "wait for completion before replying (0..60, default 0)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "wait": {"type": "integer"},
            },
            "required": ["id"],
            "additionalProperties": False,
        },
        handler=handler,
        parallel_safe=True,
        resource_key=lambda _args: "bash",
    )

