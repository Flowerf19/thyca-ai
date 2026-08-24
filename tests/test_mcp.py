from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import sys

import pytest
from mcp.types import CallToolResult, ImageContent, ListToolsResult, TextContent, Tool

from thyca.config import McpServerCfg
from thyca.protocol import ToolCall
from thyca.tools.mcp import (
    CALL_TIMEOUT,
    MCPManager,
    MCPProcess,
    join_text_blocks,
    merge_env,
    model_name,
    resolve_command,
)
from thyca.tools.registry import ToolRegistry


def test_call_timeout_is_30s() -> None:
    assert CALL_TIMEOUT == timedelta(seconds=30)


def test_model_name_prefix() -> None:
    assert model_name("echo", "ping") == "echo__ping"


def test_resolve_command_uses_running_interpreter() -> None:
    assert resolve_command("python3") == sys.executable
    assert resolve_command("/usr/bin/python3") == sys.executable
    assert resolve_command("python3.14") == sys.executable
    assert resolve_command("uv") == "uv"


def test_merge_env_keeps_defaults_and_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SECRET", "nope")
    monkeypatch.setenv("PATH", "/custom/bin")
    merged = merge_env({"FOO": "bar", "PATH": "/from-server"})
    assert merged["FOO"] == "bar"
    assert merged["PATH"] == "/from-server"
    assert "SECRET" not in merged
    assert "HOME" in merged


def test_join_text_blocks_concatenates_text_only() -> None:
    blocks = [
        TextContent(type="text", text="a"),
        ImageContent(type="image", data="xx", mimeType="image/png"),
        TextContent(type="text", text="b"),
        {"type": "resource", "uri": "file://x"},
        {"type": "text", "text": "c"},
    ]
    assert join_text_blocks(blocks) == "abc"


class _FakeSession:
    def __init__(self) -> None:
        self.initialized = False
        self.calls: list[tuple] = []
        self.tools = [
            Tool(name="ping", inputSchema={"type": "object", "properties": {}})
        ]

    async def initialize(self) -> object:
        self.initialized = True
        return object()

    async def list_tools(self) -> ListToolsResult:
        return ListToolsResult(tools=self.tools)

    async def call_tool(self, name, arguments=None, read_timeout_seconds=None, **kwargs):
        self.calls.append((name, arguments, read_timeout_seconds))
        return CallToolResult(
            content=[TextContent(type="text", text="pong")], isError=False
        )


@pytest.mark.asyncio
async def test_process_start_call_aclose_with_injected_session() -> None:
    session = _FakeSession()
    proc = MCPProcess("echo", "unused", [], {}, session=session)
    tools = await proc.start()
    assert session.initialized
    assert [tool.name for tool in tools] == ["ping"]
    await proc.call("ping", {"x": 1})
    assert session.calls == [("ping", {"x": 1}, CALL_TIMEOUT)]
    await proc.aclose()
    assert proc._session is session


@pytest.mark.asyncio
async def test_process_start_closes_on_initialize_failure() -> None:
    class Boom(_FakeSession):
        async def initialize(self) -> object:
            raise RuntimeError("init failed")

    proc = MCPProcess("echo", "unused", [], {}, session=Boom())
    with pytest.raises(RuntimeError, match="init failed"):
        await proc.start()
    await proc.aclose()


def _factory(session: _FakeSession):
    def make(name: str, cfg: McpServerCfg) -> MCPProcess:
        return MCPProcess(name, cfg.command, list(cfg.args), dict(cfg.env), session=session)

    return make


@pytest.mark.asyncio
async def test_spawn_all_empty_does_not_touch_stdio(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args, **_kwargs):
        raise AssertionError("stdio_client must not run")

    monkeypatch.setattr("thyca.tools.mcp.stdio_client", boom)
    manager = MCPManager()
    assert await manager.spawn_all({}) == []
    assert manager.tool_specs() == []
    await manager.shutdown()
    await manager.shutdown()


@pytest.mark.asyncio
async def test_manager_tool_specs_and_handler_unprefixed() -> None:
    session = _FakeSession()
    manager = MCPManager(process_factory=_factory(session))
    diags = await manager.spawn_all({"echo": McpServerCfg(command="true")})
    assert [diag.ok for diag in diags] == [True]
    specs = manager.tool_specs()
    assert len(specs) == 1
    spec = specs[0]
    assert spec.name == "echo__ping"
    assert spec.parallel_safe is False
    assert spec.resource_key({}) == "mcp:echo"
    assert spec.parameters == {"type": "object", "properties": {}}
    registry = ToolRegistry()
    registry.register(spec)
    result = await registry.dispatch(
        ToolCall(id="c1", name="echo__ping", arguments={})
    )
    assert not result.is_error
    assert result.tool_call_id == "c1"
    assert "pong" in result.content
    assert session.calls == [("ping", {}, CALL_TIMEOUT)]
    await manager.shutdown()


@pytest.mark.asyncio
async def test_spawn_all_isolates_failed_server() -> None:
    class FailStart(_FakeSession):
        async def initialize(self) -> object:
            raise RuntimeError("missing binary")

    def make(name: str, cfg: McpServerCfg) -> MCPProcess:
        session: _FakeSession = FailStart() if name == "bad" else _FakeSession()
        return MCPProcess(name, cfg.command, [], {}, session=session)

    manager = MCPManager(process_factory=make)
    diags = await manager.spawn_all(
        {
            "bad": McpServerCfg(command="true"),
            "echo": McpServerCfg(command="true"),
        }
    )
    by_name = {diag.server: diag for diag in diags}
    assert by_name["bad"].ok is False
    assert "missing binary" in by_name["bad"].message
    assert by_name["echo"].ok is True
    assert [spec.name for spec in manager.tool_specs()] == ["echo__ping"]
    await manager.shutdown()


@pytest.mark.asyncio
async def test_handler_maps_is_error() -> None:
    class Err(_FakeSession):
        async def call_tool(self, name, arguments=None, read_timeout_seconds=None, **kwargs):
            return CallToolResult(
                content=[TextContent(type="text", text="nope")], isError=True
            )

    manager = MCPManager(process_factory=_factory(Err()))
    await manager.spawn_all({"echo": McpServerCfg(command="true")})
    registry = ToolRegistry()
    registry.register(manager.tool_specs()[0])
    result = await registry.dispatch(ToolCall(id="c2", name="echo__ping", arguments={}))
    assert result.is_error
    assert result.content == "nope"
    await manager.shutdown()


_ECHO = Path(__file__).resolve().parents[1] / "examples" / "echo.py"


@pytest.mark.asyncio
async def test_spawn_missing_binary() -> None:
    manager = MCPManager()
    diags = await manager.spawn_all(
        {"gone": McpServerCfg(command="thyca-mcp-missing-xyz")}
    )
    assert len(diags) == 1
    assert diags[0].ok is False
    assert "SECRET" not in diags[0].message
    assert manager.tool_specs() == []
    await manager.shutdown()


@pytest.mark.asyncio
async def test_echo_stdio_list_call_shutdown() -> None:
    manager = MCPManager()
    diags = await manager.spawn_all(
        {
            "echo": McpServerCfg(
                command=sys.executable,
                args=[str(_ECHO)],
            )
        }
    )
    assert [diag.ok for diag in diags] == [True], diags
    specs = manager.tool_specs()
    assert [spec.name for spec in specs] == ["echo__ping"]
    registry = ToolRegistry()
    registry.register(specs[0])
    result = await registry.dispatch(ToolCall(id="c3", name="echo__ping", arguments={}))
    assert not result.is_error
    assert "pong" in result.content
    await manager.shutdown()
    await manager.shutdown()
