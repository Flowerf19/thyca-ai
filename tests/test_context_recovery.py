"""GOAL-003: recover before the context backstop (regression).

Three 2500-char user/assistant pairs under a 4000-token cap, then "hi":
pre-turn compaction must fire at the shared backstop ratio so the turn
reaches the LLM instead of dying at the guard with zero provider calls.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from thyca.agent.act import Act
from thyca.agent.assemble import Assemble
from thyca.agent.loop import AgentLoop
from thyca.agent.observe import Observe
from thyca.agent.think import Think
from thyca.config import LimitsCfg
from thyca.core.context import BACKSTOP_RATIO
from thyca.core.protocol import Message
from thyca.llm.llm_base import ChatReply
from thyca.sessions import SessionManager
from thyca.sessions.compaction import SessionCompactor, estimate_tokens


class _FakeLLM:
    def __init__(self, replies: list[ChatReply]) -> None:
        self.replies = replies
        self.requests: list[list[Message]] = []

    async def chat(self, messages: list[Message], tools=None) -> ChatReply:
        self.requests.append(list(messages))
        return self.replies.pop(0)


class _FakeDispatcher:
    async def submit(self, call):  # pragma: no cover - no tools in this turn
        raise AssertionError("no tool calls expected")


def _manager(tmp_path: Path) -> SessionManager:
    limits = LimitsCfg(contextTokens=4000)
    return SessionManager(tmp_path, limits=limits)


def _seed_pairs(manager: SessionManager) -> None:
    for round_no in (1, 2, 3):
        manager.append(Message(role="user", content=f"q{round_no} " + "u" * 2497))
        manager.append(
            Message(role="assistant", content=f"a{round_no} " + "a" * 2497)
        )


def _loop(manager: SessionManager, llm: _FakeLLM) -> AgentLoop:
    return AgentLoop(
        sessions=manager,
        assemble=Assemble(),
        think=Think(llm),
        act=Act(_FakeDispatcher()),
        observe=Observe(manager),
        loop_max=5,
        context_tokens=4000,
    )


def test_shared_backstop_ratio_is_95_percent() -> None:
    assert BACKSTOP_RATIO == 0.95


def test_compactor_fires_at_backstop_not_at_cap() -> None:
    compactor = SessionCompactor()
    messages = [
        Message(role="user", content="q " + "u" * 2490),
        Message(role="assistant", content="a " + "a" * 2490),
    ] * 3
    total = sum(estimate_tokens(message) for message in messages)
    assert total <= int(4000 * BACKSTOP_RATIO) < total + 25 <= 4000
    # History alone sits under the backstop: only the full pending
    # overhead (hot prompt + tools + the new user text) trips it.
    assert compactor.compact(list(messages), 4000) is None
    assert (
        compactor.compact(list(messages), 4000, pending_user_tokens=25)
        is not None
    )
    # Well under the backstop: no aggressive compaction for low usage.
    small = [Message(role="user", content="hi")]
    assert compactor.compact(list(small), 4000) is None


def test_recovery_turn_reaches_llm_after_compaction(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.create()
    _seed_pairs(manager)
    estimate = sum(estimate_tokens(m) for m in manager.current.messages)
    assert int(4000 * BACKSTOP_RATIO) < estimate <= 4000
    llm = _FakeLLM([ChatReply(content="still here")])
    reply = asyncio.run(_loop(manager, llm).run("hi"))
    assert reply == "still here"
    assert len(llm.requests) == 1


def test_retry_user_survives_compaction(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.create()
    _seed_pairs(manager)
    manager.append(Message(role="user", content="hi"))
    assert manager.truncate_to_last_user() is True
    compacted = manager.compact_if_needed(pending_user_tokens=9)
    assert compacted is True
    assert manager.current.messages[-1].role == "user"
    assert manager.current.messages[-1].content == "hi"


def test_tool_group_never_splits(tmp_path: Path) -> None:
    from thyca.core.protocol import ToolCall

    manager = _manager(tmp_path)
    manager.create()
    _seed_pairs(manager)
    manager.append(Message(role="user", content="run it"))
    manager.append(
        Message(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="c9", name="bash", arguments={})],
        )
    )
    manager.append(Message(role="tool", content="t" * 500, tool_call_id="c9"))
    assert manager.compact_if_needed() is True
    ids = [m.tool_call_id for m in manager.current.messages if m.role == "tool"]
    calls = [
        c.id
        for m in manager.current.messages
        if m.role == "assistant" and m.tool_calls
        for c in m.tool_calls
    ]
    for tool_id in ids:
        assert tool_id in calls
    for call_id in calls:
        assert call_id in ids


def test_overhead_only_trip_mints_no_marker(tmp_path: Path) -> None:
    compactor = SessionCompactor()
    messages = [
        Message(role="user", content="hello"),
        Message(role="assistant", content="hi there"),
    ]
    assert (
        compactor.compact(
            list(messages), 4000, pending_user_tokens=5000
        )
        is None
    )
    manager = _manager(tmp_path)
    manager.create()
    manager.append(messages[0])
    manager.append(messages[1])
    before = manager.current.path.read_bytes()
    assert manager.compact_if_needed(pending_user_tokens=5000) is False
    assert manager.current.path.read_bytes() == before


def test_overhead_only_trip_keeps_prior_marker_byte_identical(
    tmp_path: Path,
) -> None:
    compactor = SessionCompactor()
    excerpt = "E" * 1000
    marker = Message(
        role="system",
        content=(
            "[compaction: omitted 5 messages/2 turns; excerpt: "
            f"{excerpt}]"
        ),
        meta={
            "omitted_messages": 5,
            "omitted_turns": 2,
            "omitted_chars": 1500,
        },
    )
    body = [
        Message(role="user", content="hello"),
        Message(role="assistant", content="hi there"),
    ]
    assert (
        compactor.compact([marker, *body], 4000, pending_user_tokens=5000)
        is None
    )
    manager = _manager(tmp_path)
    manager.create()
    manager.append(marker)
    manager.append(body[0])
    manager.append(body[1])
    before = manager.current.path.read_bytes()
    assert manager.compact_if_needed(pending_user_tokens=5000) is False
    assert manager.current.path.read_bytes() == before
    assert manager.current.messages[0].content == marker.content
    assert manager.current.messages[0].meta == marker.meta


def test_context_stop_is_not_success(tmp_path: Path) -> None:
    from thyca.serve.trace import turns_from_session
    from thyca.sessions.title import completed_turn_count

    manager = _manager(tmp_path)
    session = manager.create()
    manager.append(Message(role="user", content="old q"))
    manager.append(Message(role="assistant", content="old a"))
    manager.append(Message(role="user", content="too big"))
    manager.append(
        Message(
            role="assistant",
            content="context limit reached",
            meta={"kind": "llm", "status": "context_limit", "round": 2},
        )
    )
    turns = turns_from_session(session)
    assert len(turns) == 2
    assert turns[0].status == "completed"
    assert turns[1].status == "context_limit"
    assert turns[1].requests == 0
    assert turns[1].rounds == 0
    # The naming threshold must not count the stopped turn.
    assert completed_turn_count(session.messages) == 1


def test_context_stop_keeps_prior_real_call(tmp_path: Path) -> None:
    from thyca.serve.trace import turns_from_session

    manager = _manager(tmp_path)
    session = manager.create()
    manager.append(Message(role="user", content="q"))
    manager.append(
        Message(
            role="assistant",
            content="real answer",
            meta={
                "kind": "llm",
                "round": 1,
                "model": "m",
                "usage": {
                    "prompt_tokens": 10,
                    "cached_tokens": 0,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
                "cost_usd": 0.000_001,
                "latency_ms": 12,
            },
        )
    )
    manager.append(
        Message(
            role="assistant",
            content="context limit reached",
            meta={"kind": "llm", "status": "context_limit", "round": 2},
        )
    )
    (turn,) = turns_from_session(session)
    assert turn.status == "context_limit"
    assert turn.requests == 1
    assert turn.rounds == 1
    assert turn.prompt_tokens == 10
    assert turn.total_tokens == 15
    assert turn.cost_usd == pytest.approx(0.000_001)
