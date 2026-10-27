"""Bot<->bot exchange pairing + read-only endpoints (plan bot-dm-exchanges).

Pair rule (thyca/bots/exchanges.py): bus rows (bus_event) pair via
explicit meta.recipient (new writes) or the in-room result row whose
parent_span_id is the bus span_id; result rows (kind=result) pair via
the in-room bus row. Unpaired rows never count.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from seeded_config import seeded_config
from thyca.app.chat_app import ChatApp
from thyca.bots.card import BotCard
from thyca.config import load, save
from thyca.core.protocol import Message
from thyca.memory.facade import MemoryFacade
from thyca.serve import default_webui, make_server
from thyca.sessions.manager import SessionManager

WEBUI = default_webui()


def _chat(tmp_path: Path) -> ChatApp:
    save(seeded_config(), tmp_path / "config.json")
    return ChatApp(tmp_path, load(tmp_path / "config.json"))


def _room_with(app: ChatApp, room_id: str) -> SessionManager:
    room = SessionManager(store=app._sessions.store)
    room.load(room_id)
    return room


def _seed_pair(app: ChatApp) -> str:
    app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                             description="Writes code.", persona="P"))
    app._bots.create(BotCard(bot_id="helper", name="helper", role="Helper",
                             description="Helps.", persona="P"))
    room = app.create(bots=["coder", "helper"])["id"]
    mgr = _room_with(app, room)
    # New-style explicit-recipient bus row + its result reply.
    mgr.append(Message(role="assistant", content="làm giúp",
                       meta={"bot_id": "coder", "kind": "message",
                             "bus_event": True, "trace_id": "t1",
                             "span_id": "sp1", "recipient": "helper"},
                       ts="2026-10-26T10:00:00Z"))
    mgr.append(Message(role="assistant", content="xong",
                       meta={"bot_id": "helper", "kind": "result",
                             "trace_id": "t1", "parent_span_id": "sp1",
                             "span_id": "sp2"},
                       ts="2026-10-26T10:01:00Z"))
    # Old-style rows (no recipient key): pair through span linkage.
    mgr.append(Message(role="assistant", content="old way",
                       meta={"bot_id": "helper", "kind": "message",
                             "bus_event": True, "trace_id": "t2",
                             "span_id": "sp3"},
                       ts="2026-10-26T10:02:00Z"))
    mgr.append(Message(role="assistant", content="ok",
                       meta={"bot_id": "coder", "kind": "result",
                             "trace_id": "t2", "parent_span_id": "sp3",
                             "span_id": "sp4"},
                       ts="2026-10-26T10:03:00Z"))
    # Noise: unpaired bus row (no reply yet) + plain assistant row.
    mgr.append(Message(role="assistant", content="chờ đã",
                       meta={"bot_id": "coder", "kind": "message",
                             "bus_event": True, "trace_id": "t3",
                             "span_id": "sp5"},
                       ts="2026-10-26T10:04:00Z"))
    mgr.append(Message(role="assistant", content="thường",
                       meta={"bot_id": "coder"},
                       ts="2026-10-26T10:05:00Z"))
    return room


def test_pairing_counts_both_directions_in_order(tmp_path: Path) -> None:
    app = _chat(tmp_path)
    try:
        _seed_pair(app)
        partners = app.dm_exchange_partners("coder")
        assert partners == [
            {"partner": "helper", "count": 4, "last_ts": "2026-10-26T10:03:00Z"},
        ]
        thread = app.dm_exchange_thread("coder", "helper")
        assert [(m["sender"], m["author"], m["kind"], m["content"]) for m in thread] == [
            ("coder", "coder", "message", "làm giúp"),
            ("coder", "helper", "result", "xong"),
            ("helper", "helper", "message", "old way"),
            ("helper", "coder", "result", "ok"),
        ]
        assert [m["ts"] for m in thread] == sorted(m["ts"] for m in thread)
        # Reversed direction reads the same thread; unknown bots are empty.
        assert app.dm_exchange_thread("helper", "coder") == thread
        assert app.dm_exchange_thread("coder", "ghost") == []
        assert app.dm_exchange_partners("helper") == [
            {"partner": "coder", "count": 4, "last_ts": "2026-10-26T10:03:00Z"},
        ]
    finally:
        app.shutdown()


def test_explicit_recipient_pairs_without_reply(tmp_path: Path) -> None:
    app = _chat(tmp_path)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="P"))
        app._bots.create(BotCard(bot_id="helper", name="helper", role="Helper",
                                 description="Helps.", persona="P"))
        room = app.create(bots=["coder", "helper"])["id"]
        # A freshly posted envelope row (no result row yet) still pairs
        # through the explicit recipient meta written by _record_bus_events.
        bus = app._runtime._bus
        bus.register("helper")
        envelope = bus.send(sender="coder", recipient="helper", kind="message",
                            session_id=room, payload="ping")
        mgr = _room_with(app, room)
        app._record_bus_events(mgr, room)
        partners = app.dm_exchange_partners("coder")
        assert partners == [
            {"partner": "helper", "count": 1, "last_ts": envelope.ts},
        ]
        thread = app.dm_exchange_thread("coder", "helper")
        assert len(thread) == 1 and thread[0]["author"] == "coder"
    finally:
        app.shutdown()


def _seed_dm_pair(app: ChatApp) -> str:
    """DM-origin exchange rows recorded in the sender's DM transcript."""
    app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                             description="Writes code.", persona="P"))
    app._bots.create(BotCard(bot_id="helper", name="helper", role="Helper",
                             description="Helps.", persona="P"))
    dm_id = app.bot_dm("coder")["id"]
    mgr = _room_with(app, dm_id)
    mgr.append(Message(role="assistant", content="nhờ trong dm",
                       meta={"bot_id": "coder", "kind": "message",
                             "bus_event": True, "trace_id": "tdm",
                             "span_id": "spdm1", "recipient": "helper"},
                       ts="2026-10-26T10:00:00Z"))
    mgr.append(Message(role="assistant", content="xong trong dm",
                       meta={"bot_id": "helper", "kind": "result",
                             "trace_id": "tdm", "parent_span_id": "spdm1",
                             "span_id": "spdm2"},
                       ts="2026-10-26T10:01:00Z"))
    return dm_id


def test_dm_origin_pair_visible_to_both_bots(tmp_path: Path) -> None:
    app = _chat(tmp_path)
    try:
        _seed_dm_pair(app)
        want = [
            {"partner": "helper", "count": 2, "last_ts": "2026-10-26T10:01:00Z"},
        ]
        assert app.dm_exchange_partners("coder") == want
        assert app.dm_exchange_partners("helper") == [
            {"partner": "coder", "count": 2, "last_ts": "2026-10-26T10:01:00Z"},
        ]
        thread = app.dm_exchange_thread("coder", "helper")
        assert [(m["sender"], m["author"], m["kind"], m["content"])
                for m in thread] == [
            ("coder", "coder", "message", "nhờ trong dm"),
            ("coder", "helper", "result", "xong trong dm"),
        ]
        assert app.dm_exchange_thread("helper", "coder") == thread
        # Unrelated pairs stay invisible.
        assert app.dm_exchange_thread("coder", "ghost") == []
        app._bots.create(BotCard(bot_id="third", name="third", role="T",
                                 description="Third.", persona="P"))
        assert app.dm_exchange_partners("third") == []
    finally:
        app.shutdown()


def test_dm_origin_delivery_records_pair_without_leaking_history(
        tmp_path: Path) -> None:
    """bot_send delivery from a DM records bus + result rows there.

    The recipient gets no DM history (room_text stays empty), DM
    participants stay strictly the owner, and both bots read the pair.
    """
    import asyncio

    from thyca.bots.context import BotTurnContext, BotTurnScope
    from thyca.bots.runtime import BotTurnResult

    app = _chat(tmp_path)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="P"))
        app._bots.create(BotCard(bot_id="helper", name="helper", role="Helper",
                                 description="Helps.", persona="P"))
        dm_id = app.bot_dm("coder")["id"]
        mgr = _room_with(app, dm_id)
        mgr.append(Message(role="user", content="bí mật riêng tư",
                           ts="2026-10-26T09:59:00Z"))
        captured: dict = {}

        async def _fake_turn(**kwargs):
            captured.update(kwargs)
            return BotTurnResult(reply="giúp xong", bot_id="helper",
                                   span_id="sp_res1")

        app._runtime.run_bot_turn_detailed = _fake_turn  # type: ignore[method-assign]
        bus = app._runtime._bus
        bus.register("helper")
        envelope = bus.send(sender="coder", recipient="helper",
                            kind="message", session_id=dm_id,
                            payload="nhờ giúp trong dm")
        scope = BotTurnScope(store=app._sessions.store, act=app._act,
                             tools=app._tools, memory=app._memory,
                             state=app._state, zone=app._zone)
        ctx = BotTurnContext(sender_id="coder", room_id=dm_id,
                             scope=scope, turn_cfg=app._current_cfg())
        assert asyncio.run(app._deliver_envelope_claimed(envelope, ctx)) == "giúp xong"
        assert captured.get("room_text", "") == ""
        assert app.room_bots(dm_id) == ["coder"]
        assert app.dm_exchange_partners("coder") == [
            {"partner": "helper", "count": 2, "last_ts": envelope.ts},
        ]
        assert app.dm_exchange_partners("helper") == [
            {"partner": "coder", "count": 2, "last_ts": envelope.ts},
        ]
        thread = app.dm_exchange_thread("coder", "helper")
        assert [m["author"] for m in thread] == ["coder", "helper"]
        assert thread[0]["content"] == "nhờ giúp trong dm"
        assert thread[1]["content"] == "giúp xong"
        assert all("bí mật" not in m["content"] for m in thread)
    finally:
        app.shutdown()


def test_send_targeted_into_third_bot_dm_records_nothing(tmp_path: Path) -> None:
    """A send/delegate explicitly targeted into a third bot's DM (via
    the room arg) records no bus/result rows there — the DM transcript
    stays private to its owner. The recipient still runs and its reply
    still returns as the tool result."""
    import asyncio

    from thyca.bots.context import BotTurnContext, BotTurnScope
    from thyca.bots.runtime import BotTurnResult

    app = _chat(tmp_path)
    try:
        for bot_id in ("coder", "helper", "third"):
            app._bots.create(BotCard(bot_id=bot_id, name=bot_id, role="R",
                                     description="D", persona="P"))
        third_dm = app.bot_dm("third")["id"]
        mgr = _room_with(app, third_dm)
        mgr.append(Message(role="user", content="riêng tư của third",
                           ts="2026-10-26T09:59:00Z"))

        async def _fake_turn(**kwargs):
            return BotTurnResult(reply="giúp xong", bot_id="helper",
                                   span_id="sp_res9")

        app._runtime.run_bot_turn_detailed = _fake_turn  # type: ignore[method-assign]
        bus = app._runtime._bus
        bus.register("helper")
        envelope = bus.send(sender="coder", recipient="helper",
                            kind="delegate", session_id=third_dm,
                            payload="việc cho helper")
        scope = BotTurnScope(store=app._sessions.store, act=app._act,
                             tools=app._tools, memory=app._memory,
                             state=app._state, zone=app._zone)
        ctx = BotTurnContext(sender_id="coder", room_id=third_dm,
                             scope=scope, turn_cfg=app._current_cfg())
        assert asyncio.run(app._deliver_envelope_claimed(envelope, ctx)) == "giúp xong"
        rows = _room_with(app, third_dm).current.messages
        assert [r for r in rows
                if (r.meta or {}).get("bus_event") is True] == []
        assert [r for r in rows
                if (r.meta or {}).get("kind") == "result"] == []
        assert [r.content for r in rows] == ["riêng tư của third"]
        assert app.room_bots(third_dm) == ["third"]
        assert app.dm_exchange_partners("coder") == []
        assert app.dm_exchange_partners("helper") == []
        assert app.dm_exchange_partners("third") == []
    finally:
        app.shutdown()


def _start(tmp_path: Path, chat: ChatApp):
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    httpd = make_server(host="127.0.0.1", port=0, webui=WEBUI, facade=facade, chat=chat)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


def _get(base: str, path: str) -> dict:
    with urlopen(base + path) as res:
        return json.loads(res.read().decode("utf-8"))


def test_exchange_endpoints_over_http(tmp_path: Path) -> None:
    app = _chat(tmp_path)
    httpd, thread = _start(tmp_path, app)
    try:
        _seed_pair(app)
        base = f"http://127.0.0.1:{httpd.server_port}"
        partners = _get(base, "/api/bots/coder/exchanges")
        assert partners == {"exchanges": [
            {"partner": "helper", "count": 4, "last_ts": "2026-10-26T10:03:00Z"},
        ]}
        thread_payload = _get(base, "/api/bots/coder/exchanges/helper")
        assert [m["sender"] for m in thread_payload["messages"]] == [
            "coder", "coder", "helper", "helper"]
        assert _get(base, "/api/bots/coder/exchanges/ghost") == {"messages": []}
        with pytest.raises(HTTPError) as exc:
            _get(base, "/api/bots/nobody/exchanges")
        assert exc.value.code == 404
    finally:
        httpd.shutdown()
        thread.join(timeout=2)
        httpd.server_close()
        app.shutdown()


def test_exchange_endpoints_include_dm_origin(tmp_path: Path) -> None:
    app = _chat(tmp_path)
    httpd, thread = _start(tmp_path, app)
    try:
        _seed_dm_pair(app)
        base = f"http://127.0.0.1:{httpd.server_port}"
        assert _get(base, "/api/bots/coder/exchanges") == {"exchanges": [
            {"partner": "helper", "count": 2, "last_ts": "2026-10-26T10:01:00Z"},
        ]}
        assert _get(base, "/api/bots/helper/exchanges") == {"exchanges": [
            {"partner": "coder", "count": 2, "last_ts": "2026-10-26T10:01:00Z"},
        ]}
        payload = _get(base, "/api/bots/coder/exchanges/helper")
        assert [(m["author"], m["content"]) for m in payload["messages"]] == [
            ("coder", "nhờ trong dm"),
            ("helper", "xong trong dm"),
        ]
    finally:
        httpd.shutdown()
        thread.join(timeout=2)
        httpd.server_close()
        app.shutdown()
