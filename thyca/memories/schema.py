"""SQLite schema versioning for the archived leaf index (stdlib-only deps).

DDL lives in ``schema.sql`` (tables) and ``schema_fts.sql`` (FTS + triggers).
This module owns version checks and migrations. Query I/O stays in
``archive_store.py`` — the store delegates here from a thin guarded wrapper
so lock/error semantics do not change.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = "10"
# Bump when Chunker.normalize changes so stale text_norm rows rebuild.
NORM_VERSION = "2"
_SCHEMA_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = _SCHEMA_DIR / "schema.sql"
SCHEMA_FTS_PATH = _SCHEMA_DIR / "schema_fts.sql"


def _read_sql(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _apply_base_schema(db: sqlite3.Connection) -> None:
    db.executescript(_read_sql(SCHEMA_PATH))
    db.executescript(_read_sql(SCHEMA_FTS_PATH))


def init_schema(db: sqlite3.Connection) -> None:
    """Create or migrate schema tables in ``db`` (caller holds the lock)."""
    _apply_base_schema(db)
    row = db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    if row is None:
        db.execute(
            "INSERT INTO meta(key, value) VALUES ('schema_version', ?)",
            (SCHEMA_VERSION,),
        )
        db.commit()
    elif row["value"] != SCHEMA_VERSION:
        migrate(db, row["value"])
    # Column backfill for unreleased intermediate schemas (phase 1E): a
    # v8 database written before habit_explicit existed reports version
    # "8", so migrate() never runs for it — ensure the columns anyway.
    # No stale-marking here: a new column reads correctly (False/NULL)
    # on old rows without any reindex.
    ensure_v8_columns(db)
    # The script cannot create these indexes itself: on an existing pre-v8
    # database the chunks table has no bots/task column until migrate() adds it.
    db.execute("CREATE INDEX IF NOT EXISTS chunks_bots ON chunks(bots)")
    db.execute("CREATE INDEX IF NOT EXISTS chunks_task ON chunks(task)")
    db.execute("CREATE INDEX IF NOT EXISTS chunks_memory ON chunks(memory_id)")
    ensure_norm_version(db)


def _drop_fts(db: sqlite3.Connection) -> None:
    for trigger in ("chunks_ai", "chunks_ad", "chunks_au"):
        db.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    db.execute("DROP TABLE IF EXISTS chunks_fts")


def rebuild_fts_v10(db: sqlite3.Connection) -> None:
    """Recreate chunks_fts + triggers from ``schema_fts.sql``, then rebuild."""
    _drop_fts(db)
    db.executescript(_read_sql(SCHEMA_FTS_PATH))
    db.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")


def migrate(db: sqlite3.Connection, from_version: str) -> None:
    if from_version == "9":
        rebuild_fts_v10(db)
        db.execute(
            "UPDATE meta SET value = ? WHERE key = 'schema_version'",
            (SCHEMA_VERSION,),
        )
        db.commit()
        return
    if from_version == "8":
        # Non-destructive v8 → v10: drop old triggers/FTS before renaming
        # columns, then rename (session_id → memory_id, session_title →
        # memory_title) and rebuild the 3-column FTS index.
        _drop_fts(db)
        ensure_memory_columns(db)
        rebuild_fts_v10(db)
        db.execute(
            "UPDATE meta SET value = ? WHERE key = 'schema_version'",
            (SCHEMA_VERSION,),
        )
        db.commit()
        return
    if from_version == "7":
        # Non-destructive v7 → v10: task/habit columns (v8), leaf id
        # rename (v9), and 3-column FTS rebuild (v10).
        _drop_fts(db)
        add_task_columns(db)
        ensure_memory_columns(db)
        rebuild_fts_v10(db)
        db.execute(
            "UPDATE meta SET value = ? WHERE key = 'schema_version'",
            (SCHEMA_VERSION,),
        )
        db.commit()
        return
    if from_version == "6":
        # Non-destructive v6 → v10: team owners column + related-edges
        # drop, task/habit columns, leaf id rename, and FTS rebuild.
        _drop_fts(db)
        add_team_columns(db)
        add_task_columns(db)
        ensure_memory_columns(db)
        rebuild_fts_v10(db)
        db.execute(
            "UPDATE meta SET value = ? WHERE key = 'schema_version'",
            (SCHEMA_VERSION,),
        )
        db.commit()
        return
    if from_version in {"3", "4", "5"}:
        # CREATE IF NOT EXISTS does not alter the existing chunks table.
        # Add the v6 columns explicitly for every non-destructive upgrade
        # path, including databases that skipped an intermediate release.
        _drop_fts(db)
        add_linking_columns(db)
        add_team_columns(db)
        add_task_columns(db)
        ensure_memory_columns(db)
        if from_version in {"3", "4"}:
            db.execute(
                """CREATE TABLE IF NOT EXISTS leaf_searches(
                    chunk_id        TEXT PRIMARY KEY,
                    memory_id      TEXT NOT NULL,
                    search_count    INTEGER NOT NULL CHECK(search_count >= 1),
                    last_search_at  TEXT NOT NULL
                )"""
            )
        rebuild_fts_v10(db)
        db.execute(
            "UPDATE meta SET value = ? WHERE key = 'schema_version'",
            (SCHEMA_VERSION,),
        )
        db.commit()
        return
    if from_version not in {"1", "2"}:
        from thyca.memories.archive_store import ArchiveError

        raise ArchiveError(f"unsupported schema_version {from_version!r}")
    for trigger in ("chunks_ai", "chunks_ad", "chunks_au"):
        db.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    for index in ("chunks_day", "chunks_path", "chunks_content_hash", "chunks_profile"):
        db.execute(f"DROP INDEX IF EXISTS {index}")
    db.execute("DROP TABLE IF EXISTS chunks_fts")
    db.execute("DROP TABLE IF EXISTS chunks")
    db.execute("DELETE FROM source_files")
    _apply_base_schema(db)
    db.execute(
        "UPDATE meta SET value = ? WHERE key = 'schema_version'",
        (SCHEMA_VERSION,),
    )
    db.commit()


def add_linking_columns(db: sqlite3.Connection) -> None:
    columns = {str(row["name"]) for row in db.execute("PRAGMA table_info(chunks)")}
    for name in ("project", "chat_session"):
        if name not in columns:
            db.execute(f"ALTER TABLE chunks ADD COLUMN {name} TEXT")
    # Existing rows contain no heading metadata. Keep the derived rows
    # until the normal reindex pass, but ensure unchanged files are read.
    db.execute("UPDATE source_files SET mtime_ns = -1")


def add_team_columns(db: sqlite3.Connection) -> None:
    """v6 → v7 team-memory column (idempotent).

    Phase 1B removed the related-edges graph table; this only ensures
    the bots column from here on."""
    columns = {str(row["name"]) for row in db.execute("PRAGMA table_info(chunks)")}
    if "bots" not in columns:
        db.execute("ALTER TABLE chunks ADD COLUMN bots TEXT")
    # Existing rows contain no heading metadata. Keep the derived rows
    # until the normal reindex pass, but ensure unchanged files are read.
    db.execute("UPDATE source_files SET mtime_ns = -1")


V8_COLUMNS = (
    ("task", "TEXT"),
    ("cont", "TEXT"),
    ("kind", "TEXT"),
    ("status", "TEXT"),
    ("habit", "TEXT"),
    ("habit_explicit", "INTEGER NOT NULL DEFAULT 0"),
)


def ensure_v8_columns(db: sqlite3.Connection) -> None:
    """Idempotent v8 column backfill with no side effects."""
    columns = {str(row["name"]) for row in db.execute("PRAGMA table_info(chunks)")}
    for name, ddl in V8_COLUMNS:
        if name not in columns:
            db.execute(f"ALTER TABLE chunks ADD COLUMN {name} {ddl}")
    db.commit()


def add_task_columns(db: sqlite3.Connection) -> None:
    """v7 → v8 task/habit columns + task index (idempotent).

    Also drops the phase-1B-removed related-edges graph table (and its
    index with it), so every path landing on v8 ends without it."""
    ensure_v8_columns(db)
    db.execute("DROP TABLE IF EXISTS related_edges")
    db.execute("CREATE INDEX IF NOT EXISTS chunks_task ON chunks(task)")
    # Existing rows contain no heading metadata. Keep the derived rows
    # until the normal reindex pass, but ensure unchanged files are read.
    db.execute("UPDATE source_files SET mtime_ns = -1")


def ensure_memory_columns(db: sqlite3.Connection) -> None:
    """Idempotent v9 leaf id rename (session_id → memory_id).

    RENAME COLUMN keeps chunk and usage-counter rows in place; indexed
    files are marked stale so the next reindex rebuilds them from
    markdown with the new column names."""

    def _columns(table: str) -> set[str]:
        return {str(row["name"]) for row in db.execute(f"PRAGMA table_info({table})")}

    for table in ("chunks", "leaf_gets", "leaf_searches"):
        columns = _columns(table)
        if "session_id" in columns and "memory_id" not in columns:
            db.execute(
                f"ALTER TABLE {table} RENAME COLUMN session_id TO memory_id")
    columns = _columns("chunks")
    if "session_title" in columns and "memory_title" not in columns:
        db.execute("ALTER TABLE chunks RENAME COLUMN session_title TO memory_title")
    db.execute("UPDATE source_files SET mtime_ns = -1")


def ensure_norm_version(db: sqlite3.Connection) -> None:
    row = db.execute("SELECT value FROM meta WHERE key='norm_version'").fetchone()
    if row is not None and row["value"] == NORM_VERSION:
        return
    # Mark indexed files stale so the next reindex rewrites text_norm.
    # Keep derived rows until then; usage counters are not FK-bound.
    db.execute("UPDATE source_files SET mtime_ns = -1")
    db.execute("DELETE FROM meta WHERE key='norm_version'")
    db.execute(
        "INSERT INTO meta(key, value) VALUES ('norm_version', ?)",
        (NORM_VERSION,),
    )
    db.commit()
