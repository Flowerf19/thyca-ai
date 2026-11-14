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

from thyca.agents.act import Act
from thyca.agents.assemble import Assemble
from thyca.agents.loop import AgentLoop
from thyca.agents.observe import Observe
from thyca.agents.think import LLMPort, Think
from thyca.app.summarizing import LlmSummarizer
from thyca.configs import Config
from thyca.llms.prompt_manager import PromptManager
from thyca.conversations.runtime import ConversationManager
from thyca.tools.builtin import register_file_tools
from thyca.tools.gateway.background import BackgroundProcs
from thyca.tools.gateway import ToolGateway
from thyca.tools.gateway.policy import Policy
from thyca.tools.gateway.gateway import tool_kill_spec, tool_read_spec
from thyca.tools.mcp import MCPManager, StartupDiagnostic
from thyca.memories.facade import MemoryFacade
from thyca.tools.memory_tools import register_memory_tools
from thyca.tools.path_guard import PathGuard
from thyca.tools.profile_tools import register_profile_tools
from thyca.tools.registry import ToolRegistry
from thyca.tools.conversation_tools import register_conversation_tools
from thyca.tools.task_store import TaskStore


def build_tool_registry(
    root: Path, cfg: Config, background: BackgroundProcs
) -> ToolRegistry:
    """Assemble the file/memory/profile tool registry (no gateway tools, no MCP, no spawn)."""
    registry = ToolRegistry()
    register_file_tools(registry, PathGuard(root), background)
    facade = MemoryFacade(root, timezone_name=cfg.timeline.timezone)
    register_memory_tools(registry, facade)
    register_profile_tools(registry, facade)
    register_conversation_tools(registry)
    return registry


def build_tool_gateway(
    registry: ToolRegistry,
    tasks: TaskStore,
    background: BackgroundProcs,
    *,
    soft_timeout_s: int,
    policy: Policy | None = None,
) -> ToolGateway:
    """Assemble the execution front door over the registry + engines,
    and register its poll tools (tool_read, tool_kill)."""
    gateway = ToolGateway(registry, tasks, background, soft_timeout_s=soft_timeout_s, policy=policy)
    registry.register(tool_read_spec(gateway))
    registry.register(tool_kill_spec(gateway))
    return gateway


def build_agent_loop(
    *,
    conversations: ConversationManager,
    connect: LLMPort,
    act: Act,
    tools: list | None,
    loop_max: int,
    model: str | None,
    pricing: dict | None,
    context_tokens: int | None = None,
) -> AgentLoop:
    """Assemble one AgentLoop: the shared per-turn wiring for ChatApp and Cli.

    Both entry points wire the same five stages; only the act instance
    (ChatApp reuses one carrying skills_root, Cli builds one per run) and
    the per-turn pieces differ, so those stay parameters."""
    prompts = PromptManager()
    return AgentLoop(
        conversations=conversations,
        assemble=Assemble(prompts),
        think=Think(connect),
        act=act,
        observe=Observe(conversations),
        loop_max=loop_max,
        tools=tools,
        model=model,
        pricing=pricing,
        prompts=prompts,
        context_tokens=context_tokens,
        summarizer=LlmSummarizer(connect),
    )


def report_spawn_diags(diags: Iterable[StartupDiagnostic], *, err: TextIO) -> None:
    """Print one stderr warning line per failed MCP server spawn."""
    for diag in diags:
        if not diag.ok:
            print(f"warning: mcp {diag.server}: {diag.message}", file=err, flush=True)


def install_mcp_specs(
    registry: ToolRegistry, mcp: MCPManager, *, err: TextIO
) -> None:
    """Register MCP tool specs; already-present names are skipped.

    Idempotent for turn-start re-installs: cross-server clashes were already
    reported as spawn diagnostics, so only genuinely new names register here.
    """
    for spec in mcp.tool_specs():
        if registry.get(spec.name) is not None:
            continue
        try:
            registry.register(spec)
        except ValueError as exc:
            print(str(exc), file=err)
