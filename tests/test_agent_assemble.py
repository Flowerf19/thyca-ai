from __future__ import annotations

import pytest

from thyca.agent.assemble import Assemble
from thyca.agent.stage import Stage
from thyca.memory.active import ActiveSnapshot
from thyca.core.protocol import Message


def test_assemble_copies_and_appends_user() -> None:
    existing = Message(role="assistant", content="previous", ts="2026-01-01T00:00:00Z")
    original = [existing]
    stage = Stage(messages=original, hot=object())

    Assemble().assemble(stage, "hello")

    assert stage.messages[0] is existing
    assert stage.messages[1].role == "user"
    assert stage.messages[1].content == "hello"
    assert stage.messages is not original
    assert original == [existing]


def test_assemble_does_not_use_hot_to_add_messages() -> None:
    existing = Message(role="user", content="previous", ts="2026-01-01T00:00:00Z")
    stage = Stage(messages=[existing], hot=object())

    Assemble().assemble(stage, "hello")

    assert [(m.role, m.content) for m in stage.messages] == [
        ("user", "previous"),
        ("user", "hello"),
    ]


def test_assemble_injects_system_from_snapshot() -> None:
    hot = ActiveSnapshot(soul="S", user="U", today="T")
    existing = Message(role="assistant", content="prev", ts="2026-01-01T00:00:00Z")
    stage = Stage(messages=[existing], hot=hot)

    Assemble().assemble(stage, "hello")

    assert [m.role for m in stage.messages] == ["system", "assistant", "user"]
    assert stage.messages[0].content is not None
    assert "<role>" in stage.messages[0].content
    assert stage.messages[-1].content == "hello"


def test_assemble_rejects_non_string_user_message() -> None:
    with pytest.raises(ValueError):
        Assemble().assemble(Stage(), 123)  # type: ignore[arg-type]


def test_assemble_drops_naming_meta_message() -> None:
    naming = Message(
        role="assistant",
        content=None,
        ts="2026-01-01T00:00:00Z",
        meta={"kind": "naming"},
    )
    stage = Stage(messages=[naming], hot=object())

    Assemble().assemble(stage, "hello")

    assert [(m.role, m.content) for m in stage.messages] == [("user", "hello")]


def test_assemble_keeps_compaction_marker_after_hot_prompt() -> None:
    hot = ActiveSnapshot(soul="S", user="U", today="T")
    marker = Message(
        role="system",
        content="[compaction: omitted 3 messages/1 turns; excerpt: hi]",
        ts="2026-01-01T00:00:00Z",
    )
    turn = Message(role="assistant", content="prev", ts="2026-01-01T00:00:01Z")
    stage = Stage(messages=[marker, turn], hot=hot)

    Assemble().assemble(stage, "hello")

    assert [m.role for m in stage.messages] == ["system", "system", "assistant", "user"]
    assert "<role>" in (stage.messages[0].content or "")
    assert stage.messages[1] is marker
    assert stage.messages[2] is turn
    assert stage.messages[3].content == "hello"


def test_assemble_still_drops_non_marker_system_messages() -> None:
    hot = ActiveSnapshot(soul="S", user="U", today="T")
    stale = Message(role="system", content="stale hot prompt", ts="2026-01-01T00:00:00Z")
    marker = Message(
        role="system",
        content="[compaction: omitted 1 messages/1 turns; excerpt: x]",
        ts="2026-01-01T00:00:01Z",
    )
    stage = Stage(messages=[stale, marker], hot=hot)

    Assemble().assemble(stage, "hello")

    assert [m.role for m in stage.messages] == ["system", "system", "user"]
    assert stage.messages[1] is marker
    assert all(m.content != "stale hot prompt" for m in stage.messages)
