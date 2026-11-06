from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path

from thyca.configs import DEFAULT_TIMELINE_TIMEZONE, LimitsCfg
from thyca.core.protocol import Message

from .compaction import SessionCompactor
from .errors import SessionBusy, SessionError
from .models import Session
from .store import SessionStore
from .title import (
    is_blank,
    mark_naming_attempted,
    refresh_title,
    rename_session,
    set_title,
    set_title_if_missing,
)


def _last_user_index(messages: list[Message]) -> int | None:
    """Index of the last user message, or None when there is none.

    The one reverse scan shared by ``truncate_to_last_user`` and
    ``mark_turn_error``."""
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].role == "user":
            return index
    return None


def _manager_runtime_root(manager) -> Path | None:
    """Runtime store root for this manager, or None for legacy/canonical."""
    try:
        from thyca.bots.timeline import CanonicalSessionStore

        if isinstance(getattr(manager, "store", None), CanonicalSessionStore):
            return None
    except Exception:
        pass
    try:
        from thyca.conversations.runtime.store import _runtime_root_for

        return _runtime_root_for(manager.sessions_dir)
    except Exception:
        return None


# Report whether this manager wraps the canonical timeline store.
def _is_canonical_store(manager) -> bool:
    try:
        from thyca.bots.timeline import CanonicalSessionStore

        return isinstance(getattr(manager, "store", None), CanonicalSessionStore)
    except Exception:
        return False


class SessionManager:
    """Orchestrate store + compactor behind a single-process lock."""

    # Build a manager over an explicit sessions directory or store.
    def __init__(
        self,
        sessions_dir: Path | None = None,
        limits: LimitsCfg | None = None,
        timezone_name: str | None = None,
        store: SessionStore | None = None,
        compactor: SessionCompactor | None = None,
    ) -> None:
        self.limits = limits or LimitsCfg()
        self.timezone_name = timezone_name or DEFAULT_TIMELINE_TIMEZONE
        if store is None and sessions_dir is None:
            raise SessionError("sessions_dir or store is required")
        self.store = store or SessionStore(Path(sessions_dir))
        self.sessions_dir = self.store.sessions_dir
        self.compactor = compactor or SessionCompactor()
        self._lock = threading.Lock()
        self._session: Session | None = None

    def _current_locked(self) -> Session:
        """Return the current session. The caller must hold ``self._lock``."""
        if self._session is None:
            raise SessionError("no current session — call create/load/continue_last first")
        return self._session

    @property
    def current(self) -> Session:
        with self._lock:
            return self._current_locked()

    # Create one new group door in the runtime conversation store.
    def create(self, *, make_current: bool = True) -> Session:
        with self._lock:
            runtime_root = _manager_runtime_root(self)
            if runtime_root is not None:
                # Runtime: new group doors mint group-... ids via the store
                # (transition: never mint legacy timestamps for new rooms).
                from thyca.conversations.store.admin import AdminStore
                from thyca.conversations.store.runtime import RuntimeWriteIntent
                from thyca.conversations.store.write_adapter import create_group

                self.store.ensure_dir()
                session = None
                for _ in range(10):
                    try:
                        conv = create_group(
                            runtime_root, ["thyca"],
                            intent=RuntimeWriteIntent("runtime-create"),
                            actor="thyca",
                        )
                        break
                    except Exception as exc:
                        from thyca.conversations.store.contracts import (
                            ConversationStoreError,
                        )

                        if isinstance(exc, ConversationStoreError):
                            continue
                        raise
                else:
                    raise SessionError("session filename collision")
                from thyca.conversations.runtime.store import _runtime_session_path

                path = _runtime_session_path(self.sessions_dir, conv.ref.id)
                session = Session(
                    conv.ref.id, path, [],
                    title=conv.title, title_source=conv.title_source,
                    naming_attempted=conv.naming_attempted,
                    bots=list(conv.participants), dm_bot=None,
                )
                if make_current:
                    self._session = session
                return session
            raise SessionError("runtime store not initialized for this root")

    def load(self, session_id: str) -> Session:
        with self._lock:
            session = self.store.load(session_id)
            self._session = session
            return session

    def continue_last(self) -> Session:
        with self._lock:
            session = self.store.latest()
            self._session = session
            return session

    def list_sessions(self) -> list[Session]:
        with self._lock:
            sessions: list[Session] = []
            for path in self.store.list_paths():
                try:
                    sessions.append(self.store.load(path.stem))
                except SessionError:
                    continue
            return sessions

    # Find or create one bot's fixed DM in the runtime store.
    def ensure_dm(self, bot_id: str) -> Session:
        """Find-or-create one bot's fixed DM (phase 2B shared helper).

        Only a session already carrying the ``dm`` marker is adopted;
        otherwise a fresh session is created and marked (bots + dm).
        No locking: single-threaded callers use it directly, while
        ChatApp.bot_dm wraps it in ``_dm_lock`` for the concurrent
        one-DM guarantee."""
        runtime_root = _manager_runtime_root(self)
        if runtime_root is not None:
            # Runtime: DM doors bootstrap via AdminStore (tombstone-safe).
            import uuid as _uuid

            from thyca.conversations.store.admin import AdminStore
            from thyca.conversations.store.contracts import (
                ConversationStoreError,
                DMBootstrapIntent,
            )

            # Fast path: existing DM in the store listing.
            try:
                items, _ = AdminStore(runtime_root).list(limit=5000)
                for row in items:
                    if row.ref.kind == "dm" and row.ref.id == bot_id:
                        # load() takes its own lock; do not hold self._lock
                        # across it (same pattern as the legacy path below).
                        return self.load(row.ref.id)
            except ConversationStoreError:
                pass
            attempt = _uuid.uuid4().hex
            try:
                result = AdminStore(runtime_root).bootstrap_initial_dm(
                    bot_id, intent=DMBootstrapIntent(bot_id, attempt)
                )
            except ConversationStoreError:
                # Lost a concurrent bootstrap race, or a tombstoned DM
                # re-bootstrap attempt: re-list and adopt the winner when
                # present so concurrent first lookups still mint exactly
                # one DM id (the CBE-5 guarantee).
                try:
                    items, _ = AdminStore(runtime_root).list(limit=5000)
                    for row in items:
                        if row.ref.kind == "dm" and row.ref.id == bot_id:
                            return self.load(row.ref.id)
                except ConversationStoreError:
                    pass
                raise SessionError("cannot ensure DM") from None
            return self.load(result.ref.id)
        raise SessionError("runtime store not initialized for this root")

    def discard_empty(self, keep: set[str] | None = None) -> list[str]:
        """Drop blank sessions except the ones named in ``keep``.

        ``keep`` is every session with an in-flight turn: a turn writes its
        first message a moment after it starts, so an empty file may still
        belong to a running turn. Sessions marked as a bot's fixed DM
        (``dm_bot``) are kept the same way: a DM is created lazily and
        stays blank until its first turn, but it is the bot's one main
        window — pruning it would silently mint a second DM later.
        """
        with self._lock:
            removed: list[str] = []
            for path in self.store.list_paths():
                try:
                    session = self.store.load(path.stem)
                except SessionError:
                    continue
                if keep and session.id in keep:
                    continue
                if session.dm_bot:
                    continue
                if not is_blank(session):
                    continue
                self.store.delete(session.id)
                if self._session is not None and self._session.id == session.id:
                    self._session = None
                removed.append(session.id)
            return removed

    # Append one row to the current session as the acting bot.
    def append(self, msg: Message, *, actor: str | None = None) -> None:
        with self._lock:
            session = self._current_locked()
            self.store.append(session.path, msg, actor=actor)
            session.messages.append(msg)

    # Check retry readiness for the acting bot without rewriting history.
    def truncate_to_last_user(self, *, actor: str | None = None) -> bool:
        """Drop messages after the last user. No-op if the tail is already a user.

        A stale failure marker on the kept user message is stripped: a retry
        starts clean, and re-marks itself only if it fails again.
        Returns False when the transcript has no ``role=user`` message.
        """
        # Runtime store is append-only: retry never rewrites history.
        # Readiness (last-user position) comes from the no-mutation
        # prepare_retry path; the trailing system error marker stays on
        # disk and is filtered from the model feed by Assemble.
        runtime_root = _manager_runtime_root(self)
        if runtime_root is not None:
            with self._lock:
                session = self._current_locked()
                from thyca.conversations.store.runtime import (
                    RuntimeWriteIntent,
                    resolve_ref,
                )
                from thyca.conversations.store.write_adapter import prepare_retry

                ref = resolve_ref(runtime_root, session.id)
                # Read-only readiness: the acting bot when known, else a
                # stored member reader. No write is authorized here.
                reader = actor or (ref.id if ref.kind == "dm" else None)
                if reader is None:
                    reader = (session.bots or ["thyca"])[0]
                plan = prepare_retry(
                    runtime_root, ref,
                    intent=RuntimeWriteIntent("runtime-retry-readiness"),
                    actor=reader,
                )
                return bool(plan.has_user)
        if not _is_canonical_store(self):
            raise SessionError("runtime store not initialized for this root")
        with self._lock:
            session = self._current_locked()
            messages = session.messages
            last = _last_user_index(messages)
            if last is None:
                return False
            stripped = False
            if (messages[last].meta or {}).get("error") is not None:
                kept_user = messages[last]
                cleaned = {
                    key: value
                    for key, value in (kept_user.meta or {}).items()
                    if key != "error"
                }
                messages[last] = replace(kept_user, meta=cleaned or None)
                stripped = True
            if last == len(messages) - 1 and not stripped:
                return True
            kept = messages[: last + 1]
            self.store.rewrite(
                session.id,
                session.path,
                kept,
                title=session.title,
                title_source=session.title_source,
                naming_attempted=session.naming_attempted,
            )
            session.messages[:] = kept
            return True

    # Record one turn failure for the acting bot without rewriting rows.
    def mark_turn_error(self, code: str, message: str, *, actor: str | None = None) -> bool:
        """Stamp the turn's user message with the failure and persist it.

        The marker is what Trace shows for a failed turn; the live chat keeps
        using the stream terminal. Returns False when there is no user
        message to mark. Disk failures raise SessionError — callers that must
        not mask the original error guard this call.
        """
        # Runtime store is append-only: failures append a trailing system
        # marker (meta.error) instead of stamping the user row in place.
        # Trace projects the marker back onto its turn's user message in
        # memory, so failed turns still read as failed without rewriting
        # stored bytes.
        runtime_root = _manager_runtime_root(self)
        if runtime_root is not None:
            with self._lock:
                session = self._current_locked()
                from thyca.conversations.store.runtime import (
                    RuntimeWriteIntent,
                    resolve_ref,
                )
                from thyca.conversations.store.turn_pipeline import record_turn_error
                from thyca.conversations.store.write_adapter import prepare_retry

                ref = resolve_ref(runtime_root, session.id)
                # The acting bot records its own failure; membership is
                # checked inside the store transactions below.
                acting = actor or (ref.id if ref.kind == "dm" else None)
                if acting is None:
                    acting = (session.bots or ["thyca"])[0]
                plan = prepare_retry(
                    runtime_root, ref,
                    intent=RuntimeWriteIntent("runtime-error-readiness"),
                    actor=acting,
                )
                if not plan.has_user:
                    return False
                record_turn_error(
                    runtime_root, ref,
                    intent=RuntimeWriteIntent("runtime-record-error"),
                    actor=acting, code=code, message=message,
                )
                # Keep the in-memory view consistent with the store: the
                # marker row is a system message carrying meta.error.
                from thyca.core.protocol import Message as _Message

                session.messages.append(
                    _Message(
                        role="system", content=message,
                        meta={"error": {"code": code, "message": message}},
                    )
                )
                return True
        if not _is_canonical_store(self):
            raise SessionError("runtime store not initialized for this root")
        with self._lock:
            session = self._current_locked()
            messages = session.messages
            last = _last_user_index(messages)
            if last is None:
                return False
            marked = dict(messages[last].meta or {})
            marked["error"] = {"code": code, "message": message}
            messages[last] = replace(messages[last], meta=marked)
            self.store.rewrite(
                session.id,
                session.path,
                messages,
                title=session.title,
                title_source=session.title_source,
                naming_attempted=session.naming_attempted,
            )
            return True

    def compact_if_needed(
        self,
        *,
        hot_tokens: int = 0,
        tools_tokens: int = 0,
        pending_user_tokens: int = 0,
        calibration: float = 1.0,
    ) -> bool:
        with self._lock:
            return self._compact_locked(
                force=False,
                hot_tokens=hot_tokens,
                tools_tokens=tools_tokens,
                pending_user_tokens=pending_user_tokens,
                calibration=calibration,
            )["compacted"]

    def compact_manual(
        self,
        *,
        hot_tokens: int = 0,
        tools_tokens: int = 0,
        calibration: float = 1.0,
    ) -> dict:
        """User-triggered compact: same tail policy, no 95% threshold.

        Returns exact before/after message counts so callers can report
        what happened without re-deriving marker arithmetic. A transcript
        that already fits the tail budget is an honest no-op
        (``compacted`` False, counts equal), never a junk marker.
        """
        with self._lock:
            return self._compact_locked(
                force=True,
                hot_tokens=hot_tokens,
                tools_tokens=tools_tokens,
                pending_user_tokens=0,
                calibration=calibration,
            )

    def _compact_locked(
        self,
        *,
        force: bool,
        hot_tokens: int,
        tools_tokens: int,
        pending_user_tokens: int,
        calibration: float = 1.0,
    ) -> dict:
        # Runtime store compaction is append-only: the prefix is replaced
        # by one system summary row via compact_through_seq (no rewrite).
        # Summaries reuse the legacy "[compaction: ...; excerpt: ...]"
        # shape so Assemble still hoists them as compaction markers.
        runtime_root = _manager_runtime_root(self)
        if runtime_root is not None:
            session = self._current_locked()
            # Rescan from the store so a concurrent append cannot be lost:
            # scan() reassembles via cursor-paged reads with bounded resync.
            on_disk, title, title_source, attempted = self.store.scan(session.path)
            session.messages[:] = on_disk
            session.naming_attempted = session.naming_attempted or attempted
            if title:
                session.title = title
                session.title_source = title_source
            before = len(on_disk)
            plan = self.compactor.plan(
                on_disk,
                self.limits.contextTokens,
                hot_tokens=hot_tokens,
                tools_tokens=tools_tokens,
                pending_user_tokens=pending_user_tokens,
                force=force,
                calibration=calibration,
            )
            if plan is None:
                return {
                    "compacted": False,
                    "messages_before": before,
                    "messages_after": before,
                }
            body = self.compactor.excerpt_body(plan)
            summary_text = (
                f"[compaction: omitted {plan.total_messages} messages/"
                f"{plan.total_turns} turns; excerpt: {body}]"
            )
            from thyca.conversations.store.admin import AdminStore
            from thyca.conversations.store.runtime import resolve_ref
            from thyca.conversations.store.database import (
                _ensure_root_ready,
                _root_instance_id_from_conn,
                _transaction_context,
                open_existing_session,
            )

            ref = resolve_ref(runtime_root, session.id)
            conn = open_existing_session(runtime_root, lock_mode="shared")
            try:
                with _transaction_context(conn, "BEGIN"):
                    _ensure_root_ready(conn, _root_instance_id_from_conn(conn))
                    rows = conn.execute(
                        "SELECT logical_seq FROM messages WHERE kind=? AND id=? "
                        "ORDER BY logical_seq",
                        (ref.kind, ref.id),
                    ).fetchall()
            finally:
                conn.close()
            if len(rows) != len(on_disk):
                raise SessionError("compaction read raced")
            keep = len(plan.tail)
            if keep >= len(rows):
                return {
                    "compacted": False,
                    "messages_before": before,
                    "messages_after": before,
                }
            through_seq = int(rows[len(rows) - keep - 1][0]) if keep else int(rows[-1][0])
            try:
                AdminStore(runtime_root).compact_through_seq(
                    ref, through_seq, lambda _old, _text=summary_text: _text,
                )
            except Exception as exc:
                from thyca.conversations.store.contracts import ConversationStoreError

                if isinstance(exc, ConversationStoreError):
                    raise SessionError("compaction failed") from exc
                raise
            after_messages, after_title, after_source, after_attempted = self.store.scan(
                session.path
            )
            session.messages[:] = after_messages
            if after_title:
                session.title = after_title
                session.title_source = after_source
            session.naming_attempted = session.naming_attempted or after_attempted
            return {
                "compacted": True,
                "messages_before": before,
                "messages_after": len(after_messages),
            }
        if not _is_canonical_store(self):
            raise SessionError("runtime store not initialized for this root")
        session = self._current_locked()
        on_disk, title, title_source, attempted = self.store.scan(session.path)
        session.messages[:] = on_disk
        session.naming_attempted = session.naming_attempted or attempted
        if title:
            session.title = title
            session.title_source = title_source
        before = len(on_disk)
        compacted = self.compactor.compact(
            on_disk,
            self.limits.contextTokens,
            hot_tokens=hot_tokens,
            tools_tokens=tools_tokens,
            pending_user_tokens=pending_user_tokens,
            force=force,
            calibration=calibration,
        )
        if compacted is None:
            return {
                "compacted": False,
                "messages_before": before,
                "messages_after": before,
            }
        self.store.rewrite(
            session.id,
            session.path,
            compacted,
            title=session.title,
            title_source=session.title_source,
            naming_attempted=session.naming_attempted,
        )
        session.messages[:] = compacted
        return {
            "compacted": True,
            "messages_before": before,
            "messages_after": len(compacted),
        }

    def apply_compaction(
        self, compacted: list[Message], *, expected: list[Message]
    ) -> bool:
        """Rewrite with a caller-rendered compaction if disk still matches.

        The LLM summary path plans from a snapshot, then spends seconds
        summarizing off-lock. Rewriting blindly would lose anything
        appended meanwhile, so the on-disk transcript must still equal
        ``expected`` (title meta lines are not messages and are picked up
        from the rescan either way). False means: caller falls back to a
        fresh rule-based compact; current is left at the rescanned state.
        """
        runtime_root = _manager_runtime_root(self)
        if runtime_root is not None:
            with self._lock:
                session = self._current_locked()
                on_disk, title, title_source, attempted = self.store.scan(session.path)
                session.messages[:] = on_disk
                session.naming_attempted = session.naming_attempted or attempted
                if title:
                    session.title = title
                    session.title_source = title_source
                if on_disk != list(expected):
                    return False
                if not compacted or compacted[0].role != "system":
                    return False
                summary_text = compacted[0].content or ""
                tail_len = len(compacted) - 1
                from thyca.conversations.store.admin import AdminStore
                from thyca.conversations.store.runtime import resolve_ref
                from thyca.conversations.store.database import (
                    _ensure_root_ready,
                    _root_instance_id_from_conn,
                    _transaction_context,
                    open_existing_session,
                )

                ref = resolve_ref(runtime_root, session.id)
                conn = open_existing_session(runtime_root, lock_mode="shared")
                try:
                    with _transaction_context(conn, "BEGIN"):
                        _ensure_root_ready(conn, _root_instance_id_from_conn(conn))
                        rows = conn.execute(
                            "SELECT logical_seq FROM messages WHERE kind=? AND id=? "
                            "ORDER BY logical_seq",
                            (ref.kind, ref.id),
                        ).fetchall()
                finally:
                    conn.close()
                if len(rows) != len(on_disk):
                    return False
                if tail_len >= len(rows):
                    return False
                through_seq = int(rows[len(rows) - tail_len - 1][0]) if tail_len else int(rows[-1][0])
                try:
                    AdminStore(runtime_root).compact_through_seq(
                        ref, through_seq, lambda _old, _text=summary_text: _text,
                    )
                except Exception:
                    return False
                after, after_title, after_source, after_attempted = self.store.scan(
                    session.path
                )
                session.messages[:] = after
                if after_title:
                    session.title = after_title
                    session.title_source = after_source
                session.naming_attempted = session.naming_attempted or after_attempted
                return True
        if not _is_canonical_store(self):
            raise SessionError("runtime store not initialized for this root")
        with self._lock:
            session = self._current_locked()
            on_disk, title, title_source, attempted = self.store.scan(session.path)
            session.messages[:] = on_disk
            session.naming_attempted = session.naming_attempted or attempted
            if title:
                session.title = title
                session.title_source = title_source
            if on_disk != list(expected):
                return False
            self.store.rewrite(
                session.id,
                session.path,
                compacted,
                title=session.title,
                title_source=session.title_source,
                naming_attempted=session.naming_attempted,
            )
            session.messages[:] = list(compacted)
            return True

    def refresh_title(self) -> None:
        """Re-read the title a stored meta line carries (see title module)."""
        with self._lock:
            refresh_title(self.store, self._session)

    # Persist the naming attempt for the acting bot.
    def mark_naming_attempted(self, *, actor: str | None = None) -> None:
        """Persist the automatic naming step's one attempt (see title module)."""
        with self._lock:
            mark_naming_attempted(self.store, self._current_locked(), actor=actor)

    # Store the automatic title for the acting bot when none exists.
    def set_title_if_missing(self, title: str, *, actor: str | None = None) -> str | None:
        """Store the automatic title only when no title is on disk."""
        with self._lock:
            return set_title_if_missing(
                self.store, self._current_locked(), title, actor=actor
            )

    def set_title(self, title: str, *, source: str | None = None) -> str | None:
        with self._lock:
            return set_title(self.store, self._current_locked(), title, source=source)

    def rename(self, session_id: str, title: str) -> str:
        """Set the title of any stored session, not only the current one."""
        with self._lock:
            return rename_session(
                self.store, session_id, title, current=self._session
            )

    def set_bots(self, session_id: str, bots: list[str]) -> None:
        """Refresh the in-memory participant list when it is current.

        Disk is owned by ``SessionStore.append_bots``; this only keeps a
        loaded current session from serving a stale roster."""
        with self._lock:
            if self._session is not None and self._session.id == session_id:
                self._session.bots = list(bots)

    def delete(self, session_id: str, *, keep: set[str] | None = None) -> None:
        """Remove a session file outright.

        ``keep`` names sessions with a turn in flight. The caller must pass a
        snapshot that cannot go stale between the check and the unlink — i.e.
        hold the same lock a turn claims under — or a claim landing in that
        window leaves the writer appending to a deleted path, which re-creates
        the file truncated on its next write.
        """
        with self._lock:
            if keep and session_id in keep:
                raise SessionBusy(session_id)
            self.store.delete(session_id)
            if self._session is not None and self._session.id == session_id:
                self._session = None
