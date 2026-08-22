"""Session service tests — TASK-303a-d verification."""
from __future__ import annotations

import json
import os
import stat
import threading
from pathlib import Path

import pytest

from thyca.config import LimitsCfg
from thyca.protocol import Message, ToolCall
from thyca.sessions import (
    SessionCorrupt,
    SessionError,
    SessionManager,
    SessionNotFound,
    SessionStore,
    estimate_tokens,
)


def msg(role: str, content: str | None = "x", **kw: object) -> Message:
    return Message(role=role, content=content, ts="2026-01-01T00:00:00Z", **kw)


def test_roundtrip_three_turns_and_permissions(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    session = manager.create()
    manager.append(msg("user", "hello", meta={"x": "y"}))
    manager.append(msg("assistant", "answer"))
    call = ToolCall("call-1", "echo", {"x": 1})
    manager.append(msg("user", "tool please"))
    manager.append(msg("assistant", None, tool_calls=[call]))
    manager.append(msg("tool", "done", tool_call_id="call-1"))
    manager.append(msg("assistant", "final"))
    assert len(session.messages) == 6
    loaded = SessionManager(tmp_path).load(session.id)
    assert len(loaded.messages) == 6
    assert loaded.messages[0].meta == {"x": "y"}
    assert loaded.messages[3].content is None
    assert loaded.messages[3].tool_calls is not None
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
    assert stat.S_IMODE(session.path.stat().st_mode) == 0o600


def test_list_paths_empty_and_skips(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    assert store.list_paths() == []
    assert SessionManager(tmp_path).list_sessions() == []
    manager = SessionManager(tmp_path)
    old, new = manager.create(), manager.create()
    os.utime(old.path, ns=(1, 1))
    os.utime(new.path, ns=(2, 2))
    (tmp_path / "notes.jsonl").write_text("nope", encoding="utf-8")
    (tmp_path / "link.jsonl").symlink_to(new.path)
    (tmp_path / "sub.jsonl").mkdir()
    assert [path.stem for path in store.list_paths()] == [new.id, old.id]


def test_list_sessions_skips_corrupt_does_not_set_current(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    good = manager.create()
    manager.append(msg("user", "ok"))
    os.utime(good.path, ns=(2, 2))
    (tmp_path / "2026-01-01T00-00-00_ffff.jsonl").write_text("{bad\n", encoding="utf-8")
    other = SessionManager(tmp_path)
    listed = other.list_sessions()
    assert [item.id for item in listed] == [good.id]
    try:
        other.current
    except SessionError:
        pass
    else:
        raise AssertionError("list_sessions must not set current")
    other.load(good.id)
    other.list_sessions()
    assert other.current.id == good.id


def test_continue_mtime_and_skips(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    old, new = manager.create(), manager.create()
    os.utime(old.path, ns=(1, 1))
    os.utime(new.path, ns=(2, 2))
    (tmp_path / "sub.jsonl").mkdir()
    (tmp_path / "link.jsonl").symlink_to(new.path)
    (tmp_path / "notes.txt").write_text("nope", encoding="utf-8")
    assert manager.continue_last().id == new.id
    with pytest.raises(SessionNotFound):
        SessionManager(tmp_path / "missing").continue_last()
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(SessionNotFound):
        SessionManager(empty).continue_last()


def test_invalid_json_reports_path_and_line(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    session = manager.create()
    session.path.write_text(
        '{"role":"user","ts":"2026-01-01T00:00:00Z"}\n{bad', encoding="utf-8"
    )
    with pytest.raises(SessionCorrupt, match=r":2"):
        manager.load(session.id)


def test_invalid_schema_and_orphan(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    session = manager.create()
    session.path.write_text(
        json.dumps({"role": "tool", "content": "x", "ts": "2026-01-01T00:00:00Z"}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(SessionCorrupt, match=r":1"):
        manager.load(session.id)
    session.path.write_text(
        json.dumps({"content": "x", "ts": "2026-01-01T00:00:00Z"}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(SessionCorrupt, match=r":1"):
        manager.load(session.id)


def test_concurrent_append(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    manager.create()
    errors: list[Exception] = []

    def append(i: int) -> None:
        try:
            manager.append(msg("user", str(i)))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=append, args=(i,)) for i in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert len(manager.continue_last().messages) == 10


def test_estimate_deterministic_and_compaction_boundary(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path, LimitsCfg(contextTokens=1000))
    session = manager.create()
    for i in range(8):
        manager.append(msg("user", "u" * 300 + str(i)))
        manager.append(msg("assistant", "a" * 300))
    assert estimate_tokens(msg("user", "same")) == estimate_tokens(msg("user", "same"))
    assert manager.compact_if_needed()
    loaded = manager.load(session.id)
    assert loaded.messages[0].role == "system"
    marker = loaded.messages[0].content or ""
    assert marker.startswith("[compaction: omitted ")
    excerpt = marker.rsplit("excerpt: ", 1)[-1][:-1]
    assert len(excerpt) <= 1000
    assert loaded.messages[0].meta is not None
    assert loaded.messages[0].meta["omitted_messages"] > 0
    assert loaded.messages[0].meta["omitted_turns"] > 0
    assert loaded.messages[0].meta["omitted_chars"] > 0
    assert all(m.role != "tool" or m.tool_call_id for m in loaded.messages)
    tail_tokens = sum(estimate_tokens(m) for m in loaded.messages[1:])
    assert tail_tokens <= int(1000 * 0.6) or len(_user_assistant_turns(loaded.messages[1:])) == 1


def _user_assistant_turns(messages: list[Message]) -> list[list[Message]]:
    turns: list[list[Message]] = []
    current: list[Message] = []
    for item in messages:
        current.append(item)
        if item.role == "assistant" and not item.tool_calls:
            turns.append(current)
            current = []
    if current:
        turns.append(current)
    return turns


def test_single_oversize_turn_kept(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path, LimitsCfg(contextTokens=1000))
    session = manager.create()
    manager.append(msg("user", "u" * 8000))
    manager.append(msg("assistant", "a" * 8000))
    assert manager.compact_if_needed()
    roles = [item.role for item in session.messages]
    assert roles == ["system", "user", "assistant"]
    loaded = manager.load(session.id)
    assert [item.role for item in loaded.messages] == ["system", "user", "assistant"]


def test_traversal_and_replace_failure_preserve_old(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = SessionManager(tmp_path, LimitsCfg(contextTokens=1000))
    session = manager.create()
    for i in range(4):
        manager.append(msg("user", "x" * 500 + str(i)))
        manager.append(msg("assistant", "y" * 500))
    original = session.path.read_bytes()
    monkeypatch.setattr(os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("no replace")))
    with pytest.raises(SessionError):
        manager.compact_if_needed()
    assert session.path.read_bytes() == original
    with pytest.raises(SessionNotFound):
        manager.load("../escape")
    with pytest.raises(SessionNotFound):
        SessionStore(tmp_path).create("../../etc/passwd")


def test_compaction_parent_fsync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = SessionManager(tmp_path, LimitsCfg(contextTokens=1000))
    manager.create()
    for i in range(4):
        manager.append(msg("user", "x" * 500 + str(i)))
        manager.append(msg("assistant", "y" * 500))
    real_fsync = os.fsync
    calls: list[int] = []
    monkeypatch.setattr(os, "fsync", lambda fd: (calls.append(fd), real_fsync(fd))[1])
    assert manager.compact_if_needed()
    assert len(calls) >= 2


def test_compaction_keeps_complete_tool_round(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path, LimitsCfg(contextTokens=1000))
    session = manager.create()
    for i in range(6):
        manager.append(msg("user", "u" * 200 + str(i)))
        manager.append(msg("assistant", None, tool_calls=[ToolCall(f"c{i}", "echo", {"i": i})]))
        manager.append(msg("tool", "r" * 200, tool_call_id=f"c{i}"))
        manager.append(msg("assistant", "a" * 200))
    assert manager.compact_if_needed()
    tail = session.messages[1:]
    pending: set[str] = set()
    for item in tail:
        if item.role == "assistant" and item.tool_calls:
            pending.update(call.id for call in item.tool_calls)
        elif item.role == "tool":
            assert item.tool_call_id in pending
            pending.discard(item.tool_call_id or "")
    assert not pending


def test_concurrent_append_and_compact(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path, LimitsCfg(contextTokens=1000))
    manager.create()
    errors: list[Exception] = []

    def work(i: int) -> None:
        try:
            manager.append(msg("user", "x" * 200 + str(i)))
            manager.append(msg("assistant", "y" * 200))
            manager.compact_if_needed()
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    loaded = manager.continue_last()
    assert loaded.messages
    roles = {item.role for item in loaded.messages}
    assert roles <= {"system", "user", "assistant"}
