"""I/O for leaf get/search counters. No FK to chunks."""
from __future__ import annotations

import sqlite3

_TABLES = {
    "leaf_gets": ("get_count", "last_get_at"),
    "leaf_searches": ("search_count", "last_search_at"),
}


class LeafUsage:
    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def get_map(self) -> dict[str, tuple[int, str]]:
        return self._map("leaf_gets")

    def search_map(self) -> dict[str, tuple[int, str]]:
        return self._map("leaf_searches")

    def record_gets(self, chunk_ids: list[str], session_id: str, now: str) -> None:
        self._record("leaf_gets", chunk_ids, session_id, now)

    def record_searches(self, chunk_ids: list[str], session_id: str, now: str) -> None:
        self._record("leaf_searches", chunk_ids, session_id, now)

    def keep_gets(self, chunk_ids: set[str]) -> None:
        self._keep("leaf_gets", chunk_ids)

    def keep_searches(self, chunk_ids: set[str]) -> None:
        self._keep("leaf_searches", chunk_ids)

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
