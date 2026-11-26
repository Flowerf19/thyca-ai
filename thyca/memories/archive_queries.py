"""SQLite query helpers, FTS5/trigram search, and Hit types for ArchiveStore."""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from itertools import pairwise

from rapidfuzz import fuzz

from thyca.memories.chunk import Chunker
from thyca.memories.heading import VISIBLE_SQL

TRIGRAM_FLOOR = 60
TYPO_TRIGRAM_FLOOR = 80
CANDIDATE_CAP = 50
GET_MEMORY_CAP = 10
BM25_WEIGHTS = (0.7, 1.2, 0.3)
BIGRAM_WEIGHT = 0.50
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NON_ALNUM = re.compile(r"[^\w]+", re.UNICODE)
_READER_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


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


def encode_bots(bots: tuple[str, ...] | list[str] | None) -> str | None:
    """Owner list → JSON column value; None stays NULL (legacy team)."""
    if bots is None:
        return None
    cleaned = [item for item in bots if isinstance(item, str) and item]
    return json.dumps(cleaned, ensure_ascii=False) if cleaned else None


def decode_bots(raw: object) -> tuple[str, ...] | None:
    """JSON column value → owner tuple; NULL/invalid reads as None (team)."""
    if raw is None or not isinstance(raw, str):
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, list):
        return None
    cleaned = tuple(item for item in data if isinstance(item, str) and item)
    return cleaned or None


def reader_clause(column: str, reader: str | None) -> tuple[str, list[object]]:
    """SQL visibility filter for one reader bot (BE-4)."""
    if reader is None or reader == "thyca":
        return "", []
    if not isinstance(reader, str) or not _READER_RE.fullmatch(reader):
        return " AND 1 = 0", []
    return (
        f" AND ({column} IS NULL OR {column} LIKE '%\"team\"%'"
        f" OR {column} LIKE '%\"{reader}\"%')",
        [],
    )


def build_filter_sql(
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
    reader_sql, reader_params = reader_clause(f"{prefix}bots", reader)
    clauses.append(reader_sql)
    params.extend(reader_params)
    return "".join(clauses), params


def safe_match(query: str) -> str | None:
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


def bigram_match(query: str) -> str | None:
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
        habit_explicit=bool(explicit),
        chat_session=(
            row["chat_session"]
            if "chat_session" in keys and isinstance(row["chat_session"], str)
            else None
        ),
    )


def fts_search_rows(
    db: sqlite3.Connection,
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
    match = safe_match(query)
    if match is None:
        return []
    filter_sql, filter_params = build_filter_sql(
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
    rows = db.execute(sql, [match, now, *filter_params, cap]).fetchall()
    return [hit_from_row(row, "fts", bm25=row["bm25"], snippet=row["snippet"]) for row in rows]


def fts_search_memories_rows(
    db: sqlite3.Connection,
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
    uni_match = safe_match(query)
    if uni_match is None:
        return [], 0, False
    bi_match = bigram_match(query)
    filter_sql, filter_params = build_filter_sql(
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
    uni_rows = db.execute(channel_sql, [uni_match, now, *filter_params, cap]).fetchall()
    bi_rows = (
        db.execute(channel_sql, [bi_match, now, *filter_params, cap]).fetchall()
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
    chunk_rows = db.execute(
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


def trigram_search_rows(
    db: sqlite3.Connection,
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
    filter_sql, filter_params = build_filter_sql(
        timeline_day, project, chat_session, reader, task, kind, status
    )
    sql = (
        "SELECT row_id, chunk_id, memory_title, text_norm FROM chunks "
        f"WHERE {VISIBLE_SQL}{filter_sql}"
    )
    scored: list[tuple[float, str, int]] = []
    for row in db.execute(sql, [now, *filter_params]):
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
    full_rows = db.execute(
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
