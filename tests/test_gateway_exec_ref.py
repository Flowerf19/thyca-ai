"""exec_ref provenance: only gateway-tracked ids may leave the gateway.

A handler-returned ToolResult.exec_ref is trusted only when it names a live
tracked execution; forged refs (never tracked) are dropped to None so shrink
never promises a pointer to unretained/nonexistent output.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from thyca.core.protocol import ToolCall, ToolResult
from thyca.tools.gateway import ToolGateway
from thyca.tools.gateway.background import BackgroundProcs
from thyca.tools.gateway.execution import Detached, current_execution
from thyca.tools.registry import ToolRegistry, ToolSpec
from thyca.tools.task_store import TaskStore


def _gateway(soft: int = 60, **kwargs) -> tuple[ToolGateway, ToolRegistry]:
    store = TaskStore()
    registry = ToolRegistry()
    return ToolGateway(registry, store, soft_timeout_s=soft, **kwargs), registry


def _spec(name: str, handler) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=f"stub {name}",
        parameters={"type": "object", "properties": {}},
        handler=handler,
        parallel_safe=True,
    )


@pytest.mark.asyncio
async def test_forged_exec_ref_dropped_to_none() -> None:
    async def forge(args: dict):
        return ToolResult(
            tool_call_id="x", name="forge", content="forged",
            is_error=False, exec_ref="exec999",
        )

    gateway, registry = _gateway()
    registry.register(_spec("forge", forge))
    assert "exec999" not in gateway.executions
    result = await gateway.submit(ToolCall(id="t1", name="forge", arguments={}))
    assert result.exec_ref is None
    assert result.content == "forged"
    assert not result.is_error


@pytest.mark.asyncio
async def test_tracked_exec_ref_still_trusted_via_resolve() -> None:
    gate = asyncio.Event()

    async def slow(args: dict) -> str:
        await gate.wait()
        return "slow-done"

    async def echo_ref(args: dict):
        return ToolResult(
            tool_call_id="x", name="echo_ref", content="points at exec1",
            is_error=False, exec_ref="exec1",
        )

    gateway, registry = _gateway(soft=0)
    registry.register(_spec("slow", slow))
    registry.register(_spec("echo_ref", echo_ref))
    try:
        tracked = await gateway.submit(ToolCall(id="t1", name="slow", arguments={}))
        assert tracked.exec_ref == "exec1"
        assert "exec1" in gateway.executions
        result = await gateway.submit(ToolCall(id="t2", name="echo_ref", arguments={}))
        assert result.exec_ref == "exec1"
    finally:
        gate.set()
        await gateway.shutdown()


@pytest.mark.asyncio
async def test_still_running_carries_tracked_ref() -> None:
    gate = asyncio.Event()

    async def slow(args: dict) -> str:
        await gate.wait()
        return "slow-done"

    gateway, registry = _gateway(soft=0)
    registry.register(_spec("slow", slow))
    try:
        result = await gateway.submit(ToolCall(id="t1", name="slow", arguments={}))
        assert result.content.startswith("still running: exec1\n")
        assert result.exec_ref == "exec1"
        assert "exec1" in gateway.executions
    finally:
        gate.set()
        await gateway.shutdown()


@pytest.mark.asyncio
async def test_detached_carries_tracked_ref() -> None:
    procs = BackgroundProcs()

    async def detach(args: dict):
        execution = current_execution()
        assert execution is not None
        bid = await procs.start("echo hi", 30, os.getcwd(), id=execution.id)
        entry = procs.get(bid)
        assert entry is not None
        return Detached(entry, f"started: {bid}\nrunning in background.")

    gateway, registry = _gateway(background=procs)
    registry.register(_spec("detach", detach))
    try:
        result = await gateway.submit(ToolCall(id="t1", name="detach", arguments={}))
        assert result.exec_ref == "exec1"
        assert "exec1" in gateway.executions
        assert gateway.executions["exec1"].kind == "proc"
    finally:
        await gateway.shutdown()


@pytest.mark.asyncio
async def test_poll_carries_tracked_ref() -> None:
    gate = asyncio.Event()

    async def slow(args: dict) -> str:
        await gate.wait()
        return "slow-done"

    gateway, registry = _gateway(soft=0)
    registry.register(_spec("slow", slow))
    try:
        await gateway.submit(ToolCall(id="t1", name="slow", arguments={}))
        running = await gateway.read("exec1")
        assert running.exec_ref == "exec1"
        gate.set()
        done = await gateway.read("exec1", wait=5)
        assert done.exec_ref == "exec1"
        assert done.content == "slow-done"
    finally:
        gate.set()
        await gateway.shutdown()


@pytest.mark.asyncio
async def test_fast_path_results_carry_none() -> None:
    async def plain(args: dict) -> str:
        return "plain"

    async def bare(args: dict):
        return ToolResult(
            tool_call_id="x", name="bare", content="bare", is_error=False,
        )

    gateway, registry = _gateway()
    registry.register(_spec("plain", plain))
    registry.register(_spec("bare", bare))
    assert (await gateway.submit(ToolCall(id="t1", name="plain", arguments={}))).exec_ref is None
    assert (await gateway.submit(ToolCall(id="t2", name="bare", arguments={}))).exec_ref is None
    assert gateway.executions == {}
