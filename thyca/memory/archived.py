"""Archived lexical memory: SQLite FTS + trigram."""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from rapidfuzz import fuzz

from thyca.config import DEFAULT_TIMELINE_TIMEZONE
from thyca.memory.chunk import Chunk, Chunker
from thyca.memory.heading import format_ts

SCHEMA_VERSION = "3"
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

    def close(self) -> None:
        self._db.close()

    def _connect(self, db_path: Path) -> sqlite3.Connection:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(db_path)
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
        self._db.execute("DELETE FROM source_files WHERE path = ?", (path,))
        self._db.commit()

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

    def session_leaf_count(self, session_id: str) -> int:
        row = self._db.execute(
            "SELECT COUNT(*) AS n FROM chunks WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return int(row["n"]) if row else 0


class ArchivedMemory:
    """Orchestrate chunking, reindex, and lexical search."""

    def __init__(
        self,
        thyca_dir: Path | None = None,
        timezone_name: str | None = None,
        store: ArchiveStore | None = None,
        chunker: Chunker | None = None,
    ) -> None:
        self.thyca_dir = Path(thyca_dir or Path.home() / ".thyca")
        self.timezone_name = timezone_name or DEFAULT_TIMELINE_TIMEZONE
        db_path = self.thyca_dir / "memory.sqlite"
        self.store = store or ArchiveStore(db_path)
        self.chunker = chunker or Chunker()

    def zone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone_name)
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            return ZoneInfo(DEFAULT_TIMELINE_TIMEZONE)

    def day(self, now: datetime | None = None) -> str:
        moment = now or datetime.now(self.zone())
        zone = self.zone()
        aware = moment.replace(tzinfo=zone) if moment.tzinfo is None else moment.astimezone(zone)
        return aware.date().isoformat()

    def lookup_session_id(self, chunk_id: str, now: datetime | None = None) -> str:
        row = self.store.get_chunk(chunk_id, format_ts(now))
        if row is None:
            raise ArchiveError(f"chunk not found: {chunk_id}")
        return str(row["session_id"])

    def reindex(self, now: datetime | None = None) -> None:
        today = self.day(now)
        wanted: set[str] = set()
        for name in ("SOUL.md", "USER.md", "MEMORY.md"):
            path = self.thyca_dir / name
            wanted.add(str(path))
            self._reindex_file(path, "canonical", None, today)
        memory_dir = self.thyca_dir / "memory"
        if memory_dir.is_dir():
            for path in sorted(memory_dir.glob("????-??-??.md")):
                if not path.is_file() or path.is_symlink():
                    continue
                day = path.stem
                if not DATE_RE.fullmatch(day):
                    continue
                wanted.add(str(path))
                self._reindex_file(path, "daily", day, today)
        existing = set(self.store.list_paths())
        for stale in existing - wanted:
            self.store.drop_source(stale)

    def fts_hits(
        self, query: str, timeline_day: str | None, now: datetime | None, limit: int
    ) -> list[Hit]:
        return self.store.fts_search(query, timeline_day, limit, format_ts(now))

    def trigram_hits(
        self, query: str, timeline_day: str | None, now: datetime | None, limit: int
    ) -> list[Hit]:
        return self.store.trigram_search(
            self.chunker.normalize(query), timeline_day, limit, format_ts(now)
        )

    def recent_hits(self, limit: int, now: datetime | None = None) -> list[Hit]:
        return [
            _hit_from_row(row, "recent", snippet=row["text_raw"][:250])
            for row in self.store.recent_rows(limit, format_ts(now))
        ]

    def with_counts(self, hits: list[Hit]) -> list[Hit]:
        counted: list[Hit] = []
        for hit in hits:
            count = self.store.session_leaf_count(hit.session_id)
            counted.append(
                Hit(
                    path=hit.path,
                    source_kind=hit.source_kind,
                    chunk_id=hit.chunk_id,
                    timeline_day=hit.timeline_day,
                    session_id=hit.session_id,
                    heading=hit.heading,
                    snippet=hit.snippet,
                    score=hit.score,
                    match_type=hit.match_type,
                    bm25=hit.bm25,
                    leaf_ord=hit.leaf_ord,
                    session_leaf_count=count,
                    has_more=count > 1,
                    line_start=hit.line_start,
                    line_end=hit.line_end,
                )
            )
        return counted

    def get(
        self,
        *,
        chunk_id: str | None = None,
        session_id: str | None = None,
        path: str | None = None,
        now: datetime | None = None,
    ) -> str:
        selectors = [item for item in (chunk_id, session_id, path) if item]
        if len(selectors) != 1:
            raise ArchiveError("exactly one of chunk_id, session_id, path is required")
        now = format_ts(now)
        if chunk_id is not None:
            row = self.store.get_chunk(chunk_id, now)
            if row is None:
                raise ArchiveError(f"chunk not found: {chunk_id}")
            return row["text_raw"]
        if session_id is not None:
            rows = self.store.get_session(session_id, now)
            if not rows:
                raise ArchiveError(f"session not found: {session_id}")
            heading = rows[0]["heading_raw"]
            body = [row["text_raw"] for row in rows[:GET_SESSION_CAP]]
            text = "\n".join([heading, *body] if heading else body)
            if len(rows) > GET_SESSION_CAP:
                text += f"\n<!-- more:{len(rows) - GET_SESSION_CAP} -->"
            return text
        allowed = self._allowed_path(Path(path or ""))
        return allowed.read_text(encoding="utf-8")

    def _reindex_file(self, path: Path, kind: str, day: str | None, today: str) -> None:
        if kind == "daily" and day is not None and day >= today:
            return
        if not path.is_file() or path.is_symlink():
            self.store.drop_source(str(path))
            return
        stat = path.stat()
        prev = self.store.source_stat(str(path))
        if prev == (stat.st_mtime_ns, stat.st_size):
            return
        text = path.read_text(encoding="utf-8")
        chunks = self.chunker.chunk_markdown(path, text, source_kind=kind, timeline_day=day)
        self.store.replace_source(str(path), kind, day, stat.st_mtime_ns, stat.st_size, chunks)

    def _allowed_path(self, path: Path) -> Path:
        root = self.thyca_dir.resolve()
        target = path.expanduser().resolve()
        if target.parent == root and target.name in {"SOUL.md", "USER.md", "MEMORY.md"}:
            return target
        memory_dir = (root / "memory").resolve()
        if target.parent == memory_dir and DATE_RE.fullmatch(target.stem) and target.suffix == ".md":
            today = self.day()
            if target.stem >= today:
                raise ArchiveError("today daily is not archived")
            return target
        raise ArchiveError(f"path not an archived memory source: {path}")

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


def dedup_siblings(hits: list[Hit]) -> list[Hit]:
    seen: set[str] = set()
    out: list[Hit] = []
    for hit in hits:
        if hit.session_id in seen:
            continue
        seen.add(hit.session_id)
        out.append(hit)
    return out
