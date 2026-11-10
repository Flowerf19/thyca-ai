"""ConversationRuntime service tests — TASK-303a-d verification."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from thyca.agents.assemble import Assemble
from thyca.agents.stage import Stage
from thyca.configs import LimitsCfg
from thyca.llms.llm_base import ChatReply
from thyca.core.protocol import Message, ToolCall
from thyca.serve import routes as serve_routes
from thyca.serve import trace as serve_trace
from thyca.serve.conversations_api import _conv_error
from thyca.conversations.runtime import (
    ConversationRuntime,
    ConversationBusy,
    ConversationCorrupt,
    ConversationError,
    ConversationManager,
    ConversationNotFound,
    ConversationStore,
    estimate_tokens,
)
from thyca.conversations.runtime.models import is_blank
from thyca.conversations.runtime.store import CONVERSATION_ID_PATTERN
from thyca.conversations.runtime.wire import (
    delete_error,
    message_dict,
    conversation_http_error,
    tool_call_dict,
)
from thyca.skills.skill_event import skill_name_for_call


def msg(role: str, content: str | None = "x", **kw: object) -> Message:
    return Message(role=role, content=content, ts="2026-01-01T00:00:00Z", **kw)


# Build a runtime-store manager over a fresh initialized root.
def _runtime_manager(root: Path, limits: LimitsCfg | None = None) -> ConversationManager:
    from thyca.conversations.store.runtime import ensure_runtime_store

    conversations_dir = Path(root)
    store_root = conversations_dir.parent if conversations_dir.name == "sessions" else conversations_dir
    ensure_runtime_store(store_root)
    return ConversationManager(conversations_dir, limits)


# Build a canonical-timeline manager holding one execution conversation.
def _canonical_manager(root: Path, limits: LimitsCfg | None = None) -> ConversationManager:
    from thyca.bots.timeline import CanonicalConversationStore, WorkspaceTimeline
    from thyca.core.channel import WindowRef

    timeline = WorkspaceTimeline(root, "thyca")
    window = WindowRef(
        window_id="thyca", channel_id="webui", kind="dm", participants=("thyca",))
    conversation_id = "2026-01-01T00-00-00_ab12"
    store = CanonicalConversationStore(
        root / "bots" / "thyca" / "sessions",
        timeline=timeline, window=window, conversation_id=conversation_id)
    manager = ConversationManager(store=store, limits=limits)
    manager.load(conversation_id)
    return manager


def test_roundtrip_three_turns_and_permissions(tmp_path: Path) -> None:
    manager = _canonical_manager(tmp_path)
    conversation = manager.current
    manager.append(msg("user", "hello", meta={"x": "y"}))
    manager.append(msg("assistant", "answer"), actor="thyca")
    call = ToolCall("call-1", "echo", {"x": 1})
    manager.append(msg("user", "tool please"))
    manager.append(msg("assistant", None, tool_calls=[call]), actor="thyca")
    manager.append(msg("tool", "done", tool_call_id="call-1"))
    manager.append(msg("assistant", "final"), actor="thyca")
    assert len(conversation.messages) == 6
    loaded = _canonical_manager(tmp_path).load(conversation.conversation_id)
    assert len(loaded.messages) == 6
    assert loaded.messages[0].meta == {"x": "y"}
    assert loaded.messages[3].content is None
    assert loaded.messages[3].tool_calls is not None



def test_list_conversations_skips_corrupt_does_not_set_current(tmp_path: Path) -> None:
    import sqlite3

    manager = _runtime_manager(tmp_path)
    good = manager.create()
    manager.append(msg("user", "ok"))
    bad = _runtime_manager(tmp_path).create(make_current=False)
    conn = sqlite3.connect(str(tmp_path / "conversations.db"))
    try:
        conn.execute(
            "UPDATE conversations SET metadata='{corrupt' WHERE id=?", (bad.conversation_id,))
        conn.commit()
    finally:
        conn.close()
    other = ConversationManager(tmp_path)
    listed = other.list_conversations()
    assert [item.conversation_id for item in listed] == [good.conversation_id]
    # list_conversations must not set current
    with pytest.raises(ConversationError, match="no current conversation"):
        _ = other.current
    other.load(good.conversation_id)
    other.list_conversations()
    assert other.current.conversation_id == good.conversation_id


def test_discard_empty_keeps_spoken_and_keep(tmp_path: Path) -> None:
    manager = _runtime_manager(tmp_path)
    blank = manager.create()
    kept = manager.create()
    spoken = manager.create()
    manager.append(msg("user", "alo"))
    removed = _runtime_manager(tmp_path).discard_empty(keep={kept.conversation_id})
    assert set(removed) == {blank.conversation_id}
    with pytest.raises(ConversationNotFound):
        _runtime_manager(tmp_path).load(blank.conversation_id)
    assert _runtime_manager(tmp_path).load(kept.conversation_id) is not None
    loaded = _runtime_manager(tmp_path).load(spoken.conversation_id)
    assert not is_blank(loaded)








def test_concurrent_append(tmp_path: Path) -> None:
    manager = _runtime_manager(tmp_path)
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
    manager = _canonical_manager(tmp_path, LimitsCfg(contextTokens=1000))
    conversation = manager.current
    for i in range(8):
        manager.append(msg("user", "u" * 300 + str(i)))
        manager.append(msg("assistant", "a" * 300), actor="thyca")
    assert estimate_tokens(msg("user", "same")) == estimate_tokens(msg("user", "same"))
    assert manager.compact_if_needed()
    loaded = _canonical_manager(tmp_path).load(conversation.conversation_id)
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


def test_compact_overhead_triggers_when_transcript_alone_fits(tmp_path: Path) -> None:
    manager = _runtime_manager(tmp_path, LimitsCfg(contextTokens=2000))
    manager.create()
    for i in range(4):
        manager.append(msg("user", "u" * 300 + str(i)))
        manager.append(msg("assistant", "a" * 300), actor="thyca")
    transcript = sum(estimate_tokens(m) for m in manager.current.messages)
    assert transcript <= 2000
    assert not manager.compact_if_needed()
    assert manager.compact_if_needed(pending_user_tokens=2000 - transcript + 1)


def test_compact_tail_budget_subtracts_overhead_and_marker() -> None:
    from thyca.conversations.runtime.compaction import ConversationCompactor, _marker_reserve_tokens

    compactor = ConversationCompactor()
    messages: list[Message] = []
    for i in range(8):
        messages.append(msg("user", "u" * 300 + str(i)))
        messages.append(msg("assistant", "a" * 300))
    plain = compactor.compact(messages, 1200)
    assert plain is not None
    overhead = 100 + 100 + 100
    lean = compactor.compact(
        messages, 1200, hot_tokens=100, tools_tokens=100, pending_user_tokens=100
    )
    assert lean is not None
    assert len(lean) <= len(plain)
    tail_tokens = sum(estimate_tokens(m) for m in lean[1:])
    expected = int((1200 - overhead - _marker_reserve_tokens()) * 0.6)
    assert tail_tokens <= expected or len(_user_assistant_turns(lean[1:])) == 1


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
    manager = _runtime_manager(tmp_path, LimitsCfg(contextTokens=1000))
    conversation = manager.create()
    manager.append(msg("user", "u" * 8000))
    manager.append(msg("assistant", "a" * 8000), actor="thyca")
    # Nothing can be omitted (the lone turn must survive), so no junk
    # 0/0 marker is minted and the file is left untouched.
    assert not manager.compact_if_needed()
    roles = [item.role for item in conversation.messages]
    assert roles == ["user", "assistant"]
    loaded = manager.load(conversation.conversation_id)
    assert [item.role for item in loaded.messages] == ["user", "assistant"]


def test_context_usage_includes_overhead() -> None:
    from thyca.conversations.runtime import context_usage

    messages = [msg("user", "hello"), msg("assistant", "hi there")]
    bare = context_usage(messages, 1000)
    full = context_usage(messages, 1000, hot_tokens=40, tools_tokens=60)
    assert full["transcript_tokens"] == bare["transcript_tokens"]
    assert full["overhead_tokens"] == 100
    assert full["estimated_tokens"] == bare["estimated_tokens"] + 100
    assert full["context_tokens"] == 1000
    assert full["ratio"] == full["estimated_tokens"] / 1000
    assert full["calibration"] == 1.0
    scaled = context_usage(messages, 1000, calibration=2.0)
    assert scaled["estimated_tokens"] == bare["estimated_tokens"] * 2
    assert scaled["transcript_tokens"] == bare["transcript_tokens"]
    assert scaled["calibration"] == 2.0


def _pair(model: str, actual: int, estimated: int) -> Message:
    return Message(
        role="assistant",
        content="x",
        ts=_TS,
        meta={
            "kind": "llm",
            "model": model,
            "usage": {"prompt_tokens": actual},
            "estimate_tokens": estimated,
        },
    )


def test_calibration_factor_median_clamp_and_model_filter() -> None:
    from thyca.conversations.runtime import calibration_factor

    assert calibration_factor([], model="m") == 1.0
    pairs =([
        _pair("m", 120, 100),
        _pair("m", 130, 100),
        _pair("m", 500, 100),
        _pair("other", 200, 100),
    ])
    # Median of in-band ratios for m (1.2, 1.3); the 5.0 outlier drops.
    assert calibration_factor(pairs, model="m") == 1.25
    assert calibration_factor(pairs, model="other") == 2.0
    assert calibration_factor(pairs, model="absent") == 1.0
    # A wildly low provider count is an outlier, not a factor near zero.
    assert calibration_factor([_pair("m", 5, 100)], model="m") == 1.0


def test_calibration_makes_auto_compact_fire_earlier(tmp_path: Path) -> None:
    manager = _runtime_manager(tmp_path, LimitsCfg(contextTokens=1000))
    manager.create()
    for i in range(3):
        manager.append(msg("user", "u" * 300 + str(i)))
        manager.append(msg("assistant", "a" * 300), actor="thyca")
    assert not manager.compact_if_needed()
    assert manager.compact_if_needed(calibration=2.5)


def test_manual_compact_forces_below_threshold(tmp_path: Path) -> None:
    manager = _runtime_manager(tmp_path, LimitsCfg(contextTokens=1000))
    conversation = manager.create()
    for i in range(4):
        manager.append(msg("user", "u" * 300 + str(i)))
        manager.append(msg("assistant", "a" * 300), actor="thyca")
    # Under the 95% auto threshold, over the 60% tail budget: auto is a
    # no-op, manual must still compact with the same turn-safe tail.
    assert not manager.compact_if_needed()
    result = manager.compact_manual()
    assert result["compacted"] is True
    assert result["messages_before"] == 8
    assert result["messages_after"] < 8
    loaded = manager.load(conversation.conversation_id)
    assert len(loaded.messages) == result["messages_after"]
    assert loaded.messages[0].role == "system"
    assert (loaded.messages[0].content or "").startswith("[compaction: omitted ")


def test_manual_compact_noop_when_already_fits(tmp_path: Path) -> None:
    manager = _runtime_manager(tmp_path, LimitsCfg(contextTokens=1000))
    conversation = manager.create()
    manager.append(msg("user", "hi"))
    manager.append(msg("assistant", "hello"), actor="thyca")
    result = manager.compact_manual()
    assert result == {"compacted": False, "messages_before": 2, "messages_after": 2}
    loaded = manager.load(conversation.conversation_id)
    assert [item.role for item in loaded.messages] == ["user", "assistant"]


def test_manual_compact_keeps_incomplete_tail(tmp_path: Path) -> None:
    manager = _runtime_manager(tmp_path, LimitsCfg(contextTokens=1000))
    manager.create()
    for i in range(4):
        manager.append(msg("user", "u" * 300 + str(i)))
        manager.append(msg("assistant", "a" * 300), actor="thyca")
    manager.append(msg("user", "pending-question"))
    result = manager.compact_manual()
    assert result["compacted"] is True
    assert manager.current.messages[-1].content == "pending-question"




def test_compaction_keeps_complete_tool_round(tmp_path: Path) -> None:
    manager = _canonical_manager(tmp_path, LimitsCfg(contextTokens=1000))
    conversation = manager.current
    for i in range(6):
        manager.append(msg("user", "u" * 200 + str(i)))
        manager.append(msg("assistant", None, tool_calls=[ToolCall(f"c{i}", "echo", {"i": i})]), actor="thyca")
        manager.append(msg("tool", "r" * 200, tool_call_id=f"c{i}"))
        manager.append(msg("assistant", "a" * 200), actor="thyca")
    assert manager.compact_if_needed()
    tail = conversation.messages[1:]
    pending: set[str] = set()
    for item in tail:
        if item.role == "assistant" and item.tool_calls:
            pending.update(call.id for call in item.tool_calls)
        elif item.role == "tool":
            assert item.tool_call_id in pending
            pending.discard(item.tool_call_id or "")
    assert not pending


def test_concurrent_append_and_compact(tmp_path: Path) -> None:
    manager = _runtime_manager(tmp_path, LimitsCfg(contextTokens=1000))
    manager.create()
    errors: list[Exception] = []

    def work(i: int) -> None:
        try:
            manager.append(msg("user", "x" * 200 + str(i)))
            manager.append(msg("assistant", "y" * 200), actor="thyca")
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


def test_compaction_keeps_messages_after_title_removal(tmp_path: Path) -> None:
    manager = _runtime_manager(tmp_path, LimitsCfg(contextTokens=1000))
    conversation = manager.create()
    for i in range(8):
        manager.append(msg("user", "u" * 300 + str(i)))
        manager.append(msg("assistant", "a" * 300), actor="thyca")
    assert manager.compact_if_needed()
    loaded = manager.load(conversation.conversation_id)
    assert loaded.messages[0].role == "system"
    assert [(item.role, item.content) for item in _runtime_manager(
        tmp_path).load(conversation.conversation_id).messages[:2]] == [
        ("system", loaded.messages[0].content),
        ("user", loaded.messages[1].content),
    ]


def test_delete_removes_the_file_and_refuses_mid_turn(tmp_path: Path) -> None:
    manager = _runtime_manager(tmp_path)
    conversation = manager.create()
    manager.append(msg("user", "alo"))

    with pytest.raises(ConversationBusy):
        manager.delete(conversation.conversation_id, keep={conversation.conversation_id})
    assert manager.load(conversation.conversation_id) is not None

    manager.delete(conversation.conversation_id)
    with pytest.raises(ConversationNotFound):
        manager.load(conversation.conversation_id)
    # Deleting again is a no-op, not an error: the row is already gone.
    manager.delete(conversation.conversation_id)



def test_delete_gate_blocks_a_claim_inside_the_window(tmp_path: Path) -> None:
    """The keep-check and the unlink share the claim lock.

    Reproduces the ordering that used to be possible: ChatApp snapshotted the
    in-flight map, then unlinked. A turn claiming in between was not in `keep`,
    so its transcript was removed under it — and the next append re-created the
    file truncated, silently dropping the earlier history.
    """
    from thyca.conversations.runtime import ConversationBusy
    from thyca.serve.turn_state import TurnState

    turns = TurnState()
    manager = _runtime_manager(tmp_path)
    conversation = manager.create()
    manager.append(msg("user", "câu hỏi"))

    # A turn claims first: the delete must refuse.
    turns.claim(conversation.conversation_id)
    with pytest.raises(ConversationBusy):
        turns.delete_unclaimed(manager, conversation.conversation_id)
    assert _runtime_manager(tmp_path).load(conversation.conversation_id) is not None
    turns.release(conversation.conversation_id)

    # No turn: the delete goes through.
    turns.delete_unclaimed(manager, conversation.conversation_id)
    with pytest.raises(ConversationNotFound):
        _runtime_manager(tmp_path).load(conversation.conversation_id)


def test_turn_state_claim_is_exclusive_and_releasable(tmp_path: Path) -> None:
    from thyca.conversations.runtime import ConversationBusy
    from thyca.serve.turn_state import TurnState

    turns = TurnState()
    assert turns.started_at("s") is None
    turns.claim("s")
    first = turns.started_at("s")
    assert first is not None
    assert turns.snapshot() == {"s": first}
    with pytest.raises(ConversationBusy):
        turns.claim("s")
    # A snapshot is a copy: later claims do not leak into it.
    held = turns.snapshot()
    turns.claim("other")
    assert held == {"s": first}
    turns.release("s")
    assert turns.started_at("s") is None
    turns.claim("s")  # free again
    # Releasing an unknown conversation is a no-op, not an error.
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

    from thyca.conversations.runtime import ConversationNotFound
    from thyca.serve.turn_state import TurnState

    turns = TurnState()
    manager = _runtime_manager(tmp_path)
    conversation = manager.create()
    manager.append(msg("user", "câu hỏi"))
    assert _runtime_manager(tmp_path).load(conversation.conversation_id) is not None

    inside = threading.Event()
    release = threading.Event()
    original_delete = ConversationStore.delete

    def paused_delete(self, conversation_id):
        inside.set()
        release.wait(timeout=5)
        return original_delete(self, conversation_id)

    claimed = threading.Event()
    failures: list[Exception] = []

    def claim_from_another_thread():
        try:
            turns.claim(conversation.conversation_id)
        except Exception as exc:  # pragma: no cover - nothing should raise
            failures.append(exc)
        finally:
            claimed.set()

    ConversationStore.delete = paused_delete
    try:
        deleter = threading.Thread(
            target=lambda: turns.delete_unclaimed(manager, conversation.conversation_id), daemon=True
        )
        deleter.start()
        assert inside.wait(timeout=5), "delete never reached the unlink step"

        claimer = threading.Thread(target=claim_from_another_thread, daemon=True)
        claimer.start()
        # The whole point: while the delete holds the claim, no other thread can
        # take the conversation — the claim is what used to slip through.
        time.sleep(0.2)
        assert not claimed.is_set(), "claim got through the delete window"
        assert _runtime_manager(tmp_path).load(conversation.conversation_id) is not None

        release.set()
        deleter.join(timeout=5)
        claimer.join(timeout=5)
    finally:
        ConversationStore.delete = original_delete
        release.set()

    assert not failures
    assert claimed.is_set()
    # The claim landed after the unlink, so the turn now finds a conversation that is
    # gone (a 404 the client can act on) instead of writing into a removed path.
    with pytest.raises(ConversationNotFound):
        manager.load(conversation.conversation_id)
    turns.release(conversation.conversation_id)


def test_mark_turn_error_stamps_last_user_and_persists(tmp_path: Path) -> None:
    mgr = _canonical_manager(tmp_path)
    conversation = mgr.current
    mgr.append(msg("user", "hi"))
    assert mgr.current.messages[0].meta is None
    assert mgr.mark_turn_error("llm_error", "provider HTTP 404: gone") is True
    marked = mgr.current.messages[0].meta
    assert marked == {"error": {"code": "llm_error", "message": "provider HTTP 404: gone"}}
    reloaded = _canonical_manager(tmp_path).load(conversation.conversation_id)
    assert reloaded.messages[0].meta == marked


def test_mark_turn_error_no_user_returns_false(tmp_path: Path) -> None:
    mgr = _runtime_manager(tmp_path / "sessions")
    mgr.create()
    assert mgr.mark_turn_error("llm_error", "x") is False


def test_truncate_to_last_user_strips_error_marker(tmp_path: Path) -> None:
    mgr = _runtime_manager(tmp_path / "sessions")
    conversation = mgr.create()
    mgr.append(msg("user", "hi", meta={"error": {"code": "llm_error", "message": "boom"}}))
    mgr.append(msg("assistant", "partial"), actor="thyca")
    assert mgr.truncate_to_last_user() is True
    assert mgr.current.messages[-1].meta is None
    reloaded = _runtime_manager(tmp_path / "sessions").load(conversation.conversation_id)
    assert reloaded.messages[-1].meta is None


def test_truncate_tail_user_with_marker_rewrites(tmp_path: Path) -> None:
    mgr = _canonical_manager(tmp_path)
    conversation = mgr.current
    mgr.append(msg("user", "hi", meta={"error": {"code": "llm_error", "message": "boom"}}))
    assert mgr.truncate_to_last_user() is True
    assert mgr.current.messages[-1].meta is None
    reloaded = _canonical_manager(tmp_path).load(conversation.conversation_id)
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


# --- X1: conversations-owned wire canonicalization ---

_TS = "2026-09-01T10:00:00Z"
_SID = "2026-09-01T10-00-00_ab12"


def test_x1_naming_kind_is_not_a_sidecar_anymore(tmp_path: Path) -> None:
    naming = Message(role="assistant", content=None, ts=_TS, meta={"kind": "naming"})
    user = Message(role="user", content="hi", ts=_TS)
    stage = Stage(messages=[user, naming])
    Assemble().assemble(stage, "next")
    assert any(m.meta == {"kind": "naming"} for m in stage.messages)
    assert [m.content for m in stage.messages if m.role == "user"] == ["hi", "next"]


def test_compaction_sidecar_is_transcript_only(tmp_path: Path) -> None:
    sidecar = Message(role="assistant", content=None, ts=_TS, meta={"kind": "compaction"})
    user = Message(role="user", content="hi", ts=_TS)
    stage = Stage(messages=[user, sidecar])
    Assemble().assemble(stage, "next")
    assert all((m.meta or {}).get("kind") != "compaction" for m in stage.messages)
    conversation = ConversationRuntime("s", tmp_path / "s.jsonl", [
        Message(role="user", content="q", ts=_TS),
        Message(role="assistant", content="a", ts=_TS, meta={"kind": "llm"}),
        sidecar,
    ])
    (turn,) = serve_trace.turns_from_conversation(conversation)
    assert turn.status == "completed"


def _old_chat_call(call: ToolCall, root) -> dict:
    entry = {"id": call.id, "name": call.name}
    skill = skill_name_for_call(call, root)
    if skill is not None:
        entry["skill"] = skill
    return entry


def _old_chat_message(message: Message, root) -> dict:
    payload: dict = {"role": message.role, "content": message.content, "ts": message.ts}
    if message.tool_calls:
        payload["tool_calls"] = [_old_chat_call(c, root) for c in message.tool_calls]
    if message.tool_call_id is not None:
        payload["tool_call_id"] = message.tool_call_id
    if message.meta is not None:
        payload["meta"] = dict(message.meta)
    return payload


def _old_trace_call(call: ToolCall, root) -> dict:
    entry: dict = {"id": call.id, "name": call.name, "arguments": call.arguments}
    if call.parse_error:
        entry["parse_error"] = call.parse_error
    skill = skill_name_for_call(call, root)
    if skill is not None:
        entry["skill"] = skill
    return entry


def _old_trace_message(message: Message, root) -> dict:
    payload = {
        "role": message.role,
        "content": message.content,
        "ts": message.ts,
        "tool_calls": [_old_trace_call(c, root) for c in (message.tool_calls or [])],
        "tool_call_id": message.tool_call_id,
        "meta": message.meta,
    }
    if message.reasoning:
        payload["reasoning"] = message.reasoning
    if message.reasoning_details:
        payload["reasoning_details"] = message.reasoning_details
    return payload


def _wire_matrix() -> list[Message]:
    return [
        Message(role="user", content="hi", ts=_TS),
        Message(role="assistant", content=None, ts=_TS, tool_calls=[
            ToolCall(id="c1", name="bash", arguments={"command": "ls"}),
            ToolCall(id="c2", name="read", arguments={}, parse_error="bad args"),
        ], meta={"kind": "llm", "round": 1}),
        Message(role="assistant", content="done", ts=_TS, meta={"kind": "llm"}),
        Message(role="tool", content="out", ts=_TS, tool_call_id="c1", meta={"is_error": False}),
        Message(
            role="assistant", content="r", ts=_TS, reasoning="thinking...",
            reasoning_details=[{"type": "text", "text": "detail"}], meta=None,
        ),
        Message(role="assistant", content=None, ts=_TS, meta={"kind": "naming"}),
    ]


def test_x1_call_and_message_dicts_match_old_shapes(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    for message in _wire_matrix():
        assert message_dict(message, root) == _old_chat_message(message, root)
        assert message_dict(message, root, include_args=True) == _old_trace_message(message, root)
        for call in message.tool_calls or []:
            assert tool_call_dict(call, root) == _old_chat_call(call, root)
            assert tool_call_dict(call, root, include_args=True) == _old_trace_call(call, root)


def test_x1_conversation_id_grammar_is_single_pattern(tmp_path: Path) -> None:
    # Hard-cut grammar: public doors are dm(bot)/group ids only, and the
    # legacy /api/conversations routes are gone. Canonical per-bot timeline
    # ids keep the timestamp shape as a separate internal concept.
    from thyca.core.conversation import ConversationRef

    assert CONVERSATION_ID_PATTERN == r"(?:group-[0-9a-f]{32}|[a-z0-9]+(?:-[a-z0-9]+)*)"
    group_id = "group-" + "a" * 32
    assert ConversationRef.dm("coder").id == "coder"
    assert ConversationRef.group(group_id).id == group_id
    with pytest.raises(ValueError):
        ConversationRef.group(_SID)
    with pytest.raises(ValueError):
        ConversationRef.dm(_SID)
    store = ConversationStore(tmp_path / "sessions")
    # Public door ids bind to a path; canonical timestamp ids do not.
    assert store.path_for("coder").name == "coder.jsonl"
    assert store.path_for(group_id).name == f"{group_id}.jsonl"
    with pytest.raises(ConversationNotFound):
        store.path_for(_SID)
    # New routes address doors explicitly; no conversation route remains.
    assert not hasattr(serve_routes, "_SESSION_RE")
    assert serve_routes._BOT_DM_RE.fullmatch("/api/bots/coder/dm") is not None
    assert serve_routes._GROUP_RE.fullmatch(f"/api/groups/{group_id}") is not None
    assert serve_routes._DM_TURN_RE.fullmatch("/api/bots/coder/dm/turn") is not None
    assert serve_routes._GROUP_TURN_RE.fullmatch(f"/api/groups/{group_id}/turn") is not None
    detail = serve_routes._TRACE_GROUP_DETAIL_RE.fullmatch(f"/api/traces/groups/{group_id}/3")
    assert detail is not None and detail.group(2) == "3"
    dm_detail = serve_routes._TRACE_DM_DETAIL_RE.fullmatch("/api/traces/dm/coder/3")
    assert dm_detail is not None and dm_detail.group(2) == "3"
    bad = [
        "2026-09-01T10-00-00_ab1g",  # non-hex
        "2026-09-01T10-00-00_ab123",  # too long
        "2026-09-01T10-00-00_ab1",  # too short
        "2026-09-01 10-00-00_ab12",  # space, not T
        "../2026-09-01T10-00-00_ab12",  # traversal
    ]
    for raw in bad:
        try:
            store.path_for(raw)
        except ConversationNotFound:
            pass
        else:
            raise AssertionError(f"path_for accepted {raw!r}")
        assert serve_routes._BOT_DM_RE.fullmatch(f"/api/bots/{raw}/dm") is None


def _error_matrix() -> list[tuple[Exception, tuple[int, str] | None]]:
    not_found: tuple[int, str] | None = (404, "conversation not found")
    busy: tuple[int, str] | None = (409, "conversation busy")
    unreadable: tuple[int, str] | None = (503, "conversation unreadable")
    unavailable: tuple[int, str] | None = (503, "conversation unavailable")
    return [
        (ConversationNotFound("/x"), not_found),
        (ConversationBusy("s"), busy),
        (ConversationCorrupt("/x", 1, "boom"), unreadable),
        (ConversationError("boom"), unavailable),
        (ValueError("bad"), None),
        (RuntimeError("boom"), None),
    ]


def test_x1_error_maps_share_one_table() -> None:
    for exc, expected in _error_matrix():
        assert conversation_http_error(exc) == expected, exc
        assert delete_error(exc) == expected, exc


class _Handler:
    def __init__(self) -> None:
        self.calls: list[tuple[int, dict]] = []

    def _json(self, status: int, payload: dict) -> None:
        self.calls.append((status, payload))


def test_x1_conversations_error_delegates_with_chat_fallback() -> None:
    for exc, expected in _error_matrix():
        handler = _Handler()
        _conv_error(handler, exc)
        if expected is None:
            assert handler.calls == [(503, {"error": "chat unavailable"})], exc
        else:
            # Public wording names conversations, never conversations or paths.
            public = expected[1]
            assert handler.calls == [(expected[0], {"error": public})], exc




# Moved from test_b4_unification.py / test_b4_p2.py (B4 batch).
import json
import pytest

def test_x17_shared_estimator() -> None:
    from thyca.core.protocol import estimate_tokens as core_estimate

    from thyca.conversations.runtime.compaction import estimate_tokens as session_estimate
    from thyca.core.protocol import Message

    assert core_estimate("abcd") == 1
    assert core_estimate("") == 0
    # Wire payload only: ts/meta/reasoning are stripped by both adapters,
    # so they must not move the estimate.
    base = Message(role="user", content="same")
    noisy = Message(
        role="user",
        content="same",
        ts="2026-01-02T00:00:00Z",
        meta={"trace": "x" * 2000},
        reasoning="r" * 2000,
    )
    assert session_estimate(noisy) == session_estimate(base)
    assert session_estimate(base) == core_estimate(
        json.dumps({"role": "user", "content": "same"}, ensure_ascii=False)
    )
    # reasoning_details rides the chat path uncapped: it stays counted.
    signed = Message(
        role="assistant", content="same", reasoning_details=[{"sig": "s" * 400}]
    )
    assert session_estimate(signed) > session_estimate(
        Message(role="assistant", content="same")
    )




def test_last_user_index_shared_by_truncate_and_mark(tmp_path: Path) -> None:
    """truncate + mark agree on the last user message in a multi-turn run."""
    from thyca.conversations.runtime.manager import _last_user_index

    mgr = _canonical_manager(tmp_path)
    mgr.append(msg("user", "first"))
    mgr.append(msg("assistant", "answer"), actor="thyca")
    mgr.append(msg("user", "second"))
    mgr.append(msg("assistant", "partial"), actor="thyca")
    messages = mgr.current.messages
    assert _last_user_index(messages) == 2
    assert _last_user_index([]) is None
    assert _last_user_index([msg("assistant", "orphan")]) is None
    assert mgr.mark_turn_error("llm_error", "boom") is True
    assert messages[2].meta == {"error": {"code": "llm_error", "message": "boom"}}
    assert messages[0].meta is None
    assert mgr.truncate_to_last_user() is True
    assert [m.content for m in mgr.current.messages] == ["first", "answer", "second"]



def test_wire_turns_match_trace_turn_slices(tmp_path: Path) -> None:
    """Wire turns, turn_slices, and Trace agree incl. system/orphan rows."""
    from thyca.conversations.runtime.wire import conversation_summary, turn_slices

    conversation = ConversationRuntime(
        conversation_id="s1",
        path=tmp_path / "s1.jsonl",
        messages=[
            msg("assistant", "orphan-before-first-user"),
            msg("user", "one"),
            msg("assistant", "a1"),
            msg("system", "compact-marker"),
            msg("user", "two"),
            msg("tool", "t", tool_call_id="c1"),
        ],
    )
    assert conversation_summary(conversation)["turns"] == 2
    assert len(turn_slices(conversation.messages)) == 2
    assert len(serve_trace.turns_from_conversation(conversation)) == 2






# --- T1: compaction excerpt is the tail, carried forward on re-compaction ---


def _marker_excerpt(marker: Message) -> str:
    return (marker.content or "").rsplit("excerpt: ", 1)[-1][:-1]


def test_t1_excerpt_takes_tail_of_omitted_content() -> None:
    from thyca.conversations.runtime import ConversationCompactor

    compactor = ConversationCompactor()
    messages: list[Message] = []
    for i in range(8):
        messages.append(msg("user", f"TURN{i}-USER-" + "u" * 300))
        messages.append(msg("assistant", f"TURN{i}-ASST-" + "a" * 300))
    result = compactor.compact(messages, 1000)
    assert result is not None
    tail_len = len(result) - 1
    omitted = messages[: len(messages) - tail_len]
    assert len(omitted) >= 4  # enough omitted history to clip the head away
    excerpt_src = "\n".join(
        m.content
        for m in omitted
        if m.role in ("user", "assistant") and m.content
    )
    assert len(excerpt_src) > 1000
    excerpt = _marker_excerpt(result[0])
    assert len(excerpt) <= 1000
    assert excerpt_src.endswith(excerpt)  # tail, not head
    newest = omitted[-2].content or ""
    assert newest[:20] in excerpt  # most recent omitted turn survives
    oldest = omitted[0].content or ""
    assert oldest[:20] not in excerpt  # oldest clipped away


def test_t1_clip_excerpt_tail_and_surrogate_safety() -> None:
    from thyca.conversations.runtime.compaction import ConversationCompactor

    clip = ConversationCompactor._clip_excerpt
    assert clip("abcdef", 4) == "cdef"
    assert clip("abc", 10) == "abc"
    assert clip("", 5) == ""
    assert clip("abcdef", 0) == ""
    # Cut lands between a surrogate pair: the orphaned low surrogate drops.
    assert clip("ab\ud800\udc00cd", 3) == "cd"
    # Astral characters off the cut point pass through intact.
    assert clip("ab\U0001f600cdef", 4) == "cdef"


def test_t1_recompact_carries_prior_excerpt_and_counts(tmp_path: Path) -> None:
    manager = _canonical_manager(tmp_path, LimitsCfg(contextTokens=1000))
    conversation = manager.current
    for i in range(8):
        manager.append(msg("user", f"gen1-TURN{i}-" + "u" * 300))
        manager.append(msg("assistant", f"gen1-REPLY{i}-" + "a" * 300), actor="thyca")
    assert manager.compact_if_needed()
    marker1 = manager.current.messages[0]
    assert marker1.role == "system"
    excerpt1 = _marker_excerpt(marker1)
    meta1 = dict(marker1.meta or {})
    assert meta1["omitted_messages"] > 0

    for i in range(6):
        manager.append(msg("user", f"gen2-TURN{i}-" + "v" * 300))
        manager.append(msg("assistant", f"gen2-REPLY{i}-" + "b" * 300), actor="thyca")
    before = len(manager.current.messages)
    assert manager.compact_if_needed()
    compacted = manager.current.messages
    # Single leading system marker: store.scan's contract.
    assert compacted[0].role == "system"
    assert all(m.role != "system" for m in compacted[1:])
    excerpt2 = _marker_excerpt(compacted[0])
    assert len(excerpt2) <= 1000
    # Prior excerpt survives (capped to its head budget), newest stays.
    prior_kept = excerpt1[-500:] if len(excerpt1) > 500 else excerpt1
    assert prior_kept in excerpt2
    assert "gen2-" in excerpt2
    # Counts accumulate the carried-forward history, not just fresh omits.
    meta2 = compacted[0].meta or {}
    fresh_omitted = before - len(compacted)
    assert fresh_omitted > 0
    assert meta2["omitted_messages"] == meta1["omitted_messages"] + fresh_omitted
    assert meta2["omitted_turns"] >= meta1["omitted_turns"]
    assert meta2["omitted_chars"] > meta1["omitted_chars"]
    # Output round-trips through store.scan (load path).
    reloaded = _canonical_manager(tmp_path).load(conversation.conversation_id)
    assert reloaded.messages[0].role == "system"
    assert reloaded.messages[0].meta == meta2

    # Markers stay bounded across repeated compactions.
    for i in range(6):
        manager.append(msg("user", f"gen3-TURN{i}-" + "w" * 300))
        manager.append(msg("assistant", f"gen3-REPLY{i}-" + "c" * 300), actor="thyca")
    assert manager.compact_if_needed()
    excerpt3 = _marker_excerpt(manager.current.messages[0])
    assert len(excerpt3) <= 1000
    assert "gen3-" in excerpt3
    reloaded = _canonical_manager(tmp_path).load(conversation.conversation_id)
    assert [m.role for m in reloaded.messages[:2]] != ["system", "system"]


def test_compact_overhead_only_returns_none_without_junk_marker() -> None:
    from thyca.conversations.runtime.compaction import ConversationCompactor

    compactor = ConversationCompactor()
    messages = [msg("user", "u" * 100), msg("assistant", "a" * 100)]
    # Overhead alone trips the cap but every turn fits: shrink nothing,
    # mint no "omitted 0 messages/0 turns" marker.
    assert (
        compactor.compact(messages, 1000, pending_user_tokens=1000) is None
    )
    assert compactor.compact([], 1000, pending_user_tokens=1000) is None


def test_ensure_dm_finds_marked_creates_marked(tmp_path: Path) -> None:
    from thyca.conversations.runtime import ConversationManager

    manager = _runtime_manager(tmp_path / "sessions")
    first = manager.ensure_dm("coder")
    assert first.dm_bot == "coder"
    assert first.bots == ["coder"]
    second = manager.ensure_dm("coder")
    assert second.conversation_id == first.conversation_id
    assert _runtime_manager(tmp_path / "sessions").ensure_dm("other").conversation_id != first.conversation_id
