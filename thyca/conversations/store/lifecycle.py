"""Root/DM lifecycle handshakes, commit-unknown resolution, and full audit."""
from __future__ import annotations

import fcntl
import os
import sqlite3
from pathlib import Path

from thyca.core.protocol import utc_now_ts
from .contracts import (BootstrapResult, ConversationRef, ConversationStoreError, DMBootstrapIntent,
                        InitializationResult, RootInitializationIntent, _decode_message,
                        _decode_metadata, _encode_metadata, _local_error, _metadata, _view,
                        validate_metadata)
from .database import (_LockedConnection, _commit_final, _connect_raw, _create_pending_database,
                       _ensure_root_ready, _raw_connection_for_resolution, _read_door_row,
                       _root_instance_id_from_conn, _transaction_context, _validate_db_file,
                       _validate_door_row, open_existing_session)
from .rootfs import (DM_MARKERS_NAME, DM_MARKER_MAX_BYTES, LOCK_FILE_NAME, ROOT_MARKER_MAX_BYTES,
                     ROOT_MARKER_NAME, ROOT_MARKER_TEMP, _create_marker_directory, _fsync_dir,
                     _marker_filename,
                     _open_root, _open_store_lock, _parse_marker_bytes, _publish_marker,
                     _read_dm_marker, _read_marker_at, _reserved_root_entries, _stat_at,
                     _validate_root_artifacts, _validate_top_namespace, _verify_root_name)

def _audit_in_transaction(conn: _LockedConnection, *, full: bool) -> None:
    root_instance_id = _root_instance_id_from_conn(conn)
    _ensure_root_ready(conn, root_instance_id)
    _validate_top_namespace(conn.root_fd)
    # First pass streams every marker and validates it against the same SQLite
    # snapshot. It retains no per-bot set in memory.
    for entry in os.scandir(conn.marker_fd):
        name = entry.name
        if name.startswith('.') and name.endswith('.json.tmp'):
            bot_id = name[1:-9]
            try:
                ref = ConversationRef.dm(bot_id)
            except ValueError as exc:
                raise ConversationStoreError() from exc
            raise _local_error(ref)
        if not name.endswith('.json'):
            raise ConversationStoreError()
        bot_id = name[:-5]
        try:
            ref = ConversationRef.dm(bot_id)
        except ValueError as exc:
            raise ConversationStoreError() from exc
        marker = _read_dm_marker(conn.marker_fd, bot_id, root_instance_id)
        row = _read_door_row(conn, ref)
        _validate_door_row(ref, row)
        if marker['dm'] != 'ready':
            raise _local_error(ref)
    # Second pass proves no ready DM row is missing its exact marker and checks
    # each record independently, keeping memory bounded per record.
    try:
        doors = conn.execute(
            'SELECT kind,id,metadata,lifecycle_state,generation,event_high_water,logical_high_water '
            'FROM conversations ORDER BY kind,id'
        )
        while True:
            batch = doors.fetchmany(1)
            if not batch:
                break
            kind, identifier, raw_metadata, state, generation, event_high, logical_high = batch[0]
            try:
                ref = ConversationRef(kind, identifier)
            except ValueError as exc:
                raise ConversationStoreError() from exc
            row = (raw_metadata, state, generation, event_high, logical_high)
            _validate_door_row(ref, row)
            if ref.kind == 'dm':
                marker = _read_dm_marker(conn.marker_fd, ref.id, root_instance_id)
                if marker['dm'] != 'ready':
                    raise _local_error(ref)
            elif ref.kind == 'group':
                pass
            try:
                maxima = conn.execute(
                    'SELECT MAX(seq),MAX(logical_seq) FROM messages WHERE kind=? AND id=?',
                    (ref.kind, ref.id),
                ).fetchone()
                if ((maxima[0] is not None and maxima[0] > event_high)
                        or (maxima[1] is not None and maxima[1] > logical_high)):
                    raise _local_error(ref, 'conversation corrupt')
                if full:
                    messages = conn.execute(
                        'SELECT data FROM messages WHERE kind=? AND id=? ORDER BY seq',
                        (ref.kind, ref.id),
                    )
                    while True:
                        records = messages.fetchmany(1)
                        if not records:
                            break
                        try:
                            _decode_message(records[0][0])
                        except ConversationStoreError as exc:
                            raise _local_error(ref, 'conversation corrupt') from exc
            except sqlite3.Error as exc:
                raise _local_error(ref) from exc
    except sqlite3.Error as exc:
        raise ConversationStoreError() from exc
    if full:
        try:
            checks = conn.execute('PRAGMA integrity_check')
            while True:
                rows = checks.fetchmany(1)
                if not rows:
                    break
                if rows[0][0] != 'ok':
                    raise ConversationStoreError()
            foreign = conn.execute('PRAGMA foreign_key_check')
            if foreign.fetchmany(1):
                raise ConversationStoreError()
        except sqlite3.Error as exc:
            raise ConversationStoreError() from exc

def _publish_dm_marker(marker_fd: int, bot_id: str, root_id: str,
                       state: str, attempt_id: str) -> None:
    name = _marker_filename(bot_id)
    temp = f'.{bot_id}.json.tmp'
    data = {'attempt_id': attempt_id, 'bot_id': bot_id, 'dm': state,
            'root_instance_id': root_id, 'version': 1}
    _publish_marker(marker_fd, name, temp, data, max_bytes=DM_MARKER_MAX_BYTES)

def _resolve_root_ready(conn: _LockedConnection, root_id: str) -> bool:
    try:
        marker = _read_marker_at(conn.root_fd, ROOT_MARKER_NAME, max_bytes=ROOT_MARKER_MAX_BYTES)
        if marker != {'root_instance_id': root_id, 'state': 'ready', 'version': 1}:
            return False
        raw = _raw_connection_for_resolution(conn)
        try:
            row = raw.execute('SELECT root_instance_id,state FROM root_state WHERE singleton=1').fetchone()
            return row == (root_id, 'ready')
        finally:
            raw.close()
    except Exception:
        return False

def _resolve_dm_ready(conn: _LockedConnection, ref: ConversationRef,
                      root_id: str, attempt_id: str) -> bool:
    try:
        marker = _read_dm_marker(conn.marker_fd, ref.id, root_id)
        if marker['dm'] != 'ready' or marker['attempt_id'] != attempt_id:
            return False
        raw = _raw_connection_for_resolution(conn)
        try:
            row = raw.execute(
                "SELECT lifecycle_state FROM conversations WHERE kind='dm' AND id=?", (ref.id,)
            ).fetchone()
            return row == ('ready',)
        finally:
            raw.close()
    except Exception:
        return False

def initialize_root(root: Path, intent: RootInitializationIntent) -> InitializationResult:
    if not isinstance(intent, RootInitializationIntent):
        raise ValueError('explicit initialization intent required')
    parent_fd, root_fd, root_name, absolute = _open_root(root, create=True)
    lock_fd = marker_fd = -1
    try:
        try:
            fcntl.flock(root_fd, fcntl.LOCK_EX)
        except OSError as exc:
            raise ConversationStoreError() from exc
        _verify_root_name(parent_fd, root_name, root_fd)
        reserved = _reserved_root_entries(root_fd)
        lock_exists = LOCK_FILE_NAME in reserved
        other = reserved - {LOCK_FILE_NAME}
        if not lock_exists and other:
            raise ConversationStoreError()
        lock_fd, created_lock = _open_store_lock(root_fd, create=not lock_exists)
        if created_lock:
            _fsync_dir(root_fd)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
        except OSError as exc:
            raise ConversationStoreError() from exc
        if not created_lock:
            # Existing artifacts are never adopted or repaired. Only a fully
            # ready matching instance may return already_initialized.
            marker_fd = _validate_ready_root(root_fd, absolute, intent.root_instance_id)
            conn = _connect_raw(absolute)
            locked = _LockedConnection(conn, root_fd, parent_fd, root_name,
                                       lock_fd, marker_fd, absolute)
            locked._root_instance_id = intent.root_instance_id
            try:
                with _transaction_context(locked, 'BEGIN'):
                    _ensure_root_ready(locked, intent.root_instance_id)
                    _audit_in_transaction(locked, full=True)
            except BaseException:
                locked.close()
                root_fd = parent_fd = lock_fd = marker_fd = -1
                raise
            # Transfer descriptors to the wrapper; its close releases both locks.
            locked.close()
            root_fd = parent_fd = lock_fd = marker_fd = -1
            return InitializationResult(intent.root_instance_id, 'already_initialized')
        if other:
            raise ConversationStoreError()
        # This invocation created the stable lock in an otherwise empty store
        # namespace while holding the pinned directory admission lock.
        marker_fd = _create_marker_directory(root_fd)
        _publish_marker(root_fd, ROOT_MARKER_NAME, ROOT_MARKER_TEMP,
                        {'root_instance_id': intent.root_instance_id,
                         'state': 'pending', 'version': 1},
                        max_bytes=ROOT_MARKER_MAX_BYTES)
        _create_pending_database(root_fd, absolute, intent.root_instance_id)
        # The marker directory's directory entry is durable before root-ready.
        _fsync_dir(root_fd)
        _publish_marker(root_fd, ROOT_MARKER_NAME, ROOT_MARKER_TEMP,
                        {'root_instance_id': intent.root_instance_id,
                         'state': 'ready', 'version': 1},
                        max_bytes=ROOT_MARKER_MAX_BYTES)
        conn = _connect_raw(absolute)
        locked = _LockedConnection(conn, root_fd, parent_fd, root_name,
                                   lock_fd, marker_fd, absolute)
        locked._root_instance_id = intent.root_instance_id
        root_fd = parent_fd = lock_fd = marker_fd = -1
        def set_ready(connection):
            connection.execute("UPDATE root_state SET state='ready' WHERE singleton=1")
            if connection.execute('SELECT changes()').fetchone()[0] != 1:
                raise sqlite3.DatabaseError('root state missing')
        try:
            _commit_final(locked, 'BEGIN IMMEDIATE', set_ready,
                          lambda: _resolve_root_ready(locked, intent.root_instance_id))
            with _transaction_context(locked, 'BEGIN'):
                _ensure_root_ready(locked, intent.root_instance_id)
                _audit_in_transaction(locked, full=True)
        finally:
            locked.close()
        return InitializationResult(intent.root_instance_id, 'initialized')
    except BaseException:
        for fd in (marker_fd, lock_fd, root_fd, parent_fd):
            if isinstance(fd, int) and fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
        raise

def _validate_ready_root(root_fd: int, root_path: Path, requested_id: str) -> int:
    _validate_top_namespace(root_fd)
    if _stat_at(root_fd, ROOT_MARKER_TEMP) is not None:
        raise ConversationStoreError()
    root_id, marker_fd = _validate_root_artifacts(root_fd)
    if root_id != requested_id:
        os.close(marker_fd)
        raise ConversationStoreError()
    _validate_db_file(root_fd)
    conn = _connect_raw(root_path)
    try:
        row = conn.execute('SELECT root_instance_id,state FROM root_state WHERE singleton=1').fetchone()
        if row != (root_id, 'ready'):
            raise ConversationStoreError()
    except BaseException:
        conn.close()
        os.close(marker_fd)
        raise
    conn.close()
    return marker_fd

def bootstrap_dm(root: Path, bot_id: str, *, intent: DMBootstrapIntent) -> BootstrapResult:
    ref = ConversationRef.dm(bot_id)
    if not isinstance(intent, DMBootstrapIntent) or intent.bot_id != bot_id:
        raise ValueError('explicit matching DM bootstrap intent required')
    conn = open_existing_session(root, lock_mode='exclusive')
    try:
        root_id = _root_instance_id_from_conn(conn)
        with _transaction_context(conn, 'BEGIN'):
            _ensure_root_ready(conn, root_id)
        temp = f'.{bot_id}.json.tmp'
        if _stat_at(conn.marker_fd, temp) is not None:
            raise _local_error(ref)
        existing_marker = _stat_at(conn.marker_fd, _marker_filename(bot_id))
        existing_row = _read_door_row(conn, ref)
        if existing_marker is not None or existing_row is not None:
            marker = _read_dm_marker(conn.marker_fd, bot_id, root_id)
            if (marker['dm'] == 'ready' and marker['attempt_id'] == intent.attempt_id
                    and existing_row is not None and existing_row[1] == 'ready'):
                return BootstrapResult(_view(ref, _decode_metadata(existing_row[0]), ()),
                                       'already_initialized')
            raise _local_error(ref, 'DM already known')
        metadata = _metadata([bot_id])
        validate_metadata(ref, metadata)
        _publish_dm_marker(conn.marker_fd, bot_id, root_id, 'pending', intent.attempt_id)
        try:
            with _transaction_context(conn, 'BEGIN IMMEDIATE'):
                conn.execute(
                    "INSERT INTO conversations(kind,id,metadata,created_at,lifecycle_state,"
                    "generation,event_high_water,logical_high_water) "
                    "VALUES('dm',?,?,?,'pending',0,0,0)",
                    (bot_id, _encode_metadata(metadata), utc_now_ts()),
                )
        except sqlite3.Error as exc:
            raise _local_error(ref) from exc
        _publish_dm_marker(conn.marker_fd, bot_id, root_id, 'ready', intent.attempt_id)
        def set_ready(connection):
            connection.execute(
                "UPDATE conversations SET lifecycle_state='ready' WHERE kind='dm' AND id=? "
                "AND lifecycle_state='pending'", (bot_id,)
            )
            if connection.execute('SELECT changes()').fetchone()[0] != 1:
                raise sqlite3.DatabaseError('DM state missing')
        _commit_final(conn, 'BEGIN IMMEDIATE', set_ready,
                      lambda: _resolve_dm_ready(conn, ref, root_id, intent.attempt_id))
        return BootstrapResult(_view(ref, metadata, ()), 'initialized')
    finally:
        conn.close()

def audit_root(root: Path) -> None:
    """Full streaming integrity and marker/DB audit on an existing store."""
    conn = open_existing_session(root, lock_mode='shared')
    try:
        with _transaction_context(conn, 'BEGIN'):
            _audit_in_transaction(conn, full=True)
    finally:
        conn.close()

