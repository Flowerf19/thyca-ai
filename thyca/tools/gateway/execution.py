"""Uniform execution records for the ToolGateway: one id space, one status shape.

Leaf module: engines import the reply-shaping helpers from here, so this file
must never import engines or the gateway (no cycles).
"""
from __future__ import annotations

import asyncio
import itertools
import time
from collections.abc import Callable, Iterable, Iterator
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Literal

from thyca.core.protocol import truncate_to_cap

if TYPE_CHECKING:
    from thyca.tools.gateway.background import _BgProc
    from thyca.tools.task_store import _TrackedTask

Status = Literal["running", "done", "failed"]

# Execution kinds: coroutine handler task vs shell subprocess.
TASK = "task"
PROC = "proc"

# Every reply is capped as head+tail; clipped replies carry a marker.
REPLY_CAP_BYTES = 32_768
REPLY_HEAD_SHARE = 4  # head takes 1/4 of the cap, tail the rest.

# Each tracked execution retains the first megabyte of output; bytes past it
# are counted, not stored, and reads past retention report the gap honestly.
RETAINED_MAX_BYTES = 1_048_576

# Shared poll ceiling for read(id, wait): 0..60, default 0.
READ_WAIT_MAX_S = 60

# Default line count when paging with offset but no limit.
PAGE_DEFAULT_LIMIT = 200


class ExecIds:
    """Per-gateway unified id counter: every tracked execution is exec<N>."""

    def __init__(self) -> None:
        self._counter: Iterator[int] = itertools.count(1)

    def next(self) -> str:
        return f"exec{next(self._counter)}"


class Execution:
    """One tracked tool call, over a task- or proc-engine entry."""

    __slots__ = (
        "entry",
        "error",
        "exposed",
        "finished",
        "id",
        "kind",
        "listeners",
        "proc",
        "started",
        "status",
        "tool",
    )

    def __init__(self, id: str, tool: str, kind: str) -> None:
        self.id = id
        self.tool = tool
        self.kind = kind
        self.status: Status = "running"
        self.error = False
        self.started = time.time()
        self.finished: float | None = None
        self.entry: _TrackedTask | None = None
        # Live proc handle once the handler starts one (bash); reads route here.
        self.proc: _BgProc | None = None
        # True once an id-bearing reply reached the model; never-exposed
        # entries are dropped when they settle (nothing can ever poll them).
        self.exposed = False
        # onExit subscribers (delivery phase consumes these later).
        self.listeners: list[Callable[[Execution], None]] = []

    def attach_proc(self, proc: _BgProc) -> None:
        """Route this execution's reads to a live proc (bash foreground)."""
        self.proc = proc
        self.kind = PROC

    def on_exit(self, fn: Callable[[Execution], None]) -> Callable[[Execution], None]:
        """Subscribe to settle; the execution arrives finished (done/failed)."""
        self.listeners.append(fn)
        return fn

    def finish(self, *, error: bool) -> None:
        """Mark settled and fire onExit listeners (first call wins)."""
        if self.status != "running":
            return
        self.error = error
        self.finished = time.time()
        self.status = "failed" if error else "done"
        for fn in self.listeners:
            try:
                fn(self)
            except Exception:
                pass


def cap_reply(
    text: str, cap: int = REPLY_CAP_BYTES, *, more: str | None, unstored: int = 0
) -> str:
    """Cap one reply at `cap` bytes as head+tail; clipped replies carry a
    marker with the hidden byte count. `more` names how to read on (a tracked
    exec id) or None when nothing is retained (fast path). `unstored` counts
    bytes past retention that were never kept (folded into the marker)."""
    raw = text.encode("utf-8")
    if len(raw) <= cap and not unstored:
        return text
    head = head_bytes(raw, cap // REPLY_HEAD_SHARE)
    tail = tail_bytes(raw, cap - cap // REPLY_HEAD_SHARE)
    hidden = len(raw) - len(head) - len(tail) + unstored
    marker = f"\n[... clipped {hidden} bytes"
    if unstored:
        marker += f" (includes {unstored} past retention, not stored)"
    marker += f"; {more}]" if more else "; full output was not retained]"
    return head.decode("utf-8") + marker + "\n" + tail.decode("utf-8")


def page_lines(text: str, offset: int, limit: int, *, unstored: int = 0) -> str:
    """Render retained lines [offset, offset+limit); past-end offsets report
    the gap honestly instead of inventing output."""
    lines = text.splitlines()
    if offset >= len(lines):
        gap = f"(gap: line offset {offset} is past the {len(lines)} retained lines"
        if unstored:
            gap += f"; {unstored} bytes past retention were not stored"
        return gap + ")"
    page = lines[offset : offset + limit]
    out = "\n".join(page)
    if offset + limit < len(lines) or unstored:
        out += f"\n[page: lines {offset + 1}-{offset + len(page)} of {len(lines)} retained"
        if unstored:
            out += f"; {unstored} bytes past retention were not stored"
        out += "]"
    return out


def _int_arg(raw: object, name: str, minimum: int, unit: str) -> int:
    """One int-arg rule for polling/timeout args: bools out, integral
    floats (``5.0``) in like bash's timeout, bounds stay per-arg."""
    if isinstance(raw, bool):
        raise ValueError(f"{name} must be a {unit}")
    if isinstance(raw, float):
        if not raw.is_integer():
            raise ValueError(f"{name} must be a {unit}")
        raw = int(raw)
    if not isinstance(raw, int) or raw < minimum:
        raise ValueError(f"{name} must be a {unit}")
    return raw


def parse_wait(raw: Any) -> int:
    """Shared wait validation for read(id, wait): 0..60, default 0."""
    if raw is None:
        return 0
    return min(_int_arg(raw, "wait", 0, "non-negative integer"), READ_WAIT_MAX_S)


def parse_offset(raw: Any) -> int | None:
    if raw is None:
        return None
    return _int_arg(raw, "offset", 0, "non-negative integer")


def parse_limit(raw: Any) -> int | None:
    if raw is None:
        return None
    return _int_arg(raw, "limit", 1, "positive integer")


def unknown_id(kind: str, eid: str, known: Iterable[str]) -> ValueError:
    """Unknown-id error with the ``(known: …)`` trailer every engine shares."""
    listing = ", ".join(known) or "none"
    return ValueError(f"unknown {kind} id: {eid} (known: {listing})")


def render_page(
    text: str,
    offset: int | None,
    limit: int | None,
    *,
    more: str,
    unstored: int = 0,
) -> str:
    """Full-or-paged reply: the whole text capped, or one line page capped."""
    if offset is None and limit is None:
        return cap_reply(text, more=more, unstored=unstored)
    return cap_reply(
        page_lines(text, offset or 0, limit or PAGE_DEFAULT_LIMIT, unstored=unstored),
        more=more,
    )


async def await_done(done: asyncio.Event, timeout: float) -> None:
    """Wait for settle, swallowing the timeout: readers report running."""
    try:
        await asyncio.wait_for(done.wait(), timeout=timeout)
    except TimeoutError:
        pass


def require_id(args: dict) -> str:
    """Non-blank tool_read/tool_kill id (schema types arrive pre-checked)."""
    eid = args.get("id")
    if not isinstance(eid, str) or not eid.strip():
        raise ValueError("id must be a non-empty string")
    return eid


def render_exit(code: int | None, text: str, timed_out: bool) -> str:
    """Foreground shell result: ``exit: N`` + optional timeout flag + body.

    A missing code renders 124 (the timeout convention); callers pass the
    code their path reports — bash maps timeouts to 124, background procs
    report the real returncode — so each shape stays byte-identical."""
    head = f"exit: {124 if code is None else code}"
    if timed_out:
        head += "\ntimed_out: true"
    return f"{head}\n{text}"


_current: ContextVar[Execution | None] = ContextVar("current_execution", default=None)


def current_execution() -> Execution | None:
    """The in-flight execution (set while its handler runs, else None)."""
    return _current.get()


class Detached:
    """Bash background:true result: proc started, gateway retains the execution."""

    __slots__ = ("entry", "message")

    def __init__(self, entry: _BgProc, message: str) -> None:
        self.entry = entry
        self.message = message


def bind_execution(execution: Execution | None):
    """Pin the in-flight execution for proc-starting handlers."""
    return _current.set(execution)


def reset_execution(token) -> None:
    _current.reset(token)


def head_bytes(raw: bytes, size: int) -> bytes:
    """First `size` bytes cut back to a UTF-8 boundary (engines reuse this)."""
    return truncate_to_cap(raw, size)[0]


def tail_bytes(raw: bytes, size: int) -> bytes:
    """Last `size` bytes cut forward to a UTF-8 boundary."""
    tail = raw[-size:] if size < len(raw) else raw
    while tail and tail[0] & 0xC0 == 0x80:
        tail = tail[1:]
    return tail
