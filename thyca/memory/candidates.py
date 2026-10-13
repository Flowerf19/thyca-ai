"""Candidate queue for team-memory promotion (BE-4).

A child bot never writes ``\"team\"`` entries directly: it proposes, and
Thyca (or the user) approves. Approval flips the entry's ``bots`` to
``[\"team\"]`` with a stable id. Stored as one JSON file
(``~/.thyca/memory_candidates.json``) with atomic writes; guarded by a
process-local lock like the memory writer.
"""
from __future__ import annotations

import json
import secrets
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

from thyca.config import atomic_write_text
from thyca.core.protocol import utc_now_ts

CANDIDATE_PREFIX = "cand_"


@dataclass
class Candidate:
    id: str
    bot_id: str
    session_id: str
    status: str = "pending"
    created_at: str = ""
    decided_by: str | None = None
    decided_at: str | None = None


class CandidateError(RuntimeError):
    """Candidate queue could not read or apply a decision."""


class CandidateQueue:
    """File-backed pending/approved/rejected candidate records."""

    def __init__(self, thyca_dir: Path) -> None:
        self._path = Path(thyca_dir) / "memory_candidates.json"
        self._lock = threading.RLock()

    def propose(self, bot_id: str, session_id: str) -> Candidate:
        """File one proposal; an identical pending row is returned as-is."""
        if not isinstance(bot_id, str) or not bot_id:
            raise CandidateError("bot_id must be a non-empty string")
        if not isinstance(session_id, str) or not session_id:
            raise CandidateError("session_id must be a non-empty string")
        with self._lock:
            records = self._read()
            for record in records:
                if (
                    record["status"] == "pending"
                    and record["bot_id"] == bot_id
                    and record["session_id"] == session_id
                ):
                    return Candidate(**record)
            candidate = Candidate(
                id=f"{CANDIDATE_PREFIX}{secrets.token_hex(4)}",
                bot_id=bot_id,
                session_id=session_id,
                created_at=utc_now_ts(),
            )
            records.append(asdict(candidate))
            self._write(records)
            return candidate

    def list_pending(self) -> list[Candidate]:
        with self._lock:
            return [
                Candidate(**record)
                for record in self._read()
                if record.get("status") == "pending"
            ]

    def list_all(self) -> list[Candidate]:
        with self._lock:
            return [Candidate(**record) for record in self._read()]

    def decide(
        self, candidate_id: str, approved: bool, decided_by: str
    ) -> Candidate:
        """Mark approved/rejected. The caller promotes on approved."""
        if not isinstance(decided_by, str) or not decided_by:
            raise CandidateError("decided_by must be a non-empty string")
        with self._lock:
            records = self._read()
            for record in records:
                if record.get("id") != candidate_id:
                    continue
                if record.get("status") != "pending":
                    raise CandidateError(f"candidate already decided: {candidate_id}")
                record["status"] = "approved" if approved else "rejected"
                record["decided_by"] = decided_by
                record["decided_at"] = utc_now_ts()
                self._write(records)
                return Candidate(**record)
        raise CandidateError(f"unknown candidate: {candidate_id}")

    def expire_older_than(self, days: int, *, now_ts: str | None = None) -> int:
        """Auto-reject pending rows older than ``days``. Returns the count.

        No auto-sweep is wired in v1 (no decided TTL in the master plan);
        the idle job may call this with an explicit window.
        """
        from datetime import UTC, datetime, timedelta

        if not isinstance(days, int) or days < 1:
            raise CandidateError("days must be a positive integer")
        now = now_ts or utc_now_ts()
        try:
            cutoff = datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=UTC
            ) - timedelta(days=days)
        except ValueError as exc:
            raise CandidateError(f"invalid now_ts: {now!r}") from exc
        expired = 0
        with self._lock:
            records = self._read()
            for record in records:
                if record.get("status") != "pending":
                    continue
                try:
                    created = datetime.strptime(
                        record.get("created_at", ""), "%Y-%m-%dT%H:%M:%SZ"
                    ).replace(tzinfo=UTC)
                except ValueError:
                    continue
                if created < cutoff:
                    record["status"] = "rejected"
                    record["decided_by"] = "expiry"
                    record["decided_at"] = now
                    expired += 1
            if expired:
                self._write(records)
        return expired

    def _read(self) -> list[dict]:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise CandidateError(f"cannot read candidates: {exc}") from exc
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise CandidateError(f"candidates file is not valid JSON: {exc}") from exc
        if not isinstance(data, list):
            raise CandidateError("candidates file must hold a list")
        return [record for record in data if isinstance(record, dict)]

    def _write(self, records: list[dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            atomic_write_text(
                self._path, json.dumps(records, ensure_ascii=False, indent=2) + "\n"
            )
        except OSError as exc:
            raise CandidateError(f"cannot write candidates: {exc}") from exc
