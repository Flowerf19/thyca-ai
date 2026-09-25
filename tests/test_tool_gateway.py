from __future__ import annotations

import asyncio
import contextlib

import pytest

from pathlib import Path

from thyca.core.protocol import ToolCall, ToolResult
from thyca.tools.builtin import register_file_tools
from thyca.tools.gateway.background import BackgroundProcs
from thyca.tools.gateway import Execution, ToolGateway
from thyca.tools.gateway.execution import (
    TASK,
    cap_reply,
    page_lines,
    parse_limit,
    parse_offset,
    parse_wait,
)
from thyca.tools.gateway.gateway import _RETAIN_CAP
from thyca.tools.gateway.policy import Policy, PolicyDenied
from thyca.tools.path_guard import PathGuard
from thyca.tools.registry import ToolRegistry, ToolSpec
from thyca.tools.task_store import TaskStore


def _echo_spec(**overrides) -> ToolSpec:
    async def echo(args: dict) -> str:
        return str(args.get("text", ""))

    fields = {
        "name": "echo",
        "description": "echo text",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        "handler": echo,
    }
    fields.update(overrides)
    return ToolSpec(**fields)


def _gateway(soft: int = 60, **kwargs) -> tuple[ToolGateway, ToolRegistry]:
    store = TaskStore()
    registry = ToolRegistry()
    return ToolGateway(registry, store, soft_timeout_s=soft, **kwargs), registry


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


async def _submit(gateway: ToolGateway, name: str, **args):
    return await gateway.submit(ToolCall(id="t1", name=name, arguments=args))


# --- submit: policy seam ---


@pytest.mark.asyncio
async def test_policy_default_allows() -> None:
    gateway, registry = _gateway()
    registry.register(_echo_spec())
    assert isinstance(gateway._policy, Policy)
    result = await _submit(gateway, "echo", text="hi")
    assert not result.is_error
    assert result.content == "hi"


@pytest.mark.asyncio
async def test_policy_deny_returns_error_without_running_handler() -> None:
    hits = {"n": 0}

    async def echo(args: dict) -> str:
        hits["n"] += 1
        return "ran"

    class Deny(Policy):
        def check(self, call) -> None:
            raise PolicyDenied("nope")

    gateway, registry = _gateway(policy=Deny())
    registry.register(_echo_spec(handler=echo))
    result = await _submit(gateway, "echo", text="x")
    assert hits["n"] == 0
    assert result.is_error
    assert result.content == "nope"
    assert result.tool_call_id == "t1"


@pytest.mark.asyncio
async def test_policy_deny_empty_message_falls_back() -> None:
    class Deny(Policy):
        def check(self, call) -> None:
            raise PolicyDenied()

    gateway, registry = _gateway(policy=Deny())
    registry.register(_echo_spec())
    result = await _submit(gateway, "echo", text="x")
    assert result.is_error
    assert result.content == "denied by policy"


# --- submit: equivalence with registry.dispatch ---


@pytest.mark.asyncio
async def test_submit_keeps_call_id_and_name() -> None:
    gateway, registry = _gateway()
    registry.register(_echo_spec())
    result = await gateway.submit(ToolCall(id="c1", name="echo", arguments={"text": "hi"}))
    assert result == ToolResult(tool_call_id="c1", name="echo", content="hi", is_error=False)


@pytest.mark.asyncio
async def test_unknown_missing_extra_and_parse_error_do_not_run_handler() -> None:
    hits = {"n": 0}

    async def echo(args: dict) -> str:
        hits["n"] += 1
        return "ran"

    gateway, registry = _gateway()
    registry.register(_echo_spec(handler=echo))
    unknown = await gateway.submit(ToolCall(id="u", name="nope", arguments={"text": "x"}))
    missing = await gateway.submit(ToolCall(id="m", name="echo", arguments={}))
    extra = await gateway.submit(
        ToolCall(id="e", name="echo", arguments={"text": "x", "bonus": 1})
    )
    parsed = await gateway.submit(
        ToolCall(id="p", name="echo", arguments={"text": "x"}, parse_error="bad json")
    )
    assert hits["n"] == 0
    assert unknown.is_error and unknown.tool_call_id == "u"
    assert missing.content == "missing argument: text"
    assert extra.content.startswith("unexpected argument")
    assert parsed.content == "bad json"


@pytest.mark.asyncio
async def test_handler_exception_bad_type_and_error_result() -> None:
    async def boom(args: dict) -> str:
        raise RuntimeError("failed")

    async def bad_type(args: dict):
        return 123

    async def bad_result(args: dict):
        return ToolResult(tool_call_id="x", name="bad", content="bad", is_error=True)

    gateway, registry = _gateway()
    registry.register(_echo_spec(name="boom", handler=boom))
    registry.register(_echo_spec(name="bad_type", handler=bad_type))
    registry.register(_echo_spec(name="bad", handler=bad_result))
    err = await _submit(gateway, "boom", text="x")
    typed = await _submit(gateway, "bad_type", text="x")
    bad = await _submit(gateway, "bad", text="x")
    assert err.is_error and err.content == "failed" and err.tool_call_id == "t1"
    assert typed.is_error and typed.content == "handler must return str or ToolResult"
    assert bad.is_error and bad.content == "bad"


@pytest.mark.asyncio
async def test_same_resource_serializes_different_keys_overlap() -> None:
    events: list[str] = []

    async def work(args: dict) -> str:
        events.append(f"start-{args['text']}")
        await asyncio.sleep(0.03)
        events.append(f"end-{args['text']}")
        return args["text"]

    gateway, registry = _gateway()
    registry.register(_echo_spec(handler=work, resource_key=lambda args: args["text"]))
    same = await asyncio.gather(
        gateway.submit(ToolCall(id="a", name="echo", arguments={"text": "k"})),
        gateway.submit(ToolCall(id="b", name="echo", arguments={"text": "k"})),
    )
    assert [r.content for r in same] == ["k", "k"]
    assert events[:4] == ["start-k", "end-k", "start-k", "end-k"]

    events.clear()
    await asyncio.gather(
        gateway.submit(ToolCall(id="c", name="echo", arguments={"text": "one"})),
        gateway.submit(ToolCall(id="d", name="echo", arguments={"text": "two"})),
    )
    assert events[0].startswith("start-") and events[1].startswith("start-")


# --- submit: fast path + unified ids ---


@pytest.mark.asyncio
async def test_fast_handler_resolves_inline_without_retained_entry() -> None:
    gateway, registry = _gateway(soft=1)
    registry.register(_slow_spec("quick", _return_soon("quick")))
    result = await _submit(gateway, "quick")
    assert not result.is_error
    assert result.content == "quick"
    assert "still running" not in result.content
    assert gateway.executions == {}


@pytest.mark.asyncio
async def test_exec_ids_are_unified_and_incremental() -> None:
    gateway, registry = _gateway(soft=1)
    registry.register(_slow_spec("slow", _return_soon("done!", delay=1.5)))
    first = await _submit(gateway, "slow")
    second = await _submit(gateway, "slow")
    assert first.content.splitlines()[0] == "still running: exec1"
    assert second.content.splitlines()[0] == "still running: exec2"
    assert sorted(gateway.executions) == ["exec1", "exec2"]


@pytest.mark.asyncio
async def test_slow_handler_tracked_then_settles() -> None:
    gateway, registry = _gateway(soft=1)
    registry.register(_slow_spec("slow", _return_soon("done!", delay=1.5)))
    result = await _submit(gateway, "slow")
    assert not result.is_error
    assert result.content.startswith("still running: exec1\n")
    assert "poll the result with tool_read" in result.content
    execution = gateway.executions["exec1"]
    assert isinstance(execution, Execution)
    assert execution.tool == "slow"
    assert execution.kind == "task"
    assert execution.status == "running"
    assert execution.exposed is True
    assert execution.finished is None
    await asyncio.wait_for(execution.entry.done.wait(), timeout=5)
    await asyncio.sleep(0)
    assert execution.status == "done"
    assert execution.error is False
    assert execution.finished is not None and execution.finished >= execution.started
    assert execution.entry.content == "done!"


@pytest.mark.asyncio
async def test_handler_error_after_tracking_marks_failed() -> None:
    async def boom_factory():
        await asyncio.sleep(1.5)
        raise RuntimeError("boom")

    gateway, registry = _gateway(soft=1)
    registry.register(_slow_spec("slow", boom_factory))
    result = await _submit(gateway, "slow")
    assert result.content.startswith("still running: exec1")
    execution = gateway.executions["exec1"]
    await asyncio.wait_for(execution.entry.done.wait(), timeout=5)
    await asyncio.sleep(0)
    assert execution.status == "failed"
    assert execution.error is True
    assert execution.entry.content == "boom"
    assert execution.entry.is_error is True


@pytest.mark.asyncio
async def test_handler_raising_timeout_error_is_not_tracking() -> None:
    async def timeout_factory():
        raise TimeoutError("inner timeout")

    gateway, registry = _gateway(soft=1)
    registry.register(_slow_spec("slow", timeout_factory))
    result = await _submit(gateway, "slow")
    assert result.is_error
    assert "inner timeout" in result.content
    assert "still running" not in result.content
    assert gateway.executions == {}


@pytest.mark.asyncio
async def test_cancelled_submit_leaves_no_execution() -> None:
    gateway, registry = _gateway(soft=1)
    registry.register(_slow_spec("slow", _return_soon("late", delay=1.5)))
    pending = asyncio.create_task(_submit(gateway, "slow"))
    await asyncio.sleep(0.2)
    pending.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await pending
    await asyncio.sleep(1.6)
    assert gateway.executions == {}


@pytest.mark.asyncio
async def test_on_exit_fires_on_settle() -> None:
    async def boom_factory():
        await asyncio.sleep(1.5)
        raise RuntimeError("boom")

    gateway, registry = _gateway(soft=1)
    registry.register(_slow_spec("slow", _return_soon("done!", delay=1.5)))
    registry.register(_slow_spec("fail", boom_factory))
    seen: list[tuple[str, str]] = []
    await _submit(gateway, "slow")
    gateway.executions["exec1"].on_exit(lambda e: seen.append((e.id, e.status)))
    await _submit(gateway, "fail")
    gateway.executions["exec2"].on_exit(lambda e: seen.append((e.id, e.status)))
    for execution in gateway.executions.values():
        await asyncio.wait_for(execution.entry.done.wait(), timeout=5)
    await asyncio.sleep(0)
    assert sorted(seen) == [("exec1", "done"), ("exec2", "failed")]


# --- caps: head+tail with marker ---


@pytest.mark.asyncio
async def test_huge_output_caps_head_tail_with_marker() -> None:
    async def huge(args: dict) -> str:
        return "A" * 40_000 + "TAIL"

    gateway, registry = _gateway()
    registry.register(_echo_spec(name="huge", handler=huge))
    result = await _submit(gateway, "huge", text="x")
    assert not result.is_error
    head, marker, tail = result.content.split("\n", 2)
    assert head == "A" * 8192
    assert tail == "A" * (24576 - 4) + "TAIL"
    assert marker.startswith("[... clipped 7236 bytes")
    assert "full output was not retained" in marker


@pytest.mark.asyncio
async def test_cap_boundary_is_exact_and_multibyte_safe() -> None:
    async def exact(args: dict) -> str:
        return "B" * 32_768

    async def wide(args: dict) -> str:
        return "á" * 40_000

    gateway, registry = _gateway()
    registry.register(_echo_spec(name="exact", handler=exact))
    registry.register(_echo_spec(name="wide", handler=wide))
    kept = await _submit(gateway, "exact", text="x")
    assert kept.content == "B" * 32_768
    clipped = await _submit(gateway, "wide", text="x")
    assert "clipped" in clipped.content
    assert clipped.content.encode("utf-8").decode("utf-8") == clipped.content
    assert "�" not in clipped.content


@pytest.mark.asyncio
async def test_small_result_cap_scales_head_tail() -> None:
    async def huge(args: dict) -> str:
        return "á" * 40_000

    gateway, registry = _gateway(result_cap=20)
    registry.register(_echo_spec(name="huge", handler=huge))
    result = await _submit(gateway, "huge", text="x")
    assert not result.is_error
    assert "�" not in result.content
    assert "[... clipped 79982 bytes" in result.content


def test_cap_reply_tracked_pointer_names_read_more() -> None:
    clipped = cap_reply("A" * 40_000, more='read more: tool_read id="exec1"')
    assert "[... clipped 7232 bytes" in clipped
    assert 'read more: tool_read id="exec1"' in clipped


# --- read: status + output + paging ---


@pytest.mark.asyncio
async def test_read_running_then_done_via_wait() -> None:
    gateway, registry = _gateway(soft=1)
    registry.register(_slow_spec("slow", _return_soon("done!", delay=1.5)))
    await _submit(gateway, "slow")
    running = await gateway.read("exec1")
    assert not running.is_error
    assert running.tool_call_id == "exec1"
    assert running.content == "status: running\nexec1 is still executing"
    done = await gateway.read("exec1", wait=5)
    assert not done.is_error
    assert done.content == "done!"


@pytest.mark.asyncio
async def test_read_error_result_preserved() -> None:
    async def boom_factory():
        await asyncio.sleep(1.5)
        raise RuntimeError("boom")

    gateway, registry = _gateway(soft=1)
    registry.register(_slow_spec("slow", boom_factory))
    await _submit(gateway, "slow")
    read = await gateway.read("exec1", wait=5)
    assert read.is_error
    assert read.content == "boom"


@pytest.mark.asyncio
async def test_read_unknown_id_lists_known() -> None:
    gateway, registry = _gateway(soft=1)
    registry.register(_slow_spec("slow", _return_soon("done!", delay=1.5)))
    with pytest.raises(ValueError, match=r"unknown execution id: exec99 \(known: none\)"):
        await gateway.read("exec99")
    await _submit(gateway, "slow")
    with pytest.raises(ValueError, match=r"unknown execution id: exec99 \(known: exec1\)"):
        await gateway.read("exec99")


@pytest.mark.asyncio
async def test_read_wait_is_clamped_but_returns_when_done() -> None:
    gateway, registry = _gateway(soft=1)
    registry.register(_slow_spec("slow", _return_soon("done!", delay=1.5)))
    await _submit(gateway, "slow")
    read = await gateway.read("exec1", wait=1000)
    assert not read.is_error
    assert read.content == "done!"


@pytest.mark.asyncio
async def test_read_rejects_bad_wait_offset_limit() -> None:
    gateway, registry = _gateway(soft=1)
    registry.register(_slow_spec("slow", _return_soon("done!", delay=1.5)))
    await _submit(gateway, "slow")
    for raw in (-1, True, "5", 1.5):
        with pytest.raises(ValueError, match="wait must be"):
            await gateway.read("exec1", wait=raw)
    for raw in (-1, True, "0"):
        with pytest.raises(ValueError, match="offset must be"):
            await gateway.read("exec1", offset=raw)
    for raw in (0, -1, True, "10"):
        with pytest.raises(ValueError, match="limit must be"):
            await gateway.read("exec1", limit=raw)


@pytest.mark.asyncio
async def test_read_pages_settled_output_by_lines() -> None:
    content = "\n".join(f"line{i}" for i in range(500))
    gateway, registry = _gateway(soft=1)
    registry.register(_slow_spec("slow", _return_soon(content, delay=1.5)))
    await _submit(gateway, "slow")
    full = await gateway.read("exec1", wait=5)
    assert full.content == content
    page = await gateway.read("exec1", offset=10, limit=5)
    assert page.content.splitlines()[:5] == [f"line{i}" for i in range(10, 15)]
    assert "[page: lines 11-15 of 500 retained]" in page.content
    tail = await gateway.read("exec1", offset=495)
    assert tail.content == "\n".join(f"line{i}" for i in range(495, 500))
    gap = await gateway.read("exec1", offset=500, limit=10)
    assert gap.content == "(gap: line offset 500 is past the 500 retained lines)"


@pytest.mark.asyncio
async def test_clipped_read_carries_marker_and_pointer() -> None:
    content = ("A" * 100 + "\n") * 400
    gateway, registry = _gateway(soft=1)
    registry.register(_slow_spec("slow", _return_soon(content, delay=1.5)))
    await _submit(gateway, "slow")
    clipped = await gateway.read("exec1", wait=5)
    assert "[... clipped 7632 bytes" in clipped.content
    assert 'read more: tool_read id="exec1"' in clipped.content
    page = await gateway.read("exec1", offset=390, limit=20)
    assert page.content == "".join("A" * 100 + "\n" for _ in range(10)).rstrip("\n")


@pytest.mark.asyncio
async def test_overflow_disclosed_and_gap_honest() -> None:
    gateway, registry = _gateway(soft=1)
    registry.register(_slow_spec("slow", _return_soon("L\n" * 600_000, delay=1.5)))
    await _submit(gateway, "slow")
    clipped = await gateway.read("exec1", wait=10)
    assert "[... clipped 1167232 bytes" in clipped.content
    assert "151424 past retention, not stored" in clipped.content
    gap = await gateway.read("exec1", offset=600_000, limit=10)
    assert gap.content == (
        "(gap: line offset 600000 is past the 524288 retained lines; "
        "151424 bytes past retention were not stored)"
    )


# --- kill ---


@pytest.mark.asyncio
async def test_kill_unknown_id_lists_known() -> None:
    gateway, registry = _gateway(soft=1)
    registry.register(_slow_spec("slow", _return_soon("done!", delay=30)))
    with pytest.raises(ValueError, match=r"unknown execution id: exec99 \(known: none\)"):
        await gateway.kill("exec99")
    await _submit(gateway, "slow")
    with pytest.raises(ValueError, match=r"unknown execution id: exec99 \(known: exec1\)"):
        await gateway.kill("exec99")


@pytest.mark.asyncio
async def test_kill_running_task_marks_failed() -> None:
    gateway, registry = _gateway(soft=1)
    registry.register(_slow_spec("slow", _return_soon("late", delay=30)))
    await _submit(gateway, "slow")
    assert await gateway.kill("exec1") == "killed exec1"
    execution = gateway.executions["exec1"]
    assert execution.status == "failed"
    assert execution.error is True
    read = await gateway.read("exec1")
    assert read.is_error
    assert read.content == "task was cancelled"
    assert await gateway.kill("exec1") == "exec1 already failed"


@pytest.mark.asyncio
async def test_kill_finished_execution_reports_status() -> None:
    gateway, registry = _gateway(soft=1)
    registry.register(_slow_spec("slow", _return_soon("done!", delay=1.5)))
    await _submit(gateway, "slow")
    await gateway.read("exec1", wait=5)
    assert await gateway.kill("exec1") == "exec1 already done"


# --- shutdown ---


@pytest.mark.asyncio
async def test_shutdown_cancels_tasks_and_kills_procs(tmp_path: Path) -> None:
    store = TaskStore()
    registry = ToolRegistry()
    procs = BackgroundProcs()
    register_file_tools(registry, PathGuard(tmp_path), procs)
    gateway = ToolGateway(registry, store, procs, soft_timeout_s=1)
    registry.register(_slow_spec("slow", _return_soon("late", delay=30)))
    await gateway.submit(ToolCall(id="t1", name="slow", arguments={}))
    started = await gateway.submit(
        ToolCall(id="t2", name="bash", arguments={"command": "sleep 30", "background": True})
    )
    assert started.content.splitlines()[0] == "started: exec2"
    task_handle = gateway.executions["exec1"].entry.task
    proc_entry = procs.get("exec2")
    assert proc_entry is not None
    await gateway.shutdown()
    assert gateway.executions == {}
    assert task_handle.cancelled()
    assert proc_entry.done.is_set()
    assert proc_entry.proc.returncode is not None


# --- shared read-validation + paging units ---


def test_parse_wait_offset_limit() -> None:
    assert parse_wait(None) == 0
    assert parse_wait(0) == 0
    assert parse_wait(60) == 60
    assert parse_wait(1000) == 60
    assert parse_wait(5.0) == 5
    for raw in (-1, True, "5", 1.5, [], {}):
        with pytest.raises(ValueError, match="wait must be"):
            parse_wait(raw)
    assert parse_offset(None) is None
    assert parse_offset(0) == 0
    assert parse_offset(2.0) == 2
    for raw in (-1, True, "0", 2.5):
        with pytest.raises(ValueError, match="offset must be"):
            parse_offset(raw)
    assert parse_limit(None) is None
    assert parse_limit(1) == 1
    assert parse_limit(2.0) == 2
    for raw in (0, -1, True, "10", 2.5):
        with pytest.raises(ValueError, match="limit must be"):
            parse_limit(raw)


def test_page_lines_shapes() -> None:
    text = "\n".join(f"line{i}" for i in range(10))
    assert page_lines(text, 0, 200) == text
    assert page_lines(text, 8, 200) == "line8\nline9"
    mid = page_lines(text, 2, 3)
    assert mid == "line2\nline3\nline4\n[page: lines 3-5 of 10 retained]"
    assert page_lines(text, 10, 5) == "(gap: line offset 10 is past the 10 retained lines)"
    gap = page_lines(text, 99, 5, unstored=12)
    assert "past the 10 retained lines; 12 bytes past retention were not stored" in gap


# --- F35: gateway eviction (executions + locks) ---


def _fast_gateway(soft: int = 0) -> tuple[ToolGateway, ToolRegistry]:
    async def echo(args: dict) -> str:
        return str(args.get("text", ""))

    registry = ToolRegistry()
    registry.register(ToolSpec(
        name="echo",
        description="echo",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}},
        handler=echo,
        parallel_safe=True,
    ))
    return ToolGateway(registry, TaskStore(), soft_timeout_s=soft), registry


async def test_f35_slow_submits_stay_bounded_and_untrack_engines() -> None:
    gateway, registry = _fast_gateway(soft=0)
    gate = asyncio.Event()

    async def gated(args: dict) -> str:
        await gate.wait()
        return "x"

    registry.register(ToolSpec(
        name="gated",
        description="gated",
        parameters={"type": "object", "properties": {}},
        handler=gated,
        parallel_safe=True,
    ))
    try:
        for i in range(_RETAIN_CAP + 5):
            result = await gateway.submit(ToolCall(id=f"t{i}", name="gated", arguments={}))
            assert "still running" in result.content
        assert len(gateway.executions) == _RETAIN_CAP + 5  # all running: no victim
        gate.set()
        await asyncio.sleep(0.2)
        # One more retain triggers a final eviction now that all have settled.
        gate.clear()
        await gateway.submit(ToolCall(id="t-last", name="gated", arguments={}))
        gate.set()
        await asyncio.sleep(0.2)
    finally:
        gate.set()

    assert len(gateway.executions) == _RETAIN_CAP
    assert all(e.status != "running" for e in gateway.executions.values())
    newest = f"exec{_RETAIN_CAP + 6}"
    assert newest in gateway.executions
    assert "exec1" not in gateway.executions
    # Engine state went with the victims: no orphan task entries.
    assert len(gateway._tasks.known_ids()) == _RETAIN_CAP
    assert "exec1" not in gateway._tasks.known_ids()


async def test_f35_running_executions_are_never_evicted() -> None:
    gateway, registry = _fast_gateway(soft=0)
    gate = asyncio.Event()

    async def hang(args: dict) -> str:
        await gate.wait()
        return "released"

    registry.register(ToolSpec(
        name="hang",
        description="hang",
        parameters={"type": "object", "properties": {}},
        handler=hang,
        parallel_safe=True,
    ))
    try:
        for i in range(3):
            await gateway.submit(ToolCall(id=f"h{i}", name="hang", arguments={}))
        for i in range(_RETAIN_CAP + 5):
            await gateway.submit(ToolCall(id=f"t{i}", name="echo", arguments={"text": "x"}))
        await asyncio.sleep(0.2)
        running = [e for e in gateway.executions.values() if e.status == "running"]
        assert sorted(e.id for e in running) == ["exec1", "exec2", "exec3"]
        assert len(gateway.executions) <= _RETAIN_CAP + 3
    finally:
        gate.set()
        await gateway.shutdown()


async def test_f35_locks_lru_capped_and_held_lock_survives() -> None:
    gateway, _ = _fast_gateway()
    for i in range(_RETAIN_CAP):
        await gateway._lock_for(f"k{i}")
    assert len(gateway._locks) == _RETAIN_CAP
    # Re-touched key becomes newest; next inserts evict the oldest untouched.
    await gateway._lock_for("k0")
    for i in range(_RETAIN_CAP, _RETAIN_CAP + 6):
        await gateway._lock_for(f"k{i}")
    assert len(gateway._locks) == _RETAIN_CAP
    assert "k0" in gateway._locks
    for i in range(1, 7):
        assert f"k{i}" not in gateway._locks

    held = await gateway._lock_for("pinned")
    await held.acquire()
    try:
        for i in range(1000, 1000 + _RETAIN_CAP + 5):
            await gateway._lock_for(f"z{i}")
        assert "pinned" in gateway._locks
    finally:
        held.release()


# Moved from test_b4_unification.py / test_b4_p2.py (B4 batch).
async def test_x3_gateway_reports_arg_error_not_coercion(tmp_path: Path) -> None:
    from thyca.core.protocol import ToolCall
    from thyca.tools.builtin import register_file_tools
    from thyca.tools.gateway import ToolGateway
    from thyca.tools.path_guard import PathGuard
    from thyca.tools.registry import ToolRegistry
    from thyca.tools.task_store import TaskStore

    registry = ToolRegistry()
    register_file_tools(registry, PathGuard(tmp_path), None)
    gateway = ToolGateway(registry, TaskStore())
    result = await gateway.submit(
        ToolCall(id="c1", name="read", arguments={"path": 123})
    )
    assert result.is_error
    assert result.content == "argument 'path' must be string, got integer"


def test_x6_gateway_head_bytes_delegates() -> None:
    from thyca.core.protocol import truncate_to_cap
    from thyca.tools.gateway.execution import head_bytes

    blobs = [b"", b"a", b"a" * 100, "éàü".encode() * 40, bytes(range(1, 256))]
    for blob in blobs:
        for size in (0, 1, 2, 3, 7, 64, len(blob), len(blob) + 1):
            assert head_bytes(blob, size) == truncate_to_cap(blob, size)[0]


def test_x27_unknown_id_messages() -> None:
    from thyca.tools.gateway.execution import unknown_id

    assert (
        str(unknown_id("task", "t9", ["t1", "t2"]))
        == "unknown task id: t9 (known: t1, t2)"
    )
    assert str(unknown_id("execution", "e9", [])) == "unknown execution id: e9 (known: none)"
    assert isinstance(unknown_id("background", "b", []), ValueError)


def test_x27_render_page_full_and_paged() -> None:
    from thyca.tools.gateway.execution import render_page

    assert render_page("short", None, None, more="m") == "short"
    page = render_page("\n".join(f"l{i}" for i in range(10)), 2, 3, more="m")
    assert page.startswith("l2\nl3\nl4\n[page: lines 3-5 of 10 retained]")


def test_x27_await_done_and_require_id() -> None:
    import asyncio as aio

    from thyca.tools.gateway.execution import await_done, require_id

    async def main() -> None:
        done = aio.Event()
        done.set()
        await await_done(done, 5)
        await await_done(aio.Event(), 0)

    asyncio.run(main())
    assert require_id({"id": "exec1"}) == "exec1"
    with pytest.raises(ValueError, match="non-empty string"):
        require_id({"id": "  "})
    with pytest.raises(ValueError, match="non-empty string"):
        require_id({})


def test_x27_render_exit_policies() -> None:
    from thyca.tools.gateway.execution import render_exit

    assert render_exit(None, "body", False) == "exit: 124\nbody"
    assert render_exit(-9, "body", True) == "exit: -9\ntimed_out: true\nbody"
    assert render_exit(0, "", False) == "exit: 0\n"


def test_x27_one_int_coercion() -> None:
    from thyca.tools.builtin.bash import parse_timeout
    from thyca.tools.gateway.execution import parse_limit, parse_offset, parse_wait

    assert parse_wait(5.0) == 5
    assert parse_offset(2.0) == 2
    assert parse_limit(2.0) == 2
    assert parse_timeout(30.0) == 30
    for fn in (parse_wait, parse_offset, parse_limit, parse_timeout):
        with pytest.raises(ValueError):
            fn(True)
        with pytest.raises(ValueError):
            fn(1.5)


async def test_m3_detached_execution_drops_task_entry(tmp_path: Path) -> None:
    from thyca.core.protocol import ToolCall
    from thyca.tools.builtin import register_file_tools
    from thyca.tools.gateway import ToolGateway
    from thyca.tools.gateway.background import BackgroundProcs
    from thyca.tools.path_guard import PathGuard
    from thyca.tools.registry import ToolRegistry
    from thyca.tools.task_store import TaskStore

    background = BackgroundProcs()
    registry = ToolRegistry()
    register_file_tools(registry, PathGuard(tmp_path), background)
    gateway = ToolGateway(registry, TaskStore(), background)
    try:
        result = await gateway.submit(
            ToolCall(
                id="b1",
                name="bash",
                arguments={"command": "echo hi", "background": True},
            )
        )
        assert not result.is_error
        execution = gateway.executions["exec1"]
        assert execution.entry is None
        assert execution.proc is not None
    finally:
        await gateway.shutdown()


# Moved from tests/test_b2_contracts.py (B2 batch).
import time
from thyca.tools.gateway.gateway import tool_kill_spec, tool_read_spec

def _stack(root: Path, background: BackgroundProcs | None, soft: int = 60):
    store = TaskStore()
    registry = ToolRegistry()
    register_file_tools(registry, PathGuard(root), background)
    gateway = ToolGateway(registry, store, background, soft_timeout_s=soft)
    registry.register(tool_read_spec(gateway))
    registry.register(tool_kill_spec(gateway))
    return gateway, background


async def test_paged_read_leaves_delta_cursor(tmp_path: Path) -> None:
    """Pins existing behavior (doc-only ruling): paging never moves the
    temporal delta cursor, so a later plain read repeats paged bytes."""
    gateway, _procs = _stack(tmp_path, BackgroundProcs())
    await gateway.submit(
        ToolCall(
            id="t1",
            name="bash",
            arguments={
                "command": "printf 'one\\ntwo\\nthree\\n'",
                "background": True,
            },
        )
    )
    done = await gateway.read("exec1", wait=10)
    assert "status: done" in done.content
    page = await gateway.read("exec1", offset=1, limit=1)
    assert "two" in page.content
    assert "one" not in page.content
    delta = await gateway.read("exec1")
    assert "one" in delta.content


def test_tool_read_description_states_cursor_independence() -> None:
    """Fails pre-fix: the independence sentence is new wording."""
    assert (
        "Positional paging never moves the temporal delta cursor"
        in tool_read_spec(None).description
    )


async def test_hard_at_soft_edge_tracks_deterministically(tmp_path: Path) -> None:
    """Pins existing behavior (accept ruling, NO behavior change): a hard cap
    firing at the soft deadline tracks ('still running') instead of waiting
    past the cap like the pre-gateway code (hard + drain grace) did."""
    gateway, _procs = _stack(tmp_path, BackgroundProcs(), soft=1)
    try:
        started = time.monotonic()
        result = await gateway.submit(
            ToolCall(
                id="t1",
                name="bash",
                arguments={
                    # setsid'd grandchild holds stdout past the hard kill, so
                    # settle lands after the 1s soft deadline, deterministically.
                    "command": "setsid sleep 2 & sleep 30",
                    "timeout": 1,
                },
            )
        )
        elapsed = time.monotonic() - started
        assert result.content.startswith("still running: exec1\n")
        assert elapsed < 5.0
    finally:
        await gateway.shutdown()
