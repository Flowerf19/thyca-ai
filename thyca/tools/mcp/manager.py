from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Literal

from mcp.types import Tool

from thyca.configs import McpServerCfg
from thyca.core.protocol import ToolResult
from thyca.tools.mcp.catalog import (
    StartupDiagnostic,
    format_mcp_status,
    join_text_blocks,
    model_name,
    summarize_error,
    tool_error,
    tool_names,
    validate_tools,
)
from thyca.tools.mcp.process import MCPProcess, ProcessFactory, default_process
from thyca.tools.registry import ToolSpec

_ServerStatus = Literal["connecting", "live", "failed"]


@dataclass
class ServerEntry:
    cfg: McpServerCfg
    status: _ServerStatus = "connecting"
    last_tools: list[Tool] | None = None
    error: str | None = None
    proc: MCPProcess | None = None
    task: asyncio.Task[None] | None = None
    tool_diags: list[StartupDiagnostic] = field(default_factory=list)
    settled: bool = True


def freeze_cfg(cfg: McpServerCfg) -> McpServerCfg:
    # Frozen copy: args/env are mutable containers, so fingerprints must not
    # alias the caller's object.
    return McpServerCfg(cfg.command, list(cfg.args), dict(cfg.env))


class MCPManager:
    def __init__(self, process_factory: ProcessFactory | None = None) -> None:
        self._factory = process_factory or default_process
        self._entries: dict[str, ServerEntry] = {}

    @property
    def _live(self) -> list[tuple[MCPProcess, list[Tool]]]:
        """Live processes for tests and legacy callers."""
        return [
            (entry.proc, list(entry.last_tools or []))
            for entry in self._entries.values()
            if entry.status == "live" and entry.proc is not None
        ]

    @property
    def _configs(self) -> dict[str, McpServerCfg]:
        return {name: entry.cfg for name, entry in self._entries.items()}

    @property
    def _failed(self) -> dict[str, str]:
        return self.failed_servers()

    def _binding_handler(self, server: str, tool_name: str):
        async def handler(args: dict) -> ToolResult:
            entry = self._entries.get(server)
            if entry is not None and entry.status == "connecting" and entry.task is not None:
                try:
                    await entry.task
                except Exception:
                    pass
                entry = self._entries.get(server)
            if entry is None or entry.status != "live" or entry.proc is None:
                reason = (
                    entry.error
                    if entry is not None and entry.error
                    else "startup failed"
                )
                return ToolResult(
                    tool_call_id="mcp",
                    name=tool_name,
                    content=f"MCP server {server!r} unavailable: {reason}",
                    is_error=True,
                )
            result = await entry.proc.call(tool_name, args)
            content = join_text_blocks(list(result.content or []))
            return ToolResult(
                tool_call_id="mcp",
                name=tool_name,
                content=content,
                is_error=bool(result.isError),
            )

        return handler

    def _spec_for(self, server: str, tool: Tool) -> ToolSpec | None:
        if tool_error(server, tool) is not None:
            return None
        return ToolSpec(
            name=model_name(server, tool.name),
            description=tool.description or tool.name,
            parameters=tool.inputSchema,
            handler=self._binding_handler(server, tool.name),
            parallel_safe=False,
            resource_key=lambda _args, name=server: f"mcp:{name}",
        )

    def _canonical_tools(
        self, seen: set[str]
    ) -> Iterator[tuple[str, Tool, ToolSpec | None, str | None]]:
        """Yield ``(server, tool, spec, error)``; exactly one of spec/error set."""
        for server, entry in self._entries.items():
            tools = entry.last_tools
            if not tools:
                continue
            for tool in tools:
                error = tool_error(server, tool, seen)
                if error is not None:
                    yield server, tool, None, error
                    continue
                spec = self._spec_for(server, tool)
                assert spec is not None
                seen.add(spec.name)
                yield server, tool, spec, None

    async def _aclose_proc(self, proc: MCPProcess | None) -> None:
        if proc is None:
            return
        try:
            await proc.aclose()
        except Exception:
            pass

    async def _cancel_task(self, task: asyncio.Task[None] | None) -> None:
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
        except Exception:
            pass

    async def _connect(self, name: str, entry: ServerEntry) -> None:
        proc = self._factory(name, entry.cfg)
        try:
            tools = await proc.start()
        except Exception as exc:
            await self._aclose_proc(proc)
            if self._entries.get(name) is not entry:
                return
            entry.proc = None
            entry.status = "failed"
            entry.error = summarize_error(exc)
            entry.tool_diags = []
            return
        if self._entries.get(name) is not entry:
            await self._aclose_proc(proc)
            return
        entry.proc = proc
        entry.last_tools = list(tools)
        entry.status = "live"
        entry.error = None
        seen: set[str] = set()
        for other, other_entry in self._entries.items():
            if other == name or not other_entry.last_tools:
                continue
            for tool in other_entry.last_tools:
                if tool_error(other, tool, seen) is None:
                    seen.add(model_name(other, tool.name))
        entry.tool_diags = validate_tools(name, tools, seen)

    def _start_connect(self, name: str, entry: ServerEntry) -> None:
        entry.status = "connecting"
        entry.error = None
        entry.settled = False
        entry.tool_diags = []
        entry.task = asyncio.create_task(
            self._connect(name, entry), name=f"mcp-connect:{name}"
        )

    async def schedule(self, servers: dict[str, McpServerCfg]) -> list[str]:
        """Converge desired servers and start connects in parallel.

        Returns spec names the caller must unregister (fully removed servers,
        or catalogs dropped because the server left the config). Config changes
        keep ``last_tools`` as stubs while the new connect runs.
        """
        removed: list[str] = []
        desired = {name: freeze_cfg(cfg) for name, cfg in servers.items()}

        for name in list(self._entries):
            if name in desired:
                continue
            entry = self._entries.pop(name)
            await self._cancel_task(entry.task)
            await self._aclose_proc(entry.proc)
            removed.extend(tool_names(name, entry.last_tools))

        for name, cfg in desired.items():
            entry = self._entries.get(name)
            if entry is not None and entry.cfg == cfg:
                if entry.status == "live":
                    continue
                if entry.status == "connecting" and entry.task is not None:
                    continue
                # failed (or connecting without a task): retry same config
                await self._cancel_task(entry.task)
                await self._aclose_proc(entry.proc)
                entry.proc = None
                entry.task = None
                self._start_connect(name, entry)
                continue

            if entry is not None:
                # Config changed: stop process, keep last_tools for stubs/wait.
                await self._cancel_task(entry.task)
                await self._aclose_proc(entry.proc)
                entry.cfg = cfg
                entry.proc = None
                entry.task = None
                self._start_connect(name, entry)
                continue

            entry = ServerEntry(cfg=cfg)
            self._entries[name] = entry
            self._start_connect(name, entry)

        return removed

    async def settle(self) -> list[StartupDiagnostic]:
        """Await pending connects and return diagnostics for newly settled ones."""
        pending = [
            entry.task
            for entry in self._entries.values()
            if entry.task is not None and not entry.task.done()
        ]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        diags: list[StartupDiagnostic] = []
        for name, entry in self._entries.items():
            if entry.settled:
                continue
            entry.settled = True
            entry.task = None
            if entry.status == "live":
                diags.append(StartupDiagnostic(name, True, ""))
                diags.extend(entry.tool_diags)
            elif entry.status == "failed":
                diags.append(
                    StartupDiagnostic(name, False, entry.error or "startup failed")
                )
        return diags

    async def spawn_all(self, servers: dict[str, McpServerCfg]) -> list[StartupDiagnostic]:
        """schedule + settle (compat wrapper for tests and one-shot callers)."""
        await self.schedule(servers)
        return await self.settle()

    async def sync(
        self, servers: dict[str, McpServerCfg]
    ) -> tuple[list[StartupDiagnostic], list[str]]:
        """Converge to ``servers``: schedule parallel connects, then settle.

        Returns ``(diags, removed_spec_names)``. Unchanged live servers are
        untouched. Failed servers retry on the next sync. Never raises for
        per-server failures.
        """
        before = {
            name: set(tool_names(name, entry.last_tools))
            for name, entry in self._entries.items()
        }
        removed = await self.schedule(servers)
        diags = await self.settle()
        # Drop catalog names that a successful reconnect no longer exposes.
        for name, entry in self._entries.items():
            if entry.status != "live" or name not in before:
                continue
            stale = before[name] - set(tool_names(name, entry.last_tools))
            removed.extend(sorted(stale))
        return diags, removed

    def failed_servers(self) -> dict[str, str]:
        """Cold-start failures only (no ``last_tools`` catalog for stubs)."""
        return {
            name: entry.error
            for name, entry in self._entries.items()
            if entry.status == "failed"
            and entry.last_tools is None
            and entry.error
        }

    def prompt_summary(self) -> str:
        """Format cold MCP failures for prompt injection (empty when stubs cover)."""
        return format_mcp_status(self.failed_servers())

    def tool_specs(self) -> list[ToolSpec]:
        seen: set[str] = set()
        return [
            spec
            for _server, _tool, spec, _error in self._canonical_tools(seen)
            if spec is not None
        ]

    async def shutdown(self) -> None:
        entries, self._entries = self._entries, {}
        for entry in entries.values():
            await self._cancel_task(entry.task)
            await self._aclose_proc(entry.proc)
