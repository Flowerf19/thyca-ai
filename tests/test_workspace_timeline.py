"""Canonical workspace timeline: the ONLY execution store.

Covers the destination behavior after the hard cut: strict journal
(schema/seq/lock/permissions), canonical write -> project per window
with DM/group isolation, view_reset as the compaction basis, the
registry room-cursor delta contract (full history once, then only fresh
rows), and flush-before-compact over the canonical transcript.
"""
from __future__ import annotations

import json
import stat
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from thyca.app.chat_app import ChatApp
from thyca.bots.card import BotCard
from thyca.bots.timeline import (
    CanonicalSessionStore,
    TimelineConflict,
    TimelineError,
    WorkspaceTimeline,
)
from thyca.bots.workspace_store import WorkspaceStore
from thyca.config import default_config, load, save
from thyca.core.channel import WindowRef
from thyca.core.protocol import Message
from thyca.llm.llm_base import ChatReply
from thyca.sessions import SessionManager


def _chat(tmp_path: Path, connect, context_tokens: int | None = None) -> ChatApp:
    cfg = default_config()
    if context_tokens is not None:
        cfg = replace(cfg, limits=replace(cfg.limits, contextTokens=context_tokens))
    save(cfg, tmp_path / "config.json")
    return ChatApp(tmp_path, load(tmp_path / "config.json"), connect=connect)


class _SpyLLM:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def chat(self, messages, tools=None):
        self.prompts.append("\n".join(str(m.content or "") for m in messages))
        return ChatReply(content="ok", model="m",
                         usage={"prompt_tokens": 5, "completion_tokens": 1,
                                "total_tokens": 6})


def _window(window_id: str, kind: str, participants: tuple) -> WindowRef:
    return WindowRef(window_id=window_id, channel_id="webui", kind=kind,
                     participants=participants)


def _timeline(tmp_path: Path, bot_id: str = "coder") -> WorkspaceTimeline:
    return WorkspaceTimeline(tmp_path, bot_id)


def _store(tmp_path: Path, bot_id: str, window: WindowRef,
           session_id: str) -> CanonicalSessionStore:
    return CanonicalSessionStore(
        tmp_path / "bots" / bot_id / "sessions",
        timeline=_timeline(tmp_path, bot_id),
        window=window, session_id=session_id)


SESSION_ID = "2026-10-20T09-00-00_cafe"


def _row(text: str, **meta) -> Message:
    return Message(role="user", content=text,
                   meta=dict(meta) if meta else None)


# --- journal strictness ---------------------------------------------------

def test_journal_file_permissions(tmp_path: Path) -> None:
    _timeline(tmp_path)
    directory = tmp_path / "bots" / "coder" / "workspace"
    assert (directory.stat().st_mode & 0o777) == 0o700
    assert ((directory / "timeline.jsonl").stat().st_mode & 0o777) == 0o600


def test_read_only_timeline_does_not_recreate_deleted_file(
    tmp_path: Path, monkeypatch
) -> None:
    timeline = _timeline(tmp_path)
    path = timeline.path
    original_is_file = Path.is_file

    def delete_after_exists_check(candidate: Path) -> bool:
        result = original_is_file(candidate)
        if candidate == path and result:
            path.unlink()
        return result

    monkeypatch.setattr(Path, "is_file", delete_after_exists_check)
    with pytest.raises(FileNotFoundError):
        WorkspaceTimeline(tmp_path, "coder", create=False)
    assert not path.exists()


def _raw_event(window_id: str, seq: int, kind: str, event_id: str,
               payload: dict | None = None) -> str:
    return json.dumps({
        "v": 1, "seq": seq, "event_id": event_id, "kind": kind,
        "bot_id": "coder",
        "window": {"window_id": window_id, "channel_id": "webui",
                   "kind": "dm", "participants": ["coder"],
                   "visibility": "private"},
        "payload": payload or {"message": {"role": "user", "content": "hi"}},
        "ts": "2026-10-20T00:00:00Z",
    })


def test_journal_rejects_torn_tail(tmp_path: Path) -> None:
    _timeline(tmp_path)
    path = tmp_path / "bots" / "coder" / "workspace" / "timeline.jsonl"
    path.write_bytes(b'{"v": 1, "seq": 1, "event_id": "a"')  # no newline
    with pytest.raises(TimelineError):
        WorkspaceTimeline(tmp_path, "coder")


def test_journal_rejects_seq_gap_and_unknown_kind(tmp_path: Path) -> None:
    _timeline(tmp_path)
    path = tmp_path / "bots" / "coder" / "workspace" / "timeline.jsonl"
    path.write_text(
        _raw_event("w", 1, "message", "a") + "\n"
        + _raw_event("w", 3, "message", "b") + "\n", encoding="utf-8")
    with pytest.raises(TimelineError):
        WorkspaceTimeline(tmp_path, "coder")
    # Shadow-era kinds are unknown now: an old journal fails loud.
    path.write_text(
        _raw_event("w", 1, "bootstrap", "a") + "\n", encoding="utf-8")
    with pytest.raises(TimelineError):
        WorkspaceTimeline(tmp_path, "coder")
    path.write_text(
        _raw_event("w", 1, "parity", "a") + "\n", encoding="utf-8")
    with pytest.raises(TimelineError):
        WorkspaceTimeline(tmp_path, "coder")


def test_event_id_replay_idempotent_then_conflict(tmp_path: Path) -> None:
    timeline = _timeline(tmp_path)
    window = _window("w", "dm", ("coder",))
    first = timeline.append_message(window, _row("hi"), event_id="e1")
    again = timeline.append_message(window, _row("hi"), event_id="e1")
    assert again["seq"] == first["seq"]
    with pytest.raises(TimelineConflict):
        timeline.append_message(window, _row("other"), event_id="e1")


def test_messages_for_window_reads_original_rows_by_exact_window(
    tmp_path: Path,
) -> None:
    timeline = _timeline(tmp_path)
    dm = _window("dm1", "dm", ("coder",))
    group = _window("group1", "group", ("thyca", "coder"))
    other = _window("group2", "group", ("thyca", "coder"))
    same_id_other_window = _window("group1", "dm", ("coder",))
    dm_row = _row("private")
    first = _row("original one")
    second = _row("original two")
    other_row = _row("other room")
    same_id_row = _row("same id, different window")
    timeline.append_message(dm, dm_row, event_id="dm-row")
    timeline.append_message(group, first, event_id="group-one")
    timeline.append_message(group, second, event_id="group-two")
    timeline.append_message(other, other_row, event_id="other-row")
    timeline.append_message(
        same_id_other_window, same_id_row, event_id="same-id-other-window",
    )
    timeline.replace_view(group, [_row("compacted copy")], event_id="group-reset")

    assert timeline.messages_for_window(group) == [first, second]
    assert timeline.messages_for_window(dm) == [dm_row]
    assert timeline.messages_for_window(other) == [other_row]
    assert timeline.messages_for_window(same_id_other_window) == [same_id_row]


def test_messages_for_window_rejects_malformed_message_payload(
    tmp_path: Path,
) -> None:
    timeline = _timeline(tmp_path)
    timeline.path.write_text(
        _raw_event("group1", 1, "message", "bad-message", {
            "message": {"role": "not-a-role", "content": "invalid"},
        }) + "\n",
        encoding="utf-8",
    )
    reader = WorkspaceTimeline(tmp_path, "coder", create=False)

    with pytest.raises(TimelineError, match="message event payload invalid"):
        reader.messages_for_window(_window("group1", "dm", ("coder",)))


def test_messages_for_span_is_window_scoped_and_survives_view_reset(
    tmp_path: Path,
) -> None:
    timeline = _timeline(tmp_path)
    dm = _window("dm1", "dm", ("coder",))
    group = _window("group1", "group", ("thyca", "coder"))
    dm_assistant = Message(
        role="assistant", content="private", reasoning="DM sentinel",
        meta={"span_id": "shared-span"},
    )
    dm_tool = Message(
        role="tool", content="DM tool sentinel", tool_call_id="dm-call",
        meta={"span_id": "shared-span"},
    )
    group_assistant = Message(
        role="assistant", content="group", reasoning="group reasoning",
        meta={"span_id": "shared-span"},
    )
    group_tool = Message(
        role="tool", content="group result", tool_call_id="group-call",
        meta={"span_id": "shared-span"},
    )
    timeline.append_message(dm, dm_assistant, event_id="dm-assistant")
    timeline.append_message(dm, dm_tool, event_id="dm-tool")
    timeline.append_message(group, group_assistant, event_id="group-assistant")
    timeline.append_message(group, group_tool, event_id="group-tool")
    timeline.replace_view(
        group,
        [group_assistant, group_tool],
        event_id="group-reset",
    )

    assert timeline.messages_for_span(dm, "shared-span") == [
        dm_assistant, dm_tool
    ]
    group_rows = timeline.messages_for_span(group, "shared-span")
    assert group_rows == [group_assistant, group_tool]
    assert sum(row.role == "tool" for row in group_rows) == 1
    assert all(row.content != "DM tool sentinel" for row in group_rows)


# --- canonical write -> project -------------------------------------------

@pytest.mark.parametrize(
    ("initial", "basis"),
    [
        (("A", "B"), ()),
        (("B", "A", "B"), ("short basis",)),
    ],
)
def test_append_same_row_after_shorter_view_reset(
    tmp_path: Path, initial: tuple[str, ...], basis: tuple[str, ...]
) -> None:
    window = _window("w", "dm", ("coder",))
    store = _store(tmp_path, "coder", window, SESSION_ID)
    manager = SessionManager(store=store)
    manager.load(SESSION_ID)
    same_a = _row("A")
    initial_rows = [same_a if text == "A" else _row(text) for text in initial]
    for row in initial_rows:
        manager.append(row)
    manager.store.rewrite(
        SESSION_ID, store.path_for(SESSION_ID), [_row(text) for text in basis],
        title=None, title_source=None, naming_attempted=False,
    )

    manager.append(same_a)

    expected = [*basis, "A"]
    assert [m.content for m in store.load(SESSION_ID).messages] == expected
    events = [
        json.loads(line)
        for line in (tmp_path / "bots" / "coder" / "workspace" /
                     "timeline.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    reset_index = max(
        i for i, event in enumerate(events) if event["kind"] == "view_reset"
    )
    post_reset = [event for event in events[reset_index + 1:]
                  if event["kind"] == "message"]
    assert len(post_reset) == 1
    assert post_reset[0]["payload"]["message"]["content"] == "A"


def test_fresh_window_accumulates_without_basis(tmp_path: Path) -> None:
    window = _window("w", "dm", ("coder",))
    store = _store(tmp_path, "coder", window, SESSION_ID)
    manager = SessionManager(store=store)
    session = manager.load(SESSION_ID)
    assert session.messages == []
    manager.append(_row("one"))
    manager.append(_row("two"))
    assert [m.content for m in store.load(SESSION_ID).messages] == ["one", "two"]


def test_dm_group_projections_never_mix(tmp_path: Path) -> None:
    dm = _window("dm1", "dm", ("coder",))
    group = _window("grp1", "group", ("thyca", "coder"))
    dm_store = _store(tmp_path, "coder", dm, "2026-10-20T09-00-00_d00d")
    grp_store = _store(tmp_path, "coder", group, "2026-10-20T09-00-01_abcd")
    dm_manager = SessionManager(store=dm_store)
    dm_manager.load("2026-10-20T09-00-00_d00d")
    dm_manager.append(_row("PRIVATE-DM-LINE"))
    grp_manager = SessionManager(store=grp_store)
    grp_manager.load("2026-10-20T09-00-01_abcd")
    grp_manager.append(_row("PUBLIC-GROUP-LINE"))
    assert [m.content for m in dm_store.load("2026-10-20T09-00-00_d00d").messages] == [
        "PRIVATE-DM-LINE"]
    assert [m.content for m in grp_store.load("2026-10-20T09-00-01_abcd").messages] == [
        "PUBLIC-GROUP-LINE"]


def test_view_reset_is_compaction_basis_with_meta(tmp_path: Path) -> None:
    window = _window("w", "group", ("thyca", "coder"))
    store = _store(tmp_path, "coder", window, SESSION_ID)
    manager = SessionManager(store=store)
    manager.load(SESSION_ID)
    for text in ("m1", "m2", "m3"):
        manager.append(_row(text))
    manager.store.rewrite(
        SESSION_ID, store.path_for(SESSION_ID),
        [_row("m3-summary")],
        title="T", title_source="model", naming_attempted=True,
        bots=["thyca", "coder"], dm=None)
    projected = store.load(SESSION_ID)
    assert [m.content for m in projected.messages] == ["m3-summary"]
    assert projected.title == "T"
    assert projected.naming_attempted is True
    assert projected.bots == ["thyca", "coder"]
    # Writes after the reset replay on top of the new basis.
    manager.load(SESSION_ID)
    manager.append(_row("m4"))
    assert [m.content for m in store.load(SESSION_ID).messages] == [
        "m3-summary", "m4"]


def test_metadata_reset_serializes_with_concurrent_message_append(
    tmp_path: Path,
) -> None:
    window = _window("w", "dm", ("coder",))
    store = _store(tmp_path, "coder", window, SESSION_ID)
    other_store = _store(tmp_path, "coder", window, SESSION_ID)
    store.append(store.path_for(SESSION_ID), _row("first"))
    projected = store._projected
    snapshot_taken = threading.Event()
    continue_reset = threading.Event()
    append_started = threading.Event()
    append_done = threading.Event()
    errors: list[BaseException] = []

    def blocked_projection():
        session = projected()
        snapshot_taken.set()
        assert continue_reset.wait(5)
        return session

    store._projected = blocked_projection

    def update_title():
        try:
            store.append_meta(store.path_for(SESSION_ID), "Title")
        except BaseException as exc:
            errors.append(exc)

    def append_concurrently():
        append_started.set()
        try:
            other_store.append(other_store.path_for(SESSION_ID), _row("concurrent"))
        except BaseException as exc:
            errors.append(exc)
        finally:
            append_done.set()

    metadata_thread = threading.Thread(target=update_title)
    metadata_thread.start()
    assert snapshot_taken.wait(5)
    append_thread = threading.Thread(target=append_concurrently)
    append_thread.start()
    assert append_started.wait(5)
    assert not append_done.wait(0.1), "append must wait for the reset transaction"
    continue_reset.set()
    metadata_thread.join(timeout=5)
    append_thread.join(timeout=5)
    assert not metadata_thread.is_alive()
    assert not append_thread.is_alive()
    assert errors == []
    assert [m.content for m in other_store.load(SESSION_ID).messages] == [
        "first", "concurrent"]


def test_title_meta_ops_journal_view_reset(tmp_path: Path) -> None:
    window = _window("w", "dm", ("coder",))
    store = _store(tmp_path, "coder", window, SESSION_ID)
    manager = SessionManager(store=store)
    manager.load(SESSION_ID)
    manager.append(_row("hello"))
    assert store.append_title_if_missing(store.path_for(SESSION_ID), "Hi", "model")
    assert not store.append_title_if_missing(
        store.path_for(SESSION_ID), "Other", "model")
    projected = store.load(SESSION_ID)
    assert projected.title == "Hi"
    assert [m.content for m in projected.messages] == ["hello"]


# --- registry cursor delta contract ---------------------------------------

def test_cursor_full_once_then_delta(tmp_path: Path) -> None:
    llm = _SpyLLM()
    app = _chat(tmp_path, llm)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Codes.", persona=""))
        room = app.create(bots=["thyca", "coder"])["id"]
        # Seed public room history with a root turn before coder's first
        # turn; coder should receive the complete unread history once.
        app.turn(room, "root seed history")

        def deltas(since: int) -> list[str]:
            return [p for p in llm.prompts[since:] if "<room_history>" in p]

        seen = len(llm.prompts)
        app.turn(room, "@coder first hello")
        first_deltas = deltas(seen)
        assert len(first_deltas) == 1
        assert "root seed history" in first_deltas[0]
        cursor = app._runtime.room_cursor("coder", room)
        assert cursor is not None and cursor[0] >= 1

        seen = len(llm.prompts)
        app.turn(room, "@coder second hello")
        # Nothing new for the bot: no second delta on the wire, and no
        # synthetic rows were persisted anywhere.
        assert deltas(seen) == []
        internal = app._runtime.internal_session("coder", room)
        store = app._runtime._canonical_store(
            "coder", app._runtime._window_for_room(room),
            internal.id)
        assert all((m.meta or {}).get("kind") != "room_context"
                   for m in store.load(internal.id).messages)
    finally:
        app.shutdown()


# --- flush-before-compact over the canonical transcript --------------------

def test_flush_sees_intact_canonical_transcript(tmp_path: Path) -> None:
    llm = _SpyLLM()
    app = _chat(tmp_path, llm, context_tokens=5200)
    try:
        app._bots.create(BotCard(bot_id="coder", name="coder", role="Coder",
                                 description="Codes.", persona="PERSONA-CODER"))
        room = app.create(bots=["coder"])["id"]
        binding = app._runtime.internal_session("coder", room)
        window = app._runtime._window_for_room(room)
        store = app._runtime._canonical_store(
            "coder", window, binding.id)
        manager = SessionManager(store=store)
        manager.load(binding.id)
        for index in range(10):
            manager.append(Message(
                role="user", content=f"FLUSH-ME-{index} " + "u" * 500,
                ts=f"2026-08-01T10:{index:02d}:00Z"))
            manager.append(Message(
                role="assistant", content=f"answer-{index} " + "a" * 500,
                ts=f"2026-08-01T10:{index:02d}:30Z"))

        seen: list[bool] = []

        def fake_flush(bot_id, messages, turn_cfg):
            seen.append(any(
                (m.content or "").startswith("[compaction: ")
                for m in messages))
            assert any("FLUSH-ME-0" in (m.content or "") for m in messages), (
                "flush must see the intact pre-compaction transcript")

        app._runtime.memory_flush = fake_flush
        app.turn(room, "continue please")
        assert seen == [False], "flush runs once, before compaction"
        assert any(
            (m.content or "").startswith("[compaction: ")
            for m in store.load(binding.id).messages
        ), "canonical transcript compacted after the flush"
        # Purge is journal-scoped: the room session is untouched.
        assert WorkspaceStore(tmp_path).get_binding(
            "coder", room).internal_session_id == binding.id
    finally:
        app.shutdown()
