"""Membership-checked agent-facing read and append operations."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from thyca.core.conversation import ConversationRef
from thyca.core.protocol import Message, utc_now_ts
from .contracts import (DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, ConversationPage, ConversationStoreError,
                        PageCursor, _decode_message, _encode_canonical_message, _local_error,
                        _require_actor, _require_ref, _validate_cursor, _view, validate_message)
from .database import (_ensure_door_ready, _ensure_root_ready, _root_instance_id_from_conn,
                       _transaction_context, open_existing_session)

class AgentStore:
    """Agent-facing facade: membership-checked reads and append only."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def read(self, ref: ConversationRef, actor: str, *,
             cursor: PageCursor | None = None,
             limit: int = DEFAULT_PAGE_LIMIT) -> ConversationPage:
        _require_ref(ref)
        _require_actor(actor)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError('limit must be a positive int')
        if cursor is not None:
            _validate_cursor(cursor, ref)
        limit = min(limit, MAX_PAGE_LIMIT)
        conn = open_existing_session(self._root)
        try:
            with _transaction_context(conn, 'BEGIN'):
                _ensure_root_ready(conn, _root_instance_id_from_conn(conn))
                metadata, generation, event_high, logical_high = _ensure_door_ready(conn, ref, actor)
                start = cursor.after_logical_seq if cursor is not None else 0
                current_cursor = cursor or PageCursor(ref, generation, 0)
                if (cursor is not None and cursor.generation != generation):
                    return _resync_page(ref, metadata, generation)
                if start > logical_high:
                    return _resync_page(ref, metadata, generation)
                if start != 0:
                    try:
                        position = conn.execute(
                            'SELECT 1 FROM messages WHERE kind=? AND id=? AND logical_seq=?',
                            (ref.kind, ref.id, start),
                        ).fetchone()
                    except sqlite3.Error as exc:
                        raise _local_error(ref) from exc
                    if position is None:
                        return _resync_page(ref, metadata, generation)
                try:
                    rows = conn.execute(
                        'SELECT data,logical_seq FROM messages WHERE kind=? AND id=? AND logical_seq>? '
                        'ORDER BY logical_seq LIMIT ?',
                        (ref.kind, ref.id, start, limit + 1),
                    ).fetchall()
                except sqlite3.Error as exc:
                    raise _local_error(ref) from exc
                has_more = len(rows) > limit
                selected = rows[:limit]
                messages = tuple(_decode_message(raw) for raw, _ in selected)
                if selected:
                    next_cursor = PageCursor(ref, generation, selected[-1][1])
                else:
                    next_cursor = current_cursor
                return ConversationPage(_view(ref, metadata, messages), next_cursor, has_more, False)
        finally:
            conn.close()

    def append(self, ref: ConversationRef, actor: str, message: Message) -> int:
        _require_ref(ref)
        _require_actor(actor)
        if not isinstance(message, Message):
            raise ValueError('expected message')
        validate_message(message)
        payload = _encode_canonical_message(message)
        conn = open_existing_session(self._root)
        try:
            with _transaction_context(conn, 'BEGIN IMMEDIATE'):
                _ensure_root_ready(conn, _root_instance_id_from_conn(conn))
                _metadata, _generation, high_event, high_logical = _ensure_door_ready(conn, ref, actor)
                seq, logical = high_event + 1, high_logical + 1
                try:
                    conn.execute(
                        'INSERT INTO messages(kind,id,seq,logical_seq,role,content,data,created_at) '
                        'VALUES(?,?,?,?,?,?,?,?)',
                        (ref.kind, ref.id, seq, logical, message.role, message.content,
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

def _resync_page(ref: ConversationRef, metadata: dict, generation: int) -> ConversationPage:
    return ConversationPage(_view(ref, metadata, ()), PageCursor(ref, generation, 0), False, True)

