"""WS-3 end-to-end: private DM memory must not leak into group prompts.

This asserts presence/absence of seeded strings in the exact payload the
(fake) provider received — it does NOT claim the model understands any
privacy rule.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from thyca.app.chat_app import ChatApp
from thyca.bots.card import BotCard
from thyca.config import load, save
from seeded_config import seeded_config
from thyca.llm.llm_base import ChatReply
from thyca.memory.facade import format_task_catalog


def _chat(tmp_path: Path, connect=None) -> ChatApp:
    save(seeded_config(), tmp_path / "config.json")
    return ChatApp(tmp_path, load(tmp_path / "config.json"), connect=connect)


class _SpyLLM:
    def __init__(self) -> None:
        self.payloads: list[str] = []

    async def chat(self, messages, tools=None):
        self.payloads.append(
            "\n".join(str(m.content or "") for m in messages))
        return ChatReply(content="ok", model="m",
                         usage={"prompt_tokens": 5, "completion_tokens": 1,
                                "total_tokens": 6})


def _remember(tmp_path: Path, chat: str, title: str, body: str,
              bots=None, task: str | None = None, kind: str | None = None,
              status: str | None = None) -> None:
    from thyca.memory.facade import MemoryFacade

    # Seeds must land on the wall-clock day (same-day entries are served
    # live from today's file; a fixed seed date rots once the clock moves
    # past it). Turns under test always run on wall-clock time.
    MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh").remember(
        title, body,
        now=datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")),
        chat=chat, bots=bots, task=task, kind=kind, status=status)


def _coder(app: ChatApp) -> None:
    app._bots.create(BotCard(bot_id="coder", name="coder",
                             role="Coder", description="Codes.",
                             persona="PERSONA-coder"))


def test_dm_checkpoint_invisible_to_same_bot_group_prompt(
        tmp_path: Path) -> None:
    llm = _SpyLLM()
    app = _chat(tmp_path, connect=llm)
    try:
        _coder(app)
        dm = app.bot_dm("coder")
        app.turn(dm["id"], "hi dm")
        _remember(tmp_path, dm["id"], "PRIVATE-TITLE", "PRIVATE-SUMMARY",
                  bots=["coder"], task="task-aabb1122", kind="checkpoint")
        group = app.create(bots=["thyca", "coder"])
        _remember(tmp_path, group["id"], "GROUP-TITLE", "GROUP-SUMMARY",
                  bots=["coder"], task="task-aabb1122", kind="checkpoint")

        llm.payloads.clear()
        detail = app.turn(group["id"], "@coder continue task-aabb1122")
        assert detail["reply"] == "ok"
        all_prompts = "\n".join(llm.payloads)
        assert "PRIVATE-TITLE" not in all_prompts
        assert "PRIVATE-SUMMARY" not in all_prompts
        assert "GROUP-TITLE" in all_prompts or "GROUP-SUMMARY" in all_prompts
    finally:
        app.shutdown()


def test_dm_prompt_still_sees_own_checkpoint(tmp_path: Path) -> None:
    llm = _SpyLLM()
    app = _chat(tmp_path, connect=llm)
    try:
        _coder(app)
        dm = app.bot_dm("coder")
        app.turn(dm["id"], "hi dm")
        _remember(tmp_path, dm["id"], "PRIVATE-TITLE", "PRIVATE-SUMMARY",
                  bots=["coder"], task="task-aabb1122", kind="checkpoint")
        llm.payloads.clear()
        detail = app.turn(dm["id"], "what was that checkpoint?")
        assert detail["reply"] == "ok"
        all_prompts = "\n".join(llm.payloads)
        assert "PRIVATE-TITLE" in all_prompts
        assert "PRIVATE-SUMMARY" in all_prompts
    finally:
        app.shutdown()


def test_armed_recall_runs_under_group_view(tmp_path: Path) -> None:
    """Post-compact recall fires inside the bound group view: the spy
    proves the callback ran AND private lines it would return are still
    filtered out of the rendered snapshot."""
    llm = _SpyLLM()
    app = _chat(tmp_path, connect=llm)
    try:
        _coder(app)
        dm = app.bot_dm("coder")
        app.turn(dm["id"], "hi dm")
        _remember(tmp_path, dm["id"], "PRIVATE-TITLE", "PRIVATE-SUMMARY",
                  bots=["coder"], task="task-aabb1122", kind="checkpoint")
        group = app.create(bots=["thyca", "coder"])
        seen: list[str] = []
        real = app._runtime.memory_searcher

        def spy(text, bot_id):
            # A callback that honestly offers both — the view filters.
            seen.append(bot_id)
            return real(text, bot_id)

        app._runtime.memory_searcher = spy
        app._runtime._reinject_pending.add(("coder", group["id"]))
        llm.payloads.clear()
        detail = app.turn(group["id"], "@coder recap")
        assert detail["reply"] == "ok"
        assert seen == ["coder"], "armed recall callback must have run"
        all_prompts = "\n".join(llm.payloads)
        assert "PRIVATE-TITLE" not in all_prompts
        assert "PRIVATE-SUMMARY" not in all_prompts
    finally:
        app.shutdown()


def test_root_and_group_prompt_privacy(tmp_path: Path) -> None:
    """Root (thyca) in a group gets the group view: another bot's DM
    entry is invisible to root there, and the channel section rides."""
    llm = _SpyLLM()
    app = _chat(tmp_path, connect=llm)
    try:
        _coder(app)
        dm = app.bot_dm("coder")
        app.turn(dm["id"], "hi dm")
        _remember(tmp_path, dm["id"], "PRIVATE-TITLE", "PRIVATE-SUMMARY",
                  bots=["coder"], task="task-aabb1122", kind="checkpoint")
        group = app.create(bots=["thyca", "coder"])
        llm.payloads.clear()
        detail = app.turn(group["id"], "no mention -> root answers")
        assert detail["reply"] == "ok"
        all_prompts = "\n".join(llm.payloads)
        assert "PRIVATE-TITLE" not in all_prompts
        assert "PRIVATE-SUMMARY" not in all_prompts
        assert "channel=webui" in all_prompts
        assert "kind=group" in all_prompts
    finally:
        app.shutdown()


def test_meter_tokens_match_builder_filtered_hot(tmp_path: Path) -> None:
    """The context meter prices the same filtered snapshot the executor
    gets: channel brief counted, private DM content contributes zero."""
    from thyca.core.protocol import estimate_tokens as _chars_to_tokens
    from thyca.llm.prompt_manager import PromptManager

    llm = _SpyLLM()
    app = _chat(tmp_path, connect=llm)
    try:
        _coder(app)
        dm = app.bot_dm("coder")
        app.turn(dm["id"], "hi dm")
        _remember(tmp_path, dm["id"], "PRIVATE-TITLE", "PRIVATE-SUMMARY",
                  bots=["coder"], task="task-aabb1122", kind="checkpoint")
        group = app.create(bots=["thyca", "coder"])
        hot_tokens, _tools = app._overhead_tokens(
            group["id"], turn_cfg=app._current_cfg(), bot_id="thyca"
        )
        assert hot_tokens > 0
        from datetime import datetime as _dt

        from thyca.memory.visibility import (
            MemoryView,
            bound_memory_view,
        )
        from thyca.app.webui_channel import WebUIChannelAdapter

        window = WebUIChannelAdapter().window_for(
            app._sessions.store.load(group["id"]))
        from thyca.bots.workspace_context import WorkspaceContextBuilder

        with bound_memory_view(
            MemoryView(window=window, bot_id="thyca", thyca_dir=tmp_path)
        ):
            peeked = app._memory.peek(
                app._state, _dt.now(app._zone), session_id=group["id"])
            filtered = PromptManager().build(
                WorkspaceContextBuilder(tmp_path).build(
                    peeked, bot_id="thyca", window=window,
                    memory_task_text=format_task_catalog(
                        app._memory_task_rows("thyca")),
                    bot_user=app._bots.bot_user_text("thyca")))
        unfiltered = PromptManager().build(app._memory.peek(
            app._state, _dt.now(app._zone), session_id=group["id"]))
        # The metered hot includes the channel brief and excludes the
        # private DM index line that an unfiltered peek still carries.
        assert "PRIVATE-TITLE" in unfiltered
        assert "PRIVATE-TITLE" not in filtered
        assert "channel=webui" in filtered
        assert _chars_to_tokens(filtered) <= hot_tokens + 1
        assert _chars_to_tokens(filtered) != _chars_to_tokens(unfiltered)
    finally:
        app.shutdown()


def test_private_followup_full_consent_flow(tmp_path: Path) -> None:
    """group → ask_private → DM raw answer → resume → API approve →
    group gets ONLY the sanitized revision; raw text never surfaces."""
    import asyncio, json

    from thyca.bots.private_followups import PrivateFollowupStore

    llm = _SpyLLM()
    app = _chat(tmp_path, connect=llm)
    try:
        _coder(app)
        dm = app.bot_dm("coder")
        group = app.create(bots=["thyca", "coder"])
        work_id = "task-aabb1122"
        app._runtime.workspaces.store.record_task_owner(
            "coder", work_id, "coder")

        ask = app._registry.get("ask_private")
        assert ask is not None
        from thyca.bots.context import BotTurnContext, bind_bot_turn_context

        window = app._webui.window_for(
            app._sessions.store.load(group["id"]))
        ctx = BotTurnContext(
            sender_id="coder", room_id=group["id"], span_id="s1",
            trace_id="t1", thyca_dir=tmp_path, window=window)
        token = bind_bot_turn_context(ctx)
        try:
            out = asyncio.run(ask.handler(
                {"work_id": work_id, "question": "your token?"}))
        finally:
            from thyca.bots.context import reset_bot_turn_context
            reset_bot_turn_context(token)
        res = json.loads(out)
        assert res["ok"], out
        pid = res["pending_id"]

        # Group only sees the generic wait line — no question/title.
        gmsgs = app.get_payload(group["id"])["messages"]
        assert any(m["content"] == "Đang chờ thông tin riêng."
                   for m in gmsgs if m.get("meta", {}).get("bot_id"))
        assert all("your token?" not in m["content"] for m in gmsgs)

        # DM got the question; store is waiting_private.
        assert PrivateFollowupStore(tmp_path).active(
            "coder").status == "waiting_private"

        # User answers in the DM: the service consumes it as received.
        app.turn(dm["id"], "RAW-SECRET-XYZ")
        pending = PrivateFollowupStore(tmp_path).get("coder", pid)
        assert pending.status == "received"
        assert pending.answer_event_id is not None

        # Bot decides resume with a sanitized proposal.
        pf = app._registry.get("private_followup")
        window_dm = app._webui.window_for(
            app._sessions.store.load(dm["id"]))
        ctx = BotTurnContext(
            sender_id="coder", room_id=dm["id"], span_id="s2",
            trace_id="t1", thyca_dir=tmp_path, window=window_dm)
        token = bind_bot_turn_context(ctx)
        try:
            out = asyncio.run(pf.handler({
                "pending_id": pid, "action": "resume",
                "redacted_text": "user shared a token"}))
        finally:
            from thyca.bots.context import reset_bot_turn_context
            reset_bot_turn_context(token)
        assert json.loads(out)["ok"], out

        # Approve via the endpoint handler directly (same code the POST
        # route calls; the request plumbing is covered by serve tests).
        from thyca.serve.sessions_api import session_private_followup

        class _H:
            def __init__(self):
                self.code, self.body = None, None
            def _json(self, code, body):
                self.code, self.body = code, body
            def _read_json(self):
                return {"pending_id": pid, "revision": 1,
                        "choice": "approve"}

        h = _H()
        session_private_followup(h, app, dm["id"])
        assert h.code == 200, h.body
        assert h.body["status"] == "resumed"

        # Group has ONLY the sanitized text — never the raw DM answer.
        gmsgs = app.get_payload(group["id"])["messages"]
        contents = [m["content"] for m in gmsgs]
        assert any("user shared a token" in c for c in contents)
        assert all("RAW-SECRET-XYZ" not in c for c in contents)
        assert all("your token?" not in c for c in contents)
        assert PrivateFollowupStore(tmp_path).get(
            "coder", pid).status == "resumed"
    finally:
        app.shutdown()
