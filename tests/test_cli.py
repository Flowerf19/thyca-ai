from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from io import StringIO
from pathlib import Path

import pytest

from thyca.app.cli import Cli
from thyca.configs import ModelCfg, McpServerCfg, default_config, load, save
from seeded_config import seeded_config
from thyca.llms.llm_base import ChatReply, LLMError
from thyca.core.protocol import Message, ToolCall
from thyca.tools.mcp import StartupDiagnostic
from thyca.tools.registry import ToolSpec
from thyca.conversations.runtime import ConversationManager


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
    # Runnable fixture config; tests with their own config overwrite it.
    # Only seed when the test has not written one already.
    if not (tmp_path / "config.json").exists():
        save(seeded_config(), tmp_path / "config.json")
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


def test_bot_and_group_exclusive(tmp_path: Path) -> None:
    cli, _out, err = _cli(tmp_path, FakeLLM(ChatReply(content="x")))
    assert cli.main(["--bot", "thyca", "--group", "group-" + "0" * 32]) == 2
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


def test_missing_group_exits_nonzero(tmp_path: Path) -> None:
    cli, _out, err = _cli(tmp_path, FakeLLM(ChatReply(content="x")))
    assert cli.main(["--group", "group-" + "0" * 32, "-p", "hi"]) == 1
    assert "not found" in err.getvalue().lower()


def test_print_writes_reply_and_persists(tmp_path: Path) -> None:
    llm = FakeLLM(ChatReply(content="pong"))
    cli, out, err = _cli(tmp_path, llm)
    assert cli.main(["-p", "ping"]) == 0
    assert err.getvalue() == ""
    assert out.getvalue().strip() == "pong"
    assert llm.requests[0][0].role == "system"
    assert llm.requests[0][-1].content == "ping"
    manager = ConversationManager(tmp_path / "sessions")
    conversation = manager.continue_last()
    assert [(m.role, m.content) for m in conversation.messages] == [
        ("user", "ping"),
        ("assistant", "pong"),
    ]


def test_bot_flag_without_prior_conversation_creates_dm(tmp_path: Path) -> None:
    cli, out, err = _cli(tmp_path, FakeLLM(ChatReply(content="new")))

    assert cli.main(["--bot", "thyca", "-p", "first"]) == 0
    assert out.getvalue().strip() == "new"
    assert err.getvalue() == ""


def test_unknown_bot_exits_nonzero(tmp_path: Path) -> None:
    cli, _out, err = _cli(tmp_path, FakeLLM(ChatReply(content="unused")))

    assert cli.main(["--bot", "no-such", "-p", "first"]) == 1
    assert "no-such" in err.getvalue()


def test_default_reopens_thyca_dm_and_remembers(tmp_path: Path) -> None:
    first = FakeLLM(ChatReply(content="one"))
    assert _cli(tmp_path, first)[0].main(["-p", "first"]) == 0
    second = FakeLLM(ChatReply(content="two"))
    cli, out, _err = _cli(tmp_path, second)
    assert cli.main(["-p", "again"]) == 0
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
        seeded_config(),
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
    assert err.getvalue().strip() == "warning: mcp remote: failed to start"


def test_model_override_does_not_persist(tmp_path: Path) -> None:
    cfg = replace(seeded_config(), models={"override-model": ModelCfg()})
    save(cfg, tmp_path / "config.json")
    before = load(tmp_path / "config.json").provider.model
    cli, _out, _err = _cli(tmp_path, FakeLLM(ChatReply(content="ok")))
    assert cli.main(["--model", "override-model", "-p", "hi"]) == 0
    assert load(tmp_path / "config.json").provider.model == before


def test_unknown_model_override_is_rejected_cleanly(tmp_path: Path) -> None:
    save(seeded_config(), tmp_path / "config.json")
    cli, _out, err = _cli(tmp_path, FakeLLM(ChatReply(content="unused")))
    assert cli.main(["--model", "unknown-model", "-p", "hi"]) == 1
    assert "invalid model" in err.getvalue()
    assert "Traceback" not in err.getvalue()


def test_repl_eof_exits_0(tmp_path: Path) -> None:
    llm = FakeLLM(ChatReply(content="hi"))
    cli, out, err = _cli(tmp_path, llm, stdin="hello\n")
    assert cli.main([]) == 0
    assert err.getvalue() == ""
    assert "dm thyca" in out.getvalue()
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
    from thyca.configs import ModelCfg, ProviderEntry

    cfg = replace(
        default_config(),
        providers={
            "default": ProviderEntry(baseUrl="https://a.invalid/v1"),
            "second": ProviderEntry(baseUrl="https://b.invalid/v1"),
        },
        defaultProvider="default",
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

    from thyca.configs import ModelCfg, ProviderEntry

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

    monkeypatch.setattr("thyca.llms._http.httpx.AsyncClient", client_factory)
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
        defaultProvider="default",
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


def test_print_tags_memory_with_cli_conversation(tmp_path: Path) -> None:
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
    conversation = ConversationManager(tmp_path / "sessions").continue_last()
    (day_file,) = list((tmp_path / "memory").glob("*.md"))
    assert f'"chat":"{conversation.conversation_id}"' in day_file.read_text(encoding="utf-8")


def test_default_opens_thyca_dm(tmp_path: Path) -> None:
    cli, _out, _err = _cli(tmp_path, FakeLLM(ChatReply(content="hi")))
    assert cli.main(["-p", "hello"]) == 0
    manager = ConversationManager(tmp_path / "sessions")
    conversation = manager.continue_last()
    assert conversation.dm_bot == "thyca"
    assert conversation.bots == ["thyca"]


def test_default_opens_dm_despite_newer_group(tmp_path: Path) -> None:
    from thyca.app.chat_app import ChatApp

    cli, _out, _err = _cli(tmp_path, FakeLLM(ChatReply(content="hi")))
    assert cli.main(["-p", "first"]) == 0
    # A newer group exists (e.g. from serve); the default still opens the DM.
    from thyca.bots.card import BotCard

    app = ChatApp(tmp_path, load(tmp_path / "config.json"),
                  connect=FakeLLM(ChatReply(content="x")))
    try:
        app._bots.create(BotCard(
            bot_id="coder", name="Coder", role="Coder", description="Codes.",
            persona="You code.",
        ))
        app.create(bots=["thyca", "coder"])
    finally:
        app.shutdown()
    cli2, _out2, _err2 = _cli(tmp_path, FakeLLM(ChatReply(content="again")))
    assert cli2.main(["-p", "second"]) == 0
    app2 = ChatApp(tmp_path, load(tmp_path / "config.json"),
                   connect=FakeLLM(ChatReply(content="x")))
    try:
        dm = app2.bot_dm("thyca")
    finally:
        app2.shutdown()
    assert dm["kind"] == "dm" and dm["dm"] == "thyca"
    assert [m["content"] for m in dm["messages"] if m["role"] == "user"] == ["first", "second"]


def test_group_flag_loads_group_by_id(tmp_path: Path) -> None:
    from thyca.app.chat_app import ChatApp

    cli, _out, _err = _cli(tmp_path, FakeLLM(ChatReply(content="hi")))
    assert cli.main(["-p", "first"]) == 0
    from thyca.bots.card import BotCard

    app = ChatApp(tmp_path, load(tmp_path / "config.json"),
                  connect=FakeLLM(ChatReply(content="x")))
    try:
        app._bots.create(BotCard(
            bot_id="coder", name="Coder", role="Coder", description="Codes.",
            persona="You code.",
        ))
        group_id = app.create(bots=["thyca", "coder"])["conversation_id"]
    finally:
        app.shutdown()
    cli2, _out2, _err2 = _cli(tmp_path, FakeLLM(ChatReply(content="in-group")))
    assert cli2.main(["--group", group_id, "-p", "hello"]) == 0
    reloaded = ConversationManager(tmp_path / "sessions").load(group_id)
    assert [m.content for m in reloaded.messages][-1] == "in-group"
