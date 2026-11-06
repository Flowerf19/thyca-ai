"""Phase 2 fixture-only write route: store writes without history rewrites.

Every entry point requires an explicit :class:`SyntheticWriteIntent` and an
explicit ``actor`` bot id. There is no ``current_bot_id`` fallback anywhere
in this module: the caller chain must thread the trusted actor down to each
call. Reads still go through cursor-paged ``AgentStore.read``; the write
route never mutates stored rows:

- retry does not truncate. :func:`prepare_retry` only inspects history and
  returns the last-user position plus the current generation; a concurrent
  purge/compaction surfaces as a bounded resync, never as a rewrite.
- turn errors do not mutate. :func:`append_turn_error` inserts a new trailing
  ``system`` row carrying ``meta.error``; the failed user/assistant rows keep
  their bytes.
- metadata updates are one atomic transaction (membership check, row update,
  and full-view re-read under the same ``BEGIN IMMEDIATE``).
- conversation deletion is the explicit whole-door :meth:`AdminStore.delete`
  (messages plus row in one transaction, trigger restored); DM markers are
  kept as tombstones so bot-id reuse stays blocked.

Fixture gating: a store root must be explicitly enabled via
:func:`enable_fixture_writes` before any write entry point touches the
filesystem. The live ``~/.thyca`` root can never be enabled, so it stays on
the legacy JSONL path. While a fixture root is enabled, legacy
``SessionStore`` mutations under that root fail closed (see
``fixture_switch``).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from thyca.core.conversation import ConversationRef
from thyca.core.protocol import Message, utc_now_ts

from .admin import AdminStore
from .agent import AgentStore
from .contracts import (
    MAX_PAGE_LIMIT,
    Conversation,
    ConversationStoreError,
    PageCursor,
    _check_text,
    _decode_message,
    _encode_canonical_message,
    _encode_metadata,
    _local_error,
    _require_actor,
    _require_ref,
    _view,
    validate_metadata,
)
from .database import (
    _ensure_door_ready,
    _ensure_root_ready,
    _root_instance_id_from_conn,
    _transaction_context,
    open_existing_session,
)
from .fixture_switch import (
    disable_fixture_writes,
    enable_fixture_writes,
    is_fixture_write_enabled,
    is_legacy_writer_blocked,
)

__all__ = [
    'SyntheticWriteIntent',
    'RetryReadiness',
    'enable_fixture_writes',
    'disable_fixture_writes',
    'is_fixture_write_enabled',
    'is_legacy_writer_blocked',
    'create_group',
    'append_message',
    'append_server_message',
    'append_server_row',
    'set_conversation_metadata',
    'delete_conversation',
    'prepare_retry',
    'append_turn_error',
]

#: Bound on resync restarts while inspecting history for a retry decision.
_MAX_RETRY_RESYNCS = 5


@dataclass(frozen=True)
class SyntheticWriteIntent:
    """Explicit caller assertion for a fixture-root write.

    This value is not authentication: it only forces the caller to state
    that the write targets a disposable fixture root enabled via
    :func:`enable_fixture_writes`. The live root can never be enabled, so
    it stays on the legacy path.
    """

    purpose: str

    def __post_init__(self) -> None:
        if not isinstance(self.purpose, str) or not self.purpose.strip():
            raise ValueError('purpose must be a non-empty string')


def _require_intent(intent: object):
    from .runtime import RuntimeWriteIntent as _RuntimeWriteIntent

    if isinstance(intent, (SyntheticWriteIntent, _RuntimeWriteIntent)):
        return intent
    raise ValueError('explicit synthetic or runtime write intent required')


def _require_enabled_root(root: Path, intent=None) -> Path:
    """Fixture gate for Synthetic intents, runtime-ready gate for Runtime."""
    if not isinstance(root, Path):
        raise ValueError('root must be a Path')
    from .runtime import RuntimeWriteIntent as _RuntimeWriteIntent
    from .runtime import is_runtime_ready as _is_runtime_ready

    if isinstance(intent, _RuntimeWriteIntent):
        if not _is_runtime_ready(root):
            raise ValueError('runtime store is not initialized for this root')
        return root
    if not is_fixture_write_enabled(root):
        raise ValueError('fixture write route is not enabled for this root')
    return root


def _check_page_limit(page_limit: object) -> int:
    if isinstance(page_limit, bool) or not isinstance(page_limit, int) or page_limit < 1:
        raise ValueError('page_limit must be a positive int')
    return min(page_limit, MAX_PAGE_LIMIT)


def create_group(
    root: Path,
    participants: list[str],
    *,
    intent: SyntheticWriteIntent,
    actor: str,
    title: str | None = None,
) -> Conversation:
    """Create a group on an enabled fixture root; *actor* must participate."""
    _require_intent(intent)
    _require_enabled_root(root, intent)
    _require_actor(actor)
    if not isinstance(participants, list) or actor not in participants:
        raise ValueError('actor must be one of the participants')
    return AdminStore(root).create_group(list(participants), title=title)


def append_message(
    root: Path,
    ref: ConversationRef,
    actor: str,
    message: Message,
    *,
    intent: SyntheticWriteIntent,
) -> int:
    """Append one user/assistant message with an explicit actor; no fallback."""
    _require_intent(intent)
    _require_enabled_root(root, intent)
    _require_ref(ref)
    _require_actor(actor)
    if not isinstance(message, Message):
        raise ValueError('expected message')
    return AgentStore(root).append(ref, actor, message)


# Append one user row authorized by the server-side caller, not a bot.
def append_server_message(
    root: Path,
    ref: ConversationRef,
    message: Message,
    *,
    intent: SyntheticWriteIntent,
) -> int:
    """Append one ``user`` row with no bot actor; no membership is faked.

    Only the trusted server-side turn flow calls this: the user (not a
    bot) authored the row, so bot membership must not authorize it. Root
    and door readiness are still checked inside the same ``BEGIN
    IMMEDIATE`` transaction as the insert. Bot-authored rows must use
    :func:`append_message` with the acting bot instead.
    """
    _require_intent(intent)
    _require_enabled_root(root, intent)
    _require_ref(ref)
    if not isinstance(message, Message) or message.role != 'user':
        raise ValueError('server append carries user rows only')
    from .contracts import validate_message

    validate_message(message)
    payload = _encode_canonical_message(message)
    conn = open_existing_session(root, lock_mode='exclusive')
    try:
        with _transaction_context(conn, 'BEGIN IMMEDIATE'):
            _ensure_root_ready(conn, _root_instance_id_from_conn(conn))
            # Door readiness without an actor: no bot membership is claimed.
            _metadata, _generation, high_event, high_logical = _ensure_door_ready(
                conn, ref
            )
            seq, logical = high_event + 1, high_logical + 1
            try:
                conn.execute(
                    'INSERT INTO messages(kind,id,seq,logical_seq,role,content,data,created_at) '
                    'VALUES(?,?,?,?,?,?,?,?)',
                    (ref.kind, ref.id, seq, logical, 'user', message.content,
                     payload, utc_now_ts()),
                )
                conn.execute(
                    'UPDATE conversations SET event_high_water=?,logical_high_water=? '
                    'WHERE kind=? AND id=?',
                    (seq, logical, ref.kind, ref.id),
                )
            except sqlite3.Error as exc:
                raise _local_error(ref) from exc
        return seq
    finally:
        conn.close()


# Append one server-recorded A2A display row without claiming bot membership.
def append_server_row(
    root: Path,
    ref: ConversationRef,
    message: Message,
    *,
    intent: SyntheticWriteIntent,
) -> int:
    """Append one A2A display row recorded by the trusted server flow.

    Only ``A2ADeliveryService`` produces these shapes (``bus_event`` rows
    and ``kind=result`` replies): the pair is gated there by DM ownership,
    and the non-owner side is never a DM participant, so no bot membership
    can honestly authorize the row. Root and door readiness are still
    checked inside the same ``BEGIN IMMEDIATE`` transaction as the insert.
    """
    _require_intent(intent)
    _require_enabled_root(root, intent)
    _require_ref(ref)
    if not isinstance(message, Message) or message.role != 'assistant':
        raise ValueError('server row carries assistant rows only')
    meta = message.meta if isinstance(message.meta, dict) else {}
    if not (meta.get('bus_event') is True or meta.get('kind') == 'result'):
        raise ValueError('server row must be an A2A display row')
    from .contracts import validate_message

    validate_message(message)
    payload = _encode_canonical_message(message)
    conn = open_existing_session(root, lock_mode='exclusive')
    try:
        with _transaction_context(conn, 'BEGIN IMMEDIATE'):
            _ensure_root_ready(conn, _root_instance_id_from_conn(conn))
            _metadata, _generation, high_event, high_logical = _ensure_door_ready(
                conn, ref
            )
            seq, logical = high_event + 1, high_logical + 1
            try:
                conn.execute(
                    'INSERT INTO messages(kind,id,seq,logical_seq,role,content,data,created_at) '
                    'VALUES(?,?,?,?,?,?,?,?)',
                    (ref.kind, ref.id, seq, logical, 'assistant', message.content,
                     payload, utc_now_ts()),
                )
                conn.execute(
                    'UPDATE conversations SET event_high_water=?,logical_high_water=? '
                    'WHERE kind=? AND id=?',
                    (seq, logical, ref.kind, ref.id),
                )
            except sqlite3.Error as exc:
                raise _local_error(ref) from exc
        return seq
    finally:
        conn.close()


def set_conversation_metadata(
    root: Path,
    ref: ConversationRef,
    *,
    intent: SyntheticWriteIntent,
    actor: str,
    title: str | None,
    title_source: str | None,
    naming_attempted: bool,
    participants: list[str],
) -> Conversation:
    """Atomically replace title/source/attempted/participants in one transaction.

    The actor membership check, the row update, and the full-view re-read all
    run under the same ``BEGIN IMMEDIATE``: either the whole metadata record
    changes together or nothing does. *actor* must already participate in the
    door (checked inside the transaction, so a concurrent removal races
    closed instead of authorizing a stale writer).
    """
    _require_intent(intent)
    _require_enabled_root(root, intent)
    _require_ref(ref)
    _require_actor(actor)
    metadata = {
        'title': title,
        'title_source': title_source,
        'naming_attempted': naming_attempted,
        'participants': list(participants),
    }
    validate_metadata(ref, metadata)
    conn = open_existing_session(root, lock_mode='exclusive')
    try:
        with _transaction_context(conn, 'BEGIN IMMEDIATE'):
            _ensure_root_ready(conn, _root_instance_id_from_conn(conn))
            # Membership is part of the atomic write: a removed actor fails
            # here, inside the same transaction as the update.
            _ensure_door_ready(conn, ref, actor)
            try:
                cur = conn.execute(
                    'UPDATE conversations SET metadata=? WHERE kind=? AND id=?',
                    (_encode_metadata(metadata), ref.kind, ref.id),
                )
                if cur.rowcount != 1:
                    raise _local_error(ref)
                rows = conn.execute(
                    'SELECT data FROM messages WHERE kind=? AND id=? ORDER BY logical_seq',
                    (ref.kind, ref.id),
                ).fetchall()
                messages = tuple(_decode_message(data) for (data,) in rows)
            except sqlite3.Error as exc:
                raise _local_error(ref) from exc
            return _view(ref, metadata, messages)
    finally:
        conn.close()


def delete_conversation(
    root: Path,
    ref: ConversationRef,
    *,
    intent: SyntheticWriteIntent,
    actor: str,
) -> None:
    """Delete one whole conversation on an enabled fixture root.

    Membership-checked (actor must currently participate; DMs: the owner).
    Deletes the door's messages plus its row in one transaction and restores
    the append-only trigger. DM markers are kept as tombstones, so reusing
    the same bot id stays blocked after a DM delete.
    """
    _require_intent(intent)
    _require_enabled_root(root, intent)
    _require_ref(ref)
    _require_actor(actor)
    conn = open_existing_session(root, lock_mode='exclusive')
    try:
        with _transaction_context(conn, 'BEGIN IMMEDIATE'):
            _ensure_root_ready(conn, _root_instance_id_from_conn(conn))
            _ensure_door_ready(conn, ref, actor)
            try:
                conn.execute('DROP TRIGGER no_delete_messages')
                conn.execute(
                    'DELETE FROM messages WHERE kind=? AND id=?',
                    (ref.kind, ref.id),
                )
                cur = conn.execute(
                    'DELETE FROM conversations WHERE kind=? AND id=?',
                    (ref.kind, ref.id),
                )
                if cur.rowcount != 1:
                    raise _local_error(ref)
                conn.execute(
                    "CREATE TRIGGER no_delete_messages BEFORE DELETE ON messages BEGIN "
                    "SELECT RAISE(ABORT, 'messages are append-only'); END"
                )
            except sqlite3.Error as exc:
                raise _local_error(ref) from exc
    finally:
        conn.close()


@dataclass(frozen=True)
class RetryReadiness:
    """No-mutation retry decision: where the last user row sits today."""

    ref: ConversationRef
    has_user: bool
    last_user_logical_seq: int | None
    generation: int
    message_count: int


def prepare_retry(
    root: Path,
    ref: ConversationRef,
    *,
    intent: SyntheticWriteIntent,
    actor: str,
    page_limit: int = 500,
) -> RetryReadiness:
    """Inspect history for a retry without deleting or rewriting anything.

    Returns whether a ``role=user`` row exists and its logical position. A
    mid-inspection generation change (purge/compaction under the reader)
    discards the partial prefix and restarts from the reset cursor, up to
    ``_MAX_RETRY_RESYNCS`` times. Never mutates: no trigger drop, no DELETE,
    no UPDATE, no generation bump.
    """
    _require_intent(intent)
    _require_enabled_root(root, intent)
    _require_ref(ref)
    _require_actor(actor)
    page_limit = _check_page_limit(page_limit)
    store = AgentStore(root)
    collected: list[Message] = []
    cursor: PageCursor | None = None
    generation = 0
    restarts = 0
    while True:
        page = store.read(ref, actor, cursor=cursor, limit=page_limit)
        if page.resync_required:
            restarts += 1
            if restarts > _MAX_RETRY_RESYNCS:
                raise ConversationStoreError('conversation unavailable')
            collected = []
            cursor = page.cursor
            continue
        generation = page.cursor.generation
        collected.extend(page.messages)
        if not page.has_more:
            break
        cursor = page.cursor
    # Exact logical positions come from the same ordering the pager used.
    # A final shared read resolves role/logical_seq plus the authoritative
    # generation; a mismatch against the paged generation means a
    # purge/compaction landed between the two reads, so restart bounded.
    for _attempt in range(_MAX_RETRY_RESYNCS + 1):
        conn = open_existing_session(root, lock_mode='shared')
        try:
            with _transaction_context(conn, 'BEGIN'):
                _ensure_root_ready(conn, _root_instance_id_from_conn(conn))
                _meta, final_generation, _ev, _log = _ensure_door_ready(conn, ref, actor)
                try:
                    rows = conn.execute(
                        'SELECT role,logical_seq FROM messages WHERE kind=? AND id=? ORDER BY logical_seq',
                        (ref.kind, ref.id),
                    ).fetchall()
                except sqlite3.Error as exc:
                    raise _local_error(ref) from exc
        finally:
            conn.close()
        if final_generation == generation and len(rows) == len(collected):
            break
        # Concurrent writer moved the door: re-assemble from the reset cursor.
        collected = []
        cursor = None
        restarts = 0
        while True:
            page = store.read(ref, actor, cursor=cursor, limit=page_limit)
            if page.resync_required:
                restarts += 1
                if restarts > _MAX_RETRY_RESYNCS:
                    raise ConversationStoreError('conversation unavailable')
                collected = []
                cursor = page.cursor
                continue
            generation = page.cursor.generation
            collected.extend(page.messages)
            if not page.has_more:
                break
            cursor = page.cursor
    else:
        raise ConversationStoreError('conversation unavailable')
    last_user_seq: int | None = None
    for role, logical_seq in rows:
        if role == 'user':
            last_user_seq = int(logical_seq)
    return RetryReadiness(
        ref=ref,
        has_user=last_user_seq is not None,
        last_user_logical_seq=last_user_seq,
        generation=final_generation,
        message_count=len(rows),
    )


def append_turn_error(
    root: Path,
    ref: ConversationRef,
    *,
    intent: SyntheticWriteIntent,
    actor: str,
    code: str,
    message: str,
) -> int:
    """Record a turn failure as a new trailing ``system`` row; no mutation.

    The failed user/assistant rows keep their bytes. The marker carries
    ``meta.error = {code, message}`` so Trace can surface it without
    rewriting history. Membership-checked; one ``INSERT`` under
    ``BEGIN IMMEDIATE``; generation is unchanged (only purge/compaction
    bump generation).
    """
    _require_intent(intent)
    _require_enabled_root(root, intent)
    _require_ref(ref)
    _require_actor(actor)
    _check_text(code, 'code')
    _check_text(message, 'message')
    if not code.strip() or not message.strip():
        raise ValueError('code and message must be non-empty')
    marker = Message(
        role='system',
        content=message,
        meta={'error': {'code': code, 'message': message}},
    )
    payload = _encode_canonical_message(marker)
    conn = open_existing_session(root, lock_mode='exclusive')
    try:
        with _transaction_context(conn, 'BEGIN IMMEDIATE'):
            _ensure_root_ready(conn, _root_instance_id_from_conn(conn))
            _metadata, _generation, high_event, high_logical = _ensure_door_ready(
                conn, ref, actor
            )
            seq, logical = high_event + 1, high_logical + 1
            try:
                conn.execute(
                    'INSERT INTO messages(kind,id,seq,logical_seq,role,content,data,created_at) '
                    'VALUES(?,?,?,?,?,?,?,?)',
                    (ref.kind, ref.id, seq, logical, 'system', message, payload, utc_now_ts()),
                )
                conn.execute(
                    'UPDATE conversations SET event_high_water=?,logical_high_water=? '
                    'WHERE kind=? AND id=?',
                    (seq, logical, ref.kind, ref.id),
                )
            except sqlite3.Error as exc:
                raise _local_error(ref) from exc
        return seq
    finally:
        conn.close()
