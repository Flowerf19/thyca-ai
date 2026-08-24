from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Protocol
import asyncio
import re
import sys

from mcp import ClientSession, StdioServerParameters, stdio_client
from mcp.client.stdio import get_default_environment
from mcp.types import CallToolResult, Tool

from thyca.config import McpServerCfg
from thyca.protocol import ToolResult
from thyca.tools.registry import ToolSpec

CALL_TIMEOUT = timedelta(seconds=30)


def merge_env(server_env: dict[str, str]) -> dict[str, str]:
    return get_default_environment() | dict(server_env)


def resolve_command(command: str) -> str:
    name = Path(command).name
    if name == "python" or name.startswith("python3"):
        return sys.executable
    return command


def model_name(server: str, tool: str) -> str:
    return f"{server}__{tool}"


def join_text_blocks(content: list[Any]) -> str:
    parts: list[str] = []
    for block in content:
        kind = getattr(block, "type", None)
        if kind is None and isinstance(block, dict):
            kind = block.get("type")
        if kind != "text":
            continue
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


class _McpSession(Protocol):
    async def initialize(self) -> object: ...
    async def list_tools(self) -> Any: ...
    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        read_timeout_seconds: timedelta | None = None,
        **kwargs: Any,
    ) -> CallToolResult: ...


class MCPProcess:
    def __init__(
        self,
        name: str,
        command: str,
        args: list[str],
        env: dict[str, str],
        session: _McpSession | None = None,
    ) -> None:
        self.name = name
        self._command = command
        self._args = args
        self._env = env
        self._session = session
        self._closed: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> list[Tool]:
        if self._session is not None:
            await self._session.initialize()
            listed = await self._session.list_tools()
            return list(listed.tools)
        ready: asyncio.Future[list[Tool]] = asyncio.get_running_loop().create_future()
        self._closed = asyncio.Event()
        self._task = asyncio.create_task(self._run(ready), name=f"mcp:{self.name}")
        try:
            return await ready
        except BaseException:
            await self.aclose()
            raise

    async def _run(self, ready: asyncio.Future[list[Tool]]) -> None:
        params = StdioServerParameters(
            command=resolve_command(self._command),
            args=list(self._args),
            env=merge_env(self._env),
        )
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    self._session = session
                    await session.initialize()
                    listed = await session.list_tools()
                    if not ready.done():
                        ready.set_result(list(listed.tools))
                    assert self._closed is not None
                    await self._closed.wait()
        except Exception as exc:
            if not ready.done():
                ready.set_exception(exc)
        finally:
            self._session = None

    async def call(
        self, tool_name: str, arguments: dict[str, Any] | None = None
    ) -> CallToolResult:
        if self._session is None:
            raise RuntimeError(f"mcp process {self.name!r} is not started")
        return await self._session.call_tool(
            tool_name,
            arguments,
            read_timeout_seconds=CALL_TIMEOUT,
        )

    async def aclose(self) -> None:
        if self._closed is not None:
            self._closed.set()
        task, self._task = self._task, None
        if task is not None:
            await task
        self._session = None


_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
_MODEL_NAME_MAX = 64
ProcessFactory = Callable[[str, McpServerCfg], MCPProcess]


@dataclass(frozen=True)
class StartupDiagnostic:
    server: str
    ok: bool
    message: str


def _default_process(name: str, cfg: McpServerCfg) -> MCPProcess:
    return MCPProcess(name, cfg.command, list(cfg.args), dict(cfg.env))


def _handler(proc: MCPProcess, tool_name: str):
    async def handler(args: dict) -> ToolResult:
        result = await proc.call(tool_name, args)
        content = join_text_blocks(list(result.content or []))
        return ToolResult(
            tool_call_id="mcp",
            name=tool_name,
            content=content,
            is_error=bool(result.isError),
        )

    return handler


def _spec_for(proc: MCPProcess, tool: Tool) -> ToolSpec | None:
    if not _TOOL_NAME.fullmatch(tool.name):
        return None
    name = model_name(proc.name, tool.name)
    if len(name) > _MODEL_NAME_MAX:
        return None
    schema = tool.inputSchema if isinstance(tool.inputSchema, dict) else {
        "type": "object",
        "properties": {},
    }
    return ToolSpec(
        name=name,
        description=tool.description or tool.name,
        parameters=schema,
        handler=_handler(proc, tool.name),
        parallel_safe=False,
        resource_key=lambda _args, server=proc.name: f"mcp:{server}",
    )


class MCPManager:
    def __init__(self, process_factory: ProcessFactory | None = None) -> None:
        self._factory = process_factory or _default_process
        self._live: list[tuple[MCPProcess, list[Tool]]] = []

    async def spawn_all(self, servers: dict[str, McpServerCfg]) -> list[StartupDiagnostic]:
        if not servers:
            return []
        diags: list[StartupDiagnostic] = []
        for name, cfg in servers.items():
            proc = self._factory(name, cfg)
            try:
                tools = await proc.start()
            except Exception as exc:
                await proc.aclose()
                diags.append(StartupDiagnostic(name, False, str(exc)))
                continue
            self._live.append((proc, tools))
            diags.append(StartupDiagnostic(name, True, ""))
        return diags

    def tool_specs(self) -> list[ToolSpec]:
        specs: list[ToolSpec] = []
        seen: set[str] = set()
        for proc, tools in self._live:
            for tool in tools:
                spec = _spec_for(proc, tool)
                if spec is None or spec.name in seen:
                    continue
                seen.add(spec.name)
                specs.append(spec)
        return specs

    async def shutdown(self) -> None:
        live, self._live = self._live, []
        for proc, _tools in live:
            await proc.aclose()
