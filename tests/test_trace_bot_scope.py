"""Usage statistics must scope contributions, never whole shared conversations."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from thyca.core.protocol import Message
from thyca.serve import trace_api
from thyca.serve.trace import aggregate, turns_from_session
from thyca.sessions import Session


TS = "2026-10-24T10:00:00Z"


def _reply(bot: str | None, prompt: int, cached: int, completion: int,
           cost: float | None, *, model: str = "coder-model", requests: int = 1,
           tools: int = 0, latency: int = 10) -> Message:
    meta = {"model": model, "requests": requests, "tool_call_count": tools,
            "rounds": requests, "latency_ms": latency,
            "usage": {"prompt_tokens": prompt, "cached_tokens": cached,
                      "completion_tokens": completion, "total_tokens": prompt + completion}}
    if bot is not None:
        meta["bot_id"] = bot
    if cost is not None:
        meta["cost_usd"] = cost
    return Message(role="assistant", content=f"reply from {bot}", ts=TS, meta=meta)


def _session(sid: str, messages: list[Message], *, dm: str | None = None) -> Session:
    return Session(sid, Path("/tmp") / f"{sid}.jsonl", messages,
                   title=sid, bots=["thyca", "coder", "idle"] if dm is None else [dm], dm_bot=dm)


def _turns():
    group = turns_from_session(_session("group", [
        Message(role="user", content="group question", ts=TS),
        _reply("thyca", 100, 20, 10, 0.02, model="root-model", requests=2, tools=1, latency=100),
        _reply("coder", 30, 5, 6, 0.0, requests=2, tools=3, latency=30),
        _reply("coder", 20, 2, 4, None, latency=20),
        Message(role="user", content="root only", ts=TS),
        _reply("thyca", 40, 4, 5, 0.01, model="root-model", latency=50),
    ]))
    dm = turns_from_session(_session("private", [
        Message(role="user", content="private", ts=TS),
        _reply("coder", 8, 1, 2, None),
    ], dm="coder"))
    unknown = turns_from_session(_session("unattributed", [
        Message(role="user", content="unknown", ts=TS),
        _reply(None, 2, 0, 1, 0.004),
    ]))
    return group + dm + unknown


def test_bot_scope_uses_child_parts_and_never_membership(monkeypatch):
    rows = _turns()
    before = [row.to_payload() for row in rows]
    monkeypatch.setattr(trace_api, "collect_turns", lambda _chat: rows)
    own = trace_api.trace_list_payload(None, "bot_id=coder")
    assert own["total"] == 2  # one shared turn, one private turn
    shared, private = own["traces"]
    assert (shared["session_id"], shared["turn_index"]) == ("group", 0)
    assert shared["bot_id"] == "coder"
    assert shared["requests"] == 3
    assert shared["tool_calls"] == 3
    assert shared["prompt_tokens"] == 50
    assert shared["cached_tokens"] == 7
    assert shared["completion_tokens"] == 10
    assert shared["total_tokens"] == 60
    assert shared["cost_usd"] == 0.0  # known zero, despite another unpriced round
    assert shared["latency_ms"] == 50
    assert shared["rounds"] == 3
    assert shared["model"] == "coder-model"
    assert {part["bot_id"] for part in shared["bot_breakdown"]} == {"coder"}
    assert private["cost_usd"] is None
    assert trace_api.trace_list_payload(None, "bot_id=idle")["total"] == 0
    assert trace_api.trace_list_payload(None, "bot_id=missing")["total"] == 0
    assert [row.to_payload() for row in rows] == before
    assert trace_api.trace_list_payload(None, "")["traces"] == before


def test_stats_and_list_share_exact_scoping_and_paging(monkeypatch):
    rows = _turns()
    monkeypatch.setattr(trace_api, "collect_turns", lambda _chat: rows)
    own = trace_api.trace_stats_payload(None, "bot_id=coder")
    total = own["totals"]
    assert total["turns"] == 2
    assert total["sessions"] == 2
    assert total["requests"] == 4
    assert total["tool_calls"] == 3
    assert total["total_tokens"] == 70
    assert total["prompt_tokens"] == 58
    assert total["cached_tokens"] == 8
    assert total["completion_tokens"] == 12
    assert total["cost_usd"] == 0
    assert total["priced_turns"] == 1  # recorded-known, not full pricing coverage
    assert {entry["bot_id"] for entry in own["by_bot"]} == {"coder"}
    assert own["by_bot"][0]["turns"] == 2
    assert own["by_bot"][0]["sessions"] == 2
    model = own["by_model"][0]
    assert model["model"] == "coder-model"
    assert model["total_tokens"] == 70
    assert model["turns"] == 2
    assert model["priced_turns"] == 1
    day = own["by_day"][0]
    assert day["total_tokens"] == day["prompt_tokens"] + day["completion_tokens"] == 70
    assert day["cached_tokens"] == 8
    assert day["turns"] == 2
    assert trace_api.trace_list_payload(None, "bot_id=coder&limit=1&offset=1")["total"] == 2
    assert len(trace_api.trace_list_payload(None, "bot_id=coder&limit=1&offset=1")["traces"]) == 1
    assert trace_api.trace_list_payload(None, "bot_id=coder&model=root-model")["total"] == 0
    assert trace_api.trace_list_payload(None, "bot_id=coder&model=coder-model")["total"] == 2
    assert trace_api.trace_list_payload(None, "bot_id=coder&from=2026-10-25")["total"] == 0
    assert trace_api.trace_list_payload(None, "bot_id=coder&q=group")["total"] == 1
    assert trace_api.trace_list_payload(None, "bot_id=coder&status=failed")["total"] == 0


def test_overview_session_counts_do_not_sum_bot_participations():
    data = aggregate(_turns())
    assert data["totals"]["sessions"] == 3
    assert data["totals"]["turns"] == 4
    assert sum(row["sessions"] for row in data["by_bot"]) == 4
    assert sum(row["turns"] for row in data["by_bot"]) == 5
    assert sum(row["requests"] for row in data["by_bot"]) == data["totals"]["requests"]
    assert sum(row["total_tokens"] for row in data["by_bot"]) == data["totals"]["total_tokens"]
    assert sum(row["cost_usd"] or 0 for row in data["by_bot"]) == pytest.approx(data["totals"]["cost_usd"])


def test_duplicate_breakdown_parts_count_turn_and_session_once():
    turn = _turns()[0]
    coder = next(part for part in turn.bot_breakdown if part["bot_id"] == "coder")
    split = replace(turn, bot_breakdown=[dict(coder), dict(coder)])
    data = aggregate([split])
    own = data["by_bot"][0]
    assert own["turns"] == 1
    assert own["sessions"] == 1
    assert own["priced_turns"] == 1
    assert own["requests"] == coder["requests"] * 2


def test_legacy_unattributed_dm_is_not_guessed_from_owner(monkeypatch):
    turn = turns_from_session(_session("old-private", [
        Message(role="user", content="old", ts=TS),
        _reply(None, 2, 0, 1, None),
    ], dm="coder"))[0]
    monkeypatch.setattr(trace_api, "collect_turns", lambda _chat: [turn])
    assert trace_api.trace_list_payload(None, "bot_id=coder")["total"] == 0
    assert trace_api.trace_stats_payload(None, "")["by_bot"][0]["bot_id"] == "unknown"
    # A summary explicitly attributed to one bot but with no breakdown is usable.
    explicit = replace(turn, bot_id="coder", bot_breakdown=[])
    monkeypatch.setattr(trace_api, "collect_turns", lambda _chat: [explicit])
    assert trace_api.trace_list_payload(None, "bot_id=coder")["traces"][0]["total_tokens"] == 3


def test_scope_does_not_change_full_detail_or_shared_status(monkeypatch):
    row = _turns()[0]
    failed = replace(row, status="failed", error={"message": "shared turn failed"})
    monkeypatch.setattr(trace_api, "collect_turns", lambda _chat: [failed])
    own = trace_api.trace_list_payload(None, "bot_id=coder")["traces"][0]
    assert own["status"] == "failed"  # outcome remains full-turn context
    monkeypatch.setattr(trace_api, "cached_turns_for", lambda _store, _sid: [failed])
    chat = SimpleNamespace(trace_store=lambda: object(), skills_root=Path("/tmp"))
    detail = trace_api.trace_detail_payload(chat, "group", 0)
    assert detail["requests"] == 5  # full turn, not the scoped 3
    assert detail["total_tokens"] == 170  # full turn, not the scoped 60
    assert detail["messages"][1]["content"] == "reply from thyca"
    assert own["total_tokens"] == 60


def test_source_http_scopes_shared_and_private_work_without_writes(tmp_path):
    """Exercise real session files -> scan -> HTTP stats/list -> full detail."""
    from test_serve_trace import _chat, _json, _start, _stop
    from thyca.sessions import SessionManager

    chat = _chat(tmp_path)
    manager = SessionManager(tmp_path / "sessions")
    expected = _turns()
    ids = {}
    try:
        for source_id in ("group", "private", "unattributed"):
            source_turns = [row for row in expected if row.session_id == source_id]
            session = manager.create()
            ids[source_id] = session.id
            manager.store.append_bots(session.path, source_turns[0].session_bots)
            if source_id == "private":
                manager.store.append_dm(session.path, "coder")
            for turn in source_turns:
                for message in turn.messages:
                    manager.append(message)
        baseline = {path: path.read_bytes() for path in manager.store.list_paths()}
        httpd, thread = _start(tmp_path, chat)
        try:
            overview = _json(httpd, "/api/traces/stats")
            assert overview["totals"]["sessions"] == 3
            assert overview["totals"]["turns"] == 4
            assert overview["totals"]["total_tokens"] == 228
            own = _json(httpd, "/api/traces/stats?bot_id=coder")
            assert own["totals"]["sessions"] == 2
            assert own["totals"]["turns"] == 2
            assert own["totals"]["total_tokens"] == 70
            assert own["totals"]["requests"] == 4
            listing = _json(httpd, "/api/traces?bot_id=coder")
            assert listing["total"] == 2
            shared = next(row for row in listing["traces"] if row["session_id"] == ids["group"])
            assert shared["total_tokens"] == 60
            assert shared["requests"] == 3
            detail = _json(httpd, f"/api/traces/{ids['group']}/0")
            assert detail["total_tokens"] == 170
            assert detail["requests"] == 5
            assert {entry["bot_id"] for entry in detail["bot_breakdown"]} == {"thyca", "coder"}
            assert {path: path.read_bytes() for path in baseline} == baseline
        finally:
            _stop(httpd, thread)
    finally:
        chat.shutdown()


def test_empty_scoped_stats_keep_cost_unknown(monkeypatch):
    monkeypatch.setattr(trace_api, "collect_turns", lambda _chat: [])
    data = trace_api.trace_stats_payload(None, "bot_id=coder")
    assert data["totals"]["sessions"] == data["totals"]["turns"] == 0
    assert data["totals"]["priced_turns"] == 0
    assert data["totals"]["cost_usd"] is None
    assert data["by_day"] == data["by_bot"] == data["by_model"] == []
