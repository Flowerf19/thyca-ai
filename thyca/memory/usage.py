"""I/O for leaf get/search counters. No FK to chunks."""
from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from functools import wraps

_TABLES = {
    "leaf_gets": ("get_count", "last_get_at"),
    "leaf_searches": ("search_count", "last_search_at"),
}


def is_lock_error(exc: sqlite3.OperationalError) -> bool:
    """True for lock/busy contention (cross-process), not for SQL bugs."""
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


def guarded(fn):
    """Serialize one shared-connection op; surface lock errors as the store error.

    ArchiveStore and LeafUsage share a single ``check_same_thread=False``
    connection, so every statement runs under the same lock — without it two
    threads BEGIN at once and SQLite reports "cannot start a transaction
    within a transaction". Lock/busy failures become the injected store error
    (ArchiveError); anything else propagates untouched.
    """

    @wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            try:
                return fn(self, *args, **kwargs)
            except sqlite3.OperationalError as exc:
                if self._error_cls is not None and is_lock_error(exc):
                    raise self._error_cls(f"archive database is locked: {exc}") from exc
                raise

    return wrapper


class LeafUsage:
    def __init__(
        self,
        db: sqlite3.Connection,
        lock: threading.RLock | None = None,
        error_cls: Callable[[str], Exception] | None = None,
    ) -> None:
        self._db = db
        self._lock = lock if lock is not None else threading.RLock()
        self._error_cls = error_cls

    @guarded
    def get_map(self) -> dict[str, tuple[int, str]]:
        return self._map("leaf_gets")

    @guarded
    def search_map(self) -> dict[str, tuple[int, str]]:
        return self._map("leaf_searches")

    @guarded
    def record_gets(self, chunk_ids: list[str], session_id: str, now: str) -> None:
        self._record("leaf_gets", chunk_ids, session_id, now)

    @guarded
    def record_searches(self, chunk_ids: list[str], session_id: str, now: str) -> None:
        self._record("leaf_searches", chunk_ids, session_id, now)

    @guarded
    def keep_gets(self, chunk_ids: set[str]) -> None:
        self._keep("leaf_gets", chunk_ids)

    @guarded
    def keep_searches(self, chunk_ids: set[str]) -> None:
        self._keep("leaf_searches", chunk_ids)

    @guarded
    def drop_ids(self, chunk_ids: list[str]) -> None:
        if not chunk_ids:
            return
        self._db.execute("BEGIN IMMEDIATE")
        try:
            self._delete_ids(chunk_ids)
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

    def _delete_ids(self, chunk_ids: list[str]) -> None:
        """Raw usage deletes for the caller's transaction (no BEGIN/COMMIT)."""
        rows = [(chunk_id,) for chunk_id in chunk_ids]
        for table in _TABLES:
            self._db.executemany(f"DELETE FROM {table} WHERE chunk_id = ?", rows)

    def _map(self, table: str) -> dict[str, tuple[int, str]]:
        count_col, time_col = _TABLES[table]
        return {
            str(row["chunk_id"]): (int(row[count_col]), str(row[time_col]))
            for row in self._db.execute(
                f"SELECT chunk_id, {count_col}, {time_col} FROM {table}"
            )
        }

    def _record(self, table: str, chunk_ids: list[str], session_id: str, now: str) -> None:
        if not chunk_ids:
            return
        count_col, time_col = _TABLES[table]
        self._db.execute("BEGIN IMMEDIATE")
        try:
            for chunk_id in chunk_ids:
                self._db.execute(
                    f"""INSERT INTO {table}(chunk_id, session_id, {count_col}, {time_col})
                        VALUES (?, ?, 1, ?)
                        ON CONFLICT(chunk_id) DO UPDATE SET
                          {count_col} = {count_col} + 1,
                          {time_col} = excluded.{time_col},
                          session_id = excluded.session_id""",
                    (chunk_id, session_id, now),
                )
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

    def _keep(self, table: str, chunk_ids: set[str]) -> None:
        if table not in _TABLES:
            raise ValueError(f"unknown usage table {table!r}")
        self._db.execute("BEGIN IMMEDIATE")
        try:
            if not chunk_ids:
                self._db.execute(f"DELETE FROM {table}")
            else:
                self._db.execute("DROP TABLE IF EXISTS temp.keep_ids")
                self._db.execute("CREATE TEMP TABLE keep_ids(chunk_id TEXT PRIMARY KEY)")
                self._db.executemany(
                    "INSERT INTO keep_ids(chunk_id) VALUES (?)",
                    [(chunk_id,) for chunk_id in chunk_ids],
                )
                self._db.execute(
                    f"DELETE FROM {table} WHERE chunk_id NOT IN (SELECT chunk_id FROM keep_ids)"
                )
                self._db.execute("DROP TABLE keep_ids")
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise
