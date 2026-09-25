"""B1b regression tests: memory + sessions safe-correctness fixes.

One focused file keeps the existing memory/session/serve test files untouched.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from thyca.core.protocol import Message, ToolCall
from thyca.llm.llm_base import ChatReply
from thyca.memory.active import ActiveMemory, ActiveMemoryError, ActiveState
from thyca.memory.archive_store import ArchiveError, ArchiveStore, Hit
from thyca.memory.archived import ArchivedMemory
from thyca.memory.chunk import Chunker
from thyca.memory.writer import MemoryWriter
from thyca.serve.memory import memory_endpoint
from thyca.sessions import SessionManager, SessionNotFound, SessionStore
from thyca.sessions.errors import SessionCorrupt, SessionError
from thyca.sessions.title import retitle_missing
from thyca.sessions.wire import session_summary
from thyca.tools.gateway import ToolGateway
from thyca.memory.facade import MemoryFacade
from thyca.tools.memory_tools import register_memory_tools
from thyca.tools.registry import ToolRegistry
from thyca.tools.task_store import TaskStore


def _msg(role: str, content: str = "x") -> Message:
    return Message(role=role, content=content, ts="2026-01-01T00:00:00Z")


def _seed_duplicate_daily(root: Path, day: str = "2026-08-13") -> Path:
    (root / "memory").mkdir(parents=True, exist_ok=True)
    path = root / "memory" / f"{day}.md"
    path.write_text(
        f"# {day}\n"
        '## 08:00 — first <!-- thyca {"id":"dddddddd","imp":3,"exp":"2027-09-12T00:00:00Z"} -->\n'
        "- first leaf is long enough here\n"
        '## 09:00 — second <!-- thyca {"id":"dddddddd","imp":3,"exp":"2027-09-12T00:00:00Z"} -->\n'
        "- second leaf is long enough here\n",
        encoding="utf-8",
    )
    return path


# --- F3: duplicate ids mutated wholesale ---


def test_duplicate_id_forget_touches_first_match_only(tmp_path: Path) -> None:
    path = _seed_duplicate_daily(tmp_path)
    MemoryWriter(tmp_path).forget("2026-08-13#dddddddd")
    text = path.read_text(encoding="utf-8")
    assert "first leaf" not in text
    assert "second leaf" in text
    assert text.count("dddddddd") == 1


def test_duplicate_id_reinforce_touches_first_match_only(tmp_path: Path) -> None:
    path = _seed_duplicate_daily(tmp_path)
    before = path.read_text(encoding="utf-8").splitlines(keepends=True)
    MemoryWriter(tmp_path).reinforce("2026-08-13#dddddddd", 5)
    after = path.read_text(encoding="utf-8").splitlines(keepends=True)
    second_before = next(line for line in before if "— second" in line)
    second_after = next(line for line in after if "— second" in line)
    assert second_after == second_before
    first_after = next(line for line in after if "— first" in line)
    assert "2027-09-12" not in first_after


# --- F8: serve drops mistyped fields ---


def test_mistyped_update_rejected_not_dropped(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    sid = facade.remember("cafe", "orig-summary", content="orig-details")
    before = next((tmp_path / "memory").glob("*.md")).read_text(encoding="utf-8")
    for kwargs in (
        {"topic": 123, "summary": "x"},
        {"summary": 123},
        {"summary": "x", "content": 123},
    ):
        with pytest.raises(ValueError):
            facade.update(sid, **kwargs)
    status, body = memory_endpoint(
        facade, "update", {"session_id": sid, "topic": 123, "summary": "x"}
    )
    assert status == 400
    assert "error" in body
    status, _body = memory_endpoint(
        facade, "update", {"session_id": sid, "summary": "x", "content": 123}
    )
    assert status == 400
    assert next((tmp_path / "memory").glob("*.md")).read_text(encoding="utf-8") == before


# --- F16: remember indent + blanks ---


def test_remember_multiline_chunks_like_update(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    sid1 = facade.remember("t1", "sum-one", content="alpha detail\nbeta detail")
    sid2 = facade.remember("t2", "placeholder")
    facade.update(sid2, summary="sum-one", content="alpha detail\nbeta detail")
    text = next((tmp_path / "memory").glob("*.md")).read_text(encoding="utf-8")
    assert "  alpha detail" in text and "  beta detail" in text
    assert "\nbeta detail\n" not in text

    def body(session_id: str) -> list[str]:
        lines = text.splitlines()
        entry = session_id.split("#", 1)[1]
        start = next(i for i, line in enumerate(lines) if entry in line)
        end = start + 1
        while end < len(lines) and not lines[end].startswith("## "):
            end += 1
        return lines[start + 1 : end]

    assert body(sid1) == body(sid2) == ["- sum-one", "  alpha detail", "  beta detail"]
    chunker = Chunker()
    day = facade.archive.day(None)
    leaves1 = [c.text_raw for c in chunker.chunk_markdown("d", "\n".join(body(sid1)), source_kind="daily", timeline_day=day)]
    leaves2 = [c.text_raw for c in chunker.chunk_markdown("d", "\n".join(body(sid2)), source_kind="daily", timeline_day=day)]
    assert leaves1 == leaves2


def test_remember_rejects_blanks_like_update(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    for topic, summary in (("   ", "s"), ("t", "  "), ("", "s"), ("t", "")):
        with pytest.raises(ValueError):
            facade.remember(topic, summary)
    assert list((tmp_path / "memory").glob("*.md")) == [] or all(
        "## " not in p.read_text(encoding="utf-8") for p in (tmp_path / "memory").glob("*.md")
    )
    sid = facade.remember("t", "s")
    with pytest.raises(ArchiveError):
        facade.writer.update_session(sid, topic="   ", body_lines=None)


# --- F17: invisible-leaf counts ---


def test_expired_leaf_count_matches_get(tmp_path: Path) -> None:
    (tmp_path / "memory").mkdir(parents=True, exist_ok=True)
    (tmp_path / "memory" / "2026-08-10.md").write_text(
        "# 2026-08-10\n"
        '## 08:00 — gone <!-- thyca {"id":"eeeeeeee","imp":3,"exp":"2020-01-01T00:00:00Z"} -->\n'
        "- this leaf expired long ago indeed\n",
        encoding="utf-8",
    )
    archived = ArchivedMemory(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    archived.reindex()
    sid = "2026-08-10#eeeeeeee"
    now = datetime(2026, 9, 25)
    assert archived.store.session_leaf_count(sid, "2026-09-25T00:00:00Z") == 0
    with pytest.raises(ArchiveError):
        archived.get(session_id=sid, now=now)
    hit = Hit(
        path="p", source_kind="daily", chunk_id=f"{sid}#1",
        timeline_day="2026-08-10", session_id=sid,
        heading="h", snippet="s", score=1.0, match_type="fts",
    )
    (counted,) = archived.with_counts([hit], now)
    assert counted.session_leaf_count == 0
    assert counted.has_more is False


# --- F18: raw UnicodeDecodeError ---


def test_non_utf8_daily_raises_typed_errors(tmp_path: Path) -> None:
    (tmp_path / "memory").mkdir(parents=True, exist_ok=True)
    (tmp_path / "memory" / "2026-08-10.md").write_bytes(b"\xff\xfe invalid \x80 bytes\n")
    archived = ArchivedMemory(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    with pytest.raises(ArchiveError):
        archived.reindex()
    active = ActiveMemory(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    now = datetime(2026, 9, 25, 12, 0)
    day = archived.day(now)
    today_path = active.memory_dir / f"{day}.md"
    today_path.parent.mkdir(parents=True, exist_ok=True)
    today_path.write_bytes(b"\xff\xfe invalid \x80 bytes\n")
    with pytest.raises(ActiveMemoryError):
        active.refresh(ActiveState(day=day, today_path=today_path), now)


# --- F19: negative limit ---


def test_negative_and_zero_limits_clamped(tmp_path: Path) -> None:
    (tmp_path / "memory").mkdir(parents=True, exist_ok=True)
    (tmp_path / "memory" / "2026-08-10.md").write_text(
        "# 2026-08-10\n"
        '## 08:00 — xylophone one <!-- thyca {"id":"11111111","imp":3,"exp":"2027-09-12T00:00:00Z"} -->\n'
        "- xylophone alpha leaf is here\n"
        '## 09:00 — xylophone two <!-- thyca {"id":"22222222","imp":3,"exp":"2027-09-12T00:00:00Z"} -->\n'
        "- xylophone beta leaf is here\n",
        encoding="utf-8",
    )
    archived = ArchivedMemory(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    archived.reindex()
    now_ts = "2026-09-25T00:00:00Z"
    assert archived.store.fts_search("xylophone", None, -1, now_ts) == []
    assert archived.store.fts_search("xylophone", None, 0, now_ts) == []
    assert len(archived.store.fts_search("xylophone", None, 1, now_ts)) == 1
    assert len(archived.store.fts_search("xylophone", None, 2, now_ts)) == 2
    norm = Chunker.normalize("xylophone")
    assert archived.store.trigram_search(norm, None, -1, now_ts) == []
    assert archived.store.trigram_search(norm, None, 0, now_ts) == []
    assert len(archived.store.trigram_search(norm, None, 2, now_ts)) == 2


# --- F20: reinforce ValueError leak ---


def test_reinforce_bad_importance_typed_both_layers(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    sid = facade.remember("cafe", "orig-summary")
    with pytest.raises(ArchiveError):
        facade.writer.reinforce(sid, 99)
    with pytest.raises(ValueError):
        facade.reinforce(sid, 99)
    status, body = memory_endpoint(
        facade, "reinforce", {"session_id": sid, "importance": "abc"}
    )
    assert (status, body) == (400, {"error": "invalid importance"})
    status, body = memory_endpoint(
        facade, "reinforce", {"session_id": sid, "importance": 99}
    )
    assert status == 400
    assert "invalid literal" not in body["error"]


# --- F22: purge swaps symlinks ---


def test_purge_leaves_symlink_daily_alone(tmp_path: Path) -> None:
    (tmp_path / "memory").mkdir(parents=True, exist_ok=True)
    (tmp_path / "memory" / "2026-08-10.md").write_text(
        "# 2026-08-10\n"
        '## 08:00 — stale <!-- thyca {"id":"aaaaaaaa","imp":1,"exp":"2020-01-01T00:00:00Z"} -->\n'
        "- stale leaf is long enough here\n",
        encoding="utf-8",
    )
    target = tmp_path / "linked.md"
    target.write_text(
        "# linked\n"
        '## 08:00 — via link <!-- thyca {"id":"bbbbbbbb","imp":1,"exp":"2020-01-01T00:00:00Z"} -->\n'
        "- linked leaf is long enough here\n",
        encoding="utf-8",
    )
    link = tmp_path / "memory" / "2026-08-11.md"
    link.symlink_to(target)
    before = target.read_text(encoding="utf-8")
    MemoryWriter(tmp_path).purge_expired(datetime(2026, 9, 25))
    assert "stale leaf" not in (tmp_path / "memory" / "2026-08-10.md").read_text(
        encoding="utf-8"
    )
    assert link.is_symlink()
    assert target.read_text(encoding="utf-8") == before


# --- F28: stat races ---


def test_list_paths_survives_racing_stat(tmp_path: Path, monkeypatch) -> None:
    manager = SessionManager(tmp_path)
    session = manager.create()
    real_stat = Path.stat
    calls = 0

    def flaky_stat(self):
        nonlocal calls
        calls += 1
        if calls <= 1:
            return real_stat(self)
        raise FileNotFoundError("gone")

    monkeypatch.setattr(Path, "stat", flaky_stat)
    assert manager.store.list_paths() == [session.path]


def test_summary_of_deleted_empty_session_is_not_found(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    session = manager.create()
    session.path.unlink()
    with pytest.raises(SessionNotFound):
        session_summary(session)


# --- F29: missing → corrupt ---


def test_compact_of_deleted_session_reports_not_found(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    manager.create()
    manager.append(_msg("user", "hello"))
    manager.current.path.unlink()
    with pytest.raises(SessionNotFound) as caught:
        manager.compact_if_needed()
    assert type(caught.value) is SessionNotFound
    assert not isinstance(caught.value, SessionCorrupt)


# --- F32: rewrite TypeError leak ---


def test_rewrite_unserializable_meta_wrapped(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    store.ensure_dir()
    sid = "2026-01-01T00-00-00_ab12"
    path = store.create(sid)
    bad = _msg("user", "x")
    object.__setattr__(bad, "meta", {"bad": object()})
    with pytest.raises(SessionError) as caught:
        store.rewrite(sid, path, [bad])
    assert type(caught.value) is not TypeError
    assert list(tmp_path.glob(".*.tmp.*")) == []
    assert path.read_text(encoding="utf-8") == ""


# --- F33: retitle hijack ---


async def test_retitle_preserves_current_and_skips_per_item(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    current = manager.create()
    manager.append(_msg("user", "current user text here"))
    manager.append(_msg("assistant", "current reply here"))
    manager.set_title("Current Name")
    u1 = manager.create()
    manager.append(_msg("user", "alpha-one distinct text"))
    manager.append(_msg("assistant", "reply one"))
    u2 = manager.create()
    manager.append(_msg("user", "beta-two distinct text"))
    manager.append(_msg("assistant", "reply two"))
    u3 = manager.create()
    manager.append(_msg("user", "gamma-three distinct text"))
    manager.append(_msg("assistant", "reply three"))
    manager.load(current.id)

    async def chat(messages, tools=None):
        if "alpha-one" in messages[1].content:
            raise RuntimeError("boom")
        return ChatReply(content="Nhịp sáng")

    real_load = manager.load

    def flaky_load(session_id: str):
        if session_id == u2.id:
            raise SessionNotFound(session_id)
        return real_load(session_id)

    manager.load = flaky_load  # type: ignore[method-assign]
    try:
        named = await retitle_missing(chat, manager)
    finally:
        manager.load = real_load  # type: ignore[method-assign]
    assert [item[0].id for item in named] == [u3.id]
    assert manager.current.id == current.id
    assert SessionManager(tmp_path).load(u3.id).title == "Nhịp sáng"
    assert SessionManager(tmp_path).load(u1.id).title is None
    assert SessionManager(tmp_path).load(u2.id).title is None


# --- M4-safe-half: limit edges + non-str inputs ---


async def test_memory_tool_limit_edges_never_raise(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    facade.remember("cafe", "limit edge summary here")
    registry = ToolRegistry()
    register_memory_tools(registry, facade)
    gateway = ToolGateway(registry, TaskStore())
    for limit in (0, -1, None):
        arguments: dict = {"query": "cafe"}
        if limit is not None:
            arguments["limit"] = limit
        result = await gateway.submit(
            ToolCall(id="s", name="memory_search", arguments=arguments)
        )
        assert not result.is_error, limit
        recent = await gateway.submit(
            ToolCall(
                id="r",
                name="memory_recent",
                arguments={} if limit is None else {"limit": limit},
            )
        )
        assert not recent.is_error, limit
    # F7-strict: mistyped limits are rejected, not truncated or defaulted.
    for limit in (2.5, "abc"):
        result = await gateway.submit(
            ToolCall(
                id="s", name="memory_search",
                arguments={"query": "cafe", "limit": limit},
            )
        )
        assert result.is_error, limit
        assert "argument 'limit' must be integer" in result.content, limit


def test_non_str_search_inputs_warn(tmp_path: Path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    assert facade.search(123).warnings == ["invalid query"]
    assert facade.search("x", timeline_day=123).warnings == ["invalid timeline_day"]
    assert facade.search("x", timeline_day="not-a-day").warnings == [
        "invalid timeline_day"
    ]


async def test_tool_non_str_timeline_day_rejected(tmp_path: Path) -> None:
    # F7-strict: mistyped tool args are rejected by the registry; the
    # warnings path survives only for direct facade calls (see
    # test_non_str_search_inputs_warn).
    facade = MemoryFacade(tmp_path, timezone_name="Asia/Ho_Chi_Minh")
    registry = ToolRegistry()
    register_memory_tools(registry, facade)
    gateway = ToolGateway(registry, TaskStore())
    result = await gateway.submit(
        ToolCall(
            id="s",
            name="memory_search",
            arguments={"query": "cafe", "timeline_day": 123},
        )
    )
    assert result.is_error
    assert "argument 'timeline_day' must be string, got integer" in result.content
