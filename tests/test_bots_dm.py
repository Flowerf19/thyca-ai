"""CBE-5: one fixed DM per bot; scheduler/independent results land there."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import pytest as _pytest
from seeded_config import seeded_config

from thyca.app.chat_app import ChatApp
from thyca.bots.card import BotCard
from thyca.bots.registry import RegistryError
from thyca.bots.scheduler import tick
from thyca.configs import load, save
from thyca.conversations.runtime import ConversationNotFound
from thyca.llms.llm_base import ChatReply
from thyca.serve.conversations_api import bot_dm as dm_endpoint


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
        assert first["conversation_id"] == second["conversation_id"]
        assert first["dm"] == "coder"
        assert first["bots"] == ["coder"]
        app.turn(first["conversation_id"], "hello dm")
        listed = {s["conversation_id"]: s for s in app.list_payload()["conversations"]}
        assert listed[first["conversation_id"]]["dm"] == "coder"
        store = app._conversations.store
        conversation = store.load(first["conversation_id"])
        store.append_bots(conversation.path, ["coder"])
        assert store.load(first["conversation_id"]).dm_bot == "coder"
        assert store.load(first["conversation_id"]).bots == ["coder"]
    finally:
        app.shutdown()


def test_bot_dm_unknown_bot_raises(tmp_path: Path) -> None:
    app = _chat(tmp_path, connect=_LLM())
    try:


        with pytest.raises(RegistryError):
            app.bot_dm("ghost")
    finally:
        app.shutdown()


def test_bot_dm_ignores_unmarked_solo_room(tmp_path: Path) -> None:
    app = _chat(tmp_path, connect=_LLM())
    try:
        _make_bot(app)
        raw = app._conversations.create()
        app._conversations.store.append_bots(raw.path, ["coder"])
        assert app._conversations.store.load(raw.conversation_id).dm_bot is None
        dm = app.bot_dm("coder")
        assert dm["conversation_id"] != raw.conversation_id
        assert dm["dm"] == "coder"
        assert dm["kind"] == "dm"
    finally:
        app.shutdown()


def test_scheduled_result_lands_in_dm_not_in_room(tmp_path: Path) -> None:
    app = _chat(tmp_path, connect=_LLM())
    try:
        _make_bot(app, schedule="* * * * *")
        room = app.create(bots=["thyca", "coder"])["conversation_id"]
        app.turn(room, "hi coder in room")
        dm_id = app.bot_dm("coder")["conversation_id"]
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
        assert all("hi coder in room" not in str(row.get("content") or "")
                   for row in dm_rows)
    finally:
        app.shutdown()


def test_dm_route_shape(tmp_path: Path) -> None:

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
        dm_endpoint(handler, app, "coder")  # type: ignore[arg-type] -- reason: intentional invalid-type or test-double argument
        assert handler.status == 200
        assert handler.payload["kind"] == "dm"
        assert handler.payload["bot_id"] == "coder"
        assert handler.payload["bots"] == ["coder"]
        assert json.dumps(handler.payload)

        missing = Handler()
        dm_endpoint(missing, app, "ghost")  # type: ignore[arg-type] -- reason: intentional invalid-type or test-double argument
        assert missing.status == 404
    finally:
        app.shutdown()


def test_bot_dm_lookup_does_not_prune_blank_group(tmp_path: Path) -> None:
    app = _chat(tmp_path, connect=_LLM())
    try:
        _make_bot(app)
        _make_bot(app, bot_id="helper")
        dm = app.bot_dm("coder")
        group = app.create(bots=["coder", "helper"])
        assert app.bot_dm("coder")["conversation_id"] == dm["conversation_id"]
        assert app._conversations.store.load(group["conversation_id"]).conversation_id == group["conversation_id"]
    finally:
        app.shutdown()


def test_first_bot_dm_prunes_previous_blank(tmp_path: Path) -> None:
    app = _chat(tmp_path, connect=_LLM())
    try:
        _make_bot(app)
        stray = app._conversations.create()
        assert app._conversations.store.load(stray.conversation_id).conversation_id == stray.conversation_id
        dm = app.bot_dm("coder")
        assert dm["dm"] == "coder"


        with _pytest.raises(ConversationNotFound):
            app._conversations.store.load(stray.conversation_id)
    finally:
        app.shutdown()
