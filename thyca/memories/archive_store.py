"""SQLite I/O for the archived leaf index (split from archived.py)."""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path

from rapidfuzz import fuzz

from thyca.memories.chunk import Chunk, Chunker
from thyca.memories.heading import VISIBLE_SQL, format_ts
from thyca.memories.schema import (
    SCHEMA_VERSION,  # noqa: F401 -- reason: re-exported
    init_schema,
)
from thyca.memories.usage import LeafUsage, guarded

TRIGRAM_FLOOR = 60
TYPO_TRIGRAM_FLOOR = 80
CANDIDATE_CAP = 50
GET_MEMORY_CAP = 10
BM25_WEIGHTS = (0.7, 1.2, 0.3)
BIGRAM_WEIGHT = 0.50
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
    memory_id: str
    heading: str
    snippet: str
    score: float
    match_type: str
    bm25: float | None = None
    leaf_ord: int = 1
    memory_leaf_count: int = 1
    has_more: bool = False
    line_start: int = 1
    line_end: int = 1
    bots: tuple[str, ...] | None = None
    task: str | None = None
    cont: str | None = None
    kind: str | None = None
    status: str | None = None
    habit: str | None = None
    habit_explicit: bool = False
    chat_session: str | None = None


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
        cap = max(0, min(limit, CANDIDATE_CAP))
        if cap == 0:
            return []
        match = _safe_match(query)
        if match is None:
            return []
        filter_sql, filter_params = _build_filter_sql(
            timeline_day, project, chat_session, reader, task, kind, status, alias="c"
        )
        bm25_expr = f"bm25(chunks_fts, {BM25_WEIGHTS[0]}, {BM25_WEIGHTS[1]}, {BM25_WEIGHTS[2]})"
        sql = f"""
            SELECT c.*, snippet(chunks_fts, 0, '⟨', '⟩', '…', 6) AS snippet,
                   {bm25_expr} AS bm25
            FROM chunks_fts
            JOIN chunks c ON c.row_id = chunks_fts.rowid
            WHERE chunks_fts MATCH ?
              AND {VISIBLE_SQL}{filter_sql}
            ORDER BY bm25 ASC, c.chunk_id ASC LIMIT ?
        """
        rows = self._db.execute(sql, [match, now, *filter_params, cap]).fetchall()
        return [hit_from_row(row, "fts", bm25=row["bm25"], snippet=row["snippet"]) for row in rows]

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
        cap = max(0, min(limit, CANDIDATE_CAP))
        if cap == 0:
            return [], 0, False
        uni_match = _safe_match(query)
        if uni_match is None:
            return [], 0, False
        bi_match = _bigram_match(query)
        filter_sql, filter_params = _build_filter_sql(
            timeline_day, project, chat_session, reader, task, kind, status, alias="c"
        )
        bm25_expr = f"bm25(chunks_fts, {BM25_WEIGHTS[0]}, {BM25_WEIGHTS[1]}, {BM25_WEIGHTS[2]})"
        channel_sql = f"""
            WITH scored AS MATERIALIZED (
                SELECT c.row_id, c.memory_id, c.chunk_id,
                       {bm25_expr} AS bm25,
                       snippet(chunks_fts, 0, '⟨', '⟩', '…', 6) AS snippet
                FROM chunks_fts
                JOIN chunks c ON c.row_id = chunks_fts.rowid
                WHERE chunks_fts MATCH ?
                  AND {VISIBLE_SQL}{filter_sql}
            ),
            ranked AS (
                SELECT row_id, memory_id, chunk_id, bm25, snippet,
                       ROW_NUMBER() OVER (PARTITION BY memory_id ORDER BY bm25 ASC, chunk_id ASC) AS rn,
                       COUNT(*) OVER (PARTITION BY memory_id) AS sib_cnt
                FROM scored
            )
            SELECT row_id, memory_id, chunk_id, bm25, snippet, sib_cnt
            FROM ranked
            WHERE rn = 1
            ORDER BY bm25 ASC, chunk_id ASC
            LIMIT ?
        """
        uni_rows = self._db.execute(channel_sql, [uni_match, now, *filter_params, cap]).fetchall()
        bi_rows = (
            self._db.execute(channel_sql, [bi_match, now, *filter_params, cap]).fetchall()
            if bi_match is not None
            else []
        )
        uni_capped = len(uni_rows) >= cap
        bi_capped = len(bi_rows) >= cap

        fused: dict[str, dict[str, object]] = {}
        for row in uni_rows:
            mid = str(row["memory_id"])
            raw_bm25 = float(row["bm25"])
            uni_pos = -raw_bm25
            fused[mid] = {
                "row_id": int(row["row_id"]),
                "chunk_id": str(row["chunk_id"]),
                "fused_score": uni_pos,
                "uni_pos": uni_pos,
                "raw_bm25": raw_bm25,
                "sib_cnt": int(row["sib_cnt"]),
                "snippet": str(row["snippet"] or ""),
            }
        if bi_match is not None:
            for row in bi_rows:
                mid = str(row["memory_id"])
                bi_bm25 = float(row["bm25"])
                bi_pos = -bi_bm25
                weighted = BIGRAM_WEIGHT * bi_pos
                existing = fused.get(mid)
                if existing is None:
                    fused[mid] = {
                        "row_id": int(row["row_id"]),
                        "chunk_id": str(row["chunk_id"]),
                        "fused_score": weighted,
                        "uni_pos": 0.0,
                        "raw_bm25": bi_bm25,
                        "sib_cnt": int(row["sib_cnt"]),
                        "snippet": str(row["snippet"] or ""),
                    }
                else:
                    existing["fused_score"] = float(existing["fused_score"]) + weighted
                    existing["sib_cnt"] = max(int(existing["sib_cnt"]), int(row["sib_cnt"]))
                    if weighted > float(existing["uni_pos"]):
                        existing["row_id"] = int(row["row_id"])
                        existing["chunk_id"] = str(row["chunk_id"])
                        existing["raw_bm25"] = bi_bm25
                        existing["snippet"] = str(row["snippet"] or "")

        top_items = sorted(
            fused.values(),
            key=lambda item: (-float(item["fused_score"]), str(item["chunk_id"])),
        )[:cap]
        if not top_items:
            return [], 0, False

        hidden_siblings = sum(max(0, int(item["sib_cnt"]) - 1) for item in top_items)
        row_ids = [int(item["row_id"]) for item in top_items]
        marks = ",".join("?" * len(row_ids))
        chunk_rows = self._db.execute(
            f"SELECT * FROM chunks WHERE row_id IN ({marks})", row_ids
        ).fetchall()
        rows_by_rid = {int(row["row_id"]): row for row in chunk_rows}

        hits = [
            hit_from_row(
                rows_by_rid[int(item["row_id"])],
                "fts",
                score=float(item["fused_score"]),
                bm25=float(item["raw_bm25"]),
                snippet=str(item.get("snippet") or "") or None,
            )
            for item in top_items
            if int(item["row_id"]) in rows_by_rid
        ]
        return hits, hidden_siblings, (uni_capped or bi_capped)

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
        cap = max(0, min(limit, CANDIDATE_CAP))
        if cap == 0:
            return []
        tokens = [t for t in _NON_ALNUM.split(query_norm) if len(t) >= 3]
        grams = tuple({tok[i : i + 3] for tok in tokens for i in range(len(tok) - 2)})
        filter_sql, filter_params = _build_filter_sql(
            timeline_day, project, chat_session, reader, task, kind, status
        )
        sql = (
            "SELECT row_id, chunk_id, memory_title, text_norm FROM chunks "
            f"WHERE {VISIBLE_SQL}{filter_sql}"
        )
        scored: list[tuple[float, str, int]] = []
        for row in self._db.execute(sql, [now, *filter_params]):
            title = str(row["memory_title"] or "")
            hay = f"{Chunker.normalize(title)} {row['text_norm']}" if title else row["text_norm"]
            has_exact_token = not tokens or any(t in hay for t in tokens)
            if not has_exact_token and (not grams or not any(g in hay for g in grams)):
                continue
            score = float(fuzz.partial_ratio(query_norm, hay))
            floor = TRIGRAM_FLOOR if has_exact_token else TYPO_TRIGRAM_FLOOR
            if score >= floor:
                scored.append((score, str(row["chunk_id"]), int(row["row_id"])))
        scored.sort(key=lambda item: (-item[0], item[1]))
        top_scored = scored[:cap]
        if not top_scored:
            return []
        top_rids = [item[2] for item in top_scored]
        marks = ",".join("?" * len(top_rids))
        full_rows = self._db.execute(
            f"SELECT * FROM chunks WHERE row_id IN ({marks})", top_rids
        ).fetchall()
        rows_by_rid = {int(row["row_id"]): row for row in full_rows}
        return [
            hit_from_row(
                rows_by_rid[rid],
                "trigram",
                score=score,
                snippet=rows_by_rid[rid]["text_raw"][:250],
            )
            for score, _cid, rid in top_scored
            if rid in rows_by_rid
        ]

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
        """One row per (task, session) for visible task entries (phase 1A).

        Leaves of one session share a heading, so per-session aggregates
        are exact: title/status/bots are uniform within a session."""
        clause, clause_params = _reader_clause("bots", reader)
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
        """One row per habit session for the sweep (phase 1E).

        Leaves of one session share a heading, so per-session aggregates
        are exact."""
        clause, clause_params = _reader_clause("bots", reader)
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
        clause, clause_params = _reader_clause("bots", reader)
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
        """Owner list of one memory session; None = legacy (team) entry.

        Unknown sessions also read as None — callers check existence first
        (get_memory_any) when the distinction matters."""
        row = self._db.execute(
            "SELECT bots FROM chunks WHERE memory_id = ? LIMIT 1",
            (memory_id,),
        ).fetchone()
        if row is None:
            return None
        return decode_bots(row["bots"])

    @guarded
    def recent_rows(self, limit: int, now: str, *, reader: str | None = None) -> list[sqlite3.Row]:
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


def _build_filter_sql(
    timeline_day: str | None,
    project: str | None,
    chat_session: str | None,
    reader: str | None,
    task: str | None,
    kind: str | None,
    status: str | None,
    *,
    alias: str = "",
) -> tuple[str, list[object]]:
    """Build parameterized SQL WHERE filters shared across FTS and trigram queries."""
    prefix = f"{alias}." if alias else ""
    clauses: list[str] = []
    params: list[object] = []
    for col, val in (
        ("timeline_day", timeline_day),
        ("project", project),
        ("chat_session", chat_session),
        ("task", task),
        ("kind", kind),
        ("status", status),
    ):
        if val is not None:
            clauses.append(f" AND {prefix}{col} = ?")
            params.append(val)
    reader_sql, reader_params = _reader_clause(f"{prefix}bots", reader)
    clauses.append(reader_sql)
    params.extend(reader_params)
    return "".join(clauses), params


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


def _bigram_match(query: str) -> str | None:
    """Build deduplicated quoted adjacent-bigram FTS5 OR expression."""
    raw_terms = [raw.strip() for raw in _NON_ALNUM.split(query) if raw.strip()]
    if len(raw_terms) < 2:
        return None
    terms: list[str] = []
    seen: set[str] = set()

    for a, b in pairwise(raw_terms):
        key = f"{a.casefold()} {b.casefold()}"
        if key in seen:
            continue
        seen.add(key)
        ea = a.replace('"', '""')
        eb = b.replace('"', '""')
        terms.append(f'"{ea} {eb}"')
    return " OR ".join(terms) if terms else None


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
        memory_id=row["memory_id"],
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
        chat_session=(
            row["chat_session"]
            if "chat_session" in keys and isinstance(row["chat_session"], str)
            else None
        ),
    )


_hit_from_row = hit_from_row
