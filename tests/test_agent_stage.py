from __future__ import annotations

import pytest

from thyca.agent.stage import Stage
from thyca.protocol import Message


def test_stage_defaults_isolated() -> None:
    a = Stage()
    b = Stage()
    assert a.messages is not b.messages
    assert a.results is not b.results
    a.messages.append(Message(role="user", content="x", ts="2026-01-01T00:00:00Z"))
    assert b.messages == []


def test_stage_rejects_negative_round() -> None:
    with pytest.raises(ValueError):
        Stage(round=-1)
