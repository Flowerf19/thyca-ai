"""Post-review fixes: self-escalation, A2A delivery, cost, ownership.

Security regressions are mandatory coverage here: a child bot must gain
nothing by trying to edit its own (or any) BOT.md, the live config, or
the team skill store, and must not enumerate other bots' executions.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from thyca.agent.meta import tool_message
from thyca.app.chat_app import ChatApp
from thyca.bots.admin_tools import AdminDeps, register_admin_tools
from thyca.bots.bus import Bus
from thyca.bots.card import BotCard, BotMemoryPolicy
from thyca.bots.context import BotTurnContext, bind_bot_turn_context, reset_bot_turn_context
from thyca.bots.policy import BotPolicy, bind_allowed_tools, reset_allowed_tools
from thyca.bots.registry import BotRegistry
from thyca.bots.runtime import BotRuntime
from thyca.config import default_config, load, save
from thyca.core.protocol import ToolCall, ToolResult
from thyca.llm.llm_base import ChatReply
from thyca.memory.candidates import CandidateQueue
from thyca.memory.facade import MemoryFacade
from thyca.memory.identity import bind_bot_id, reset_bot_id
from thyca.serve.memory import candidate_decide
from thyca.tools.builtin import register_file_tools
from thyca.tools.gateway import ToolGateway
from thyca.tools.gateway.policy import PolicyDenied
from thyca.tools.path_guard import PathDenied, PathGuard
from thyca.tools.registry import ToolRegistry
from thyca.tools.task_store import TaskStore


def _as(bot_id: str | None):
    return bind_bot_id(bot_id)


# --- CRITICAL: card self-escalation -------------------------------------

def test_child_bot_cannot_read_write_edit_team_control_files(tmp_path: Path) -> None:
    (tmp_path / "bots" / "coder").mkdir(parents=True)
    card_file = tmp_path / "bots" / "coder" / "BOT.md"
    card_file.write_text("original card", encoding="utf-8")
    (tmp_path / "config.json").write_text('{"secret": 1}', encoding="utf-8")
    (tmp_path / "skills-team" / "x").mkdir(parents=True)
    team_skill = tmp_path / "skills-team" / "x" / "SKILL.md"
    team_skill.write_text("team skill", encoding="utf-8")

    registry = ToolRegistry()
    register_file_tools(registry, PathGuard(tmp_path), None)

    async def attempt() -> dict[str, str]:
        out = {}
        for name, args in (
            ("write", {"path": str(card_file), "content": "escalated"}),
            ("edit", {"path": str(card_file),
                      "edits": [{"oldText": "original", "newText": "pwned"}]}),
            ("write", {"path": str(tmp_path / "config.json"), "content": "{}"}),
            ("write", {"path": str(team_skill), "content": "pwned"}),
            ("read", {"path": str(tmp_path / "config.json")}),
            ("read", {"path": str(card_file)}),
        ):
            try:
                await registry.get(name).handler(args)
                out[name + args["path"]] = "ALLOWED"
            except PathDenied:
                out[name + args["path"]] = "denied"
        return out

    token = _as("coder")
    try:
        results = asyncio.run(attempt())
    finally:
        reset_bot_id(token)
    assert all(value == "denied" for value in results.values()), results
    # Gains nothing by trying: every target is byte-identical.
    assert card_file.read_text(encoding="utf-8") == "original card"
    assert (tmp_path / "config.json").read_text(encoding="utf-8") == '{"secret": 1}'
    assert team_skill.read_text(encoding="utf-8") == "team skill"

    # The user (no bot) and Thyca keep access.
    token = _as("thyca")
    try:
        guard = PathGuard(tmp_path)
        assert guard.deny_write(str(tmp_path / "config.json")) is not None
        assert guard.deny_read(str(card_file)) is not None
    finally:
        reset_bot_id(token)


def test_gateway_policy_denies_child_paths_bash_and_unlisted_tools(tmp_path: Path) -> None:
    policy = BotPolicy()
    ctx_token = bind_bot_turn_context(
        BotTurnContext(sender_id="coder", room_id="r", thyca_dir=tmp_path)
    )
    token = _as("coder")
    try:
        with pytest.raises(PolicyDenied):
            policy.check(ToolCall(id="1", name="write", arguments={
                "path": str(tmp_path / "bots" / "coder" / "BOT.md"), "content": "x"}))
        with pytest.raises(PolicyDenied):
            policy.check(ToolCall(id="2", name="bash", arguments={
                "command": "cat ~/.thyca/config.json"}))
        with pytest.raises(PolicyDenied):
            policy.check(ToolCall(id="3", name="bash", arguments={
                "command": "sed -i s/a/b/ BOT.md"}))
        policy.check(ToolCall(id="4", name="read", arguments={"path": "/tmp/ok.txt"}))
    finally:
        reset_bot_id(token)
        reset_bot_turn_context(ctx_token)
    # Fail-closed allowlist at the gateway: empty set denies everything.
    token = _as("coder")
    allowed = bind_allowed_tools(frozenset())
    try:
        with pytest.raises(PolicyDenied):
            policy.check(ToolCall(id="5", name="read", arguments={"path": "/tmp/ok.txt"}))
    finally:
        reset_allowed_tools(allowed)
        reset_bot_id(token)


def test_team_skill_seeds_ship_and_child_index_is_private_plus_team(tmp_path: Path) -> None:
    registry = BotRegistry(tmp_path)
    registry.ensure_thyca()
    assert (tmp_path / "skills-team" / "create-skill" / "SKILL.md").is_file()
    assert (tmp_path / "skills-team" / "create-mcp-tool" / "SKILL.md").is_file()
    registry.create(BotCard(bot_id="coder", name="coder", role="C",
                            description="D", persona="P"))
    index = registry.skill_index_text("coder")
    assert "create-skill" in index
    assert "create-mcp-tool" in index


# --- IMPORTANT: cross-bot tracked executions ----------------------------

def test_gateway_executions_are_owner_scoped_for_bots() -> None:
    async def scenario() -> tuple[str, str]:
        registry = ToolRegistry()
        gateway = ToolGateway(registry, TaskStore(), soft_timeout_s=0)

        async def slow(args: dict) -> str:
            await asyncio.sleep(5)
            return "done"

        from thyca.tools.registry import ToolSpec

        registry.register(ToolSpec(
            name="slow", description="slow",
            parameters={"type": "object", "properties": {}},
            handler=slow, parallel_safe=True,
        ))
        token = _as("coder")
        try:
            started = await gateway.submit(ToolCall(id="t1", name="slow", arguments={}))
        finally:
            reset_bot_id(token)
        exec_id = started.exec_ref
        assert exec_id
        # Another bot: read/kill read as unknown and enumerate nothing.
        token = _as("researcher")
        try:
            with pytest.raises(ValueError) as read_err:
                await gateway.read(exec_id)
            with pytest.raises(ValueError) as kill_err:
                await gateway.kill(exec_id)
            with pytest.raises(ValueError) as unknown_err:
                await gateway.read("exec999")
        finally:
            reset_bot_id(token)
        for err in (read_err, kill_err, unknown_err):
            assert "(known: none)" in str(err.value)
            assert exec_id not in str(err.value).split("(known:")[-1]
        # The owner can still read and kill it.
        token = _as("coder")
        try:
            running = await gateway.read(exec_id)
            assert "running" in running.content
            assert await gateway.kill(exec_id) == f"killed {exec_id}"
        finally:
            reset_bot_id(token)
        await gateway.shutdown()
        return exec_id, "ok"

    asyncio.run(scenario())


# --- IMPORTANT: memory_propose_team ownership ---------------------------

def test_memory_propose_team_only_for_own_entries(tmp_path: Path) -> None:
    from thyca.memory.archived import ArchiveError
    from thyca.tools.memory_tools import register_memory_tools

    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    token = _as("coder")
    try:
        own = facade.remember("mine", "coder fact")
    finally:
        reset_bot_id(token)
    token = _as("researcher")
    try:
        other = facade.remember("theirs", "researcher fact")
    finally:
        reset_bot_id(token)
    team = facade.remember("shared", "team fact", bots=["team"])

    registry = ToolRegistry()
    register_memory_tools(registry, facade)
    spec = registry.get("memory_propose_team")
    assert spec is not None
    token = _as("coder")
    try:
        with pytest.raises(ArchiveError):
            asyncio.run(spec.handler({"session_id": other}))
        with pytest.raises(ArchiveError):
            asyncio.run(spec.handler({"session_id": team}))
        out = json.loads(asyncio.run(spec.handler({"session_id": own})))
    finally:
        reset_bot_id(token)
    assert out["status"] == "pending"
    pending = CandidateQueue(tmp_path).list_pending()
    assert [cand.session_id for cand in pending] == [own]


# --- MINOR: tool_message, candidate ordering, paused, bus drain ---------

def test_tool_message_error_keeps_bot_id() -> None:
    msg = tool_message(
        ToolResult(tool_call_id="c1", name="read", content="boom", is_error=True),
        bot_id="coder",
    )
    assert msg.meta == {"bot_id": "coder", "is_error": True}


def test_candidate_decide_first_and_reopens_on_promote_failure(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    queue = CandidateQueue(tmp_path)
    bad = queue.propose("coder", "2026-08-01#deadbeef")
    status, _body = candidate_decide(facade, bad.id, True, "user")
    assert status == 404
    # Promotion failed → the candidate is pending again, not "approved".
    assert [cand.id for cand in queue.list_pending()] == [bad.id]

    sid = facade.remember("gift", "egg coffee")
    good = queue.propose("coder", sid)
    status, body = candidate_decide(facade, good.id, True, "thyca")
    assert status == 200 and body["status"] == "approved"
    decided = [cand for cand in queue.list_all() if cand.id == good.id][0]
    assert decided.decided_by == "thyca"


def test_bus_drain_log_is_per_room() -> None:
    bus = Bus()
    bus.register("a")
    bus.send(sender="x", recipient="a", kind="message", session_id="room1", payload="1")
    bus.send(sender="x", recipient="a", kind="message", session_id="room2", payload="2")
    taken = bus.drain_log("room1")
    assert [event.envelope.payload for event in taken] == ["1"]
    assert [event.envelope.payload for event in bus.drain_log("room2")] == ["2"]
    assert bus.drain_log("room1") == []


# --- ChatApp-level: delivery, cost, memory.read, paused, skills ---------

def _chat(tmp_path: Path, connect=None) -> ChatApp:
    save(default_config(), tmp_path / "config.json")
    return ChatApp(tmp_path, load(tmp_path / "config.json"), connect=connect)


class _ChainLLM:
    """Thyca delegates to coder; coder asks helper via bot_send; all reply."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def chat(self, messages, tools=None):
        text = "\n".join(str(m.content or "") for m in messages)
        self.prompts.append(text)
        tool_text = "\n".join(
            str(m.content or "") for m in messages if m.role == "tool"
        )
        if "PERSONA-HELPER" in text:
            return ChatReply(content="helper done", model="m-helper",
                             usage={"prompt_tokens": 10, "completion_tokens": 2,
                                    "total_tokens": 12})
        if "PERSONA-CODER" in text:
            if "helper done" in tool_text:
                return ChatReply(content="coder done", model="m-coder",
                                 usage={"prompt_tokens": 20, "completion_tokens": 3,
                                        "total_tokens": 23})
            return ChatReply(content=None, model="m-coder", tool_calls=[
                ToolCall(id="c-send", name="bot_send",
                         arguments={"bot_id": "helper", "message": "help me"}),
            ])
        # Thyca (system persona).
        if "coder done" in tool_text:
            return ChatReply(content="thyca final", model="m")
        return ChatReply(content=None, model="m", tool_calls=[
            ToolCall(id="t-delegate", name="bot_delegate",
                     arguments={"bot_id": "coder", "task": "do the work"}),
        ])


def test_a2a_delivery_multihop_records_attributed_room_rows(tmp_path: Path) -> None:
    llm = _ChainLLM()
    app = _chat(tmp_path, connect=llm)
    try:
        app._bots.create(BotCard(
            bot_id="coder", name="coder", role="Coder", description="Codes.",
            persona="PERSONA-CODER", tools=("bot_send",),
        ))
        app._bots.create(BotCard(
            bot_id="helper", name="helper", role="Helper", description="Helps.",
            persona="PERSONA-HELPER",
        ))
        created = app.create(bots=["thyca", "coder", "helper"])
        detail = app.turn(created["id"], "please coordinate")
        assert detail["reply"] == "thyca final"

        rows = app.get_payload(created["id"])["messages"]
        by_bot_kind = {}
        for row in rows:
            meta = row.get("meta") or {}
            if meta.get("bot_id"):
                by_bot_kind.setdefault((meta["bot_id"], meta.get("kind")), row)
        # Both delivered bots actually ran and landed attributed results.
        coder_result = by_bot_kind[("coder", "result")]
        helper_result = by_bot_kind[("helper", "result")]
        assert coder_result["content"] == "coder done"
        assert helper_result["content"] == "helper done"
        # Cost attribution: the internal turns' meta rides on room rows.
        assert coder_result["meta"]["model"] == "m-coder"
        assert coder_result["meta"]["usage"]["total_tokens"] == 23
        assert isinstance(coder_result["meta"]["latency_ms"], int)
        assert helper_result["meta"]["usage"]["total_tokens"] == 12
        # FE delegation tree: every bot row has its own span; delivered
        # results parent to their envelope's span.
        for row in (coder_result, helper_result):
            assert row["meta"]["span_id"].startswith("sp_")
            assert row["meta"]["parent_span_id"].startswith("sp_")
        send_row = by_bot_kind[("coder", "message")]
        assert helper_result["meta"]["parent_span_id"] == send_row["meta"]["span_id"]
        assert coder_result["meta"]["trace_id"] == helper_result["meta"]["trace_id"]
    finally:
        app.shutdown()


def test_bot_send_sender_comes_from_context_never_args(tmp_path: Path) -> None:
    registry = BotRegistry(tmp_path)
    registry.ensure_thyca()
    registry.create(BotCard(bot_id="helper", name="helper", role="H",
                            description="D", persona="P"))
    bus = Bus()
    runtime = BotRuntime(tmp_path, registry, bus)
    tools = ToolRegistry()
    register_admin_tools(tools, AdminDeps(registry, runtime, lambda: None))
    spec = tools.get("bot_send")
    assert spec is not None
    ctx = BotTurnContext(sender_id="coder", room_id="room1")
    token = bind_bot_turn_context(ctx)
    bot_token = _as("coder")
    try:
        out = json.loads(asyncio.run(spec.handler(
            {"bot_id": "helper", "message": "hi", "sender": "thyca",
             "from": "thyca"}
        )))
    finally:
        reset_bot_id(bot_token)
        reset_bot_turn_context(token)
    assert out["ok"] is True
    envelope = bus.log()[0].envelope
    assert envelope.sender == "coder"


def test_memory_read_false_hot_prompt_has_no_memory(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    facade.remember("SECRET-TOPIC-XYZ", "secret body", bots=["team"])

    class CaptureLLM:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        async def chat(self, messages, tools=None):
            self.prompts.append("\n".join(str(m.content or "") for m in messages))
            return ChatReply(content="ok", model="m")

    llm = CaptureLLM()
    app = _chat(tmp_path, connect=llm)
    try:
        app._bots.create(BotCard(
            bot_id="coder", name="coder", role="Coder", description="Codes.",
            persona="PERSONA-CODER",
            memory=BotMemoryPolicy(read=False, write=True, propose_team=True),
        ))
        created = app.create(bots=["coder"])
        app.turn(created["id"], "hi")
        assert llm.prompts
        assert all("SECRET-TOPIC-XYZ" not in prompt for prompt in llm.prompts)
        assert all("secret body" not in prompt for prompt in llm.prompts)
    finally:
        app.shutdown()


def test_child_prompt_skill_index_excludes_thyca_private(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "thyca-only-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: thyca-only-skill\ndescription: Thyca private skill.\n---\nbody\n",
        encoding="utf-8",
    )

    class CaptureLLM:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        async def chat(self, messages, tools=None):
            self.prompts.append("\n".join(str(m.content or "") for m in messages))
            return ChatReply(content="ok", model="m")

    llm = CaptureLLM()
    app = _chat(tmp_path, connect=llm)
    try:
        app._bots.create(BotCard(
            bot_id="coder", name="coder", role="Coder", description="Codes.",
            persona="PERSONA-CODER",
        ))
        created = app.create(bots=["coder"])
        app.turn(created["id"], "hi")
        prompt = llm.prompts[0]
        assert "create-skill" in prompt  # team seed is there
        assert "thyca-only-skill" not in prompt  # Thyca private never leaks
    finally:
        app.shutdown()


def test_paused_bot_removable_and_responder_skips_paused(tmp_path: Path) -> None:
    app = _chat(tmp_path)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="C",
                                 description="D", persona="P"))
        created = app.create()
        assert app.set_room_bots(created["id"], "coder", "add") == ["thyca", "coder"]
        app._bots.set_paused("coder", True)
        # Removable while paused; a paused mention is skipped for Thyca.
        assert app.set_room_bots(created["id"], "coder", "remove") == ["thyca"]
        resolve = ChatApp._resolve_responder
        assert resolve(["thyca", "coder"], "hi @coder", paused={"coder"}) == "thyca"
        assert resolve(["coder"], "hi", paused={"coder"}) == "coder"  # caller errors
    finally:
        app.shutdown()


def test_serve_scheduler_timer_ticks(tmp_path: Path) -> None:
    import time as _time

    from thyca.serve.server import start_scheduler

    class _Bots:
        def list(self):
            return []

    class _App:
        def __init__(self) -> None:
            self.ticks = 0
            self._bots = _Bots()

        def sweep_idle_sessions(self, idle_s: int = 900) -> dict:
            self.ticks += 1
            return {}

    fake = _App()
    _thread, stop = start_scheduler(fake, interval_s=0.02)  # type: ignore[arg-type]
    try:
        _time.sleep(0.09)
    finally:
        stop.set()
    assert fake.ticks >= 1
