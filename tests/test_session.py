from __future__ import annotations

import json
import os
import stat
import threading
from pathlib import Path

import pytest

from thyca.config import LimitsCfg
from thyca.protocol import Message, ToolCall
from thyca.session import SessionCorrupt, SessionManager, SessionNotFound, SessionError, estimate_tokens


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
    loaded = SessionManager(tmp_path).load(session.id)
    assert len(loaded.messages) == 6
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
    assert stat.S_IMODE(session.path.stat().st_mode) == 0o600


def test_continue_mtime_and_skips(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    old, new = manager.create(), manager.create()
    os.utime(old.path, ns=(1, 1)); os.utime(new.path, ns=(2, 2))
    (tmp_path / "sub.jsonl").mkdir()
    (tmp_path / "link.jsonl").symlink_to(new.path)
    assert manager.continue_last().id == new.id
    with pytest.raises(SessionNotFound):
        SessionManager(tmp_path / "missing").continue_last()


def test_invalid_json_reports_path_and_line(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    session = manager.create()
    session.path.write_text('{"role":"user","ts":"2026-01-01T00:00:00Z"}\n{bad', encoding="utf-8")
    with pytest.raises(SessionCorrupt, match=r"2"):
        manager.load(session.id)


def test_invalid_schema_and_orphan(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    session = manager.create()
    session.path.write_text(json.dumps({"role": "tool", "content": "x", "ts": "2026-01-01T00:00:00Z"}) + "\n", encoding="utf-8")
    with pytest.raises(SessionCorrupt, match=r":1"):
        manager.load(session.id)


def test_concurrent_append(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path); manager.create()
    errors: list[Exception] = []
    def append(i: int) -> None:
        try: manager.append(msg("user", str(i)))
        except Exception as exc: errors.append(exc)
    threads = [threading.Thread(target=append, args=(i,)) for i in range(10)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert not errors
    assert len(manager.continue_last().messages) == 10


def test_estimate_deterministic_and_compaction_boundary(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path, LimitsCfg(contextTokens=1000)); session = manager.create()
    for i in range(8):
        manager.append(msg("user", "u" * 300 + str(i)))
        manager.append(msg("assistant", "a" * 300))
    assert estimate_tokens(msg("user", "same")) == estimate_tokens(msg("user", "same"))
    assert manager.compact_if_needed()
    loaded = manager.load(session.id)
    assert loaded.messages[0].role == "system"
    marker = loaded.messages[0].content or ""
    assert marker.endswith("a]")
    assert marker.count("\n") <= 4
    assert len(marker.rsplit("excerpt: ", 1)[-1][:-1]) <= 1000
    assert all(m.role != "tool" or m.tool_call_id for m in loaded.messages)


def test_traversal_and_replace_failure_preserve_old(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = SessionManager(tmp_path, LimitsCfg(contextTokens=1000)); session = manager.create()
    for i in range(4): manager.append(msg("user", "x" * 500 + str(i))); manager.append(msg("assistant", "y" * 500))
    original = session.path.read_bytes()
    monkeypatch.setattr(os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("no replace")))
    with pytest.raises(SessionError): manager.compact_if_needed()
    assert session.path.read_bytes() == original
    with pytest.raises(SessionNotFound): manager.load("../escape")


def test_compaction_parent_fsync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = SessionManager(tmp_path, LimitsCfg(contextTokens=1000)); manager.create()
    for i in range(4): manager.append(msg("user", "x" * 500 + str(i))); manager.append(msg("assistant", "y" * 500))
    real_fsync = os.fsync; calls: list[int] = []
    monkeypatch.setattr(os, "fsync", lambda fd: (calls.append(fd), real_fsync(fd))[1])
    assert manager.compact_if_needed()
    assert len(calls) >= 2
