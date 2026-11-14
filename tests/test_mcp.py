from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp.types import CallToolResult, ImageContent, ListToolsResult, TextContent, Tool

from thyca.configs import McpServerCfg
from thyca.core.protocol import ToolCall
from thyca.tools.mcp import (
    CALL_TIMEOUT,
    MCPManager,
    MCPProcess,
    START_TIMEOUT,
    _summarize_error,
    format_mcp_status,
    join_text_blocks,
    merge_env,
    model_name,
    resolve_command,
)
from thyca.tools.gateway import ToolGateway
from thyca.tools.registry import ToolRegistry
from thyca.tools.task_store import TaskStore


def test_call_timeout_is_30s() -> None:
    assert timedelta(seconds=30) == CALL_TIMEOUT


def test_start_timeout_is_10s() -> None:
    assert timedelta(seconds=10) == START_TIMEOUT


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
    assert "pydantic_settings" in merged["PYTHONWARNINGS"]
    assert merge_env({"PYTHONWARNINGS": "default"})["PYTHONWARNINGS"] == "default"


def test_merge_env_keeps_tls_ca_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SSL_CERT_FILE", "/etc/ssl/certs/ca.pem")
    monkeypatch.setenv("NODE_EXTRA_CA_CERTS", "/etc/pki/ca-trust/tls-ca-bundle.pem")
    merged = merge_env({})
    assert merged["SSL_CERT_FILE"] == "/etc/ssl/certs/ca.pem"
    assert merged["NODE_EXTRA_CA_CERTS"] == "/etc/pki/ca-trust/tls-ca-bundle.pem"
    assert merge_env({"SSL_CERT_FILE": "/override.pem"})["SSL_CERT_FILE"] == "/override.pem"


def test_quiet_warnings_actually_silences_fastmcp() -> None:
    """The filter must survive `warnings` import: a dotted category path is
    dropped there with "invalid module name", leaving the child noisy."""
    script = "from mcp.server.fastmcp import FastMCP\nFastMCP('t')\n"
    env = {**os.environ, "PYTHONWARNINGS": merge_env({})["PYTHONWARNINGS"]}
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        cwd=Path(script).parent,
    )
    assert result.returncode == 0
    assert "Invalid -W option" not in result.stderr
    assert "IncompleteFieldDefinitionWarning" not in result.stderr


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
    assert proc._session is None


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
    gateway = ToolGateway(registry, TaskStore())
    result = await gateway.submit(
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
async def test_startup_cleanup_failure_does_not_abort_other_servers() -> None:
    class FailStart(_FakeSession):
        async def initialize(self) -> object:
            raise RuntimeError("start failed")

    class CloseFails(MCPProcess):
        async def aclose(self) -> None:
            raise RuntimeError("close failed")

    def make(name: str, cfg: McpServerCfg) -> MCPProcess:
        if name == "bad":
            return CloseFails(name, cfg.command, [], {}, session=FailStart())
        return MCPProcess(name, cfg.command, [], {}, session=_FakeSession())

    manager = MCPManager(process_factory=make)
    diags = await manager.spawn_all(
        {"bad": McpServerCfg(command="true"), "good": McpServerCfg(command="true")}
    )

    assert any(diag.server == "bad" and not diag.ok for diag in diags)
    assert any(diag.server == "good" and diag.ok for diag in diags)
    assert [spec.name for spec in manager.tool_specs()] == ["good__ping"]
    await manager.shutdown()


@pytest.mark.asyncio
async def test_shutdown_closes_remaining_servers_after_failure() -> None:
    class CloseFails:
        async def aclose(self) -> None:
            raise RuntimeError("close failed")

    class Closes:
        def __init__(self) -> None:
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True

    manager = MCPManager()
    remaining = Closes()
    manager._live = [(CloseFails(), []), (remaining, [])]  # type: ignore[list-item]

    await manager.shutdown()

    assert remaining.closed


@pytest.mark.asyncio
async def test_invalid_mcp_tool_emits_startup_diagnostic() -> None:
    class InvalidSession(_FakeSession):
        async def list_tools(self):
            return SimpleNamespace(
                tools=[
                    SimpleNamespace(
                        name="bad.name",
                        description="bad",
                        inputSchema={"type": "object"},
                    ),
                    SimpleNamespace(
                        name="bad_schema",
                        description="bad schema",
                        inputSchema=None,
                    ),
                    SimpleNamespace(
                        name="scalar_schema",
                        description="scalar schema",
                        inputSchema={"type": "string"},
                    ),
                    SimpleNamespace(
                        name="bad_property",
                        description="bad property",
                        inputSchema={"type": "object", "properties": {"x": "bad"}},
                    ),
                    SimpleNamespace(
                        name="bad_nested_additional",
                        description="bad nested additional",
                        inputSchema={
                            "type": "object",
                            "additionalProperties": {
                                "type": "object",
                                "properties": {"x": "bad"},
                            },
                        },
                    ),
                ]
            )

    manager = MCPManager(
        process_factory=lambda name, cfg: MCPProcess(
            name, cfg.command, [], {}, session=InvalidSession()
        )
    )
    diags = await manager.spawn_all({"echo": McpServerCfg(command="true")})

    assert [spec.name for spec in manager.tool_specs()] == []
    messages = [diag.message for diag in diags if not diag.ok]
    assert any("bad.name" in message for message in messages)
    assert any("bad_schema" in message for message in messages)
    assert any("scalar_schema" in message and "object schema" in message for message in messages)
    assert any("bad_property" in message and "object schema" in message for message in messages)
    assert any(
        "bad_nested_additional" in message and "object schema" in message
        for message in messages
    )
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
    gateway = ToolGateway(registry, TaskStore())
    result = await gateway.submit(ToolCall(id="c2", name="echo__ping", arguments={}))
    assert result.is_error
    assert result.content == "nope"
    await manager.shutdown()


ECHO_SRC = '''\
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("echo", log_level="WARNING")


@mcp.tool()
def ping() -> str:
    return "pong"


if __name__ == "__main__":
    mcp.run()
'''


def _write_echo(tmp_path: Path, name: str = "echo_fixture.py") -> Path:
    script = tmp_path / name
    script.write_text(ECHO_SRC, encoding="utf-8")
    return script


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
async def test_echo_stdio_list_call_shutdown(tmp_path: Path) -> None:
    script = _write_echo(tmp_path)
    manager = MCPManager()
    diags = await manager.spawn_all(
        {
            "echo": McpServerCfg(
                command=sys.executable,
                args=[str(script)],
            )
        }
    )
    assert [diag.ok for diag in diags] == [True], diags
    specs = manager.tool_specs()
    assert [spec.name for spec in specs] == ["echo__ping"]
    registry = ToolRegistry()
    registry.register(specs[0])
    gateway = ToolGateway(registry, TaskStore())
    result = await gateway.submit(ToolCall(id="c3", name="echo__ping", arguments={}))
    assert not result.is_error
    assert "pong" in result.content
    await manager.shutdown()
    await manager.shutdown()


@pytest.mark.asyncio
async def test_echo_shutdown_from_other_task(tmp_path: Path) -> None:
    script = _write_echo(tmp_path)
    manager = MCPManager()

    async def spawn() -> None:
        diags = await manager.spawn_all(
            {"echo": McpServerCfg(command=sys.executable, args=[str(script)])}
        )
        assert [diag.ok for diag in diags] == [True], diags

    await asyncio.create_task(spawn())
    await asyncio.create_task(manager.shutdown())


@pytest.mark.asyncio
async def test_killed_child_call_is_informative_and_stays_flagged(tmp_path: Path) -> None:
    import signal

    script = _write_echo(tmp_path, "doomed_echo.py")
    manager = MCPManager()
    diags = await manager.spawn_all(
        {"doomed": McpServerCfg(command=sys.executable, args=[str(script)])}
    )
    assert [diag.ok for diag in diags] == [True]
    try:
        out = subprocess.run(
            ["pgrep", "-f", str(script)], capture_output=True, text=True
        )
        os.kill(int(out.stdout.strip().split()[0]), signal.SIGKILL)
        await asyncio.sleep(0.5)
        proc = manager._live[0][0]
        with pytest.raises(RuntimeError, match="doomed.*failed"):
            await asyncio.wait_for(proc.call("ping", {}), timeout=15)
        # Flagged, not a silent zombie: the next call is informative too.
        with pytest.raises(RuntimeError, match="doomed"):
            await asyncio.wait_for(proc.call("ping", {}), timeout=15)
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_recorded_failure_surfaces_after_session_gone() -> None:
    proc = MCPProcess("gone", "unused", [], {})
    proc._failure = RuntimeError("child exited")
    with pytest.raises(RuntimeError, match="gone.*child exited"):
        await proc.call("ping", {})


@pytest.mark.asyncio
async def test_dup_tool_name_first_wins_with_skip_diagnostic() -> None:
    """spawn_all and tool_specs agree: first wins, dup yields a skip diag."""
    session = _FakeSession()
    session.tools = [
        Tool(name="ping", inputSchema={"type": "object", "properties": {}}),
        Tool(name="ping", inputSchema={"type": "object", "properties": {}}),
    ]
    manager = MCPManager(process_factory=_factory(session))
    diags = await manager.spawn_all({"echo": McpServerCfg(command="true")})
    assert [diag.ok for diag in diags] == [True, False]
    assert "already registered" in diags[1].message
    assert [spec.name for spec in manager.tool_specs()] == ["echo__ping"]
    await manager.shutdown()


# Moved from test_b4_unification.py / test_b4_p2.py (B4 batch).
def test_m4_resolve_command_narrow_match() -> None:
    import sys

    from thyca.tools.mcp import resolve_command

    assert resolve_command("python3foo") == "python3foo"
    assert resolve_command("python3") == sys.executable
    assert resolve_command("python3.14") == sys.executable
    assert resolve_command("/usr/bin/python3") == sys.executable


def _server_cfg(**overrides) -> McpServerCfg:
    fields: dict = {"command": "tavily", "args": [], "env": {}}
    fields.update(overrides)
    return McpServerCfg(**fields)


@pytest.mark.asyncio
async def test_sync_spawns_added_server() -> None:
    manager = MCPManager(process_factory=_factory(_FakeSession()))
    diags, removed = await manager.sync({"tavily": _server_cfg()})
    assert [diag.ok for diag in diags] == [True]
    assert removed == []
    assert [spec.name for spec in manager.tool_specs()] == ["tavily__ping"]
    await manager.shutdown()


@pytest.mark.asyncio
async def test_sync_stops_removed_server_and_reports_names() -> None:
    manager = MCPManager(process_factory=_factory(_FakeSession()))
    await manager.spawn_all({"tavily": _server_cfg()})
    proc = manager._live[0][0]
    diags, removed = await manager.sync({})
    assert diags == []
    assert removed == ["tavily__ping"]
    assert manager._live == []
    assert manager.tool_specs() == []
    assert proc._session is None  # aclosed
    await manager.shutdown()


@pytest.mark.asyncio
async def test_sync_keeps_unchanged_server_without_respawn() -> None:
    manager = MCPManager(process_factory=_factory(_FakeSession()))
    await manager.spawn_all({"tavily": _server_cfg()})
    proc = manager._live[0][0]
    diags, removed = await manager.sync({"tavily": _server_cfg()})
    assert diags == []
    assert removed == []
    assert manager._live[0][0] is proc
    await manager.shutdown()


@pytest.mark.asyncio
async def test_sync_respawns_on_env_change() -> None:
    started: list[str] = []
    base = _factory(_FakeSession())

    def make(name: str, cfg: McpServerCfg):
        started.append(cfg.env.get("K", ""))
        return base(name, cfg)

    manager = MCPManager(process_factory=make)
    await manager.spawn_all({"t": _server_cfg(env={"K": "1"})})
    old = manager._live[0][0]
    diags, removed = await manager.sync({"t": _server_cfg(env={"K": "2"})})
    assert [diag.ok for diag in diags] == [True]
    assert removed == ["t__ping"]
    assert manager._live[0][0] is not old
    assert started == ["1", "2"]
    await manager.shutdown()


@pytest.mark.asyncio
async def test_sync_failed_spawn_reports_diag_and_retries_next_sync() -> None:
    class FailStart(_FakeSession):
        async def initialize(self) -> object:
            raise RuntimeError("boom")

    manager = MCPManager(process_factory=_factory(FailStart()))
    diags, removed = await manager.sync({"bad": _server_cfg()})
    assert len(diags) == 1
    assert diags[0].ok is False
    assert "boom" in diags[0].message
    assert removed == []
    assert manager.tool_specs() == []
    diags, _ = await manager.sync({"bad": _server_cfg()})
    assert len(diags) == 1 and diags[0].ok is False
    await manager.shutdown()


@pytest.mark.asyncio
async def test_sync_stop_failure_is_swallowed() -> None:
    class CloseFails(MCPProcess):
        async def aclose(self) -> None:
            raise RuntimeError("close boom")

    session = _FakeSession()
    manager = MCPManager(
        process_factory=lambda name, cfg: CloseFails(
            name, cfg.command, list(cfg.args), dict(cfg.env), session=session
        )
    )
    await manager.spawn_all({"t": _server_cfg()})
    diags, removed = await manager.sync({})
    assert diags == []
    assert removed == ["t__ping"]
    assert manager._live == []
    await manager.shutdown()


@pytest.mark.asyncio
async def test_failed_servers_tracks_startup_errors_unwraps_groups_and_clears() -> None:
    class GroupFail(_FakeSession):
        async def initialize(self) -> object:
            raise ExceptionGroup(
                "unhandled errors in a TaskGroup",
                [RuntimeError("unable to get local issuer certificate\nline2")],
            )

    state = {"broken": True}

    def make(name: str, cfg: McpServerCfg) -> MCPProcess:
        session = GroupFail() if state["broken"] else _FakeSession()
        return MCPProcess(name, cfg.command, list(cfg.args), dict(cfg.env), session=session)

    manager = MCPManager(process_factory=make)
    await manager.spawn_all({"tavily": _server_cfg()})
    failed = manager.failed_servers()
    assert "tavily" in failed
    assert "RuntimeError: unable to get local issuer certificate line2" == failed["tavily"]
    summary = manager.prompt_summary()
    assert "tavily" in summary and "unable to get local issuer certificate line2" in summary

    # Removing from config clears its diagnostic even before another spawn.
    await manager.sync({})
    assert manager.failed_servers() == {}
    assert manager.prompt_summary() == ""

    # Recovering on sync clears the failed entry as well.
    await manager.sync({"tavily": _server_cfg()})
    assert "tavily" in manager.failed_servers()
    state["broken"] = False
    await manager.sync({"tavily": _server_cfg()})
    assert manager.failed_servers() == {}
    assert manager.prompt_summary() == ""
    await manager.shutdown()


@pytest.mark.asyncio
async def test_process_start_timeout_cancels_hung_run(monkeypatch: pytest.MonkeyPatch) -> None:
    from contextlib import asynccontextmanager

    cleaned = {"exited": False}

    @asynccontextmanager
    async def fake_stdio(_params):
        try:
            yield object(), object()
        finally:
            cleaned["exited"] = True

    class HungClientSession:
        def __init__(self, _read, _write) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def initialize(self) -> object:
            await asyncio.Future()
            return {}

    monkeypatch.setattr("thyca.tools.mcp.stdio_client", fake_stdio)
    monkeypatch.setattr("thyca.tools.mcp.ClientSession", HungClientSession)

    proc = MCPProcess("hung", "unused", [], {}, start_timeout=timedelta(seconds=0.05))
    with pytest.raises(TimeoutError, match="startup timed out after 0.05s"):
        await proc.start()
    assert cleaned["exited"] is True
    assert proc._task is None


@pytest.mark.asyncio
async def test_spawn_all_isolates_hung_server_timeout() -> None:
    class HungSession(_FakeSession):
        async def initialize(self) -> object:
            await asyncio.Future()
            return {}

    def make(name: str, cfg: McpServerCfg) -> MCPProcess:
        if name == "hung":
            return MCPProcess(
                name,
                cfg.command,
                [],
                {},
                session=HungSession(),
                start_timeout=timedelta(seconds=0.05),
            )
        return MCPProcess(name, cfg.command, [], {}, session=_FakeSession())

    manager = MCPManager(process_factory=make)
    diags = await manager.spawn_all(
        {
            "hung": McpServerCfg(command="true"),
            "echo": McpServerCfg(command="true"),
        }
    )
    by_name = {diag.server: diag for diag in diags}
    assert by_name["hung"].ok is False
    assert by_name["hung"].message == "TimeoutError: startup timed out after 0.05s"
    assert by_name["echo"].ok is True
    assert [spec.name for spec in manager.tool_specs()] == ["echo__ping"]
    assert manager.failed_servers() == {"hung": "TimeoutError: startup timed out after 0.05s"}
    assert "- hung: TimeoutError: startup timed out after 0.05s" in manager.prompt_summary()
    await manager.shutdown()


@pytest.mark.asyncio
async def test_real_hung_subprocess_times_out_and_keeps_echo_alive(tmp_path: Path) -> None:
    hung_script = tmp_path / "hung_mcp.py"
    hung_script.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    echo_script = _write_echo(tmp_path)

    def make(name: str, cfg: McpServerCfg) -> MCPProcess:
        timeout = timedelta(seconds=0.2) if name == "hung" else None
        return MCPProcess(
            name, cfg.command, list(cfg.args), dict(cfg.env), start_timeout=timeout
        )

    manager = MCPManager(process_factory=make)
    diags = await manager.spawn_all(
        {
            "hung": McpServerCfg(command=sys.executable, args=[str(hung_script)]),
            "echo": McpServerCfg(command=sys.executable, args=[str(echo_script)]),
        }
    )
    by_name = {diag.server: diag for diag in diags}
    assert by_name["hung"].ok is False
    assert by_name["hung"].message == "TimeoutError: startup timed out after 0.2s"
    assert by_name["echo"].ok is True
    assert [spec.name for spec in manager.tool_specs()] == ["echo__ping"]
    assert "hung" in manager.failed_servers()
    await manager.shutdown()


@pytest.mark.asyncio
async def test_real_crashing_subprocess_fails_soft_alongside_healthy(tmp_path: Path) -> None:
    crash_script = tmp_path / "crash_mcp.py"
    crash_script.write_text("import sys\nsys.exit(1)\n", encoding="utf-8")
    echo_script = _write_echo(tmp_path)

    manager = MCPManager()
    diags = await manager.spawn_all(
        {
            "crash": McpServerCfg(command=sys.executable, args=[str(crash_script)]),
            "echo": McpServerCfg(command=sys.executable, args=[str(echo_script)]),
        }
    )
    by_name = {diag.server: diag for diag in diags}
    assert by_name["crash"].ok is False
    assert by_name["echo"].ok is True
    assert [spec.name for spec in manager.tool_specs()] == ["echo__ping"]
    assert "crash" in manager.failed_servers()
    await manager.shutdown()


@pytest.mark.asyncio
async def test_list_tools_timeout_is_caught_and_sync_recovers() -> None:
    class HangListTools(_FakeSession):
        async def list_tools(self):
            await asyncio.Future()

    broken = {"on": True}

    def make(name: str, cfg: McpServerCfg) -> MCPProcess:
        session = HangListTools() if broken["on"] else _FakeSession()
        return MCPProcess(
            name,
            cfg.command,
            [],
            {},
            session=session,
            start_timeout=timedelta(seconds=0.05),
        )

    manager = MCPManager(process_factory=make)
    diags, _ = await manager.sync({"slow": _server_cfg()})
    assert len(diags) == 1 and diags[0].ok is False
    assert "startup timed out after 0.05s" in diags[0].message
    assert "slow" in manager.failed_servers()

    broken["on"] = False
    diags, _ = await manager.sync({"slow": _server_cfg()})
    assert [diag.ok for diag in diags] == [True]
    assert manager.failed_servers() == {}
    assert manager.prompt_summary() == ""
    assert [spec.name for spec in manager.tool_specs()] == ["slow__ping"]
    await manager.shutdown()


def test_format_mcp_status_and_summarize_error_bounds() -> None:
    assert format_mcp_status({}) == ""
    long_exc = RuntimeError("x " * 300)
    summary = _summarize_error(long_exc)
    assert len(summary) <= 200
    assert summary.endswith("...")

    rendered = format_mcp_status({"a": "first\nline", "b": ""})
    assert "- a: first line" in rendered
    assert "- b: startup failed" in rendered


def test_report_spawn_diags_emits_single_warning_line_per_failure() -> None:
    from io import StringIO

    from thyca.app.toolchain import report_spawn_diags
    from thyca.tools.mcp import StartupDiagnostic

    buf = StringIO()
    report_spawn_diags(
        [
            StartupDiagnostic("healthy", True, ""),
            StartupDiagnostic("tavily", False, "TimeoutError: startup timed out after 10s"),
        ],
        err=buf,
    )
    assert buf.getvalue().splitlines() == [
        "warning: mcp tavily: TimeoutError: startup timed out after 10s"
    ]
