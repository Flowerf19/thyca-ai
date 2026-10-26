"""WS-4 integration: pending lifecycle across real turns, endpoint
errors, cancel linkage, and the WS-3 safety guards (drain crash,
group PathGuard, habits auto-block projection)."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from thyca.app.chat_app import ChatApp
from thyca.bots.card import BotCard
from thyca.config import load, save
from seeded_config import seeded_config
from thyca.llm.llm_base import ChatReply


def _today(days_ago: int = 0) -> datetime:
    """Wall-clock-relative memory seed; avoids fixed-day index bombs."""
    zone = ZoneInfo("Asia/Ho_Chi_Minh")
    return (datetime.now(zone).replace(hour=12, minute=0, second=0, microsecond=0)
            - timedelta(days=days_ago))


def _chat(tmp_path: Path, connect=None) -> ChatApp:
    save(seeded_config(), tmp_path / "config.json")
    return ChatApp(tmp_path, load(tmp_path / "config.json"), connect=connect)


class _LLM:
    def __init__(self) -> None:
        self.payloads: list[str] = []

    async def chat(self, messages, tools=None):
        self.payloads.append(
            "\n".join(str(m.content or "") for m in messages))
        return ChatReply(content="ok", model="m",
                         usage={"prompt_tokens": 5, "completion_tokens": 1,
                                "total_tokens": 6})


def _coder(app: ChatApp) -> None:
    app._bots.create(BotCard(bot_id="coder", name="coder",
                             role="Coder", description="Codes.",
                             persona="PERSONA-coder"))


def _ctx(app, tmp_path, bot: str, room_id: str, span: str = "s1"):
    from thyca.bots.context import BotTurnContext, bind_bot_turn_context

    window = app._webui.window_for(
        app._sessions.store.load(room_id))
    ctx = BotTurnContext(
        sender_id=bot, room_id=room_id, span_id=span,
        trace_id="t1", thyca_dir=tmp_path, window=window)
    return bind_bot_turn_context(ctx)


def _unctx(token) -> None:
    from thyca.bots.context import reset_bot_turn_context
    reset_bot_turn_context(token)


def _ask(app, tmp_path, work_id="task-aabb1122"):
    app._runtime.workspaces.store.record_task_owner(
        "coder", work_id, "coder")
    app.bot_dm("coder")
    group = app.create(bots=["thyca", "coder"])
    token = _ctx(app, tmp_path, "coder", group["id"])
    try:
        out = asyncio.run(app._registry.get("ask_private").handler(
            {"work_id": work_id, "question": "private q?"}))
    finally:
        _unctx(token)
    res = json.loads(out)
    assert res["ok"], out
    return group, res["pending_id"]


def _decide_via_route(app, dm_id, body):
    from thyca.serve.sessions_api import session_private_followup

    class _H:
        def __init__(self):
            self.code, self.body = None, None
        def _json(self, code, body):
            self.code, self.body = code, body
        def _read_json(self):
            return body

    h = _H()
    session_private_followup(h, app, dm_id)
    return h


def _received(app, tmp_path, group, pid, bot="coder"):
    from thyca.bots.private_followups import PrivateFollowupStore

    store = PrivateFollowupStore(tmp_path)
    store.mark_waiting(bot, pid) if store.get(
        bot, pid).status == "open" else None
    store.receive(bot, pid, answer_event_id="e-1") if store.get(
        bot, pid).status == "waiting_private" else None
    return PrivateFollowupStore(tmp_path).get(bot, pid)


def test_ready_leaves_zero_group_conclusion(tmp_path: Path) -> None:
    llm = _LLM()
    app = _chat(tmp_path, llm)
    try:
        _coder(app)
        dm = app.bot_dm("coder")
        group, pid = _ask(app, tmp_path)
        _received(app, tmp_path, group, pid)
        token = _ctx(app, tmp_path, "coder", dm["id"], "s2")
        try:
            out = asyncio.run(app._registry.get("private_followup").handler(
                {"pending_id": pid, "action": "ready"}))
        finally:
            _unctx(token)
        assert json.loads(out)["ok"], out
        before = len(app.get_payload(group["id"])["messages"])
        h = _decide_via_route(app, dm["id"], {
            "pending_id": pid, "revision": 0, "choice": "keep_private"})
        assert h.code == 200, h.body
        after = app.get_payload(group["id"])["messages"]
        assert len(after) == before
        assert all("token" not in m["content"] for m in after)
    finally:
        app.shutdown()


def test_stale_revision_and_wrong_dm_endpoint_errors(tmp_path: Path) -> None:
    app = _chat(tmp_path, _LLM())
    try:
        _coder(app)
        dm = app.bot_dm("coder")
        group, pid = _ask(app, tmp_path)
        _received(app, tmp_path, group, pid)
        token = _ctx(app, tmp_path, "coder", dm["id"], "s2")
        try:
            asyncio.run(app._registry.get("private_followup").handler(
                {"pending_id": pid, "action": "resume",
                 "redacted_text": "safe"}))
        finally:
            _unctx(token)
        h = _decide_via_route(app, dm["id"], {
            "pending_id": pid, "revision": 99, "choice": "approve"})
        assert h.code == 409, (h.code, h.body)
        h = _decide_via_route(app, group["id"], {
            "pending_id": pid, "revision": 1, "choice": "approve"})
        assert h.code == 400, (h.code, h.body)
        h = _decide_via_route(app, dm["id"], {
            "pending_id": "pf-nope", "revision": 1, "choice": "approve"})
        assert h.code == 404, (h.code, h.body)
        h = _decide_via_route(app, dm["id"], {
            "pending_id": pid, "revision": "one", "choice": "approve"})
        assert h.code == 400, (h.code, h.body)
    finally:
        app.shutdown()


def test_terminal_late_dm_is_ordinary_turn(tmp_path: Path) -> None:
    llm = _LLM()
    app = _chat(tmp_path, llm)
    try:
        _coder(app)
        dm = app.bot_dm("coder")
        group, pid = _ask(app, tmp_path)
        _received(app, tmp_path, group, pid)
        from thyca.bots.private_followups import PrivateFollowupStore
        PrivateFollowupStore(tmp_path).decide(
            "coder", pid, action="done")
        detail = app.turn(dm["id"], "late answer")
        assert detail["reply"] == "ok"
        assert PrivateFollowupStore(tmp_path).get(
            "coder", pid).status == "done"
        assert PrivateFollowupStore(tmp_path).get(
            "coder", pid).answer_event_id == "e-1"
    finally:
        app.shutdown()


def test_retry_dm_does_not_reconsume(tmp_path: Path) -> None:
    llm = _LLM()
    app = _chat(tmp_path, llm)
    try:
        _coder(app)
        dm = app.bot_dm("coder")
        group, pid = _ask(app, tmp_path)
        app.turn(dm["id"], "the answer")
        from thyca.bots.private_followups import PrivateFollowupStore
        p = PrivateFollowupStore(tmp_path).get("coder", pid)
        assert p.status == "received"
        first_ref = p.answer_event_id
        detail = app.turn(dm["id"], "the answer", retry=True)
        assert detail["reply"] == "ok"
        p2 = PrivateFollowupStore(tmp_path).get("coder", pid)
        assert p2.answer_event_id == first_ref
    finally:
        app.shutdown()


def test_origin_membership_rejects_share(tmp_path: Path) -> None:
    app = _chat(tmp_path, _LLM())
    try:
        _coder(app)
        dm = app.bot_dm("coder")
        group, pid = _ask(app, tmp_path)
        _received(app, tmp_path, group, pid)
        token = _ctx(app, tmp_path, "coder", dm["id"], "s2")
        try:
            asyncio.run(app._registry.get("private_followup").handler(
                {"pending_id": pid, "action": "resume",
                 "redacted_text": "safe"}))
        finally:
            _unctx(token)
        from thyca.serve.sessions_api import session_bots

        class _HB:
            def __init__(self):
                self.code, self.body = None, None
            def _json(self, code, body):
                self.code, self.body = code, body
            def _read_json(self):
                return {"bot_id": "coder", "action": "remove"}

        session_bots(_HB(), app, group["id"])
        h = _decide_via_route(app, dm["id"], {
            "pending_id": pid, "revision": 1, "choice": "approve"})
        assert h.code == 409, (h.code, h.body)
    finally:
        app.shutdown()


def test_app_cancel_cancels_own_pending(tmp_path: Path) -> None:
    from thyca.bots.private_followups import PrivateFollowupStore

    app = _chat(tmp_path, _LLM())
    try:
        _coder(app)
        dm = app.bot_dm("coder")
        group, pid = _ask(app, tmp_path)
        app.cancel(dm["id"])
        assert PrivateFollowupStore(tmp_path).get(
            "coder", pid).status == "cancelled"
    finally:
        app.shutdown()


def test_ask_private_denied_from_dm(tmp_path: Path) -> None:
    app = _chat(tmp_path, _LLM())
    try:
        _coder(app)
        dm = app.bot_dm("coder")
        app._runtime.workspaces.store.record_task_owner(
            "coder", "task-aabb1122", "coder")
        token = _ctx(app, tmp_path, "coder", dm["id"])
        try:
            out = asyncio.run(app._registry.get("ask_private").handler(
                {"work_id": "task-aabb1122", "question": "q"}))
        finally:
            _unctx(token)
        assert "denied" in out
    finally:
        app.shutdown()


def test_drain_matching_restores_queue_on_predicate_error() -> None:
    from thyca.bots.bus import Bus

    bus = Bus()
    bus.register("b")
    for i in range(3):
        bus.send(sender="a", recipient="b", kind="message",
                 session_id=f"s{i}", payload="p")
    calls = {"n": 0}

    def boom(item):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("predicate crash")
        return calls["n"] == 1

    with pytest.raises(RuntimeError):
        bus.drain_matching("b", boom)
    back = bus.drain("b")
    assert len(back) == 3
    assert [e.session_id for e in back] == ["s0", "s1", "s2"]


def test_group_view_denies_raw_private_paths(tmp_path: Path) -> None:
    from thyca.core.channel import WindowRef
    from thyca.memory.visibility import MemoryView, bound_memory_view
    from thyca.tools.path_guard import PathDenied, PathGuard

    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "2026-10-19.md").write_text("x", "utf-8")
    (tmp_path / "USER.md").write_text("name", "utf-8")
    guard = PathGuard(tmp_path)
    window = WindowRef(window_id="g1", channel_id="webui", kind="group",
                       participants=("thyca", "coder"))
    with bound_memory_view(
            MemoryView(window=window, bot_id="coder", thyca_dir=tmp_path)):
        for p in ("memory/2026-10-19.md", "USER.md",
                  "bots/coder/workspace/registry.json"):
            with pytest.raises(PathDenied):
                guard.deny_read(str(tmp_path / p))
        assert str(guard.deny_read(str(tmp_path / "other.txt")))
    assert str(guard.deny_read(str(tmp_path / "USER.md")))


def test_group_view_strips_auto_habits_block() -> None:
    from thyca.memory.habits import strip_auto_habits

    text = "# User\nname\n<!-- habits:auto:start -->\n- secret label\n<!-- habits:auto:end -->\nmanual tail\n"
    out = strip_auto_habits(text)
    assert "secret label" not in out
    assert "name" in out and "manual tail" in out
    broken = "# User\nname\n<!-- habits:auto:start -->\n- secret label\n"
    assert strip_auto_habits(broken) == "# User\nname\n"


def test_app_cancel_idle_dm_cancels_own_pending(tmp_path: Path) -> None:
    from thyca.bots.private_followups import PrivateFollowupStore
    from thyca.bots.card import BotCard

    app = _chat(tmp_path, _LLM())
    try:
        _coder(app)
        app._bots.create(BotCard(bot_id="helper", name="helper",
                                 role="Helper", description="h", persona=""))
        dm = app.bot_dm("coder")
        helper_dm = app.bot_dm("helper")
        group, pid = _ask(app, tmp_path)
        other = PrivateFollowupStore(tmp_path).create(
            "helper", work_id="task-bbcc2233",
            origin_window=group["id"],
            dm_window=helper_dm["id"], question="other")
        app.cancel(dm["id"])
        assert PrivateFollowupStore(tmp_path).get(
            "coder", pid).status == "cancelled"
        assert PrivateFollowupStore(tmp_path).get(
            "helper", other.pending_id).status != "cancelled"
    finally:
        app.shutdown()


def test_group_ambiguous_pending_cancel_conflicts(tmp_path: Path) -> None:
    from thyca.bots.private_followups import PrivateFollowupStore
    from thyca.app.chat_app import TurnInFlight

    app = _chat(tmp_path, _LLM())
    try:
        _coder(app)
        app._bots.create(BotCard(bot_id="helper", name="helper",
                                 role="Helper", description="h", persona=""))
        app.bot_dm("coder")
        helper_dm = app.bot_dm("helper")
        group, pid = _ask(app, tmp_path)
        store = PrivateFollowupStore(tmp_path)
        app.set_room_bots(group["id"], "helper", "add")
        store.create("helper", work_id="task-bbcc2233",
                     origin_window=group["id"], dm_window=helper_dm["id"],
                     question="other")
        from thyca.sessions import SessionBusy
        with pytest.raises((SessionBusy, TurnInFlight)):
            app.cancel(group["id"])
        assert store.get("coder", pid).status == "waiting_private"
        assert store.get("helper", store.active("helper").pending_id
                       ).status == "open"
    finally:
        app.shutdown()


def test_crash_after_post_recovers_single_row(tmp_path: Path) -> None:
    from thyca.bots.private_followups import PrivateFollowupStore

    app = _chat(tmp_path, _LLM())
    try:
        _coder(app)
        dm = app.bot_dm("coder")
        group, pid = _ask(app, tmp_path)
        _received(app, tmp_path, group, pid)
        token = _ctx(app, tmp_path, "coder", dm["id"], "s2")
        try:
            asyncio.run(app._registry.get("private_followup").handler(
                {"pending_id": pid, "action": "resume",
                 "redacted_text": "SAFE"}))
        finally:
            _unctx(token)
        orig = PrivateFollowupStore.finalize_resumed
        boom = {"called": False}

        def _boom(self, bid, pid2, **kw):
            if not boom["called"]:
                boom["called"] = True
                raise RuntimeError("crash before finalize")
            return orig(self, bid, pid2, **kw)

        import unittest.mock as mock
        with mock.patch.object(
                PrivateFollowupStore, "finalize_resumed", _boom):
            h = _decide_via_route(app, dm["id"], {
                "pending_id": pid, "revision": 1, "choice": "approve"})
        rows = [m for m in app.get_payload(group["id"])["messages"]
                if (m.get("meta") or {}).get("kind") == "private_resume"]
        assert len(rows) == 1
        assert (rows[0]["meta"] or {}).get("pending_id") == pid
        app.shutdown()
        app2 = _chat(tmp_path, _LLM())
        try:
            h = _decide_via_route(app2, dm["id"], {
                "pending_id": pid, "revision": 1, "choice": "approve"})
            assert h.code == 200, h.body
            rows = [m for m in app2.get_payload(group["id"])["messages"]
                    if (m.get("meta") or {}).get("kind") == "private_resume"]
            assert len(rows) == 1
            assert PrivateFollowupStore(tmp_path).get(
                "coder", pid).status == "resumed"
        finally:
            app2.shutdown()
    finally:
        try:
            app.shutdown()
        except Exception:
            pass


def test_concurrent_approve_single_row(tmp_path: Path) -> None:
    import threading
    app = _chat(tmp_path, _LLM())
    try:
        _coder(app)
        dm = app.bot_dm("coder")
        group, pid = _ask(app, tmp_path)
        _received(app, tmp_path, group, pid)
        token = _ctx(app, tmp_path, "coder", dm["id"], "s2")
        try:
            asyncio.run(app._registry.get("private_followup").handler(
                {"pending_id": pid, "action": "resume",
                 "redacted_text": "SAFE"}))
        finally:
            _unctx(token)
        results = []
        barrier = threading.Barrier(3)

        def approve():
            barrier.wait()
            results.append(_decide_via_route(app, dm["id"], {
                "pending_id": pid, "revision": 1, "choice": "approve"}).code)

        ts = [threading.Thread(target=approve) for _ in range(2)]
        for t in ts: t.start()
        barrier.wait()
        for t in ts: t.join(30)
        rows = [m for m in app.get_payload(group["id"])["messages"]
                if (m.get("meta") or {}).get("kind") == "private_resume"]
        assert len(rows) == 1
        assert all(c in (200, 409) for c in results)
    finally:
        app.shutdown()


def test_legacy_singleton_task_owner_via_remember(tmp_path: Path) -> None:
    app = _chat(tmp_path, _LLM())
    try:
        _coder(app)
        from thyca.memory.facade import MemoryFacade
        MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh").remember(
            "t", "b", task="task-aabb1122", bots=["coder"],
            now=_today())
        group = app.create(bots=["thyca", "coder"])
        token = _ctx(app, tmp_path, "coder", group["id"])
        try:
            out = asyncio.run(app._registry.get("ask_private").handler(
                {"work_id": "task-aabb1122", "question": "q"}))
        finally:
            _unctx(token)
        assert json.loads(out)["ok"], out
    finally:
        app.shutdown()


def test_team_ambiguous_owner_denied(tmp_path: Path) -> None:
    app = _chat(tmp_path, _LLM())
    try:
        _coder(app)
        from thyca.memory.facade import MemoryFacade
        MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh").remember(
            "t", "b", task="task-aabb1122", bots=["team", "coder"],
            now=_today())
        group = app.create(bots=["thyca", "coder"])
        token = _ctx(app, tmp_path, "coder", group["id"])
        try:
            out = asyncio.run(app._registry.get("ask_private").handler(
                {"work_id": "task-aabb1122", "question": "q"}))
        finally:
            _unctx(token)
        assert "denied" in out
    finally:
        app.shutdown()


def test_single_pending_origin_group_cancel(tmp_path: Path) -> None:
    from thyca.bots.private_followups import PrivateFollowupStore

    app = _chat(tmp_path, _LLM())
    try:
        _coder(app)
        group, pid = _ask(app, tmp_path)
        app.cancel(group["id"])
        assert PrivateFollowupStore(tmp_path).get(
            "coder", pid).status == "cancelled"
    finally:
        app.shutdown()


def test_stale_keep_and_cancel_after_new_revision_409(
        tmp_path: Path) -> None:
    app = _chat(tmp_path, _LLM())
    try:
        _coder(app)
        dm = app.bot_dm("coder")
        group, pid = _ask(app, tmp_path)
        _received(app, tmp_path, group, pid)
        for span, v in (("s2", "v1"), ("s3", "v2")):
            token = _ctx(app, tmp_path, "coder", dm["id"], span)
            try:
                asyncio.run(app._registry.get("private_followup").handler(
                    {"pending_id": pid, "action": "resume",
                     "redacted_text": v}))
            finally:
                _unctx(token)
        for choice in ("keep_private", "cancel"):
            h = _decide_via_route(app, dm["id"], {
                "pending_id": pid, "revision": 1, "choice": choice})
            assert h.code == 409, (choice, h.code, h.body)
        from thyca.bots.private_followups import PrivateFollowupStore
        p = PrivateFollowupStore(tmp_path).get("coder", pid)
        assert p.status == "received" and p.proposal_revision == 2
        assert p.proposed_redacted == "v2"
    finally:
        app.shutdown()


@pytest.mark.parametrize("sender", ["thyca", "coder"])
def test_scripted_tool_call_pipeline(tmp_path: Path, sender: str) -> None:
    from thyca.bots.private_followups import PrivateFollowupStore
    from thyca.core.protocol import ToolCall

    tool_results: list[str] = []
    hot_dms: list[str] = []
    work_id_box: list[str] = []
    resumed = {"done": False}

    class ScriptedLLM:
        def __init__(self) -> None:
            self.payloads: list[str] = []

        async def chat(self, messages, tools=None):
            joined = "\n".join(str(m.content or "") for m in messages)
            self.payloads.append(joined)
            for m in messages:
                if m.role == "tool" and isinstance(m.content, str):
                    tool_results.append(m.content)
                    try:
                        data = json.loads(m.content)
                    except Exception:
                        data = {}
                    if isinstance(data.get("task_id"), str):
                        work_id_box.append(data["task_id"])
                    if (data.get("ok") is True
                            and data.get("requested_outcome") == "resume"):
                        resumed["done"] = True
            if "pending_private=" in joined:
                hot_dms.append(joined)
                if resumed["done"]:
                    return ChatReply(content="done")
                import re as _re
                m = _re.search(r"pf-[0-9a-f]+", joined)
                if m:
                    return ChatReply(content=None, tool_calls=[ToolCall(
                        id="pf1", name="private_followup",
                        arguments={"pending_id": m.group(0),
                                   "action": "resume",
                                   "redacted_text": "SAFE-deploy"})])
                return ChatReply(content="done")
            asked = any('"pending_id"' in r for r in tool_results)
            if work_id_box and not asked:
                return ChatReply(content=None, tool_calls=[ToolCall(
                    id="t2", name="ask_private",
                    arguments={"work_id": work_id_box[0],
                               "question": "deploy secret?"})])
            if not work_id_box:
                return ChatReply(content=None, tool_calls=[ToolCall(
                    id="t1", name="memory_remember",
                    arguments={"topic": "deploy plan",
                               "summary": "deploy-steps",
                               "new_task": True})])
            return ChatReply(content="done")

    llm = ScriptedLLM()
    app = _chat(tmp_path, llm)
    try:
        if sender == "coder":
            from thyca.bots.card import BotCard
            app._bots.create(BotCard(
                bot_id="coder", name="coder", role="Coder",
                description="Codes.", persona="PERSONA-coder",
                tools=("ask_private", "private_followup",
                       "memory_remember", "task_open"),
            ))
        else:
            _coder(app)
        app.bot_dm(sender)
        group = app.create(bots=["thyca", "coder"])
        detail = app.turn(group["id"], f"@{sender} set up deploy")
        assert detail["reply"]
        assert any(r for r in tool_results), "no tool results observed"
        assert not any(
            '"ok": false' in r or "failed:" in r or "denied:" in r
            for r in tool_results), tool_results
        store = PrivateFollowupStore(tmp_path)
        pending = store.active(sender)
        assert pending is not None
        assert pending.status in ("open", "waiting_private")
        assert pending.work_id == work_id_box[0]
        dm = app.bot_dm(sender)
        dm_msgs = app.get_payload(dm["id"])["messages"]
        assert any("deploy secret" in m["content"] for m in dm_msgs)
        detail2 = app.turn(dm["id"], "RAW-SECRET")
        assert detail2["reply"] == "done"
        p2 = PrivateFollowupStore(tmp_path).get(sender, pending.pending_id)
        assert p2.status == "received"
        assert resumed["done"]
        assert any("pending_private=" in h for h in hot_dms)
        assert any(
            f"pending_private={pending.pending_id}" in h for h in hot_dms)
        assert any(f"work_id={pending.work_id}" in h for h in hot_dms)
        h = _decide_via_route(app, dm["id"], {
            "pending_id": pending.pending_id,
            "revision": p2.proposal_revision, "choice": "approve"})
        assert h.code == 200, h.body
        group_msgs = app.get_payload(group["id"])["messages"]
        assert any("SAFE-deploy" in m["content"] for m in group_msgs)
        assert not any("RAW-SECRET" in m["content"] for m in group_msgs)
        assert not any("deploy secret" in m["content"] for m in group_msgs)
        from thyca.bots.tasks import derive_open_tasks
        room = app._sessions.store.load(group["id"])
        opens = derive_open_tasks(list(room.messages), [], bot_id=sender)
        assert isinstance(opens, list)
    finally:
        app.shutdown()


def test_new_task_wrapper_records_owner(tmp_path: Path) -> None:
    app = _chat(tmp_path, _LLM())
    try:
        _coder(app)
        app.bot_dm("coder")
        group = app.create(bots=["thyca", "coder"])
        token = _ctx(app, tmp_path, "coder", group["id"])
        try:
            out = asyncio.run(app._registry.get("memory_remember").handler(
                {"topic": "deploy", "summary": "x", "new_task": True}))
        finally:
            _unctx(token)
        data = json.loads(out)
        wid = data.get("task_id") or data.get("work_id")
        assert wid and wid.startswith("task-")
        owner = app._runtime.workspaces.store.task_owner("coder", wid)
        assert owner == "coder"
    finally:
        app.shutdown()


def test_earliest_owner_wins(tmp_path: Path) -> None:
    app = _chat(tmp_path, _LLM())
    try:
        _coder(app)
        from thyca.memory.facade import MemoryFacade
        facade = MemoryFacade(
            tmp_path, timezone_name="Asia/Ho_Chi_Minh")
        facade.remember("t", "b", task="task-ccddee44",
                        bots=["coder"],
                        now=_today(2))
        facade.remember("t2", "b2", task="task-ccddee44",
                        bots=["thyca"],
                        now=_today(1))
        store = app._runtime.workspaces.store
        assert store.task_owner_fallback(
            "coder", "task-ccddee44") == "coder"
        assert store.task_owner_fallback(
            "thyca", "task-ccddee44") == "coder"
    finally:
        app.shutdown()


def test_active_turn_cancel_closes_pending(tmp_path: Path) -> None:
    import threading, time
    from thyca.bots.private_followups import PrivateFollowupStore

    started = threading.Event()
    release = threading.Event()

    class BlockedLLM:
        async def chat(self, messages, tools=None):
            started.set()
            await asyncio.to_thread(release.wait, 30)
            return ChatReply(content="unblocked")

    app = _chat(tmp_path, BlockedLLM())
    try:
        _coder(app)
        dm = app.bot_dm("coder")
        group, pid = _ask(app, tmp_path)
        err: list = []

        def run_turn():
            try:
                app.turn(dm["id"], "blocked turn")
            except Exception as exc:
                err.append(exc)

        t = threading.Thread(target=run_turn, daemon=True)
        t.start()
        assert started.wait(10)
        app.cancel(dm["id"])
        release.set()
        t.join(30)
        assert not t.is_alive()
        assert PrivateFollowupStore(tmp_path).get(
            "coder", pid).status == "cancelled"
    finally:
        release.set()
        app.shutdown()


def test_task_open_memory_task_id_links_work_item(tmp_path: Path) -> None:
    from thyca.bots.tasks import derive_open_tasks
    from thyca.bots.work_items import project_work_items
    from thyca.core.protocol import Message, ToolCall

    app = _chat(tmp_path, _LLM())
    try:
        _coder(app)
        group = app.create(bots=["thyca", "coder"])
        room = app._sessions.store.load(group["id"])
        from thyca.sessions.manager import SessionManager
        mgr = SessionManager(
            limits=app._current_cfg().effective_limits(),
            timezone_name=app._current_cfg().timeline.timezone,
            store=app._sessions.store,
        )
        mgr.load(group["id"])
        mgr.append(Message(
            role="assistant", content="",
            tool_calls=[ToolCall(
                id="c1", name="task_open",
                arguments={"title": "deploy",
                           "memory_task_id": "task-aabb1122"})],
            meta={"bot_id": "coder"}))
        mgr.append(Message(
            role="tool", tool_call_id="c1",
            content='{"ok": true, "task": "deploy", "key": "k1"}',
            meta={"bot_id": "coder"}))
        opens = derive_open_tasks(
            list(app._sessions.store.load(group["id"]).messages),
            [], bot_id="coder")
        assert any(t.memory_task_id == "task-aabb1122" for t in opens)
        items = project_work_items(opens, [
            {"task_id": "task-aabb1122", "title": "deploy",
             "status": "open"}], owner_bot="coder")
        linked = [i for i in items if i.memory_task_id == "task-aabb1122"]
        assert linked and linked[0].work_id == "task-aabb1122"
    finally:
        app.shutdown()


def test_task_open_memory_task_id_pipeline(tmp_path: Path) -> None:
    from thyca.bots.tasks import derive_open_tasks
    from thyca.bots.work_items import project_work_items
    from thyca.core.protocol import ToolCall

    calls = {"n": 0}

    class OpenLLM:
        async def chat(self, messages, tools=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return ChatReply(content=None, tool_calls=[ToolCall(
                    id="to1", name="task_open",
                    arguments={"title": "deploy",
                               "memory_task_id": "task-aabb1122"})])
            if any(m.role == "tool" and "task-aabb1122" in (
                    m.content or "") for m in messages):
                return ChatReply(content="linked")
            return ChatReply(content="linked")

    app = _chat(tmp_path, OpenLLM())
    try:
        _coder(app)
        group = app.create(bots=["thyca", "coder"])
        detail = app.turn(group["id"], "@coder open deploy")
        assert detail["reply"] == "linked"
        internal = app._runtime.internal_session("coder", group["id"])
        opens = derive_open_tasks(
            list(internal.messages), [], bot_id="coder")
        assert any(t.memory_task_id == "task-aabb1122" for t in opens)
        items = project_work_items(
            opens,
            [{"task_id": "task-aabb1122", "title": "deploy",
              "status": "open"}],
            owner_bot="coder")
        assert any(i.memory_task_id == "task-aabb1122"
                   and i.work_id == "task-aabb1122" for i in items)
    finally:
        app.shutdown()


def test_task_open_bad_memory_task_id_rejected(tmp_path: Path) -> None:
    app = _chat(tmp_path, _LLM())
    try:
        _coder(app)
        group = app.create(bots=["thyca", "coder"])
        token = _ctx(app, tmp_path, "coder", group["id"])
        try:
            out = asyncio.run(app._registry.get("task_open").handler(
                {"title": "x", "memory_task_id": "task-bad"}))
        finally:
            _unctx(token)
        assert "invalid" in out
    finally:
        app.shutdown()


def test_ask_private_group_survives_first_dm(tmp_path: Path) -> None:
    from thyca.bots.private_followups import PrivateFollowupStore

    app = _chat(tmp_path, _LLM())
    try:
        _coder(app)
        app._runtime.workspaces.store.record_task_owner(
            "coder", "task-aabb1122", "coder")
        group = app.create(bots=["thyca", "coder"])
        token = _ctx(app, tmp_path, "coder", group["id"])
        try:
            out = asyncio.run(app._registry.get("ask_private").handler(
                {"work_id": "task-aabb1122", "question": "secret?"}))
        finally:
            _unctx(token)
        assert json.loads(out)["ok"], out
        session = app._sessions.store.load(group["id"])
        assert session is not None
    finally:
        app.shutdown()


def test_pending_group_survives_other_bot_dm(tmp_path: Path) -> None:
    from thyca.bots.card import BotCard
    from thyca.bots.private_followups import PrivateFollowupStore

    app = _chat(tmp_path, _LLM())
    try:
        _coder(app)
        app._bots.create(BotCard(
            bot_id="helper", name="helper", role="Helper",
            description="h", persona=""))
        group, pid = _ask(app, tmp_path)
        app.bot_dm("helper")
        session = app._sessions.store.load(group["id"])
        assert session is not None
        assert PrivateFollowupStore(tmp_path).get(
            "coder", pid).status == "waiting_private"
    finally:
        app.shutdown()
