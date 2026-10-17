from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from thyca.core.protocol import ToolCall
from thyca.memory.facade import MemoryFacade
from thyca.memory.heading import (
    parse_heading,
    render_heading,
    HeadingMeta,
    new_task_id,
    validate_task_meta,
)
from thyca.memory.identity import bind_bot_id, reset_bot_id
from thyca.tools.gateway import ToolGateway
from thyca.tools.memory_tools import register_memory_tools
from thyca.tools.registry import ToolRegistry
from thyca.tools.task_store import TaskStore

UTC = timezone.utc
TZ = "Asia/Ho_Chi_Minh"


def at(day: int, hour: int = 12) -> datetime:
    return datetime(2026, 9, day, hour, 0, tzinfo=UTC)


def facade_at(tmp_path, day: int) -> MemoryFacade:
    return MemoryFacade(tmp_path, timezone_name=TZ)


# -- heading roundtrip ------------------------------------------------------

def test_heading_roundtrip_with_all_five_fields() -> None:
    meta = HeadingMeta(
        time="08:00", title="task work", entry_id="a1b2c3d4",
        task="task-1234abcd", cont="2026-09-01#deadbeef",
        kind="habit", status="open", habit="uong ca phe den",
    )
    line = render_heading(meta)
    back = parse_heading(line)
    assert back is not None
    assert back.task == "task-1234abcd"
    assert back.cont == "2026-09-01#deadbeef"
    assert back.kind == "habit"
    assert back.status == "open"
    assert back.habit == "uong ca phe den"


def test_heading_without_new_keys_reads_none() -> None:
    back = parse_heading("## 08:00 — plain <!-- thyca {\"id\":\"a1b2c3d4\",\"imp\":3} -->")
    assert back is not None
    assert back.task is None
    assert back.cont is None
    assert back.kind is None
    assert back.status is None
    assert back.habit is None


def test_new_task_id_format_and_uniqueness() -> None:
    seen = {new_task_id() for _ in range(50)}
    assert len(seen) == 50
    for task_id in seen:
        assert task_id.startswith("task-") and len(task_id) == 13


# -- validation -------------------------------------------------------------

def test_validate_task_meta_rejections() -> None:
    with pytest.raises(ValueError, match="task must match"):
        validate_task_meta("nope", None, None, None, None)
    with pytest.raises(ValueError, match="status requires task"):
        validate_task_meta(None, None, None, "done", None)
    with pytest.raises(ValueError, match="cont requires task"):
        validate_task_meta(None, "2026-09-01#deadbeef", None, None, None)
    with pytest.raises(ValueError, match="requires a non-empty habit"):
        validate_task_meta("task-1234abcd", None, "habit", None, "  ")
    with pytest.raises(ValueError, match="habit requires kind"):
        validate_task_meta("task-1234abcd", None, None, None, "label")
    with pytest.raises(ValueError, match="must be one of"):
        validate_task_meta("task-1234abcd", None, "project", None, None)
    # Valid sets pass.
    validate_task_meta("task-1234abcd", "2026-09-01#deadbeef", "habit", "open", "label")
    validate_task_meta("task-1234abcd", None, None, "done", None)
    validate_task_meta(None, None, None, None, None)


def test_remember_validation_errors(tmp_path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    with pytest.raises(ValueError, match="task must match"):
        facade.remember("t", "s", now=at(1), task="bad")
    with pytest.raises(ValueError, match="status requires task"):
        facade.remember("t", "s", now=at(1), status="done")
    with pytest.raises(ValueError, match="cont requires task"):
        facade.remember("t", "s", now=at(1), cont="2026-09-01#deadbeef")


# -- remember → reindex → search ---------------------------------------------

def test_remember_search_filter_task_kind_status(tmp_path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    tid = facade.new_task_id()
    facade.remember("plumbing", "fix sink gasket uniquetoken", now=at(1),
                    task=tid, status="open")
    facade.remember("coffee", "ca phe den habit uniquetoken", now=at(1),
                    task=tid, kind="habit", habit="uong ca phe den")
    facade.remember("other", "unrelated uniquetoken", now=at(1))
    now = at(5)
    facade._refresh_index(now)
    assert {h.session_id for h in facade.search("uniquetoken", now=now, task=tid).hits} \
        == {h.session_id for h in facade.search("uniquetoken", now=now).hits
            if h.task == tid}
    assert len(facade.search("uniquetoken", now=now, task=tid).hits) == 2
    assert len(facade.search("uniquetoken", now=now, kind="habit").hits) == 1
    assert len(facade.search("uniquetoken", now=now, status="open").hits) == 1
    assert facade.search("uniquetoken", now=now, status="done").hits == []
    assert facade.search("uniquetoken", now=now, task="task-00000000").hits == []
    hit = facade.search("uniquetoken", now=now, kind="habit").hits[0]
    assert hit.task == tid and hit.habit == "uong ca phe den"
    bad = facade.search("uniquetoken", now=now, status="bogus")
    assert bad.hits == [] and bad.warnings == ["invalid status"]


def test_rebuild_from_markdown_keeps_task_cont(tmp_path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    tid = facade.new_task_id()
    first = facade.remember("step one", "alpha uniquetoken", now=at(1), task=tid)
    second_sid = facade.remember("step two", "beta uniquetoken", now=at(2), task=tid, cont=first)
    facade._refresh_index(at(5))
    (tmp_path / "memory.sqlite").unlink()
    rebuilt = MemoryFacade(tmp_path, timezone_name=TZ)
    rebuilt._refresh_index(at(5))
    hits = rebuilt.search("uniquetoken", now=at(5), task=tid).hits
    assert len(hits) == 2
    second = next(h for h in hits if h.session_id == second_sid)
    assert second.cont == first


# -- memory_tasks ------------------------------------------------------------

def test_tasks_groups_days_and_latest_status_wins(tmp_path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    tid = facade.new_task_id()
    facade.remember("start", "kickoff", now=at(1), task=tid, status="open")
    facade.remember("middle", "progress", now=at(3), task=tid, status="done")
    facade._refresh_index(at(5))
    catalog = facade.tasks(now=at(5))
    assert len(catalog["tasks"]) == 1
    entry = catalog["tasks"][0]
    assert entry["task_id"] == tid
    assert entry["title"] == "start"
    assert entry["status"] == "done"
    assert entry["entry_count"] == 2
    assert entry["first_day"] == "2026-09-01"
    assert entry["last_day"] == "2026-09-03"
    # Reopen on day 4 beats done.
    facade.remember("again", "reopened", now=at(4), task=tid, status="open")
    facade._refresh_index(at(5))
    assert facade.tasks(now=at(5))["tasks"][0]["status"] == "open"
    # A later entry without status does not clear the earlier done.
    other = facade.new_task_id()
    facade.remember("o1", "one", now=at(1), task=other, status="open")
    facade.remember("o2", "two", now=at(2), task=other, status="done")
    facade.remember("o3", "three", now=at(3), task=other)
    facade._refresh_index(at(5))
    by_id = {t["task_id"]: t for t in facade.tasks(now=at(5))["tasks"]}
    assert by_id[other]["status"] == "done"


def test_tasks_latest_session_id_tracks_newest_entry(tmp_path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    tid = facade.new_task_id()
    first = facade.remember("start", "kickoff", now=at(1), task=tid)
    second = facade.remember("middle", "progress", now=at(3), task=tid)
    facade._refresh_index(at(5))
    entry = facade.tasks(now=at(5))["tasks"][0]
    assert entry["latest_session_id"] == second
    # Same-day entry (unindexed) still reports its own session id.
    third = facade.remember("today", "doing", now=at(5), task=tid)
    assert facade.tasks(now=at(5))["tasks"][0]["latest_session_id"] == third
    assert first != second != third


def test_tasks_filters_and_sort_and_limit(tmp_path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    open_tid = facade.new_task_id()
    done_tid = facade.new_task_id()
    facade.remember("early", "e", now=at(1), task=open_tid)
    facade.remember("late", "l", now=at(10), task=done_tid, status="done")
    facade._refresh_index(at(12))
    assert [t["task_id"] for t in facade.tasks(now=at(12))["tasks"]] == [done_tid, open_tid]
    assert [t["task_id"] for t in facade.tasks(now=at(12), status="done")["tasks"]] == [done_tid]
    assert facade.tasks(now=at(12), status="open")["tasks"][0]["task_id"] == open_tid
    ranged = facade.tasks(now=at(12), date_from="2026-09-09", date_to="2026-09-11")["tasks"]
    assert [t["task_id"] for t in ranged] == [done_tid]
    assert facade.tasks(now=at(12), date_from="2026-09-11")["tasks"] == []
    limited = facade.tasks(now=at(12), limit=1)
    assert len(limited["tasks"]) == 1
    clamped = facade.tasks(now=at(12), limit=500)
    assert len(clamped["tasks"]) == 2
    assert clamped["warnings"] == ["limit clamped from 500 to 50"]
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        facade.tasks(now=at(12), date_from="not-a-date")
    with pytest.raises(ValueError, match="must be one of"):
        facade.tasks(now=at(12), status="bogus")


def test_tasks_reader_hides_other_bot_entries(tmp_path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    shared = facade.new_task_id()
    solo = facade.new_task_id()
    facade.remember("team goal", "shared work", now=at(1), task=shared)
    token = bind_bot_id("coder")
    try:
        facade.remember("coder part", "coder work", now=at(1), task=shared)
        facade.remember("solo", "solo work", now=at(1), task=solo)
    finally:
        reset_bot_id(token)
    facade._refresh_index(at(5))
    other = bind_bot_id("designer")
    try:
        catalog = facade.tasks(now=at(5))
    finally:
        reset_bot_id(other)
    by_id = {t["task_id"]: t for t in catalog["tasks"]}
    assert by_id[shared]["entry_count"] == 1
    assert by_id[shared]["bots"] == ["team"]
    assert solo not in by_id


def test_tasks_bots_union(tmp_path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    tid = facade.new_task_id()
    facade.remember("team bit", "shared", now=at(1), task=tid)
    token = bind_bot_id("coder")
    try:
        facade.remember("coder bit", "coded", now=at(2), task=tid)
    finally:
        reset_bot_id(token)
    facade._refresh_index(at(5))
    entry = facade.tasks(now=at(5))["tasks"][0]
    assert entry["bots"] == ["coder", "team"]


# -- migration v7 → v8 --------------------------------------------------------

V7_NEW_COLUMNS = ("task", "cont", "kind", "status", "habit")
V6_NEW_COLUMNS = ("bots",) + V7_NEW_COLUMNS


V7_GRAPH_DDL = """
CREATE TABLE related_edges(
  a_session   TEXT NOT NULL,
  b_session   TEXT NOT NULL,
  reason      TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL,
  PRIMARY KEY (a_session, b_session),
  CHECK(a_session < b_session)
);
CREATE INDEX related_edges_b ON related_edges(b_session);
"""


def _schema_without(*drop: str, with_graph: bool = True) -> str:
    """Current schema.sql minus columns (and optionally the graph table)
    an old release never had — keeps triggers/FTS in sync like a real
    old DB."""
    from pathlib import Path as _Path

    script = (_Path(__file__).parent.parent / "thyca" / "memory" / "schema.sql").read_text(
        encoding="utf-8"
    )
    out: list[str] = []
    skip_block = False
    for line in script.splitlines(keepends=True):
        stripped = line.strip()
        if not with_graph and "CREATE TABLE IF NOT EXISTS related_edges(" in stripped:
            skip_block = True
            continue
        if skip_block:
            if stripped == ");":
                skip_block = False
            continue
        if not with_graph and "related_edges_b" in stripped:
            continue
        if stripped.split(" ")[0] in drop:
            continue
        out.append(line)
    if with_graph:
        # Phase 1B removed the graph table from schema.sql; a realistic
        # v7 database still has it.
        out.append(V7_GRAPH_DDL)
    return "".join(out)


def _make_v7_db(tmp_path) -> None:
    """Realistic v7 database: current schema.sql minus the v8 columns.

    Triggers keep chunks_fts in sync, exactly like a database created by
    an older release (a hand-rolled chunks table without the FTS side
    breaks cascade deletes)."""
    db_path = tmp_path / "memory.sqlite"
    db = sqlite3.connect(db_path)
    db.executescript(_schema_without(*V7_NEW_COLUMNS))
    db.execute("INSERT INTO meta VALUES ('schema_version', '7')")
    db.execute(
        "INSERT INTO source_files VALUES (?, 'daily', '2026-09-01', 1, 10)",
        (str(tmp_path / "memory" / "2026-09-01.md"),),
    )
    db.execute(
        """INSERT INTO chunks(chunk_id, path, source_kind, timeline_day, session_id,
           session_title, heading_raw, leaf_ord, line_start, line_end,
           text_raw, text_norm, content_hash, expires_at)
           VALUES ('2026-09-01#deadbeef#1', ?, 'daily', '2026-09-01',
           '2026-09-01#deadbeef', 'old topic', '## 08:00 — old topic',
           1, 2, 2, 'old body text', 'old body text', 'hash', NULL)""",
        (str(tmp_path / "memory" / "2026-09-01.md"),),
    )
    db.commit()
    db.close()


def test_migration_v7_to_v8_keeps_data_and_loads_new_fields(tmp_path) -> None:
    (tmp_path / "memory").mkdir(parents=True)
    (tmp_path / "memory" / "2026-09-01.md").write_text(
        "# 2026-09-01\n"
        "## 08:00 — old topic <!-- thyca {\"id\":\"deadbeef\",\"imp\":3} -->\n"
        "- old body text\n",
        encoding="utf-8",
    )
    _make_v7_db(tmp_path)
    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    db = sqlite3.connect(tmp_path / "memory.sqlite")
    try:
        assert db.execute(
            "SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "8"
        cols = {row[1] for row in db.execute("PRAGMA table_info(chunks)")}
        assert {"task", "cont", "kind", "status", "habit"} <= cols
        row = db.execute(
            "SELECT text_raw, task FROM chunks WHERE chunk_id='2026-09-01#deadbeef#1'"
        ).fetchone()
        assert row[0] == "- old body text" and row[1] is None
    finally:
        db.close()
    # Reindex picks up a task key added to the markdown afterwards.
    (tmp_path / "memory" / "2026-09-01.md").write_text(
        "# 2026-09-01\n"
        "## 08:00 — old topic <!-- thyca {\"id\":\"deadbeef\",\"imp\":3,\"task\":\"task-aaaaaaaa\"} -->\n"
        "- old body text\n",
        encoding="utf-8",
    )
    facade._refresh_index(at(5))
    assert len(facade.search("old body", now=at(5), task="task-aaaaaaaa").hits) == 1


def _make_v6_db(tmp_path) -> None:
    """Realistic v6 database: no bots/task columns, no related_edges."""
    db_path = tmp_path / "memory.sqlite"
    db = sqlite3.connect(db_path)
    db.executescript(_schema_without(*V6_NEW_COLUMNS, with_graph=False))
    db.execute("INSERT INTO meta VALUES ('schema_version', '6')")
    db.execute(
        "INSERT INTO source_files VALUES (?, 'daily', '2026-09-01', 1, 10)",
        (str(tmp_path / "memory" / "2026-09-01.md"),),
    )
    db.execute(
        """INSERT INTO chunks(chunk_id, path, source_kind, timeline_day, session_id,
           session_title, heading_raw, leaf_ord, line_start, line_end,
           text_raw, text_norm, content_hash, expires_at)
           VALUES ('2026-09-01#deadbeef#1', ?, 'daily', '2026-09-01',
           '2026-09-01#deadbeef', 'old topic', '## 08:00 — old topic',
           1, 2, 2, 'old body text', 'old body text', 'hash', NULL)""",
        (str(tmp_path / "memory" / "2026-09-01.md"),),
    )
    db.commit()
    db.close()


def test_migration_v7_with_edges_drops_graph_table(tmp_path) -> None:
    (tmp_path / "memory").mkdir(parents=True)
    (tmp_path / "memory" / "2026-09-01.md").write_text(
        "# 2026-09-01\n"
        "## 08:00 — old topic <!-- thyca {\"id\":\"deadbeef\",\"imp\":3} -->\n"
        "- old body text\n",
        encoding="utf-8",
    )
    _make_v7_db(tmp_path)
    setup = sqlite3.connect(tmp_path / "memory.sqlite")
    try:
        setup.execute(
            "INSERT INTO related_edges VALUES ('2026-09-01#deadbeef', '2026-09-02#x', '', 't')"
        )
        setup.commit()
    finally:
        setup.close()
    MemoryFacade(tmp_path, timezone_name=TZ)
    db = sqlite3.connect(tmp_path / "memory.sqlite")
    try:
        assert db.execute(
            "SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "8"
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "related_edges" not in tables
        assert db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] >= 1
    finally:
        db.close()


def test_migration_v6_to_v8_gains_bots_and_task_columns(tmp_path) -> None:
    (tmp_path / "memory").mkdir(parents=True)
    (tmp_path / "memory" / "2026-09-01.md").write_text(
        "# 2026-09-01\n"
        "## 08:00 — old topic <!-- thyca {\"id\":\"deadbeef\",\"imp\":3} -->\n"
        "- old body text\n",
        encoding="utf-8",
    )
    _make_v6_db(tmp_path)
    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    db = sqlite3.connect(tmp_path / "memory.sqlite")
    try:
        assert db.execute(
            "SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "8"
        cols = {row[1] for row in db.execute("PRAGMA table_info(chunks)")}
        assert {"bots", "task", "cont", "kind", "status", "habit"} <= cols
        # Phase 1B: landing on v8 ends without the graph table (it never
        # existed in this v6 database, and migration must not create it).
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "related_edges" not in tables
        row = db.execute(
            "SELECT text_raw, bots, task FROM chunks "
            "WHERE chunk_id='2026-09-01#deadbeef#1'"
        ).fetchone()
        assert row[0] == "- old body text" and row[1] is None and row[2] is None
        # The task index from init_schema works on the migrated table.
        indexes = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        assert "chunks_task" in indexes
    finally:
        db.close()
    facade._refresh_index(at(5))
    assert len(facade.search("old body", now=at(5)).hits) == 1


# -- same-day catalog (Finding 2) -------------------------------------------------


def test_tasks_lists_task_opened_today(tmp_path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    tid = facade.new_task_id()
    facade.remember("today goal", "doing things today", now=at(3), task=tid)
    # No day-close, no manual reindex: the catalog still shows it.
    catalog = facade.tasks(now=at(3))
    assert [t["task_id"] for t in catalog["tasks"]] == [tid]
    assert catalog["tasks"][0]["entry_count"] == 1
    assert catalog["tasks"][0]["last_day"] == "2026-09-03"


def test_tasks_no_double_count_after_day_close(tmp_path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    tid = facade.new_task_id()
    facade.remember("today goal", "doing things today", now=at(3), task=tid)
    assert facade.tasks(now=at(3))["tasks"][0]["entry_count"] == 1
    facade._refresh_index(at(5))
    catalog = facade.tasks(now=at(5))
    assert [t["task_id"] for t in catalog["tasks"]] == [tid]
    assert catalog["tasks"][0]["entry_count"] == 1


def test_tasks_today_hidden_from_other_child_bot(tmp_path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    tid = facade.new_task_id()
    token = bind_bot_id("coder")
    try:
        facade.remember("coder goal", "coder work today", now=at(3), task=tid)
    finally:
        reset_bot_id(token)
    # The user (no reader) sees everything, including today's entry.
    assert [t["task_id"] for t in facade.tasks(now=at(3))["tasks"]] == [tid]
    other = bind_bot_id("designer")
    try:
        assert facade.tasks(now=at(3))["tasks"] == []
    finally:
        reset_bot_id(other)
    designer = bind_bot_id("coder")
    try:
        assert [t["task_id"] for t in facade.tasks(now=at(3))["tasks"]] == [tid]
    finally:
        reset_bot_id(designer)


# -- update / reinforce preserve ------------------------------------------------

def test_update_and_reinforce_keep_task_fields(tmp_path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    tid = facade.new_task_id()
    first = facade.remember("one", "alpha", now=at(1), task=tid)
    sid = facade.remember("two", "beta", now=at(1), task=tid, cont=first,
                           kind="habit", status="open", habit="label")
    facade.update(sid, topic="two renamed", now=at(2))
    facade.reinforce(sid, now=at(2))
    meta = parse_heading(next(
        line for line in (tmp_path / "memory" / "2026-09-01.md").read_text(
            encoding="utf-8").splitlines() if sid.split("#")[1] in line
    ))
    assert meta is not None
    assert (meta.task, meta.cont, meta.kind, meta.status, meta.habit) == (
        tid, first, "habit", "open", "label")
    facade.update(sid, status="done", now=at(3))
    facade._refresh_index(at(5))
    assert facade.search("beta", now=at(5), status="done").hits
    plain = facade.remember("plain", "no task here", now=at(1))
    with pytest.raises(Exception, match="status requires task"):
        facade.update(plain, status="done", now=at(3))


# -- tool contract ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_tool_remember_new_task_and_continue(tmp_path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    registry = ToolRegistry()
    register_memory_tools(registry, facade)
    gateway = ToolGateway(registry, TaskStore())

    async def call(name, args):
        result = await gateway.submit(ToolCall(id=name, name=name, arguments=args))
        assert not result.is_error, result.content
        return json.loads(result.content)

    created = await call("memory_remember", {
        "topic": "goal", "summary": "start work", "new_task": True})
    assert created["task_id"].startswith("task-")
    assert created["session_id"][10] == "#"
    second = await call("memory_remember", {
        "topic": "goal", "summary": "more work",
        "task": created["task_id"], "cont": created["session_id"]})
    assert second["task_id"] == created["task_id"]
    # Catalog listing over closed days (entries seeded via the facade
    # with explicit dates; the tool itself stamps real now).
    tid = facade.new_task_id()
    facade.remember("past goal", "past work", now=at(1), task=tid,
                      importance=5)
    facade._refresh_index(at(5))
    catalog = await call("memory_tasks", {})
    # The same-day tool task is listed too (Finding 2: today's daily
    # file merges into the catalog), newest first.
    assert [t["task_id"] for t in catalog["tasks"]] == [created["task_id"], tid]
    assert catalog["tasks"][1]["entry_count"] == 1


@pytest.mark.asyncio
async def test_tool_remember_rejects_task_plus_new_task(tmp_path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    registry = ToolRegistry()
    register_memory_tools(registry, facade)
    gateway = ToolGateway(registry, TaskStore())
    result = await gateway.submit(ToolCall(
        id="r", name="memory_remember",
        arguments={"topic": "t", "summary": "s",
                   "task": "task-1234abcd", "new_task": True}))
    assert result.is_error
    assert "new_task cannot be combined with task" in result.content
