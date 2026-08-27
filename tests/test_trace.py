from __future__ import annotations

from pathlib import Path

from thyca.protocol import Message, ToolCall
from thyca.sessions import Session
from thyca.trace import aggregate, turns_from_session

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
    days = {row["day"]: row for row in data["by_day"]}
    assert days["2026-08-26"]["requests"] == 1
    assert days["2026-08-25"]["cost_usd"] is None
    statuses = {row["status"]: row["requests"] for row in data["by_status"]}
    assert statuses["completed"] == 2
    hours = {row["hour"]: row["requests"] for row in data["by_hour"]}
    assert hours["09"] == 2
