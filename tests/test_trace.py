from __future__ import annotations

from pathlib import Path

from thyca.core.protocol import Message, ToolCall
from thyca.sessions import Session
from thyca.serve.trace import aggregate, turns_from_session

TS = "2026-08-26T09:12:03Z"
TS2 = "2026-08-26T09:12:04Z"


def _session(session_id: str, messages: list[Message], title: str | None = None) -> Session:
    return Session(session_id, Path(f"{session_id}.jsonl"), messages, title)


def test_turns_from_session_groups_user_to_final_assistant() -> None:
    call = ToolCall(id="c1", name="echo")
    session = _session(
        "2026-08-26T09-12-03_abcd",
        [
            Message(role="user", content="hi", ts=TS),
            Message(
                role="assistant",
                content=None,
                tool_calls=[call],
                ts=TS,
                meta={
                    "kind": "llm",
                    "round": 1,
                    "model": "gpt-4o-mini",
                    "latency_ms": 100,
                    "usage": {
                        "prompt_tokens": 100,
                        "cached_tokens": 20,
                        "completion_tokens": 10,
                        "total_tokens": 110,
                    },
                    "cost_usd": 0.00002,
                },
            ),
            Message(role="tool", content="ok", tool_call_id="c1", ts=TS, meta={"latency_ms": 5, "round": 1}),
            Message(
                role="assistant",
                content="done",
                ts=TS2,
                meta={
                    "kind": "llm",
                    "round": 2,
                    "model": "gpt-4o-mini",
                    "latency_ms": 50,
                    "usage": {
                        "prompt_tokens": 10,
                        "cached_tokens": 0,
                        "completion_tokens": 4,
                        "total_tokens": 14,
                    },
                    "cost_usd": 0.000004,
                },
            ),
        ],
        title="Linux là target",
    )
    turns = turns_from_session(session)
    assert len(turns) == 1
    turn = turns[0]
    assert turn.turn_index == 0
    assert turn.title == "Linux là target"
    assert turn.model == "gpt-4o-mini"
    assert turn.status == "completed"
    assert turn.rounds == 2
    assert turn.prompt_tokens == 110
    assert turn.cached_tokens == 20
    assert turn.completion_tokens == 14
    assert turn.total_tokens == 124
    assert turn.cost_usd == 0.000024
    assert turn.latency_ms == 150


def test_legacy_session_without_usage_keeps_tokens_none() -> None:
    session = _session(
        "2026-08-26T09-12-03_abce",
        [
            Message(role="user", content="hi", ts=TS),
            Message(role="assistant", content="hey", ts=TS2),
        ],
    )
    turn = turns_from_session(session)[0]
    assert turn.prompt_tokens is None
    assert turn.cached_tokens is None
    assert turn.completion_tokens is None
    assert turn.total_tokens is None
    assert turn.cost_usd is None
    assert turn.status == "completed"


def test_loop_limit_and_incomplete_tool_round_status() -> None:
    limited = _session(
        "2026-08-26T09-12-03_abcf",
        [
            Message(role="user", content="go", ts=TS),
            Message(
                role="assistant",
                content="loop limit reached",
                ts=TS2,
                meta={"kind": "llm", "status": "loop_limit"},
            ),
        ],
    )
    assert turns_from_session(limited)[0].status == "loop_limit"

    incomplete = _session(
        "2026-08-26T09-12-03_abcg",
        [
            Message(role="user", content="go", ts=TS),
            Message(
                role="assistant",
                content=None,
                tool_calls=[ToolCall(id="c1", name="echo")],
                ts=TS,
            ),
            Message(role="tool", content="x", tool_call_id="c1", ts=TS2),
        ],
    )
    assert turns_from_session(incomplete)[0].status == "failed"

    lone_user = _session(
        "2026-08-26T09-12-03_abch",
        [Message(role="user", content="go", ts=TS)],
    )
    assert turns_from_session(lone_user)[0].status == "failed"


def test_aggregate_by_model_and_unknown_cost() -> None:
    mini = turns_from_session(
        _session(
            "2026-08-26T09-12-03_aaa1",
            [
                Message(role="user", content="a", ts=TS),
                Message(
                    role="assistant",
                    content="a",
                    ts=TS2,
                    meta={
                        "model": "gpt-4o-mini",
                        "usage": {"prompt_tokens": 10, "cached_tokens": 0, "completion_tokens": 2, "total_tokens": 12},
                        "cost_usd": 0.00001,
                        "latency_ms": 100,
                    },
                ),
            ],
        )
    )
    other = turns_from_session(
        _session(
            "2026-08-26T09-12-03_aaa2",
            [
                Message(role="user", content="b", ts="2026-08-25T09:12:03Z"),
                Message(
                    role="assistant",
                    content="b",
                    ts="2026-08-25T09:12:04Z",
                    meta={
                        "model": "foo/bar",
                        "usage": {"prompt_tokens": 5, "cached_tokens": 0, "completion_tokens": 1, "total_tokens": 6},
                        "latency_ms": 200,
                    },
                ),
            ],
        )
    )
    data = aggregate([*mini, *other])
    assert data["totals"]["requests"] == 2
    assert data["totals"]["prompt_tokens"] == 15
    assert data["totals"]["cost_usd"] == 0.00001
    models = {row["model"]: row for row in data["by_model"]}
    assert models["gpt-4o-mini"]["cost_usd"] == 0.00001
    assert models["foo/bar"]["cost_usd"] is None
    assert data["models"] == ["foo/bar", "gpt-4o-mini"]
    # "Gần nhất" needs the newest turn per model; the rows above carry
    # different dates, so each model reports its own.
    assert models["gpt-4o-mini"]["last_started_at"] == TS
    assert models["foo/bar"]["last_started_at"] == "2026-08-25T09:12:03Z"
    days = {row["day"]: row for row in data["by_day"]}
    assert days["2026-08-26"]["requests"] == 1
    assert days["2026-08-25"]["cost_usd"] is None
    statuses = {row["status"]: row["requests"] for row in data["by_status"]}
    assert statuses["completed"] == 2
    hours = {row["hour"]: row["requests"] for row in data["by_hour"]}
    assert hours["09"] == 2


def _assistant(bot_id, prompt, completion, cost, latency, ts=TS2):
    meta = {
        "kind": "llm", "model": "m", "latency_ms": latency,
        "usage": {"prompt_tokens": prompt, "cached_tokens": 0,
                  "completion_tokens": completion,
                  "total_tokens": prompt + completion},
        "cost_usd": cost,
    }
    if bot_id is not None:
        meta["bot_id"] = bot_id
    return Message(role="assistant", content="x", ts=ts, meta=meta)


def test_multi_bot_slice_breakdown_attributes_each_bot() -> None:
    session = _session(
        "2026-08-26T09-12-03_mbot",
        [
            Message(role="user", content="go", ts=TS),
            _assistant("thyca", 100, 10, 0.02, 100),
            _assistant("helper", 50, 5, 0.01, 50),
            _assistant("helper", 10, 1, 0.002, 10),
            _assistant("buddy", 20, 2, 0.005, 20),
        ],
    )
    (turn,) = turns_from_session(session)
    # Legacy single-bot fields unchanged: first bot + whole-slice totals.
    assert turn.bot_id == "thyca"
    assert turn.requests == 4
    assert turn.prompt_tokens == 180
    assert turn.total_tokens == 198
    assert turn.cost_usd == 0.037
    assert turn.latency_ms == 180
    breakdown = {e["bot_id"]: e for e in turn.bot_breakdown}
    assert breakdown["thyca"]["requests"] == 1
    assert breakdown["thyca"]["cost_usd"] == 0.02
    assert breakdown["helper"]["requests"] == 2
    assert breakdown["helper"]["prompt_tokens"] == 60
    assert breakdown["helper"]["cost_usd"] == 0.012
    assert breakdown["helper"]["latency_ms"] == 60
    assert breakdown["buddy"]["total_tokens"] == 22
    # Turn totals == sum of breakdown.
    assert sum(e["requests"] for e in turn.bot_breakdown) == turn.requests
    assert sum(e["prompt_tokens"] or 0 for e in turn.bot_breakdown) == turn.prompt_tokens
    assert sum(e["total_tokens"] or 0 for e in turn.bot_breakdown) == turn.total_tokens
    assert sum(e["cost_usd"] or 0 for e in turn.bot_breakdown) == turn.cost_usd
    assert sum(e["latency_ms"] or 0 for e in turn.bot_breakdown) == turn.latency_ms
    assert turn.to_payload()["bot_breakdown"] == turn.bot_breakdown
    data = aggregate([turn])
    by_bot = {row["bot_id"]: row for row in data["by_bot"]}
    assert by_bot["thyca"]["requests"] == 1
    assert by_bot["thyca"]["cost_usd"] == 0.02
    assert by_bot["helper"]["requests"] == 2
    assert by_bot["helper"]["cost_usd"] == 0.012
    assert by_bot["helper"]["prompt_tokens"] == 60
    assert by_bot["buddy"]["requests"] == 1
    assert sum(r["requests"] for r in data["by_bot"]) == data["totals"]["requests"]
    assert sum(r["cost_usd"] or 0 for r in data["by_bot"]) == data["totals"]["cost_usd"]


def test_single_bot_breakdown_matches_turn_and_legacy_unknown() -> None:
    session = _session(
        "2026-08-26T09-12-03_sbot",
        [
            Message(role="user", content="go", ts=TS),
            _assistant("coder", 10, 2, 0.001, 30),
            _assistant("coder", 5, 1, 0.0005, 20),
        ],
    )
    (turn,) = turns_from_session(session)
    assert len(turn.bot_breakdown) == 1
    entry = turn.bot_breakdown[0]
    assert entry["bot_id"] == "coder"
    assert entry["requests"] == turn.requests == 2
    assert entry["prompt_tokens"] == turn.prompt_tokens
    assert entry["cost_usd"] == turn.cost_usd
    assert entry["latency_ms"] == turn.latency_ms
    by_bot = {r["bot_id"]: r for r in aggregate([turn])["by_bot"]}
    assert by_bot["coder"]["requests"] == 2
    assert by_bot["coder"]["cost_usd"] == turn.cost_usd
    # No bot_id anywhere: breakdown carries None, aggregate keeps "unknown".
    legacy = turns_from_session(_session(
        "2026-08-26T09-12-03_leg1",
        [Message(role="user", content="go", ts=TS),
         _assistant(None, 3, 1, None, 10)],
    ))[0]
    assert legacy.bot_breakdown[0]["bot_id"] is None
    unknown = {r["bot_id"]: r for r in aggregate([legacy])["by_bot"]}
    assert unknown["unknown"]["requests"] == 1
    assert unknown["unknown"]["cost_usd"] is None


def test_error_marker_forces_failed_and_payload() -> None:
    marked = _session(
        "2026-08-26T09-12-03_abcm",
        [
            Message(
                role="user",
                content="go",
                ts=TS,
                meta={"error": {"code": "llm_error", "message": "provider HTTP 404: gone"}},
            ),
        ],
    )
    (turn,) = turns_from_session(marked)
    assert turn.status == "failed"
    assert turn.to_payload()["error"] == {"code": "llm_error", "message": "provider HTTP 404: gone"}


def test_error_marker_wins_over_trailing_assistant() -> None:
    # A turn that died mid-loop after persisting round 1 must not read completed.
    mid = _session(
        "2026-08-26T09-12-03_abcn",
        [
            Message(
                role="user",
                content="go",
                ts=TS,
                meta={"error": {"code": "chat_unavailable", "message": "chat unavailable"}},
            ),
            Message(role="assistant", content="partial", ts=TS2, meta={"kind": "llm"}),
        ],
    )
    assert turns_from_session(mid)[0].status == "failed"


def test_completed_turn_has_no_error() -> None:
    ok = _session(
        "2026-08-26T09-12-03_abco",
        [
            Message(role="user", content="go", ts=TS),
            Message(role="assistant", content="done", ts=TS2, meta={"kind": "llm"}),
        ],
    )
    (turn,) = turns_from_session(ok)
    assert turn.status == "completed"
    assert turn.to_payload()["error"] is None


def test_row_requests_truthful_counting() -> None:
    """Bus rows cost 0; aggregated rows report their own count; cards cost 0."""
    session = _session(
        "2026-08-26T09-12-03_mixd",
        [
            Message(role="user", content="go", ts=TS),
            # Plain llm row: one real call.
            Message(role="assistant", content="a", ts=TS2,
                    meta={"kind": "llm", "bot_id": "thyca"}),
            # Bus envelope row (sender side): no call.
            Message(role="assistant", content="fwd", ts=TS2,
                    meta={"bot_id": "thyca", "kind": "delegate",
                          "bus_event": True}),
            # Legacy bus message (pre-marker): no model/usage + parent.
            Message(role="assistant", content="old fwd", ts=TS2,
                    meta={"bot_id": "thyca", "kind": "message",
                          "parent_span_id": "sp_1"}),
            # Aggregated internal turn of 4 rounds.
            Message(role="assistant", content="done", ts=TS2,
                    meta={"bot_id": "coder", "kind": "result",
                          "model": "m",
                          "usage": {"prompt_tokens": 4, "cached_tokens": 0,
                                    "completion_tokens": 4, "total_tokens": 8},
                          "requests": 4}),
            # App-written card: its model call sits in an aggregate.
            Message(role="assistant", content="", ts=TS2,
                    meta={"bot_id": "coder", "kind": "card",
                          "card": {"type": "question"}}),
        ],
    )
    (turn,) = turns_from_session(session)
    assert turn.requests == 1 + 4
    by_bot = {e["bot_id"]: e for e in turn.bot_breakdown}
    assert by_bot["thyca"]["requests"] == 1
    assert by_bot["coder"]["requests"] == 4
    data = aggregate([turn])
    assert data["totals"]["requests"] == 5
    bots = {b["bot_id"]: b for b in data["by_bot"]}
    assert bots["thyca"]["requests"] == 1
    assert bots["coder"]["requests"] == 4


def test_turns_carry_session_channel_fields(tmp_path=None) -> None:
    from pathlib import Path as _Path

    def _s(sid, bots, dm, messages):
        return Session(sid, _Path(f"{sid}.jsonl"), messages, None, bots=bots, dm_bot=dm)

    user = Message(role="user", content="go", ts=TS)
    final = Message(role="assistant", content="done", ts=TS2, meta={"kind": "llm"})
    dm_turns = turns_from_session(_s("dm1", ["coder"], "coder", [user, final]))
    assert dm_turns[0].session_kind == "dm"
    assert dm_turns[0].session_dm == "coder"
    assert dm_turns[0].session_bots == ["coder"]
    group_turns = turns_from_session(
        _s("g1", ["thyca", "coder"], None, [user, final]))
    assert group_turns[0].session_kind == "group"
    assert group_turns[0].session_dm is None
    assert group_turns[0].session_bots == ["thyca", "coder"]
    legacy_turns = turns_from_session(_s("old", None, None, [user, final]))
    assert legacy_turns[0].session_kind == "group"
    assert legacy_turns[0].session_dm is None
    assert legacy_turns[0].session_bots == ["thyca"]
    payload = dm_turns[0].to_payload()
    assert (payload["session_kind"], payload["session_dm"],
            payload["session_bots"]) == ("dm", "coder", ["coder"])


def test_row_tool_calls_truthful_counting() -> None:
    """Root rows count len(tool_calls); aggregated rows report their own
    tool_call_count (never plus their body list); bus/card/context-stop 0."""
    from thyca.core.protocol import ToolCall

    def call(cid):
        return ToolCall(id=cid, name="read", arguments={})

    session = _session(
        "2026-08-26T09-12-03_mixt",
        [
            Message(role="user", content="go", ts=TS),
            # Root row with two real tool calls.
            Message(role="assistant", content="a", ts=TS2,
                    tool_calls=[call("c1"), call("c2")],
                    meta={"kind": "llm", "bot_id": "thyca"}),
            # Aggregated internal turn: meta count wins over the body list.
            Message(role="assistant", content="done", ts=TS2,
                    tool_calls=[call("c9")],
                    meta={"bot_id": "coder", "kind": "result",
                          "model": "m", "requests": 4, "tool_call_count": 5}),
            # Bus envelope row: no calls.
            Message(role="assistant", content="fwd", ts=TS2,
                    tool_calls=[call("cb")],
                    meta={"bot_id": "thyca", "kind": "delegate",
                          "bus_event": True}),
            # App-written card: no calls.
            Message(role="assistant", content="", ts=TS2,
                    tool_calls=[call("cc")],
                    meta={"bot_id": "coder", "kind": "card",
                          "card": {"type": "question"}}),
            # Context-stop marker: no calls.
            Message(role="assistant", content="stop", ts=TS2,
                    tool_calls=[call("cs")],
                    meta={"kind": "llm", "status": "context_limit"}),
        ],
    )
    (turn,) = turns_from_session(session)
    assert turn.tool_calls == 2 + 5
    by_bot = {e["bot_id"]: e for e in turn.bot_breakdown}
    assert by_bot["thyca"]["tool_calls"] == 2
    assert by_bot["coder"]["tool_calls"] == 5
    payload = turn.to_payload()
    assert payload["tool_calls"] == 7
    data = aggregate([turn])
    assert data["totals"]["tool_calls"] == 7
    bots = {b["bot_id"]: b for b in data["by_bot"]}
    assert bots["thyca"]["tool_calls"] == 2
    assert bots["coder"]["tool_calls"] == 5
