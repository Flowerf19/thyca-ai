"""CBE-5: one fixed DM per bot; scheduler/independent results land there."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from thyca.app.chat_app import ChatApp
from thyca.bots.card import BotCard
from thyca.bots.scheduler import tick
from thyca.config import load, save
from seeded_config import seeded_config
from thyca.llm.llm_base import ChatReply


def _chat(tmp_path: Path, connect=None) -> ChatApp:
    save(seeded_config(), tmp_path / "config.json")
    return ChatApp(tmp_path, load(tmp_path / "config.json"), connect=connect)


class _LLM:
    async def chat(self, messages, tools=None):
        return ChatReply(content="bot reply", model="m",
                         usage={"prompt_tokens": 5, "completion_tokens": 1,
                                "total_tokens": 6})


def _make_bot(app: ChatApp, bot_id: str = "coder", schedule: str | None = None) -> None:
    app._bots.create(BotCard(bot_id=bot_id, name=bot_id, role="Coder",
                             description="Writes code.", persona=f"PERSONA-{bot_id}",
                             schedule=schedule))


def test_bot_dm_is_lazy_singleton_and_marked(tmp_path: Path) -> None:
    app = _chat(tmp_path, connect=_LLM())
    try:
        _make_bot(app)
        first = app.bot_dm("coder")
        second = app.bot_dm("coder")
        assert first["id"] == second["id"]
        assert first["dm"] == "coder"
        assert first["bots"] == ["coder"]
        # The list payload carries the marker too, so FE can pin it
        # (blank sessions are pruned from lists, so give the DM a turn).
        app.turn(first["id"], "hello dm")
        listed = {s["id"]: s for s in app.list_payload()["sessions"]}
        assert listed[first["id"]]["dm"] == "coder"
        # Marker survives a session rewrite (compaction path rebuilds meta).
        store = app._sessions.store
        session = store.load(first["id"])
        store.rewrite(session.id, session.path, list(session.messages),
                      title=session.title)
        assert store.load(first["id"]).dm_bot == "coder"
    finally:
        app.shutdown()


def test_bot_dm_unknown_bot_raises(tmp_path: Path) -> None:
    app = _chat(tmp_path, connect=_LLM())
    try:
        import pytest

        from thyca.bots.registry import RegistryError

        with pytest.raises(RegistryError):
            app.bot_dm("ghost")
    finally:
        app.shutdown()


def test_bot_dm_ignores_unmarked_solo_room(tmp_path: Path) -> None:
    app = _chat(tmp_path, connect=_LLM())
    try:
        _make_bot(app)
        # Raw unmarked solo room (pre-CBE-5 style): phase 2B treats it as
        # an ordinary group, never adopts it as the DM.
        raw = app._sessions.create()
        app._sessions.store.append_bots(raw.path, ["coder"])
        assert app._sessions.store.load(raw.id).dm_bot is None
        dm = app.bot_dm("coder")
        assert dm["id"] != raw.id
        assert dm["dm"] == "coder"
        assert dm["kind"] == "dm"
    finally:
        app.shutdown()


def test_scheduled_result_lands_in_dm_not_in_room(tmp_path: Path) -> None:
    app = _chat(tmp_path, connect=_LLM())
    try:
        _make_bot(app, schedule="* * * * *")
        # A shared room the bot also works in.
        room = app.create(bots=["thyca", "coder"])["id"]
        app.turn(room, "hi coder in room")
        dm_id = app.bot_dm("coder")["id"]
        assert dm_id != room

        report = tick(app, now=datetime(2026, 8, 1, 10, 0,
                                         tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")))
        assert [fired["bot_id"] for fired in report["fired"]] == ["coder"]
        assert report["fired"][0]["room"] == dm_id

        dm_rows = app.get_payload(dm_id)["messages"]
        assert any(
            (row.get("meta") or {}).get("bot_id") == "coder"
            and row["content"] == "bot reply"
            for row in dm_rows
        ), "scheduled result must be appended into the bot DM by the app"
        # Room work stays in the room: the room turn did not leak into the DM
        # before the scheduled fire (user row text differs).
        assert all("hi coder in room" not in str(row.get("content") or "")
                   for row in dm_rows)
    finally:
        app.shutdown()


def test_dm_route_shape(tmp_path: Path) -> None:
    # Route-level: GET /api/bots/<id>/dm returns the session detail; the
    # handler is exercised through sessions_api.bot_dm with a stub handler.
    from thyca.serve.sessions_api import bot_dm as dm_endpoint

    app = _chat(tmp_path, connect=_LLM())
    try:
        _make_bot(app)

        class Handler:
            def __init__(self) -> None:
                self.status = None
                self.payload = None

            def _json(self, status, payload) -> None:
                self.status = status
                self.payload = payload

        handler = Handler()
        dm_endpoint(handler, app, "coder")  # type: ignore[arg-type]
        assert handler.status == 200
        assert handler.payload["dm"] == "coder"
        assert handler.payload["bots"] == ["coder"]
        assert json.dumps(handler.payload)  # serializable, no internals

        missing = Handler()
        dm_endpoint(missing, app, "ghost")  # type: ignore[arg-type]
        assert missing.status == 404
    finally:
        app.shutdown()


def test_bot_dm_lookup_does_not_prune_blank_group(tmp_path: Path) -> None:
    app = _chat(tmp_path, connect=_LLM())
    try:
        _make_bot(app)
        _make_bot(app, bot_id="helper")
        dm = app.bot_dm("coder")  # DM exists from here on.
        group = app.create(bots=["coder", "helper"])  # blank group
        assert app.bot_dm("coder")["id"] == dm["id"]
        assert (tmp_path / "sessions" / f"{group['id']}.jsonl").exists()
    finally:
        app.shutdown()


def test_first_bot_dm_prunes_previous_blank(tmp_path: Path) -> None:
    app = _chat(tmp_path, connect=_LLM())
    try:
        _make_bot(app)
        stray = app._sessions.create()
        assert stray.path.is_file()
        dm = app.bot_dm("coder")  # first lookup: create path prunes.
        assert dm["dm"] == "coder"
        assert not stray.path.exists()
    finally:
        app.shutdown()
