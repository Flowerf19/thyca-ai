"""WS-5: scheduler system turns, wire metadata, workspace status."""
from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

import pytest

from thyca.app.chat_app import ChatApp
from thyca.bots.card import BotCard
from thyca.config import load, save
from seeded_config import seeded_config
from thyca.llm.llm_base import ChatReply


def _chat(tmp_path: Path, connect=None) -> ChatApp:
    save(seeded_config(), tmp_path / "config.json")
    return ChatApp(tmp_path, load(tmp_path / "config.json"), connect=connect)


class _LLM:
    async def chat(self, messages, tools=None):
        return ChatReply(content="ok", model="m",
                         usage={"prompt_tokens": 5, "completion_tokens": 1,
                                "total_tokens": 6})


def _coder(app: ChatApp, schedule=None) -> None:
    app._bots.create(BotCard(
        bot_id="coder", name="coder", role="Coder",
        description="Codes.", persona="P", schedule=schedule))


def test_scheduled_turn_lands_in_dm_with_system_meta(tmp_path: Path) -> None:
    app = _chat(tmp_path, _LLM())
    try:
        _coder(app)
        detail = app.scheduled_turn("coder", "[scheduled * * * * *] tick")
        assert detail["reply"] == "ok"
        dm = app.bot_dm("coder")
        msgs = app.get_payload(dm["id"])["messages"]
        user = next(m for m in msgs if m["role"] == "user")
        assert (user.get("meta") or {}).get("actor_kind") == "system"
        assert (user.get("meta") or {}).get("channel_id") == "webui"
        asst = next(m for m in msgs if m["role"] == "assistant")
        assert (asst.get("meta") or {}).get("channel") == "webui"
    finally:
        app.shutdown()


def test_scheduler_skip_busy_no_row(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()

    class Blocked:
        async def chat(self, messages, tools=None):
            started.set()
            await asyncio.to_thread(release.wait, 30)
            return ChatReply(content="unblocked")

    app = _chat(tmp_path, Blocked())
    try:
        _coder(app)
        dm = app.bot_dm("coder")
        before = len(app.get_payload(dm["id"])["messages"])
        t = threading.Thread(
            target=lambda: app.turn(dm["id"], "busy"), daemon=True)
        t.start()
        assert started.wait(10)
        from thyca.sessions import SessionBusy
        with pytest.raises(SessionBusy):
            app.scheduled_turn("coder", "tick while busy")
        mid = len(app.get_payload(dm["id"])["messages"])
        assert mid == before + 1
        release.set()
        t.join(30)
    finally:
        release.set()
        app.shutdown()


def test_scheduler_tick_uses_scheduled_turn(tmp_path: Path) -> None:
    from thyca.bots.scheduler import tick

    seen: list[tuple[str, str]] = []

    class Fake:
        def sweep_idle_sessions(self, idle_s=900):
            return {}
        _bots = None

        def scheduled_turn(self, bot_id, text):
            seen.append((bot_id, text))
            return {"id": "room-1", "reply": "r"}

    fake = Fake()

    class _CardDoc:
        ok = True
        card = BotCard(bot_id="coder", name="c", role="r",
                       description="d", persona="", schedule="* * * * *")

    fake._bots = type("B", (), {"list": lambda self: [_CardDoc()]})()
    report = tick(fake)
    assert seen and seen[0][0] == "coder"
    assert report["fired"][0]["room"] == "room-1"


def test_session_wire_metadata(tmp_path: Path) -> None:
    app = _chat(tmp_path, _LLM())
    try:
        _coder(app)
        group = app.create(bots=["thyca", "coder"])
        from thyca.sessions.wire import session_summary
        row = session_summary(app._sessions.store.load(group["id"]))
        assert row["channel"] == "webui" and row["window_id"] == group["id"]
        detail = app.get_payload(group["id"])
        assert detail["channel"] == "webui" and detail["window_id"] == group["id"]
    finally:
        app.shutdown()


def test_turn_summary_channel_window(tmp_path: Path) -> None:
    from thyca.serve.trace import turns_from_session

    app = _chat(tmp_path, _LLM())
    try:
        _coder(app)
        group = app.create(bots=["thyca", "coder"])
        app.turn(group["id"], "hi")
        session = app._sessions.store.load(group["id"])
        turns = turns_from_session(session)
        assert turns
        assert turns[0].channel == "webui"
        assert turns[0].window_id == group["id"]
        payload = turns[0].to_payload()
        assert payload["channel"] == "webui"
        assert "work_id" in payload
    finally:
        app.shutdown()


def test_private_resume_row_requests_zero(tmp_path: Path) -> None:
    app = _chat(tmp_path, _LLM())
    try:
        _coder(app)
        app._runtime.workspaces.store.record_task_owner(
            "coder", "task-aabb1122", "coder")
        app.bot_dm("coder")
        group = app.create(bots=["thyca", "coder"])
        from thyca.bots.private_followups import PrivateFollowupStore
        from thyca.app.private_followups import PrivateFollowupService
        from thyca.bots.context import BotTurnContext, bind_bot_turn_context, reset_bot_turn_context

        store = PrivateFollowupStore(tmp_path)
        dm = app.bot_dm("coder")
        p = store.create("coder", work_id="task-aabb1122",
                         origin_window=group["id"], dm_window=dm["id"],
                         question="q")
        store.mark_waiting("coder", p.pending_id)
        store.receive("coder", p.pending_id, answer_event_id="e1")
        store.decide("coder", p.pending_id, action="resume",
                     redacted="SAFE")
        h = PrivateFollowupService(app).decide(
            dm["id"], p.pending_id, 1, "approve")
        assert h["ok"]
        rows = [m for m in app.get_payload(group["id"])["messages"]
                if (m.get("meta") or {}).get("kind") == "private_resume"]
        assert rows and (rows[0]["meta"] or {}).get("requests") == 0
    finally:
        app.shutdown()


def test_workspace_status_pending_and_helpers(tmp_path: Path) -> None:
    from thyca.bots.status import workspace_status
    from thyca.bots.private_followups import Pending

    snap = {"state": "idle", "room_id": "g1", "trace_id": None,
            "span_id": None, "phase": None, "waiting_on": None}
    p = Pending(
        pending_id="pf-1", bot_id="coder", work_id="task-aabb1122",
        origin_window="g1", dm_window="d1", status="waiting_private",
        created_at="t", updated_at="t", question="secret")
    out = workspace_status(snap, pending=p)
    assert out["state"] == "waiting"
    assert out["phase"] == "private"
    assert out["waiting_on"] == {"kind": "private",
                               "state": "waiting_private"}
    assert out["work_id"] == "task-aabb1122"
    assert "secret" not in json.dumps(out)
    active = dict(snap, state="thinking", room_id="g1")
    out2 = workspace_status(active, pending=p)
    assert out2["state"] == "thinking" and out2["work_id"] == "task-aabb1122"
    other = dict(snap, state="thinking", room_id="other")
    out3 = workspace_status(other, pending=p)
    assert out3["work_id"] is None
    offline = dict(snap, state="offline")
    out4 = workspace_status(offline, pending=p)
    assert out4["state"] == "offline"


def test_bots_list_reconstructs_waiting(tmp_path: Path) -> None:
    from thyca.bots.private_followups import PrivateFollowupStore

    app = _chat(tmp_path, _LLM())
    try:
        _coder(app)
        dm = app.bot_dm("coder")
        store = PrivateFollowupStore(tmp_path)
        p = store.create("coder", work_id="task-aabb1122",
                         origin_window=app.create(bots=["thyca","coder"])["id"],
                         dm_window=dm["id"],
                         question="q")
        store.mark_waiting("coder", p.pending_id)
        bots = app.bots_list()
        coder = next(b for b in bots if b["id"] == "coder")
        st = coder["agent_status"]
        assert st["state"] == "waiting" and st["phase"] == "private"
        assert st["pending_private_id"] == p.pending_id
        store.cancel("coder", p.pending_id)
        bots2 = app.bots_list()
        coder2 = next(b for b in bots2 if b["id"] == "coder")
        assert coder2["agent_status"]["state"] != "waiting" or \
            coder2["agent_status"].get("phase") != "private"
    finally:
        app.shutdown()


def test_solo_room_fallback_unchanged() -> None:
    from thyca.bots.scheduler import _solo_room

    calls = []

    class Legacy:
        _sessions = type("S", (), {"list_sessions": lambda s: []})()

        def create(self, bots=None):
            calls.append(bots)
            return {"id": "new-1"}

        def turn(self, room, text):
            return {"reply": "ok"}

    assert _solo_room(Legacy(), "x") == "new-1"


def test_scheduler_skips_when_bot_busy_in_other_room(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()

    class Blocked:
        async def chat(self, messages, tools=None):
            started.set()
            await asyncio.to_thread(release.wait, 30)
            return ChatReply(content="unblocked")

    app = _chat(tmp_path, Blocked())
    try:
        _coder(app)
        app.bot_dm("coder")
        group = app.create(bots=["thyca", "coder"])
        dm_before = app.get_payload(app.bot_dm("coder")["id"])["messages"]
        t = threading.Thread(
            target=lambda: app.turn(group["id"], "@coder hold"),
            daemon=True)
        t.start()
        assert started.wait(10)
        from thyca.sessions import SessionBusy
        with pytest.raises(SessionBusy):
            app.scheduled_turn("coder", "tick")
        dm_after = app.get_payload(app.bot_dm("coder")["id"])["messages"]
        assert len(dm_after) == len(dm_before)
        release.set()
        t.join(30)
        assert not t.is_alive()
    finally:
        release.set()
        app.shutdown()


def test_scheduled_turn_leaves_pending_untouched(tmp_path: Path) -> None:
    from thyca.bots.private_followups import PrivateFollowupStore

    app = _chat(tmp_path, _LLM())
    try:
        _coder(app)
        dm = app.bot_dm("coder")
        group = app.create(bots=["thyca", "coder"])
        store = PrivateFollowupStore(tmp_path)
        p = store.create("coder", work_id="task-aabb1122",
                         origin_window=group["id"], dm_window=dm["id"],
                         question="secret?")
        store.mark_waiting("coder", p.pending_id)
        app.scheduled_turn("coder", "tick")
        p2 = PrivateFollowupStore(tmp_path).get("coder", p.pending_id)
        assert p2.status == "waiting_private"
        assert p2.answer_ref is None
        rows = app.get_payload(dm["id"])["messages"]
        assert any((m.get("meta") or {}).get("actor_kind") == "system"
                   for m in rows)
    finally:
        app.shutdown()


def test_waiting_status_reconstructed_after_restart(tmp_path: Path) -> None:
    from thyca.bots.private_followups import PrivateFollowupStore

    app = _chat(tmp_path, _LLM())
    _coder(app)
    dm = app.bot_dm("coder")
    group = app.create(bots=["thyca", "coder"])
    store = PrivateFollowupStore(tmp_path)
    p = store.create("coder", work_id="task-aabb1122",
                     origin_window=group["id"], dm_window=dm["id"],
                     question="q")
    store.mark_waiting("coder", p.pending_id)
    app.shutdown()
    app2 = _chat(tmp_path, _LLM())
    try:
        bots = app2.bots_list()
        coder = next(b for b in bots if b["id"] == "coder")
        st = coder["agent_status"]
        assert st["state"] == "waiting" and st["phase"] == "private"
        assert st["pending_private_id"] == p.pending_id
        assert "q" not in json.dumps(st)
    finally:
        app2.shutdown()


def test_workspace_status_ignores_terminal_pending(tmp_path: Path) -> None:
    from thyca.bots.status import workspace_status
    from thyca.bots.private_followups import Pending

    p = Pending(
        pending_id="pf-1", bot_id="coder", work_id="task-aabb1122",
        origin_window="2026-10-19T09-00-00_aa11",
        dm_window="2026-10-19T09-01-00_bb22", status="cancelled",
        created_at="t", updated_at="t", question="x")
    snap = {"state": "idle", "room_id": None}
    out = workspace_status(snap, pending=p)
    assert out["state"] == "idle"
    assert "pending_private_id" not in out


@pytest.mark.parametrize("sender", ["thyca", "coder"])
def test_trusted_work_ref_on_user_and_assistant_rows(
        tmp_path: Path, sender: str) -> None:
    from thyca.core.channel import ActorRef, ChannelEvent
    from thyca.core.protocol import utc_now_ts

    app = _chat(tmp_path, _LLM())
    try:
        if sender == "coder":
            _coder(app)
        dm = app.bot_dm(sender)
        session = app._sessions.store.load(dm["id"])
        window = app._webui.window_for(session)
        event = ChannelEvent(
            event_id="ev-trace1", channel_id="webui",
            window_id=dm["id"],
            actor=ActorRef(kind="user", id="user"),
            text="do it",
            work_ref="task-aabb1122",
            received_at=utc_now_ts(),
        )
        detail = app._turn_service.run(event, window=window)
        assert detail["reply"] == "ok"
        msgs = app.get_payload(dm["id"])["messages"]
        user = next(m for m in msgs if m["role"] == "user")
        meta = user.get("meta") or {}
        assert meta.get("work_id") == "task-aabb1122"
        assert meta.get("channel_id") == "webui"
        assert meta.get("window_id") == dm["id"]
        asst = next(m for m in msgs if m["role"] == "assistant")
        ameta = asst.get("meta") or {}
        assert ameta.get("channel") == "webui"
        assert ameta.get("window_id") == dm["id"]
        assert ameta.get("work_id") == "task-aabb1122"
    finally:
        app.shutdown()


@pytest.mark.parametrize("sender", ["thyca", "coder"])
def test_scheduled_turn_never_consumes_user_pending(
        tmp_path: Path, sender: str) -> None:
    from thyca.bots.private_followups import PrivateFollowupStore

    app = _chat(tmp_path, _LLM())
    try:
        if sender == "coder":
            _coder(app)
        dm = app.bot_dm(sender)
        group = app.create(
            bots=["thyca"] + (["coder"] if sender == "coder" else []))
        store = PrivateFollowupStore(tmp_path)
        p = store.create(sender, work_id="task-aabb1122",
                         origin_window=group["id"], dm_window=dm["id"],
                         question="secret?")
        store.mark_waiting(sender, p.pending_id)
        app.scheduled_turn(sender, "tick")
        p2 = PrivateFollowupStore(tmp_path).get(sender, p.pending_id)
        assert p2.status == "waiting_private"
        assert p2.answer_ref is None
    finally:
        app.shutdown()
