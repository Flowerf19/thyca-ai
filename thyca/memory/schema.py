"""SQLite schema versioning for the archived leaf index (stdlib-only deps).

Table creation lives in ``schema.sql``; this module owns version checks and
migrations. Query I/O stays in ``archive_store.py`` — the store delegates
here from a thin guarded wrapper so lock/error semantics do not change.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = "8"
# Bump when Chunker.normalize changes so stale text_norm rows rebuild.
NORM_VERSION = "2"
SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def init_schema(db: sqlite3.Connection) -> None:
    """Create or migrate schema tables in ``db`` (caller holds the lock)."""
    db.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
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
    ensure_norm_version(db)


def migrate(db: sqlite3.Connection, from_version: str) -> None:
    if from_version == "7":
        # Non-destructive v7 → v8: task/habit columns.
        add_task_columns(db)
        db.execute(
            "UPDATE meta SET value = ? WHERE key = 'schema_version'",
            (SCHEMA_VERSION,),
        )
        db.commit()
        return
    if from_version == "6":
        # Non-destructive v6 → v8: team owners column + related-edges
        # table (v7) plus task/habit columns (v8).
        add_team_columns(db)
        add_task_columns(db)
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
        add_linking_columns(db)
        add_team_columns(db)
        add_task_columns(db)
        if from_version in {"3", "4"}:
            db.execute(
                """CREATE TABLE IF NOT EXISTS leaf_searches(
                    chunk_id        TEXT PRIMARY KEY,
                    session_id      TEXT NOT NULL,
                    search_count    INTEGER NOT NULL CHECK(search_count >= 1),
                    last_search_at  TEXT NOT NULL
                )"""
            )
        db.execute(
            "UPDATE meta SET value = ? WHERE key = 'schema_version'",
            (SCHEMA_VERSION,),
        )
        db.commit()
        return
    if from_version not in {"1", "2"}:
        from thyca.memory.archive_store import ArchiveError

        raise ArchiveError(f"unsupported schema_version {from_version!r}")
    for trigger in ("chunks_ai", "chunks_ad", "chunks_au"):
        db.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    for index in ("chunks_day", "chunks_path", "chunks_content_hash", "chunks_profile"):
        db.execute(f"DROP INDEX IF EXISTS {index}")
    db.execute("DROP TABLE IF EXISTS chunks_fts")
    db.execute("DROP TABLE IF EXISTS chunks")
    db.execute("DELETE FROM source_files")
    db.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
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
