from __future__ import annotations

import pytest

from thyca.agent.assemble import Assemble
from thyca.agent.stage import Stage
from thyca.memory.active import ActiveSnapshot
from thyca.protocol import Message


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
    hot = ActiveSnapshot(soul="S", user="U", memory="M", today="T", yesterday="")
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
