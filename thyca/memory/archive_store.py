"""SQLite I/O for the archived leaf index (split from archived.py)."""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz

from thyca.memory.chunk import Chunk
from thyca.memory.heading import VISIBLE_SQL, format_ts
from thyca.memory.schema import (
    SCHEMA_VERSION,  # noqa: F401 — re-exported
    init_schema,
)
from thyca.memory.usage import LeafUsage, guarded

TRIGRAM_FLOOR = 60
CANDIDATE_CAP = 50
GET_SESSION_CAP = 10
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NON_ALNUM = re.compile(r"[^\w]+", re.UNICODE)


class ArchiveError(RuntimeError):
    """Archived index could not be read or written."""


@dataclass(frozen=True)
class Hit:
    path: str
    source_kind: str
    chunk_id: str
    timeline_day: str | None
    session_id: str
    heading: str
    snippet: str
    score: float
    match_type: str
    bm25: float | None = None
    leaf_ord: int = 1
    session_leaf_count: int = 1
    has_more: bool = False
    line_start: int = 1
    line_end: int = 1
    # Team owners (BE-4): None = legacy team entry.
    bots: tuple[str, ...] | None = None
    # Task + habits (phase 1A): None = legacy entry without the key.
    task: str | None = None
    cont: str | None = None
    kind: str | None = None
    status: str | None = None
    habit: str | None = None
    # Phase 1E: user explicitly stated the habit.
    habit_explicit: bool = False


@dataclass
class SearchResult:
    hits: list[Hit] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


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
    def replace_source(self, path: str, kind: str, day: str | None, mtime_ns: int, size: int, chunks: list[Chunk]) -> None:
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
                        chunk_id, path, source_kind, timeline_day, session_id, session_title,
                        heading_raw, leaf_ord, line_start, line_end, text_raw, text_norm,
                        content_hash, expires_at, forgotten_at, project, chat_session, bots,
                        task, cont, kind, status, habit, habit_explicit
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        chunk.chunk_id,
                        chunk.path,
                        chunk.source_kind,
                        chunk.timeline_day,
                        chunk.session_id,
                        chunk.session_title,
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
        # One transaction: the cascade delete and the usage deletes commit
        # together, so a crash between them cannot orphan usage rows.
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
        match = _safe_match(query)
        if match is None:
            return []
        sql = f"""
            SELECT c.*, snippet(chunks_fts, 0, '⟨', '⟩', '…', 6) AS snippet,
                   bm25(chunks_fts) AS bm25
            FROM chunks_fts
            JOIN chunks c ON c.row_id = chunks_fts.rowid
            WHERE chunks_fts MATCH ?
              AND {VISIBLE_SQL}
        """
        params: list[object] = [match, now]
        if timeline_day is not None:
            sql += " AND c.timeline_day = ?"
            params.append(timeline_day)
        if project is not None:
            sql += " AND c.project = ?"
            params.append(project)
        if chat_session is not None:
            sql += " AND c.chat_session = ?"
            params.append(chat_session)
        if task is not None:
            sql += " AND c.task = ?"
            params.append(task)
        if kind is not None:
            sql += " AND c.kind = ?"
            params.append(kind)
        if status is not None:
            sql += " AND c.status = ?"
            params.append(status)
        clause, clause_params = _reader_clause("c.bots", reader)
        sql += clause
        params.extend(clause_params)
        sql += " ORDER BY bm25 ASC, c.chunk_id ASC LIMIT ?"
        params.append(max(0, min(limit, CANDIDATE_CAP)))
        rows = self._db.execute(sql, params).fetchall()
        return [hit_from_row(row, "fts", bm25=row["bm25"], snippet=row["snippet"]) for row in rows]

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
        tokens = [t for t in _NON_ALNUM.split(query_norm) if len(t) >= 3]
        sql = f"""SELECT * FROM chunks
                  WHERE {VISIBLE_SQL}"""
        params: list[object] = [now]
        if timeline_day is not None:
            sql += " AND timeline_day = ?"
            params.append(timeline_day)
        if project is not None:
            sql += " AND project = ?"
            params.append(project)
        if chat_session is not None:
            sql += " AND chat_session = ?"
            params.append(chat_session)
        if task is not None:
            sql += " AND task = ?"
            params.append(task)
        if kind is not None:
            sql += " AND kind = ?"
            params.append(kind)
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        clause, clause_params = _reader_clause("bots", reader)
        sql += clause
        params.extend(clause_params)
        scored: list[tuple[float, sqlite3.Row]] = []
        for row in self._db.execute(sql, params):
            text = row["text_norm"]
            if tokens and not any(t in text for t in tokens):
                continue
            score = float(fuzz.partial_ratio(query_norm, text))
            if score >= TRIGRAM_FLOOR:
                scored.append((score, row))
        scored.sort(key=lambda item: (-item[0], item[1]["chunk_id"]))
        return [
            hit_from_row(row, "trigram", score=score, snippet=row["text_raw"][:250])
            for score, row in scored[: max(0, min(limit, CANDIDATE_CAP))]
        ]

    @guarded
    def get_chunk(self, chunk_id: str, now: str) -> sqlite3.Row | None:
        return self._db.execute(
            f"""SELECT * FROM chunks WHERE chunk_id = ?
               AND {VISIBLE_SQL}""",
            (chunk_id, now),
        ).fetchone()

    @guarded
    def get_session(self, session_id: str, now: str) -> list[sqlite3.Row]:
        return list(
            self._db.execute(
                f"""SELECT * FROM chunks WHERE session_id = ?
                   AND {VISIBLE_SQL}
                   ORDER BY leaf_ord ASC""",
                (session_id, now),
            )
        )

    @guarded
    def get_chunk_any(self, chunk_id: str) -> sqlite3.Row | None:
        """Raw chunk row ignoring visibility (T6a miss classification)."""
        return self._db.execute(
            "SELECT * FROM chunks WHERE chunk_id = ?", (chunk_id,)
        ).fetchone()

    @guarded
    def get_session_any(self, session_id: str) -> list[sqlite3.Row]:
        """Raw session rows ignoring visibility (T6a miss classification)."""
        return list(
            self._db.execute(
                """SELECT * FROM chunks WHERE session_id = ?
                   ORDER BY leaf_ord ASC""",
                (session_id,),
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
    def task_session_rows(self, now: str, *, reader: str | None = None) -> list[sqlite3.Row]:
        """One row per (task, session) for visible task entries (phase 1A).

        Leaves of one session share a heading, so per-session aggregates
        are exact: title/status/bots are uniform within a session."""
        clause, clause_params = _reader_clause("bots", reader)
        return list(
            self._db.execute(
                f"""SELECT task, session_id,
                           MIN(timeline_day) AS first_day,
                           MAX(timeline_day) AS last_day,
                           MIN(line_start) AS first_line,
                           MAX(line_start) AS last_line,
                           MAX(session_title) AS title,
                           MAX(status) AS status,
                           MAX(bots) AS bots
                    FROM chunks
                    WHERE task IS NOT NULL AND {VISIBLE_SQL}{clause}
                    GROUP BY task, session_id""",
                (now, *clause_params),
            )
        )

    @guarded
    def habit_rows(self, now: str, *, reader: str | None = None) -> list[sqlite3.Row]:
        """One row per habit session for the sweep (phase 1E).

        Leaves of one session share a heading, so per-session aggregates
        are exact."""
        clause, clause_params = _reader_clause("bots", reader)
        return list(
            self._db.execute(
                f"""SELECT session_id,
                           MIN(timeline_day) AS day,
                           MAX(habit) AS habit,
                           MAX(habit_explicit) AS explicit,
                           MAX(bots) AS bots
                    FROM chunks
                    WHERE kind = 'habit' AND {VISIBLE_SQL}{clause}
                    GROUP BY session_id""",
                (now, *clause_params),
            )
        )

    @guarded
    def session_bots(self, session_id: str) -> tuple[str, ...] | None:
        """Owner list of one memory session; None = legacy (team) entry.

        Unknown sessions also read as None — callers check existence first
        (get_session_any) when the distinction matters."""
        row = self._db.execute(
            "SELECT bots FROM chunks WHERE session_id = ? LIMIT 1",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        return decode_bots(row["bots"])

    @guarded
    def recent_rows(self, limit: int, now: str, *, reader: str | None = None) -> list[sqlite3.Row]:
        # Daily recency only: canonical profile leaves live in stats, not in
        # the "recently updated notes" feed.
        clause, clause_params = _reader_clause("c.bots", reader)
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
        """heading + text_norm for in-order ranking. Empty dict if no ids."""
        if not chunk_ids:
            return {}
        marks = ",".join("?" * len(chunk_ids))
        rows = self._db.execute(
            f"SELECT chunk_id, heading_raw, text_norm FROM chunks WHERE chunk_id IN ({marks})",
            chunk_ids,
        )
        return {
            row["chunk_id"]: f"{row['heading_raw']} {row['text_norm']}" for row in rows
        }

    @guarded
    def visible_chunk_maps(self, now: str) -> list[dict[str, object]]:
        rows = self._db.execute(
            f"""SELECT chunk_id, session_id, heading_raw, text_raw, source_kind,
                      timeline_day, expires_at
               FROM chunks
               WHERE {VISIBLE_SQL}""",
            (now,),
        )
        return [dict(row) for row in rows]

    @guarded
    def session_leaf_count(self, session_id: str, now: str | None = None) -> int:
        now_ts = now if now is not None else format_ts(None)
        row = self._db.execute(
            f"""SELECT COUNT(*) AS n FROM chunks WHERE session_id = ?
               AND {VISIBLE_SQL}""",
            (session_id, now_ts),
        ).fetchone()
        return int(row["n"]) if row else 0


_READER_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def encode_bots(bots: tuple[str, ...] | list[str] | None) -> str | None:
    """Owner list → JSON column value; None stays NULL (legacy team)."""
    if bots is None:
        return None
    cleaned = [item for item in bots if isinstance(item, str) and item]
    return json.dumps(cleaned, ensure_ascii=False) if cleaned else None


def decode_bots(raw: object) -> tuple[str, ...] | None:
    """JSON column value → owner tuple; NULL/invalid reads as None (team)."""
    if raw is None:
        return None
    if not isinstance(raw, str):
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, list):
        return None
    cleaned = tuple(item for item in data if isinstance(item, str) and item)
    return cleaned or None


def _reader_clause(column: str, reader: str | None) -> tuple[str, list[object]]:
    """SQL visibility filter for one reader bot (BE-4).

    No reader (user/CLI) and Thyca see everything (empty clause). A child
    bot sees legacy rows (NULL = team), team rows, and its own rows —
    other bots' private rows stay hidden. Bot ids are charset-safe, so the
    JSON-substring LIKE cannot escape its quotes; an invalid reader
    matches nothing instead of everything."""
    if reader is None or reader == "thyca":
        return "", []
    if not isinstance(reader, str) or not _READER_RE.fullmatch(reader):
        return " AND 1 = 0", []
    return (
        f" AND ({column} IS NULL OR {column} LIKE '%\"team\"%'"
        f" OR {column} LIKE '%\"{reader}\"%')",
        [],
    )


def _safe_match(query: str) -> str | None:
    terms: list[str] = []
    seen: set[str] = set()
    for raw in _NON_ALNUM.split(query):
        term = raw.strip()
        if not term:
            continue
        key = term.casefold()
        if key in seen:
            continue
        seen.add(key)
        escaped = term.replace('"', '""')
        terms.append(f'"{escaped}"')
    if not terms:
        return None
    return " OR ".join(terms)


def hit_from_row(
    row: sqlite3.Row,
    match_type: str,
    *,
    score: float | None = None,
    bm25: float | None = None,
    snippet: str | None = None,
) -> Hit:
    bots = None
    extra: dict[str, str | None] = {}
    try:
        keys = row.keys()
    except Exception:
        keys = ()
    if "bots" in keys:
        bots = decode_bots(row["bots"])
    for name in ("task", "cont", "kind", "status", "habit"):
        value = row[name] if name in keys else None
        extra[name] = value if isinstance(value, str) and value else None
    explicit = row["habit_explicit"] if "habit_explicit" in keys else 0
    extra["habit_explicit"] = bool(explicit)
    return Hit(
        path=row["path"],
        source_kind=row["source_kind"],
        chunk_id=row["chunk_id"],
        timeline_day=row["timeline_day"],
        session_id=row["session_id"],
        heading=row["heading_raw"],
        snippet=snippet if snippet is not None else row["text_raw"][:250],
        score=float(score if score is not None else (-bm25 if bm25 is not None else 0.0)),
        match_type=match_type,
        bm25=None if bm25 is None else float(bm25),
        leaf_ord=int(row["leaf_ord"]),
        line_start=int(row["line_start"]),
        line_end=int(row["line_end"]),
        bots=bots,
        task=extra["task"],
        cont=extra["cont"],
        kind=extra["kind"],
        status=extra["status"],
        habit=extra["habit"],
        habit_explicit=bool(extra["habit_explicit"]),
    )


# Private alias kept for backward compatibility (tests / older imports).
_hit_from_row = hit_from_row
