from __future__ import annotations

import asyncio
import re
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Protocol

from mcp import ClientSession, StdioServerParameters, stdio_client
from mcp.client.stdio import get_default_environment
from mcp.types import CallToolResult, Tool

from thyca.config import McpServerCfg
from thyca.core.protocol import ToolResult
from thyca.tools.registry import ToolSpec

CALL_TIMEOUT = timedelta(seconds=30)


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


_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
_PYTHON3_MINOR = re.compile(r"python3\.\d+")
_MODEL_NAME_MAX = 64
ProcessFactory = Callable[[str, McpServerCfg], "MCPProcess"]


# PYTHONWARNINGS is parsed while `warnings` itself is still importing, so a
# dotted category path (…sources.utils.IncompleteFieldDefinitionWarning) can
# never be imported at that point: the whole option is dropped with "invalid
# module name" and the warning it was meant to silence still prints. Match the
# emitting module instead — a regex, no import needed at parse time. The
# cost of not being able to name the category is that every warning raised
# from this third-party module is silenced, not just this one.
_QUIET_WARNINGS = "ignore:::pydantic_settings.sources.utils"


def merge_env(server_env: dict[str, str]) -> dict[str, str]:
    merged = get_default_environment() | dict(server_env)
    merged.setdefault("PYTHONWARNINGS", _QUIET_WARNINGS)
    return merged


def resolve_command(command: str) -> str:
    name = Path(command).name
    # python3 + optional minor only: python3foo is a real binary name.
    if name == "python" or name == "python3" or _PYTHON3_MINOR.fullmatch(name):
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


def _is_object_schema(schema: object) -> bool:
    def is_schema(candidate: object) -> bool:
        if not isinstance(candidate, dict):
            return False
        properties = candidate.get("properties")
        if properties is not None and (
            not isinstance(properties, dict)
            or any(not is_schema(value) for value in properties.values())
        ):
            return False
        required = candidate.get("required")
        if required is not None and (
            not isinstance(required, list)
            or any(not isinstance(name, str) for name in required)
        ):
            return False
        additional = candidate.get("additionalProperties", True)
        return isinstance(additional, bool) or is_schema(additional)

    return isinstance(schema, dict) and schema.get("type") == "object" and is_schema(schema)


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
        self._failure: BaseException | None = None

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
            async with stdio_client(params) as (read, write), ClientSession(
                read, write
            ) as session:
                    self._session = session
                    await session.initialize()
                    listed = await session.list_tools()
                    if not ready.done():
                        ready.set_result(list(listed.tools))
                    # _closed is always set in start() before this task exists.
                    await self._closed.wait()
        except Exception as exc:
            if not ready.done():
                ready.set_exception(exc)
            else:
                self._failure = exc
        finally:
            self._session = None

    async def call(
        self, tool_name: str, arguments: dict[str, Any] | None = None
    ) -> CallToolResult:
        if self._session is None:
            if self._failure is not None:
                raise RuntimeError(
                    f"mcp process {self.name!r} failed: {self._failure!r}"
                ) from self._failure
            raise RuntimeError(f"mcp process {self.name!r} is not started")
        try:
            result = await self._session.call_tool(
                tool_name,
                arguments,
                read_timeout_seconds=CALL_TIMEOUT,
            )
        except Exception as exc:
            self._failure = exc
            raise RuntimeError(
                f"mcp process {self.name!r} tool {tool_name!r} failed: {exc!r}"
            ) from exc
        self._failure = None
        return result

    async def aclose(self) -> None:
        if self._closed is not None:
            self._closed.set()
        task, self._task = self._task, None
        if task is not None:
            await task
        self._session = None
        self._failure = None


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


def _tool_error(
    proc: MCPProcess, tool: Tool, seen: set[str] | None = None
) -> str | None:
    tool_name = getattr(tool, "name", None)
    if not isinstance(tool_name, str) or _TOOL_NAME.fullmatch(tool_name) is None:
        return "tool name must match [A-Za-z0-9_-]+"
    name = model_name(proc.name, tool_name)
    if len(name) > _MODEL_NAME_MAX:
        return "generated tool name is longer than 64 characters"
    if seen is not None and name in seen:
        return "generated tool name is already registered"
    if not _is_object_schema(getattr(tool, "inputSchema", None)):
        return "input schema must be an object schema"
    description = getattr(tool, "description", None) or tool_name
    if not isinstance(description, str) or not description:
        return "tool description must be a non-empty string"
    return None


def _spec_for(proc: MCPProcess, tool: Tool) -> ToolSpec | None:
    if _tool_error(proc, tool) is not None:
        return None
    return ToolSpec(
        name=model_name(proc.name, tool.name),
        description=tool.description or tool.name,
        parameters=tool.inputSchema,
        handler=_handler(proc, tool.name),
        parallel_safe=False,
        resource_key=lambda _args, server=proc.name: f"mcp:{server}",
    )


def _canonical_tools(
    live: list[tuple[MCPProcess, list[Tool]]], seen: set[str]
) -> Iterator[tuple[MCPProcess, Tool, ToolSpec | None, str | None]]:
    """Yield ``(proc, tool, spec, error)`` per tool; exactly one of spec/error set.

    The single validation+dedup point: first name wins, accepted names join
    ``seen``. ``spawn_all`` turns errors into skip diagnostics; ``tool_specs``
    keeps the specs — so the two can never disagree on which tool won."""
    for proc, tools in live:
        for tool in tools:
            error = _tool_error(proc, tool, seen)
            if error is not None:
                yield proc, tool, None, error
                continue
            spec = _spec_for(proc, tool)
            # The un-seen validation inside _spec_for is a subset of the
            # check that just passed, so the build cannot fail here.
            assert spec is not None
            seen.add(spec.name)
            yield proc, tool, spec, None


class MCPManager:
    def __init__(self, process_factory: ProcessFactory | None = None) -> None:
        self._factory = process_factory or _default_process
        self._live: list[tuple[MCPProcess, list[Tool]]] = []

    async def spawn_all(self, servers: dict[str, McpServerCfg]) -> list[StartupDiagnostic]:
        if not servers:
            return []
        diags: list[StartupDiagnostic] = []
        # One set across the loop: rebuilding tool_specs() per server was O(n²).
        seen = {spec.name for spec in self.tool_specs()}
        for name, cfg in servers.items():
            proc = self._factory(name, cfg)
            try:
                tools = await proc.start()
            except Exception as exc:
                try:
                    await proc.aclose()
                except Exception:
                    pass
                diags.append(StartupDiagnostic(name, False, str(exc)))
                continue
            self._live.append((proc, tools))
            diags.append(StartupDiagnostic(name, True, ""))
            for _proc, tool, _spec, error in _canonical_tools([(proc, tools)], seen):
                if error is None:
                    continue
                raw_name = getattr(tool, "name", "unknown")
                diags.append(
                    StartupDiagnostic(
                        name, False, f"MCP tool {raw_name!r} skipped: {error}"
                    )
                )
        return diags

    def tool_specs(self) -> list[ToolSpec]:
        seen: set[str] = set()
        return [
            spec
            for _proc, _tool, spec, _error in _canonical_tools(self._live, seen)
            if spec is not None
        ]

    async def shutdown(self) -> None:
        live, self._live = self._live, []
        for proc, _tools in live:
            try:
                await proc.aclose()
            except Exception:
                continue
