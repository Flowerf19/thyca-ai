"""BE-3/BE-6: rooms (participants, routing) + trace/meta attribution."""
from __future__ import annotations

from pathlib import Path

import pytest

from thyca.app.chat_app import ChatApp
from thyca.bots.registry import RegistryError
from thyca.config import default_config, load, save
from thyca.core.protocol import Message
from thyca.llm.llm_base import ChatReply
from thyca.serve.trace import aggregate, turns_from_session
from thyca.sessions import Session


def _chat(tmp_path: Path, connect=None) -> ChatApp:
    save(default_config(), tmp_path / "config.json")
    return ChatApp(tmp_path, load(tmp_path / "config.json"), connect=connect)


def _coder(app: ChatApp) -> None:
    from thyca.bots.card import BotCard

    app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                             description="Codes.", persona="You code."))


def test_create_accepts_bots_and_payload_carries_them(tmp_path: Path) -> None:
    app = _chat(tmp_path)
    try:
        _coder(app)
        detail = app.create(bots=["thyca", "coder"])
        assert detail["bots"] == ["thyca", "coder"]
        assert detail["kind"] == "group"
        assert app.get_payload(detail["id"])["bots"] == ["thyca", "coder"]
        # No bots: Thyca's DM (phase 2B), not a plain session.
        plain = app.create()
        assert plain["bots"] == ["thyca"]
        assert plain["dm"] == "thyca"
        assert plain["kind"] == "dm"
    finally:
        app.shutdown()


def test_create_single_bot_resolves_to_that_dm(tmp_path: Path) -> None:
    app = _chat(tmp_path)
    try:
        _coder(app)
        first = app.create(bots=["coder"])
        second = app.create(bots=["coder"])
        assert first["id"] == second["id"]
        assert first["dm"] == "coder"
        assert first["kind"] == "dm"
        # Duplicate ids in one call dedupe to the same DM.
        assert app.create(bots=["coder", "coder"])["id"] == first["id"]
        # Two-bot calls always mint fresh groups.
        g1 = app.create(bots=["thyca", "coder"])["id"]
        g2 = app.create(bots=["thyca", "coder"])["id"]
        assert g1 != g2
    finally:
        app.shutdown()


def test_create_rejects_unknown_or_paused_bot(tmp_path: Path) -> None:
    app = _chat(tmp_path)
    try:
        with pytest.raises(RegistryError):
            app.create(bots=["ghost"])
        _coder(app)
        app._bots.set_paused("coder", True)
        with pytest.raises(RegistryError):
            app.create(bots=["coder"])
    finally:
        app.shutdown()


def test_add_remove_bot_round_trip(tmp_path: Path) -> None:
    app = _chat(tmp_path)
    try:
        _coder(app)
        # Add/remove runs on a group (create() with no bots is Thyca's DM).
        created = app.create(bots=["thyca", "coder"])
        assert created["kind"] == "group"
        assert app.set_room_bots(created["id"], "coder", "remove") == ["thyca"]
        assert app.room_bots(created["id"]) == ["thyca"]
        assert app.set_room_bots(created["id"], "coder", "add") == ["thyca", "coder"]
        assert app.set_room_bots(created["id"], "coder", "remove") == ["thyca"]
        with pytest.raises(RegistryError):
            app.set_room_bots(created["id"], "thyca", "remove")
        with pytest.raises(RegistryError):
            app.set_room_bots(created["id"], "ghost", "add")
        with pytest.raises(ValueError):
            app.set_room_bots(created["id"], "coder", "kick")
    finally:
        app.shutdown()


def test_responder_resolution() -> None:
    resolve = ChatApp._resolve_responder
    assert resolve(["thyca"], "hello") == "thyca"
    assert resolve(["coder"], "hello") == "coder"
    assert resolve(["thyca", "coder"], "hey @coder look") == "coder"
    assert resolve(["thyca", "coder"], "hello all") == "thyca"
    assert resolve(["thyca", "coder"], "hi @ghost") == "thyca"


def test_bot_turn_records_attributed_room_rows(tmp_path: Path, monkeypatch) -> None:
    class FakeLLM:
        async def chat(self, messages, tools=None):
            return ChatReply(content="bot reply", model="m")

    app = _chat(tmp_path, connect=FakeLLM())
    try:
        _coder(app)
        created = app.create(bots=["coder"])
        detail = app.turn(created["id"], "do the thing")
        assert detail["reply"] == "bot reply"
        messages = detail["messages"]
        bot_rows = [m for m in messages
                    if isinstance(m.get("meta"), dict) and m["meta"].get("bot_id") == "coder"]
        assert bot_rows, "room transcript must carry the bot's reply row"
        reply = bot_rows[-1]
        assert reply["meta"]["kind"] == "message"
        assert reply["meta"]["trace_id"].startswith("tr_")
        assert detail["bots"] == ["coder"]
    finally:
        app.shutdown()


def test_thyca_room_uses_default_path(tmp_path: Path) -> None:
    class FakeLLM:
        def __init__(self) -> None:
            self.calls = 0

        async def chat(self, messages, tools=None):
            self.calls += 1
            return ChatReply(content="thyca reply", model="m")

    llm = FakeLLM()
    app = _chat(tmp_path, connect=llm)
    try:
        detail = app.turn(app.create()["id"], "hi")
        assert detail["reply"] == "thyca reply"
        assert llm.calls == 1
    finally:
        app.shutdown()


def _session_with_bot_rows() -> Session:
    return Session(
        id="s1", path=Path("/tmp/x.jsonl"),
        messages=[
            Message(role="user", content="hi", ts="2026-08-01T10:00:00Z"),
            Message(role="assistant", content="a", ts="2026-08-01T10:00:01Z",
                    meta={"kind": "llm", "bot_id": "coder", "trace_id": "tr_1",
                          "model": "m", "usage": {"prompt_tokens": 5},
                          "cost_usd": 0.01}),
            Message(role="user", content="yo", ts="2026-08-01T10:01:00Z"),
            Message(role="assistant", content="b", ts="2026-08-01T10:01:01Z",
                    meta={"kind": "llm", "model": "m"}),
        ],
    )


def test_trace_groups_by_bot() -> None:
    turns = turns_from_session(_session_with_bot_rows())
    assert turns[0].bot_id == "coder"
    assert turns[0].trace_id == "tr_1"
    assert turns[0].to_payload()["bot_id"] == "coder"
    assert turns[1].bot_id is None
    data = aggregate(turns)
    by_bot = {row["bot_id"]: row for row in data["by_bot"]}
    assert by_bot["coder"]["requests"] == 1
    assert by_bot["unknown"]["requests"] == 1


def test_wire_kind_dm_group_legacy(tmp_path: Path) -> None:
    app = _chat(tmp_path)
    try:
        _coder(app)
        dm = app.bot_dm("coder")
        assert app.get_payload(dm["id"])["kind"] == "dm"
        group = app.create(bots=["thyca", "coder"])
        assert app.get_payload(group["id"])["kind"] == "group"
        # Legacy session without any meta line reads as group.
        raw = app._sessions.create()
        assert app.get_payload(raw.id)["kind"] == "group"
        assert app.get_payload(raw.id)["bots"] == ["thyca"]
    finally:
        app.shutdown()


def test_create_rejects_stray_string_accepts_empty(tmp_path: Path) -> None:
    import pytest as _pytest

    app = _chat(tmp_path)
    try:
        _coder(app)
        with _pytest.raises(ValueError, match="non-empty list"):
            app.create(bots="coder")
        assert app.create(bots=[])["dm"] == "thyca"
        assert app.create()["dm"] == "thyca"
    finally:
        app.shutdown()
