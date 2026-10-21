from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from io import StringIO
from pathlib import Path

import pytest

from thyca.app.cli import Cli
from thyca.config import ModelCfg, McpServerCfg, default_config, load, save
from thyca.llm.llm_base import ChatReply, LLMError
from thyca.core.protocol import Message, ToolCall
from thyca.tools.mcp import StartupDiagnostic
from thyca.tools.registry import ToolSpec
from thyca.sessions import SessionError, SessionManager


@dataclass
class FakeLLM:
    reply: ChatReply
    requests: list[list[Message]] = field(default_factory=list)
    schemas: list[list[dict] | None] = field(default_factory=list)
    model: str | None = None

    async def chat(self, messages: list[Message], tools: list | None = None) -> ChatReply:
        self.requests.append(list(messages))
        self.schemas.append(tools)
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


def test_cli_reports_config_error_without_traceback(tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text("not json", encoding="utf-8")
    cli, _out, err = _cli(tmp_path, FakeLLM(ChatReply(content="unused")))
    assert cli.main(["-p", "hi"]) == 1
    assert "Traceback" not in err.getvalue()
    assert "config" in err.getvalue().lower()


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

        async def sync(self, servers):
            self.servers = servers
            return [], set()

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
    monkeypatch.setattr("thyca.app.chat_app.MCPManager", lambda: manager)
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

        async def sync(self, servers):
            return [], set()

        def tool_specs(self):
            return []

        async def shutdown(self) -> None:
            return

    monkeypatch.setattr("thyca.app.chat_app.MCPManager", FakeManager)
    cli, _out, err = _cli(tmp_path, FakeLLM(ChatReply(content="ok")))

    assert cli.main(["-p", "hi"]) == 0
    assert "remote: failed to start" in err.getvalue()


def test_model_override_does_not_persist(tmp_path: Path) -> None:
    cfg = replace(default_config(), models={"override-model": ModelCfg()})
    save(cfg, tmp_path / "config.json")
    before = load(tmp_path / "config.json").provider.model
    cli, _out, _err = _cli(tmp_path, FakeLLM(ChatReply(content="ok")))
    assert cli.main(["--model", "override-model", "-p", "hi"]) == 0
    assert load(tmp_path / "config.json").provider.model == before


def test_unknown_model_override_is_rejected_cleanly(tmp_path: Path) -> None:
    save(default_config(), tmp_path / "config.json")
    cli, _out, err = _cli(tmp_path, FakeLLM(ChatReply(content="unused")))
    assert cli.main(["--model", "unknown-model", "-p", "hi"]) == 1
    assert "invalid model" in err.getvalue()
    assert "Traceback" not in err.getvalue()


def test_repl_eof_exits_0(tmp_path: Path) -> None:
    llm = FakeLLM(ChatReply(content="hi"))
    cli, out, err = _cli(tmp_path, llm, stdin="hello\n")
    assert cli.main([]) == 0
    assert err.getvalue() == ""
    assert "session " in out.getvalue()
    assert "you> " in out.getvalue()
    assert "hi" in out.getvalue()
    assert llm.requests[0][-1].content == "hello"


@pytest.mark.parametrize("custom_persona", [False, True])
@pytest.mark.parametrize("user", [None, "", "# User\n", "# User\nPrefers short answers.\n"])
def test_debug_prints_prompt_flags(
    tmp_path: Path, custom_persona: bool, user: str | None
) -> None:
    if custom_persona:
        (tmp_path / "SOUL.md").write_text("# Custom voice\nSpeak plainly.\n", encoding="utf-8")
        (tmp_path / "IDENTITY.md").write_text("# Custom identity\nName: Local\n", encoding="utf-8")
    if user is not None:
        (tmp_path / "USER.md").write_text(user, encoding="utf-8")
    llm = FakeLLM(ChatReply(content="ok"))
    cli, _out, err = _cli(tmp_path, llm)
    assert cli.main(["--debug", "-p", "hi"]) == 0
    dbg = err.getvalue()
    assert "debug " in dbg
    assert "soul=True" in dbg
    assert "identity=True" in dbg
    has_user = user is None or user.strip() not in {"", "# User"}
    assert f"user={has_user}" in dbg
    assert "tools=" in dbg
    assert f"tools={len(llm.schemas[0] or [])}" in dbg
    system = llm.requests[0][0].content or ""
    assert f"system_chars={len(system)}" in dbg


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


def test_cli_factory_receives_registered_provider(tmp_path: Path, monkeypatch) -> None:
    from thyca.app.chat_app import ConnectFactory
    from thyca.config import ModelCfg, ProviderEntry

    cfg = replace(
        default_config(),
        providers={
            "default": ProviderEntry(baseUrl="https://a.invalid/v1"),
            "second": ProviderEntry(baseUrl="https://b.invalid/v1"),
        },
        defaultModel="model-a",
        models={"model-a": ModelCfg(), "model-b": ModelCfg(provider="second")},
    )
    save(cfg, tmp_path / "config.json")
    captured = []
    llm = FakeLLM(ChatReply(content="stub-ok", model="model-b"))

    def create(api, provider):
        captured.append((api, provider))
        return llm

    monkeypatch.setattr(ConnectFactory, "create", create)
    cli, out, _err = _cli(tmp_path, None)
    assert cli.main(["-p", "--model", "model-b", "hi"]) == 0
    assert out.getvalue().strip() == "stub-ok"
    assert len(captured) == 1
    api, provider = captured[0]
    assert api == cfg.providers["second"].api
    assert provider.model == "model-b"
    assert provider.baseUrl == "https://b.invalid/v1"


def test_print_model_override_routes_to_its_provider_http_auth(
    tmp_path: Path, monkeypatch
) -> None:
    import httpx

    from thyca.config import ModelCfg, ProviderEntry

    calls_a: list[dict] = []
    calls_b: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        call = {
            "url": str(request.url),
            "auth": request.headers.get("Authorization"),
            "model": body.get("model"),
        }
        if request.url.host == "a.invalid":
            calls_a.append(call)
            reply_model = "model-a"
        else:
            calls_b.append(call)
            reply_model = "model-b"
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            json={
                "model": reply_model,
                "choices": [{"message": {"content": "stub-ok"}}],
            },
        )

    original_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original_client(*args, **kwargs)

    monkeypatch.setattr("thyca.llm._http.httpx.AsyncClient", client_factory)
    cfg = replace(
        default_config(),
        providers={
            "default": ProviderEntry(
                baseUrl="https://a.invalid/v1", apiKey="sk-a"
            ),
            "second": ProviderEntry(
                baseUrl="https://b.invalid/v1", apiKey="sk-b"
            ),
        },
        defaultModel="model-a",
        models={"model-a": ModelCfg(), "model-b": ModelCfg(provider="second")},
    )
    save(cfg, tmp_path / "config.json")
    cli, out, _err = _cli(tmp_path, None)
    assert cli.main(["-p", "--model", "model-b", "hi"]) == 0
    assert out.getvalue().strip() == "stub-ok"
    assert calls_a == []
    assert calls_b == [{
        "url": "https://b.invalid/v1/chat/completions",
        "auth": "Bearer sk-b",
        "model": "model-b",
    }]


def test_print_tags_memory_with_cli_session(tmp_path: Path) -> None:
    remember = ChatReply(
        content=None,
        tool_calls=[
            ToolCall(
                id="c1",
                name="memory_remember",
                arguments={"topic": "t", "summary": "s"},
            )
        ],
    )

    class SeqLLM:
        def __init__(self) -> None:
            self.requests: list[list[Message]] = []

        async def chat(self, messages, tools=None):
            self.requests.append(list(messages))
            return remember if len(self.requests) == 1 else ChatReply(content="done")

    cli, _out, _err = _cli(tmp_path, SeqLLM())
    assert cli.main(["-p", "hi"]) == 0
    session = SessionManager(tmp_path / "sessions").continue_last()
    (day_file,) = list((tmp_path / "memory").glob("*.md"))
    assert f'"chat":"{session.id}"' in day_file.read_text(encoding="utf-8")


def test_default_opens_thyca_dm(tmp_path: Path) -> None:
    cli, _out, _err = _cli(tmp_path, FakeLLM(ChatReply(content="hi")))
    assert cli.main(["-p", "hello"]) == 0
    sessions = list((tmp_path / "sessions").glob("*.jsonl"))
    assert len(sessions) == 1
    manager = SessionManager(tmp_path / "sessions")
    session = manager.load(sessions[0].stem)
    assert session.dm_bot == "thyca"
    assert session.bots == ["thyca"]


def test_continue_opens_dm_despite_newer_group(tmp_path: Path) -> None:
    cli, _out, _err = _cli(tmp_path, FakeLLM(ChatReply(content="hi")))
    assert cli.main(["-p", "first"]) == 0
    # A newer group session exists on disk (e.g. from serve).
    manager = SessionManager(tmp_path / "sessions")
    group = manager.create()
    manager.store.append_bots(group.path, ["thyca", "coder"])
    cli2, _out2, _err2 = _cli(tmp_path, FakeLLM(ChatReply(content="again")))
    assert cli2.main(["--continue", "-p", "second"]) == 0
    current = SessionManager(tmp_path / "sessions").load(
        SessionManager(tmp_path / "sessions").continue_last().id)
    assert current.dm_bot == "thyca"


def test_session_flag_loads_group_by_id(tmp_path: Path) -> None:
    cli, _out, _err = _cli(tmp_path, FakeLLM(ChatReply(content="hi")))
    assert cli.main(["-p", "first"]) == 0
    manager = SessionManager(tmp_path / "sessions")
    group = manager.create()
    manager.store.append_bots(group.path, ["thyca", "coder"])
    cli2, _out2, _err2 = _cli(tmp_path, FakeLLM(ChatReply(content="in-group")))
    assert cli2.main(["--session", group.id, "-p", "hello"]) == 0
    reloaded = SessionManager(tmp_path / "sessions").load(group.id)
    assert [m.content for m in reloaded.messages][-1] == "in-group"
