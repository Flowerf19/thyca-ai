"""CBE-1/CBE-2/CBE-4: per-bot budgets, slim prompt, flush + re-inject."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from thyca.agent.loop import AgentLoop
from thyca.app.chat_app import ChatApp
from thyca.bots.card import BotCard
from thyca.config import default_config, load, save
from thyca.core.protocol import Message, ToolCall
from thyca.llm.llm_base import ChatReply
from thyca.memory.active import ActiveSnapshot
from thyca.sessions import SessionManager
from thyca.sessions.compaction import SessionCompactor


def _chat(tmp_path: Path, connect, context_tokens: int | None = None) -> ChatApp:
    cfg = default_config()
    if context_tokens is not None:
        cfg = replace(cfg, limits=replace(cfg.limits, contextTokens=context_tokens))
    save(cfg, tmp_path / "config.json")
    return ChatApp(tmp_path, load(tmp_path / "config.json"), connect=connect)


class _CaptureLLM:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def chat(self, messages, tools=None):
        self.prompts.append("\n".join(str(m.content or "") for m in messages))
        return ChatReply(content="ok reply", model="m",
                         usage={"prompt_tokens": 10, "completion_tokens": 2,
                                "total_tokens": 12})


def _seed_history(app: ChatApp, bot_id: str, room_id: str, *, marker: str) -> None:
    internal = app._runtime.internal_session(bot_id, room_id)
    manager = SessionManager(store=app._runtime.store_for(bot_id))
    manager.load(internal.id)
    for index in range(10):
        manager.append(Message(
            role="user", content=f"{marker}-{index} " + "u" * 500,
            ts=f"2026-08-01T10:{index:02d}:00Z"))
        manager.append(Message(
            role="assistant", content=f"answer-{index} " + "a" * 500,
            ts=f"2026-08-01T10:{index:02d}:30Z"))


def _internal_messages(app: ChatApp, bot_id: str, room_id: str):
    internal = app._runtime.internal_session(bot_id, room_id)
    return app._runtime.store_for(bot_id).load(internal.id).messages


# --- CBE-1: per-instance budget ------------------------------------------

def test_bot_instance_compacts_with_own_budget_and_overhead(tmp_path: Path) -> None:
    llm = _CaptureLLM()
    # 3400 (was 3000, was 2000): legacy cards now always keep the baseline
    # tools, whose schemas are real counted overhead in this budget;
    # message_user joining the baseline raised that overhead again.
    app = _chat(tmp_path, llm, context_tokens=3400)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="PERSONA-CODER"))
        app._bots.create(BotCard(bot_id="helper", name="helper", role="Helper",
                                 description="Helps.", persona="PERSONA-HELPER"))
        other = app.create(bots=["helper"])["id"]
        _seed_history(app, "helper", other, marker="HELPER-HISTORY")
        # Created last: creating a session prunes other blank sessions.
        room = app.create(bots=["coder"])["id"]
        _seed_history(app, "coder", room, marker="EARLY-SECRET")

        detail = app.turn(room, "continue please")
        assert detail["reply"] == "ok reply"

        coder_msgs = _internal_messages(app, "coder", room)
        assert any(
            m.role == "system" and (m.content or "").startswith("[compaction: ")
            for m in coder_msgs
        ), "bot session must compact under its own context budget"
        # The compacted main prompt no longer carries the earliest seeded
        # turns (the summarizer side-call legitimately sees them once).
        main_prompts = [p for p in llm.prompts if "PERSONA-CODER" in p]
        assert main_prompts
        assert all("EARLY-SECRET-0" not in prompt for prompt in main_prompts)
        # The other bot's session is untouched by coder's compaction.
        helper_msgs = _internal_messages(app, "helper", other)
        assert not any(
            m.role == "system" and (m.content or "").startswith("[compaction: ")
            for m in helper_msgs
        )
    finally:
        app.shutdown()


def test_compactor_counts_hot_and_tools_overhead() -> None:
    # Transcript alone fits under the backstop; transcript + hot + tools
    # does not. The plan must fire only when overhead is counted.
    messages = [
        Message(role="user", content="u" * 1200, ts="2026-08-01T10:00:00Z"),
        Message(role="assistant", content="a" * 1200, ts="2026-08-01T10:01:00Z"),
        Message(role="user", content="u" * 600, ts="2026-08-01T10:02:00Z"),
        Message(role="assistant", content="a" * 600, ts="2026-08-01T10:03:00Z"),
    ]
    compactor = SessionCompactor()
    assert compactor.plan(messages, 1000) is None
    assert compactor.plan(messages, 1000, hot_tokens=150, tools_tokens=150) is not None


def test_agent_loop_hot_and_tools_token_counters() -> None:
    loop = AgentLoop(
        sessions=None, assemble=None, think=None, act=None, observe=None,  # type: ignore[arg-type]
        loop_max=1, tools=[{"type": "function", "function": {"name": "read"}}],
        context_tokens=1000,
    )
    hot = ActiveSnapshot(
        soul="# Soul\nYou are Thyca.", user="# User\n", today="notes",
        team="- coder — Coder", tasks="- job A · open · room1",
        recalled="- hit: fact",
    )
    assert loop._hot_tokens(hot) > 0
    # Every hot section (team/tasks/recalled included) is counted.
    bigger = replace(hot, team=hot.team + "\n- x — y" * 50)
    assert loop._hot_tokens(bigger) > loop._hot_tokens(hot)
    assert loop._tools_tokens() > 0


# --- CBE-2: slim prompt ----------------------------------------------------

def test_team_index_is_name_and_role_only(tmp_path: Path) -> None:
    from thyca.bots.registry import BotRegistry

    registry = BotRegistry(tmp_path)
    registry.ensure_thyca()
    registry.create(BotCard(
        bot_id="coder", name="coder", role="Coder",
        description="A very long description that must not ride prompts.",
        persona="P"))
    index = registry.team_index_text()
    assert "- coder — Coder" in index
    assert "very long description" not in index
    # The public API keeps the description; only prompts slim down.
    public = {row["id"]: row for row in registry.public_list()}
    assert "very long description" in public["coder"]["description"]


def test_skill_index_is_name_plus_short_description_no_body(tmp_path: Path) -> None:
    from thyca.bots.registry import BotRegistry

    registry = BotRegistry(tmp_path)
    registry.ensure_thyca()
    skill = tmp_path / "bots" / "thyca" / "skills" / "mine"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: mine\ndescription: " + "d" * 400 + "\n---\nSECRET BODY TEXT\n",
        encoding="utf-8")
    index = registry.skill_index_text("thyca")
    assert "SECRET BODY" not in index
    line = [row for row in index.splitlines() if row.startswith("- mine")][0]
    assert len(line) < 300  # name + capped (<=256) short description only


def test_bot_prompt_carries_task_index_counted_in_hot(tmp_path: Path) -> None:
    llm = _CaptureLLM()
    app = _chat(tmp_path, llm)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="PERSONA-CODER",
                                 tools=("task_open",)))
        room = app.create(bots=["coder"])["id"]
        internal = app._runtime.internal_session("coder", room)
        manager = SessionManager(store=app._runtime.store_for("coder"))
        manager.load(internal.id)
        manager.append(Message(
            role="assistant", content="", ts="2026-08-01T10:00:00Z",
            tool_calls=[ToolCall(id="t1", name="task_open",
                                 arguments={"title": "long research"})]))
        manager.append(Message(
            role="tool", content='{"ok": true, "status": "open"}',
            tool_call_id="t1", ts="2026-08-01T10:00:10Z"))
        app.turn(room, "status?")
        assert any("long research · open" in prompt for prompt in llm.prompts)
    finally:
        app.shutdown()


# --- CBE-4: flush before compact, re-inject after ---------------------------

def test_flush_runs_before_compaction_and_recall_fires_once(tmp_path: Path) -> None:
    llm = _CaptureLLM()
    # 3400 (was 3000, was 2000): see the baseline-overhead note above.
    app = _chat(tmp_path, llm, context_tokens=3400)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="PERSONA-CODER"))
        room = app.create(bots=["coder"])["id"]
        _seed_history(app, "coder", room, marker="FLUSH-ME")

        flush_calls: list[tuple[str, bool]] = []

        def fake_flush(bot_id, messages, turn_cfg):
            has_marker = any(
                (m.content or "").startswith("[compaction: ") for m in messages
            )
            flush_calls.append((bot_id, has_marker))

        app._runtime.memory_flush = fake_flush
        app._runtime.memory_searcher = lambda text, bot_id: ["- recalled: egg coffee fact"]

        app.turn(room, "first turn")
        # Flush saw the intact (pre-compaction) transcript, exactly once.
        assert flush_calls == [("coder", False)]
        assert any(
            (m.content or "").startswith("[compaction: ")
            for m in _internal_messages(app, "coder", room)
        )

        prompts_before = len(llm.prompts)
        app.turn(room, "second turn about egg coffee")
        assert any(
            "egg coffee fact" in prompt for prompt in llm.prompts[prompts_before:]
        ), "first post-compact turn must re-inject relevant memory hits"

        prompts_before = len(llm.prompts)
        app.turn(room, "third turn")
        assert all(
            "egg coffee fact" not in prompt for prompt in llm.prompts[prompts_before:]
        ), "recall is a one-shot re-injection, not a permanent prompt tax"
    finally:
        app.shutdown()


def test_no_flush_when_no_compaction_due(tmp_path: Path) -> None:
    llm = _CaptureLLM()
    app = _chat(tmp_path, llm, context_tokens=1000)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="PERSONA-CODER"))
        room = app.create(bots=["coder"])["id"]
        calls: list[str] = []
        app._runtime.memory_flush = lambda bot_id, messages, cfg: calls.append(bot_id)
        app.turn(room, "hi")
        assert calls == []
    finally:
        app.shutdown()


# --- Two-layer twin identity (2026-10-13) -----------------------------------

def _marker_home(tmp_path: Path, app: ChatApp) -> None:
    (tmp_path / "SOUL.md").write_text(
        "# Soul\n\nSHARED-SOUL-MARKER: shared team voice.\n", encoding="utf-8")
    (tmp_path / "IDENTITY.md").write_text(
        "# Identity\n\nName: Thyca\nTHYCA-IDENTITY-MARKER\n", encoding="utf-8")


def test_child_prompt_own_identity_shared_soul_persona_overlay(tmp_path: Path) -> None:
    llm = _CaptureLLM()
    app = _chat(tmp_path, llm)
    try:
        _marker_home(tmp_path, app)
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="PERSONA-CODER"))
        room = app.create(bots=["coder"])["id"]
        app.turn(room, "who are you?")
        prompts = [p for p in llm.prompts if "PERSONA-CODER" in p]
        assert prompts, "child main prompt must carry its persona overlay"
        prompt = prompts[-1]
        # Own identity from TWIN.md, shared soul, persona overlay …
        assert "Name: coder" in prompt
        assert "SHARED-SOUL-MARKER" in prompt
        # … and never Thyca's IDENTITY.md (the child-as-Thyca leak).
        assert "THYCA-IDENTITY-MARKER" not in prompt
        assert "Name: Thyca" not in prompt
    finally:
        app.shutdown()


def test_child_empty_persona_keeps_own_identity_and_shared_soul(tmp_path: Path) -> None:
    llm = _CaptureLLM()
    app = _chat(tmp_path, llm)
    try:
        _marker_home(tmp_path, app)
        app._bots.create(BotCard(bot_id="helper", name="helper", role="Helper",
                                 description="Helps out.", persona=""))
        room = app.create(bots=["helper"])["id"]
        app.turn(room, "who are you?")
        prompts = [p for p in llm.prompts if "Name: helper" in p]
        assert prompts, "empty persona must still yield the twin's own identity"
        prompt = prompts[-1]
        assert "SHARED-SOUL-MARKER" in prompt
        assert "THYCA-IDENTITY-MARKER" not in prompt
        assert "Name: Thyca" not in prompt
    finally:
        app.shutdown()
