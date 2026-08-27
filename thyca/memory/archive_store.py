"""SQLite I/O for the archived leaf index (split from archived.py)."""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz

from thyca.memory.usage import LeafUsage

SCHEMA_VERSION = "5"
SCHEMA_PATH = Path(__file__).with_name("schema.sql")
TRIGRAM_MIN_FTS = 3
TRIGRAM_FLOOR = 70
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


@dataclass
class SearchResult:
    hits: list[Hit] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class ArchiveStore:
    """SQLite I/O for the archived leaf index."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._db = self._connect(db_path)
        self._init_schema()
        self.usage = LeafUsage(self._db)

    def close(self) -> None:
        self._db.close()

    def _connect(self, db_path: Path) -> sqlite3.Connection:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(db_path, check_same_thread=False)
        db.isolation_level = None
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        return db

    def _init_schema(self) -> None:
        self._db.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        row = self._db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if row is None:
            self._db.execute(
                "INSERT INTO meta(key, value) VALUES ('schema_version', ?)",
                (SCHEMA_VERSION,),
            )
            self._db.commit()
        elif row["value"] != SCHEMA_VERSION:
            self._migrate(row["value"])

    def _migrate(self, from_version: str) -> None:
        if from_version == "4":
            self._db.execute(
                """CREATE TABLE IF NOT EXISTS leaf_searches(
                    chunk_id        TEXT PRIMARY KEY,
                    session_id      TEXT NOT NULL,
                    search_count    INTEGER NOT NULL CHECK(search_count >= 1),
                    last_search_at  TEXT NOT NULL
                )"""
            )
            self._db.execute(
                "UPDATE meta SET value = ? WHERE key = 'schema_version'",
                (SCHEMA_VERSION,),
            )
            self._db.commit()
            return
        if from_version == "3":
            self._db.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            self._db.execute(
                "UPDATE meta SET value = ? WHERE key = 'schema_version'",
                (SCHEMA_VERSION,),
            )
            self._db.commit()
            return
        if from_version not in {"1", "2"}:
            raise ArchiveError(f"unsupported schema_version {from_version!r}")
        for trigger in ("chunks_ai", "chunks_ad", "chunks_au"):
            self._db.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        for index in ("chunks_day", "chunks_path", "chunks_content_hash", "chunks_profile"):
            self._db.execute(f"DROP INDEX IF EXISTS {index}")
        self._db.execute("DROP TABLE IF EXISTS chunks_fts")
        self._db.execute("DROP TABLE IF EXISTS chunks")
        self._db.execute("DELETE FROM source_files")
        self._db.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        self._db.execute(
            "UPDATE meta SET value = ? WHERE key = 'schema_version'",
            (SCHEMA_VERSION,),
        )
        self._db.commit()

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
                        content_hash, expires_at, forgotten_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    ),
                )
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

    def drop_source(self, path: str) -> None:
        ids = [
            str(row["chunk_id"])
            for row in self._db.execute("SELECT chunk_id FROM chunks WHERE path = ?", (path,))
        ]
        self._db.execute("DELETE FROM source_files WHERE path = ?", (path,))
        self._db.commit()
        self.usage.drop_ids(ids)

    def source_stat(self, path: str) -> tuple[int, int] | None:
        row = self._db.execute(
            "SELECT mtime_ns, size_bytes FROM source_files WHERE path = ?",
            (path,),
        ).fetchone()
        if row is None:
            return None
        return int(row["mtime_ns"]), int(row["size_bytes"])

    def fts_search(self, query: str, timeline_day: str | None, limit: int, now: str) -> list[Hit]:
        match = _safe_match(query)
        if match is None:
            return []
        sql = """
            SELECT c.*, snippet(chunks_fts, 0, '⟨', '⟩', '…', 6) AS snippet,
                   bm25(chunks_fts) AS bm25
            FROM chunks_fts
            JOIN chunks c ON c.row_id = chunks_fts.rowid
            WHERE chunks_fts MATCH ?
              AND c.forgotten_at IS NULL
              AND (c.expires_at IS NULL OR c.expires_at > ?)
        """
        params: list[object] = [match, now]
        if timeline_day is not None:
            sql += " AND c.timeline_day = ?"
            params.append(timeline_day)
        sql += " ORDER BY bm25 ASC, c.chunk_id ASC LIMIT ?"
        params.append(min(limit, CANDIDATE_CAP))
        rows = self._db.execute(sql, params).fetchall()
        return [_hit_from_row(row, "fts", bm25=row["bm25"], snippet=row["snippet"]) for row in rows]

    def trigram_search(self, query_norm: str, timeline_day: str | None, limit: int, now: str) -> list[Hit]:
        sql = """SELECT * FROM chunks
                  WHERE forgotten_at IS NULL
                    AND (expires_at IS NULL OR expires_at > ?)"""
        params: list[object] = [now]
        if timeline_day is not None:
            sql += " AND timeline_day = ?"
            params.append(timeline_day)
        scored: list[tuple[float, sqlite3.Row]] = []
        for row in self._db.execute(sql, params):
            score = float(fuzz.partial_ratio(query_norm, row["text_norm"]))
            if score >= TRIGRAM_FLOOR:
                scored.append((score, row))
        scored.sort(key=lambda item: (-item[0], item[1]["chunk_id"]))
        return [
            _hit_from_row(row, "trigram", score=score, snippet=row["text_raw"][:250])
            for score, row in scored[: min(limit, CANDIDATE_CAP)]
        ]

    def get_chunk(self, chunk_id: str, now: str) -> sqlite3.Row | None:
        return self._db.execute(
            """SELECT * FROM chunks WHERE chunk_id = ?
               AND forgotten_at IS NULL
               AND (expires_at IS NULL OR expires_at > ?)""",
            (chunk_id, now),
        ).fetchone()

    def get_session(self, session_id: str, now: str) -> list[sqlite3.Row]:
        return list(
            self._db.execute(
                """SELECT * FROM chunks WHERE session_id = ?
                   AND forgotten_at IS NULL
                   AND (expires_at IS NULL OR expires_at > ?)
                   ORDER BY leaf_ord ASC""",
                (session_id, now),
            )
        )

    def recent_rows(self, limit: int, now: str) -> list[sqlite3.Row]:
        return list(
            self._db.execute(
                """SELECT c.* FROM chunks c
                   JOIN source_files s ON s.path = c.path
                   WHERE c.forgotten_at IS NULL
                     AND (c.expires_at IS NULL OR c.expires_at > ?)
                   ORDER BY s.mtime_ns DESC, c.leaf_ord ASC
                   LIMIT ?""",
                (now, limit),
            )
        )

    def list_paths(self) -> list[str]:
        return [row["path"] for row in self._db.execute("SELECT path FROM source_files")]

    def chunk_ids(self) -> list[str]:
        return [row["chunk_id"] for row in self._db.execute("SELECT chunk_id FROM chunks")]

    def visible_chunk_maps(self, now: str) -> list[dict[str, object]]:
        rows = self._db.execute(
            """SELECT chunk_id, session_id, heading_raw, text_raw, source_kind,
                      timeline_day, expires_at
               FROM chunks
               WHERE forgotten_at IS NULL
                 AND (expires_at IS NULL OR expires_at > ?)""",
            (now,),
        )
        return [dict(row) for row in rows]

    def session_leaf_count(self, session_id: str) -> int:
        row = self._db.execute(
            "SELECT COUNT(*) AS n FROM chunks WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return int(row["n"]) if row else 0


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


def _hit_from_row(
    row: sqlite3.Row,
    match_type: str,
    *,
    score: float | None = None,
    bm25: float | None = None,
    snippet: str | None = None,
) -> Hit:
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
    )
