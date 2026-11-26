from __future__ import annotations

import threading
import uuid as _uuid
from dataclasses import replace
from pathlib import Path

from thyca.configs import DEFAULT_TIMELINE_TIMEZONE, LimitsCfg
from thyca.conversations.runtime.store import _runtime_conversation_path, _runtime_root_for
from thyca.conversations.store.admin import AdminStore
from thyca.conversations.store.contracts import (
    ConversationStoreError,
    DMBootstrapIntent,
)
from thyca.conversations.store.database import (
    _ensure_root_ready,
    _root_instance_id_from_conn,
    _transaction_context,
    open_existing_conversation,
)
from thyca.conversations.store.runtime import (
    RuntimeWriteIntent,
    resolve_ref,
)
from thyca.conversations.store.turn_pipeline import record_turn_error
from thyca.conversations.store.write_adapter import create_group, prepare_retry
from thyca.core.protocol import Message
from thyca.core.protocol import Message as _Message

from .compaction import ConversationCompactor
from .errors import ConversationBusy, ConversationError
from .models import ConversationRuntime, is_blank
from .store import ConversationStore


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
        from thyca.bots.timeline import CanonicalConversationStore  # noqa: PLC0415 -- reason: break circular import with bots.timeline

        if isinstance(getattr(manager, "store", None), CanonicalConversationStore):
            return None
    except Exception:
        pass
    try:
        return _runtime_root_for(manager.conversations_dir)
    except Exception:
        return None


def _is_canonical_store(manager) -> bool:
    try:
        from thyca.bots.timeline import CanonicalConversationStore  # noqa: PLC0415 -- reason: break circular import with bots.timeline

        return isinstance(getattr(manager, "store", None), CanonicalConversationStore)
    except Exception:
        return False


class ConversationManager:
    """Orchestrate store + compactor behind a single-process lock."""

    def __init__(
        self,
        conversations_dir: Path | None = None,
        limits: LimitsCfg | None = None,
        timezone_name: str | None = None,
        store: ConversationStore | None = None,
        compactor: ConversationCompactor | None = None,
    ) -> None:
        self.limits = limits or LimitsCfg()
        self.timezone_name = timezone_name or DEFAULT_TIMELINE_TIMEZONE
        if store is None and conversations_dir is None:
            raise ConversationError("conversations_dir or store is required")
        self.store = store or ConversationStore(Path(conversations_dir))
        self.conversations_dir = self.store.conversations_dir
        self.compactor = compactor or ConversationCompactor()
        self._lock = threading.Lock()
        self._conversation: ConversationRuntime | None = None

    def _current_locked(self) -> ConversationRuntime:
        """Return the current conversation. The caller must hold ``self._lock``."""
        if self._conversation is None:
            raise ConversationError("no current conversation — call create/load/continue_last first")
        return self._conversation

    @property
    def current(self) -> ConversationRuntime:
        with self._lock:
            return self._current_locked()

    def create(self, *, make_current: bool = True) -> ConversationRuntime:
        with self._lock:
            runtime_root = _manager_runtime_root(self)
            if runtime_root is not None:

                self.store.ensure_dir()
                conversation = None
                for _ in range(10):
                    try:
                        conv = create_group(
                            runtime_root, ["thyca"],
                            intent=RuntimeWriteIntent("runtime-create"),
                            actor="thyca",
                        )
                        break
                    except Exception as exc:

                        if isinstance(exc, ConversationStoreError):
                            continue
                        raise
                else:
                    raise ConversationError("conversation filename collision")

                path = _runtime_conversation_path(self.conversations_dir, conv.ref.id)
                conversation = ConversationRuntime(
                    conv.ref.id, path, [],
                    bots=list(conv.participants), dm_bot=None,
                    title=conv.title,
                )
                if make_current:
                    self._conversation = conversation
                return conversation
            raise ConversationError("runtime store not initialized for this root")

    def load(self, conversation_id: str) -> ConversationRuntime:
        with self._lock:
            conversation = self.store.load(conversation_id)
            self._conversation = conversation
            return conversation

    def continue_last(self) -> ConversationRuntime:
        with self._lock:
            conversation = self.store.latest()
            self._conversation = conversation
            return conversation

    def list_conversations(self) -> list[ConversationRuntime]:
        with self._lock:
            conversations: list[ConversationRuntime] = []
            for path in self.store.list_paths():
                try:
                    conversations.append(self.store.load(path.stem))
                except ConversationError:
                    continue
            return conversations

    def ensure_dm(self, bot_id: str) -> ConversationRuntime:
        """Find-or-create one bot's fixed DM (phase 2B shared helper).

        Only a conversation already carrying the ``dm`` marker is adopted;
        otherwise a fresh conversation is created and marked (bots + dm).
        No locking: single-threaded callers use it directly, while
        ChatApp.bot_dm wraps it in ``_dm_lock`` for the concurrent
        one-DM guarantee."""
        runtime_root = _manager_runtime_root(self)
        if runtime_root is not None:


            try:
                items, _ = AdminStore(runtime_root).list(limit=5000)
                for row in items:
                    if row.ref.kind == "dm" and row.ref.id == bot_id:
                        return self.load(row.ref.id)
            except ConversationStoreError:
                pass
            attempt = _uuid.uuid4().hex
            try:
                result = AdminStore(runtime_root).bootstrap_initial_dm(
                    bot_id, intent=DMBootstrapIntent(bot_id, attempt)
                )
            except ConversationStoreError:
                try:
                    items, _ = AdminStore(runtime_root).list(limit=5000)
                    for row in items:
                        if row.ref.kind == "dm" and row.ref.id == bot_id:
                            return self.load(row.ref.id)
                except ConversationStoreError:
                    pass
                raise ConversationError("cannot ensure DM") from None
            return self.load(result.ref.id)
        raise ConversationError("runtime store not initialized for this root")

    def discard_empty(self, keep: set[str] | None = None) -> list[str]:
        """Drop blank conversations except the ones named in ``keep``.

        ``keep`` is every conversation with an in-flight turn: a turn writes its
        first message a moment after it starts, so an empty file may still
        belong to a running turn. Conversations marked as a bot's fixed DM
        (``dm_bot``) are kept the same way: a DM is created lazily and
        stays blank until its first turn, but it is the bot's one main
        window — pruning it would silently mint a second DM later.
        """
        with self._lock:
            removed: list[str] = []
            for path in self.store.list_paths():
                try:
                    conversation = self.store.load(path.stem)
                except ConversationError:
                    continue
                if keep and conversation.conversation_id in keep:
                    continue
                if conversation.dm_bot:
                    continue
                if not is_blank(conversation):
                    continue
                self.store.delete(conversation.conversation_id)
                if self._conversation is not None and self._conversation.conversation_id == conversation.conversation_id:
                    self._conversation = None
                removed.append(conversation.conversation_id)
            return removed

    def append(self, msg: Message, *, actor: str | None = None) -> None:
        with self._lock:
            conversation = self._current_locked()
            self.store.append(conversation.path, msg, actor=actor)
            conversation.messages.append(msg)

    def truncate_to_last_user(self, *, actor: str | None = None) -> bool:
        """Drop messages after the last user. No-op if the tail is already a user.

        A stale failure marker on the kept user message is stripped: a retry
        starts clean, and re-marks itself only if it fails again.
        Returns False when the transcript has no ``role=user`` message.
        """
        runtime_root = _manager_runtime_root(self)
        if runtime_root is not None:
            with self._lock:
                conversation = self._current_locked()

                ref = resolve_ref(runtime_root, conversation.conversation_id)
                reader = actor or (ref.id if ref.kind == "dm" else None)
                if reader is None:
                    reader = (conversation.bots or ["thyca"])[0]
                plan = prepare_retry(
                    runtime_root, ref,
                    intent=RuntimeWriteIntent("runtime-retry-readiness"),
                    actor=reader,
                )
                return bool(plan.has_user)
        if not _is_canonical_store(self):
            raise ConversationError("runtime store not initialized for this root")
        with self._lock:
            conversation = self._current_locked()
            messages = conversation.messages
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
                conversation.conversation_id,
                conversation.path,
                kept,
            )
            conversation.messages[:] = kept
            return True

    def mark_turn_error(self, code: str, message: str, *, actor: str | None = None) -> bool:
        """Stamp the turn's user message with the failure and persist it.

        The marker is what Trace shows for a failed turn; the live chat keeps
        using the stream terminal. Returns False when there is no user
        message to mark. Disk failures raise ConversationError — callers that must
        not mask the original error guard this call.
        """
        runtime_root = _manager_runtime_root(self)
        if runtime_root is not None:
            with self._lock:
                conversation = self._current_locked()

                ref = resolve_ref(runtime_root, conversation.conversation_id)
                acting = actor or (ref.id if ref.kind == "dm" else None)
                if acting is None:
                    acting = (conversation.bots or ["thyca"])[0]
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

                conversation.messages.append(
                    _Message(
                        role="system", content=message,
                        meta={"error": {"code": code, "message": message}},
                    )
                )
                return True
        if not _is_canonical_store(self):
            raise ConversationError("runtime store not initialized for this root")
        with self._lock:
            conversation = self._current_locked()
            messages = conversation.messages
            last = _last_user_index(messages)
            if last is None:
                return False
            marked = dict(messages[last].meta or {})
            marked["error"] = {"code": code, "message": message}
            messages[last] = replace(messages[last], meta=marked)
            self.store.rewrite(
                conversation.conversation_id,
                conversation.path,
                messages,
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
        drop_prior: bool = False,
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
                drop_prior=drop_prior,
            )

    def _compact_locked(
        self,
        *,
        force: bool,
        hot_tokens: int,
        tools_tokens: int,
        pending_user_tokens: int,
        calibration: float = 1.0,
        drop_prior: bool = False,
    ) -> dict:
        runtime_root = _manager_runtime_root(self)
        if runtime_root is not None:
            conversation = self._current_locked()
            on_disk = self.store.scan(conversation.path)
            conversation.messages[:] = on_disk
            before = len(on_disk)
            plan = self.compactor.plan(
                on_disk,
                self.limits.contextTokens,
                hot_tokens=hot_tokens,
                tools_tokens=tools_tokens,
                pending_user_tokens=pending_user_tokens,
                force=force,
                calibration=calibration,
                drop_prior=drop_prior,
            )
            if plan is None:
                return {
                    "compacted": False,
                    "messages_before": before,
                    "messages_after": before,
                }
            body = self.compactor.excerpt_body(plan)
            summary_text = self.compactor.render(plan, body)[0].content or ""

            ref = resolve_ref(runtime_root, conversation.conversation_id)
            conn = open_existing_conversation(runtime_root, lock_mode="shared")
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
                raise ConversationError("compaction read raced")
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

                if isinstance(exc, ConversationStoreError):
                    raise ConversationError("compaction failed") from exc
                raise
            after_messages = self.store.scan(
                conversation.path
            )
            conversation.messages[:] = after_messages
            return {
                "compacted": True,
                "messages_before": before,
                "messages_after": len(after_messages),
            }
        if not _is_canonical_store(self):
            raise ConversationError("runtime store not initialized for this root")
        conversation = self._current_locked()
        on_disk = self.store.scan(conversation.path)
        conversation.messages[:] = on_disk
        before = len(on_disk)
        compacted = self.compactor.compact(
            on_disk,
            self.limits.contextTokens,
            hot_tokens=hot_tokens,
            tools_tokens=tools_tokens,
            pending_user_tokens=pending_user_tokens,
            force=force,
            calibration=calibration,
            drop_prior=drop_prior,
        )
        if compacted is None:
            return {
                "compacted": False,
                "messages_before": before,
                "messages_after": before,
            }
        self.store.rewrite(
            conversation.conversation_id,
            conversation.path,
            compacted,
        )
        conversation.messages[:] = compacted
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
        ``expected``. False means: caller falls back to a
        fresh rule-based compact; current is left at the rescanned state.
        """
        runtime_root = _manager_runtime_root(self)
        if runtime_root is not None:
            with self._lock:
                conversation = self._current_locked()
                on_disk = self.store.scan(conversation.path)
                conversation.messages[:] = on_disk
                if on_disk != list(expected):
                    return False
                if not compacted or compacted[0].role != "system":
                    return False
                summary_text = compacted[0].content or ""
                tail_len = len(compacted) - 1

                ref = resolve_ref(runtime_root, conversation.conversation_id)
                conn = open_existing_conversation(runtime_root, lock_mode="shared")
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
                after = self.store.scan(
                    conversation.path
                )
                conversation.messages[:] = after
                return True
        if not _is_canonical_store(self):
            raise ConversationError("runtime store not initialized for this root")
        with self._lock:
            conversation = self._current_locked()
            on_disk = self.store.scan(conversation.path)
            conversation.messages[:] = on_disk
            if on_disk != list(expected):
                return False
            self.store.rewrite(
                conversation.conversation_id,
                conversation.path,
                compacted,
            )
            conversation.messages[:] = list(compacted)
            return True

    def set_bots(self, conversation_id: str, bots: list[str]) -> None:
        """Refresh the in-memory participant list when it is current.

        Disk is owned by ``ConversationStore.append_bots``; this only keeps a
        loaded current conversation from serving a stale roster."""
        with self._lock:
            if self._conversation is not None and self._conversation.conversation_id == conversation_id:
                self._conversation.bots = list(bots)

    def update_group_title(self, conversation_id: str, title: str | None) -> None:
        """Refresh the in-memory group title when it is current.

        Disk is owned by ``ConversationStore.set_group_title``; this only
        keeps a loaded current conversation from serving a stale title."""
        with self._lock:
            if self._conversation is not None and self._conversation.conversation_id == conversation_id:
                self._conversation.title = title

    def delete(self, conversation_id: str, *, keep: set[str] | None = None) -> None:
        """Remove a conversation file outright.

        ``keep`` names conversations with a turn in flight. The caller must pass a
        snapshot that cannot go stale between the check and the unlink — i.e.
        hold the same lock a turn claims under — or a claim landing in that
        window leaves the writer appending to a deleted path, which re-creates
        the file truncated on its next write.
        """
        with self._lock:
            if keep and conversation_id in keep:
                raise ConversationBusy(conversation_id)
            self.store.delete(conversation_id)
            if self._conversation is not None and self._conversation.conversation_id == conversation_id:
                self._conversation = None
