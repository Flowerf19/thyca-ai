from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path as _Path

import pytest

from thyca.core.protocol import ToolCall
from thyca.memories.archive_store import ArchiveError
from thyca.memories.archived import SCHEMA_VERSION
from thyca.memories.chunk import Chunker
from thyca.memories.facade import MemoryFacade, format_task_catalog
from thyca.memories.heading import (
    HeadingMeta,
    new_task_id,
    parse_heading,
    render_heading,
    validate_task_meta,
)
from thyca.memories.identity import bind_bot_id, reset_bot_id
from thyca.tools.gateway import ToolGateway
from thyca.tools.memory_tools import (
    bind_chat_conversation,
    register_memory_tools,
    reset_chat_conversation,
)
from thyca.tools.registry import ToolRegistry
from thyca.tools.task_store import TaskStore

TZ = "Asia/Ho_Chi_Minh"


def at(day: int, hour: int = 12) -> datetime:
    return datetime(2026, 9, day, hour, 0, tzinfo=UTC)


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
    with pytest.raises(ValueError, match=r"checkpoint.*requires task"):
        validate_task_meta(None, None, "checkpoint", None, None)
    with pytest.raises(ValueError, match="does not take a habit"):
        validate_task_meta("task-1234abcd", None, "checkpoint", None, "label")
    validate_task_meta("task-1234abcd", "2026-09-01#deadbeef", "habit", "open", "label")
    validate_task_meta("task-1234abcd", None, "checkpoint", "open", None)
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
    assert {h.memory_id for h in facade.search("uniquetoken", now=now, task=tid).hits} \
        == {h.memory_id for h in facade.search("uniquetoken", now=now).hits
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
    second = next(h for h in hits if h.memory_id == second_sid)
    assert second.cont == first


def test_checkpoint_roundtrip_search_catalog_and_rebuild(tmp_path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    tid = facade.new_task_id()
    first = facade.remember("goal", "start checkpointtoken", now=at(1),
                            task=tid, status="open")
    checkpoint = facade.remember(
        "goal", "state checkpointtoken next step", now=at(2), task=tid,
        cont=first, kind="checkpoint", status="open")
    facade._refresh_index(at(5))
    hits = facade.search("checkpointtoken", now=at(5), kind="checkpoint").hits
    assert len(hits) == 1
    hit = hits[0]
    assert (hit.memory_id, hit.task, hit.cont, hit.kind, hit.status) == (
        checkpoint, tid, first, "checkpoint", "open")
    entry = facade.tasks(now=at(5))["tasks"][0]
    assert entry["latest_memory_id"] == checkpoint
    assert entry["entry_count"] == 2
    (tmp_path / "memory.sqlite").unlink()
    rebuilt = MemoryFacade(tmp_path, timezone_name=TZ)
    rebuilt._refresh_index(at(5))
    rebuilt_hits = rebuilt.search(
        "checkpointtoken", now=at(5), kind="checkpoint").hits
    assert len(rebuilt_hits) == 1
    assert rebuilt_hits[0].cont == first


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
    facade.remember("again", "reopened", now=at(4), task=tid, status="open")
    facade._refresh_index(at(5))
    assert facade.tasks(now=at(5))["tasks"][0]["status"] == "open"
    other = facade.new_task_id()
    facade.remember("o1", "one", now=at(1), task=other, status="open")
    facade.remember("o2", "two", now=at(2), task=other, status="done")
    facade.remember("o3", "three", now=at(3), task=other)
    facade._refresh_index(at(5))
    by_id = {t["task_id"]: t for t in facade.tasks(now=at(5))["tasks"]}
    assert by_id[other]["status"] == "done"


def test_tasks_latest_memory_id_tracks_newest_entry(tmp_path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    tid = facade.new_task_id()
    first = facade.remember("start", "kickoff", now=at(1), task=tid)
    second = facade.remember("middle", "progress", now=at(3), task=tid)
    facade._refresh_index(at(5))
    entry = facade.tasks(now=at(5))["tasks"][0]
    assert entry["latest_memory_id"] == second
    third = facade.remember("today", "doing", now=at(5), task=tid)
    assert facade.tasks(now=at(5))["tasks"][0]["latest_memory_id"] == third
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


V7_NEW_COLUMNS = ("task", "cont", "kind", "status", "habit")
V6_NEW_COLUMNS = ("bots", *V7_NEW_COLUMNS)


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
    """Current schema.sql + schema_fts.sql minus columns (and optionally the
    graph table) an old release never had — keeps triggers/FTS in sync like
    a real old DB. Leaf id columns read back as their pre-v9 names
    (``session_id`` / ``session_title``) so fixtures rebuild the
    genuine old schema the migration has to rename."""

    root = _Path(__file__).parent.parent / "thyca" / "memories"
    script = (
        (root / "schema.sql").read_text(encoding="utf-8")
        + "\n"
        + (root / "schema_fts.sql").read_text(encoding="utf-8")
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
        out.append(V7_GRAPH_DDL)
    joined = "".join(out)
    joined = joined.replace("memory_id", "session_id")
    joined = joined.replace("memory_title", "session_title")
    return joined


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
            "SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == SCHEMA_VERSION
        cols = {row[1] for row in db.execute("PRAGMA table_info(chunks)")}
        assert {"task", "cont", "kind", "status", "habit"} <= cols
        row = db.execute(
            "SELECT text_raw, task FROM chunks WHERE chunk_id='2026-09-01#deadbeef#1'"
        ).fetchone()
        assert row[0] == "- old body text" and row[1] is None
    finally:
        db.close()
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
            "SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == SCHEMA_VERSION
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
            "SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == SCHEMA_VERSION
        cols = {row[1] for row in db.execute("PRAGMA table_info(chunks)")}
        assert {"bots", "task", "cont", "kind", "status", "habit"} <= cols
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "related_edges" not in tables
        row = db.execute(
            "SELECT text_raw, bots, task FROM chunks "
            "WHERE chunk_id='2026-09-01#deadbeef#1'"
        ).fetchone()
        assert row[0] == "- old body text" and row[1] is None and row[2] is None
        indexes = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        assert "chunks_task" in indexes
    finally:
        db.close()
    facade._refresh_index(at(5))
    assert len(facade.search("old body", now=at(5)).hits) == 1


def test_migration_v8_to_v9_renames_id_and_keeps_usage(tmp_path) -> None:
    """Genuine pre-v9 database: leaf id columns still named session_*.

    RENAME COLUMN must carry chunk + usage rows over, so get/search
    counters written before the upgrade still count afterwards."""
    (tmp_path / "memory").mkdir(parents=True)
    (tmp_path / "memory" / "2026-09-01.md").write_text(
        "# 2026-09-01\n"
        "## 08:00 — old topic <!-- thyca {\"id\":\"deadbeef\",\"imp\":3} -->\n"
        "- old body text\n",
        encoding="utf-8",
    )
    db_path = tmp_path / "memory.sqlite"
    db = sqlite3.connect(db_path)
    db.executescript(_schema_without())
    db.execute("INSERT INTO meta VALUES ('schema_version', '8')")
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
    db.execute(
        "INSERT INTO leaf_gets VALUES (?, ?, 7, '2026-09-02T00:00:00Z')",
        ("2026-09-01#deadbeef#1", "2026-09-01#deadbeef"),
    )
    db.execute(
        "INSERT INTO leaf_searches VALUES (?, ?, 3, '2026-09-03T00:00:00Z')",
        ("2026-09-01#deadbeef#1", "2026-09-01#deadbeef"),
    )
    db.commit()
    db.close()
    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    facade._refresh_index(at(5))
    db = sqlite3.connect(db_path)
    try:
        assert db.execute(
            "SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == SCHEMA_VERSION
        cols = {row[1] for row in db.execute("PRAGMA table_info(chunks)")}
        assert "memory_id" in cols and "memory_title" in cols
        assert "session_id" not in cols and "session_title" not in cols
        row = db.execute("SELECT get_count, memory_id FROM leaf_gets").fetchone()
        assert row == (7, "2026-09-01#deadbeef")
        search_row = db.execute(
            "SELECT search_count, memory_id FROM leaf_searches").fetchone()
        assert search_row == (3, "2026-09-01#deadbeef")
    finally:
        db.close()
    assert facade.get(memory_id="2026-09-01#deadbeef", now=at(5)) is not None


def test_tasks_lists_task_opened_today(tmp_path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    tid = facade.new_task_id()
    facade.remember("today goal", "doing things today", now=at(3), task=tid)
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

    with pytest.raises(ArchiveError, match="does not take a habit"):
        facade.update(sid, kind="checkpoint", now=at(3))
    facade.update(first, kind="checkpoint", now=at(3))
    facade._refresh_index(at(5))
    assert facade.search("alpha", now=at(5), kind="checkpoint").hits
    plain = facade.remember("plain", "no task here", now=at(1))
    with pytest.raises(ArchiveError, match=r"checkpoint.*requires task"):
        facade.update(plain, kind="checkpoint", now=at(3))
    with pytest.raises(ArchiveError, match="status requires task"):
        facade.update(plain, status="done", now=at(3))


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
    assert created["memory_id"][10] == "#"
    second = await call("memory_remember", {
        "topic": "goal", "summary": "more work",
        "task": created["task_id"], "cont": created["memory_id"]})
    assert second["task_id"] == created["task_id"]
    tid = facade.new_task_id()
    facade.remember("past goal", "past work", now=at(1), task=tid,
                      importance=5)
    facade._refresh_index(at(5))
    catalog = await call("memory_tasks", {})
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


@pytest.mark.asyncio
async def test_tool_checkpoint_contract(tmp_path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    registry = ToolRegistry()
    register_memory_tools(registry, facade)
    gateway = ToolGateway(registry, TaskStore())

    async def submit(name, args):
        return await gateway.submit(ToolCall(id=name, name=name, arguments=args))

    tok_a = bind_chat_conversation("room-a")
    try:
        created_result = await submit("memory_remember", {
            "topic": "goal", "summary": "state one; next two",
            "new_task": True, "kind": "checkpoint"})
        assert not created_result.is_error, created_result.content
        created = json.loads(created_result.content)
        second_result = await submit("memory_remember", {
            "topic": "goal", "summary": "state two; next three",
            "task": created["task_id"], "cont": created["memory_id"],
            "kind": "checkpoint"})
        assert not second_result.is_error, second_result.content
        second = json.loads(second_result.content)
        assert second["memory_id"] == created["memory_id"]
    finally:
        reset_chat_conversation(tok_a)
    got = await submit("memory_get", {"memory_id": second["memory_id"]})
    assert not got.is_error
    assert "state two; next three" in got.content
    catalog = json.loads((await submit("memory_tasks", {})).content)
    entry = next(t for t in catalog["tasks"] if t["task_id"] == created["task_id"])
    assert entry["entry_count"] == 1
    assert entry["latest_memory_id"] == second["memory_id"]

    tok_b = bind_chat_conversation("room-b")
    try:
        cross_result = await submit("memory_remember", {
            "topic": "goal", "summary": "dm secret checkpoint",
            "task": created["task_id"], "cont": created["memory_id"],
            "kind": "checkpoint"})
        assert not cross_result.is_error, cross_result.content
        cross = json.loads(cross_result.content)
        assert cross["memory_id"] != created["memory_id"]
    finally:
        reset_chat_conversation(tok_b)
    catalog2 = json.loads((await submit("memory_tasks", {})).content)
    entry2 = next(t for t in catalog2["tasks"] if t["task_id"] == created["task_id"])
    assert entry2["entry_count"] == 2
    assert "dm secret checkpoint" not in (await submit("memory_get", {"memory_id": created["memory_id"]})).content

    no_task = await submit("memory_remember", {
        "topic": "orphan", "summary": "state", "kind": "checkpoint"})
    assert no_task.is_error
    assert "checkpoint" in no_task.content and "requires task" in no_task.content
    with_habit = await submit("memory_remember", {
        "topic": "goal", "summary": "state", "task": created["task_id"],
        "kind": "checkpoint", "habit": "label"})
    assert with_habit.is_error
    assert "does not take a habit" in with_habit.content


def test_status_cancelled_validation_search_and_catalog(tmp_path) -> None:

    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    validate_task_meta("task-1234abcd", None, "checkpoint", "cancelled", None)
    tid_open = facade.new_task_id()
    tid_cancel = facade.new_task_id()
    open_sid = facade.remember("active goal", "step one", now=at(1), task=tid_open, status="open")
    cancel_sid = facade.remember("dropped goal", "abandoned tokenxyz", now=at(1), task=tid_cancel, status="open")
    facade.update(cancel_sid, status="cancelled", now=at(2))
    facade._refresh_index(at(5))

    assert [t["task_id"] for t in facade.tasks(status="open", now=at(5))["tasks"]] == [tid_open]
    assert [t["task_id"] for t in facade.tasks(status="cancelled", now=at(5))["tasks"]] == [tid_cancel]
    assert len(facade.search("tokenxyz", now=at(5), status="cancelled").hits) == 1

    all_rows = facade.tasks(now=at(5))["tasks"]
    rendered = format_task_catalog(all_rows)
    assert tid_open in rendered
    assert open_sid in rendered
    assert "step one" in rendered
    assert tid_cancel not in rendered


def test_upsert_task_card_updates_in_place_across_days(tmp_path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    tid = facade.new_task_id()
    first = facade.upsert_task_card(
        "Build L2 optimize",
        "Initial setup",
        content="- done: schema checked\n- processing: writer\n- next: tests",
        task=tid,
        status="open",
        now=at(1),
    )
    second = facade.upsert_task_card(
        "progress",
        "Writer done",
        content="- done: schema + writer\n- processing: jobs\n- next: tests",
        task=tid,
        status="done",
        now=at(3),
    )
    assert second == first
    assert not (tmp_path / "memory" / "2026-09-03.md").exists()
    day1_text = (tmp_path / "memory" / "2026-09-01.md").read_text(encoding="utf-8")
    assert day1_text.count("## ") == 1
    assert "Build L2 optimize" in day1_text
    assert "Writer done" in day1_text
    assert "- done: schema + writer" in day1_text
    catalog = facade.tasks(now=at(5))["tasks"]
    assert len(catalog) == 1
    assert catalog[0]["entry_count"] == 1
    assert catalog[0]["status"] == "done"
    assert catalog[0]["last_day"] == "2026-09-03"
    assert catalog[0]["summary"] == "Writer done"
    assert [
        t["task_id"]
        for t in facade.tasks(date_from="2026-09-03", now=at(5))["tasks"]
    ] == [tid]


def test_upsert_task_card_isolates_cross_window_dm(tmp_path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    tid = facade.new_task_id()
    group_sid = facade.upsert_task_card(
        "Group goal", "Public step", task=tid, chat="group-1", bots=["coder"], now=at(1)
    )
    dm_sid = facade.upsert_task_card(
        "DM progress", "DM_SECRET_STEP", task=tid, chat="coder", bots=["coder"], now=at(2)
    )
    assert dm_sid != group_sid
    day1_text = (tmp_path / "memory" / "2026-09-01.md").read_text(encoding="utf-8")
    day2_text = (tmp_path / "memory" / "2026-09-02.md").read_text(encoding="utf-8")
    assert "DM_SECRET_STEP" not in day1_text
    assert "Public step" in day1_text
    assert "DM_SECRET_STEP" in day2_text
    dm_meta = parse_heading(next(line for line in day2_text.splitlines() if line.startswith("## ")))
    assert dm_meta is not None
    assert dm_meta.chat == "coder"
    assert dm_meta.cont == group_sid


def test_record_habit_entry_dedupes_same_day_and_upgrades_explicit(tmp_path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    token = bind_bot_id("coder")
    try:
        sid1 = facade.record_habit_entry(
            "Uống trà", "Uống trà buổi sáng", habit="Uống  trà sáng", now=at(2)
        )
        sid2 = facade.record_habit_entry(
            "Uống trà", "User tự nói thích uống trà sáng",
            habit="uong tra sang", habit_explicit=True, now=at(2, 15),
        )
    finally:
        reset_bot_id(token)
    assert sid1 == sid2
    day_text = (tmp_path / "memory" / "2026-09-02.md").read_text(encoding="utf-8")
    assert day_text.count("## ") == 1
    meta = next(m for line in day_text.splitlines() if (m := parse_heading(line)))
    assert meta.habit_explicit is True


def test_append_other_journal_groups_scope_and_skips_duplicates(tmp_path) -> None:

    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    sid1 = facade.append_other_journal(
        "Môi trường", "Máy chạy Fedora Bazzite", chat="room-a", bots=["coder"], now=at(2, 10)
    )
    sid_dup = facade.append_other_journal(
        "Môi trường", "may chay fedora bazzite!", chat="room-a", bots=["coder"], now=at(2, 11)
    )
    sid2 = facade.append_other_journal(
        "CI Fedora", "CI cần cài util-linux", content="Cho script test",
        chat="room-a", bots=["coder"], now=at(2, 12)
    )
    sid_fuzzy = facade.append_other_journal(
        "CI Fedora", "CI trên Fedora cần cài thêm util-linux",
        chat="room-a", bots=["coder"], now=at(2, 12)
    )
    sid_same_topic = facade.append_other_journal(
        "CI Fedora", "Runner bật cache pip", content="Giảm thời gian build",
        chat="room-a", bots=["coder"], now=at(2, 12)
    )
    sid_other_room = facade.append_other_journal(
        "Phòng khác", "Fact riêng phòng B", chat="room-b", bots=["coder"], now=at(2, 13)
    )
    assert sid1 == sid_dup
    assert sid2 != sid1
    assert sid2 == sid_fuzzy == sid_same_topic
    assert sid_other_room not in {sid1, sid2}

    path = tmp_path / "memory" / "2026-09-02.md"
    day_text = path.read_text(encoding="utf-8")
    assert day_text.count("## ") == 3
    assert "Môi trường · CI Fedora" not in day_text
    assert day_text.count("Máy chạy Fedora Bazzite") == 1
    assert "CI trên Fedora cần cài thêm util-linux" not in day_text
    assert "  - 19:00 — Runner bật cache pip" in day_text
    assert "    Giảm thời gian build" in day_text

    chunks = Chunker().chunk_markdown(path, day_text, source_kind="daily", timeline_day="2026-09-02")
    room_a_ci_chunks = [c for c in chunks if c.memory_id == sid2]
    assert len(room_a_ci_chunks) == 1

    facade.forget(sid2, now=at(2, 14))
    after_forget = path.read_text(encoding="utf-8")
    assert "Máy chạy Fedora Bazzite" in after_forget
    assert "CI cần cài util-linux" not in after_forget


def test_migration_v9_to_v10_keeps_usage_and_indexes_title_and_d_stroke(tmp_path) -> None:

    (tmp_path / "memory").mkdir(parents=True)
    day_file = tmp_path / "memory" / "2026-09-01.md"
    day_file.write_text(
        "# 2026-09-01\n"
        '## 08:00 — BazziteTitle <!-- thyca {"id":"deadbeef","imp":5} -->\n'
        "- Du lịch Đà Nẵng cuối tuần\n",
        encoding="utf-8",
    )
    st = day_file.stat()
    db_path = tmp_path / "memory.sqlite"
    db = sqlite3.connect(db_path)
    root = _Path(__file__).parent.parent / "thyca" / "memories"
    tables_sql = (root / "schema.sql").read_text(encoding="utf-8")
    v9_fts = (
        (root / "schema_fts.sql")
        .read_text(encoding="utf-8")
        .replace(
            "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(\n"
            "  text_raw,\n"
            "  memory_title,\n"
            "  text_norm,\n",
            "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(\n"
            "  text_raw,\n",
        )
        .replace(
            "INSERT INTO chunks_fts(rowid, text_raw, memory_title, text_norm)\n"
            "  VALUES (new.row_id, new.text_raw, new.memory_title, new.text_norm);",
            "INSERT INTO chunks_fts(rowid, text_raw) VALUES (new.row_id, new.text_raw);",
        )
        .replace(
            "INSERT INTO chunks_fts(chunks_fts, rowid, text_raw, memory_title, text_norm)\n"
            "  VALUES ('delete', old.row_id, old.text_raw, old.memory_title, old.text_norm);",
            "INSERT INTO chunks_fts(chunks_fts, rowid, text_raw) "
            "VALUES ('delete', old.row_id, old.text_raw);",
        )
    )
    db.executescript(tables_sql + "\n" + v9_fts)
    db.execute("INSERT INTO meta VALUES ('schema_version', '9')")
    db.execute("INSERT INTO meta VALUES ('norm_version', '2')")
    db.execute(
        "INSERT INTO source_files VALUES (?, 'daily', '2026-09-01', ?, ?)",
        (str(day_file), st.st_mtime_ns, st.st_size),
    )
    db.execute(
        """INSERT INTO chunks(chunk_id, path, source_kind, timeline_day, memory_id,
           memory_title, heading_raw, leaf_ord, line_start, line_end,
           text_raw, text_norm, content_hash, expires_at)
           VALUES ('2026-09-01#deadbeef#1', ?, 'daily', '2026-09-01',
           '2026-09-01#deadbeef', 'BazziteTitle', '## 08:00 — BazziteTitle',
           1, 2, 3, '- Du lịch Đà Nẵng cuối tuần', 'du lich da nang cuoi tuan',
           'hash', NULL)""",
        (str(day_file),),
    )
    db.execute(
        "INSERT INTO leaf_gets VALUES ('2026-09-01#deadbeef#1', '2026-09-01#deadbeef', 4, '2026-09-02T00:00:00Z')"
    )
    db.execute(
        "INSERT INTO leaf_searches VALUES ('2026-09-01#deadbeef#1', '2026-09-01#deadbeef', 7, '2026-09-02T00:00:00Z')"
    )
    db.commit()
    db.close()

    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    db = sqlite3.connect(db_path)
    try:
        assert db.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()[0] == SCHEMA_VERSION
        assert db.execute(
            "SELECT get_count FROM leaf_gets WHERE chunk_id='2026-09-01#deadbeef#1'"
        ).fetchone()[0] == 4
        assert db.execute(
            "SELECT search_count FROM leaf_searches WHERE chunk_id='2026-09-01#deadbeef#1'"
        ).fetchone()[0] == 7
    finally:
        db.close()
    assert facade.archive.fts_hits("BazziteTitle", None, at(5), 5)
    assert facade.archive.fts_hits("da", None, at(5), 5)


def test_upsert_task_card_does_not_overwrite_different_task_via_cont(tmp_path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    tid_a = facade.new_task_id()
    tid_b = facade.new_task_id()
    _sid_a = facade.upsert_task_card("Task A", "Summary A", task=tid_a, chat="room-x", now=at(1))
    sid_b = facade.upsert_task_card("Task B", "Summary B", task=tid_b, chat="room-x", now=at(1))

    sid_new_a = facade.upsert_task_card(
        "Work on A",
        "Updated A",
        task=tid_a,
        chat="room-x",
        target_memory_id=sid_b,
        now=at(1),
    )
    assert sid_new_a != sid_b
    day_text = (tmp_path / "memory" / "2026-09-01.md").read_text(encoding="utf-8")
    assert "Summary B" in day_text
    assert "Updated A" in day_text
    by_tid = {t["task_id"]: t for t in facade.tasks(now=at(5))["tasks"]}
    assert set(by_tid) == {tid_a, tid_b}
    assert by_tid[tid_b]["summary"] == "Summary B"


def test_append_other_journal_preserves_updates_and_dedupes_multiline_and_cross_day(tmp_path) -> None:
    facade = MemoryFacade(tmp_path, timezone_name=TZ)
    day1 = at(1)
    day2 = at(2)
    facade.append_other_journal("Version", "Project thyca version is 0.88.0.dev2 on branch main", chat="c1", now=day1)
    facade.append_other_journal("Version", "Project thyca version is 0.88.0.dev3 on branch main", chat="c1", now=day1)
    facade.append_other_journal("DB", "User switched database from PostgreSQL to SQLite", chat="c2", now=day1)
    facade.append_other_journal("DB", "User switched database from SQLite to PostgreSQL", chat="c2", now=day1)
    facade.append_other_journal("Docker", "User is not using docker for local dev", chat="c3", now=day1)
    facade.append_other_journal("Docker", "using docker for local dev", chat="c3", now=day1)
    facade.append_other_journal("Tabs", "Trong nhóm dùng tab bốn khoảng để code", chat="c3b", now=day1)
    facade.append_other_journal("Tabs", "Trong nhóm đừng dùng tab bốn khoảng để code", chat="c3b", now=day1)
    facade.append_other_journal("Tabs2", "dung tab bon khoang", chat="c3c", now=day1)
    facade.append_other_journal("Tabs2", "đừng tab bốn khoảng", chat="c3c", now=day1)
    facade.append_other_journal("Style", "Team coding standards for Python", content="Use ruff", chat="c6", now=day1)
    facade.append_other_journal("Style", "Team coding standards for Python", content="Use uv", chat="c6", now=day1)
    facade.append_other_journal("Deploy", "Backend deployment notes", chat="c7", now=day1)
    facade.append_other_journal("Deploy", "Backend deployment notes", content="Use systemd", chat="c7", now=day1)
    facade.append_other_journal(
        "Stack",
        "Backend architecture choices",
        content="Line one uses FastAPI\nLine two uses SQLite WAL\nLine three deploys on Linux",
        chat="c4",
        now=day1,
    )
    facade.append_other_journal(
        "Stack",
        "Backend architecture choices",
        content="Line one runs FastAPI\nLine two runs SQLite WAL\nLine three runs on Linux",
        chat="c4",
        now=day1,
    )
    sid_os_1 = facade.append_other_journal("OS", "User runs Fedora Bazzite Linux", chat="c5", now=day1)
    facade.append_other_journal("Tools", "Repo uses uv", chat="c5", now=day2)
    sid_os_2 = facade.append_other_journal("OS", "User runs Fedora Bazzite Linux", chat="c5", now=day2)

    d1_text = (tmp_path / "memory" / "2026-09-01.md").read_text(encoding="utf-8")
    d2_text = (tmp_path / "memory" / "2026-09-02.md").read_text(encoding="utf-8")
    assert "0.88.0.dev2" in d1_text and "0.88.0.dev3" in d1_text
    assert "from PostgreSQL to SQLite" in d1_text and "from SQLite to PostgreSQL" in d1_text
    assert "User is not using docker for local dev" in d1_text
    assert sum("using docker for local dev" in ln for ln in d1_text.splitlines()) == 2
    assert "nhóm dùng tab" in d1_text and "đừng dùng tab" in d1_text
    assert "dung tab bon khoang" in d1_text and "đừng tab bốn khoảng" in d1_text
    assert "Use ruff" in d1_text and "Use uv" in d1_text
    assert "Use systemd" in d1_text
    assert "Line one uses FastAPI" in d1_text
    assert "Line one runs FastAPI" not in d1_text
    assert sid_os_2 == sid_os_1
    assert "Repo uses uv" in d2_text
    assert "Fedora Bazzite Linux" not in d2_text
