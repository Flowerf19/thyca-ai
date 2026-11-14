from __future__ import annotations

import asyncio
import os
import re
import sys
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any, Protocol

from mcp import ClientSession, StdioServerParameters, stdio_client
from mcp.client.stdio import get_default_environment
from mcp.types import CallToolResult, Tool

from thyca.configs import McpServerCfg

START_TIMEOUT = timedelta(seconds=10)
CALL_TIMEOUT = timedelta(seconds=30)
_TLS_ENV_VARS = (
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "NODE_EXTRA_CA_CERTS",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
)
_PYTHON3_MINOR = re.compile(r"python3\.\d+")
ProcessFactory = Callable[[str, McpServerCfg], "MCPProcess"]


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


# PYTHONWARNINGS is parsed while `warnings` itself is still importing, so a
# dotted category path (…sources.utils.IncompleteFieldDefinitionWarning) can
# never be imported at that point: the whole option is dropped with "invalid
# module name" and the warning it was meant to silence still prints. Match the
# emitting module instead — a regex, no import needed at parse time. The
# cost of not being able to name the category is that every warning raised
# from this third-party module is silenced, not just this one.
_QUIET_WARNINGS = "ignore:::pydantic_settings.sources.utils"


def merge_env(server_env: dict[str, str]) -> dict[str, str]:
    merged = get_default_environment()
    for key in _TLS_ENV_VARS:
        value = os.environ.get(key)
        if value:
            merged[key] = value
    merged.update(server_env)
    merged.setdefault("PYTHONWARNINGS", _QUIET_WARNINGS)
    return merged


def resolve_command(command: str) -> str:
    name = Path(command).name
    # python3 + optional minor only: python3foo is a real binary name.
    if name == "python" or name == "python3" or _PYTHON3_MINOR.fullmatch(name):
        return sys.executable
    return command


def _format_timeout(seconds: float) -> str:
    label = f"{int(seconds)}s" if float(seconds).is_integer() else f"{seconds:g}s"
    return f"startup timed out after {label}"


class MCPProcess:
    def __init__(
        self,
        name: str,
        command: str,
        args: list[str],
        env: dict[str, str],
        session: _McpSession | None = None,
        start_timeout: timedelta | None = None,
    ) -> None:
        self.name = name
        self._command = command
        self._args = args
        self._env = env
        self._session = session
        self._start_timeout = start_timeout
        self._closed: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None
        self._running_wait = False
        self._failure: BaseException | None = None

    def _timeout_seconds(self) -> float:
        timeout = START_TIMEOUT if self._start_timeout is None else self._start_timeout
        return timeout.total_seconds()

    async def start(self) -> list[Tool]:
        timeout_s = self._timeout_seconds()
        if self._session is not None:
            try:
                async with asyncio.timeout(timeout_s):
                    await self._session.initialize()
                    listed = await self._session.list_tools()
                    return list(listed.tools)
            except TimeoutError as exc:
                raise TimeoutError(_format_timeout(timeout_s)) from exc
        ready: asyncio.Future[list[Tool]] = asyncio.get_running_loop().create_future()
        self._closed = asyncio.Event()
        self._running_wait = False
        self._task = asyncio.create_task(self._run(ready), name=f"mcp:{self.name}")
        try:
            async with asyncio.timeout(timeout_s):
                return await ready
        except TimeoutError as exc:
            await self.aclose()
            raise TimeoutError(_format_timeout(timeout_s)) from exc
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
                    self._running_wait = True
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
            self._running_wait = False
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
            # Cancel if startup is still blocked before _closed.wait(); once waiting,
            # _closed.set() exits cleanly and lets stdio_client terminate the child.
            if not self._running_wait and not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                current = asyncio.current_task()
                if current is not None and current.cancelling():
                    raise
        self._running_wait = False
        self._session = None
        self._failure = None


def default_process(name: str, cfg: McpServerCfg) -> MCPProcess:
    return MCPProcess(name, cfg.command, list(cfg.args), dict(cfg.env))
