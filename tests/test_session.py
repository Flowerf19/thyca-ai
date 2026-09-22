"""Session service tests — TASK-303a-d verification."""
from __future__ import annotations

import asyncio
import json
import os
import stat
import threading
from pathlib import Path

import pytest

from thyca.config import LimitsCfg
from thyca.llm.llm_base import ChatReply
from thyca.core.protocol import Message, ToolCall
from thyca.sessions import (
    Session,
    SessionBusy,
    SessionCorrupt,
    SessionError,
    SessionManager,
    SessionNotFound,
    SessionStore,
    estimate_tokens,
)
from thyca.sessions.title import (
    USER_TITLE_MAX,
    accept_title,
    display_title,
    fallback_title,
    is_blank,
    retitle_missing,
    sanitize_title,
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
    # list_sessions must not set current
    with pytest.raises(SessionError, match="no current session"):
        _ = other.current
    other.load(good.id)
    other.list_sessions()
    assert other.current.id == good.id


def test_discard_empty_keeps_spoken_and_keep(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    blank = manager.create()
    kept = manager.create()
    spoken = manager.create()
    manager.append(msg("user", "alo"))
    removed = SessionManager(tmp_path).discard_empty(keep={kept.id})
    assert set(removed) == {blank.id}
    assert not blank.path.exists()
    assert kept.path.exists()
    assert spoken.path.exists()
    loaded = SessionManager(tmp_path).load(spoken.id)
    assert not is_blank(loaded)


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


def test_continue_skips_newer_invalid_filename(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    valid = manager.create()
    os.utime(valid.path, ns=(1, 1))
    invalid = tmp_path / "notes.jsonl"
    invalid.write_text("not a session\n", encoding="utf-8")
    os.utime(invalid, ns=(2, 2))

    assert manager.continue_last().id == valid.id


def test_continue_skips_newer_corrupt_session(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    valid = manager.create()
    manager.append(msg("user", "older valid"))
    os.utime(valid.path, ns=(1, 1))

    newer = SessionManager(tmp_path).create(make_current=False)
    newer.path.write_text("{bad\n", encoding="utf-8")
    os.utime(newer.path, ns=(2, 2))

    assert manager.continue_last().id == valid.id
    with pytest.raises(SessionCorrupt):
        manager.load(newer.id)


def test_invalid_utf8_is_corrupt_and_skipped_by_continue(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    valid = manager.create()
    manager.append(msg("user", "older valid"))
    os.utime(valid.path, ns=(1, 1))

    corrupt = SessionManager(tmp_path).create(make_current=False)
    corrupt.path.write_bytes(b'{"role":"user",\xff}\n')
    os.utime(corrupt.path, ns=(2, 2))

    assert manager.continue_last().id == valid.id
    with pytest.raises(SessionCorrupt, match="invalid UTF-8"):
        manager.load(corrupt.id)


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


def test_meta_title_roundtrip_and_last_wins(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    session = manager.create()
    manager.append(msg("user", "alo"))
    manager.append(msg("assistant", "hey"))
    assert manager.set_title("Cà phê với Hòa") == "Cà phê với Hòa"
    assert manager.set_title("  'Nhịp sáng'  ") == "Nhịp sáng"
    loaded = SessionManager(tmp_path).load(session.id)
    assert loaded.title == "Nhịp sáng"
    assert [(item.role, item.content) for item in loaded.messages] == [
        ("user", "alo"),
        ("assistant", "hey"),
    ]
    assert display_title(loaded) == "Nhịp sáng"


def test_meta_without_role_is_not_corrupt_unknown_type_is(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    session = manager.create()
    session.path.write_text(
        json.dumps({"type": "meta"}) + "\n" + json.dumps(msg("user", "hi").to_canonical_dict()) + "\n",
        encoding="utf-8",
    )
    loaded = manager.load(session.id)
    assert loaded.title is None
    assert [item.content for item in loaded.messages] == ["hi"]
    session.path.write_text(json.dumps({"type": "note", "title": "x"}) + "\n", encoding="utf-8")
    with pytest.raises(SessionCorrupt, match=r":1"):
        manager.load(session.id)


def test_compaction_preserves_title(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path, LimitsCfg(contextTokens=1000))
    session = manager.create()
    manager.set_title("Cà phê với Hòa")
    for i in range(8):
        manager.append(msg("user", "u" * 300 + str(i)))
        manager.append(msg("assistant", "a" * 300))
    assert manager.compact_if_needed()
    loaded = manager.load(session.id)
    assert loaded.title == "Cà phê với Hòa"
    assert loaded.messages[0].role == "system"


def test_fallback_and_sanitize_title(tmp_path: Path) -> None:
    assert fallback_title("2026-08-24T10-56-24_abcd") == "Sáng 24 thg 8"
    assert fallback_title("2026-08-22T16-51-00_2e20") == "Chiều 22 thg 8"
    assert fallback_title("2026-08-22T21-00-00_aaaa") == "Tối 22 thg 8"
    assert sanitize_title('"Cà phê với Hòa."') == "Cà phê với Hòa"
    assert sanitize_title("**Nhịp sáng**") == "Nhịp sáng"
    assert sanitize_title("x" * 32) == "x" * 32
    assert sanitize_title("x" * 60) == "x" * 31 + "…"
    assert sanitize_title("   ") is None
    spoken = msg("user", "alo")
    session = Session(
        "2026-08-24T10-56-24_abcd", tmp_path, [spoken, msg("assistant", "pong")]
    )
    assert accept_title("alo", session) is None
    assert accept_title("pong", session) is None
    assert accept_title("打招呼", session) is None
    assert accept_title("Cà phê với Hòa", session) == "Cà phê với Hòa"
    session.title = "打招呼"
    assert display_title(session) == "Sáng 24 thg 8"


def test_retitle_missing_skips_named_and_empty(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    blank = manager.create()
    untitled = manager.create()
    manager.append(msg("user", "alo"))
    manager.append(msg("assistant", "hey"))
    named = manager.create()
    manager.append(msg("user", "xin chào"))
    manager.set_title("Đã có tên")
    cjk = manager.create()
    manager.append(msg("user", "chào"))
    manager.set_title("打招呼")

    class LLM:
        async def chat(self, messages, tools=None):
            return ChatReply(content="Nhịp sáng")

    result = asyncio.run(retitle_missing(LLM().chat, SessionManager(tmp_path)))
    ids = {item[0].id: item[2] for item in result}
    assert ids == {untitled.id: "Nhịp sáng", cjk.id: "Nhịp sáng"}
    assert SessionManager(tmp_path).load(untitled.id).title == "Nhịp sáng"
    assert SessionManager(tmp_path).load(named.id).title == "Đã có tên"
    assert SessionManager(tmp_path).load(cjk.id).title == "Nhịp sáng"
    assert SessionManager(tmp_path).load(blank.id).title is None


def test_user_title_is_verbatim_and_survives_compaction(tmp_path: Path) -> None:
    """A title the user typed is the authority on its own notebook.

    The agent's naming policy refuses titles that echo the first message or
    contain CJK; neither is this policy's business when the user wrote it.
    """
    manager = SessionManager(tmp_path)
    session = manager.create()
    manager.append(msg("user", "Cà phê với Hòa"))
    manager.append(msg("assistant", "ok"))
    # Verbatim: the text the user typed is exactly what comes back, even if it
    # happens to repeat their first message.
    assert manager.set_title("Cà phê với Hòa", source="user") == "Cà phê với Hòa"
    # A whole phrase is allowed — the 32-char agent label is not the ceiling.
    long = "Kế hoạch tuần này cho dự án Thyca cùng Hòa"
    assert manager.set_title(long, source="user") == long
    assert len(long) > 32
    loaded = SessionManager(tmp_path).load(session.id)
    assert loaded.title == long
    assert loaded.title_source == "user"
    assert display_title(loaded) == long

    # Compaction rewrites the file; the user's title and its provenance stay.
    sized = SessionManager(tmp_path, LimitsCfg(contextTokens=1000))
    sized.load(session.id)
    for i in range(8):
        sized.append(msg("user", "u" * 300 + str(i)))
        sized.append(msg("assistant", "a" * 300))
    assert sized.compact_if_needed()
    after = SessionManager(tmp_path).load(session.id)
    assert after.title == long
    assert after.title_source == "user"


def test_user_title_caps_and_cleans_but_keeps_punctuation(tmp_path: Path) -> None:
    from thyca.sessions.title import sanitize_user_title

    assert sanitize_user_title("  Nhịp   sáng  ") == "Nhịp sáng"
    assert sanitize_user_title("Nhịp sáng.") == "Nhịp sáng."
    assert sanitize_user_title("打招呼") == "打招呼"
    assert sanitize_user_title("   ") is None
    assert sanitize_user_title("x" * 200) == "x" * (USER_TITLE_MAX - 1) + "…"

    manager = SessionManager(tmp_path)
    manager.create()
    assert manager.set_title("   ", source="user") is None
    assert manager.set_title("Sáng 15 thg 9", source="user") == "Sáng 15 thg 9"


def test_agent_title_still_goes_through_the_naming_policy(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    session = manager.create()
    manager.append(msg("user", "alo"))
    manager.append(msg("assistant", "hey"))
    # No source: the model's proposal keeps the old veting (32 chars, no CJK,
    # not a copy of what the user said).
    assert manager.set_title("打招呼") == "打招呼"
    loaded = SessionManager(tmp_path).load(session.id)
    assert loaded.title_source is None
    assert display_title(loaded) == fallback_title(session.id)
    assert manager.set_title("alo") == "alo"
    assert display_title(SessionManager(tmp_path).load(session.id)) == fallback_title(session.id)


def test_rename_targets_any_stored_session(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    first = manager.create()
    manager.append(msg("user", "alo"))
    second = manager.create()
    manager.append(msg("user", "chào"))

    # Any session, not only the one the manager currently holds.
    assert manager.rename(first.id, "  Chuyện   buổi sáng ") == "Chuyện buổi sáng"
    loaded = SessionManager(tmp_path).load(first.id)
    assert loaded.title == "Chuyện buổi sáng"
    assert loaded.title_source == "user"
    manager.rename(second.id, "Chuyện buổi chiều")
    assert SessionManager(tmp_path).load(second.id).title == "Chuyện buổi chiều"

    with pytest.raises(SessionNotFound):
        manager.rename("2026-01-01T00-00-00_beef", "x")
    with pytest.raises(ValueError):
        manager.rename(first.id, "   ")


def test_delete_removes_the_file_and_refuses_mid_turn(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    session = manager.create()
    manager.append(msg("user", "alo"))
    path = session.path
    assert path.exists()

    with pytest.raises(SessionBusy):
        manager.delete(session.id, keep={session.id})
    assert path.exists()
    assert manager.load(session.id) is not None

    manager.delete(session.id)
    assert not path.exists()
    with pytest.raises(SessionNotFound):
        manager.load(session.id)
    # Deleting again is a no-op, not an error: the row is already gone.
    manager.delete(session.id)


def test_naming_step_does_not_overwrite_the_user_title(tmp_path: Path) -> None:
    """A title typed while a turn runs wins over the agent's naming step."""
    manager = SessionManager(tmp_path)
    session = manager.create()
    manager.append(msg("user", "alo"))
    # The turn's snapshot predates the rename: another manager writes the meta
    # line to the same file while this one holds its stale view.
    other = SessionManager(tmp_path)
    other.rename(session.id, "Tên tôi tự đặt")
    assert manager.current.title is None
    manager.refresh_title()
    assert manager.current.title == "Tên tôi tự đặt"
    assert manager.current.title_source == "user"
    assert display_title(manager.current) == "Tên tôi tự đặt"


def test_read_title_walks_the_tail_and_last_meta_wins(tmp_path: Path) -> None:
    """The naming step reads only the title, so it must not parse the whole file."""
    manager = SessionManager(tmp_path)
    session = manager.create()
    # More than one tail chunk (>8 KiB) so the backwards walk has to loop.
    for i in range(300):
        manager.append(msg("user", "u" * 200 + str(i)))
        manager.append(msg("assistant", "a" * 200))
    manager.set_title("Tên cũ")
    for i in range(300):
        manager.append(msg("user", "v" * 200 + str(i)))
    manager.set_title("Tên mới", source="user")

    store = SessionStore(tmp_path)
    assert store.read_title(session.path) == ("Tên mới", "user")
    # Equivalent to a full scan's answer, at a fraction of the work.
    _messages, title, title_source = store.scan(session.path)
    assert (title, title_source) == ("Tên mới", "user")
    # No meta line at all → None (caller leaves the in-memory title alone).
    bare = SessionManager(tmp_path)
    empty = bare.create()
    assert store.read_title(empty.path) is None


def test_read_title_ignores_non_meta_lines_and_bad_json(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    path = tmp_path / "2026-01-01T00-00-00_abcd.jsonl"
    path.write_text(
        json.dumps({"role": "user", "ts": "t", "content": "có title trong chữ"}) + "\n"
        + json.dumps({"type": "meta", "title": "Đúng"}) + "\n",
        encoding="utf-8",
    )
    assert store.read_title(path) == ("Đúng", None)

    # A half-written line at the tail must not hide the meta line above it.
    path.write_text(
        json.dumps({"type": "meta", "title": "Vẫn đọc được"}) + "\n" + '{"role": "user", "co',
        encoding="utf-8",
    )
    assert store.read_title(path) == ("Vẫn đọc được", None)


def test_delete_gate_blocks_a_claim_inside_the_window(tmp_path: Path) -> None:
    """The keep-check and the unlink share the claim lock.

    Reproduces the ordering that used to be possible: ChatApp snapshotted the
    in-flight map, then unlinked. A turn claiming in between was not in `keep`,
    so its transcript was removed under it — and the next append re-created the
    file truncated, silently dropping the earlier history.
    """
    from thyca.sessions import SessionBusy
    from thyca.serve.turn_state import TurnState

    turns = TurnState()
    manager = SessionManager(tmp_path)
    session = manager.create()
    manager.append(msg("user", "câu hỏi"))
    path = session.path

    # A turn claims first: the delete must refuse.
    turns.claim(session.id)
    with pytest.raises(SessionBusy):
        turns.delete_unclaimed(manager, session.id)
    assert path.exists()
    turns.release(session.id)

    # No turn: the delete goes through.
    turns.delete_unclaimed(manager, session.id)
    assert not path.exists()


def test_turn_state_claim_is_exclusive_and_releasable(tmp_path: Path) -> None:
    from thyca.sessions import SessionBusy
    from thyca.serve.turn_state import TurnState

    turns = TurnState()
    assert turns.started_at("s") is None
    turns.claim("s")
    first = turns.started_at("s")
    assert first is not None
    assert turns.snapshot() == {"s": first}
    with pytest.raises(SessionBusy):
        turns.claim("s")
    # A snapshot is a copy: later claims do not leak into it.
    held = turns.snapshot()
    turns.claim("other")
    assert held == {"s": first}
    turns.release("s")
    assert turns.started_at("s") is None
    turns.claim("s")  # free again
    # Releasing an unknown session is a no-op, not an error.
    turns.release("never-claimed")


def test_turn_hub_replays_to_late_subscriber_and_closes_on_release() -> None:
    from thyca.serve.turn_state import TurnHub, TurnState

    turns = TurnState()
    hub = turns.claim("s")
    assert turns.hub("s") is hub
    hub.publish("a")
    early = hub.subscribe()
    assert early.get(timeout=1) == "a"
    hub.publish("b")
    assert early.get(timeout=1) == "b"
    late = hub.subscribe()
    assert late.get(timeout=1) == "a"
    assert late.get(timeout=1) == "b"
    hub.drop(early)
    hub.publish("c")
    assert late.get(timeout=1) == "c"
    assert early.empty()
    turns.release("s")
    assert late.get(timeout=1) is TurnHub.SENTINEL
    assert turns.hub("s") is None
    assert turns.started_at("s") is None


def test_claim_cannot_slip_between_the_check_and_the_unlink(tmp_path: Path) -> None:
    """A concurrent claim waits for the delete, so it cannot land in the window."""
    import threading
    import time

    from thyca.sessions import SessionNotFound
    from thyca.serve.turn_state import TurnState

    turns = TurnState()
    manager = SessionManager(tmp_path)
    session = manager.create()
    manager.append(msg("user", "câu hỏi"))
    path = session.path
    assert path.exists()

    inside = threading.Event()
    release = threading.Event()
    original_delete = SessionStore.delete

    def paused_delete(self, session_id):
        inside.set()
        release.wait(timeout=5)
        return original_delete(self, session_id)

    claimed = threading.Event()
    failures: list[Exception] = []

    def claim_from_another_thread():
        try:
            turns.claim(session.id)
        except Exception as exc:  # pragma: no cover - nothing should raise
            failures.append(exc)
        finally:
            claimed.set()

    SessionStore.delete = paused_delete
    try:
        deleter = threading.Thread(
            target=lambda: turns.delete_unclaimed(manager, session.id), daemon=True
        )
        deleter.start()
        assert inside.wait(timeout=5), "delete never reached the unlink step"

        claimer = threading.Thread(target=claim_from_another_thread, daemon=True)
        claimer.start()
        # The whole point: while the delete holds the claim, no other thread can
        # take the session — the claim is what used to slip through.
        time.sleep(0.2)
        assert not claimed.is_set(), "claim got through the delete window"
        assert path.exists()

        release.set()
        deleter.join(timeout=5)
        claimer.join(timeout=5)
    finally:
        SessionStore.delete = original_delete
        release.set()

    assert not failures
    assert claimed.is_set()
    # The claim landed after the unlink, so the turn now finds a session that is
    # gone (a 404 the client can act on) instead of writing into a removed path.
    with pytest.raises(SessionNotFound):
        manager.load(session.id)
    turns.release(session.id)


def test_mark_turn_error_stamps_last_user_and_persists(tmp_path: Path) -> None:
    mgr = SessionManager(tmp_path / "sessions")
    session = mgr.create()
    mgr.append(msg("user", "hi"))
    assert mgr.current.messages[0].meta is None
    assert mgr.mark_turn_error("llm_error", "provider HTTP 404: gone") is True
    marked = mgr.current.messages[0].meta
    assert marked == {"error": {"code": "llm_error", "message": "provider HTTP 404: gone"}}
    reloaded = SessionManager(tmp_path / "sessions").load(session.id)
    assert reloaded.messages[0].meta == marked


def test_mark_turn_error_no_user_returns_false(tmp_path: Path) -> None:
    mgr = SessionManager(tmp_path / "sessions")
    mgr.create()
    assert mgr.mark_turn_error("llm_error", "x") is False


def test_truncate_to_last_user_strips_error_marker(tmp_path: Path) -> None:
    mgr = SessionManager(tmp_path / "sessions")
    session = mgr.create()
    mgr.append(msg("user", "hi", meta={"error": {"code": "llm_error", "message": "boom"}}))
    mgr.append(msg("assistant", "partial"))
    assert mgr.truncate_to_last_user() is True
    assert mgr.current.messages[-1].meta is None
    reloaded = SessionManager(tmp_path / "sessions").load(session.id)
    assert reloaded.messages[-1].meta is None


def test_truncate_tail_user_with_marker_rewrites(tmp_path: Path) -> None:
    mgr = SessionManager(tmp_path / "sessions")
    session = mgr.create()
    mgr.append(msg("user", "hi", meta={"error": {"code": "llm_error", "message": "boom"}}))
    assert mgr.truncate_to_last_user() is True
    assert mgr.current.messages[-1].meta is None
    reloaded = SessionManager(tmp_path / "sessions").load(session.id)
    assert reloaded.messages[-1].meta is None


def test_message_reasoning_details_canonical_roundtrip(tmp_path: Path) -> None:
    details = [{"type": "reasoning.encrypted", "data": "blob"}]
    msg = Message(
        role="assistant",
        content="hi",
        ts="2026-01-01T00:00:00Z",
        reasoning_details=details,
    )
    assert Message.from_dict(msg.to_canonical_dict()).reasoning_details == details
    # old lines without the key load as None
    bare = Message.from_dict(
        {"role": "user", "content": "x", "ts": "2026-01-01T00:00:00Z"}
    )
    assert bare.reasoning_details is None


def test_message_reasoning_details_rejects_bad_shape() -> None:
    with pytest.raises(ValueError, match="reasoning_details"):
        Message(role="assistant", content="x", reasoning_details="nope")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="reasoning_details"):
        Message.from_dict(
            {
                "role": "assistant",
                "content": "x",
                "ts": "2026-01-01T00:00:00Z",
                "reasoning_details": [{"type": "reasoning.text", "text": "t"}, 42],
            }
        )
