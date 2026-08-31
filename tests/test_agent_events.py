from __future__ import annotations

import pytest

from thyca.agent.events import TurnEvent, emit_event


def test_valid_types_to_dict_exact_keys() -> None:
    assert TurnEvent(type="turn.accepted").to_dict() == {"type": "turn.accepted"}
    assert TurnEvent(type="llm.started", round=1).to_dict() == {
        "type": "llm.started",
        "round": 1,
    }
    assert TurnEvent(type="llm.finished", round=1, tool_count=2).to_dict() == {
        "type": "llm.finished",
        "round": 1,
        "tool_count": 2,
    }
    assert TurnEvent(
        type="tool.started", round=1, call_id="call-1", name="bash"
    ).to_dict() == {
        "type": "tool.started",
        "round": 1,
        "call_id": "call-1",
        "name": "bash",
    }
    assert TurnEvent(
        type="tool.finished", round=1, call_id="call-1", name="bash", ok=True
    ).to_dict() == {
        "type": "tool.finished",
        "round": 1,
        "call_id": "call-1",
        "name": "bash",
        "ok": True,
    }
    assert TurnEvent(type="session.naming.started").to_dict() == {
        "type": "session.naming.started"
    }
    assert TurnEvent(type="session.naming.finished", updated=True).to_dict() == {
        "type": "session.naming.finished",
        "updated": True,
    }


def test_false_flags_appear_in_to_dict() -> None:
    finished = TurnEvent(
        type="tool.finished", round=1, call_id="call-1", name="bash", ok=False
    )
    assert finished.to_dict()["ok"] is False
    naming = TurnEvent(type="session.naming.finished", updated=False)
    assert naming.to_dict()["updated"] is False


def test_extra_field_rejected() -> None:
    with pytest.raises(ValueError, match="unexpected field"):
        TurnEvent(type="turn.accepted", round=1)


def test_unknown_type_rejected() -> None:
    with pytest.raises(ValueError, match="unknown event type"):
        TurnEvent(type="turn.completed")
    with pytest.raises(ValueError, match="unknown event type"):
        TurnEvent(type="turn.failed")


def test_bool_as_int_rejected() -> None:
    with pytest.raises(ValueError, match="round"):
        TurnEvent(type="llm.started", round=True)
    with pytest.raises(ValueError, match="tool_count"):
        TurnEvent(type="llm.finished", round=1, tool_count=False)


def test_invalid_name_becomes_public_tool() -> None:
    path_like = TurnEvent(
        type="tool.started", round=1, call_id="call-1", name="/usr/bin/bash"
    )
    assert path_like.name == "tool"
    oversized = TurnEvent(
        type="tool.started", round=1, call_id="call-1", name="a" * 65
    )
    assert oversized.name == "tool"
    assert oversized.to_dict()["name"] == "tool"


def test_valid_mcp_name_kept() -> None:
    event = TurnEvent(
        type="tool.started", round=1, call_id="call-1", name="echo__ping"
    )
    assert event.name == "echo__ping"
    assert event.to_dict()["name"] == "echo__ping"


def test_skill_events_to_dict_exact_keys() -> None:
    assert TurnEvent(
        type="skill.started", round=1, call_id="call-1", name="create-skill"
    ).to_dict() == {
        "type": "skill.started",
        "round": 1,
        "call_id": "call-1",
        "name": "create-skill",
    }
    assert TurnEvent(
        type="skill.finished", round=1, call_id="call-1", name="create-skill", ok=True
    ).to_dict() == {
        "type": "skill.finished",
        "round": 1,
        "call_id": "call-1",
        "name": "create-skill",
        "ok": True,
    }


def test_skill_event_rejects_extra_fields() -> None:
    with pytest.raises(TypeError, match="path"):
        TurnEvent(
            type="skill.started",
            round=1,
            call_id="call-1",
            name="create-skill",
            path="/home/x/.thyca/skills/create-skill/SKILL.md",
        )
    with pytest.raises(ValueError, match="unexpected field"):
        TurnEvent(
            type="tool.started",
            round=1,
            call_id="call-1",
            name="bash",
            ok=True,
        )


def test_bad_call_id_becomes_public_call() -> None:
    empty = TurnEvent(type="tool.started", round=1, call_id="", name="bash")
    assert empty.call_id == "call"
    path_like = TurnEvent(
        type="tool.started", round=1, call_id="../secret", name="bash"
    )
    assert path_like.call_id == "call"
    assert path_like.to_dict()["call_id"] == "call"


def test_emit_event_none_is_noop() -> None:
    emit_event(None, TurnEvent(type="turn.accepted"))


def test_emit_event_swallows_sink_errors() -> None:
    def boom(_event: TurnEvent) -> None:
        raise RuntimeError("sink exploded")

    emit_event(boom, TurnEvent(type="turn.accepted"))


def test_emit_event_delivers() -> None:
    received: list[TurnEvent] = []
    event = TurnEvent(type="turn.accepted")
    emit_event(received.append, event)
    assert received == [event]
