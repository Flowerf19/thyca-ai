"""CBE-1/CBE-2/CBE-4: per-bot budgets, slim prompt, flush + re-inject."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from thyca.agent.loop import AgentLoop
from thyca.app.chat_app import ChatApp
from thyca.bots.card import BotCard
from thyca.config import load, save
from seeded_config import seeded_config
from thyca.core.protocol import Message, ToolCall
from thyca.llm.llm_base import ChatReply
from thyca.memory.active import ActiveSnapshot
from thyca.sessions import SessionManager
from thyca.sessions.compaction import SessionCompactor


def _chat(tmp_path: Path, connect, context_tokens: int | None = None) -> ChatApp:
    cfg = seeded_config()
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


def _internal_store(app: ChatApp, bot_id: str, room_id: str):
    """Canonical journal store + session id for a bot's room binding."""
    internal = app._runtime.internal_session(bot_id, room_id)
    window = app._runtime._window_for_room(room_id)
    assert window is not None, f"no window for room {room_id}"
    store = app._runtime._canonical_store(
        bot_id, window, internal.id)
    return store, internal.id


def _seed_history(app: ChatApp, bot_id: str, room_id: str, *, marker: str) -> None:
    store, session_id = _internal_store(app, bot_id, room_id)
    manager = SessionManager(store=store)
    manager.load(session_id)
    for index in range(10):
        manager.append(Message(
            role="user", content=f"{marker}-{index} " + "u" * 500,
            ts=f"2026-08-01T10:{index:02d}:00Z"))
        manager.append(Message(
            role="assistant", content=f"answer-{index} " + "a" * 500,
            ts=f"2026-08-01T10:{index:02d}:30Z"))


def _internal_messages(app: ChatApp, bot_id: str, room_id: str):
    store, session_id = _internal_store(app, bot_id, room_id)
    return store.load(session_id).messages


# --- CBE-1: per-instance budget ------------------------------------------

def test_bot_instance_compacts_with_own_budget_and_overhead(tmp_path: Path) -> None:
    llm = _CaptureLLM()
    # 5400 (was 5200/4800/4400/4000/3400/3000/2000): legacy cards now always keep
    # the baseline tools, whose schemas are real counted overhead in this
    # budget; message_user, then ask_user/report_error, then memory_tasks
    # (phase 1A), then memory_tasks in baseline (review rework), then the
    # private follow-up channel tools, then the compact tool (TURN-CURATE)
    # raised it again.
    app = _chat(tmp_path, llm, context_tokens=5400)
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
        store, session_id = _internal_store(app, "coder", room)
        manager = SessionManager(store=store)
        manager.load(session_id)
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
    # 5400 (was 5200/...): the baseline tool set (incl. the compact tool)
    # is counted overhead in this budget, like the first test above.
    app = _chat(tmp_path, llm, context_tokens=5400)
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


def test_no_flush_when_agent_checkpointed_segment(tmp_path: Path) -> None:
    llm = _CaptureLLM()
    app = _chat(tmp_path, llm, context_tokens=5400)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="PERSONA-CODER"))
        room = app.create(bots=["coder"])["id"]
        _seed_history(app, "coder", room, marker="CKPT")
        # Agent đã chủ động ghi checkpoint (kind='checkpoint' + task link)
        # trong segment này — hệ thống phải đứng yên, không chạy mono.
        store, session_id = _internal_store(app, "coder", room)
        manager = SessionManager(store=store)
        manager.load(session_id)
        manager.append(Message(
            role="assistant", content="",
            tool_calls=[ToolCall(
                id="call-ckpt", name="memory_remember",
                arguments={"topic": "Goal", "summary": "Done A; next B",
                           "kind": "checkpoint", "task": "task-1234abcd"},
            )],
            ts="2026-08-01T10:20:00Z",
        ))
        manager.append(Message(
            role="tool", content='{"session_id": "2026-08-01#abc"}',
            tool_call_id="call-ckpt", ts="2026-08-01T10:20:30Z",
        ))
        calls: list[str] = []
        app._runtime.memory_flush = lambda bot_id, messages, cfg: calls.append(bot_id)
        app.turn(room, "first turn")
        assert calls == [], "checkpointed segment must skip the mono flush"
        assert any(
            (m.content or "").startswith("[compaction: ")
            for m in _internal_messages(app, "coder", room)
        ), "the turn still compacts"
    finally:
        app.shutdown()


def test_aggregate_team_profiles_runs_thyca_turn_and_strips_legacy(
    tmp_path: Path, monkeypatch
) -> None:
    from thyca.memory.habits import AUTO_END as LEGACY_END
    from thyca.memory.habits import AUTO_START as LEGACY_START
    from thyca.memory.profile import PROFILE_AUTO_END, PROFILE_AUTO_START

    llm = _CaptureLLM()
    app = _chat(tmp_path, llm)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="PERSONA-CODER"))
        bot_user = app._bots.bot_dir("coder") / "USER.md"
        bot_user.parent.mkdir(parents=True, exist_ok=True)
        bot_user.write_text(
            f"# User — bot tự ghi\n## Profile\n{PROFILE_AUTO_START}\n"
            f"- coder fact\n{PROFILE_AUTO_END}\n",
            encoding="utf-8",
        )
        shared = tmp_path / "USER.md"
        shared.write_text(
            f"# User\n## Profile\n{PROFILE_AUTO_START}\n- coder fact\n"
            f"{PROFILE_AUTO_END}\n## Habits\n{LEGACY_START}\n- coder fact\n"
            f"{LEGACY_END}\n",
            encoding="utf-8",
        )
        calls = []
        monkeypatch.setattr(
            app._background_jobs._deps, "scheduled_turn",
            lambda bot_id, text: (calls.append((bot_id, text)), {"id": "dm"})[1],
        )
        result = app.aggregate_team_profiles()
        assert result["ran"] is True
        assert len(calls) == 1
        assert calls[0][0] == "thyca"
        prompt = calls[0][1]
        assert "coder fact" in prompt
        assert "Writes code." in prompt  # card context injected
        # Legacy ## Habits block đã strip vì line nằm trong profile block.
        text = shared.read_text(encoding="utf-8")
        assert "## Habits" not in text
        assert "## Profile" in text
    finally:
        app.shutdown()


def test_aggregate_team_profiles_skips_within_window(
    tmp_path: Path, monkeypatch
) -> None:
    from thyca.memory.profile import PROFILE_AUTO_END, PROFILE_AUTO_START

    llm = _CaptureLLM()
    app = _chat(tmp_path, llm)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona=""))
        bot_user = app._bots.bot_dir("coder") / "USER.md"
        bot_user.parent.mkdir(parents=True, exist_ok=True)
        bot_user.write_text(
            f"# User — bot tự ghi\n## Profile\n{PROFILE_AUTO_START}\n"
            f"- coder fact\n{PROFILE_AUTO_END}\n",
            encoding="utf-8",
        )
        calls = []
        monkeypatch.setattr(
            app._background_jobs._deps, "scheduled_turn",
            lambda bot_id, text: (calls.append((bot_id, text)), {"id": "dm"})[1],
        )
        assert app.aggregate_team_profiles()["ran"] is True
        assert app.aggregate_team_profiles()["ran"] is False  # trong 24h
        assert len(calls) == 1
    finally:
        app.shutdown()


def test_aggregate_team_profiles_skips_without_input(tmp_path: Path) -> None:
    llm = _CaptureLLM()
    app = _chat(tmp_path, llm)
    try:
        assert app.aggregate_team_profiles()["ran"] is False
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


# --- Phase 1C: extractor catalog per bot + <memory_tasks> section ---------

def _seed_task(app: ChatApp, bot_id: str | None, topic: str, task_id: str,
               chat: str | None = None):
    from thyca.memory.identity import bind_bot_id, reset_bot_id

    facade = app.job_facade()
    token = bind_bot_id(bot_id) if bot_id else None
    try:
        return facade.remember(topic, f"work on {topic}", task=task_id,
                               chat=chat)
    finally:
        if token is not None:
            reset_bot_id(token)


def test_flush_extractor_prompt_hides_other_bot_task(tmp_path: Path) -> None:
    import asyncio

    llm = _CaptureLLM()
    app = _chat(tmp_path, llm)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona=""))
        app._bots.create(BotCard(bot_id="designer", name="designer", role="Designer",
                                 description="Draws.", persona=""))
        designer_task = "task-dec0ded1"
        app.job_facade().remember("look", "design work", task=designer_task,
                                  bots=["designer"])
        coder_task = "task-c0de0001"
        _seed_task(app, "coder", "build", coder_task)
        asyncio.run(app._flush_bot_memory(
            "coder",
            [Message(role="user", content="ship it", ts="2026-10-16T10:00:00Z")],
            app._current_cfg(),
        ))
        extractor_prompts = [p for p in llm.prompts if "durable memories" in p]
        assert extractor_prompts, "flush must call the extractor model"
        prompt = extractor_prompts[-1]
        assert coder_task in prompt
        assert designer_task not in prompt
    finally:
        app.shutdown()


def test_child_prompt_memory_tasks_filtered_per_bot(tmp_path: Path) -> None:
    llm = _CaptureLLM()
    app = _chat(tmp_path, llm)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona=""))
        app._bots.create(BotCard(bot_id="designer", name="designer",
                                 role="Designer", description="Draws.", persona=""))
        coder_task = "task-c0de0002"
        designer_task = "task-dec0ded2"
        room = app.create(bots=["coder"])["id"]
        _seed_task(app, "coder", "build it", coder_task, chat=room)
        _seed_task(app, "designer", "draw it", designer_task, chat=room)
        app.turn(room, "status?")
        prompts = [p for p in llm.prompts if "<memory_tasks>" in p]
        assert prompts, "child prompt must carry the memory-task catalog"
        prompt = prompts[-1]
        assert coder_task in prompt
        assert designer_task not in prompt
    finally:
        app.shutdown()


def test_thyca_prompt_sees_all_memory_tasks(tmp_path: Path) -> None:
    llm = _CaptureLLM()
    app = _chat(tmp_path, llm)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona=""))
        coder_task = "task-c0de0003"
        designer_task = "task-dec0ded3"
        _seed_task(app, "coder", "build all", coder_task)
        _seed_task(app, "designer", "draw all", designer_task)
        room = app.create()["id"]
        app.turn(room, "status?")
        prompts = [p for p in llm.prompts if "<memory_tasks>" in p]
        assert prompts, "Thyca prompt must carry the memory-task catalog"
        assert coder_task in prompts[-1]
        assert designer_task in prompts[-1]
    finally:
        app.shutdown()


def test_prompt_sections_open_tasks_and_memory_tasks_stay_separate() -> None:
    from thyca.llm.prompt_manager import PromptManager

    hot = ActiveSnapshot(
        soul="# Soul\nVoice.", user="# User\n", today="notes",
        tasks="- job A · open · room1",
        memory_tasks="- task-aaaaaaaa: Fix sink · last active 2026-09-01 · bots coder",
    )
    out = PromptManager().build(hot)
    assert "<open_tasks>\n- job A · open · room1\n</open_tasks>" in out
    assert "<memory_tasks>\n- task-aaaaaaaa: Fix sink" in out


# --- Phase 1E: per-bot USER.md in prompt -------------------------------------

def _write_bot_user(app: ChatApp, bot_id: str, text: str) -> None:
    path = app._bots.bot_dir(bot_id) / "USER.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_child_prompt_carries_own_bot_user_only(tmp_path: Path) -> None:
    llm = _CaptureLLM()
    app = _chat(tmp_path, llm)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona=""))
        app._bots.create(BotCard(bot_id="designer", name="designer",
                                 role="Designer", description="Draws.", persona=""))
        _write_bot_user(app, "coder", "# User — bot tự ghi\n- coder habit line\n")
        _write_bot_user(app, "designer", "# User — bot tự ghi\n- designer habit line\n")
        room = app.create(bots=["coder"])["id"]
        app.turn(room, "hi")
        prompts = [p for p in llm.prompts if "<bot_user>" in p]
        assert prompts, "child prompt must carry its own bot USER.md"
        assert "coder habit line" in prompts[-1]
        assert "designer habit line" not in prompts[-1]
    finally:
        app.shutdown()


def test_memory_read_false_has_no_bot_user_section(tmp_path: Path) -> None:
    from thyca.bots.card import BotMemoryPolicy

    llm = _CaptureLLM()
    app = _chat(tmp_path, llm)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="",
                                 memory=BotMemoryPolicy(read=False, write=True)))
        _write_bot_user(app, "coder", "# User — bot tự ghi\n- hidden habit\n")
        room = app.create(bots=["coder"])["id"]
        app.turn(room, "hi")
        assert not any("<bot_user>" in p for p in llm.prompts)
    finally:
        app.shutdown()


def test_thyca_prompt_carries_thyca_bot_user(tmp_path: Path) -> None:
    llm = _CaptureLLM()
    app = _chat(tmp_path, llm)
    try:
        _write_bot_user(app, "thyca", "# User — bot tự ghi\n- thyca habit line\n")
        room = app.create()["id"]
        app.turn(room, "hi")
        prompts = [p for p in llm.prompts if "<bot_user>" in p]
        assert prompts
        assert "thyca habit line" in prompts[-1]
    finally:
        app.shutdown()


def test_flush_explicit_habit_lands_in_bot_user(tmp_path: Path) -> None:
    import asyncio
    import json as _json

    class HabitLLM:
        async def chat(self, messages, tools=None):
            return type("R", (), {"content": _json.dumps([{
                "topic": "morning drink", "summary": "user drinks tea",
                "kind": "habit", "habit": "uong tra buoi sang",
                "habit_explicit": True,
            }])})()

    app = _chat(tmp_path, HabitLLM())
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona=""))
        asyncio.run(app._flush_bot_memory(
            "coder",
            [Message(role="user", content="tôi luôn uống trà buổi sáng",
                     ts="2026-10-16T10:00:00Z")],
            app._current_cfg(),
        ))
        text = (app._bots.bot_dir("coder") / "USER.md").read_text(encoding="utf-8")
        assert "- uong tra buoi sang" in text
    finally:
        app.shutdown()


def test_shared_user_habit_reaches_prompt_through_user_section(tmp_path: Path) -> None:
    llm = _CaptureLLM()
    app = _chat(tmp_path, llm)
    try:
        (tmp_path / "USER.md").write_text(
            "# User\n\n## Habits\n<!-- habits:auto:start -->\n"
            "- uong tra buoi sang\n<!-- habits:auto:end -->\n",
            encoding="utf-8",
        )
        room = app.create()["id"]
        app.turn(room, "hi")
        users = [p for p in llm.prompts if "<user>" in p]
        assert users, "shared USER.md must keep flowing into <user>"
        assert "uong tra buoi sang" in users[-1]
    finally:
        app.shutdown()


# --- Phase 2A: public room context -----------------------------------------

def _internal_rows(app: ChatApp, bot_id: str, room_id: str):
    """Raw canonical transcript rows for a bot's room binding."""
    return list(_internal_messages(app, bot_id, room_id))


def _delta_prompts(llm, since: int = 0) -> list[str]:
    """Wire prompts carrying an ephemeral room-delta block.

    The delta rides the hot snapshot (``<room_history>``) and is never
    persisted, so per-turn delivery is observed on the wire, scoped to
    prompts captured after index ``since``.
    """
    return [p for p in llm.prompts[since:]
            if "lịch sử phòng chưa đọc" in p or "Tin mới trong phòng" in p]


def _room_history_section(prompt: str) -> str:
    """The ``<room_history>`` delta section of one wire prompt.

    Content assertions scope to the delta itself: the surrounding prompt
    legitimately carries task indexes and the current user text.
    """
    start = prompt.find("<room_history>")
    end = prompt.find("</room_history>")
    assert start != -1 and end != -1, "no room_history section on the wire"
    return prompt[start:end]


def _room_context_blocks(app: ChatApp, bot_id: str, room_id: str) -> list[str]:
    # Canonical-only: no synthetic room_context rows are ever persisted.
    # Kept as a guard — must always be empty.
    return [m.content or "" for m in _internal_rows(app, bot_id, room_id)
            if isinstance(m.meta, dict) and m.meta.get("kind") == "room_context"]


def _seed_room_pair(app: ChatApp) -> str:
    """Room with one user message + one designer final. Returns room id."""
    app._bots.create(BotCard(bot_id="designer", name="designer",
                             role="Designer", description="Draws.", persona=""))
    app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                             description="Writes code.", persona=""))
    # Group from the start (phase 2B): solo rooms are DMs, which admit
    # no second participant. Designer answers first as participant[0].
    room = app.create(bots=["designer", "coder"])["id"]
    app.turn(room, "user asks about deploy")
    return room


def test_first_turn_sees_public_history_not_secrets(tmp_path: Path) -> None:
    from thyca.sessions import SessionManager

    llm = _CaptureLLM()
    app = _chat(tmp_path, llm)
    try:
        room = _seed_room_pair(app)
        # Private envelope + raw tool output must never enter room context.
        app._runtime.send(sender="designer", recipient="coder", kind="message",
                          session_id=room, payload="SECRET-ENVELOPE")
        store = SessionManager(store=app._sessions.store)
        store.load(room)
        store.append(Message(
            role="assistant", content="thinking step",
            meta={"bot_id": "designer", "kind": "llm"},
            tool_calls=[ToolCall(id="t1", name="bash", arguments={})],
            ts="2026-10-16T10:01:30Z",
        ))
        store.append(Message(role="tool", content="SECRET-TOOL-OUTPUT",
                             tool_call_id="t1",
                             ts="2026-10-16T10:02:00Z"))
        since = len(llm.prompts)
        app.turn(room, "@coder go")
        # Nothing persisted — and the wire carried exactly one delta.
        assert _room_context_blocks(app, "coder", room) == []
        blocks = _delta_prompts(llm, since)
        assert len(blocks) == 1
        delta = _room_history_section(blocks[0])
        assert "user asks about deploy" in delta
        assert "ok reply" in delta
        assert "SECRET-ENVELOPE" not in delta
        assert "SECRET-TOOL-OUTPUT" not in delta
        assert "lịch sử phòng chưa đọc" in delta
    finally:
        app.shutdown()


def test_second_turn_gets_no_repeat_of_answered_message(tmp_path: Path) -> None:
    llm = _CaptureLLM()
    app = _chat(tmp_path, llm)
    try:
        room = _seed_room_pair(app)
        seeded = len(llm.prompts)
        app.turn(room, "@coder go")
        assert len(_delta_prompts(llm, seeded)) == 1
        seen = len(llm.prompts)
        # The first turn's user row was marked read on success: the next
        # turn delivers nothing already answered.
        app.turn(room, "@coder again")
        assert _delta_prompts(llm, seen) == []
    finally:
        app.shutdown()


def test_next_turn_gets_only_fresh_message(tmp_path: Path) -> None:
    from thyca.sessions import SessionManager

    llm = _CaptureLLM()
    app = _chat(tmp_path, llm)
    try:
        room = _seed_room_pair(app)
        app.turn(room, "@coder go")
        store = SessionManager(store=app._sessions.store)
        store.load(room)
        store.append(Message(role="assistant", content="DESIGNER-NEWS",
                             meta={"bot_id": "designer", "kind": "message",
                                   "parent_span_id": None},
                             ts="2026-10-16T10:05:00Z"))
        since = len(llm.prompts)
        app.turn(room, "@coder update?")
        assert _room_context_blocks(app, "coder", room) == []
        blocks = _delta_prompts(llm, since)
        assert len(blocks) == 1
        delta = _room_history_section(blocks[-1])
        assert "DESIGNER-NEWS" in delta
        assert "user asks about deploy" not in delta
        assert "@coder go" not in delta
        assert "Tin mới trong phòng" in delta
    finally:
        app.shutdown()


def test_dm_turn_produces_no_room_context(tmp_path: Path) -> None:
    llm = _CaptureLLM()
    app = _chat(tmp_path, llm)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona=""))
        dm = app.bot_dm("coder")["id"]
        since = len(llm.prompts)
        app.turn(dm, "private hello")
        assert _room_context_blocks(app, "coder", dm) == []
        assert _delta_prompts(llm, since) == []
    finally:
        app.shutdown()


def test_deliver_excludes_current_envelope_from_delta(tmp_path: Path) -> None:
    import asyncio

    from thyca.bots.context import BotTurnScope
    from thyca.bots.bus import new_span_id, new_trace_id
    from thyca.bots.context import BotTurnContext

    llm = _CaptureLLM()
    app = _chat(tmp_path, llm)
    try:
        room = _seed_room_pair(app)
        envelope = app._runtime.send(
            sender="designer", recipient="coder", kind="delegate",
            session_id=room, payload="SECRET-DELEGATE-PAYLOAD")
        scope = BotTurnScope(
            store=app._sessions.store, act=app._act, tools=app._tools,
            memory=app._memory, state=app._state, zone=app._zone,
            injected_connect=llm,
        )
        ctx = BotTurnContext(
            sender_id="designer", room_id=room, trace_id=new_trace_id(),
            span_id=new_span_id(), scope=scope, turn_cfg=app._current_cfg(),
        )
        since = len(llm.prompts)
        reply = asyncio.run(app._deliver_envelope(envelope, ctx))
        assert reply == "ok reply"
        # Nothing persisted — the wire carried exactly one delta.
        assert _room_context_blocks(app, "coder", room) == []
        blocks = _delta_prompts(llm, since)
        assert len(blocks) == 1
        delta = _room_history_section(blocks[0])
        assert "SECRET-DELEGATE-PAYLOAD" not in delta
        assert "user asks about deploy" in delta
        assert "ok reply" in delta
        # Cursor stops at the delivered delta: the live envelope is not
        # counted as read room history.
        cursor = app._runtime.room_cursor("coder", room)
        assert cursor is not None and cursor[0] == 2
    finally:
        app.shutdown()


def test_failed_turn_keeps_current_message_unread(tmp_path: Path) -> None:
    import asyncio

    from thyca.bots.context import BotTurnScope
    from thyca.llm.llm_base import ChatReply

    class DownLLM:
        async def chat(self, messages, tools=None):
            raise RuntimeError("provider down")

    llm = _CaptureLLM()
    app = _chat(tmp_path, llm)
    try:
        room = _seed_room_pair(app)
        card = app._bots.require("coder")
        scope = BotTurnScope(
            store=app._sessions.store, act=app._act, tools=app._tools,
            memory=app._memory, state=app._state, zone=app._zone,
            injected_connect=DownLLM(),
        )
        with __import__("pytest").raises(RuntimeError):
            asyncio.run(app._runtime.run_bot_turn_detailed(
                scope=scope, card=card, room_id=room, text="@coder go",
                turn_cfg=app._current_cfg(), room_text="CTX",
                room_cursor_after=(3, "fpcur"),
            ))
        # The ephemeral delta was never persisted and the cursor never
        # advanced: the failed turn journaled only its error-marked user row.
        assert app._runtime.room_cursor("coder", room) is None
        rows = _internal_rows(app, "coder", room)
        assert not [m for m in rows
                    if isinstance(m.meta, dict)
                    and m.meta.get("kind") == "room_context"]
        assert any((m.content or "") == "@coder go"
                   and (m.meta or {}).get("error", {}).get("code")
                   for m in rows if m.role == "user")
        # A later success with the same after-cursor does advance it.
        scope2 = BotTurnScope(
            store=app._sessions.store, act=app._act, tools=app._tools,
            memory=app._memory, state=app._state, zone=app._zone,
            injected_connect=llm,
        )
        asyncio.run(app._runtime.run_bot_turn_detailed(
            scope=scope2, card=card, room_id=room, text="@coder go",
            turn_cfg=app._current_cfg(), room_text="",
            room_cursor_after=(3, "fpcur"),
        ))
        assert app._runtime.room_cursor("coder", room) == (3, "fpcur")
    finally:
        app.shutdown()


def test_registry_binding_cursor_roundtrip(tmp_path: Path) -> None:
    import json

    from thyca.bots.bus import Bus
    from thyca.bots.registry import BotRegistry
    from thyca.bots.runtime import BotRuntime

    registry = BotRegistry(tmp_path)
    runtime = BotRuntime(tmp_path, registry, Bus())
    # First binding mints a stable canonical session id (no cursor yet).
    internal = runtime.internal_session("coder", "room1")
    binding = runtime.workspaces.store.get_binding("coder", "room1")
    assert binding is not None and binding.internal_session_id == internal.id
    assert runtime.room_cursor("coder", "room1") is None
    again = runtime.internal_session("coder", "room1")
    assert again.id == internal.id
    # Roundtrip: cursor persists in the one current registry shape.
    runtime.set_room_cursor("coder", "room1", (3, "fpabc"))
    assert runtime.room_cursor("coder", "room1") == (3, "fpabc")
    raw = json.loads((tmp_path / "bots" / "coder" / "workspace"
                       / "registry.json").read_text(encoding="utf-8"))
    entry = raw["windows"]["room1"]
    assert entry["session"] == internal.id
    assert (entry["room_cursor"], entry["room_fingerprint"]) == (3, "fpabc")
    # No room_sessions.json mirror is ever written.
    assert not (tmp_path / "bots" / "coder" / "room_sessions.json").exists()
    assert not (tmp_path / "bots" / "coder"
                / "room_sessions.json.bak").exists()
    # No binding: nothing is created or written for a cursor.
    assert runtime.room_cursor("coder", "noroom") is None
    assert runtime._workspaces.store.get_binding("coder", "noroom") is None
    runtime.set_room_cursor("coder", "noroom", (1, "x"))
    assert runtime.room_cursor("coder", "noroom") is None
    assert runtime._workspaces.store.get_binding("coder", "noroom") is None
    # None cursor writes nothing.
    before = (tmp_path / "bots" / "coder" / "workspace"
              / "registry.json").read_bytes()
    runtime.set_room_cursor("coder", "room1", None)
    assert (tmp_path / "bots" / "coder" / "workspace"
            / "registry.json").read_bytes() == before


def test_failed_bot_turn_keeps_user_row_with_error_marker(tmp_path: Path) -> None:
    import pytest as _pytest

    from thyca.llm.llm_base import ChatReply

    class FlakyLLM:
        def __init__(self) -> None:
            self.calls = 0
            self.prompts: list[str] = []

        async def chat(self, messages, tools=None):
            self.calls += 1
            self.prompts.append(
                "\n".join(str(m.content or "") for m in messages))
            if self.calls == 2:
                raise RuntimeError("provider down")
            return ChatReply(content="ok reply", model="m",
                             usage={"prompt_tokens": 1, "completion_tokens": 1,
                                    "total_tokens": 2})

    llm = FlakyLLM()
    app = _chat(tmp_path, llm)
    try:
        room = _seed_room_pair(app)
        with _pytest.raises(RuntimeError):
            app.turn(room, "@coder go")
        from thyca.sessions import SessionManager

        store = SessionManager(store=app._sessions.store)
        store.load(room)
        user_rows = [m for m in store.current.messages
                     if m.role == "user" and (m.content or "") == "@coder go"]
        assert len(user_rows) == 1
        error = (user_rows[0].meta or {}).get("error") or {}
        assert error.get("code") == "chat_unavailable"
        # The failed turn delivered nothing, so no cursor advanced: the
        # ephemeral delta was never marked read (no pre-write on failure).
        assert app._runtime.room_cursor("coder", room) is None
        # Next turn still sees the kept user row as unread delta.
        since = len(llm.prompts)
        app.turn(room, "@coder retry?")
        blocks = _delta_prompts(llm, since)
        assert len(blocks) == 1
        assert "@coder go" in _room_history_section(blocks[-1])
    finally:
        app.shutdown()
