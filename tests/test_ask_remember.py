from __future__ import annotations

from datetime import datetime, timedelta, timezone

from thyca.protocol import Message, ToolCall
from thyca.sessions.ask_remember import ask_remember

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


def _ts(delta: timedelta) -> str:
    return (NOW - delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def _user(delta: timedelta, text: str = "hi") -> Message:
    return Message(role="user", content=text, ts=_ts(delta))


def _remember(delta: timedelta) -> Message:
    return Message(
        role="assistant",
        content=None,
        ts=_ts(delta),
        tool_calls=[ToolCall(id="c1", name="memory_remember", arguments={})],
    )


def test_empty_and_fresh_user() -> None:
    assert ask_remember([], NOW) is False
    assert ask_remember([_user(timedelta(minutes=14))], NOW) is False


def test_idle_unclosed_user() -> None:
    assert ask_remember([_user(timedelta(minutes=15))], NOW) is True


def test_remember_after_user_closes() -> None:
    messages = [_user(timedelta(minutes=20)), _remember(timedelta(minutes=1))]
    assert ask_remember(messages, NOW) is False


def test_new_user_after_remember_can_ask() -> None:
    messages = [
        _user(timedelta(minutes=40), "old"),
        _remember(timedelta(minutes=30)),
        _user(timedelta(minutes=15), "new"),
    ]
    assert ask_remember(messages, NOW) is True


def test_read_only_does_not_mutate() -> None:
    messages = [_user(timedelta(minutes=20))]
    ask_remember(messages, NOW)
    assert len(messages) == 1
