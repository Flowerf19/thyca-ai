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
from thyca.configs import load, save
from thyca.core.protocol import Message
from thyca.memories.facade import MemoryFacade
from thyca.serve import default_webui, make_server
from thyca.conversations.runtime.manager import SessionManager

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


def _private_group(app: ChatApp) -> str:
    """Typed group with members [coder, thyca]; helper stays outside."""
    app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                             description="Writes code.", persona="P"))
    app._bots.create(BotCard(bot_id="helper", name="helper", role="Helper",
                             description="Helps.", persona="P"))
    room = app.create(bots=["coder", "thyca"])["id"]
    assert app.room_bots(room) == ["coder", "thyca"]
    return room


def _deliver(app: ChatApp, envelope, sender: str, room: str):
    """Run one delivery with the sender's live-turn context (sync helper)."""
    import asyncio

    from thyca.bots.context import BotTurnContext, BotTurnScope

    scope = BotTurnScope(store=app._sessions.store, act=app._act,
                         tools=app._tools, memory=app._memory,
                         state=app._state, zone=app._zone)
    ctx = BotTurnContext(sender_id=sender, room_id=room,
                         scope=scope, turn_cfg=app._current_cfg())
    return _asyncio_mark.run(app._deliver_envelope_claimed(envelope, ctx))


def test_private_delivery_to_non_member_records_nothing(tmp_path: Path) -> None:
    """A group non-member gets a private invocation, not a group turn.

    The reply returns to the sender; the group gains no bus/result rows,
    membership is unchanged, and the pair stays out of the exchanges.
    """
    from thyca.bots.runtime import BotTurnResult

    app = _chat(tmp_path)
    try:
        room = _private_group(app)
        mgr = _room_with(app, room)
        mgr.append(Message(role="user", content="GROUP-ONLY-MARKER please coordinate",
                           ts="2026-10-26T09:00:00Z"))
        before = len(_room_with(app, room).current.messages)
        captured: dict = {}

        async def _fake_turn(**kwargs):
            captured.update(kwargs)
            return BotTurnResult(reply="private answer", bot_id="helper",
                                 span_id="sp_priv1")

        app._runtime.run_bot_turn_detailed = _fake_turn  # type: ignore[method-assign]
        bus = app._runtime._bus
        bus.register("helper")
        envelope = bus.send(sender="coder", recipient="helper", kind="message",
                            session_id=room, payload="chosen task")
        assert _deliver(app, envelope, "coder", room) == "private answer"
        assert captured.get("room_text", "missing") == ""
        assert captured.get("room_cursor_after") is None
        assert captured.get("private_invocation") is True
        assert app.room_bots(room) == ["coder", "thyca"]
        rows = _room_with(app, room).current.messages
        assert len(rows) == before
        assert [r for r in rows if (r.meta or {}).get("bus_event")] == []
        assert [r for r in rows if (r.meta or {}).get("kind") == "result"] == []
        assert app._runtime.room_cursor("helper", room) is None
        assert all(p["partner"] != "helper"
                   for p in app.dm_exchange_partners("coder"))
        assert app.dm_exchange_partners("helper") == []
    finally:
        app.shutdown()


def test_member_delivery_records_grouped_exchange(tmp_path: Path) -> None:
    """A member recipient keeps the grouped exchange: history, rows, pair."""
    from thyca.bots.runtime import BotTurnResult

    app = _chat(tmp_path)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="P"))
        app._bots.create(BotCard(bot_id="helper", name="helper", role="Helper",
                                 description="Helps.", persona="P"))
        room = app.create(bots=["coder", "helper"])["id"]
        assert app.room_bots(room) == ["coder", "helper"]
        mgr = _room_with(app, room)
        mgr.append(Message(role="user", content="GROUP-ONLY-MARKER please coordinate",
                           ts="2026-10-26T09:00:00Z"))
        captured: dict = {}

        async def _fake_turn(**kwargs):
            captured.update(kwargs)
            return BotTurnResult(reply="giúp xong", bot_id="helper",
                                 span_id="sp_mem1")

        app._runtime.run_bot_turn_detailed = _fake_turn  # type: ignore[method-assign]
        bus = app._runtime._bus
        bus.register("helper")
        envelope = bus.send(sender="coder", recipient="helper", kind="message",
                            session_id=room, payload="nhờ giúp")
        assert _deliver(app, envelope, "coder", room) == "giúp xong"
        assert "GROUP-ONLY-MARKER" in captured.get("room_text", "")
        assert captured.get("room_cursor_after") is not None
        assert captured.get("private_invocation") is False
        assert app.room_bots(room) == ["coder", "helper"]
        rows = _room_with(app, room).current.messages
        bus_rows = [r for r in rows if (r.meta or {}).get("bus_event")]
        result_rows = [r for r in rows if (r.meta or {}).get("kind") == "result"]
        assert len(bus_rows) == 1 and bus_rows[0].meta["recipient"] == "helper"
        assert len(result_rows) == 1 and result_rows[0].content == "giúp xong"
        # last_ts is the latest persisted row timestamp, derived from the
        # room transcript itself rather than the send-time envelope stamp:
        # message rows are stamped on append, so a send/record pair that
        # crosses a second boundary must report the row maximum.
        assert app.dm_exchange_partners("coder") == [
            {"partner": "helper", "count": 2, "last_ts": max(
                bus_rows[0].ts, result_rows[0].ts)},
        ]
    finally:
        app.shutdown()


def test_exchange_pair_last_ts_uses_row_maximum_across_seconds(tmp_path: Path) -> None:
    # Deterministic cross-second case: the bus row lands in one second
    # and the result row in the next; the exchange lists the row maximum.
    app = _chat(tmp_path)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="P"))
        app._bots.create(BotCard(bot_id="helper", name="helper", role="Helper",
                                 description="Helps.", persona="P"))
        room = app.create(bots=["coder", "helper"])["id"]
        mgr = _room_with(app, room)
        mgr.append(Message(role="assistant", content="ping",
                           meta={"bot_id": "coder", "kind": "message",
                                 "bus_event": True, "trace_id": "tx",
                                 "span_id": "sp_boundary_bus",
                                 "recipient": "helper"},
                           ts="2026-10-26T10:00:00Z"))
        mgr.append(Message(role="assistant", content="pong",
                           meta={"bot_id": "helper", "kind": "result",
                                 "trace_id": "tx",
                                 "parent_span_id": "sp_boundary_bus",
                                 "span_id": "sp_boundary_res"},
                           ts="2026-10-26T10:00:01Z"))
        assert app.dm_exchange_partners("coder") == [
            {"partner": "helper", "count": 2,
             "last_ts": "2026-10-26T10:00:01Z"},
        ]
    finally:
        app.shutdown()


def test_member_delivery_across_second_boundary_reports_row_max(tmp_path: Path) -> None:
    # A delivery whose rows land in a later second than the send must
    # still report the persisted rows' maximum, not the send stamp.
    # Timestamps are fully mocked (T0 send, T1 record) so the boundary
    # is deterministic with no wall-clock wait.
    from thyca.bots.runtime import BotTurnResult as _BTR
    from unittest.mock import patch as _patch
    app = _chat(tmp_path)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="P"))
        app._bots.create(BotCard(bot_id="helper", name="helper", role="Helper",
                                 description="Helps.", persona="P"))
        room = app.create(bots=["coder", "helper"])["id"]
        _room_with(app, room).append(Message(role="user", content="go",
                                              ts="2026-10-26T09:00:00Z"))

        async def _fake_turn(**kwargs):
            return _BTR(reply="crossed", bot_id="helper",
                        span_id="sp_cross1")

        app._runtime.run_bot_turn_detailed = _fake_turn  # type: ignore[method-assign]
        bus = app._runtime._bus
        bus.register("helper")
        with _patch("thyca.bots.bus.utc_now_ts", return_value="2026-10-26T10:00:00Z"):
            envelope = bus.send(sender="coder", recipient="helper", kind="message",
                                session_id=room, payload="cross me")
        assert envelope.ts == "2026-10-26T10:00:00Z"
        # Row timestamps are controlled by patching the service module's
        # Message binding (the dataclass default factory is bound at class
        # creation, so the module attribute is the mock point).
        import functools as _functools_cross
        from thyca.core.protocol import Message as _MsgTS
        _PinnedMsg = _functools_cross.partial(
            _MsgTS, ts="2026-10-26T10:00:01Z")
        with _patch("thyca.app.a2a_delivery.service.Message", _PinnedMsg):
            assert _deliver(app, envelope, "coder", room) == "crossed"
        rows = _room_with(app, room).current.messages
        bus_rows = [r for r in rows if (r.meta or {}).get("bus_event")]
        result_rows = [r for r in rows if (r.meta or {}).get("kind") == "result"]
        assert len(bus_rows) == 1 and len(result_rows) == 1
        assert bus_rows[0].ts == "2026-10-26T10:00:01Z"
        assert result_rows[0].ts == "2026-10-26T10:00:01Z"
        assert envelope.ts == "2026-10-26T10:00:00Z" != bus_rows[0].ts, \
            "send and record timestamps must differ for this regression test"
        assert app.dm_exchange_partners("coder") == [
            {"partner": "helper", "count": 2,
             "last_ts": max(bus_rows[0].ts, result_rows[0].ts)},
        ]
        assert app.dm_exchange_partners("coder")[0]["last_ts"] != envelope.ts
    finally:
        app.shutdown()


def test_private_delivery_failure_records_nothing(tmp_path: Path) -> None:
    """A failed private turn reports the failure and writes no rows."""
    app = _chat(tmp_path)
    try:
        room = _private_group(app)
        mgr = _room_with(app, room)
        mgr.append(Message(role="user", content="GROUP-ONLY-MARKER please coordinate",
                           ts="2026-10-26T09:00:00Z"))
        before = len(_room_with(app, room).current.messages)
        captured: dict = {}

        async def _fake_turn(**kwargs):
            captured.update(kwargs)
            raise RuntimeError("model down")

        app._runtime.run_bot_turn_detailed = _fake_turn  # type: ignore[method-assign]
        bus = app._runtime._bus
        bus.register("helper")
        envelope = bus.send(sender="coder", recipient="helper", kind="message",
                            session_id=room, payload="chosen task")
        reply = _deliver(app, envelope, "coder", room)
        assert reply.startswith("delivery failed")
        assert captured.get("private_invocation") is True
        assert app.room_bots(room) == ["coder", "thyca"]
        rows = _room_with(app, room).current.messages
        assert len(rows) == before
        assert [r for r in rows if (r.meta or {}).get("bus_event")] == []
        assert [r for r in rows if (r.meta or {}).get("kind") == "result"] == []
        assert app._runtime.room_cursor("helper", room) is None
    finally:
        app.shutdown()


def test_private_real_runtime_hides_group_and_team_markers(tmp_path: Path) -> None:
    """Real runtime + fake LLM: outsider sees payload, no history or team memory.

    Seeds a group transcript marker and a team memory entry with the
    group as origin. The outsider helper runs a real turn (fake LLM
    calls memory_search once, then replies). Its prompts must show the
    chosen payload but neither marker; the reply returns, roster and
    transcript stay unchanged, and no bus/result rows or exchanges appear.
    Former-member replay is covered by the versioned span selector: the
    private projection starts from the current span only (see the
    former-member and rejoin tests below)."""
    from seeded_config import seeded_config as _seeded
    from thyca.configs import load as _load, save as _save
    from thyca.core.protocol import ToolCall as _ToolCall
    from thyca.llms.llm_base import ChatReply as _ChatReply
    from thyca.memories.facade import MemoryFacade as _Facade

    GROUP_MARKER = "GROUP-ONLY-MARKER-REAL-7X"
    TEAM_MARKER = "TEAMMARKERREAL9Z"
    OWN_MARKER = "OWNMARKERREAL4W"
    CHOSEN = "chosen task REAL-5Q"

    class _FakeLLM:
        # Fake LLM harness state for _FakeLLM.
        def __init__(self) -> None:
            self.prompts: list[str] = []

        # Fake provider: searches team memory once, then answers.
        async def chat(self, messages, tools=None):
            text = "\n".join(str(m.content or "") for m in messages)
            tool_text = "\n".join(
                str(m.content or "") for m in messages if m.role == "tool"
            )
            self.prompts.append(text + "\n---TOOL---\n" + tool_text)
            has_tool = any(m.role == "tool" for m in messages)
            if not has_tool:
                return _ChatReply(content=None, model="m", tool_calls=[
                    _ToolCall(id="t-search", name="memory_search",
                              arguments={"query": "MARKERREAL"}),
                ])
            return _ChatReply(content="private answer", model="m",
                                usage={"prompt_tokens": 5, "completion_tokens": 1,
                                       "total_tokens": 6})

    llm = _FakeLLM()
    _save(_seeded(), tmp_path / "config.json")
    app = ChatApp(tmp_path, _load(tmp_path / "config.json"), connect=llm)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="P"))
        app._bots.create(BotCard(bot_id="helper", name="helper", role="Helper",
                                 description="Helps.", persona="PERSONA-HELPER"))
        room = app.create(bots=["coder", "thyca"])["id"]
        assert app.room_bots(room) == ["coder", "thyca"]
        mgr = _room_with(app, room)
        mgr.append(Message(role="user", content=f"{GROUP_MARKER} please coordinate",
                           ts="2026-10-26T09:00:00Z"))
        before = len(_room_with(app, room).current.messages)
        from datetime import datetime as _dt, timedelta as _td
        from zoneinfo import ZoneInfo as _ZI
        _yesterday = (_dt.now(_ZI("Asia/Ho_Chi_Minh")).date() - _td(days=1)).isoformat()
        _at = _dt.fromisoformat(f"{_yesterday}T12:00:00+07:00")
        facade = _Facade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
        facade.remember(topic=f"team topic {TEAM_MARKER}",
                        summary=f"{TEAM_MARKER} body", chat=room, now=_at)
        from thyca.memories.identity import bind_bot_id as _bind, reset_bot_id as _reset
        _tok = _bind("helper")
        try:
            facade.remember(topic=f"own topic {OWN_MARKER}",
                            summary=f"{OWN_MARKER} body", chat=room,
                            bots=["helper"], now=_at)
        finally:
            _reset(_tok)
        facade._refresh_index()
        outside = facade.search("MARKERREAL")
        assert any(TEAM_MARKER in (h.snippet or "") or TEAM_MARKER in (h.heading or "")
                   for h in outside.hits), "team seed must exist outside a turn"
        assert any(OWN_MARKER in (h.snippet or "") or OWN_MARKER in (h.heading or "")
                   for h in outside.hits), "own seed must exist outside a turn"
        bus = app._runtime._bus
        bus.register("helper")
        envelope = bus.send(sender="coder", recipient="helper", kind="message",
                            session_id=room, payload=CHOSEN)
        import asyncio as _asyncio
        from thyca.bots.context import BotTurnContext as _Ctx, BotTurnScope as _Scope
        _scope = _Scope(store=app._sessions.store, act=app._act,
                        tools=app._tools, memory=app._memory,
                        state=app._state, zone=app._zone)
        _ctx = _Ctx(sender_id="coder", room_id=room,
                    scope=_scope, turn_cfg=app._current_cfg())
        assert _asyncio.run(app._deliver_envelope(envelope, _ctx)) == "private answer"
        assert any(CHOSEN in p for p in llm.prompts), "chosen payload must be visible"
        assert all(GROUP_MARKER not in p for p in llm.prompts), "group history must stay hidden"
        assert all(TEAM_MARKER not in p for p in llm.prompts), "team memory must stay hidden"
        assert any(OWN_MARKER in p for p in llm.prompts), "own memory stays readable"
        assert app.room_bots(room) == ["coder", "thyca"]
        rows = _room_with(app, room).current.messages
        assert len(rows) == before
        assert [r for r in rows if (r.meta or {}).get("bus_event")] == []
        assert [r for r in rows if (r.meta or {}).get("kind") == "result"] == []
        assert app._runtime.room_cursor("helper", room) is None
        assert app.dm_exchange_partners("helper") == []
        assert all(p["partner"] != "helper"
                   for p in app.dm_exchange_partners("coder"))
        binding = app._runtime.workspaces.store.get_binding("helper", room)
        assert binding is not None and binding.window is not None
        # Approved boundary: the registry keeps the real group window;
        # the DM-like visible window lives only within the turn.
        assert binding.window.kind == "group"
        assert binding.window.window_id == room
        from thyca.core.channel import WindowRef as _WR
        from thyca.memories.visibility import MemoryView as _MV
        assert _MV(window=None, bot_id="helper",
                   thyca_dir=tmp_path).can_read_origin(room) is False
        assert _MV(window=_WR(window_id=room, channel_id="webui", kind="group",
                              participants=("coder", "thyca")),
                   bot_id="coder", thyca_dir=tmp_path).can_read_origin(room) is True
    finally:
        app.shutdown()


def test_private_dispatch_pins_current_envelope_on_roster_change(tmp_path: Path) -> None:
    """Dispatch private stays private even if the roster changes before recording.

    The first room_bots read (dispatch) excludes helper; the second
    (record) includes helper. Without the pin the current envelope would
    publish; with it the group gains no rows. Fails before the pin fix."""
    from thyca.bots.runtime import BotTurnResult as _Result

    app = _chat(tmp_path)
    try:
        room = _private_group(app)
        mgr = _room_with(app, room)
        mgr.append(Message(role="user", content="GROUP-ONLY-MARKER please coordinate",
                           ts="2026-10-26T09:00:00Z"))
        before = len(_room_with(app, room).current.messages)
        calls: list[int] = []

        def _changing_roster(session_id: str) -> list[str]:
            calls.append(1)
            if len(calls) == 1:
                return ["coder", "thyca"]
            return ["coder", "thyca", "helper"]

        app._delivery._deps.room_bots = _changing_roster  # type: ignore[method-assign]

        async def _fake_turn(**kwargs):
            return _Result(reply="private answer", bot_id="helper",
                           span_id="sp_pin1")

        app._runtime.run_bot_turn_detailed = _fake_turn  # type: ignore[method-assign]
        bus = app._runtime._bus
        bus.register("helper")
        envelope = bus.send(sender="coder", recipient="helper", kind="message",
                            session_id=room, payload="chosen task")
        assert _deliver(app, envelope, "coder", room) == "private answer"
        assert calls == [1, 1, 1], "dispatch, record, and fresh result check"
        rows = _room_with(app, room).current.messages
        assert len(rows) == before
        assert [r for r in rows if (r.meta or {}).get("bus_event")] == []
        assert [r for r in rows if (r.meta or {}).get("kind") == "result"] == []
        assert app.dm_exchange_partners("helper") == []
    finally:
        app.shutdown()


def test_private_dispatch_read_failure_records_nothing(tmp_path: Path) -> None:
    """A roster read failure at dispatch means private with no rows."""
    from thyca.bots.runtime import BotTurnResult as _Result

    app = _chat(tmp_path)
    try:
        room = _private_group(app)
        mgr = _room_with(app, room)
        mgr.append(Message(role="user", content="GROUP-ONLY-MARKER please coordinate",
                           ts="2026-10-26T09:00:00Z"))
        before = len(_room_with(app, room).current.messages)
        captured: dict = {}

        def _failing_roster(session_id: str) -> list[str]:
            raise RuntimeError("roster unavailable")

        app._delivery._deps.room_bots = _failing_roster  # type: ignore[method-assign]

        async def _fake_turn(**kwargs):
            captured.update(kwargs)
            return _Result(reply="private answer", bot_id="helper",
                           span_id="sp_fail1")

        app._runtime.run_bot_turn_detailed = _fake_turn  # type: ignore[method-assign]
        bus = app._runtime._bus
        bus.register("helper")
        envelope = bus.send(sender="coder", recipient="helper", kind="message",
                            session_id=room, payload="chosen task")
        assert _deliver(app, envelope, "coder", room) == "private answer"
        assert captured.get("private_invocation") is True
        assert captured.get("room_text", "missing") == ""
        rows = _room_with(app, room).current.messages
        assert len(rows) == before
        assert [r for r in rows if (r.meta or {}).get("bus_event")] == []
        assert [r for r in rows if (r.meta or {}).get("kind") == "result"] == []
    finally:
        app.shutdown()


def test_member_record_read_failure_skips_rows_fail_closed(tmp_path: Path) -> None:
    """Dispatch member but record roster fails: no bus or result rows (fail closed)."""
    from thyca.bots.runtime import BotTurnResult as _Result

    app = _chat(tmp_path)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="P"))
        app._bots.create(BotCard(bot_id="helper", name="helper", role="Helper",
                                 description="Helps.", persona="P"))
        room = app.create(bots=["coder", "helper"])["id"]
        mgr = _room_with(app, room)
        mgr.append(Message(role="user", content="GROUP-ONLY-MARKER please coordinate",
                           ts="2026-10-26T09:00:00Z"))
        before = len(_room_with(app, room).current.messages)
        calls: list[int] = []

        def _flaky_roster(session_id: str) -> list[str]:
            calls.append(1)
            if len(calls) == 1:
                return ["coder", "helper"]
            raise RuntimeError("roster unavailable")

        app._delivery._deps.room_bots = _flaky_roster  # type: ignore[method-assign]

        async def _fake_turn(**kwargs):
            return _Result(reply="ok", bot_id="helper", span_id="sp_flaky1")

        app._runtime.run_bot_turn_detailed = _fake_turn  # type: ignore[method-assign]
        bus = app._runtime._bus
        bus.register("helper")
        envelope = bus.send(sender="coder", recipient="helper", kind="message",
                            session_id=room, payload="hi")
        assert _deliver(app, envelope, "coder", room) == "ok"
        rows = _room_with(app, room).current.messages
        assert len(rows) == before
        assert [r for r in rows if (r.meta or {}).get("bus_event")] == []
        assert [r for r in rows if (r.meta or {}).get("kind") == "result"] == []
    finally:
        app.shutdown()


def test_private_former_member_gets_no_prior_group_history(tmp_path: Path) -> None:
    """Former member private turn starts empty (no projector change).

    Helper runs once as a member (its timeline gains group rows), is
    removed, then runs privately. The private prompt must lack both the
    group marker and helper's own prior group rows. Without the
    in-runtime empty-history guard the same window id replays them."""
    from seeded_config import seeded_config as _seeded2
    from thyca.configs import load as _load2, save as _save2
    from thyca.llms.llm_base import ChatReply as _ChatReply2

    MEMBER_TASK = "member task FORMER-1M"
    MEMBER_REPLY = "member reply FORMER-1R"
    PRIVATE_TASK = "private task FORMER-2P"
    GROUP_MARKER = "GROUP-ONLY-MARKER-FORMER-3G"

    class _FakeLLM2:
        # Fake LLM harness state for _FakeLLM2.
        def __init__(self) -> None:
            self.prompts: list[str] = []

        # Fake provider: member vs private replies branch on payload.
        async def chat(self, messages, tools=None):
            text = "\n".join(str(m.content or "") for m in messages)
            self.prompts.append(text)
            if PRIVATE_TASK in text:
                return _ChatReply2(content="private answer", model="m",
                                   usage={"prompt_tokens": 5, "completion_tokens": 1,
                                          "total_tokens": 6})
            return _ChatReply2(content=MEMBER_REPLY, model="m",
                               usage={"prompt_tokens": 5, "completion_tokens": 1,
                                      "total_tokens": 6})

    llm = _FakeLLM2()
    _save2(_seeded2(), tmp_path / "config.json")
    app = ChatApp(tmp_path, _load2(tmp_path / "config.json"), connect=llm)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="P"))
        app._bots.create(BotCard(bot_id="helper", name="helper", role="Helper",
                                 description="Helps.", persona="PERSONA-HELPER"))
        room = app.create(bots=["coder", "helper"])["id"]
        mgr = _room_with(app, room)
        mgr.append(Message(role="user", content=f"{GROUP_MARKER} please coordinate",
                           ts="2026-10-26T09:00:00Z"))
        import asyncio as _asyncio2
        from thyca.bots.context import BotTurnContext as _Ctx2, BotTurnScope as _Scope2

        def _ctx(sender: str):
            scope = _Scope2(store=app._sessions.store, act=app._act,
                            tools=app._tools, memory=app._memory,
                            state=app._state, zone=app._zone)
            return _Ctx2(sender_id=sender, room_id=room,
                         scope=scope, turn_cfg=app._current_cfg())

        bus = app._runtime._bus
        bus.register("helper")
        member_env = bus.send(sender="coder", recipient="helper", kind="message",
                              session_id=room, payload=MEMBER_TASK)
        assert _asyncio2.run(app._deliver_envelope(member_env, _ctx("coder"))) == MEMBER_REPLY
        assert any(MEMBER_TASK in p for p in llm.prompts)
        # Helper leaves the group; its timeline keeps the member rows.
        assert app.set_room_bots(room, "helper", "remove") == ["coder"]
        llm.prompts.clear()
        before = len(_room_with(app, room).current.messages)
        private_env = bus.send(sender="coder", recipient="helper", kind="message",
                               session_id=room, payload=PRIVATE_TASK)
        assert _asyncio2.run(app._deliver_envelope(private_env, _ctx("coder"))) == "private answer"
        assert any(PRIVATE_TASK in p for p in llm.prompts)
        assert all(GROUP_MARKER not in p for p in llm.prompts)
        assert all(MEMBER_TASK not in p for p in llm.prompts)
        assert all(MEMBER_REPLY not in p for p in llm.prompts)
        rows = _room_with(app, room).current.messages
        assert len(rows) == before, "private delivery must add no room rows"
        assert all(PRIVATE_TASK not in (r.content or "") for r in rows)
        assert all("private answer" not in (r.content or "") for r in rows)
        assert app.room_bots(room) == ["coder"]
    finally:
        app.shutdown()

def test_private_nested_bot_send_to_member_stays_private(tmp_path: Path) -> None:
    # Outer private helper calls bot_send to member coder; the nested turn
    # must not inherit group history or publish automatic group rows.
    # Control: a direct member delivery to coder records its pair.
    from seeded_config import seeded_config as _seeded3
    from thyca.configs import load as _load3, save as _save3
    from thyca.core.protocol import ToolCall as _ToolCall3
    from thyca.llms.llm_base import ChatReply as _ChatReply3

    OUTER_TASK = "outer task NESTED-1"
    NESTED_TASK = "nested task NESTED-2"
    GROUP_MARKER = "GROUP-ONLY-MARKER-NESTED-3"

    class _FakeLLM3:
        # Fake LLM harness state for _FakeLLM3.
        def __init__(self) -> None:
            self.prompts: list[str] = []

        # Fake provider for the nested-chain test: helper delegates once, coder answers.
        async def chat(self, messages, tools=None):
            text = "\n".join(str(m.content or "") for m in messages)
            self.prompts.append(text)
            if NESTED_TASK in text:
                return _ChatReply3(content="nested reply", model="m",
                                   usage={"prompt_tokens": 5, "completion_tokens": 1,
                                          "total_tokens": 6})
            if OUTER_TASK in text and not any(
                    m.role == "tool" for m in messages):
                return _ChatReply3(content=None, model="m", tool_calls=[
                    _ToolCall3(id="t-nested", name="bot_send",
                               arguments={"bot_id": "coder",
                                          "message": NESTED_TASK}),
                ])
            return _ChatReply3(content="outer reply", model="m",
                               usage={"prompt_tokens": 5, "completion_tokens": 1,
                                      "total_tokens": 6})

    llm = _FakeLLM3()
    _save3(_seeded3(), tmp_path / "config.json")
    app = ChatApp(tmp_path, _load3(tmp_path / "config.json"), connect=llm)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="P"))
        app._bots.create(BotCard(bot_id="helper", name="helper", role="Helper",
                                 description="Helps.", persona="PERSONA-HELPER"))
        room = app.create(bots=["coder", "thyca"])["id"]
        mgr = _room_with(app, room)
        mgr.append(Message(role="user", content=f"{GROUP_MARKER} coordinate",
                           ts="2026-10-26T09:00:00Z"))
        # Control: direct member delivery records its grouped pair.
        bus = app._runtime._bus
        bus.register("coder")
        control_env = bus.send(sender="thyca", recipient="coder", kind="message",
                               session_id=room, payload="control task")
        import asyncio as _asyncio3
        from thyca.bots.context import BotTurnContext as _Ctx3, BotTurnScope as _Scope3

        def _ctx3(sender: str):
            scope = _Scope3(store=app._sessions.store, act=app._act,
                            tools=app._tools, memory=app._memory,
                            state=app._state, zone=app._zone)
            return _Ctx3(sender_id=sender, room_id=room,
                         scope=scope, turn_cfg=app._current_cfg())
        assert _asyncio3.run(app._deliver_envelope(control_env, _ctx3("thyca"))) \
            == "outer reply"
        control_rows = len(_room_with(app, room).current.messages)
        assert control_rows > 1
        llm.prompts.clear()
        bus.register("helper")
        outer_env = bus.send(sender="coder", recipient="helper", kind="message",
                             session_id=room, payload=OUTER_TASK)
        before = len(_room_with(app, room).current.messages)
        assert _asyncio3.run(app._deliver_envelope(outer_env, _ctx3("coder"))) \
            == "outer reply"
        assert any(NESTED_TASK in pr for pr in llm.prompts)
        assert all(GROUP_MARKER not in pr for pr in llm.prompts)
        rows = _room_with(app, room).current.messages
        assert len(rows) == before, "nested private chain must add no group rows"
        assert all(NESTED_TASK not in (r.content or "") for r in rows)
    finally:
        app.shutdown()


def test_private_stream_stays_runtime_local(tmp_path: Path) -> None:
    # Member turns forward live deltas to the group sink; private turns
    # keep status local and forward nothing. Seed control proves the sink works.
    from seeded_config import seeded_config as _seeded4
    from thyca.configs import load as _load4, save as _save4
    from thyca.llms.llm_base import ChatReply as _ChatReply4

    STREAM_THINK = "streamed thinking STREAM-4T"
    STREAM_SAY = "streamed content STREAM-4C"

    class _FakeLLM4:
        # Invokes the real streaming callbacks like a streaming provider.
        async def chat(self, messages, tools=None, on_reasoning=None,
                       on_content=None, **kwargs):
            if callable(on_reasoning):
                on_reasoning(STREAM_THINK)
            if callable(on_content):
                on_content(STREAM_SAY)
            return _ChatReply4(content="reply text here", model="m",
                               usage={"prompt_tokens": 5, "completion_tokens": 1,
                                      "total_tokens": 6})

    llm = _FakeLLM4()
    _save4(_seeded4(), tmp_path / "config.json")
    app = ChatApp(tmp_path, _load4(tmp_path / "config.json"), connect=llm)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="P"))
        app._bots.create(BotCard(bot_id="helper", name="helper", role="Helper",
                                 description="Helps.", persona="P"))
        room = app.create(bots=["coder", "helper"])["id"]
        import asyncio as _asyncio4
        from thyca.bots.context import BotTurnContext as _Ctx4, BotTurnScope as _Scope4

        def _ctx4(sender: str, rid: str):
            scope = _Scope4(store=app._sessions.store, act=app._act,
                            tools=app._tools, memory=app._memory,
                            state=app._state, zone=app._zone)
            return _Ctx4(sender_id=sender, room_id=rid,
                         scope=scope, turn_cfg=app._current_cfg())

        bus = app._runtime._bus
        bus.register("helper")
        member_events: list = []
        member_env = bus.send(sender="coder", recipient="helper", kind="message",
                              session_id=room, payload="member task")
        _ctxm = _ctx4("coder", room)
        _ctxm.event_sink = member_events.append
        assert _asyncio4.run(app._deliver_envelope(member_env, _ctxm))
        assert len(member_events) > 0, "member control must forward live events"
        assert any(STREAM_THINK in str(getattr(e, "delta", "")) for e in member_events), \
            "member control must forward streamed reasoning"
        assert any(STREAM_SAY in str(getattr(e, "delta", "")) for e in member_events), \
            "member control must forward streamed content"
        # Create the second room only after the first holds rows: blank
        # group rooms are pruned on the next create.
        other = app.create(bots=["coder", "thyca"])["id"]
        private_events: list = []
        private_env = bus.send(sender="coder", recipient="helper", kind="message",
                               session_id=other, payload="private task")
        _ctxp = _ctx4("coder", other)
        _ctxp.event_sink = private_events.append
        assert _asyncio4.run(app._deliver_envelope(private_env, _ctxp))
        assert private_events == [], "private deltas must not reach the group sink"
    finally:
        app.shutdown()


def test_private_leaves_cursor_and_recall_untouched(tmp_path: Path) -> None:
    # Real runtime: a private turn advances no group cursor, consumes no
    # recall, and takes no injected group context (fail closed pre-model).
    from seeded_config import seeded_config as _seeded5
    from thyca.configs import load as _load5, save as _save5
    from thyca.llms.llm_base import ChatReply as _ChatReply5
    from thyca.bots.workspace import WorkspaceTurnConflict as _Conflict5

    GROUP_MARKER = "GROUP-ONLY-MARKER-CURSOR-5"
    PRIVATE_TASK = "private task CURSOR-RECALL-5"

    class _FakeLLM5:
        # Captures prompts; private turn answers without tools.
        def __init__(self) -> None:
            self.prompts: list[str] = []

        # Real-runtime fake: records the wire prompt, then answers.
        async def chat(self, messages, tools=None):
            self.prompts.append("\n".join(str(m.content or "") for m in messages))
            return _ChatReply5(content="private answer", model="m",
                               usage={"prompt_tokens": 5, "completion_tokens": 1,
                                      "total_tokens": 6})

    llm = _FakeLLM5()
    _save5(_seeded5(), tmp_path / "config.json")
    app = ChatApp(tmp_path, _load5(tmp_path / "config.json"), connect=llm)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="P"))
        app._bots.create(BotCard(bot_id="helper", name="helper", role="Helper",
                                 description="Helps.", persona="P"))
        room = app.create(bots=["coder", "helper"])["id"]
        mgr = _room_with(app, room)
        mgr.append(Message(role="user", content=f"{GROUP_MARKER} cursor check",
                           ts="2026-10-26T09:00:00Z"))
        other = app.create(bots=["coder", "thyca"])["id"]
        # Seed the other room so creating it does not prune the first.
        _room_with(app, other).append(Message(role="user", content="seed",
                                              ts="2026-10-26T09:00:00Z"))
        app._runtime._reinject_pending.add(("helper", other))
        before_pending = set(app._runtime._reinject_pending)
        bus = app._runtime._bus
        bus.register("helper")
        envelope = bus.send(sender="coder", recipient="helper", kind="message",
                            session_id=other, payload=PRIVATE_TASK)
        import asyncio as _asyncio5
        from thyca.bots.context import BotTurnContext as _Ctx5, BotTurnScope as _Scope5
        scope = _Scope5(store=app._sessions.store, act=app._act,
                        tools=app._tools, memory=app._memory,
                        state=app._state, zone=app._zone)
        ctx = _Ctx5(sender_id="coder", room_id=other,
                    scope=scope, turn_cfg=app._current_cfg())
        assert _asyncio5.run(app._deliver_envelope(envelope, ctx)) == "private answer"
        assert any(PRIVATE_TASK in pr for pr in llm.prompts)
        assert all(GROUP_MARKER not in pr for pr in llm.prompts)
        assert app._runtime.room_cursor("helper", other) is None
        assert app._runtime.room_cursor("coder", room) is None
        assert set(app._runtime._reinject_pending) == before_pending
        # Injected group context into a private turn fails before the model
        # with the exact isolation error (real runtime, no provider call).
        card = app._bots.require("helper")
        calls: list[str] = []
        orig_create = __import__("thyca.llms.llm_factory",
                                 fromlist=["ConnectFactory"]).ConnectFactory.create

        @classmethod
        def _counting_create(cls, *args, **kwargs):
            calls.append("provider")
            return orig_create(*args, **kwargs)

        with __import__("pytest").raises(_Conflict5, match="private invocation with group context"):
            with __import__("unittest.mock", fromlist=["patch"]).patch.object(
                    __import__("thyca.llms.llm_factory",
                               fromlist=["ConnectFactory"]).ConnectFactory,
                    "create", _counting_create):
                _asyncio5.run(app._runtime.run_bot_turn_detailed(
                    scope=scope, card=card, room_id=other, text="x",
                    turn_cfg=app._current_cfg(), room_text="GROUP LEAK",
                    private_invocation=True))
        assert calls == [], "isolation must fail before any provider call"
    finally:
        app.shutdown()


def test_private_result_withheld_after_removal_during_model(tmp_path: Path) -> None:
    # Dispatch and recording allow the member; removal during the model run
    # withholds the result while the eligible request row stays.
    from thyca.bots.runtime import BotTurnResult as _Result6
    app = _chat(tmp_path)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="P"))
        app._bots.create(BotCard(bot_id="helper", name="helper", role="Helper",
                                 description="Helps.", persona="P"))
        room = app.create(bots=["coder", "helper"])["id"]
        mgr = _room_with(app, room)
        mgr.append(Message(role="user", content="GROUP-ONLY-MARKER removal race",
                           ts="2026-10-26T09:00:00Z"))
        before = len(_room_with(app, room).current.messages)

        async def _fake_turn(**kwargs):
            assert app.set_room_bots(room, "helper", "remove") == ["coder"]
            return _Result6(reply="late reply", bot_id="helper",
                            span_id="sp_race1")

        app._runtime.run_bot_turn_detailed = _fake_turn  # type: ignore[method-assign]
        bus = app._runtime._bus
        bus.register("helper")
        envelope = bus.send(sender="coder", recipient="helper", kind="message",
                            session_id=room, payload="member task")
        assert _deliver(app, envelope, "coder", room) == "late reply"
        rows = _room_with(app, room).current.messages
        bus_rows = [r for r in rows
                    if (r.meta or {}).get("bus_event") is True
                    and (r.meta or {}).get("span_id") == envelope.span_id]
        assert len(bus_rows) == 1, "eligible request row stays"
        assert [r for r in rows if (r.meta or {}).get("kind") == "result"] == []
        assert all("late reply" not in (r.content or "") for r in rows)
        assert len(rows) == before + 1
    finally:
        app.shutdown()


def test_unrelated_drained_envelope_does_not_authorize_current(tmp_path: Path) -> None:
    # An eligible drained envelope records itself but never authorizes the
    # pinned private current envelope's result.
    from thyca.bots.runtime import BotTurnResult as _Result7
    app = _chat(tmp_path)
    try:
        room = _private_group(app)
        other_bus = app._runtime._bus
        other_bus.register("thyca")
        other_bus.send(sender="coder", recipient="thyca", kind="message",
                       session_id=room, payload="eligible unrelated")

        async def _fake_turn(**kwargs):
            return _Result7(reply="private answer", bot_id="helper",
                            span_id="sp_unrel1")

        app._runtime.run_bot_turn_detailed = _fake_turn  # type: ignore[method-assign]
        bus = app._runtime._bus
        bus.register("helper")
        current = bus.send(sender="coder", recipient="helper", kind="message",
                           session_id=room, payload="private task")
        assert _deliver(app, current, "coder", room) == "private answer"
        rows = _room_with(app, room).current.messages
        assert any((r.meta or {}).get("bus_event") is True
                   and r.content == "eligible unrelated" for r in rows)
        assert all("private task" not in (r.content or "") for r in rows)
        assert [r for r in rows if (r.meta or {}).get("kind") == "result"] == []
    finally:
        app.shutdown()

def test_private_nested_lease_rejection_stays_unpublished_real_root_turn(
    tmp_path: Path,
) -> None:
    # Real root turn: coder asks outside helper; helper sends back to the
    # still-running coder, whose lease rejects the nested delivery. The
    # asking turn's final recorder must not publish that private envelope.
    from seeded_config import seeded_config as _seeded8
    from thyca.configs import load as _load8, save as _save8
    from thyca.core.protocol import ToolCall as _ToolCall8
    from thyca.llms.llm_base import ChatReply as _ChatReply8

    OUTER_USER = "outer start NESTED-LEASE-1"
    TO_HELPER = "please help NESTED-LEASE-2"
    NESTED_BACK = "nested back NESTED-LEASE-3"

    class _FakeLLM8:
        # Routes coder/helper turns by prompt content through the nested flow.
        def __init__(self) -> None:
            self.prompts: list[str] = []

        # Fake provider routing helper/coder turns through the nested flow.
        async def chat(self, messages, tools=None):
            text = "\n".join(str(m.content or "") for m in messages)
            self.prompts.append(text)
            tool_done = any(m.role == "tool" for m in messages)
            if "PERSONA-HELPER" in text:
                if not tool_done:
                    return _ChatReply8(content=None, model="m", tool_calls=[
                        _ToolCall8(id="t-back", name="bot_send",
                                   arguments={"bot_id": "coder",
                                              "message": NESTED_BACK}),
                    ])
                return _ChatReply8(content="helper reply", model="m",
                                   usage={"prompt_tokens": 5, "completion_tokens": 1,
                                          "total_tokens": 6})
            if TO_HELPER in text and not tool_done:
                return _ChatReply8(content=None, model="m", tool_calls=[
                    _ToolCall8(id="t-out", name="bot_send",
                               arguments={"bot_id": "helper",
                                          "message": TO_HELPER}),
                ])
            return _ChatReply8(content="coder final", model="m",
                               usage={"prompt_tokens": 5, "completion_tokens": 1,
                                      "total_tokens": 6})

    llm = _FakeLLM8()
    _save8(_seeded8(), tmp_path / "config.json")
    app = ChatApp(tmp_path, _load8(tmp_path / "config.json"), connect=llm)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="P"))
        app._bots.create(BotCard(bot_id="helper", name="helper", role="Helper",
                                 description="Helps.", persona="PERSONA-HELPER"))
        room = app.create(bots=["coder", "thyca"])["id"]
        detail = app.turn(room, "outer start " + TO_HELPER)
        assert detail["reply"] == "coder final"
        assert any("helper reply" in pr for pr in llm.prompts), \
            "nested private helper must have run and replied"
        rows = _room_with(app, room).current.messages
        assert all(NESTED_BACK not in (r.content or "") for r in rows)
        assert [r for r in rows if (r.meta or {}).get("bus_event")] == []
        assert [r for r in rows if (r.meta or {}).get("kind") == "result"] == []
    finally:
        app.shutdown()


def test_private_result_uses_membership_checked_append(tmp_path: Path) -> None:
    # Removal racing the result append must not publish: group results go
    # through the membership-checked append with the recipient as actor.
    # Fails while results use the unchecked server row.
    from thyca.bots.runtime import BotTurnResult as _Result9
    app = _chat(tmp_path)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="P"))
        app._bots.create(BotCard(bot_id="helper", name="helper", role="Helper",
                                 description="Helps.", persona="P"))
        room = app.create(bots=["coder", "helper"])["id"]
        mgr = _room_with(app, room)
        mgr.append(Message(role="user", content="GROUP-ONLY-MARKER append race",
                           ts="2026-10-26T09:00:00Z"))
        removed: list[bool] = [False]
        import thyca.conversations.store.write_adapter as _wa9
        _orig_append_message = _wa9.append_message

        def _removing_append_message(root, ref, actor, message, *, intent):
            # A removal racing the result append commits first; the
            # membership-checked transaction must then fail closed.
            if (
                not removed[0]
                and getattr(message, "role", None) == "assistant"
                and isinstance(getattr(message, "meta", None), dict)
                and message.meta.get("kind") == "result"
            ):
                removed[0] = True
                assert app.set_room_bots(room, "helper", "remove") == ["coder"]
            return _orig_append_message(
                root, ref, actor, message, intent=intent)

        async def _fake_turn(**kwargs):
            return _Result9(reply="late reply", bot_id="helper",
                            span_id="sp_atomic1")

        app._runtime.run_bot_turn_detailed = _fake_turn  # type: ignore[method-assign]
        bus = app._runtime._bus
        bus.register("helper")
        envelope = bus.send(sender="coder", recipient="helper", kind="message",
                            session_id=room, payload="member task")
        import asyncio as _asyncio9
        from thyca.bots.context import BotTurnContext as _Ctx9, BotTurnScope as _Scope9
        scope = _Scope9(store=app._sessions.store, act=app._act,
                        tools=app._tools, memory=app._memory,
                        state=app._state, zone=app._zone)
        ctx = _Ctx9(sender_id="coder", room_id=room,
                    scope=scope, turn_cfg=app._current_cfg())
        with __import__("unittest.mock", fromlist=["patch"]).patch.object(
                _wa9, "append_message", _removing_append_message):
            reply = _asyncio9.run(app._deliver_envelope_claimed(envelope, ctx))
        assert reply == "late reply"
        assert removed[0], "removal must win the race in this test"
        rows = _room_with(app, room).current.messages
        assert all("late reply" not in (r.content or "") for r in rows)
        assert [r for r in rows if (r.meta or {}).get("kind") == "result"] == []
    finally:
        app.shutdown()


def test_private_skips_relevant_flagged_mail_and_tasks(tmp_path: Path) -> None:
    # Relevant flagged mail (group origin, readable by the member view)
    # must not enter private invocations, normal member prompts, or the
    # task API; every flagged envelope stays queued afterwards.
    from seeded_config import seeded_config as _seeded10
    from thyca.configs import load as _load10, save as _save10
    from thyca.llms.llm_base import ChatReply as _ChatReply10

    FLAGGED_FOR_CODER = "flagged queued FLAGMAIL-10"
    FLAGGED_FOR_HELPER = "flagged queued FLAGMAIL-11"
    PRIVATE_TASK = "private task SKIPS-MAIL-11"
    GROUP_MARKER = "GROUP-ONLY-MARKER-SKIP-12"

    class _FakeLLM10:
        # Captures prompts; answers without tools.
        def __init__(self) -> None:
            self.prompts: list[str] = []

        # Fake provider capturing prompts; answers without tools.
        async def chat(self, messages, tools=None):
            self.prompts.append("\n".join(str(m.content or "") for m in messages))
            return _ChatReply10(content="private answer", model="m",
                                usage={"prompt_tokens": 5, "completion_tokens": 1,
                                       "total_tokens": 6})

    llm = _FakeLLM10()
    _save10(_seeded10(), tmp_path / "config.json")
    app = ChatApp(tmp_path, _load10(tmp_path / "config.json"), connect=llm)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="P"))
        app._bots.create(BotCard(bot_id="helper", name="helper", role="Helper",
                                 description="Helps.", persona="P"))
        room = app.create(bots=["coder", "thyca"])["id"]
        _room_with(app, room).append(Message(role="user", content=GROUP_MARKER,
                                             ts="2026-10-26T09:00:00Z"))
        bus = app._runtime._bus
        bus.register("helper")
        bus.register("coder")
        flagged_coder = bus.send(sender="helper", recipient="coder", kind="message",
                                 session_id=room, payload=FLAGGED_FOR_CODER,
                                 private_chain=True)
        flagged_helper = bus.send(sender="thyca", recipient="helper", kind="message",
                                  session_id=room, payload=FLAGGED_FOR_HELPER,
                                  private_chain=True)
        # The task API must not surface flagged payloads as titles.
        coder_tasks = app.bot_room_tasks(room, "coder")["tasks"]
        assert all(FLAGGED_FOR_CODER not in row.get("title", "")
                   for row in coder_tasks)
        assert all(FLAGGED_FOR_HELPER not in row.get("title", "")
                   for row in coder_tasks)
        import asyncio as _asyncio10
        from thyca.bots.context import BotTurnContext as _Ctx10, BotTurnScope as _Scope10
        scope = _Scope10(store=app._sessions.store, act=app._act,
                         tools=app._tools, memory=app._memory,
                         state=app._state, zone=app._zone)
        # Mechanism pin: a private turn must not even drain the mailbox.
        import thyca.bots.runtime as _rt10
        _orig_prepend = _rt10.BotRuntime._prepend_mailbox
        _prepend_calls: list[str] = []

        def _counting_prepend(self, bot_id, room_id, text):
            _prepend_calls.append(bot_id)
            return _orig_prepend(self, bot_id, room_id, text)

        ctx = _Ctx10(sender_id="coder", room_id=room,
                     scope=scope, turn_cfg=app._current_cfg())
        envelope = bus.send(sender="coder", recipient="helper", kind="message",
                            session_id=room, payload=PRIVATE_TASK,
                            private_chain=True)
        with __import__("unittest.mock", fromlist=["patch"]).patch.object(
                _rt10.BotRuntime, "_prepend_mailbox", _counting_prepend):
            assert _asyncio10.run(app._deliver_envelope(envelope, ctx)) == "private answer"
        assert _prepend_calls == [], "private must skip the mailbox drain"
        assert any(PRIVATE_TASK in pr for pr in llm.prompts)
        assert all(FLAGGED_FOR_CODER not in pr for pr in llm.prompts)
        assert all(FLAGGED_FOR_HELPER not in pr for pr in llm.prompts)
        assert all(GROUP_MARKER not in pr for pr in llm.prompts)
        assert bus.pending("helper") == 1, "flagged helper mail stays queued"
        assert bus.pending("coder") == 1, "flagged coder mail stays queued"
        rows = _room_with(app, room).current.messages
        assert all(FLAGGED_FOR_CODER not in (r.content or "") for r in rows)
        assert all(FLAGGED_FOR_HELPER not in (r.content or "") for r in rows)
        assert [r for r in rows if (r.meta or {}).get("bus_event")] == []
        # A subsequent normal member turn still excludes the flagged mail.
        llm.prompts.clear()
        member_env = bus.send(sender="thyca", recipient="coder", kind="message",
                              session_id=room, payload="member follow-up")
        _scope_m = _Scope10(store=app._sessions.store, act=app._act,
                            tools=app._tools, memory=app._memory,
                            state=app._state, zone=app._zone)
        _ctx_m = _Ctx10(sender_id="thyca", room_id=room,
                        scope=_scope_m, turn_cfg=app._current_cfg())
        assert _asyncio10.run(app._deliver_envelope(member_env, _ctx_m)) == "private answer"
        assert all(FLAGGED_FOR_CODER not in pr for pr in llm.prompts)
        assert all(FLAGGED_FOR_HELPER not in pr for pr in llm.prompts)
        # The member turn consumed only its own envelope; both flagged
        # mails stay queued.
        assert bus.pending("coder") == 1, "flagged mail stays queued after member turn"
        assert bus.pending("helper") == 1
    finally:
        app.shutdown()


def test_private_rejoin_failure_compaction_and_second_private(tmp_path: Path) -> None:
    # Former member: private success, then private model failure (v2 error
    # reset), then rejoin. Group projection keeps earlier messages and
    # metadata, excludes every private row; a second private invocation
    # replays nothing; operations after rejoin select group context.
    from seeded_config import seeded_config as _seeded11
    from thyca.configs import load as _load11, save as _save11
    from thyca.llms.llm_base import ChatReply as _ChatReply11

    MEMBER_TASK = "member task REJOIN-1"
    PRIVATE_ONE = "private one REJOIN-2"
    PRIVATE_TWO = "private two REJOIN-3"
    GROUP_MARKER = "GROUP-ONLY-MARKER-REJOIN-4"

    class _FakeLLM11:
        # Fails exactly one private turn to mint a v2 error reset.
        def __init__(self) -> None:
            self.prompts: list[str] = []
            self.fail_next = False

        # Fake provider: fails exactly one private turn to mint a v2 error reset.
        async def chat(self, messages, tools=None):
            self.prompts.append("\n".join(str(m.content or "") for m in messages))
            if self.fail_next:
                self.fail_next = False
                raise RuntimeError("model down")
            return _ChatReply11(content="reply ok", model="m",
                                usage={"prompt_tokens": 5, "completion_tokens": 1,
                                       "total_tokens": 6})

    llm = _FakeLLM11()
    _save11(_seeded11(), tmp_path / "config.json")
    app = ChatApp(tmp_path, _load11(tmp_path / "config.json"), connect=llm)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="P"))
        app._bots.create(BotCard(bot_id="helper", name="helper", role="Helper",
                                 description="Helps.", persona="P"))
        room = app.create(bots=["coder", "helper"])["id"]
        mgr = _room_with(app, room)
        mgr.append(Message(role="user", content=f"{GROUP_MARKER} coordinate",
                           ts="2026-10-26T09:00:00Z"))
        import asyncio as _asyncio11
        from thyca.bots.context import BotTurnContext as _Ctx11, BotTurnScope as _Scope11

        def _ctx11(sender: str):
            scope = _Scope11(store=app._sessions.store, act=app._act,
                             tools=app._tools, memory=app._memory,
                             state=app._state, zone=app._zone)
            return _Ctx11(sender_id=sender, room_id=room,
                          scope=scope, turn_cfg=app._current_cfg())

        bus = app._runtime._bus
        bus.register("helper")
        member_env = bus.send(sender="coder", recipient="helper", kind="message",
                              session_id=room, payload=MEMBER_TASK)
        assert _asyncio11.run(app._deliver_envelope(member_env, _ctx11("coder"))) == "reply ok"
        assert app.set_room_bots(room, "helper", "remove") == ["coder"]
        llm.prompts.clear()
        first = bus.send(sender="coder", recipient="helper", kind="message",
                         session_id=room, payload=PRIVATE_ONE)
        assert _asyncio11.run(app._deliver_envelope(first, _ctx11("coder"))) == "reply ok"
        assert any(PRIVATE_ONE in pr for pr in llm.prompts)
        assert all(GROUP_MARKER not in pr for pr in llm.prompts)
        assert all(MEMBER_TASK not in pr for pr in llm.prompts)
        llm.fail_next = True
        doomed = bus.send(sender="coder", recipient="helper", kind="message",
                          session_id=room, payload="doomed private")
        assert _asyncio11.run(app._deliver_envelope(doomed, _ctx11("coder"))).startswith(
            "delivery failed")
        # Group projection is unchanged by the private failure reset.
        from thyca.bots.timeline import WorkspaceTimeline as _TL11
        group_window = app._webui.window_for(app._sessions.store.load(room))
        helper_binding = app._runtime.workspaces.store.get_binding("helper", room)
        assert helper_binding is not None and helper_binding.internal_session_id
        helper_tl = _TL11(tmp_path, "helper", create=False)
        assert all("doomed private" not in m.content
                   for m in helper_tl.project(
                       group_window,
                       session_id=helper_binding.internal_session_id).messages)
        # Second private invocation replays nothing from the first.
        llm.prompts.clear()
        second = bus.send(sender="coder", recipient="helper", kind="message",
                          session_id=room, payload=PRIVATE_TWO)
        assert _asyncio11.run(app._deliver_envelope(second, _ctx11("coder"))) == "reply ok"
        assert all(PRIVATE_ONE not in pr for pr in llm.prompts)
        assert all("reply ok" not in pr or PRIVATE_TWO in pr for pr in llm.prompts)
        # Rejoin: member delivery sees fresh group history, never private rows.
        # (The original group marker was already read by the first member
        # turn, so the control uses a new group message after rejoining.)
        assert app.set_room_bots(room, "helper", "add") == ["coder", "helper"]
        _room_with(app, room).append(Message(role="user", content="GROUP-NEWS after rejoin",
                                             ts="2026-10-26T10:00:00Z"))
        llm.prompts.clear()
        back = bus.send(sender="coder", recipient="helper", kind="message",
                        session_id=room, payload="welcome back")
        assert _asyncio11.run(app._deliver_envelope(back, _ctx11("coder"))) == "reply ok"
        assert any("GROUP-NEWS after rejoin" in pr for pr in llm.prompts)
        assert all(PRIVATE_ONE not in pr for pr in llm.prompts)
        assert all(PRIVATE_TWO not in pr for pr in llm.prompts)
        assert all("doomed private" not in pr for pr in llm.prompts)
        # Manual compaction after rejoin must actually reset: seed enough
        # canonical history to force a rewrite, then verify the new group
        # basis carries no private spans.
        from thyca.bots.timeline import WorkspaceTimeline as _TLR
        from thyca.core.protocol import Message as _MsgR
        _seed_tl = _TLR(tmp_path, "helper", create=False)
        _seed_window = app._webui.window_for(app._sessions.store.load(room))
        for _i in range(400):
            _seed_tl.append_message(
                _seed_window,
                _MsgR(role="user", content=f"filler {_i} " + "x" * 2000),
                event_id=f"seed-fill-{_i:04d}")
        compacted = app.compact_session(room)
        helper_report = compacted["compaction"]["per_bot"]["helper"]
        assert helper_report.get("compacted") is True, helper_report
        default_after = helper_tl.project(
            group_window, session_id=helper_binding.internal_session_id)
        assert all(PRIVATE_ONE not in (m.content or "") for m in default_after.messages)
        assert all(PRIVATE_TWO not in (m.content or "") for m in default_after.messages)
        assert all("doomed private" not in (m.content or "") for m in default_after.messages)
        # Operations after rejoin select group context, not private spans.
        ops = app.bot_operations(room, "helper")
        assert ops["bot_id"] == "helper"
        from thyca.bots.timeline import WorkspaceTimeline as _TL12
        history = _TL12(tmp_path, "helper", create=False).messages_for_window(
            group_window)
        assert all(PRIVATE_ONE not in (m.content or "") for m in history)
        assert all(PRIVATE_TWO not in (m.content or "") for m in history)
    finally:
        app.shutdown()


def test_private_changed_roster_memory_stays_denied_real_model(tmp_path: Path) -> None:
    # Real runtime: helper is an outsider at dispatch, joins mid-turn, yet
    # the pinned group denial still hides team memory; a later member turn
    # reads it, proving the seed and the later successful read.
    from seeded_config import seeded_config as _seeded12
    from thyca.configs import load as _load12, save as _save12
    from thyca.core.protocol import ToolCall as _ToolCall12
    from thyca.llms.llm_base import ChatReply as _ChatReply12
    from thyca.memories.facade import MemoryFacade as _Facade12

    TEAM_MARKER = "TEAMMARKERDENY12"
    CHOSEN = "chosen task DENY-ROSTER-13"
    GROUP_MARKER = "GROUP-ONLY-MARKER-DENY-14"

    holder: dict = {}

    class _FakeLLM12:
        # Searches team memory, then answers; joins mid-turn on first call.
        def __init__(self) -> None:
            self.prompts: list[str] = []

        # Fake provider: joins mid-turn, searches team memory, then answers.
        async def chat(self, messages, tools=None):
            text = "\n".join(str(m.content or "") for m in messages)
            tool_text = "\n".join(
                str(m.content or "") for m in messages if m.role == "tool")
            self.prompts.append(text + "\n---TOOL---\n" + tool_text)
            if not any(m.role == "tool" for m in messages):
                app = holder["app"]
                app.set_room_bots(holder["room"], "helper", "add")
                return _ChatReply12(content=None, model="m", tool_calls=[
                    _ToolCall12(id="t-s", name="memory_search",
                                arguments={"query": TEAM_MARKER}),
                ])
            return _ChatReply12(content="private answer", model="m",
                                usage={"prompt_tokens": 5, "completion_tokens": 1,
                                       "total_tokens": 6})

    llm = _FakeLLM12()
    _save12(_seeded12(), tmp_path / "config.json")
    app = ChatApp(tmp_path, _load12(tmp_path / "config.json"), connect=llm)
    holder["app"] = app
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="P"))
        app._bots.create(BotCard(bot_id="helper", name="helper", role="Helper",
                                 description="Helps.", persona="P"))
        room = app.create(bots=["coder", "thyca"])["id"]
        holder["room"] = room
        mgr = _room_with(app, room)
        mgr.append(Message(role="user", content=f"{GROUP_MARKER} coordinate",
                           ts="2026-10-26T09:00:00Z"))
        from datetime import datetime as _dt12, timedelta as _td12
        from zoneinfo import ZoneInfo as _ZI12
        _yesterday = (_dt12.now(_ZI12("Asia/Ho_Chi_Minh")).date() - _td12(days=1)).isoformat()
        _at = _dt12.fromisoformat(f"{_yesterday}T12:00:00+07:00")
        facade = _Facade12(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
        facade.remember(topic=f"team topic {TEAM_MARKER}",
                        summary=f"{TEAM_MARKER} body", chat=room, now=_at)
        facade._refresh_index()
        assert any(TEAM_MARKER in (h.snippet or "") or TEAM_MARKER in (h.heading or "")
                   for h in facade.search(TEAM_MARKER).hits)
        bus = app._runtime._bus
        bus.register("helper")
        envelope = bus.send(sender="coder", recipient="helper", kind="message",
                            session_id=room, payload=CHOSEN)
        import asyncio as _asyncio12
        from thyca.bots.context import BotTurnContext as _Ctx12, BotTurnScope as _Scope12
        scope = _Scope12(store=app._sessions.store, act=app._act,
                         tools=app._tools, memory=app._memory,
                         state=app._state, zone=app._zone)
        ctx = _Ctx12(sender_id="coder", room_id=room,
                     scope=scope, turn_cfg=app._current_cfg())
        assert _asyncio12.run(app._deliver_envelope(envelope, ctx)) == "private answer"
        assert app.room_bots(room) == ["coder", "thyca", "helper"]
        assert any(CHOSEN in pr for pr in llm.prompts)
        assert all(GROUP_MARKER not in pr for pr in llm.prompts)
        assert all(TEAM_MARKER not in pr for pr in llm.prompts), \
            "pinned denial must survive mid-turn membership gain"
        rows = _room_with(app, room).current.messages
        assert [r for r in rows if (r.meta or {}).get("bus_event")] == []
        assert [r for r in rows if (r.meta or {}).get("kind") == "result"] == []
        # Later member turn reads the same seed: proves the fixture and
        # that only the pinned private invocation was denied.
        llm.prompts.clear()
        member_env = bus.send(sender="coder", recipient="helper", kind="message",
                              session_id=room, payload="member follow-up")
        assert _asyncio12.run(app._deliver_envelope(member_env, ctx)) == "private answer"
        assert any(TEAM_MARKER in pr for pr in llm.prompts), \
            "member turn must read the team seed the private turn could not"
    finally:
        app.shutdown()

def test_predrained_request_still_authorizes_member_result(tmp_path: Path) -> None:
    # A first recorder drains and publishes the request; a second pinned
    # recorder draining nothing must still recognize the eligible request.
    from thyca.bots.runtime import BotTurnResult as _Result13
    app = _chat(tmp_path)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="P"))
        app._bots.create(BotCard(bot_id="helper", name="helper", role="Helper",
                                 description="Helps.", persona="P"))
        room = app.create(bots=["coder", "helper"])["id"]
        mgr = _room_with(app, room)
        mgr.append(Message(role="user", content="GROUP-ONLY-MARKER predrain",
                           ts="2026-10-26T09:00:00Z"))
        bus = app._runtime._bus
        bus.register("helper")
        first = bus.send(sender="coder", recipient="helper", kind="message",
                         session_id=room, payload="first task")
        second = bus.send(sender="coder", recipient="helper", kind="message",
                          session_id=room, payload="second task")
        first_room = _room_with(app, room)
        assert app._delivery.record_bus_events(first_room, room) is True
        assert any((m.meta or {}).get("span_id") == first.span_id
                   for m in first_room.current.messages
                   if (m.meta or {}).get("bus_event") is True)

        async def _fake_turn(**kwargs):
            return _Result13(reply="second reply", bot_id="helper",
                             span_id="sp_pre1")

        app._runtime.run_bot_turn_detailed = _fake_turn  # type: ignore[method-assign]
        import asyncio as _asyncio13
        from thyca.bots.context import BotTurnContext as _Ctx13, BotTurnScope as _Scope13
        scope = _Scope13(store=app._sessions.store, act=app._act,
                         tools=app._tools, memory=app._memory,
                         state=app._state, zone=app._zone)
        ctx = _Ctx13(sender_id="coder", room_id=room,
                     scope=scope, turn_cfg=app._current_cfg())
        assert _asyncio13.run(app._deliver_envelope_claimed(second, ctx)) == "second reply"
        rows = _room_with(app, room).current.messages
        assert any(r.content == "second reply"
                   and (r.meta or {}).get("kind") == "result" for r in rows)
        # DM twin: an unpinned drain publishes the DM pair, and a later
        # pinned delivery with an empty drain still recognizes it.
        dm = app.bot_dm("coder")["id"]
        _room_with(app, dm).append(Message(role="user", content="dm seed",
                                            ts="2026-10-26T09:00:00Z"))
        dm_second = bus.send(sender="coder", recipient="helper", kind="message",
                             session_id=dm, payload="dm second")
        assert app._delivery.record_bus_events(_room_with(app, dm), dm) is True
        dm_ctx = _Ctx13(sender_id="coder", room_id=dm, scope=scope,
                        turn_cfg=app._current_cfg())
        assert bus.drain_log(dm) == [], "drain must be empty before pinned delivery"
        assert _asyncio13.run(app._deliver_envelope_claimed(dm_second, dm_ctx)) == "second reply"
        assert any(r.content == "second reply"
                   for r in _room_with(app, dm).current.messages)
    finally:
        app.shutdown()


def test_private_cancelled_turn_leaves_no_group_trace(tmp_path: Path) -> None:
    # Cancellation mid-model writes a v2 error reset that the group
    # projection ignores; nothing is published and membership is intact.
    import asyncio as _asyncio14
    from seeded_config import seeded_config as _seeded14
    from thyca.configs import load as _load14, save as _save14

    # Fake provider that cancels mid-model like a dropped call.
    class _CancellingLLM:
        # Raises cancellation instead of replying, mid-model.
        async def chat(self, messages, tools=None):
            raise _asyncio14.CancelledError()

    llm = _CancellingLLM()
    _save14(_seeded14(), tmp_path / "config.json")
    app = ChatApp(tmp_path, _load14(tmp_path / "config.json"), connect=llm)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="P"))
        app._bots.create(BotCard(bot_id="helper", name="helper", role="Helper",
                                 description="Helps.", persona="P"))
        room = app.create(bots=["coder", "thyca"])["id"]
        mgr = _room_with(app, room)
        mgr.append(Message(role="user", content="GROUP-ONLY-MARKER cancel",
                           ts="2026-10-26T09:00:00Z"))
        before = len(_room_with(app, room).current.messages)
        from thyca.bots.context import BotTurnContext as _Ctx14, BotTurnScope as _Scope14
        scope = _Scope14(store=app._sessions.store, act=app._act,
                         tools=app._tools, memory=app._memory,
                         state=app._state, zone=app._zone)
        ctx = _Ctx14(sender_id="coder", room_id=room,
                     scope=scope, turn_cfg=app._current_cfg())
        bus = app._runtime._bus
        bus.register("helper")
        envelope = bus.send(sender="coder", recipient="helper", kind="message",
                            session_id=room, payload="cancelled task")
        with __import__("pytest").raises(_asyncio14.CancelledError):
            _asyncio14.run(app._deliver_envelope(envelope, ctx))
        rows = _room_with(app, room).current.messages
        assert len(rows) == before
        assert [r for r in rows if (r.meta or {}).get("bus_event")] == []
        assert [r for r in rows if (r.meta or {}).get("kind") == "result"] == []
        assert app.room_bots(room) == ["coder", "thyca"]
        from thyca.bots.timeline import WorkspaceTimeline as _TL14
        helper_tl = _TL14(tmp_path, "helper", create=False)
        group_window = app._webui.window_for(app._sessions.store.load(room))
        binding = app._runtime.workspaces.store.get_binding("helper", room)
        assert binding is not None and binding.internal_session_id
        default = helper_tl.project(group_window,
                                    session_id=binding.internal_session_id)
        assert all("cancelled task" not in (m.content or "") for m in default.messages)
    finally:
        app.shutdown()


def test_private_early_failures_publish_nothing_later(tmp_path: Path) -> None:
    # Unknown/paused recipients and room-load failures never record; the
    # flagged log row (if any) stays unpublished by later unpinned drains.
    from thyca.bots.runtime import BotTurnResult as _Result15
    app = _chat(tmp_path)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="P"))
        app._bots.create(BotCard(bot_id="helper", name="helper", role="Helper",
                                 description="Helps.", persona="P"))
        room = app.create(bots=["coder", "helper"])["id"]
        _room_with(app, room).append(Message(role="user", content="seed",
                                             ts="2026-10-26T09:00:00Z"))
        other = app.create(bots=["coder", "thyca"])["id"]
        bus = app._runtime._bus
        bus.register("helper")
        import asyncio as _asyncio15
        from thyca.bots.context import BotTurnContext as _Ctx15, BotTurnScope as _Scope15

        def _ctx15(sender: str, rid: str):
            scope = _Scope15(store=app._sessions.store, act=app._act,
                             tools=app._tools, memory=app._memory,
                             state=app._state, zone=app._zone)
            return _Ctx15(sender_id=sender, room_id=rid,
                          scope=scope, turn_cfg=app._current_cfg())

        bus.register("ghost")
        ghost = bus.send(sender="coder", recipient="ghost", kind="message",
                         session_id=room, payload="ghost task")
        assert _asyncio15.run(
            app._deliver_envelope_claimed(ghost, _ctx15("coder", room))).startswith(
            "delivery failed")
        app._bots.create(BotCard(bot_id="sleepy", name="sleepy", role="S",
                                 description="Sleeps.", persona="P"))
        app._bots.set_paused("sleepy", True)
        bus.register("sleepy")
        nap = bus.send(sender="coder", recipient="sleepy", kind="message",
                       session_id=room, payload="nap task")
        assert "paused" in _asyncio15.run(
            app._deliver_envelope_claimed(nap, _ctx15("coder", room)))
        rows = _room_with(app, room).current.messages
        assert all("ghost task" not in (r.content or "") for r in rows)
        assert all("nap task" not in (r.content or "") for r in rows)
        # A flagged private envelope stays unpublished by later unpinned
        # drains (e.g. a room/root turn's final recorder).
        bus.register("coder")
        flagged = bus.send(sender="helper", recipient="coder", kind="message",
                           session_id=room, payload="flagged private",
                           private_chain=True)
        assert bus.pending("coder") == 1
        idle = _room_with(app, room)
        assert app._delivery.record_bus_events(idle, room) is True
        assert bus.pending("coder") == 1, "flagged mail stays queued"
        after = [r for r in _room_with(app, room).current.messages
                 if "flagged private" in (r.content or "")]
        assert after == [], "flagged request never enters the transcript"
    finally:
        app.shutdown()


def test_private_result_write_failure_still_returns_reply(tmp_path: Path) -> None:
    # A result-write failure withholds the row but still returns the reply.
    from thyca.bots.runtime import BotTurnResult as _Result16
    app = _chat(tmp_path)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="P"))
        app._bots.create(BotCard(bot_id="helper", name="helper", role="Helper",
                                 description="Helps.", persona="P"))
        room = app.create(bots=["coder", "helper"])["id"]
        mgr = _room_with(app, room)
        mgr.append(Message(role="user", content="GROUP-ONLY-MARKER write fail",
                           ts="2026-10-26T09:00:00Z"))
        before = len(_room_with(app, room).current.messages)

        async def _fake_turn(**kwargs):
            return _Result16(reply="unstored reply", bot_id="helper",
                             span_id="sp_wf1")

        app._runtime.run_bot_turn_detailed = _fake_turn  # type: ignore[method-assign]
        bus = app._runtime._bus
        bus.register("helper")
        envelope = bus.send(sender="coder", recipient="helper", kind="message",
                            session_id=room, payload="stored task")
        import asyncio as _asyncio16
        from thyca.bots.context import BotTurnContext as _Ctx16, BotTurnScope as _Scope16
        scope = _Scope16(store=app._sessions.store, act=app._act,
                         tools=app._tools, memory=app._memory,
                         state=app._state, zone=app._zone)
        ctx = _Ctx16(sender_id="coder", room_id=room,
                     scope=scope, turn_cfg=app._current_cfg())
        import thyca.conversations.store.write_adapter as _wa16
        real_append = _wa16.append_message

        def _boom(root, ref, actor, message, *, intent):
            import thyca.conversations.store.write_adapter as _w
            if isinstance(getattr(message, "meta", None), dict) and message.meta.get(
                    "kind") == "result":
                raise RuntimeError("disk gone")
            return real_append(root, ref, actor, message, intent=intent)

        with __import__("unittest.mock", fromlist=["patch"]).patch.object(
                _wa16, "append_message", _boom):
            reply = _asyncio16.run(app._deliver_envelope_claimed(envelope, ctx))
        assert reply == "unstored reply"
        rows = _room_with(app, room).current.messages
        assert [r for r in rows if (r.meta or {}).get("kind") == "result"] == []
        assert any((r.meta or {}).get("bus_event") is True for r in rows)
        assert len(rows) == before + 1, "only the eligible request row lands"
    finally:
        app.shutdown()

def test_exact_request_match_rejects_mismatched_group_and_dm_rows(tmp_path: Path) -> None:
    # Same span but different sender/recipient/trace/payload/refs must not
    # authorize a result, for group rooms and DMs alike. The decoy is the
    # only logged row, so only an exact field match could publish.
    from thyca.bots.bus import BusEvent as _BusEvent
    from thyca.bots.runtime import BotTurnResult as _Result18
    from thyca.core.protocol import Message as _Msg18
    app = _chat(tmp_path)
    try:
        for bot_id in ("coder", "helper"):
            app._bots.create(BotCard(bot_id=bot_id, name=bot_id, role="R",
                                     description="D", persona="P"))
        room = app.create(bots=["coder", "helper"])["id"]
        # Seed before opening the DM: blank group rooms are pruned on create.
        _room_with(app, room).append(_Msg18(role="user", content="seed",
                                            ts="2026-10-26T09:00:00Z"))
        dm = app.bot_dm("coder")["id"]
        mgr = _room_with(app, room)
        mgr.append(_Msg18(role="user", content="GROUP-ONLY-MARKER exact match",
                          ts="2026-10-26T09:00:00Z"))

        async def _fake_turn(**kwargs):
            return _Result18(reply="exact reply", bot_id="helper",
                             span_id="sp_exact1")

        app._runtime.run_bot_turn_detailed = _fake_turn  # type: ignore[method-assign]
        import asyncio as _asyncio18
        from thyca.bots.context import BotTurnContext as _Ctx18, BotTurnScope as _Scope18

        def _ctx18(sender: str, rid: str):
            scope = _Scope18(store=app._sessions.store, act=app._act,
                             tools=app._tools, memory=app._memory,
                             state=app._state, zone=app._zone)
            return _Ctx18(sender_id=sender, room_id=rid,
                          scope=scope, turn_cfg=app._current_cfg())

        bus = app._runtime._bus
        bus.register("helper")
        current = bus.send(sender="coder", recipient="helper", kind="message",
                           session_id=room, payload="exact task",
                           refs=["/tmp/real.md"])
        # Drop the true log row; the decoy is the only same-span row left.
        dropped = bus.drain_log(room)
        assert any(e.envelope.span_id == current.span_id for e in dropped)
        # Forge same-span rows that differ in exactly one trusted field.
        forgeries = [
            dict(sender="mallory"),
            dict(recipient="ghost"),
            dict(trace_id="tr_forged"),
            dict(payload="forged task"),
            dict(refs=["/tmp/forged.md"]),
        ]
        from thyca.bots.bus import Envelope as _Env18
        for i, change in enumerate(forgeries):
            base = dict(sender=current.sender, recipient=current.recipient,
                        kind=current.kind, session_id=current.session_id,
                        payload=current.payload, trace_id=current.trace_id,
                        parent_span_id=current.parent_span_id,
                        ts=current.ts, span_id=current.span_id,
                        refs=list(current.refs), truncated=current.truncated,
                        private_chain=current.private_chain)
            base.update(change)
            forged = _Env18(**base)
            bus.register("ghost")
            app._runtime._bus._log.append(_BusEvent(envelope=forged, hops=0))
            before = len(_room_with(app, room).current.messages)
            reply = _asyncio18.run(
                app._deliver_envelope_claimed(current, _ctx18("coder", room)))
            assert reply == "exact reply", f"reply must return for forgery {i}"
            rows = _room_with(app, room).current.messages
            assert len(rows) == before, \
                f"mismatched row {i} must publish nothing"
            assert all(r.content != "exact reply" for r in rows)
        # DM twin: same span, different payload in the DM log.
        dm_mgr = _room_with(app, dm)
        dm_mgr.append(_Msg18(role="user", content="dm seed",
                             ts="2026-10-26T09:00:00Z"))
        dm_env = bus.send(sender="coder", recipient="helper", kind="message",
                          session_id=dm, payload="dm task")
        bus.drain_log(dm)
        forged_dm = _Env18(
            sender="coder", recipient="helper", kind="message",
            session_id=dm, payload="forged dm task",
            trace_id=dm_env.trace_id, parent_span_id=dm_env.parent_span_id,
            ts=dm_env.ts, span_id=dm_env.span_id, refs=(), truncated=False,
            private_chain=False)
        app._runtime._bus._log.append(_BusEvent(envelope=forged_dm, hops=0))
        before_dm = len(_room_with(app, dm).current.messages)
        reply = _asyncio18.run(
            app._deliver_envelope_claimed(dm_env, _ctx18("coder", dm)))
        assert reply == "exact reply"
        assert len(_room_with(app, dm).current.messages) == before_dm
    finally:
        app.shutdown()

def test_initial_outsider_room_load_failure_stays_unpublished(tmp_path: Path) -> None:
    # The first outsider request is classified at dispatch; a transient
    # room.load failure must not leave a publishable row behind, even if
    # the recipient joins before the next unpinned drain.
    from thyca.bots.runtime import BotTurnResult as _Result18b
    from thyca.conversations.runtime import SessionManager as _SM18b
    app = _chat(tmp_path)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="P"))
        app._bots.create(BotCard(bot_id="helper", name="helper", role="Helper",
                                 description="Helps.", persona="P"))
        room = app.create(bots=["coder", "thyca"])["id"]
        _room_with(app, room).append(Message(role="user", content="seed",
                                             ts="2026-10-26T09:00:00Z"))
        before = len(_room_with(app, room).current.messages)

        async def _fake_turn(**kwargs):
            return _Result18b(reply="private answer", bot_id="helper",
                              span_id="sp_loadfail1")

        app._runtime.run_bot_turn_detailed = _fake_turn  # type: ignore[method-assign]
        bus = app._runtime._bus
        bus.register("helper")
        envelope = bus.send(sender="coder", recipient="helper", kind="message",
                            session_id=room, payload="first ask")
        import asyncio as _asyncio18b
        from thyca.bots.context import BotTurnContext as _Ctx18b, BotTurnScope as _Scope18b

        def _ctx18b(sender: str):
            scope = _Scope18b(store=app._sessions.store, act=app._act,
                              tools=app._tools, memory=app._memory,
                              state=app._state, zone=app._zone)
            return _Ctx18b(sender_id=sender, room_id=room,
                           scope=scope, turn_cfg=app._current_cfg())

        real_load = _SM18b.load
        calls: list[str] = []

        def _fail_once(self, session_id: str):
            if not calls:
                calls.append(session_id)
                raise RuntimeError("transient store outage")
            return real_load(self, session_id)

        with __import__("unittest.mock", fromlist=["patch"]).patch.object(
                _SM18b, "load", _fail_once):
            assert _asyncio18b.run(
                app._deliver_envelope_claimed(envelope, _ctx18b("coder"))) == "private answer"
        assert calls == [room]
        # Dispatch expunged the log row before the failed load, so no
        # later drain can publish the classified-private request.
        assert all(e.envelope.span_id != envelope.span_id
                   for e in bus.log()), "failed dispatch must leave no log row"
        # The recipient joins before any later unpinned drain runs.
        assert app.set_room_bots(room, "helper", "add") == ["coder", "thyca", "helper"]
        idle = _room_with(app, room)
        assert app._delivery.record_bus_events(idle, room) is True
        rows = _room_with(app, room).current.messages
        assert len(rows) == before
        assert all("first ask" not in (r.content or "") for r in rows)
        assert [r for r in rows if (r.meta or {}).get("kind") == "result"] == []
    finally:
        app.shutdown()


def _cancel_and_await_parked(app: ChatApp, tasks: dict) -> dict:
    """Cancel parked app-loop delivery tasks and await their full unwind.

    The one shared cleanup routine for the concurrent failure tests:
    every recorded task is cancelled first, so one bad join can never
    leave a sibling parked; each task is then awaited to real
    completion on the app loop (not just the caller's thread futures).
    Cancellation of a parked task is the expected outcome. A missing
    task or an already-finished task is reported, never hidden.
    Unexpected join errors are collected from every task and raised
    together instead of being swallowed, so a test can prove cleanup
    really happened before shutdown."""
    import asyncio as _aio_shared

    async def _join_all():
        outcomes = {}
        to_join = []
        for _key in ("delivery", "member"):
            _task = tasks.get(_key)
            if _task is None:
                outcomes[_key] = "missing"
            elif _task.done():
                outcomes[_key] = "already-done"
            else:
                # Cancel now, join later: every task gets its
                # cancellation even if a sibling's join fails.
                _task.cancel()
                to_join.append((_key, _task))
        errors = []
        for _key, _task in to_join:
            try:
                await _task
            except _aio_shared.CancelledError:
                outcomes[_key] = "cancelled"
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise ExceptionGroup(
                "shared cleanup join failures", errors)
        return outcomes

    join_fut = _aio_shared.run_coroutine_threadsafe(
        _join_all(), app._loop)
    return join_fut.result(timeout=10)


def test_concurrent_lease_wait_then_cancel_publishes_nothing(tmp_path: Path) -> None:
    # An ordinary (non-denied) sender's initial outsider request is marked
    # at send time. While its delivery waits on the held recipient lease,
    # a concurrent member turn runs for real on the app loop and publishes,
    # an unpinned drain publishes nothing private (even after the recipient
    # joins), the waiting task is cancelled and its cleanup awaited, and
    # the task API hides the flagged mail before and after release.
    import asyncio as _asyncio19
    import time as _time19
    import threading as _threading19
    import concurrent.futures as _cfutures19
    send_fut = None
    hold_fut = None
    hold_consumed19 = False
    member_fut = None
    tasks: dict = {}
    from thyca.llms.llm_base import ChatReply as _ChatReply19
    from seeded_config import seeded_config as _seeded19
    from thyca.configs import load as _load19, save as _save19

    class _FakeLLM19:
        # Fake provider for the concurrent member turn (the private waiter
        # never reaches the model: it stays parked on the held lease).
        async def chat(self, messages, tools=None):
            return _ChatReply19(content="coder ack", model="m",
                                usage={"prompt_tokens": 5, "completion_tokens": 1,
                                       "total_tokens": 6})

    llm = _FakeLLM19()
    _save19(_seeded19(), tmp_path / "config.json")
    app = ChatApp(tmp_path, _load19(tmp_path / "config.json"), connect=llm)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="P"))
        app._bots.create(BotCard(bot_id="helper", name="helper", role="Helper",
                                 description="Helps.", persona="P"))
        room = app.create(bots=["coder", "thyca"])["id"]
        _room_with(app, room).append(Message(role="user", content="seed",
                                             ts="2026-10-26T09:00:00Z"))
        before = len(_room_with(app, room).current.messages)
        bus = app._runtime._bus
        bus.register("helper")
        bus.register("coder")
        scope = None
        from thyca.bots.context import BotTurnContext as _Ctx19, BotTurnScope as _Scope19
        scope = _Scope19(store=app._sessions.store, act=app._act,
                         tools=app._tools, memory=app._memory,
                         state=app._state, zone=app._zone)
        holder_started = _threading19.Event()
        release_holder = _threading19.Event()

        async def _hold_coder():
            # Hold the *recipient's* lease so the private delivery waits.
            async with app._runtime.workspaces.lease("helper"):
                holder_started.set()
                await app._loop.run_in_executor(None, release_holder.wait)

        tasks: dict = {}

        async def _cancel_and_join(task):
            # Shared cleanup helper: cancel one app-loop task and await
            # its full unwind on the loop (not just a thread future).
            task.cancel()
            try:
                await task
            except _asyncio19.CancelledError:
                return "cancelled"
            return "finished"


        async def _private_send():
            # Ordinary caller context: no denied view is bound here, so
            # only send-time classification can mark this private.
            # Record the real delivery task so the test can cancel and
            # await that exact task rather than only its thread future.
            from thyca.bots.context import bind_bot_turn_context as _bind19
            from thyca.bots.context import BotTurnContext as _ICtx19
            from thyca.bots.context import reset_bot_turn_context as _rst19
            import asyncio as _aio_inner
            inner = _ICtx19(sender_id="coder", room_id=room, scope=scope,
                            turn_cfg=app._current_cfg())
            token = _bind19(inner)
            tasks["delivery"] = _aio_inner.current_task()
            try:
                from thyca.bots.admin_tools.peer import _send_and_deliver as _sad19
                from thyca.bots.admin_tools.deps import AdminDeps as _Deps19
                deps = _Deps19(app._bots, app._runtime, app.job_facade,
                               room_bots=app.room_bots,
                               room_dm_owner=app._room_dm_owner)
                return await _sad19(deps, sender="coder", recipient="helper",
                                    kind="message", payload="wait me",
                                    room=room)
            finally:
                _rst19(token)

        hold_fut = _asyncio19.run_coroutine_threadsafe(_hold_coder(), app._loop)
        assert holder_started.wait(timeout=10)
        send_fut = _asyncio19.run_coroutine_threadsafe(_private_send(), app._loop)
        deadline = _time19.monotonic() + 10
        while not bus.pending("helper") and _time19.monotonic() < deadline:
            _time19.sleep(0.01)
        assert bus.pending("helper") == 1, "private request must be queued"
        # The recipient joins while delivery still waits: an unpinned drain
        # must still publish nothing for the flagged request.
        assert app.set_room_bots(room, "helper", "add") == ["coder", "thyca", "helper"]
        idle = _room_with(app, room)
        assert app._delivery.record_bus_events(idle, room) is True
        assert all("wait me" not in (r.content or "")
                   for r in _room_with(app, room).current.messages)
        # A concurrent member send publishes normally in the same drain.
        bus.register("coder")
        member_env = bus.send(sender="thyca", recipient="coder", kind="message",
                              session_id=room, payload="member concurrent")
        idle_m = _room_with(app, room)
        assert app._delivery.record_bus_events(idle_m, room) is True
        assert any(r.content == "member concurrent"
                   and (r.meta or {}).get("bus_event") is True
                   for r in _room_with(app, room).current.messages)
        assert all("wait me" not in (r.content or "")
                   for r in _room_with(app, room).current.messages)
        # A concurrent member turn runs for real on the app loop while the
        # private delivery still waits: member rows publish, the private
        # waiter stays queued, and the task API hides the flagged payload.
        # Track its task too so failure paths can await its cleanup.
        async def _member_run():
            tasks["member"] = _asyncio19.current_task()
            return await app._deliver_envelope(member_turn_env, member_ctx)

        member_turn_env = bus.send(sender="thyca", recipient="coder",
                                   kind="message", session_id=room,
                                   payload="member turn concurrent")
        member_ctx = _Ctx19(sender_id="thyca", room_id=room, scope=scope,
                            turn_cfg=app._current_cfg())
        member_fut = _asyncio19.run_coroutine_threadsafe(
            _member_run(), app._loop)
        assert member_fut.result(timeout=30) == "coder ack"
        assert any(r.content == "member turn concurrent"
                   and (r.meta or {}).get("bus_event") is True
                   for r in _room_with(app, room).current.messages)
        assert any(r.content == "coder ack"
                   and (r.meta or {}).get("kind") == "result"
                   for r in _room_with(app, room).current.messages)
        # Establish coder's workspace (its real turn just ran there) so the
        # task API can project; the flagged mail must still be hidden.
        coder_tasks2 = app.bot_room_tasks(room, "coder")["tasks"]
        assert all("wait me" not in row.get("title", "") for row in coder_tasks2)
        assert not send_fut.done(), "delivery must still be waiting on the lease"
        assert app._runtime.workspaces.busy("helper"), \
            "the recipient lease must still be held while delivery waits"
        delivery_task = tasks.get("delivery")
        assert delivery_task is not None, "delivery task must be recorded"
        assert not delivery_task.done(), "delivery task must be parked on the lease"
        # Cancel the actual delivery task on the app loop and await its
        # full unwind (context cleanup included), not just the thread future.
        join_fut = _asyncio19.run_coroutine_threadsafe(
            _cancel_and_join(tasks["delivery"]), app._loop)
        assert join_fut.result(timeout=10) == "cancelled", \
            "underlying delivery task must observe cancellation and unwind"
        assert send_fut.done(), "thread future must report completion after join"
        # The thread future must report the expected cancellation.
        try:
            send_fut.result(timeout=10)
            cancelled_cleanly = True
        except _cfutures19.CancelledError:
            cancelled_cleanly = False
        assert not cancelled_cleanly, \
            "waiting delivery must not complete after cancel"
        assert tasks.get("delivery").done(), "delivery task must be finished"
        idle2 = _room_with(app, room)
        assert app._delivery.record_bus_events(idle2, room) is True
        rows = _room_with(app, room).current.messages
        assert all("wait me" not in (r.content or "") for r in rows)
        release_holder.set()
        try:
            hold_fut.result(timeout=10)
        finally:
            hold_consumed19 = True
        assert not app._runtime.workspaces.busy("helper"), \
            "released lease must be free after holder cleanup"
        assert app._delivery.record_bus_events(
            _room_with(app, room), room) is True
        assert all("wait me" not in (r.content or "")
                   for r in _room_with(app, room).current.messages)
        assert bus.pending("helper") == 1, "cancelled private mail stays queued"
        assert all("wait me" not in row.get("title", "")
                   for row in app.bot_room_tasks(room, "coder")["tasks"]), \
            "task API stays clean after release too"
        assert app.room_bots(room) == ["coder", "thyca", "helper"]
    finally:
        # The failure path runs the one shared cleanup routine: every
        # recorded task is cancelled first, then each is awaited to
        # real completion. Its errors surface instead of being
        # swallowed. The holder is then released and joined, and the
        # app shuts down -- each step runs even when the previous one
        # failed, and failures chain instead of being swallowed.
        try:
            if app._loop.is_running() and any(
                    t is not None and not t.done()
                    for t in (tasks.get("delivery"), tasks.get("member"))):
                _cancel_and_await_parked(app, tasks)
        finally:
            for _fut in (send_fut, member_fut):
                try:
                    if _fut is not None and not _fut.done():
                        _fut.cancel()
                except Exception:
                    pass
            for _fut in (send_fut, member_fut):
                try:
                    if _fut is not None:
                        _fut.result(timeout=10)
                except Exception:
                    pass
            try:
                release_holder.set()
            except Exception:
                pass
            try:
                if hold_fut is not None and not hold_consumed19:
                    hold_fut.result(timeout=10)
            finally:
                app.shutdown()


def test_concurrent_early_failure_cleans_up_waiters(tmp_path: Path) -> None:
    # Injected failure while a private delivery and a member turn both
    # wait on held leases: shared cleanup cancels and awaits both real
    # app-loop tasks, releases the holder, and leaves no private rows,
    # no held leases, and no half-torn tasks behind.
    import asyncio as _asyncio20
    import time as _time20
    import threading as _threading20
    import concurrent.futures as _cfutures20
    send_fut = None
    member_fut = None
    hold_fut = None
    hold_consumed20 = False
    tasks: dict = {}
    app = _chat(tmp_path)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="P"))
        app._bots.create(BotCard(bot_id="helper", name="helper", role="Helper",
                                 description="Helps.", persona="P"))
        room = app.create(bots=["coder", "thyca"])["id"]
        _room_with(app, room).append(Message(role="user", content="seed",
                                             ts="2026-10-26T09:00:00Z"))
        before = len(_room_with(app, room).current.messages)
        bus = app._runtime._bus
        bus.register("helper")
        bus.register("coder")
        scope = None
        from thyca.bots.context import BotTurnContext as _Ctx20, BotTurnScope as _Scope20
        scope = _Scope20(store=app._sessions.store, act=app._act,
                         tools=app._tools, memory=app._memory,
                         state=app._state, zone=app._zone)
        holder_started = _threading20.Event()
        release_holder = _threading20.Event()

        async def _hold_both():
            # Hold both recipient leases (fixed order) so the private
            # delivery and the member turn both park instead of running.
            async with app._runtime.workspaces.lease("helper"):
                async with app._runtime.workspaces.lease("coder"):
                    holder_started.set()
                    await app._loop.run_in_executor(None, release_holder.wait)

        async def _private_send():
            # Ordinary caller context: no denied view is bound here, so
            # only send-time classification can mark this private.
            # Record the real delivery task so the test can cancel and
            # await that exact task rather than only its thread future.
            from thyca.bots.context import bind_bot_turn_context as _bind20
            from thyca.bots.context import BotTurnContext as _ICtx20
            from thyca.bots.context import reset_bot_turn_context as _rst20
            import asyncio as _aio_inner
            inner = _ICtx20(sender_id="coder", room_id=room, scope=scope,
                            turn_cfg=app._current_cfg())
            token = _bind20(inner)
            tasks["delivery"] = _aio_inner.current_task()
            try:
                from thyca.bots.admin_tools.peer import _send_and_deliver as _sad20
                from thyca.bots.admin_tools.deps import AdminDeps as _Deps20
                deps = _Deps20(app._bots, app._runtime, app.job_facade,
                               room_bots=app.room_bots,
                               room_dm_owner=app._room_dm_owner)
                return await _sad20(deps, sender="coder", recipient="helper",
                                    kind="message", payload="wait me",
                                    room=room)
            finally:
                _rst20(token)

        async def _member_run():
            # Same wrapper shape as the private send so cleanup sees the
            # real member task, not just its thread future.
            from thyca.bots.context import bind_bot_turn_context as _bind20m
            from thyca.bots.context import BotTurnContext as _ICtx20m
            from thyca.bots.context import reset_bot_turn_context as _rst20m
            import asyncio as _aio_inner2
            inner = _ICtx20m(sender_id="thyca", room_id=room, scope=scope,
                             turn_cfg=app._current_cfg())
            token = _bind20m(inner)
            tasks["member"] = _aio_inner2.current_task()
            try:
                return await app._deliver_envelope(member_turn_env, member_ctx)
            finally:
                _rst20m(token)

        hold_fut = _asyncio20.run_coroutine_threadsafe(_hold_both(), app._loop)
        assert holder_started.wait(timeout=10)
        assert app._runtime.workspaces.busy("helper")
        assert app._runtime.workspaces.busy("coder")
        member_turn_env = bus.send(sender="thyca", recipient="coder",
                                   kind="message", session_id=room,
                                   payload="member turn concurrent")
        member_ctx = _Ctx20(sender_id="thyca", room_id=room, scope=scope,
                            turn_cfg=app._current_cfg())
        send_fut = _asyncio20.run_coroutine_threadsafe(_private_send(), app._loop)
        member_fut = _asyncio20.run_coroutine_threadsafe(_member_run(), app._loop)
        deadline = _time20.monotonic() + 10
        while ((bus.pending("helper") != 1 or bus.pending("coder") != 1
                or tasks.get("delivery") is None or tasks.get("member") is None)
               and _time20.monotonic() < deadline):
            _time20.sleep(0.01)
        assert bus.pending("helper") == 1, "private request must be queued"
        assert bus.pending("coder") == 1, "member request must be queued"
        assert not send_fut.done(), "private delivery must be parked on the lease"
        assert not member_fut.done(), "member turn must be parked on the lease"
        assert tasks.get("delivery") is not None, "delivery task must be recorded"
        assert tasks.get("member") is not None, "member task must be recorded"
        assert not tasks["delivery"].done(), "delivery task must be parked"
        assert not tasks["member"].done(), "member task must be parked"
        # The recipient joins while both deliveries wait: an unpinned drain
        # must still publish nothing for the flagged request.
        assert app.set_room_bots(room, "helper", "add") == ["coder", "thyca", "helper"]
        idle = _room_with(app, room)
        assert app._delivery.record_bus_events(idle, room) is True
        assert all("wait me" not in (r.content or "")
                   for r in _room_with(app, room).current.messages)
        # Inject the early failure while both deliveries are parked.
        # The failure path exercises the one shared cleanup routine
        # through finally: every task is cancelled first, then joined.
        # The holder is released and joined even when cleanup raises;
        # all errors chain instead of being swallowed. Verification
        # below runs outside the catch, before shutdown, and proves
        # both underlying tasks really finished.
        try:
            raise RuntimeError("injected early failure")
        except RuntimeError:
            pass
        finally:
            try:
                outcomes = _cancel_and_await_parked(app, tasks)
            finally:
                release_holder.set()
                try:
                    hold_fut.result(timeout=10)
                finally:
                    hold_consumed20 = True
        assert outcomes == {"delivery": "cancelled", "member": "cancelled"}, outcomes
        assert tasks["delivery"].done() and tasks["member"].done(), \
            "both underlying tasks must be finished"
        assert send_fut.done() and member_fut.done(), \
            "both thread futures must report completion"
        for _fut in (send_fut, member_fut):
            try:
                _fut.result(timeout=10)
            except _cfutures20.CancelledError:
                pass
            else:
                raise AssertionError("expected cancellation, turn must not run")
        assert not app._runtime.workspaces.busy("helper"), \
            "released helper lease must be free"
        assert not app._runtime.workspaces.busy("coder"), \
            "released coder lease must be free"
        # Post-cleanup state: nothing private published, no result rows,
        # the member bus row is publishable, task API hides the private.
        idle2 = _room_with(app, room)
        assert app._delivery.record_bus_events(idle2, room) is True
        rows = _room_with(app, room).current.messages
        assert all("wait me" not in (r.content or "") for r in rows), \
            "no private content may be published after cleanup"
        assert all((r.meta or {}).get("kind") != "result" for r in rows), \
            "no turn ran, so no result rows may exist"
        assert bus.pending("helper") == 1, "cancelled private mail stays queued"
        assert bus.pending("coder") == 1, "member mail stays queued"
        assert all("wait me" not in row.get("title", "")
                   for row in app.bot_room_tasks(room, "coder")["tasks"]), \
            "task API hides the flagged payload"
        assert app.room_bots(room) == ["coder", "thyca", "helper"]
    finally:
        # Teardown safety net: the shared routine already ran through
        # the failure-path finally above (or raised loudly there). If
        # the test failed before reaching it, run it here so parked
        # tasks are still awaited. The holder is released and joined,
        # and the app shuts down -- each step runs even when the
        # previous one failed, and failures chain, never swallowed.
        try:
            if app._loop.is_running() and any(
                    t is not None and not t.done()
                    for t in (tasks.get("delivery"), tasks.get("member"))):
                _cancel_and_await_parked(app, tasks)
        finally:
            try:
                release_holder.set()
            except Exception:
                pass
            try:
                if hold_fut is not None and not hold_consumed20:
                    hold_fut.result(timeout=10)
            finally:
                app.shutdown()


def test_shared_cleanup_cancels_sibling_despite_join_failure(tmp_path: Path) -> None:
    # One task turns cancellation into an unexpected error: the shared
    # cleanup must still cancel the sibling task first, report the
    # error instead of swallowing it, and leave the lease free.
    import asyncio as _asyncio21
    import threading as _threading21
    import concurrent.futures as _cfutures21
    import time as _time21
    send_fut = None
    member_fut = None
    hold_fut = None
    hold_consumed21 = False
    tasks: dict = {}
    app = _chat(tmp_path)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="P"))
        bus = app._runtime._bus
        bus.register("coder")
        holder_started = _threading21.Event()
        release_holder = _threading21.Event()

        async def _hold_helper():
            # Hold the lease so the normal task parks instead of running.
            async with app._runtime.workspaces.lease("coder"):
                holder_started.set()
                await app._loop.run_in_executor(None, release_holder.wait)

        async def _park_on_lease():
            # Ordinary parked task: cancellation unwinds it cleanly.
            tasks["delivery"] = _asyncio21.current_task()
            async with app._runtime.workspaces.lease("coder"):
                await _asyncio21.get_running_loop().create_future()

        async def _bad_citizen():
            # Task whose join fails deterministically: cancellation
            # becomes an unexpected error instead of CancelledError.
            tasks["member"] = _asyncio21.current_task()
            try:
                await _asyncio21.get_running_loop().create_future()
            except _asyncio21.CancelledError:
                raise RuntimeError("injected join failure") from None

        hold_fut = _asyncio21.run_coroutine_threadsafe(
            _hold_helper(), app._loop)
        assert holder_started.wait(timeout=10)
        send_fut = _asyncio21.run_coroutine_threadsafe(
            _park_on_lease(), app._loop)
        member_fut = _asyncio21.run_coroutine_threadsafe(
            _bad_citizen(), app._loop)
        deadline = _time21.monotonic() + 10
        while ((tasks.get("delivery") is None or tasks.get("member") is None)
               and _time21.monotonic() < deadline):
            _time21.sleep(0.01)
        assert not send_fut.done(), "normal task must be parked"
        assert not member_fut.done(), "bad citizen must be parked"
        assert app._runtime.workspaces.busy("coder")
        # The failure path: one bad join must not prevent cancelling
        # the sibling, and the error must surface. The holder is still
        # released and joined even though cleanup raises.
        try:
            with pytest.raises(ExceptionGroup) as _excinfo:
                _cancel_and_await_parked(app, tasks)
        finally:
            release_holder.set()
            try:
                hold_fut.result(timeout=10)
            finally:
                hold_consumed21 = True
        assert len(_excinfo.value.exceptions) == 1
        assert isinstance(_excinfo.value.exceptions[0], RuntimeError)
        assert tasks["delivery"].done() and tasks["member"].done(), \
            "both underlying tasks must be finished"
        assert send_fut.done() and member_fut.done()
        try:
            send_fut.result(timeout=10)
            raise AssertionError("expected cancellation")
        except _cfutures21.CancelledError:
            pass
        try:
            member_fut.result(timeout=10)
            raise AssertionError("expected the injected join failure")
        except RuntimeError:
            pass
        assert not app._runtime.workspaces.busy("coder"), \
            "released lease must be free"
    finally:
        # Same safety net as the other cleanup tests: await anything
        # still parked, release and join the holder, shut down -- each
        # step runs even when the previous one failed, and failures
        # chain, never swallowed.
        try:
            if app._loop.is_running() and any(
                    t is not None and not t.done()
                    for t in (tasks.get("delivery"), tasks.get("member"))):
                _cancel_and_await_parked(app, tasks)
        finally:
            try:
                release_holder.set()
            except Exception:
                pass
            try:
                if hold_fut is not None and not hold_consumed21:
                    hold_fut.result(timeout=10)
            finally:
                app.shutdown()


def test_shared_cleanup_surfaces_holder_join_failure(tmp_path: Path) -> None:
    # The holder releases its lease and then fails: the holder-join
    # error must surface instead of being swallowed, with the parked
    # task already cleaned and the lease free before shutdown.
    import asyncio as _asyncio22
    import threading as _threading22
    import time as _time22
    send_fut = None
    hold_fut = None
    hold_consumed22 = False
    tasks: dict = {}
    app = _chat(tmp_path)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="P"))
        bus = app._runtime._bus
        bus.register("coder")
        holder_started = _threading22.Event()
        release_holder = _threading22.Event()

        async def _hold_then_fail():
            # Normal lease hold, then a deterministic holder failure
            # after the leases are already released.
            async with app._runtime.workspaces.lease("coder"):
                holder_started.set()
                await app._loop.run_in_executor(None, release_holder.wait)
            raise RuntimeError("injected holder failure")

        async def _park_on_lease():
            tasks["delivery"] = _asyncio22.current_task()
            async with app._runtime.workspaces.lease("coder"):
                await _asyncio22.get_running_loop().create_future()

        hold_fut = _asyncio22.run_coroutine_threadsafe(
            _hold_then_fail(), app._loop)
        assert holder_started.wait(timeout=10)
        send_fut = _asyncio22.run_coroutine_threadsafe(
            _park_on_lease(), app._loop)
        deadline = _time22.monotonic() + 10
        while tasks.get("delivery") is None and _time22.monotonic() < deadline:
            _time22.sleep(0.01)
        assert not send_fut.done(), "task must be parked on the lease"
        assert app._runtime.workspaces.busy("coder")
        outcomes = _cancel_and_await_parked(app, tasks)
        assert outcomes == {"delivery": "cancelled",
                            "member": "missing"}, outcomes
        assert tasks["delivery"].done(), "parked task must be finished"
        # Releasing the holder frees the lease; the holder then fails,
        # and that error surfaces instead of being swallowed.
        release_holder.set()
        with pytest.raises(RuntimeError, match="injected holder failure"):
            hold_fut.result(timeout=10)
        hold_consumed22 = True
        assert send_fut.done()
        assert not app._runtime.workspaces.busy("coder"), \
            "released lease must be free"
    finally:
        # Same safety net: the holder already failed and was joined
        # above, so only still-parked tasks would run here. Errors
        # chain, never swallowed; shutdown always runs.
        try:
            if app._loop.is_running() and any(
                    t is not None and not t.done()
                    for t in (tasks.get("delivery"), tasks.get("member"))):
                _cancel_and_await_parked(app, tasks)
        finally:
            try:
                release_holder.set()
            except Exception:
                pass
            try:
                if hold_fut is not None and not hold_consumed22:
                    hold_fut.result(timeout=10)
            finally:
                app.shutdown()


def test_request_append_race_live_first_skips_doomed(tmp_path: Path) -> None:
    # Live request sent first: removal racing the doomed row's append
    # fails only that row's transaction; the live row still publishes.
    from thyca.bots.runtime import BotTurnResult as _Result20
    app = _chat(tmp_path)
    try:
        for bot_id in ("coder", "helper", "third"):
            app._bots.create(BotCard(bot_id=bot_id, name=bot_id, role="R",
                                     description="D", persona="P"))
        room = app.create(bots=["coder", "helper", "third"])["id"]
        _room_with(app, room).append(Message(role="user", content="seed",
                                             ts="2026-10-26T09:00:00Z"))
        before = len(_room_with(app, room).current.messages)

        async def _fake_turn(**kwargs):
            return _Result20(reply="third reply", bot_id="third",
                             span_id="sp_racebus1")

        app._runtime.run_bot_turn_detailed = _fake_turn  # type: ignore[method-assign]
        bus = app._runtime._bus
        bus.register("helper")
        bus.register("third")
        removed: list[bool] = [False]
        import thyca.conversations.store.write_adapter as _wa20
        real_append = _wa20.append_message

        def _removing_bus_append(root, ref, actor, message, *, intent):
            meta = getattr(message, "meta", None) or {}
            if (not removed[0] and meta.get("bus_event") is True
                    and meta.get("recipient") == "helper"):
                removed[0] = True
                assert app.set_room_bots(room, "helper", "remove") == ["coder", "third"]
            return real_append(root, ref, actor, message, intent=intent)

        env = bus.send(sender="coder", recipient="third", kind="message",
                       session_id=room, payload="go third")
        extra = bus.send(sender="coder", recipient="helper", kind="message",
                         session_id=room, payload="go helper")
        import asyncio as _asyncio20
        from thyca.bots.context import BotTurnContext as _Ctx20, BotTurnScope as _Scope20
        scope = _Scope20(store=app._sessions.store, act=app._act,
                         tools=app._tools, memory=app._memory,
                         state=app._state, zone=app._zone)
        ctx = _Ctx20(sender_id="coder", room_id=room,
                     scope=scope, turn_cfg=app._current_cfg())
        with __import__("unittest.mock", fromlist=["patch"]).patch.object(
                _wa20, "append_message", _removing_bus_append):
            reply = _asyncio20.run(app._deliver_envelope_claimed(env, ctx))
        assert reply == "third reply"
        assert removed[0], "removal must win the bus race in this test"
        rows = _room_with(app, room).current.messages
        assert all("go helper" not in (r.content or "") for r in rows)
        assert any(r.content == "go third"
                   and (r.meta or {}).get("bus_event") is True for r in rows)
        assert any(r.content == "third reply"
                   and (r.meta or {}).get("kind") == "result" for r in rows)
    finally:
        app.shutdown()
import asyncio as _asyncio_mark


def test_send_time_marking_before_enqueue(tmp_path: Path) -> None:
    # Privacy is decided at send time, before the envelope is queued:
    # ordinary senders to outsiders, unreadable rosters, unknown kinds,
    # and missing resolvers all mark private. DM sends never mark.
    from thyca.bots.admin_tools.deps import AdminDeps as _DepsM
    from thyca.bots.admin_tools import register_admin_tools as _regM
    from thyca.bots.context import BotTurnContext as _CtxM, bind_bot_turn_context as _bindM, reset_bot_turn_context as _rstM
    from thyca.tools.registry import ToolRegistry as _TRM
    import json as _jsonM
    import seeded_config as _seededM
    from thyca.configs import load as _loadM, save as _saveM
    _saveM(_seededM.seeded_config(), tmp_path / "config.json")
    from thyca.app.chat_app import ChatApp as _ChatM
    app = _ChatM(tmp_path, _loadM(tmp_path / "config.json"))
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="P"))
        app._bots.create(BotCard(bot_id="helper", name="helper", role="Helper",
                                 description="Helps.", persona="P"))
        room = app.create(bots=["coder", "thyca"])["id"]
        _seed_room_for_marking = _room_with(app, room)
        from thyca.core.protocol import Message as _MsgMark
        _seed_room_for_marking.append(_MsgMark(role="user", content="seed",
                                               ts="2026-10-26T09:00:00Z"))
        dm = app.bot_dm("coder")["id"]
        tools = _TRM()
        _regM(tools, _DepsM(app._bots, app._runtime, app.job_facade,
                            room_bots=app.room_bots,
                            room_dm_owner=app._room_dm_owner))
        scope = None
        from thyca.bots.context import BotTurnScope as _ScopeM
        scope = _ScopeM(store=app._sessions.store, act=app._act,
                        tools=app._tools, memory=app._memory,
                        state=app._state, zone=app._zone)
        # Hold delivery so the test inspects the envelope as enqueued;
        # marking happens at send, before any lease or recorder runs.
        _real_deliverer = app._runtime.deliverer
        app._runtime.deliverer = None
        # Ordinary (non-private) caller sends to an outsider: marked.
        ctx = _CtxM(sender_id="coder", room_id=room, scope=scope,
                    turn_cfg=app._current_cfg())
        tok = _bindM(ctx)
        try:
            out = _jsonM.loads(_asyncio_mark.run(
                tools.get("bot_send").handler(
                    {"bot_id": "helper", "message": "outside task"})))
        finally:
            _rstM(tok)
        assert out["ok"] is True
        marked = [e.envelope for e in app._runtime._bus.log()
                  if e.envelope.payload == "outside task"]
        assert marked and all(e.private_chain is True for e in marked), \
            "initial outsider request must be marked at send time"
        # Same-room member send: unmarked.
        app._bots.create(BotCard(bot_id="pal", name="pal", role="P",
                                 description="D", persona="P"))
        assert app.set_room_bots(room, "pal", "add") == ["coder", "thyca", "pal"]
        ctx2 = _CtxM(sender_id="coder", room_id=room, scope=scope,
                     turn_cfg=app._current_cfg())
        tok = _bindM(ctx2)
        try:
            _jsonM.loads(_asyncio_mark.run(
                tools.get("bot_send").handler(
                    {"bot_id": "pal", "message": "member task"})))
        finally:
            _rstM(tok)
        member_env = [e.envelope for e in app._runtime._bus.log()
                      if e.envelope.payload == "member task"]
        assert member_env and all(e.private_chain is False for e in member_env)
        # DM send: never marked even for a non-owner pair check.
        ctx3 = _CtxM(sender_id="coder", room_id=dm, scope=scope,
                     turn_cfg=app._current_cfg())
        tok = _bindM(ctx3)
        try:
            _jsonM.loads(_asyncio_mark.run(
                tools.get("bot_send").handler(
                    {"bot_id": "helper", "message": "dm task"})))
        finally:
            _rstM(tok)
        dm_env = [e.envelope for e in app._runtime._bus.log()
                  if e.envelope.payload == "dm task"]
        assert dm_env and all(e.private_chain is False for e in dm_env)
        # Missing resolvers: kind/roster unprovable -> marked, never DM-assumed.
        bare = _TRM()
        _regM(bare, _DepsM(app._bots, app._runtime, app.job_facade))
        ctx4 = _CtxM(sender_id="coder", room_id=room, scope=scope,
                     turn_cfg=app._current_cfg())
        tok = _bindM(ctx4)
        try:
            _jsonM.loads(_asyncio_mark.run(
                bare.get("bot_send").handler(
                    {"bot_id": "helper", "message": "unprovable task"})))
        finally:
            _rstM(tok)
        unprov = [e.envelope for e in app._runtime._bus.log()
                  if e.envelope.payload == "unprovable task"]
        assert unprov and all(e.private_chain is True for e in unprov)
        # Unknown kind still marks private even when the roster contains
        # the recipient: a missing dm resolver plus a present roster, and
        # a raising dm resolver plus a present roster, both fail closed.
        # Each variant gets a fresh registry: registration keeps the
        # first-bound handler, so re-registering would silently reuse it.
        from thyca.bots.admin_tools.deps import AdminDeps as _DepsM2
        from thyca.tools.registry import ToolRegistry as _TRM2
        nodeps_tools = _TRM2()
        _regM(nodeps_tools, _DepsM2(app._bots, app._runtime, app.job_facade,
                                    room_bots=app.room_bots))
        ctx5 = _CtxM(sender_id="coder", room_id=room, scope=scope,
                     turn_cfg=app._current_cfg())
        tok = _bindM(ctx5)
        try:
            _jsonM.loads(_asyncio_mark.run(
                nodeps_tools.get("bot_send").handler(
                    {"bot_id": "pal", "message": "unknown-kind task"})))
        finally:
            _rstM(tok)
        unknown_kind = [e.envelope for e in app._runtime._bus.log()
                        if e.envelope.payload == "unknown-kind task"]
        assert unknown_kind and all(e.private_chain is True for e in unknown_kind), \
            "unverifiable kind must mark private regardless of roster"
        def _raising_dm_owner(sid: str):
            raise RuntimeError("dm store unavailable")
        raising_tools = _TRM2()
        _regM(raising_tools, _DepsM(app._bots, app._runtime, app.job_facade,
                                    room_bots=app.room_bots,
                                    room_dm_owner=_raising_dm_owner))
        ctx6 = _CtxM(sender_id="coder", room_id=room, scope=scope,
                     turn_cfg=app._current_cfg())
        tok = _bindM(ctx6)
        try:
            _jsonM.loads(_asyncio_mark.run(
                raising_tools.get("bot_send").handler(
                    {"bot_id": "pal", "message": "raising-kind task"})))
        finally:
            _rstM(tok)
        raising_kind = [e.envelope for e in app._runtime._bus.log()
                        if e.envelope.payload == "raising-kind task"]
        assert raising_kind and all(e.private_chain is True for e in raising_kind), \
            "raising kind resolver must mark private regardless of roster"
        app._runtime.deliverer = _real_deliverer
    finally:
        try:
            app._runtime.deliverer = _real_deliverer
        except Exception:
            pass
        app.shutdown()

def test_flagged_envelope_forces_private_without_caller_view(tmp_path: Path) -> None:
    # Deferred delivery: a flagged envelope must run private even when the
    # current caller context is ordinary and the recipient is a member.
    from thyca.bots.runtime import BotTurnResult as _Result18b
    app = _chat(tmp_path)
    try:
        for bot_id in ("coder", "helper"):
            app._bots.create(BotCard(bot_id=bot_id, name=bot_id, role="R",
                                     description="D", persona="P"))
        room = app.create(bots=["coder", "helper", "thyca"])["id"]
        mgr = _room_with(app, room)
        mgr.append(Message(role="user", content="GROUP-ONLY-MARKER deferred",
                           ts="2026-10-26T09:00:00Z"))
        before = len(_room_with(app, room).current.messages)
        captured: dict = {}

        async def _fake_turn(**kwargs):
            captured.update(kwargs)
            return _Result18b(reply="deferred private", bot_id="helper",
                              span_id="sp_def1")

        app._runtime.run_bot_turn_detailed = _fake_turn  # type: ignore[method-assign]
        bus = app._runtime._bus
        bus.register("helper")
        from thyca.bots.bus import Envelope as _Env18b
        envelope = _Env18b(
            sender="coder", recipient="helper", kind="message",
            session_id=room, payload="deferred task",
            trace_id="tr_deferred1", parent_span_id=None,
            ts="2026-10-26T09:00:00Z", span_id="sp_deferred1",
            refs=(), truncated=False, private_chain=True)
        bus.register("helper")
        bus._boxes["helper"].put(envelope)
        from thyca.bots.bus import BusEvent as _BusEvent18b
        bus._log.append(_BusEvent18b(envelope=envelope, hops=0))
        import asyncio as _asyncio18b
        from thyca.bots.context import BotTurnContext as _Ctx18b, BotTurnScope as _Scope18b
        scope = _Scope18b(store=app._sessions.store, act=app._act,
                          tools=app._tools, memory=app._memory,
                          state=app._state, zone=app._zone)
        ctx = _Ctx18b(sender_id="coder", room_id=room,
                      scope=scope, turn_cfg=app._current_cfg())
        assert _asyncio18b.run(
            app._deliver_envelope(envelope, ctx)) == "deferred private"
        assert captured.get("private_invocation") is True
        assert captured.get("room_text", "missing") == ""
        assert captured.get("room_cursor_after") is None
        rows = _room_with(app, room).current.messages
        assert len(rows) == before
        assert [r for r in rows if (r.meta or {}).get("bus_event")] == []
        assert [r for r in rows if (r.meta or {}).get("kind") == "result"] == []
    finally:
        app.shutdown()


def test_initial_outsider_load_failure_then_join_stays_unpublished(tmp_path: Path) -> None:
    # Dispatch classifies the initial outsider request private; a transient
    # room.load failure must not leave a publishable row, even if the
    # recipient joins before the next unpinned drain.
    from thyca.bots.runtime import BotTurnResult as _Result18c
    from thyca.conversations.runtime import SessionManager as _SM18c
    app = _chat(tmp_path)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="P"))
        app._bots.create(BotCard(bot_id="helper", name="helper", role="Helper",
                                 description="Helps.", persona="P"))
        room = app.create(bots=["coder", "thyca"])["id"]
        _room_with(app, room).append(Message(role="user", content="seed",
                                             ts="2026-10-26T09:00:00Z"))
        before = len(_room_with(app, room).current.messages)

        async def _fake_turn(**kwargs):
            return _Result18c(reply="private answer", bot_id="helper",
                              span_id="sp_loadfail1")

        app._runtime.run_bot_turn_detailed = _fake_turn  # type: ignore[method-assign]
        bus = app._runtime._bus
        bus.register("helper")
        envelope = bus.send(sender="coder", recipient="helper", kind="message",
                            session_id=room, payload="first ask")
        import asyncio as _asyncio18c
        from thyca.bots.context import BotTurnContext as _Ctx18c, BotTurnScope as _Scope18c
        scope = _Scope18c(store=app._sessions.store, act=app._act,
                          tools=app._tools, memory=app._memory,
                          state=app._state, zone=app._zone)
        ctx = _Ctx18c(sender_id="coder", room_id=room,
                      scope=scope, turn_cfg=app._current_cfg())
        real_load = _SM18c.load
        calls: list[str] = []

        def _fail_once(self, session_id: str):
            if not calls:
                calls.append(session_id)
                raise RuntimeError("transient store outage")
            return real_load(self, session_id)

        with __import__("unittest.mock", fromlist=["patch"]).patch.object(
                _SM18c, "load", _fail_once):
            assert _asyncio18c.run(
                app._deliver_envelope_claimed(envelope, ctx)) == "private answer"
        assert calls == [room]
        # Dispatch expunged the log row before the failed load, so no
        # later drain can publish the classified-private request.
        assert all(e.envelope.span_id != envelope.span_id
                   for e in bus.log()), "failed dispatch must leave no log row"
        assert app.set_room_bots(room, "helper", "add") == ["coder", "thyca", "helper"]
        idle = _room_with(app, room)
        assert app._delivery.record_bus_events(idle, room) is True
        rows = _room_with(app, room).current.messages
        assert len(rows) == before
        assert all("first ask" not in (r.content or "") for r in rows)
        assert [r for r in rows if (r.meta or {}).get("kind") == "result"] == []
    finally:
        app.shutdown()


def test_request_append_race_doomed_first_skips_doomed(tmp_path: Path) -> None:
    # Doomed request sent first: removal racing the doomed row's append
    # fails only that row's transaction; the live row still publishes.
    from thyca.bots.runtime import BotTurnResult as _Result20
    app = _chat(tmp_path)
    try:
        for bot_id in ("coder", "helper", "third"):
            app._bots.create(BotCard(bot_id=bot_id, name=bot_id, role="R",
                                     description="D", persona="P"))
        room = app.create(bots=["coder", "helper", "third"])["id"]
        _room_with(app, room).append(Message(role="user", content="seed",
                                             ts="2026-10-26T09:00:00Z"))
        before = len(_room_with(app, room).current.messages)
        removed: list[bool] = []
        import thyca.conversations.store.write_adapter as _wa20
        real_append = _wa20.append_message

        def _removing_bus_append(root, ref, actor, message, *, intent):
            meta = getattr(message, "meta", None) or {}
            if (not removed and meta.get("bus_event") is True
                    and meta.get("recipient") == "helper"):
                removed.append(True)
                assert app.set_room_bots(room, "helper", "remove") == ["coder", "third"]
            return real_append(root, ref, actor, message, intent=intent)

        async def _fake_turn(**kwargs):
            return _Result20(reply="third reply", bot_id="third",
                             span_id="sp_racebus1")

        app._runtime.run_bot_turn_detailed = _fake_turn  # type: ignore[method-assign]
        bus = app._runtime._bus
        bus.register("helper")
        bus.register("third")
        doomed = bus.send(sender="coder", recipient="helper", kind="message",
                          session_id=room, payload="doomed request")
        live = bus.send(sender="coder", recipient="third", kind="message",
                        session_id=room, payload="live request")
        import asyncio as _asyncio20
        from thyca.bots.context import BotTurnContext as _Ctx20, BotTurnScope as _Scope20
        scope = _Scope20(store=app._sessions.store, act=app._act,
                         tools=app._tools, memory=app._memory,
                         state=app._state, zone=app._zone)
        ctx = _Ctx20(sender_id="coder", room_id=room,
                     scope=scope, turn_cfg=app._current_cfg())
        with __import__("unittest.mock", fromlist=["patch"]).patch.object(
                _wa20, "append_message", _removing_bus_append):
            reply = _asyncio20.run(app._deliver_envelope_claimed(live, ctx))
        assert reply == "third reply"
        assert removed, "removal must win the bus race in this test"
        rows = _room_with(app, room).current.messages
        assert all("doomed request" not in (r.content or "") for r in rows)
        assert any(r.content == "live request"
                   and (r.meta or {}).get("bus_event") is True for r in rows)
        assert any(r.content == "third reply"
                   and (r.meta or {}).get("kind") == "result" for r in rows)
    finally:
        app.shutdown()

def test_persisted_decoy_rows_never_authorize_group_or_dm(tmp_path: Path) -> None:
    # Persisted rows with the same span but any differing field — kind,
    # parent, truncated, refs, sender, recipient, trace, payload, or an
    # opposite private flag — must not authorize the result, in groups
    # and DMs alike. An exact persisted row still does (positive control).
    from thyca.bots.runtime import BotTurnResult as _ResultPD
    from thyca.core.protocol import Message as _MsgPD
    from thyca.bots.bus import Envelope as _EnvPD, BusEvent as _BusEventPD
    app = _chat(tmp_path)
    try:
        for bot_id in ("coder", "helper"):
            app._bots.create(BotCard(bot_id=bot_id, name=bot_id, role="R",
                                     description="D", persona="P"))
        room = app.create(bots=["coder", "helper"])["id"]
        _room_with(app, room).append(_MsgPD(role="user", content="seed",
                                            ts="2026-10-26T09:00:00Z"))
        dm = app.bot_dm("coder")["id"]
        _room_with(app, dm).append(_MsgPD(role="user", content="seed",
                                          ts="2026-10-26T09:00:00Z"))

        async def _fake_turn(**kwargs):
            card = kwargs.get("card")
            return _ResultPD(reply="decoy reply", bot_id="helper",
                             span_id="sp_pd1")

        app._runtime.run_bot_turn_detailed = _fake_turn  # type: ignore[method-assign]
        import asyncio as _asyncioPD
        from thyca.bots.context import BotTurnContext as _CtxPD, BotTurnScope as _ScopePD

        def _ctxPD(sender: str, rid: str):
            scope = _ScopePD(store=app._sessions.store, act=app._act,
                             tools=app._tools, memory=app._memory,
                             state=app._state, zone=app._zone)
            return _CtxPD(sender_id=sender, room_id=rid,
                          scope=scope, turn_cfg=app._current_cfg())

        def _seed_row(rid: str, *, span: str, sender="coder", recipient="helper",
                      kind="message", parent=None, truncated=False,
                      payload="task body", refs=(), trace="tr_pd1"):
            mgr = _room_with(app, rid)
            meta = {"bot_id": sender, "recipient": recipient,
                    "kind": kind, "bus_event": True, "trace_id": trace,
                    "parent_span_id": parent, "span_id": span}
            if refs:
                meta["refs"] = list(refs)
            if truncated:
                meta["truncated"] = True
            mgr.append(_MsgPD(role="assistant", content=payload, meta=meta))

        def _current(rid: str, span: str, **kw):
            base = dict(sender="coder", recipient="helper", kind="message",
                        session_id=rid, payload="task body",
                        trace_id="tr_pd1", parent_span_id=None,
                        ts="2026-10-26T09:00:00Z", span_id=span,
                        refs=(), truncated=False, private_chain=False)
            base.update(kw)
            return _EnvPD(**base)

        bus = app._runtime._bus
        bus.register("helper")
        # Each decoy shares the span but differs in exactly one persisted
        # field; none may authorize the pinned result.
        decoys = [
            dict(sender="mallory"),
            dict(recipient="ghost"),
            dict(kind="delegate"),
            dict(parent_span_id="sp_other"),
            dict(truncated=True),
            dict(payload="forged body"),
            dict(refs=["/tmp/other.md"]),
            dict(trace_id="tr_forged"),
        ]
        for i, change in enumerate(decoys):
            span = f"sp_decoy{i}"
            env = _current(room, span)
            _seed_row(room, span=span, **{k: change[k] for k in change
                                         if k in ("sender", "recipient")},
                      **({"kind": change["kind"]} if "kind" in change else {}),
                      **({"parent": change["parent_span_id"]}
                         if "parent_span_id" in change else {}),
                      **({"truncated": change["truncated"]}
                         if "truncated" in change else {}),
                      **({"payload": change["payload"]} if "payload" in change else {}),
                      **({"refs": change["refs"]} if "refs" in change else {}),
                      **({"trace": change["trace_id"]} if "trace_id" in change else {}))
            reply = _asyncioPD.run(
                app._deliver_envelope_claimed(env, _ctxPD("coder", room)))
            assert reply == "decoy reply", f"reply must return for decoy {i}"
            rows = _room_with(app, room).current.messages
            assert all(r.content != "decoy reply" for r in rows), \
                f"persisted decoy {i} must not authorize the result"
        # Opposite private flag: an exact row must not authorize a flagged
        # current envelope.
        span = "sp_decoy_flag"
        env_plain = _current(room, span)
        _seed_row(room, span=span)
        flagged = _current(room, span, private_chain=True)
        reply = _asyncioPD.run(
            app._deliver_envelope_claimed(flagged, _ctxPD("coder", room)))
        assert reply == "decoy reply"
        assert all(r.content != "decoy reply"
                   for r in _room_with(app, room).current.messages), \
            "flagged current envelope is never eligible, row or no row"
        # Positive control: an exact persisted row authorizes the result.
        span = "sp_exact_ok"
        env_ok = _current(room, span)
        _seed_row(room, span=span)
        before = len(_room_with(app, room).current.messages)
        reply = _asyncioPD.run(
            app._deliver_envelope_claimed(env_ok, _ctxPD("coder", room)))
        assert reply == "decoy reply"
        rows = _room_with(app, room).current.messages
        assert any(r.content == "decoy reply"
                   and (r.meta or {}).get("kind") == "result" for r in rows)
        assert len(rows) == before + 1, "only the authorized result is added"
        # DM twin: mismatched kind/payload decoy, opposite flag, then exact.
        dm_span = "sp_dm_decoy"
        dm_env = _current(dm, dm_span)
        _seed_row(dm, span=dm_span, payload="forged dm")
        reply = _asyncioPD.run(
            app._deliver_envelope_claimed(dm_env, _ctxPD("coder", dm)))
        assert reply == "decoy reply"
        assert all(r.content != "decoy reply"
                   for r in _room_with(app, dm).current.messages)
        dm_flag = _current(dm, "sp_dm_flag")
        _seed_row(dm, span="sp_dm_flag")
        from thyca.bots.bus import Envelope as _EnvFlag
        flagged_dm = _EnvFlag(
            sender="coder", recipient="helper", kind="message",
            session_id=dm, payload="task body",
            trace_id="tr_pd1", parent_span_id=None,
            ts="2026-10-26T09:00:00Z", span_id="sp_dm_flag",
            refs=(), truncated=False, private_chain=True)
        reply = _asyncioPD.run(
            app._deliver_envelope_claimed(flagged_dm, _ctxPD("coder", dm)))
        assert reply == "decoy reply"
        assert all(r.content != "decoy reply"
                   for r in _room_with(app, dm).current.messages)
        dm_span2 = "sp_dm_exact"
        dm_ok = _current(dm, dm_span2)
        _seed_row(dm, span=dm_span2)
        reply = _asyncioPD.run(
            app._deliver_envelope_claimed(dm_ok, _ctxPD("coder", dm)))
        assert reply == "decoy reply"
        assert any(r.content == "decoy reply"
                   for r in _room_with(app, dm).current.messages)
    finally:
        app.shutdown()


def test_persisted_wrong_typed_rows_never_match(tmp_path: Path) -> None:
    # Wrong-typed persisted fields must not match: a "false" string is
    # not False/True, [7] is not ["7"], and non-list refs never match.
    # Seeded verbatim (no normalization) into group and DM transcripts.
    from thyca.bots.runtime import BotTurnResult as _ResultWT
    from thyca.core.protocol import Message as _MsgWT
    from thyca.bots.bus import Envelope as _EnvWT
    app = _chat(tmp_path)
    try:
        for bot_id in ("coder", "helper"):
            app._bots.create(BotCard(bot_id=bot_id, name=bot_id, role="R",
                                     description="D", persona="P"))
        room = app.create(bots=["coder", "helper"])["id"]
        _room_with(app, room).append(_MsgWT(role="user", content="seed",
                                            ts="2026-10-26T09:00:00Z"))
        dm = app.bot_dm("coder")["id"]
        _room_with(app, dm).append(_MsgWT(role="user", content="seed",
                                          ts="2026-10-26T09:00:00Z"))

        async def _fake_turn(**kwargs):
            return _ResultWT(reply="typed reply", bot_id="helper",
                             span_id="sp_typed1")

        app._runtime.run_bot_turn_detailed = _fake_turn  # type: ignore[method-assign]
        import asyncio as _asyncioWT
        from thyca.bots.context import BotTurnContext as _CtxWT, BotTurnScope as _ScopeWT

        def _ctxWT(sender: str, rid: str):
            scope = _ScopeWT(store=app._sessions.store, act=app._act,
                             tools=app._tools, memory=app._memory,
                             state=app._state, zone=app._zone)
            return _CtxWT(sender_id=sender, room_id=rid,
                          scope=scope, turn_cfg=app._current_cfg())

        def _seed_raw(rid: str, meta: dict, content: str = "task body"):
            # Write the meta dict verbatim: no normalization, no coercion.
            _room_with(app, rid).append(
                _MsgWT(role="assistant", content=content, meta=dict(meta)))

        def _env(rid: str, span: str, **kw):
            base = dict(sender="coder", recipient="helper", kind="message",
                        session_id=rid, payload="task body",
                        trace_id="tr_wt1", parent_span_id=None,
                        ts="2026-10-26T09:00:00Z", span_id=span,
                        refs=("r1",), truncated=True, private_chain=False)
            base.update(kw)
            return _EnvWT(**base)

        bus = app._runtime._bus
        bus.register("helper")
        cases = [
            # Row type on the left, envelope type on the right; each pair
            # agrees under naive coercion but must be rejected strictly.
            ("string-false-vs-true", {"truncated": "false"}, {}),
            ("int-one-vs-true", {"truncated": 1}, {}),
            ("int-refs-vs-str-refs", {"refs": [7]}, {"refs": ("7",)}),
            ("str-refs-vs-list", {"refs": "ab"}, {"refs": ("a", "b")}),
            ("none-refs-vs-list", {}, {"refs": ("r1",)}),
        ]
        for label, wrong_row, wrong_env in cases:
            span = f"sp_wt_{label}"
            env = _env(room, span, **wrong_env)
            meta = {"bot_id": "coder", "recipient": "helper",
                    "kind": "message", "bus_event": True, "trace_id": "tr_wt1",
                    "parent_span_id": None, "span_id": span,
                    "refs": ["r1"], "truncated": True}
            meta.update(wrong_row)
            if label == "none-refs-vs-list":
                del meta["refs"]
            _seed_raw(room, meta)
            reply = _asyncioWT.run(
                app._deliver_envelope_claimed(env, _ctxWT("coder", room)))
            assert reply == "typed reply", f"reply must return ({label})"
            assert all(r.content != "typed reply"
                       for r in _room_with(app, room).current.messages), \
                f"wrong-typed persisted row must not authorize ({label})"
        for label, wrong_row, wrong_env in [("dm-string-false", {"truncated": "false"}, {}),
                             ("dm-int-refs", {"refs": [7]}, {"refs": ("7",)})]:
            span = f"sp_wtdm_{label}"
            env = _env(dm, span, **wrong_env)
            meta = {"bot_id": "coder", "recipient": "helper",
                    "kind": "message", "bus_event": True, "trace_id": "tr_wt1",
                    "parent_span_id": None, "span_id": span,
                    "refs": ["r1"], "truncated": True}
            meta.update(wrong_row)
            _seed_raw(dm, meta)
            reply = _asyncioWT.run(
                app._deliver_envelope_claimed(env, _ctxWT("coder", dm)))
            assert reply == "typed reply", f"reply must return ({label})"
            assert all(r.content != "typed reply"
                       for r in _room_with(app, dm).current.messages), \
                f"wrong-typed DM row must not authorize ({label})"
    finally:
        app.shutdown()


def test_private_raw_memory_read_denied_but_own_search_allowed(tmp_path: Path) -> None:
    # Private turns keep the raw-file denial while filtered tools still
    # return the bot's own entries. A group outsider with `read` must not
    # open the daily memory file, but may reuse its own filtered entry.
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from seeded_config import seeded_config as _seeded_priv
    from thyca.configs import load as _load_priv, save as _save_priv
    from thyca.core.protocol import ToolCall as _CallPriv
    from thyca.llms.llm_base import ChatReply as _ReplyPriv
    from thyca.memories.facade import MemoryFacade as _FacadePriv
    from thyca.tools.path_guard import PathDenied as _DeniedPriv, PathGuard as _GuardPriv
    from thyca.memories.visibility import MemoryView as _ViewPriv, bound_memory_view as _bound_priv
    from thyca.core.channel import WindowRef as _WindowPriv
    import asyncio as _asyncio_priv
    from thyca.bots.context import BotTurnContext as _CtxPriv, BotTurnScope as _ScopePriv

    zone = ZoneInfo("Asia/Ho_Chi_Minh")
    yesterday = (datetime.now(zone).date() - timedelta(days=1)).isoformat()
    when = datetime.fromisoformat(f"{yesterday}T12:00:00+07:00")
    team_marker = "TEAMPRIV1-LEAK-CHECK"
    own_marker = "OWNPRIV1-KEEP-CHECK"
    foreign_marker = "FOREIGNPRIV1-OTHER-CHECK"
    day = _FacadePriv(tmp_path, timezone_name="Asia/Ho_Chi_Minh").archive.day(when)
    raw_path = tmp_path / "memory" / f"{day}.md"

    class _PrivReadLLM:
        # Fake provider for the outsider: first asks for a raw file
        # read plus an own-entry search, then answers after tool results.
        def __init__(self) -> None:
            self.seen_tools: set[str] = set()
            self.tool_contents: list[tuple[str | None, str]] = []

        async def chat(self, messages, tools=None):
            has_tool = any(getattr(m, "role", None) == "tool" for m in messages)
            if not has_tool:
                names: set[str] = set()
                for item in (tools or []):
                    try:
                        name = (item.get("function") or {}).get("name")
                    except Exception:
                        name = None
                    if name:
                        names.add(name)
                self.seen_tools = names
                return _ReplyPriv(
                    content=None, model="m",
                    tool_calls=[
                        _CallPriv(id="c-read", name="read",
                                  arguments={"path": str(raw_path)}),
                        _CallPriv(id="c-search", name="memory_search",
                                  arguments={"query": own_marker}),
                    ],
                    usage={"prompt_tokens": 5, "completion_tokens": 1,
                           "total_tokens": 6},
                )
            for item in messages:
                if getattr(item, "role", None) == "tool":
                    self.tool_contents.append(
                        (getattr(item, "tool_call_id", None),
                         str(getattr(item, "content", "") or "")))
            return _ReplyPriv(content="private done", model="m",
                                usage={"prompt_tokens": 5, "completion_tokens": 1,
                                       "total_tokens": 6})

    llm = _PrivReadLLM()
    _save_priv(_seeded_priv(), tmp_path / "config.json")
    app = ChatApp(tmp_path, _load_priv(tmp_path / "config.json"), connect=llm)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Writes code.", persona="P"))
        app._bots.create(BotCard(bot_id="helper", name="helper", role="Helper",
                                 description="Helps.", persona="P", tools=("read",)))
        room = app.create(bots=["coder", "thyca"])["id"]
        assert app.room_bots(room) == ["coder", "thyca"]
        facade = _FacadePriv(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
        facade.remember(f"team marker {team_marker}", f"{team_marker} shared body",
                        now=when, chat=room)
        facade.remember(f"own marker {own_marker}", f"{own_marker} own body",
                        now=when, chat=room, bots=["helper"])
        facade.remember(f"foreign marker {foreign_marker}",
                        f"{foreign_marker} other body",
                        now=when, chat=room, bots=["coder"])
        facade._refresh_index()
        assert team_marker in raw_path.read_text(encoding="utf-8")
        assert own_marker in raw_path.read_text(encoding="utf-8")
        bus = app._runtime._bus
        bus.register("helper")
        bus.register("coder")
        envelope = bus.send(sender="coder", recipient="helper", kind="message",
                            session_id=room, payload="chosen task")
        scope = _ScopePriv(store=app._sessions.store, act=app._act,
                           tools=app._tools, memory=app._memory,
                           state=app._state, zone=app._zone)
        ctx = _CtxPriv(sender_id="coder", room_id=room,
                       scope=scope, turn_cfg=app._current_cfg())
        reply = _asyncio_priv.run(app._deliver_envelope(envelope, ctx))
        assert reply == "private done"
        assert "read" in llm.seen_tools, "outsider must be configured with read"
        assert "memory_search" in llm.seen_tools
        by_id = dict(llm.tool_contents)
        assert "c-read" in by_id and "c-search" in by_id
        raw_result = by_id["c-read"]
        assert "read denied" in raw_result, "raw memory read must stay denied"
        assert team_marker not in raw_result
        assert own_marker not in raw_result
        assert foreign_marker not in raw_result
        search_result = by_id["c-search"]
        assert own_marker in search_result, "own filtered entry stays permitted"
        assert team_marker not in search_result
        assert foreign_marker not in search_result
        # Normal controls: group denies, DM and unbound allow, pinned private denies.
        guard = _GuardPriv(tmp_path)
        group_view = _ViewPriv(
            _WindowPriv(window_id=room, channel_id="webui", kind="group",
                        participants=("coder", "thyca")), "helper", tmp_path)
        dm_view = _ViewPriv(
            _WindowPriv(window_id="helper", channel_id="webui", kind="dm",
                        participants=("helper",)), "helper", tmp_path)
        pinned = _ViewPriv(window=None, bot_id="helper", thyca_dir=tmp_path,
                           denied_origins=frozenset({room}))
        with _bound_priv(group_view):
            try:
                guard.deny_read(str(raw_path))
                raise AssertionError("group raw read must be denied")
            except _DeniedPriv:
                pass
        with _bound_priv(dm_view):
            guard.deny_read(str(raw_path))
        guard.deny_read(str(raw_path))
        with _bound_priv(pinned):
            try:
                guard.deny_read(str(raw_path))
                raise AssertionError("pinned private raw read must be denied")
            except _DeniedPriv:
                pass
    finally:
        app.shutdown()
