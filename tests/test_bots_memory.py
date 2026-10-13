"""BE-4: bots field end-to-end, ownership, candidates, edges, hot hiding."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from thyca.memory.archive_store import ArchiveError
from thyca.memory.candidates import CandidateQueue
from thyca.memory.facade import MemoryFacade
from thyca.memory.heading import parse_heading
from thyca.memory.identity import bind_bot_id, reset_bot_id

T0 = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
T1 = T0 + timedelta(days=1)


def _facade(tmp_path: Path) -> MemoryFacade:
    return MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")


def _as(bot_id: str | None):
    return bind_bot_id(bot_id)


def _file_bots(tmp_path: Path, day: str) -> dict[str, tuple | None]:
    """Heading bots per entry id in one daily file."""
    out = {}
    for line in (tmp_path / "memory" / f"{day}.md").read_text(encoding="utf-8").splitlines():
        meta = parse_heading(line)
        if meta is not None and meta.entry_id is not None:
            out[meta.entry_id] = meta.bots
    return out


def _write_daily(tmp_path: Path, day: str, blocks: list[tuple[str, str, str | None]]) -> None:
    """Hand-write a daily file: (entry_id, body_token, bots_json_or_None)."""
    lines = [f"# {day}\n"]
    for entry_id, token, bots in blocks:
        if bots is None:
            comment = f'<!-- thyca {{\"id\":\"{entry_id}\",\"imp\":5}} -->'
        else:
            comment = f'<!-- thyca {{\"id\":\"{entry_id}\",\"imp\":5,\"bots\":{bots}}} -->'
        lines.append(f"## 10:00 — note {token} {comment}\n- body {token}\n")
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / f"{day}.md").write_text("".join(lines), encoding="utf-8")


def test_bots_round_trip_heading_to_sqlite(tmp_path: Path) -> None:
    facade = _facade(tmp_path)
    token = _as("coder")
    try:
        sid = facade.remember("priv", "coder secret", now=T0, importance=5)
    finally:
        reset_bot_id(token)
    bots = _file_bots(tmp_path, "2026-08-01")
    assert bots[sid.split("#")[1]] == ("coder",)
    # A reindexed facade (new process view) carries the owner in SQLite.
    facade2 = _facade(tmp_path)
    assert facade2.archive.store.session_bots(sid) == ("coder",)
    # Legacy user entries carry no bots field.
    sid2 = facade.remember("pub", "user note", now=T0, importance=5)
    entry2 = sid2.split("#")[1]
    assert _file_bots(tmp_path, "2026-08-01")[entry2] is None


def test_child_cannot_touch_team_or_other_private(tmp_path: Path) -> None:
    facade = _facade(tmp_path)
    team_sid = facade.remember("shared", "team fact", now=T0, bots=["team"])
    other = _as("coder")
    try:
        other_sid = facade.remember("mine", "coder fact", now=T0)
    finally:
        reset_bot_id(other)
    token = _as("researcher")
    try:
        with pytest.raises(ArchiveError):
            facade.forget(team_sid, now=T1)
        with pytest.raises(ArchiveError):
            facade.update(other_sid, summary="hijack", now=T1)
        with pytest.raises(ArchiveError):
            facade.get(session_id=other_sid, now=T1)
        # Team + legacy stay readable.
        assert "team fact" in facade.get(session_id=team_sid, now=T1)
    finally:
        reset_bot_id(token)


def test_owner_can_edit_own_and_thyca_can_all(tmp_path: Path) -> None:
    facade = _facade(tmp_path)
    token = _as("coder")
    try:
        sid = facade.remember("mine", "v1", now=T0)
        facade.update(sid, summary="v2", now=T1)
        assert "v2" in facade.get(session_id=sid, now=T1)
    finally:
        reset_bot_id(token)
    token = _as("thyca")
    try:
        facade.update(sid, summary="v3 by thyca", now=T1)
        assert "v3 by thyca" in facade.get(session_id=sid, now=T1)
    finally:
        reset_bot_id(token)


def test_child_cannot_write_team_directly(tmp_path: Path) -> None:
    facade = _facade(tmp_path)
    token = _as("coder")
    try:
        with pytest.raises(ArchiveError):
            facade.remember("sneaky", "to team", now=T0, bots=["team"])
    finally:
        reset_bot_id(token)


def test_search_hides_other_private(tmp_path: Path) -> None:
    _write_daily(tmp_path, "2026-08-01", [
        ("a1b2c3d4", "zqxw-coder-secret", '["coder"]'),
        ("e5f6a7b8", "zqxw-team-shared", '["team"]'),
    ])
    facade = _facade(tmp_path)
    at = datetime(2026, 8, 2, tzinfo=UTC)
    token = _as("researcher")
    try:
        hits = facade.search("zqxw", now=at).hits
        assert {hit.session_id for hit in hits} == {"2026-08-01#e5f6a7b8"}
    finally:
        reset_bot_id(token)
    token = _as("coder")
    try:
        hits = facade.search("zqxw", now=at).hits
        assert len(hits) == 2
    finally:
        reset_bot_id(token)
    token = _as(None)
    try:
        assert len(facade.search("zqxw", now=at).hits) == 2
    finally:
        reset_bot_id(token)


def test_candidate_propose_approve_flow(tmp_path: Path) -> None:
    facade = _facade(tmp_path)
    token = _as("coder")
    try:
        sid = facade.remember("gift", "coffee eggs", now=T0)
    finally:
        reset_bot_id(token)
    queue = CandidateQueue(tmp_path)
    cand = queue.propose("coder", sid)
    assert cand.status == "pending"
    assert [c.id for c in queue.list_pending()] == [cand.id]
    # Approve promotes with a stable id and Thyca attribution.
    decided = queue.decide(cand.id, True, "user")
    facade.promote_to_team(sid, now=T1)
    assert decided.status == "approved"
    assert _file_bots(tmp_path, "2026-08-01")[sid.split("#")[1]] == ("team",)
    # Reject leaves no residue.
    cand2 = queue.propose("coder", sid)
    queue.decide(cand2.id, False, "user")
    assert queue.list_pending() == []


def test_child_cannot_promote(tmp_path: Path) -> None:
    facade = _facade(tmp_path)
    sid = facade.remember("x", "y", now=T0)
    token = _as("coder")
    try:
        with pytest.raises(ArchiveError):
            facade.promote_to_team(sid, now=T1)
    finally:
        reset_bot_id(token)


def test_related_edges_drop_on_forget(tmp_path: Path) -> None:
    facade = _facade(tmp_path)
    a = facade.remember("alpha", "first aa", now=T0)
    b = facade.remember("beta", "second bb", now=T0)
    store = facade.archive.store
    from thyca.memory.heading import format_ts, utc_now

    store.add_related(a, b, "liên quan", format_ts(utc_now(None)))
    assert store.related_of(a)
    facade.forget(a, now=T1)
    assert store.related_of(b) == []


def test_hot_prompt_hides_other_private(tmp_path: Path) -> None:
    from thyca.memory.active import ActiveMemory
    from thyca.tools.memory_tools import bind_chat_session, reset_chat_session

    chat_token = bind_chat_session("room1")
    try:
        _facade(tmp_path).remember("priv", "coder hidden gem", now=T0,
                                   bots=["coder"], chat="room1")
    finally:
        reset_chat_session(chat_token)
    memory = ActiveMemory(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    state = memory.open_session(datetime(2026, 8, 1, tzinfo=UTC))
    moment = datetime(2026, 8, 1, 13, 0, tzinfo=UTC)
    snap = memory.refresh(state, moment, session_id="room1", reader="researcher")
    assert "hidden gem" not in snap.today
    assert "hidden gem" not in snap.today_elsewhere
    snap2 = memory.refresh(state, moment, session_id="room1", reader=None)
    assert "hidden gem" in snap2.today or "hidden gem" in snap2.today_elsewhere
