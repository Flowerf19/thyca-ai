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
