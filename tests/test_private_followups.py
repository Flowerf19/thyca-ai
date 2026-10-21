"""WS-4: PrivateFollowupStore state machine + ownership rules."""
from pathlib import Path

import pytest

from thyca.bots.private_followups import (
    PendingConflict,
    PrivateFollowupError,
    PrivateFollowupStore,
)


def _mk(store: PrivateFollowupStore, bot: str = "coder", **kw):
    kw.setdefault("work_id", "task-aabb1122")
    kw.setdefault("origin_window", "2026-10-19T09-00-00_ab12")
    kw.setdefault("dm_window", "2026-10-19T09-01-00_cd34")
    kw.setdefault("question", "what is the token?")
    return store.create(bot, **kw)


def test_create_conflict_and_idempotent_duplicate(tmp_path: Path) -> None:
    store = PrivateFollowupStore(tmp_path)
    p = _mk(store, idempotency_key="span1")
    assert p.status == "open"
    same = store.create(
        "coder", work_id="task-aabb1122", origin_window=p.origin_window,
        dm_window=p.dm_window, question=p.question, idempotency_key="span1")
    assert same.pending_id == p.pending_id
    with pytest.raises(PendingConflict):
        _mk(store, work_id="task-bbcc2233")


def test_state_transitions(tmp_path: Path) -> None:
    store = PrivateFollowupStore(tmp_path)
    p = _mk(store)
    with pytest.raises(PrivateFollowupError):
        store.receive("coder", p.pending_id, answer_event_id="e1")
    store.mark_waiting("coder", p.pending_id)
    p = store.receive("coder", p.pending_id, answer_event_id="e1",
                      answer_ref="dm:1", idempotency_key="e1")
    assert p.status == "received"
    # Exact replay (same event + ref + key) is idempotent; a changed
    # payload under the same key is a conflict, not a dedup.
    assert store.receive("coder", p.pending_id, answer_event_id="e1",
                         answer_ref="dm:1",
                         idempotency_key="e1").status == "received"
    with pytest.raises((PendingConflict, PrivateFollowupError)):
        store.receive("coder", p.pending_id, answer_event_id="e1",
                      answer_ref="dm:2", idempotency_key="e1")
    p = store.decide("coder", p.pending_id, action="ready")
    assert p.requested_outcome == "ready"
    p = store.decide("coder", p.pending_id, action="resume",
                     redacted="SAFE-TEXT")
    assert p.requested_outcome == "resume" and p.proposal_revision == 1
    p = store.approve("coder", p.pending_id, revision=1,
                      idempotency_key="m1")
    # Two-stage: approval only RECORDS authorization; the group row must
    # land before finalize marks resumed.
    assert p.status == "received" and p.approved_revision == 1
    p = store.finalize_resumed("coder", p.pending_id, posted_marker="m1")
    assert p.status == "resumed"
    # Idempotent finalization: same marker returns the terminal state.
    assert store.finalize_resumed("coder", p.pending_id,
                                  posted_marker="m1").status == "resumed"
    with pytest.raises(PrivateFollowupError):
        store.decide("coder", p.pending_id, action="done")


def test_invalid_inputs_rejected(tmp_path: Path) -> None:
    store = PrivateFollowupStore(tmp_path)
    p = _mk(store)
    with pytest.raises(PrivateFollowupError):
        store.create("coder", work_id="not-a-task",
                     origin_window="x", dm_window="y", question="q")
    with pytest.raises(PrivateFollowupError):
        store.create("coder", work_id="task-aabb1122",
                     origin_window="", dm_window="y", question="q")
    with pytest.raises(PrivateFollowupError):
        store.create("coder", work_id="task-aabb1122",
                     origin_window="x", dm_window="y", question="")
    with pytest.raises(PrivateFollowupError):
        store.create("coder", work_id="task-aabb1122",
                     origin_window=None, dm_window="y", question="q")
    store.cancel("coder", p.pending_id)


def test_stale_revision_denied(tmp_path: Path) -> None:
    store = PrivateFollowupStore(tmp_path)
    p = _mk(store)
    store.mark_waiting("coder", p.pending_id)
    store.receive("coder", p.pending_id, answer_event_id="e1")
    store.decide("coder", p.pending_id, action="resume", redacted="V1")
    store.decide("coder", p.pending_id, action="resume", redacted="V2")
    with pytest.raises(PrivateFollowupError):
        store.approve("coder", p.pending_id, revision=1)


def test_done_cancel_keep_private_expire(tmp_path: Path) -> None:
    store = PrivateFollowupStore(tmp_path)
    p = _mk(store)
    store.mark_waiting("coder", p.pending_id)
    store.receive("coder", p.pending_id, answer_event_id="e1")
    store.decide("coder", p.pending_id, action="done")
    assert store.active("coder") is None

    p2 = _mk(store, work_id="task-bbcc2233")
    store.cancel("coder", p2.pending_id)
    p3 = _mk(store, work_id="task-ddee3344")
    store.mark_waiting("coder", p3.pending_id)
    store.receive("coder", p3.pending_id, answer_event_id="e1")
    store.decide("coder", p3.pending_id, action="resume", redacted="X")
    kept = store.keep_private("coder", p3.pending_id)
    assert kept.proposed_redacted is None
    assert kept.status == "received"
    store.expire("coder", p3.pending_id)
    assert store.get("coder", p3.pending_id).status == "expired"


def test_resume_needs_redacted(tmp_path: Path) -> None:
    store = PrivateFollowupStore(tmp_path)
    p = _mk(store)
    store.mark_waiting("coder", p.pending_id)
    store.receive("coder", p.pending_id, answer_event_id="e1")
    with pytest.raises(PrivateFollowupError):
        store.decide("coder", p.pending_id, action="resume", redacted="")


def test_corrupt_file_errors_never_resets(tmp_path: Path) -> None:
    store = PrivateFollowupStore(tmp_path)
    _mk(store)
    path = tmp_path / "bots" / "coder" / "workspace" / "private_followups.json"
    path.write_text("{bad json", encoding="utf-8")
    with pytest.raises(PrivateFollowupError):
        store.active("coder")


def test_restart_same_state(tmp_path: Path) -> None:
    store = PrivateFollowupStore(tmp_path)
    p = _mk(store)
    store.mark_waiting("coder", p.pending_id)
    store2 = PrivateFollowupStore(tmp_path)
    loaded = store2.active("coder")
    assert loaded is not None
    assert loaded.pending_id == p.pending_id
    assert loaded.status == "waiting_private"


def test_ownership_record_and_fallback(tmp_path: Path) -> None:
    from thyca.bots.workspace_store import WorkspaceStore

    store = WorkspaceStore(tmp_path)
    store.record_task_owner("coder", "task-aabb1122", "coder")
    assert store.task_owner("coder", "task-aabb1122") == "coder"
    assert store.task_owner("coder", "task-ffffffff") is None
    with pytest.raises(Exception):
        store.record_task_owner("coder", "not-a-task", "coder")
    doc = (tmp_path / "bots" / "coder" / "workspace" / "registry.json")
    import json
    assert json.loads(doc.read_text())["task_owners"]["task-aabb1122"] == "coder"
    with pytest.raises(Exception):
        store.record_task_owner("coder", "task-aabb1122", "helper")


def test_invalid_bot_rejected(tmp_path: Path) -> None:
    store = PrivateFollowupStore(tmp_path)
    with pytest.raises(PrivateFollowupError):
        store.active("../escape")


def test_terminal_create_replay_exact_and_mismatched(tmp_path: Path) -> None:
    store = PrivateFollowupStore(tmp_path)
    p = _mk(store, idempotency_key="k1")
    store.mark_waiting("coder", p.pending_id)
    store.receive("coder", p.pending_id, answer_event_id="e1")
    store.decide("coder", p.pending_id, action="done")
    same = store.create(
        "coder", work_id="task-aabb1122",
        origin_window=p.origin_window, dm_window=p.dm_window,
        question=p.question, idempotency_key="k1")
    assert same.pending_id == p.pending_id
    with pytest.raises((PendingConflict, PrivateFollowupError)):
        store.create(
            "coder", work_id="task-aabb1122",
            origin_window=p.origin_window, dm_window=p.dm_window,
            question="different question", idempotency_key="k1")


def test_decide_same_key_altered_payload_conflicts(tmp_path: Path) -> None:
    store = PrivateFollowupStore(tmp_path)
    p = _mk(store)
    store.mark_waiting("coder", p.pending_id)
    store.receive("coder", p.pending_id, answer_event_id="e1")
    store.decide("coder", p.pending_id, action="resume",
                 redacted="V1", idempotency_key="d1")
    with pytest.raises((PendingConflict, PrivateFollowupError)):
        store.decide("coder", p.pending_id, action="resume",
                     redacted="V2-different", idempotency_key="d1")


def test_keep_private_then_revision2_stale_fails(tmp_path: Path) -> None:
    store = PrivateFollowupStore(tmp_path)
    p = _mk(store)
    store.mark_waiting("coder", p.pending_id)
    store.receive("coder", p.pending_id, answer_event_id="e1")
    store.decide("coder", p.pending_id, action="resume", redacted="V1")
    store.keep_private("coder", p.pending_id, revision=1)
    store.decide("coder", p.pending_id, action="resume", redacted="V2")
    with pytest.raises(PrivateFollowupError):
        store.approve("coder", p.pending_id, revision=1)
    with pytest.raises(PrivateFollowupError):
        store.keep_private("coder", p.pending_id, revision=1)


def test_finalize_without_auth_or_wrong_marker_fails(tmp_path: Path) -> None:
    store = PrivateFollowupStore(tmp_path)
    p = _mk(store)
    store.mark_waiting("coder", p.pending_id)
    store.receive("coder", p.pending_id, answer_event_id="e1")
    with pytest.raises(PrivateFollowupError):
        store.finalize_resumed("coder", p.pending_id, posted_marker="m1")
    store.decide("coder", p.pending_id, action="resume", redacted="V1")
    with pytest.raises(PrivateFollowupError):
        store.finalize_resumed("coder", p.pending_id, posted_marker="wrong")


def test_malformed_persisted_fields_error(tmp_path: Path) -> None:
    store = PrivateFollowupStore(tmp_path)
    p = _mk(store)
    path = tmp_path / "bots" / "coder" / "workspace" / "private_followups.json"
    import json as _json
    doc = _json.loads(path.read_text())
    doc["pending"][0]["work_id"] = "bogus"
    path.write_text(_json.dumps(doc))
    with pytest.raises(PrivateFollowupError):
        store.active("coder")
    doc = _json.loads(path.read_text())
    doc["pending"][0]["work_id"] = "task-aabb1122"
    doc["pending"][0]["origin_window"] = 123
    path.write_text(_json.dumps(doc))
    with pytest.raises(PrivateFollowupError):
        store.active("coder")
    doc = _json.loads(path.read_text())
    doc["pending"][0]["origin_window"] = "x"
    doc["pending"][0]["proposal_revision"] = "one"
    path.write_text(_json.dumps(doc))
    with pytest.raises(PrivateFollowupError):
        store.active("coder")


def test_write_permission_failure_propagates(tmp_path: Path) -> None:
    import os, stat
    store = PrivateFollowupStore(tmp_path)
    p = _mk(store)
    path = tmp_path / "bots" / "coder" / "workspace" / "private_followups.json"
    path.parent.chmod(0o555)
    try:
        with pytest.raises((PrivateFollowupError, OSError)):
            store.mark_waiting("coder", p.pending_id)
    finally:
        path.parent.chmod(0o755)
