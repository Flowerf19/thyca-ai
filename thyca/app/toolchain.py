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

from thyca.agent.act import Act
from thyca.agent.assemble import Assemble
from thyca.agent.loop import AgentLoop
from thyca.agent.observe import Observe
from thyca.agent.think import LLMPort, Think
from thyca.config import Config
from thyca.llm.prompt_manager import PromptManager
from thyca.sessions import SessionManager
from thyca.tools.builtin import register_file_tools
from thyca.tools.gateway.background import BackgroundProcs
from thyca.tools.gateway import ToolGateway
from thyca.tools.gateway.gateway import tool_kill_spec, tool_read_spec
from thyca.tools.mcp import MCPManager, StartupDiagnostic
from thyca.memory.facade import MemoryFacade
from thyca.tools.memory_tools import register_memory_tools
from thyca.tools.path_guard import PathGuard
from thyca.tools.registry import ToolRegistry
from thyca.tools.task_store import TaskStore


def build_tool_registry(
    root: Path, cfg: Config, background: BackgroundProcs
) -> ToolRegistry:
    """Assemble the file/memory tool registry (no gateway tools, no MCP, no spawn)."""
    registry = ToolRegistry()
    register_file_tools(registry, PathGuard(root), background)
    register_memory_tools(
        registry, MemoryFacade(root, timezone_name=cfg.timeline.timezone)
    )
    return registry


def build_tool_gateway(
    registry: ToolRegistry,
    tasks: TaskStore,
    background: BackgroundProcs,
    *,
    soft_timeout_s: int,
) -> ToolGateway:
    """Assemble the execution front door over the registry + engines,
    and register its poll tools (tool_read, tool_kill)."""
    gateway = ToolGateway(registry, tasks, background, soft_timeout_s=soft_timeout_s)
    registry.register(tool_read_spec(gateway))
    registry.register(tool_kill_spec(gateway))
    return gateway


def build_agent_loop(
    *,
    sessions: SessionManager,
    connect: LLMPort,
    act: Act,
    tools: list | None,
    loop_max: int,
    model: str | None,
    pricing: dict | None,
) -> AgentLoop:
    """Assemble one AgentLoop: the shared per-turn wiring for ChatApp and Cli.

    Both entry points wire the same five stages; only the act instance
    (ChatApp reuses one carrying skills_root, Cli builds one per run) and
    the per-turn pieces differ, so those stay parameters."""
    return AgentLoop(
        sessions=sessions,
        assemble=Assemble(PromptManager()),
        think=Think(connect),
        act=act,
        observe=Observe(sessions),
        loop_max=loop_max,
        tools=tools,
        model=model,
        pricing=pricing,
    )


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
