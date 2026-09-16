from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from io import StringIO
from pathlib import Path

from thyca.cli import Cli
from thyca.config import McpServerCfg, default_config, load, save
from thyca.llm.llm_base import ChatReply, LLMError
from thyca.protocol import Message, ToolCall
from thyca.tools.mcp import StartupDiagnostic
from thyca.tools.registry import ToolSpec
from thyca.sessions import SessionError, SessionManager


@dataclass
class FakeLLM:
    reply: ChatReply
    requests: list[list[Message]] = field(default_factory=list)
    model: str | None = None

    async def chat(self, messages: list[Message], tools: list | None = None) -> ChatReply:
        self.requests.append(list(messages))
        return self.reply


def _cli(tmp_path: Path, connect, stdin: str = "") -> tuple[Cli, StringIO, StringIO]:
    out = StringIO()
    err = StringIO()
    cli = Cli(
        thyca_dir=tmp_path,
        connect=connect,
        stdin=StringIO(stdin),
        stdout=out,
        stderr=err,
    )
    return cli, out, err


def test_empty_print_exits_2(tmp_path: Path) -> None:
    cli, _out, err = _cli(tmp_path, FakeLLM(ChatReply(content="x")))
    assert cli.main(["-p"]) == 2
    assert "requires a prompt" in err.getvalue()


def test_continue_and_session_exclusive(tmp_path: Path) -> None:
    cli, _out, err = _cli(tmp_path, FakeLLM(ChatReply(content="x")))
    assert cli.main(["--continue", "--session", "x"]) == 2
    assert "mutually exclusive" in err.getvalue()


def test_prompt_without_p_exits_2(tmp_path: Path) -> None:
    cli, _out, err = _cli(tmp_path, FakeLLM(ChatReply(content="x")))
    assert cli.main(["hello"]) == 2
    assert "requires -p" in err.getvalue()


def test_missing_session_exits_nonzero(tmp_path: Path) -> None:
    cli, _out, err = _cli(tmp_path, FakeLLM(ChatReply(content="x")))
    assert cli.main(["--session", "no-such", "-p", "hi"]) == 1
    assert "session" in err.getvalue().lower()


def test_print_writes_reply_and_persists(tmp_path: Path) -> None:
    llm = FakeLLM(ChatReply(content="pong"))
    cli, out, err = _cli(tmp_path, llm)
    assert cli.main(["-p", "ping"]) == 0
    assert err.getvalue() == ""
    assert out.getvalue().strip() == "pong"
    assert llm.requests[0][0].role == "system"
    assert llm.requests[0][-1].content == "ping"
    manager = SessionManager(tmp_path / "sessions")
    session = manager.continue_last()
    assert [(m.role, m.content) for m in session.messages] == [
        ("user", "ping"),
        ("assistant", "pong"),
    ]


def test_continue_without_prior_session_creates_one(tmp_path: Path) -> None:
    cli, out, err = _cli(tmp_path, FakeLLM(ChatReply(content="new")))

    assert cli.main(["--continue", "-p", "first"]) == 0
    assert out.getvalue().strip() == "new"
    assert err.getvalue() == ""


def test_continue_creation_failure_is_reported(tmp_path: Path, monkeypatch) -> None:
    def fail_create(self, *, make_current: bool = True):
        raise SessionError("cannot create fallback session")

    monkeypatch.setattr(SessionManager, "create", fail_create)
    cli, _out, err = _cli(tmp_path, FakeLLM(ChatReply(content="unused")))

    assert cli.main(["--continue", "-p", "first"]) == 1
    assert "cannot create fallback session" in err.getvalue()


def test_continue_remembers_prior_turn(tmp_path: Path) -> None:
    first = FakeLLM(ChatReply(content="one"))
    assert _cli(tmp_path, first)[0].main(["-p", "first"]) == 0
    second = FakeLLM(ChatReply(content="two"))
    cli, out, _err = _cli(tmp_path, second)
    assert cli.main(["--continue", "-p", "again"]) == 0
    assert out.getvalue().strip() == "two"
    roles = [m.role for m in second.requests[0]]
    assert roles == ["system", "user", "assistant", "user"]
    assert [m.content for m in second.requests[0] if m.role == "user"] == ["first", "again"]


def test_cli_registers_and_closes_configured_mcp_tools(tmp_path: Path, monkeypatch) -> None:
    class FakeManager:
        def __init__(self) -> None:
            self.servers = None
            self.closed = False

        async def spawn_all(self, servers):
            self.servers = servers
            return []

        def tool_specs(self):
            async def ping(_args: dict) -> str:
                return "pong"

            return [
                ToolSpec(
                    name="echo__ping",
                    description="ping",
                    parameters={"type": "object", "properties": {}},
                    handler=ping,
                )
            ]

        async def shutdown(self) -> None:
            self.closed = True

    manager = FakeManager()
    monkeypatch.setattr("thyca.cli.MCPManager", lambda: manager)
    cfg = replace(
        default_config(),
        mcpServers={"echo": McpServerCfg(command="ignored")},
    )
    save(cfg, tmp_path / "config.json")

    @dataclass
    class ToolCallingLLM:
        requests: list[list[Message]] = field(default_factory=list)
        schemas: list[list[dict] | None] = field(default_factory=list)
        replies: list[ChatReply] = field(
            default_factory=lambda: [
                ChatReply(
                    content=None,
                    tool_calls=[ToolCall(id="c1", name="echo__ping")],
                ),
                ChatReply(content="done"),
            ]
        )

        async def chat(self, messages, tools=None):
            self.requests.append(list(messages))
            self.schemas.append(tools)
            return self.replies.pop(0)

    llm = ToolCallingLLM()
    cli, out, err = _cli(tmp_path, llm)

    assert cli.main(["-p", "ping"]) == 0
    assert out.getvalue().strip() == "done"
    assert err.getvalue() == ""
    assert manager.servers == {"echo": cfg.mcpServers["echo"]}
    assert manager.closed
    assert any(item["function"]["name"] == "echo__ping" for item in llm.schemas[0])


def test_cli_mcp_diagnostic_includes_server_name(tmp_path: Path, monkeypatch) -> None:
    class FakeManager:
        async def spawn_all(self, servers):
            return [StartupDiagnostic("remote", False, "failed to start")]

        def tool_specs(self):
            return []

        async def shutdown(self) -> None:
            return

    monkeypatch.setattr("thyca.cli.MCPManager", FakeManager)
    cli, _out, err = _cli(tmp_path, FakeLLM(ChatReply(content="ok")))

    assert cli.main(["-p", "hi"]) == 0
    assert "remote: failed to start" in err.getvalue()


def test_model_override_does_not_persist(tmp_path: Path) -> None:
    save(default_config(), tmp_path / "config.json")
    before = load(tmp_path / "config.json").provider.model
    cli, _out, _err = _cli(tmp_path, FakeLLM(ChatReply(content="ok")))
    assert cli.main(["--model", "override-model", "-p", "hi"]) == 0
    assert load(tmp_path / "config.json").provider.model == before


def test_repl_eof_exits_0(tmp_path: Path) -> None:
    llm = FakeLLM(ChatReply(content="hi"))
    cli, out, err = _cli(tmp_path, llm, stdin="hello\n")
    assert cli.main([]) == 0
    assert err.getvalue() == ""
    assert "session " in out.getvalue()
    assert "you> " in out.getvalue()
    assert "hi" in out.getvalue()
    assert llm.requests[0][-1].content == "hello"


def test_debug_prints_prompt_flags(tmp_path: Path) -> None:
    cli, _out, err = _cli(tmp_path, FakeLLM(ChatReply(content="ok")))
    assert cli.main(["--debug", "-p", "hi"]) == 0
    dbg = err.getvalue()
    assert "debug " in dbg
    assert "soul=True" in dbg
    assert "identity=True" in dbg
    assert "tools=11" in dbg


def test_print_llm_error_is_safe(tmp_path: Path) -> None:
    class Boom:
        async def chat(self, messages, tools=None):
            raise LLMError("provider HTTP 401: denied [redacted]")

    cli, _out, err = _cli(tmp_path, Boom())
    assert cli.main(["-p", "hi"]) == 1
    assert "401" in err.getvalue()
    raw = json.dumps(default_config().to_dict())
    assert "sk-" not in err.getvalue()
    assert raw not in err.getvalue()
