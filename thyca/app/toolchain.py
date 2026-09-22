"""Shared agent-toolchain builders for ChatApp and Cli (M8 TASK-010/011).

Both entry points assemble the same registry (file tools + task spec +
memory tools) and install MCP specs the same way; the only difference is
how MCP servers are spawned (ChatApp submits onto its loop thread, Cli
awaits directly), so spawning stays at the call sites.
"""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import TextIO

from thyca.config import Config
from thyca.tools.builtin import register_file_tools
from thyca.tools.builtin.background import BackgroundProcs
from thyca.tools.mcp import MCPManager, StartupDiagnostic
from thyca.tools.memory import MemoryFacade
from thyca.tools.memory_tools import register_memory_tools
from thyca.tools.path_guard import PathGuard
from thyca.tools.registry import ToolRegistry
from thyca.tools.task_store import TaskStore, tool_read_spec


def build_tool_registry(
    root: Path, cfg: Config, tasks: TaskStore, background: BackgroundProcs
) -> ToolRegistry:
    """Assemble the file/task/memory tool registry (no MCP, no spawn)."""
    registry = ToolRegistry(tasks=tasks)
    register_file_tools(registry, PathGuard(root), background)
    registry.register(tool_read_spec(tasks))
    register_memory_tools(
        registry, MemoryFacade(root, timezone_name=cfg.timeline.timezone)
    )
    return registry


def report_spawn_diags(diags: Iterable[StartupDiagnostic], *, err: TextIO) -> None:
    """Print one stderr line per failed MCP server spawn."""
    for diag in diags:
        if not diag.ok:
            print(f"{diag.server}: {diag.message}", file=err)


def install_mcp_specs(
    registry: ToolRegistry, mcp: MCPManager, *, err: TextIO
) -> None:
    """Register MCP tool specs; name clashes stay stderr warnings."""
    for spec in mcp.tool_specs():
        try:
            registry.register(spec)
        except ValueError as exc:
            print(str(exc), file=err)
