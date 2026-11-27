"""SQLite I/O for the archived leaf index."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from thyca.memories.archive_queries import (
    BIGRAM_WEIGHT,  # noqa: F401 -- reason: re-exported
    BM25_WEIGHTS,  # noqa: F401 -- reason: re-exported
    CANDIDATE_CAP,  # noqa: F401 -- reason: re-exported
    DATE_RE,  # noqa: F401 -- reason: re-exported
    GET_MEMORY_CAP,  # noqa: F401 -- reason: re-exported
    TRIGRAM_FLOOR,  # noqa: F401 -- reason: re-exported
    TYPO_TRIGRAM_FLOOR,  # noqa: F401 -- reason: re-exported
    ArchiveError,
    Hit,
    SearchResult,  # noqa: F401 -- reason: re-exported
    decode_bots,
    encode_bots,
    fts_search_memories_rows,
    fts_search_rows,
    hit_from_row,
    reader_clause,
    trigram_search_rows,
)
from thyca.memories.chunk import Chunk, Chunker
from thyca.memories.heading import VISIBLE_SQL, format_ts
from thyca.memories.schema import (
    SCHEMA_VERSION,  # noqa: F401 -- reason: re-exported
    init_schema,
)
from thyca.memories.usage import LeafUsage, guarded

_hit_from_row = hit_from_row


class ArchiveStore:
    """SQLite I/O for the archived leaf index."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._lock = threading.RLock()
        self._error_cls = ArchiveError
        self._db = self._connect(db_path)
        self._init_schema()
        self.usage = LeafUsage(self._db, lock=self._lock, error_cls=ArchiveError)

    @guarded
    def close(self) -> None:
        self._db.close()

    def _connect(self, db_path: Path) -> sqlite3.Connection:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(db_path, check_same_thread=False)
        db.isolation_level = None
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        return db

    @guarded
    def _init_schema(self) -> None:
        init_schema(self._db)

    @guarded
    def replace_source(
        self,
        path: str,
        kind: str,
        day: str | None,
        mtime_ns: int,
        size: int,
        chunks: list[Chunk],
    ) -> None:
        self._db.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute("DELETE FROM source_files WHERE path = ?", (path,))
            self._db.execute(
                """INSERT INTO source_files(path, source_kind, timeline_day, mtime_ns, size_bytes)
                   VALUES (?, ?, ?, ?, ?)""",
                (path, kind, day, mtime_ns, size),
            )
            for chunk in chunks:
                self._db.execute(
                    """INSERT INTO chunks(
                        chunk_id, path, source_kind, timeline_day, memory_id, memory_title,
                        heading_raw, leaf_ord, line_start, line_end, text_raw, text_norm,
                        content_hash, expires_at, forgotten_at, project, chat_session, bots,
                        task, cont, kind, status, habit, habit_explicit
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        chunk.chunk_id,
                        chunk.path,
                        chunk.source_kind,
                        chunk.timeline_day,
                        chunk.memory_id,
                        chunk.memory_title,
                        chunk.heading_raw,
                        chunk.leaf_ord,
                        chunk.line_start,
                        chunk.line_end,
                        chunk.text_raw,
                        chunk.text_norm,
                        chunk.content_hash,
                        chunk.expires_at,
                        chunk.forgotten_at,
                        chunk.project,
                        chunk.chat_session,
                        encode_bots(chunk.bots),
                        chunk.task,
                        chunk.cont,
                        chunk.kind,
                        chunk.status,
                        chunk.habit,
                        1 if chunk.habit_explicit else 0,
                    ),
                )
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

    @guarded
    def drop_source(self, path: str) -> None:
        ids = [
            str(row["chunk_id"])
            for row in self._db.execute("SELECT chunk_id FROM chunks WHERE path = ?", (path,))
        ]
        self._db.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute("DELETE FROM source_files WHERE path = ?", (path,))
            self.usage._delete_ids(ids)
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

    @guarded
    def source_stat(self, path: str) -> tuple[int, int] | None:
        row = self._db.execute(
            "SELECT mtime_ns, size_bytes FROM source_files WHERE path = ?",
            (path,),
        ).fetchone()
        if row is None:
            return None
        return int(row["mtime_ns"]), int(row["size_bytes"])

    @guarded
    def fts_search(
        self,
        query: str,
        timeline_day: str | None,
        limit: int,
        now: str,
        *,
        project: str | None = None,
        chat_session: str | None = None,
        reader: str | None = None,
        task: str | None = None,
        kind: str | None = None,
        status: str | None = None,
    ) -> list[Hit]:
        return fts_search_rows(
            self._db,
            query,
            timeline_day,
            limit,
            now,
            project=project,
            chat_session=chat_session,
            reader=reader,
            task=task,
            kind=kind,
            status=status,
        )

    @guarded
    def fts_search_memories(
        self,
        query: str,
        timeline_day: str | None,
        limit: int,
        now: str,
        *,
        project: str | None = None,
        chat_session: str | None = None,
        reader: str | None = None,
        task: str | None = None,
        kind: str | None = None,
        status: str | None = None,
    ) -> tuple[list[Hit], int, bool]:
        """Search distinct ``memory_id`` sessions via unigram + bigram FTS5 fusion."""
        return fts_search_memories_rows(
            self._db,
            query,
            timeline_day,
            limit,
            now,
            project=project,
            chat_session=chat_session,
            reader=reader,
            task=task,
            kind=kind,
            status=status,
        )

    @guarded
    def trigram_search(
        self,
        query_norm: str,
        timeline_day: str | None,
        limit: int,
        now: str,
        *,
        project: str | None = None,
        chat_session: str | None = None,
        reader: str | None = None,
        task: str | None = None,
        kind: str | None = None,
        status: str | None = None,
    ) -> list[Hit]:
        return trigram_search_rows(
            self._db,
            query_norm,
            timeline_day,
            limit,
            now,
            project=project,
            chat_session=chat_session,
            reader=reader,
            task=task,
            kind=kind,
            status=status,
        )

    @guarded
    def get_chunk(self, chunk_id: str, now: str) -> sqlite3.Row | None:
        return self._db.execute(
            f"""SELECT * FROM chunks WHERE chunk_id = ?
               AND {VISIBLE_SQL}""",
            (chunk_id, now),
        ).fetchone()

    @guarded
    def get_memory(self, memory_id: str, now: str) -> list[sqlite3.Row]:
        return list(
            self._db.execute(
                f"""SELECT * FROM chunks WHERE memory_id = ?
                   AND {VISIBLE_SQL}
                   ORDER BY leaf_ord ASC""",
                (memory_id, now),
            )
        )

    @guarded
    def get_chunk_any(self, chunk_id: str) -> sqlite3.Row | None:
        """Raw chunk row ignoring visibility (T6a miss classification)."""
        return self._db.execute(
            "SELECT * FROM chunks WHERE chunk_id = ?", (chunk_id,)
        ).fetchone()

    @guarded
    def get_memory_any(self, memory_id: str) -> list[sqlite3.Row]:
        """Raw session rows ignoring visibility (T6a miss classification)."""
        return list(
            self._db.execute(
                """SELECT * FROM chunks WHERE memory_id = ?
                   ORDER BY leaf_ord ASC""",
                (memory_id,),
            )
        )

    @guarded
    def has_task(self, task: str) -> bool:
        """True when any chunk already carries this task id (phase 1A)."""
        row = self._db.execute(
            "SELECT 1 FROM chunks WHERE task = ? LIMIT 1",
            (task,),
        ).fetchone()
        return row is not None

    @guarded
    def task_memory_rows(self, now: str, *, reader: str | None = None) -> list[sqlite3.Row]:
        """One row per (task, session) for visible task entries (phase 1A)."""
        clause, clause_params = reader_clause("bots", reader)
        return list(
            self._db.execute(
                f"""SELECT task, memory_id,
                           MIN(timeline_day) AS first_day,
                           MAX(timeline_day) AS last_day,
                           MIN(line_start) AS first_line,
                           MAX(line_start) AS last_line,
                           MAX(memory_title) AS title,
                           MAX(status) AS status,
                           MAX(bots) AS bots,
                           MAX(chat_session) AS chat,
                           GROUP_CONCAT(text_raw, char(10)) AS text_raw
                    FROM chunks
                    WHERE task IS NOT NULL AND {VISIBLE_SQL}{clause}
                    GROUP BY task, memory_id""",
                (now, *clause_params),
            )
        )

    @guarded
    def habit_rows(self, now: str, *, reader: str | None = None) -> list[sqlite3.Row]:
        """One row per habit session for the sweep (phase 1E)."""
        clause, clause_params = reader_clause("bots", reader)
        return list(
            self._db.execute(
                f"""SELECT memory_id,
                           MIN(timeline_day) AS day,
                           MAX(habit) AS habit,
                           MAX(habit_explicit) AS explicit,
                           MAX(bots) AS bots,
                           MAX(chat_session) AS chat,
                           GROUP_CONCAT(text_raw, char(10)) AS text_raw
                    FROM chunks
                    WHERE kind = 'habit' AND {VISIBLE_SQL}{clause}
                    GROUP BY memory_id""",
                (now, *clause_params),
            )
        )

    @guarded
    def other_journal_rows(
        self,
        now: str,
        *,
        reader: str | None = None,
        chat_session: str | None = None,
        project: str | None = None,
    ) -> list[sqlite3.Row]:
        """One row per archived daily Other-journal session for cross-day dedupe."""
        clause, clause_params = reader_clause("bots", reader)
        params: list[object] = [now, *clause_params]
        sql = (
            "SELECT memory_id, MAX(bots) AS bots, MAX(chat_session) AS chat_session, "
            "MAX(project) AS project, MAX(timeline_day) AS day "
            "FROM chunks "
            f"WHERE source_kind = 'daily' AND task IS NULL AND kind IS NULL AND {VISIBLE_SQL}{clause}"
        )
        if chat_session is None:
            sql += " AND chat_session IS NULL"
        else:
            sql += " AND chat_session = ?"
            params.append(chat_session)
        if project is None:
            sql += " AND project IS NULL"
        else:
            sql += " AND project = ?"
            params.append(project)
        sql += " GROUP BY memory_id ORDER BY day DESC, memory_id DESC"
        return list(self._db.execute(sql, params))

    @guarded
    def memory_bots(self, memory_id: str) -> tuple[str, ...] | None:
        """Owner list of one memory session; None = legacy (team) entry."""
        row = self._db.execute(
            "SELECT bots FROM chunks WHERE memory_id = ? LIMIT 1",
            (memory_id,),
        ).fetchone()
        if row is None:
            return None
        return decode_bots(row["bots"])

    @guarded
    def recent_rows(self, limit: int, now: str, *, reader: str | None = None) -> list[sqlite3.Row]:
        clause, clause_params = reader_clause("c.bots", reader)
        return list(
            self._db.execute(
                f"""SELECT c.* FROM chunks c
                   JOIN source_files s ON s.path = c.path
                   WHERE {VISIBLE_SQL} AND c.source_kind = 'daily'{clause}
                   ORDER BY s.mtime_ns DESC, c.leaf_ord ASC
                   LIMIT ?""",
                (now, *clause_params, max(0, limit)),
            )
        )

    @guarded
    def list_paths(self) -> list[str]:
        return [row["path"] for row in self._db.execute("SELECT path FROM source_files")]

    @guarded
    def chunk_ids(self) -> list[str]:
        return [row["chunk_id"] for row in self._db.execute("SELECT chunk_id FROM chunks")]

    @guarded
    def rank_hays(self, chunk_ids: list[str]) -> dict[str, str]:
        """Normalized heading + text_norm for phrase ranking. Empty dict if no ids."""
        if not chunk_ids:
            return {}
        marks = ",".join("?" * len(chunk_ids))
        rows = self._db.execute(
            f"SELECT chunk_id, heading_raw, text_norm FROM chunks WHERE chunk_id IN ({marks})",
            chunk_ids,
        )
        return {
            row["chunk_id"]: f"{Chunker.normalize(row['heading_raw'])} {row['text_norm']}"
            for row in rows
        }

    @guarded
    def visible_chunk_maps(self, now: str) -> list[dict[str, object]]:
        rows = self._db.execute(
            f"""SELECT chunk_id, memory_id, heading_raw, text_raw, source_kind,
                      timeline_day, expires_at, bots, kind, task, status, habit
               FROM chunks
               WHERE {VISIBLE_SQL}""",
            (now,),
        )
        return [dict(row) for row in rows]

    @guarded
    def memory_leaf_count(self, memory_id: str, now: str | None = None) -> int:
        now_ts = now if now is not None else format_ts(None)
        row = self._db.execute(
            f"""SELECT COUNT(*) AS n FROM chunks WHERE memory_id = ?
               AND {VISIBLE_SQL}""",
            (memory_id, now_ts),
        ).fetchone()
        return int(row["n"]) if row else 0

    @guarded
    def memory_leaf_counts(self, memory_ids: list[str], now: str | None = None) -> dict[str, int]:
        """Visible chunk counts keyed by ``memory_id`` in one grouped SQL query."""
        unique = list(dict.fromkeys(memory_ids))
        if not unique:
            return {}
        now_ts = now if now is not None else format_ts(None)
        marks = ",".join("?" * len(unique))
        rows = self._db.execute(
            f"""SELECT memory_id, COUNT(*) AS n FROM chunks
                WHERE memory_id IN ({marks}) AND {VISIBLE_SQL}
                GROUP BY memory_id""",
            [*unique, now_ts],
        )
        return {str(row["memory_id"]): int(row["n"]) for row in rows}
