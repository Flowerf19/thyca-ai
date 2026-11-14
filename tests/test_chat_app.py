"""ChatApp.turn event sink — TASK-004 verification."""
from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool
from test_serve_chat import FakeLLM, ScriptedLLM, _chat

from thyca.agents.events import TurnEvent
from thyca.app.chat_app import ChatApp
from thyca.configs import McpServerCfg, default_config, load, save
from seeded_config import seeded_config
from thyca.llms.llm_base import ChatReply, LLMError
from thyca.memories.active import ActiveMemory
from thyca.memories.heading import parse_heading
from thyca.core.protocol import Message, ToolCall
from thyca.conversations.runtime import ConversationBusy, ConversationManager
from thyca.tools.mcp import MCPProcess


def _naming_pairs(events: list[TurnEvent]) -> list[tuple[str, bool | None]]:
    return [
        (event.type, event.updated)
        for event in events
        if event.type.startswith("conversation.naming")
    ]


# Build a canonical-timeline manager holding one execution conversation.
def _canonical_manager(root: Path) -> ConversationManager:
    from thyca.bots.timeline import CanonicalConversationStore, WorkspaceTimeline
    from thyca.core.channel import WindowRef

    timeline = WorkspaceTimeline(root, "thyca")
    window = WindowRef(
        window_id="thyca", channel_id="webui", kind="dm", participants=("thyca",))
    conversation_id = "2026-01-01T00-00-00_ab12"
    store = CanonicalConversationStore(
        root / "bots" / "thyca" / "sessions",
        timeline=timeline, window=window, conversation_id=conversation_id)
    manager = ConversationManager(store=store)
    manager.load(conversation_id)
    return manager


def test_turns_carry_no_title(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [
            ChatReply(content="pong"),
            ChatReply(content="pong again"),
        ]
    )
    app = _chat(tmp_path, llm)
    created = app.create()
    first_events: list[TurnEvent] = []
    try:
        first = app.turn(created["conversation_id"], "alo", event_sink=first_events.append)
        assert first["reply"] == "pong"
        assert "title" not in first
        assert _naming_pairs(first_events) == []
        events: list[TurnEvent] = []
        turned = app.turn(created["conversation_id"], "kể thêm", event_sink=events.append)
        assert turned["reply"] == "pong again"
        # No title on either payload and no naming events ever fire.
        assert "title" not in turned
        assert _naming_pairs(events) == []
        # Only the two turn calls — no sidecar LLM call.
        assert len(llm.requests) == 2
        conversation = ConversationManager(tmp_path / "sessions").load(created["conversation_id"])
        assert [(item.role, item.content) for item in conversation.messages] == [
            ("user", "alo"),
            ("assistant", "pong"),
            ("user", "kể thêm"),
            ("assistant", "pong again"),
        ]
        assert not [m for m in conversation.messages
                    if (m.meta or {}).get("kind") == "naming"]
    finally:
        app.shutdown()




def test_sidecar_meta_does_not_flip_failed_turn_status(tmp_path: Path) -> None:
    from thyca.serve.trace import turns_from_conversation

    conversation = _canonical_manager(tmp_path)
    conversation.append(Message(role="user", content="alo", ts="2026-08-26T09:12:00Z"))
    conversation.append(
        Message(
            role="assistant",
            content="loop limit reached",
            ts="2026-08-26T09:12:05Z",
            meta={"kind": "llm", "round": 1, "finish_reason": "stop"},
        )
    )
    conversation.append(
        Message(
            role="assistant",
            content=None,
            ts="2026-08-26T09:12:06Z",
            meta={"kind": "compaction", "latency_ms": 300},
        )
    )
    turns = turns_from_conversation(conversation.current)
    assert len(turns) == 1
    assert turns[0].status == "loop_limit"




def test_second_turn_needs_no_rename(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [
            ChatReply(content="pong"),
            ChatReply(content="pong again"),
        ]
    )
    app = _chat(tmp_path, llm)
    created = app.create()
    try:
        app.turn(created["conversation_id"], "alo")
        events: list[TurnEvent] = []
        second = app.turn(created["conversation_id"], "thêm", event_sink=events.append)
        assert second["reply"] == "pong again"
        assert "title" not in second
        assert _naming_pairs(events) == []
        assert len(llm.requests) == 2
    finally:
        app.shutdown()




def test_memory_remember_injects_turn_conversation_id(tmp_path: Path) -> None:
    class RememberLLM:
        def __init__(self) -> None:
            self.calls = 0

        async def chat(self, messages, tools=None):
            self.calls += 1
            if self.calls == 1:
                return ChatReply(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="remember-1",
                            name="memory_remember",
                            arguments={"topic": "linked", "summary": "linked-token"},
                        )
                    ],
                )
            return ChatReply(content="done")

    app = _chat(tmp_path, RememberLLM())
    created = app.create()
    try:
        app.turn(created["conversation_id"], "remember this")
        daily = next((tmp_path / "memory").glob("*.md"))
        meta = next(
            parsed
            for line in daily.read_text(encoding="utf-8").splitlines()
            if (parsed := parse_heading(line))
        )
        assert meta.chat == created["conversation_id"]
    finally:
        app.shutdown()


def test_turn_without_sink_still_works(tmp_path: Path) -> None:
    llm = FakeLLM(ChatReply(content="pong"))
    app = _chat(tmp_path, llm)
    created = app.create()
    try:
        turned = app.turn(created["conversation_id"], "alo")
        assert turned["reply"] == "pong"
        assert "title" not in turned
    finally:
        app.shutdown()


def test_sink_not_stored_on_app(tmp_path: Path) -> None:
    app = _chat(tmp_path, FakeLLM(ChatReply(content="pong")))
    created = app.create()

    def sink(_event: TurnEvent) -> None:
        pass

    try:
        app.turn(created["conversation_id"], "alo", event_sink=sink)
        values = list(app.__dict__.values())
        assert all(value is not sink for value in values)
    finally:
        app.shutdown()


def test_two_conversations_run_turns_in_parallel(tmp_path: Path) -> None:
    """A turn in flight owns only its own conversation — it blocks no other one."""
    first_started = threading.Event()
    release = threading.Event()

    class Gated:
        async def chat(self, messages, tools=None):
            text = messages[-1].content
            if text == "slow":
                first_started.set()
                await asyncio.to_thread(release.wait)
            return ChatReply(content=f"reply:{text}")

    # One connect per turn is what the factory does; the injected one is
    # shared, so make each chat call independent instead of stateful.
    app = _chat(tmp_path, Gated())
    from thyca.bots.card import BotCard

    app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                             description="Codes.", persona="You code."))
    # Phase 2B: create() with no bots is Thyca's DM singleton, so the
    # parallel conversation below is a group (parallelism is model-agnostic).
    slow_id = app.create()["conversation_id"]
    errors: list[BaseException] = []
    slow: dict = {}

    def run_slow() -> None:
        try:
            slow.update(app.turn(slow_id, "slow"))
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=run_slow)
    worker.start()
    assert first_started.wait(2)
    try:
        # The second conversation is idle: its turn must complete while the first
        # one is still parked in the provider call.
        other_id = app.create(bots=["thyca", "coder"])["conversation_id"]
        started_at = time.monotonic()
        quick = app.turn(other_id, "@coder fast")
        elapsed = time.monotonic() - started_at
        assert quick["reply"] == "reply:@coder fast"
        assert elapsed < 2, f"second conversation waited on the first turn: {elapsed:.2f}s"
    finally:
        release.set()
        worker.join(timeout=5)
    assert not errors
    assert slow["reply"] == "reply:slow"
    assert sorted(app.running_conversations()) == []
    # Each turn wrote only its own transcript: sharing one conversation holder would
    # have appended the slow reply into whichever conversation the fast turn loaded.
    store = ConversationManager(tmp_path / "sessions")
    slow_messages = [(item.role, item.content) for item in store.load(slow_id).messages]
    fast_messages = [(item.role, item.content) for item in store.load(other_id).messages]
    assert slow_messages[:2] == [("user", "slow"), ("assistant", "reply:slow")]
    assert fast_messages[:2] == [
        ("user", "@coder fast"), ("assistant", "reply:@coder fast")]
    assert not any("fast" in (content or "") for _role, content in slow_messages)
    assert not any("slow" in (content or "") for _role, content in fast_messages)
    app.shutdown()


def test_second_turn_on_same_conversation_is_busy(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()

    class Slow:
        async def chat(self, messages, tools=None):
            started.set()
            await asyncio.to_thread(release.wait)
            return ChatReply(content="late")

    app = _chat(tmp_path, Slow())
    conversation_id = app.create()["conversation_id"]
    errors: list[BaseException] = []

    def run_turn() -> None:
        try:
            app.turn(conversation_id, "first")
        except BaseException as exc:  # surfaced by the assert below
            errors.append(exc)

    worker = threading.Thread(target=run_turn)
    worker.start()
    assert started.wait(2)
    try:
        assert app.running_conversations()[conversation_id]
        detail = app.get_payload(conversation_id)
        assert detail["running"] is True
        assert detail["started_at"]
        with pytest.raises(ConversationBusy):
            app.turn(conversation_id, "second")
    finally:
        release.set()
        worker.join(timeout=5)
    assert not errors
    assert app.running_conversations() == {}
    assert app.get_payload(conversation_id)["running"] is False
    app.shutdown()


def test_create_keeps_blank_conversation_with_running_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """create() prunes blank conversations except the one whose turn is in flight.

    A turn claims its conversation before writing anything, so an empty transcript
    on disk still belongs to a running turn.
    """
    claimed = threading.Event()
    release = threading.Event()
    real_refresh = ActiveMemory.refresh

    def gated_refresh(self, state, now, *args, **kwargs):
        claimed.set()
        release.wait(5)
        return real_refresh(self, state, now, *args, **kwargs)

    monkeypatch.setattr(ActiveMemory, "refresh", gated_refresh)
    app = _chat(tmp_path, FakeLLM(ChatReply(content="late")))
    from thyca.bots.card import BotCard

    app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                             description="Codes.", persona="You code."))
    running_id = app.create()["conversation_id"]
    assert app.get_payload(running_id)["messages"] == []
    errors: list[BaseException] = []

    def run_turn() -> None:
        try:
            app.turn(running_id, "hi")
        except BaseException as exc:  # surfaced by the assert below
            errors.append(exc)

    worker = threading.Thread(target=run_turn)
    worker.start()
    assert claimed.wait(2)
    try:
        # Phase 2B: the no-bots create is the DM singleton (== running_id
        # here), so the "fresh" conversation is a group.
        fresh = app.create(bots=["thyca", "coder"])
        assert fresh["conversation_id"] != running_id
        # Store equivalent of the file check: the blank door still loads.
        assert app._conversations.store.load(running_id).conversation_id == running_id
    finally:
        release.set()
        worker.join(timeout=5)
    assert not errors
    stored = ConversationManager(tmp_path / "sessions").load(running_id)
    assert [(item.role, item.content) for item in stored.messages] == [
        ("user", "hi"),
        ("assistant", "late"),
    ]
    app.shutdown()


def test_turn_response_does_not_claim_to_be_running(tmp_path: Path) -> None:
    """The turn's own payload must not tell the client to wait for itself."""
    app = _chat(tmp_path, FakeLLM(ChatReply(content="pong")))
    created = app.create()
    try:
        turned = app.turn(created["conversation_id"], "alo")
        assert turned["running"] is False
        assert "started_at" not in turned
    finally:
        app.shutdown()


def test_corrupt_config_file_falls_back_to_passed_cfg(tmp_path: Path) -> None:
    """A corrupt on-disk config.json must not break init when a good cfg was passed."""
    from test_serve_chat import FakeLLM

    from thyca.app.chat_app import ChatApp
    from thyca.configs import default_config
    from thyca.llms.llm_base import ChatReply

    (tmp_path / "config.json").write_text("{not valid json", encoding="utf-8")
    cfg = default_config()
    app = ChatApp(tmp_path, cfg, connect=FakeLLM(ChatReply(content="pong")))
    try:
        assert app.default_model() == cfg.defaultModel
    finally:
        app.shutdown()


def test_cancel_past_wait_reports_turn_in_flight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import thyca.app.chat_app as chat_mod
    from thyca.app.chat_app import TurnInFlight

    monkeypatch.setattr(chat_mod, "_CANCEL_WAIT_S", 0.05)
    release = threading.Event()

    class BlockingLLM:
        async def chat(self, messages, tools=None):
            await asyncio.to_thread(release.wait, 10)
            return ChatReply(content="late")

    app = _chat(tmp_path, BlockingLLM())
    try:
        created = app.create()
        errors: list[BaseException] = []

        def run() -> None:
            try:
                app.turn(created["conversation_id"], "go")
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        deadline = time.monotonic() + 5
        while created["conversation_id"] not in app.running_conversations():
            assert time.monotonic() < deadline, "turn never started"
            time.sleep(0.01)
        with pytest.raises(TurnInFlight):
            app.cancel(created["conversation_id"])
        # The cancel was still requested: the worker ends cancelled (or
        # finishes once released) — either way the hub is released.
        release.set()
        worker.join(timeout=10)
        assert not worker.is_alive()
        from thyca.app.chat_app import TurnCancelled

        assert all(isinstance(exc, TurnCancelled) for exc in errors)
        assert created["conversation_id"] not in app.running_conversations()
    finally:
        release.set()
        app.shutdown()


def test_cancel_turn_in_flight_maps_to_409() -> None:
    from thyca.app.chat_app import TurnInFlight
    from thyca.serve.conversations_api import dm_turn_cancel

    class Handler:
        def __init__(self) -> None:
            self.sent: tuple[int, dict] | None = None

        def _read_body(self) -> bytes:
            return b""

        def _json(self, status: int, payload: dict) -> None:
            self.sent = (status, payload)

    class BusyApp:
        def bot_dm(self, bot_id: str) -> dict:
            return {"id": bot_id}

        def cancel(self, conversation_id: str) -> None:
            raise TurnInFlight("turn still in flight")

    handler = Handler()
    dm_turn_cancel(handler, BusyApp(), "thyca")
    assert handler.sent == (409, {"error": "turn in flight"})


def test_compaction_usage_mutate_after_does_not_leak(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from thyca.agents.meta import compaction_meta

    usage = {"prompt_tokens": 10, "completion_tokens": 5}
    meta = compaction_meta(
        SimpleNamespace(usage=usage, model="m-test"), 7, "m-test", None
    )
    usage["prompt_tokens"] = 999
    usage["injected"] = True
    assert meta["usage"] == {"prompt_tokens": 10, "completion_tokens": 5}


def test_turn_refreshes_live_gateway_soft_timeout(tmp_path: Path) -> None:
    """TASK-034: a saved softTimeoutS applies to the next turn, no restart."""
    from dataclasses import replace

    from thyca.configs import load, save

    app = _chat(tmp_path, FakeLLM(ChatReply(content="pong")))
    created = app.create()
    try:
        assert app._gateway._soft_timeout_s == 60  # default at construction
        cfg = load(tmp_path / "config.json")
        save(replace(cfg, limits=replace(cfg.limits, softTimeoutS=5)), tmp_path / "config.json")
        app.turn(created["conversation_id"], "alo")
        assert app._gateway._soft_timeout_s == 5
    finally:
        app.shutdown()


# Moved from test_b4_unification.py / test_b4_p2.py (B4 batch).
import pytest
from pathlib import Path

def test_x2_turn_options_single_shape_check() -> None:
    from thyca.app.chat_app import InvalidTurnOption
    from thyca.app.turn_options import MODEL_MAX, validate_turn_options

    assert MODEL_MAX == 200
    assert validate_turn_options({"model": "m", "effort": "e", "retry": True}) == (
        "m",
        "e",
        True,
    )
    with pytest.raises(InvalidTurnOption):
        validate_turn_options({"model": "a\nb"})


def test_x20_single_registry_lifecycle() -> None:
    import asyncio as aio

    from thyca.app.loop_turns import _LoopTurns
    from thyca.serve.turn_state import TurnState
    from thyca.conversations.runtime import ConversationBusy, ConversationError

    loop = aio.new_event_loop()
    try:
        turns = TurnState()
        view = _LoopTurns(loop, turns)
        hub = turns.claim("s")
        assert turns.hub("s") is hub
        assert turns.job("s") is None
        job = view.begin("s")
        assert turns.job("s") is job
        with pytest.raises(ConversationBusy):
            turns.claim("s")
        assert view.request_cancel("missing") is False
        assert view.request_cancel("s") is True
        other = view.begin("s")  # re-begin overwrites, like before
        assert turns.job("s") is other
        view.end("s", job)  # mismatch: pinned job stays
        assert turns.job("s") is other
        view.end("s", other)
        assert turns.job("s") is None
        turns.release("s")
        assert turns.hub("s") is None
        with pytest.raises(ConversationError):
            view.begin("never-claimed")
    finally:
        loop.close()


def test_x21_shared_loop_wiring(tmp_path: Path) -> None:
    from thyca.agents.act import Act
    from thyca.agents.assemble import Assemble
    from thyca.agents.loop import AgentLoop
    from thyca.agents.observe import Observe
    from thyca.agents.think import Think
    from thyca.app.toolchain import build_agent_loop
    from thyca.conversations.runtime import ConversationManager

    conversations = _canonical_manager(tmp_path)

    async def fake_chat(messages, tools=None):
        from thyca.llms.llm_base import ChatReply

        return ChatReply(content="x")

    loop = build_agent_loop(
        conversations=conversations,
        connect=fake_chat,  # type: ignore[arg-type]
        act=Act(dispatcher=None),  # type: ignore[arg-type]
        tools=[],
        loop_max=3,
        model="m",
        pricing=None,
    )
    assert isinstance(loop, AgentLoop)
    assert isinstance(loop._assemble, Assemble)
    assert isinstance(loop._think, Think)
    assert isinstance(loop._observe, Observe)




# Moved from tests/test_b2_contracts.py (B2 batch).
from thyca.app.chat_app import InvalidTurnOption, InvalidTurnText
from thyca.app.turn_options import (
    SYSTEM_TEXT_MAX,
    _clean_turn_text,
    overlay_turn_cfg,
)
from thyca.configs import Config, ModelCfg

def test_f5_turn_effort_override_is_model_aware() -> None:
    """Fails pre-fix: per-turn junk effort sailed through overlay."""
    cfg = Config(
        models={"m": ModelCfg(reasoningEfforts=("minimal", "medium"))},
        defaultModel="m",
    )
    assert overlay_turn_cfg(cfg, "m", "minimal").defaultModel == "m"
    with pytest.raises(InvalidTurnOption, match="allows minimal/medium"):
        overlay_turn_cfg(cfg, "m", "ultra")
    plain = default_config()
    with pytest.raises(InvalidTurnOption, match="use low/medium/high/xhigh/max"):
        overlay_turn_cfg(plain, None, "ultra")
    assert overlay_turn_cfg(plain, None, "low").defaultModel == plain.defaultModel


def test_f10_clean_turn_text_reasons() -> None:
    """Fails pre-fix: plain ValueError, and 'text must be a string' wording."""
    with pytest.raises(InvalidTurnText, match="^invalid$"):
        _clean_turn_text(5, retry=False)
    with pytest.raises(InvalidTurnText, match="^empty$"):
        _clean_turn_text("   ", retry=False)
    with pytest.raises(InvalidTurnText, match="^too long$"):
        _clean_turn_text("x" * 4001, retry=False)
    assert _clean_turn_text("  hi  ", retry=False) == "hi"
    assert _clean_turn_text(123, retry=True) == ""


def test_system_turn_text_uses_larger_cap() -> None:
    # System prompts (curation, profile aggregation) carry injected data
    # beyond the user-message cap; the system cap still bounds them.
    with pytest.raises(InvalidTurnText, match="^too long$"):
        _clean_turn_text("x" * (SYSTEM_TEXT_MAX + 1), retry=False,
                         text_max=SYSTEM_TEXT_MAX)
    assert _clean_turn_text("x" * 5000, retry=False,
                            text_max=SYSTEM_TEXT_MAX) == "x" * 5000
    with pytest.raises(InvalidTurnText, match="^too long$"):
        _clean_turn_text("x" * 5000, retry=False)


class _TavilySession:
    """Fake MCP child exposing one `search` tool."""

    def __init__(self) -> None:
        self.initialized = False

    async def initialize(self) -> object:
        self.initialized = True
        return object()

    async def list_tools(self) -> ListToolsResult:
        return ListToolsResult(
            tools=[Tool(name="search", inputSchema={"type": "object", "properties": {}})]
        )

    async def call_tool(self, name, arguments=None, read_timeout_seconds=None, **kwargs):
        return CallToolResult(
            content=[TextContent(type="text", text="ok")], isError=False
        )


def _tavily_factory(started: list):
    def make(name: str, cfg: McpServerCfg) -> MCPProcess:
        started.append((name, dict(cfg.env)))
        return MCPProcess(
            name, cfg.command, list(cfg.args), dict(cfg.env), session=_TavilySession()
        )

    return make


def _chat_with_mcp(tmp_path: Path, connect, factory) -> ChatApp:
    save(seeded_config(), tmp_path / "config.json")
    return ChatApp(
        tmp_path, load(tmp_path / "config.json"), connect=connect, mcp_factory=factory
    )


def _saw_tool(llm, name: str) -> bool:
    for tools in llm.tools:
        if tools and any(item["function"]["name"] == name for item in tools):
            return True
    return False


def _save_mcp(tmp_path: Path, servers: dict) -> None:
    cfg = load(tmp_path / "config.json")
    cfg.mcpServers.clear()
    cfg.mcpServers.update(servers)
    save(cfg, tmp_path / "config.json")


def test_turn_hot_reloads_added_mcp_server(tmp_path: Path) -> None:
    started: list = []
    llm = FakeLLM(ChatReply(content="pong"))
    app = _chat_with_mcp(tmp_path, llm, _tavily_factory(started))
    try:
        created = app.create()
        app.turn(created["conversation_id"], "hi")
        assert not _saw_tool(llm, "tavily__search")
        _save_mcp(tmp_path, {"tavily": McpServerCfg(command="tavily")})
        app.turn(created["conversation_id"], "search now")
        assert _saw_tool(llm, "tavily__search")
        assert started == [("tavily", {})]
    finally:
        app.shutdown()


def test_turn_unloads_removed_mcp_server(tmp_path: Path) -> None:
    started: list = []
    llm = FakeLLM(ChatReply(content="pong"))
    save(seeded_config(), tmp_path / "config.json")
    _save_mcp(tmp_path, {"tavily": McpServerCfg(command="tavily")})
    app = ChatApp(
        tmp_path,
        load(tmp_path / "config.json"),
        connect=llm,
        mcp_factory=_tavily_factory(started),
    )
    try:
        created = app.create()
        app.turn(created["conversation_id"], "hi")
        assert _saw_tool(llm, "tavily__search")
        seen_before = len(llm.tools)
        _save_mcp(tmp_path, {})
        app.turn(created["conversation_id"], "again")
        for tools in llm.tools[seen_before:]:
            if tools:
                assert all(t["function"]["name"] != "tavily__search" for t in tools)
    finally:
        app.shutdown()


def test_turn_respawns_mcp_server_on_env_change(tmp_path: Path) -> None:
    started: list = []
    llm = FakeLLM(ChatReply(content="pong"))
    app = _chat_with_mcp(tmp_path, llm, _tavily_factory(started))
    try:
        created = app.create()
        _save_mcp(tmp_path, {"t": McpServerCfg(command="t", env={"K": "1"})})
        app.turn(created["conversation_id"], "hi")
        assert _saw_tool(llm, "t__search")
        _save_mcp(tmp_path, {"t": McpServerCfg(command="t", env={"K": "2"})})
        app.turn(created["conversation_id"], "again")
        assert started == [("t", {"K": "1"}), ("t", {"K": "2"})]
        assert _saw_tool(llm, "t__search")
    finally:
        app.shutdown()


def test_turn_completes_when_mcp_spawn_fails(tmp_path: Path) -> None:
    class BoomSession(_TavilySession):
        async def initialize(self) -> object:
            raise RuntimeError("child exited")

    def boom_factory(name: str, cfg: McpServerCfg) -> MCPProcess:
        return MCPProcess(
            name, cfg.command, list(cfg.args), dict(cfg.env), session=BoomSession()
        )

    llm = FakeLLM(ChatReply(content="pong"))
    app = _chat_with_mcp(tmp_path, llm, boom_factory)
    try:
        created = app.create()
        _save_mcp(tmp_path, {"bad": McpServerCfg(command="bad")})
        turned = app.turn(created["conversation_id"], "hi")
        assert turned["reply"] == "pong"
        assert not _saw_tool(llm, "bad__search")
        system = next(
            m.content for msgs in llm.requests for m in msgs if m.role == "system"
        )
        assert "<mcp_status>" in system
        assert "- bad: RuntimeError: child exited" in system
    finally:
        app.shutdown()


def test_turn_mcp_status_clears_after_recovery(tmp_path: Path) -> None:
    class ToggleSession(_TavilySession):
        broken = True

        async def initialize(self) -> object:
            if ToggleSession.broken:
                raise RuntimeError("connect failed")
            return {}

    def toggle_factory(name: str, cfg: McpServerCfg) -> MCPProcess:
        return MCPProcess(
            name, cfg.command, list(cfg.args), dict(cfg.env), session=ToggleSession()
        )

    llm = ScriptedLLM([ChatReply(content="first"), ChatReply(content="second")])
    app = _chat_with_mcp(tmp_path, llm, toggle_factory)
    try:
        created = app.create()
        _save_mcp(tmp_path, {"tavily": McpServerCfg(command="tavily")})
        assert app.turn(created["conversation_id"], "one")["reply"] == "first"
        first_system = next(
            m.content for m in llm.requests[0] if m.role == "system"
        )
        assert "<mcp_status>" in first_system
        assert "- tavily: RuntimeError: connect failed" in first_system

        ToggleSession.broken = False
        assert app.turn(created["conversation_id"], "two")["reply"] == "second"
        second_system = next(
            m.content for m in llm.requests[1] if m.role == "system"
        )
        assert "<mcp_status>" not in second_system
        assert _saw_tool(llm, "tavily__search")
    finally:
        app.shutdown()


def test_chat_app_starts_and_warns_when_mcp_times_out(tmp_path: Path) -> None:
    from io import StringIO

    class HungSession(_TavilySession):
        async def initialize(self) -> object:
            await asyncio.Future()
            return {}

    def hung_factory(name: str, cfg: McpServerCfg) -> MCPProcess:
        return MCPProcess(
            name,
            cfg.command,
            list(cfg.args),
            dict(cfg.env),
            session=HungSession(),
            start_timeout=timedelta(seconds=0.05),
        )

    save(
        replace(seeded_config(), mcpServers={"hung": McpServerCfg(command="hung")}),
        tmp_path / "config.json",
    )
    stderr = StringIO()
    llm = FakeLLM(ChatReply(content="still up"))
    app = ChatApp(
        tmp_path,
        load(tmp_path / "config.json"),
        connect=llm,
        mcp_factory=hung_factory,
        stderr=stderr,
    )
    try:
        # Boot only schedules; settle + warning happen on the first turn.
        assert stderr.getvalue() == ""
        created = app.create()
        turned = app.turn(created["conversation_id"], "hi")
        assert turned["reply"] == "still up"
        assert stderr.getvalue().splitlines() == [
            "warning: mcp hung: TimeoutError: startup timed out after 0.05s"
        ]
        system = next(
            m.content for msgs in llm.requests for m in msgs if m.role == "system"
        )
        assert "<mcp_status>" in system
        assert "- hung: TimeoutError: startup timed out after 0.05s" in system
    finally:
        app.shutdown()


def test_turn_injects_conversation_scoped_today(tmp_path: Path) -> None:
    llm = FakeLLM(ChatReply(content="pong"))
    app = _chat(tmp_path, llm)
    try:
        created = app.create()
        sid = created["conversation_id"]
        (day_file,) = list((tmp_path / "memory").glob("*.md"))
        day = day_file.stem
        day_file.write_text(
            f"# {day}\n"
            f"## 10:00 — own-note <!-- thyca {{\"id\":\"aaaa1111\",\"imp\":3,\"chat\":\"{sid}\"}} -->\n"
            "- body OWN-UNIQUE detail\n"
            "## 11:00 — foreign-note <!-- thyca {\"id\":\"bbbb2222\",\"imp\":3,\"chat\":\"OTHER-SID\"} -->\n"
            "- body FOREIGN-UNIQUE detail\n",
            encoding="utf-8",
        )
        app.turn(sid, "hi")
        system = next(
            m.content for msgs in llm.requests for m in msgs if m.role == "system"
        )
        assert "OWN-UNIQUE" in system
        assert "FOREIGN-UNIQUE" not in system
        assert "foreign-note" in system  # title survives in the index
        assert f"{day}#bbbb2222" in system  # pullable id
        assert "<today_elsewhere>" in system
    finally:
        app.shutdown()


def test_group_with_deleted_bot_stays_readable(tmp_path: Path) -> None:
    from thyca.bots.card import BotCard
    from thyca.bots.registry import RegistryError

    llm = FakeLLM(ChatReply(content="pong"))
    app = _chat(tmp_path, llm)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder", description="Codes.", persona="You code."))
        app._bots.create(BotCard(bot_id="helper", name="helper", role="Helper", description="Helps.", persona="You help."))
        group = app.create(bots=["coder", "helper"])
        gid = group["conversation_id"]
        app.turn(gid, "ping")
        # Simulate an old orphan left in the database.
        app._bots.delete("coder")
        payload = app.get_payload(gid)
        assert payload["kind"] == "group" and payload["bots"] == ["coder", "helper"]
        assert "title" not in payload
        result = app.turn(gid, "hello")
        assert result["reply"] == "pong"
        # Removing the deleted bot works; unknown ids still fail.
        assert app.set_room_bots(gid, "coder", "remove") == ["helper"]
        try:
            app.set_room_bots(gid, "ghost", "remove")
        except RegistryError as exc:
            assert "not in room" in str(exc)
        else:
            raise AssertionError("expected RegistryError for unknown remove")
        try:
            app.set_room_bots(gid, "ghost", "add")
        except RegistryError:
            pass
        else:
            raise AssertionError("expected RegistryError for unknown add")
        # Compact skips the deleted member instead of failing.
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder", description="Codes.", persona="You code."))
        g2 = app.create(bots=["coder", "helper"])
        app.turn(g2["conversation_id"], "ping")
        app._bots.delete("coder")
        compacted = app.compact_conversation(g2["conversation_id"])
        assert compacted["compaction"]["per_bot"]["coder"]["skipped"] == "bot_unavailable"
    finally:
        app.shutdown()


def test_group_with_all_bots_deleted_turn_is_clear(tmp_path: Path) -> None:
    from thyca.bots.card import BotCard
    from thyca.conversations.runtime import NoLiveResponder

    llm = FakeLLM(ChatReply(content="pong"))
    app = _chat(tmp_path, llm)
    try:
        app._bots.create(BotCard(bot_id="solo1", name="solo1", role="R", description="D.", persona="p"))
        app._bots.create(BotCard(bot_id="solo2", name="solo2", role="R", description="D.", persona="p"))
        group = app.create(bots=["solo1", "solo2"])
        gid = group["conversation_id"]
        app.turn(gid, "ping")
        app._bots.delete("solo1")
        app._bots.delete("solo2")
        payload = app.get_payload(gid)
        assert payload["kind"] == "group" and payload["bots"] == ["solo1", "solo2"]
        try:
            app.turn(gid, "hello")
        except NoLiveResponder:
            pass
        else:
            raise AssertionError("expected NoLiveResponder for all-dead group")
        assert "title" not in payload
        app.delete_conversation(gid)
    finally:
        app.shutdown()
