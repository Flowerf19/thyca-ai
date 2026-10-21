"""Private follow-up store (WS-4).

A bot may ask the user for private information in its own DM for a
canonical work item (memory ``task-<8 hex>``). State lives at
``bots/<bot>/workspace/private_followups.json`` — atomic writes, file
lock, ``0600``. One nonterminal pending per bot at a time.

State machine: open -> waiting_private -> received -> resumed|done|
cancelled|expired. Terminal states are immutable. The raw DM answer is
never stored here — only the answer's event/row reference.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from thyca.bots.card import _NAME_RE, NAME_MAX
from thyca.config import atomic_write_text
from thyca.core.channel import WORK_REF_RE
from thyca.core.protocol import parse_ts
from thyca.memory.heading import format_ts, utc_now
from thyca.sessions.store import SESSION_ID_PATTERN

NONTERMINAL = ("open", "waiting_private", "received")
TERMINAL = ("resumed", "done", "cancelled", "expired")
STATES = NONTERMINAL + TERMINAL
OUTCOMES = ("ready", "resume", "done", "cancel")
SESSION_ID_RE = re.compile(SESSION_ID_PATTERN)
PENDING_ID_RE = re.compile(r"pf-[0-9a-f]{12}")


class PrivateFollowupError(RuntimeError):
    """Store/transition failure (invalid state, corruption, conflict)."""


class PendingConflict(PrivateFollowupError):
    """A nonterminal pending already exists for this bot's DM."""


def _check_bot(bot_id: str) -> None:
    if (
        not isinstance(bot_id, str)
        or not bot_id
        or len(bot_id) > NAME_MAX
        or _NAME_RE.fullmatch(bot_id) is None
    ):
        raise PrivateFollowupError(f"invalid bot id: {bot_id!r}")


def _check_text(value, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PrivateFollowupError(f"{name} must be a non-empty string")
    return value


def _check_session(value, name: str) -> str:
    value = _check_text(value, name)
    if SESSION_ID_RE.fullmatch(value) is None:
        raise PrivateFollowupError(f"invalid {name}")
    return value


@dataclass
class Pending:
    pending_id: str
    bot_id: str
    work_id: str
    origin_window: str
    dm_window: str
    status: str = "open"
    created_at: str = ""
    updated_at: str = ""
    question: str = ""
    answer_event_id: str | None = None
    answer_ref: str | None = None
    proposed_redacted: str | None = None
    proposal_revision: int = 0
    requested_outcome: str | None = None
    share_policy: str = "private"
    approved_revision: int | None = None
    create_idempotency: str | None = None
    receive_idempotency: str | None = None
    decide_idempotency: str | None = None
    posted_marker: str | None = None
    approve_marker: str | None = None
    operations: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}

    @staticmethod
    def _opt_str(value, name: str) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise PrivateFollowupError(f"{name} must be a string or null")
        return value

    @staticmethod
    def _opt_int(value, name: str) -> int | None:
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool):
            raise PrivateFollowupError(f"{name} must be an int or null")
        return value

    @staticmethod
    def from_dict(data: dict) -> "Pending":
        if not isinstance(data, dict):
            raise PrivateFollowupError("pending entry must be an object")
        status = data.get("status")
        if status not in STATES:
            raise PrivateFollowupError(f"bad pending status: {status!r}")
        outcome = data.get("requested_outcome")
        if outcome is not None and outcome not in OUTCOMES:
            raise PrivateFollowupError(f"bad requested_outcome: {outcome!r}")
        revision = Pending._opt_int(data.get("proposal_revision", 0), "proposal_revision")
        if revision is None or revision < 0:
            raise PrivateFollowupError("invalid proposal_revision")
        operations = data.get("operations", {})
        if not isinstance(operations, dict) or any(
            not isinstance(key, str) or not isinstance(value, dict)
            for key, value in operations.items()
        ):
            raise PrivateFollowupError("invalid operation history")
        pending = Pending(
            pending_id=_check_text(data.get("pending_id"), "pending_id"),
            bot_id=_check_text(data.get("bot_id"), "bot_id"),
            work_id=_check_text(data.get("work_id"), "work_id"),
            origin_window=_check_session(
                data.get("origin_window"), "origin_window"),
            dm_window=_check_session(data.get("dm_window"), "dm_window"),
            status=status,
            created_at=_check_text(data.get("created_at"), "created_at"),
            updated_at=_check_text(data.get("updated_at"), "updated_at"),
            question=_check_text(data.get("question"), "question"),
            answer_event_id=Pending._opt_str(
                data.get("answer_event_id"), "answer_event_id"),
            answer_ref=Pending._opt_str(
                data.get("answer_ref"), "answer_ref"),
            proposed_redacted=Pending._opt_str(
                data.get("proposed_redacted"), "proposed_redacted"),
            proposal_revision=revision,
            requested_outcome=outcome,
            share_policy=data.get("share_policy", "private"),
            approved_revision=Pending._opt_int(
                data.get("approved_revision"), "approved_revision"),
            create_idempotency=Pending._opt_str(
                data.get("create_idempotency"), "create_idempotency"),
            receive_idempotency=Pending._opt_str(
                data.get("receive_idempotency"), "receive_idempotency"),
            decide_idempotency=Pending._opt_str(
                data.get("decide_idempotency"), "decide_idempotency"),
            posted_marker=Pending._opt_str(
                data.get("posted_marker"), "posted_marker"),
            approve_marker=Pending._opt_str(
                data.get("approve_marker"), "approve_marker"),
            operations=operations,
        )
        _check_bot(pending.bot_id)
        if PENDING_ID_RE.fullmatch(pending.pending_id) is None:
            raise PrivateFollowupError("invalid pending_id")
        if WORK_REF_RE.fullmatch(pending.work_id) is None:
            raise PrivateFollowupError("invalid work_id")
        try:
            parse_ts(pending.created_at)
            parse_ts(pending.updated_at)
        except (TypeError, ValueError) as exc:
            raise PrivateFollowupError("invalid pending timestamp") from exc
        if pending.share_policy not in ("private", "redacted"):
            raise PrivateFollowupError("invalid share_policy")
        if pending.proposed_redacted is not None and (
            not pending.proposed_redacted.strip() or pending.proposal_revision < 1
        ):
            raise PrivateFollowupError("invalid proposal")
        if pending.approved_revision is not None and (
            pending.approved_revision != pending.proposal_revision
            or pending.proposed_redacted is None
            or pending.requested_outcome != "resume"
            or pending.status not in ("received", "resumed")
            or pending.approve_marker is None
        ):
            raise PrivateFollowupError("invalid approved revision")
        if pending.status in ("received", "resumed", "done") and not pending.answer_event_id:
            raise PrivateFollowupError("received pending has no answer reference")
        if pending.status == "resumed" and (
            pending.approved_revision is None
            or pending.posted_marker != pending.approve_marker
        ):
            raise PrivateFollowupError("resumed pending has no approved post")
        return pending


class PrivateFollowupStore:
    """Durable per-bot pending store (atomic + lock + 0600)."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def _path(self, bot_id: str) -> Path:
        return self._root / "bots" / bot_id / "workspace" / "private_followups.json"

    def _lock_path(self, bot_id: str) -> Path:
        return self._root / "bots" / bot_id / "workspace" / "private_followups.lock"

    def _load(self, bot_id: str) -> list[Pending]:
        path = self._path(bot_id)
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError) as exc:
            raise PrivateFollowupError(f"corrupt private_followups: {exc}") from exc
        if (
            not isinstance(data, dict)
            or data.get("version") != 1
            or not isinstance(data.get("pending"), list)
        ):
            raise PrivateFollowupError("corrupt private_followups: bad shape")
        pendings = [Pending.from_dict(raw) for raw in data["pending"]]
        ids = [p.pending_id for p in pendings]
        if len(ids) != len(set(ids)):
            raise PrivateFollowupError("duplicate pending_id in store")
        actives = [p for p in pendings if p.status in NONTERMINAL]
        if len(actives) > 1:
            raise PrivateFollowupError("multiple active pendings in store")
        for pending in pendings:
            if pending.bot_id != bot_id:
                raise PrivateFollowupError("pending bot does not match file")
        return pendings

    def _save(self, bot_id: str, pendings: list[Pending]) -> None:
        path = self._path(bot_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        records = [p.to_dict() for p in pendings]
        for record in records:
            Pending.from_dict(record)
        doc = {"version": 1, "pending": records}
        atomic_write_text(path, json.dumps(doc, ensure_ascii=False))
        path.chmod(0o600)

    def _txn(self, bot_id: str):
        from thyca.config.store import FileLock

        return FileLock(self._lock_path(bot_id))

    def active(self, bot_id: str) -> Pending | None:
        _check_bot(bot_id)
        for pending in self._load(bot_id):
            if pending.status in NONTERMINAL:
                return pending
        return None

    def get(self, bot_id: str, pending_id: str) -> Pending | None:
        _check_bot(bot_id)
        for pending in self._load(bot_id):
            if pending.pending_id == pending_id:
                return pending
        return None

    def create(
        self,
        bot_id: str,
        *,
        work_id: str,
        origin_window: str,
        dm_window: str,
        question: str,
        idempotency_key: str | None = None,
    ) -> Pending:
        """Create a pending, or return the matching idempotent one.

        An idempotent replay must carry the SAME work/question/windows —
        a mismatched payload with a reused key is a conflict, not a dup.
        """
        _check_bot(bot_id)
        if not isinstance(work_id, str) or not WORK_REF_RE.fullmatch(work_id):
            raise PrivateFollowupError(f"invalid work_id: {work_id!r}")
        _check_session(origin_window, "origin_window")
        _check_session(dm_window, "dm_window")
        _check_text(question, "question")
        if idempotency_key is not None:
            _check_text(idempotency_key, "idempotency_key")
        with self._txn(bot_id):
            pendings = self._load(bot_id)
            for pending in pendings:
                if (
                    idempotency_key is not None
                    and pending.create_idempotency == idempotency_key
                ):
                    if (
                        pending.work_id == work_id
                        and pending.question == question
                        and pending.origin_window == origin_window
                        and pending.dm_window == dm_window
                    ):
                        return pending
                    raise PendingConflict(
                        "idempotency key replayed with different payload")
            for pending in pendings:
                if pending.status in TERMINAL:
                    continue
                raise PendingConflict(
                    f"pending already active: {pending.pending_id}"
                )
            now = format_ts(utc_now())
            pending = Pending(
                pending_id=f"pf-{uuid.uuid4().hex[:12]}",
                bot_id=bot_id,
                work_id=work_id,
                origin_window=origin_window,
                dm_window=dm_window,
                status="open",
                created_at=now,
                updated_at=now,
                question=question,
                create_idempotency=idempotency_key,
            )
            pendings.append(pending)
            self._save(bot_id, pendings)
            return pending

    def _find(self, pendings: list[Pending], pending_id: str) -> Pending:
        _check_text(pending_id, "pending_id")
        pending = next(
            (p for p in pendings if p.pending_id == pending_id), None
        )
        if pending is None:
            raise PrivateFollowupError(f"unknown pending: {pending_id}")
        return pending

    @staticmethod
    def _replayed(pending: Pending, operation: str, key: str | None, payload: dict) -> bool:
        if key is None:
            return False
        _check_text(key, "idempotency_key")
        marker = f"{operation}:{key}"
        if marker in pending.operations:
            if pending.operations[marker] != payload:
                raise PendingConflict("idempotency key replayed with different payload")
            return True
        return False

    def _commit(
        self, bot_id: str, pendings: list[Pending], pending: Pending,
        operation: str, key: str | None, payload: dict,
    ) -> Pending:
        if key is not None:
            pending.operations[f"{operation}:{key}"] = payload
        pending.updated_at = format_ts(utc_now())
        self._save(bot_id, pendings)
        return pending

    def mark_waiting(self, bot_id: str, pending_id: str) -> Pending:
        _check_bot(bot_id)
        with self._txn(bot_id):
            pendings = self._load(bot_id)
            pending = self._find(pendings, pending_id)
            if pending.status == "waiting_private":
                return pending
            if pending.status != "open":
                raise PrivateFollowupError(
                    f"cannot wait from {pending.status}")
            pending.status = "waiting_private"
            pending.updated_at = format_ts(utc_now())
            self._save(bot_id, pendings)
            return pending

    def receive(
        self,
        bot_id: str,
        pending_id: str,
        *,
        answer_event_id: str,
        answer_ref: str | None = None,
        idempotency_key: str | None = None,
    ) -> Pending:
        """DM answer landed — waiting_private -> received (ref only)."""
        _check_bot(bot_id)
        _check_text(answer_event_id, "answer_event_id")
        if answer_ref is not None:
            _check_text(answer_ref, "answer_ref")
        payload = {"answer_event_id": answer_event_id, "answer_ref": answer_ref}
        with self._txn(bot_id):
            pendings = self._load(bot_id)
            pending = self._find(pendings, pending_id)
            if self._replayed(pending, "receive", idempotency_key, payload):
                return pending
            if pending.status != "waiting_private":
                raise PrivateFollowupError(
                    f"cannot receive from {pending.status}")
            pending.status = "received"
            pending.answer_event_id = answer_event_id
            pending.answer_ref = answer_ref
            pending.receive_idempotency = idempotency_key
            return self._commit(bot_id, pendings, pending, "receive", idempotency_key, payload)

    def decide(
        self,
        bot_id: str,
        pending_id: str,
        *,
        action: str,
        redacted: str | None = None,
        idempotency_key: str | None = None,
    ) -> Pending:
        """Bot resolves its own pending: ready|resume|done|cancel."""
        _check_bot(bot_id)
        if action not in OUTCOMES:
            raise PrivateFollowupError("invalid pending action")
        if redacted is not None and not isinstance(redacted, str):
            raise PrivateFollowupError("redacted_text must be a string")
        redacted = redacted.strip() if redacted is not None else None
        if action == "resume" and not redacted:
            raise PrivateFollowupError("resume needs redacted_text")
        if action != "resume" and redacted is not None:
            raise PrivateFollowupError("redacted_text only applies to resume")
        payload = {"action": action, "redacted": redacted}
        with self._txn(bot_id):
            pendings = self._load(bot_id)
            pending = self._find(pendings, pending_id)
            if self._replayed(pending, "decide", idempotency_key, payload):
                return pending
            if pending.status in TERMINAL or (
                pending.status != "received" and action != "cancel"
            ):
                raise PrivateFollowupError(
                    f"cannot decide from {pending.status}")
            if action == "ready":
                pending.requested_outcome = "ready"
                pending.proposed_redacted = None
                pending.approved_revision = None
                pending.approve_marker = None
                pending.share_policy = "private"
            elif action == "resume":
                if not redacted or not redacted.strip():
                    raise PrivateFollowupError("resume needs redacted_text")
                pending.proposed_redacted = redacted.strip()
                pending.proposal_revision += 1
                pending.requested_outcome = "resume"
                pending.approved_revision = None
                pending.approve_marker = None
                pending.share_policy = "private"
            elif action == "done":
                pending.status = "done"
                pending.requested_outcome = "done"
                pending.approved_revision = None
                pending.approve_marker = None
                pending.share_policy = "private"
            elif action == "cancel":
                pending.status = "cancelled"
                pending.requested_outcome = "cancel"
                pending.approved_revision = None
                pending.approve_marker = None
                pending.share_policy = "private"
            pending.decide_idempotency = idempotency_key
            return self._commit(bot_id, pendings, pending, "decide", idempotency_key, payload)

    def approve(
        self,
        bot_id: str,
        pending_id: str,
        *,
        revision: int,
        idempotency_key: str | None = None,
    ) -> Pending:
        """Stage user approval for the exact stored proposal revision.

        This records authorization only — the caller lands the group
        row, then calls :meth:`finalize_resumed`. Idempotent by key and
        by already-applied marker."""
        _check_bot(bot_id)
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            raise PrivateFollowupError("invalid proposal revision")
        marker = idempotency_key or f"{pending_id}:{revision}"
        payload = {"revision": revision}
        with self._txn(bot_id):
            pendings = self._load(bot_id)
            pending = self._find(pendings, pending_id)
            if self._replayed(pending, "approve", marker, payload):
                if pending.approved_revision != revision or pending.approve_marker != marker:
                    raise PendingConflict("approval is no longer current")
                return pending
            if pending.status != "received" or pending.requested_outcome != "resume":
                raise PrivateFollowupError(
                    f"cannot approve from {pending.status}")
            if revision != pending.proposal_revision or pending.proposed_redacted is None:
                raise PrivateFollowupError("stale or missing proposal")
            pending.approved_revision = revision
            pending.approve_marker = marker
            pending.share_policy = "redacted"
            return self._commit(bot_id, pendings, pending, "approve", marker, payload)

    def finalize_resumed(self, bot_id: str, pending_id: str, *, posted_marker: str) -> Pending:
        """Group row landed for the approved revision -> resumed."""
        _check_bot(bot_id)
        _check_text(posted_marker, "posted_marker")
        with self._txn(bot_id):
            pendings = self._load(bot_id)
            pending = self._find(pendings, pending_id)
            if pending.status == "resumed":
                if pending.posted_marker != posted_marker:
                    raise PendingConflict("different resumed marker")
                return pending
            if (
                pending.status != "received"
                or pending.requested_outcome != "resume"
                or pending.approved_revision != pending.proposal_revision
                or pending.approved_revision is None
                or pending.approve_marker != posted_marker
                or not pending.proposed_redacted
            ):
                raise PrivateFollowupError("no approved revision to finalize")
            pending.status = "resumed"
            pending.posted_marker = posted_marker
            pending.updated_at = format_ts(utc_now())
            self._save(bot_id, pendings)
            return pending

    def keep_private(self, bot_id: str, pending_id: str, *, revision: int | None = None) -> Pending:
        """User declined sharing: proposal + approval cleared, ready."""
        _check_bot(bot_id)
        with self._txn(bot_id):
            pendings = self._load(bot_id)
            pending = self._find(pendings, pending_id)
            if revision is not None and (
                not isinstance(revision, int)
                or isinstance(revision, bool)
                or revision != pending.proposal_revision
            ):
                raise PendingConflict("stale proposal revision")
            if pending.status == "received" and pending.proposed_redacted is None:
                pending.requested_outcome = "ready"
            elif pending.status != "received":
                raise PrivateFollowupError(
                    f"cannot keep_private from {pending.status}")
            else:
                pending.proposed_redacted = None
                pending.approved_revision = None
                pending.approve_marker = None
                pending.requested_outcome = "ready"
            pending.share_policy = "private"
            pending.updated_at = format_ts(utc_now())
            self._save(bot_id, pendings)
            return pending

    def cancel(self, bot_id: str, pending_id: str) -> Pending:
        """Cancel from ANY nonterminal state (own DM or origin window)."""
        _check_bot(bot_id)
        with self._txn(bot_id):
            pendings = self._load(bot_id)
            pending = self._find(pendings, pending_id)
            if pending.status == "cancelled":
                return pending
            if pending.status in TERMINAL:
                raise PrivateFollowupError(
                    f"pending is terminal: {pending.status}")
            pending.status = "cancelled"
            pending.requested_outcome = "cancel"
            pending.approved_revision = None
            pending.approve_marker = None
            pending.share_policy = "private"
            pending.updated_at = format_ts(utc_now())
            self._save(bot_id, pendings)
            return pending

    def expire(self, bot_id: str, pending_id: str) -> Pending:
        """Explicit expiry transition (no automatic timer)."""
        _check_bot(bot_id)
        with self._txn(bot_id):
            pendings = self._load(bot_id)
            pending = self._find(pendings, pending_id)
            if pending.status == "expired":
                return pending
            if pending.status in TERMINAL:
                raise PrivateFollowupError(
                    f"pending is terminal: {pending.status}")
            pending.status = "expired"
            pending.approved_revision = None
            pending.approve_marker = None
            pending.share_policy = "private"
            pending.updated_at = format_ts(utc_now())
            self._save(bot_id, pendings)
            return pending
