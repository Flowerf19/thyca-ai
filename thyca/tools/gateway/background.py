"""Background shell processes started by the bash tool.

One manager per app (daemon or one CLI run). It owns the asyncio
subprocesses, their retained output buffers, and their lifetime; the
gateway polls it behind exec ids (the ``tool_read`` tool).
Handlers run on the app's own event loop, so no thread locking is needed.
"""
from __future__ import annotations

import asyncio
import itertools
import time

from thyca.tools.builtin.bash import kill_process_group, select_shell
from thyca.tools.gateway.execution import (
    RETAINED_MAX_BYTES,
    await_done,
    cap_reply,
    head_bytes,
    render_exit,
    render_page,
    unknown_id,
)

_DRAIN_GRACE_S = 5.0
# asyncio Process.wait() resolves on pipe disconnect, not process exit (a
# surviving child can hold stdout forever), so shell exit is detected by
# polling returncode instead.
_EXIT_POLL_S = 0.05


class _BgProc:
    """One background process: retained output, timeout kill, done signal."""

    __slots__ = (
        "buf",
        "cap",
        "consumed",
        "done",
        "id",
        "killed",
        "overflow",
        "proc",
        "task",
        "timed_out",
    )

    def __init__(self, bid: str, proc: asyncio.subprocess.Process, cap: int) -> None:
        self.id = bid
        self.proc = proc
        self.buf = bytearray()
        self.cap = cap
        # Delta cursor: output bytes already returned to a reader.
        self.consumed = 0
        self.done = asyncio.Event()
        self.timed_out = False
        self.killed = False
        # Bytes past retention that were never kept (readers see the gap).
        self.overflow = 0
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
            room = max(self.cap - len(self.buf), 0)
            self.buf.extend(chunk[:room])
            self.overflow += len(chunk) - min(len(chunk), room)

    def kill(self) -> None:
        if self.proc.returncode is None:
            try:
                kill_process_group(self.proc.pid)
            except ProcessLookupError:
                pass

    def render_plain(self) -> str:
        """Foreground-shaped result, for commands that finished quickly."""
        text = bytes(self.buf).decode("utf-8", errors="replace")
        return render_exit(self.proc.returncode, text, self.timed_out)


class BackgroundProcs:
    """Registry of background bash processes, keyed by gateway exec ids
    (``bg<N>`` fallback when starting outside the gateway)."""

    def __init__(self, cap: int = RETAINED_MAX_BYTES) -> None:
        self._procs: dict[str, _BgProc] = {}
        self._counter = itertools.count(1)
        self._cap = cap

    async def start(
        self, command: str, timeout: int, cwd: str, *, id: str | None = None
    ) -> str:
        bid = id if id is not None else f"bg{next(self._counter)}"
        if bid in self._procs:
            raise ValueError(f"background id already tracked: {bid}")
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

    def get(self, bid: str) -> _BgProc | None:
        return self._procs.get(bid)

    def drop(self, bid: str) -> None:
        """Forget a finished entry whose id was never exposed (fast path)."""
        self._procs.pop(bid, None)

    def known_ids(self) -> list[str]:
        return list(self._procs)

    async def read(
        self,
        bid: str,
        wait_s: int,
        *,
        offset: int | None = None,
        limit: int | None = None,
    ) -> str:
        entry = self._procs.get(bid)
        if entry is None:
            raise unknown_id("background", bid, self.known_ids())
        if wait_s > 0 and not entry.done.is_set():
            await await_done(entry.done, wait_s)
        more = f'read more: tool_read id="{bid}"'
        if offset is not None or limit is not None:
            # Positional paging never moves the temporal delta cursor (consumed
            # below): a later plain read may repeat these bytes.
            return render_page(
                self._decode(bytes(entry.buf), entry.done.is_set()),
                offset,
                limit,
                more=more,
                unstored=entry.overflow,
            )
        raw = bytes(entry.buf[entry.consumed :])
        if entry.done.is_set():
            body = raw.decode("utf-8", errors="replace")
            entry.consumed = len(entry.buf)
        else:
            kept = head_bytes(raw, len(raw))
            entry.consumed += len(kept)
            body = kept.decode("utf-8")
        if not entry.done.is_set():
            head = "status: running"
        else:
            code = 124 if entry.proc.returncode is None else entry.proc.returncode
            head = f"status: done\nexit: {code}"
            if entry.timed_out:
                head += "\ntimed_out: true"
            if not body:
                body = "(no new output)"
        return cap_reply(f"{head}\n{body}", more=more, unstored=entry.overflow)

    @staticmethod
    def _decode(buf: bytes, finished: bool) -> str:
        if finished:
            return buf.decode("utf-8", errors="replace")
        return head_bytes(buf, len(buf)).decode("utf-8")

    async def kill(self, bid: str) -> None:
        """Terminate one tracked proc (process-group kill) and await its settle."""
        entry = self._procs.get(bid)
        if entry is None:
            raise unknown_id("background", bid, self.known_ids())
        entry.killed = True
        entry.kill()
        await await_done(entry.done, _DRAIN_GRACE_S)

    async def kill_all(self) -> None:
        pending: list[asyncio.Task] = []
        for entry in list(self._procs.values()):
            # Same flag kill() sets: shutdown-killed procs settle failed,
            # not done, so on_exit listeners see the real outcome.
            entry.killed = True
            entry.kill()
            if entry.task is not None and not entry.task.done():
                pending.append(entry.task)
        if pending:
            await asyncio.wait(pending, timeout=_DRAIN_GRACE_S)

