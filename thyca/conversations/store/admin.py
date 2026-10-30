"""Admin-facing conversation lifecycle, retention, and compaction."""
from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

from thyca.core.protocol import Message, parse_ts, utc_now_ts
from .agent import AgentStore
from .contracts import (BootstrapResult, Conversation, ConversationRef, ConversationStoreError,
                        DMBootstrapIntent, InitializationResult, RootInitializationIntent,
                        _check_text, _decode_message, _encode_canonical_message, _encode_metadata,
                        _local_error, _metadata, _require_ref, _view, validate_metadata)
from .database import (_ensure_door_ready, _ensure_root_ready, _read_door_row,
                       _root_instance_id_from_conn, _transaction_context, open_existing_session)
from .lifecycle import _audit_in_transaction, audit_root, bootstrap_dm, initialize_root

class _GroupIdCollision(Exception):
    """Internal signal for retrying only an explicit group-ID collision."""

class AdminStore:
    """Synthetic-root admin operations; never wire to runtime without review."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def agent(self) -> AgentStore:
        return AgentStore(self._root)

    def initialize(self, intent: RootInitializationIntent) -> InitializationResult:
        return initialize_root(self._root, intent)

    def audit(self) -> None:
        return audit_root(self._root)

    def bootstrap_initial_dm(self, bot_id: str, *, intent: DMBootstrapIntent) -> BootstrapResult:
        return bootstrap_dm(self._root, bot_id, intent=intent)

    def create_group(self, participants: list[str], *, title: str | None = None) -> Conversation:
        metadata = _metadata(participants, title)
        # Validate all user-supplied data before filesystem access.
        ref = ConversationRef.new_group()
        validate_metadata(ref, metadata)
        conn = open_existing_session(self._root, lock_mode='shared')
        try:
            with _transaction_context(conn, 'BEGIN IMMEDIATE'):
                _ensure_root_ready(conn, _root_instance_id_from_conn(conn))
                for _ in range(10):
                    ref = ConversationRef.new_group()
                    try:
                        conn.execute(
                            "INSERT INTO conversations(kind,id,metadata,created_at,lifecycle_state,"
                            "generation,event_high_water,logical_high_water) "
                            "VALUES('group',?,?,?,'ready',0,0,0)",
                            (ref.id, _encode_metadata(metadata), utc_now_ts()),
                        )
                        break
                    except sqlite3.IntegrityError as exc:
                        if 'conversations.kind, conversations.id' not in str(exc).lower():
                            raise ConversationStoreError() from exc
                else:
                    raise ConversationStoreError('group identifier collision')
            return _view(ref, metadata, ())
        except sqlite3.Error as exc:
            raise ConversationStoreError() from exc
        finally:
            conn.close()

    def set_metadata(self, ref: ConversationRef, *, title: str | None,
                     title_source: str | None, naming_attempted: bool,
                     participants: list[str]) -> Conversation:
        _require_ref(ref)
        metadata = {'title': title, 'title_source': title_source,
                    'naming_attempted': naming_attempted, 'participants': participants}
        validate_metadata(ref, metadata)
        conn = open_existing_session(self._root)
        try:
            with _transaction_context(conn, 'BEGIN IMMEDIATE'):
                _ensure_root_ready(conn, _root_instance_id_from_conn(conn))
                _ensure_door_ready(conn, ref)
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

    def purge_before(self, cutoff: str) -> int:
        parse_ts(cutoff)
        conn = open_existing_session(self._root, lock_mode='shared')
        try:
            with _transaction_context(conn, 'BEGIN IMMEDIATE'):
                _audit_in_transaction(conn, full=False)
                try:
                    affected = conn.execute(
                        'SELECT DISTINCT kind,id FROM messages WHERE created_at<? ORDER BY kind,id',
                        (cutoff,),
                    ).fetchall()
                    conn.execute('DROP TRIGGER no_delete_messages')
                    cur = conn.execute('DELETE FROM messages WHERE created_at<?', (cutoff,))
                    removed = cur.rowcount
                    for kind, identifier in affected:
                        conn.execute(
                            'UPDATE conversations SET generation=generation+1 WHERE kind=? AND id=?',
                            (kind, identifier),
                        )
                    conn.execute(
                        "CREATE TRIGGER no_delete_messages BEFORE DELETE ON messages BEGIN "
                        "SELECT RAISE(ABORT, 'messages are append-only'); END"
                    )
                except sqlite3.Error as exc:
                    raise ConversationStoreError() from exc
            return removed
        finally:
            conn.close()

    def compact_through_seq(self, ref: ConversationRef, through_seq: int,
                            summarize: Callable[[tuple[Message, ...]], str]) -> Message:
        _require_ref(ref)
        if isinstance(through_seq, bool) or not isinstance(through_seq, int) or through_seq < 1:
            raise ValueError('through_seq must be a positive logical position')
        if not callable(summarize):
            raise ValueError('summarize must be callable')
        # Snapshot phase: lock and read transaction close before decoding or callback.
        conn = open_existing_session(self._root, lock_mode='shared')
        try:
            with _transaction_context(conn, 'BEGIN'):
                _ensure_root_ready(conn, _root_instance_id_from_conn(conn))
                _metadata, generation, _event_high, logical_high = _ensure_door_ready(conn, ref)
                if through_seq > logical_high:
                    raise ValueError('logical position exceeds snapshot high-water')
                try:
                    raw_rows = tuple(conn.execute(
                        'SELECT seq,logical_seq,role,content,data,created_at FROM messages '
                        'WHERE kind=? AND id=? AND logical_seq<=? ORDER BY logical_seq',
                        (ref.kind, ref.id, through_seq),
                    ).fetchall())
                except sqlite3.Error as exc:
                    raise _local_error(ref) from exc
        finally:
            conn.close()
        if not raw_rows:
            raise ConversationStoreError('nothing to compact', scope='local', ref=ref)
        effective = raw_rows[-1][1]
        old = tuple(_decode_message(row[4]) for row in raw_rows)
        summary_text = summarize(old)
        _check_text(summary_text, 'summary')
        summary = Message(role='system', content=summary_text,
                          meta={'compacted': True, 'through_seq': effective})
        payload = _encode_canonical_message(summary)

        # Final phase permits only a pure concurrent append suffix. Purge or
        # another compaction changes generation and conflicts without retry.
        conn = open_existing_session(self._root, lock_mode='shared')
        try:
            with _transaction_context(conn, 'BEGIN IMMEDIATE'):
                _ensure_root_ready(conn, _root_instance_id_from_conn(conn))
                _metadata, current_generation, event_high, logical_high = _ensure_door_ready(conn, ref)
                if current_generation != generation:
                    raise ConversationStoreError('compaction conflict', scope='local', ref=ref)
                try:
                    current = tuple(conn.execute(
                        'SELECT seq,logical_seq,role,content,data,created_at FROM messages '
                        'WHERE kind=? AND id=? AND logical_seq<=? ORDER BY logical_seq',
                        (ref.kind, ref.id, effective),
                    ).fetchall())
                except sqlite3.Error as exc:
                    raise _local_error(ref) from exc
                if current != raw_rows:
                    raise ConversationStoreError('compaction conflict', scope='local', ref=ref)
                conn.execute('DROP TRIGGER no_delete_messages')
                conn.execute(
                    'DELETE FROM messages WHERE kind=? AND id=? AND logical_seq<=?',
                    (ref.kind, ref.id, effective),
                )
                new_seq = event_high + 1
                try:
                    conn.execute(
                        'INSERT INTO messages(kind,id,seq,logical_seq,role,content,data,created_at) '
                        'VALUES(?,?,?,?,?,?,?,?)',
                        (ref.kind, ref.id, new_seq, effective, 'system', summary_text,
                         payload, utc_now_ts()),
                    )
                    conn.execute(
                        'UPDATE conversations SET generation=generation+1,event_high_water=?,'
                        'logical_high_water=? WHERE kind=? AND id=?',
                        (new_seq, max(logical_high, effective), ref.kind, ref.id),
                    )
                    conn.execute(
                        "CREATE TRIGGER no_delete_messages BEFORE DELETE ON messages BEGIN "
                        "SELECT RAISE(ABORT, 'messages are append-only'); END"
                    )
                except sqlite3.Error as exc:
                    raise _local_error(ref) from exc
            return summary
        finally:
            conn.close()

    def vacuum(self) -> None:
        conn = open_existing_session(self._root, lock_mode='exclusive')
        try:
            with _transaction_context(conn, 'BEGIN'):
                _audit_in_transaction(conn, full=True)
            try:
                conn.execute('VACUUM')
            except sqlite3.Error as exc:
                raise ConversationStoreError() from exc
        finally:
            conn.close()

