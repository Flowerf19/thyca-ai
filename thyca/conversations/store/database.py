"""SQLite schema, connection setup, transactions, and locked-session factory."""
from __future__ import annotations

import fcntl
import os
import re
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from thyca.core.conversation import ConversationRef, bot_is_participant
from .contracts import ConversationStoreError, _decode_metadata, _local_error, validate_metadata
from .rootfs import (DB_FILE_NAME, LOCK_FILE_NAME, ROOT_MARKER_TEMP, _checked_regular_at,
                     _fsync_dir, _open_root, _open_store_lock, _read_dm_marker, _stat_at,
                     _validate_root_artifacts, _validate_top_namespace, _verify_root_name)

BUSY_TIMEOUT_MS = 5000

SCHEMA_VERSION = 2

_SCHEMA_STATEMENTS = (
    """CREATE TABLE root_state(
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        root_instance_id TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('pending','ready'))
    ) STRICT""",
    """CREATE TABLE conversations(
        kind TEXT NOT NULL CHECK(kind IN ('dm','group')),
        id TEXT NOT NULL,
        metadata TEXT NOT NULL,
        created_at TEXT NOT NULL,
        lifecycle_state TEXT NOT NULL CHECK(lifecycle_state IN ('pending','ready')),
        generation INTEGER NOT NULL CHECK(generation>=0),
        event_high_water INTEGER NOT NULL CHECK(event_high_water>=0),
        logical_high_water INTEGER NOT NULL CHECK(logical_high_water>=0),
        PRIMARY KEY(kind,id)
    ) STRICT""",
    """CREATE TABLE messages(
        kind TEXT NOT NULL,
        id TEXT NOT NULL,
        seq INTEGER NOT NULL CHECK(seq>0),
        logical_seq INTEGER NOT NULL CHECK(logical_seq>0),
        role TEXT NOT NULL CHECK(role IN ('user','assistant','system')),
        content TEXT NOT NULL,
        data TEXT NOT NULL CHECK(length(CAST(data AS BLOB))<=1544192),
        created_at TEXT NOT NULL,
        PRIMARY KEY(kind,id,seq),
        UNIQUE(kind,id,logical_seq),
        FOREIGN KEY(kind,id) REFERENCES conversations(kind,id)
    ) STRICT""",
    """CREATE TRIGGER no_update_messages
    BEFORE UPDATE ON messages
    BEGIN
        SELECT RAISE(ABORT, 'messages are append-only');
    END""",
    """CREATE TRIGGER no_delete_messages
    BEFORE DELETE ON messages
    BEGIN
        SELECT RAISE(ABORT, 'messages are append-only');
    END""",
)

def _normalized_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", sql.strip()).rstrip(";").lower()

_EXPECTED_SCHEMA = {
    ("table", "root_state"): _normalized_sql(_SCHEMA_STATEMENTS[0]),
    ("table", "conversations"): _normalized_sql(_SCHEMA_STATEMENTS[1]),
    ("table", "messages"): _normalized_sql(_SCHEMA_STATEMENTS[2]),
    ("trigger", "no_update_messages"): _normalized_sql(_SCHEMA_STATEMENTS[3]),
    ("trigger", "no_delete_messages"): _normalized_sql(_SCHEMA_STATEMENTS[4]),
}

def _connect_raw(root_path: Path) -> sqlite3.Connection:
    uri = root_path.joinpath(DB_FILE_NAME).as_uri() + '?mode=rw'
    deadline = time.monotonic() + BUSY_TIMEOUT_MS / 1000
    delay = 0.005
    while True:
        conn = None
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=BUSY_TIMEOUT_MS / 1000,
                                   isolation_level=None)
            conn.execute(f'PRAGMA busy_timeout={BUSY_TIMEOUT_MS}')
            conn.execute('PRAGMA foreign_keys=ON')
            if conn.execute('PRAGMA synchronous').fetchone()[0] != 2:
                raise ConversationStoreError()
            if conn.execute('PRAGMA journal_mode').fetchone()[0].lower() != 'wal':
                raise ConversationStoreError()
            version = conn.execute('PRAGMA user_version').fetchone()[0]
            if version != SCHEMA_VERSION:
                raise ConversationStoreError()
            _validate_schema(conn)
            return conn
        except ConversationStoreError:
            if conn is not None:
                conn.close()
            raise
        except sqlite3.OperationalError as exc:
            if conn is not None:
                conn.close()
            if (('locked' not in str(exc).lower() and 'locking protocol' not in str(exc).lower())
                    or time.monotonic() >= deadline):
                raise ConversationStoreError() from exc
            time.sleep(delay)
            delay = min(delay * 2, 0.2)
        except sqlite3.Error as exc:
            if conn is not None:
                conn.close()
            raise ConversationStoreError() from exc

def _validate_schema(conn: sqlite3.Connection) -> None:
    try:
        rows = conn.execute(
            "SELECT type,name,sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ).fetchall()
    except sqlite3.Error as exc:
        raise ConversationStoreError() from exc
    actual = {(kind, name): _normalized_sql(sql) if sql is not None else None
              for kind, name, sql in rows}
    if actual != _EXPECTED_SCHEMA:
        raise ConversationStoreError()

def _validate_db_file(root_fd: int) -> None:
    _checked_fd, _ = _checked_regular_at(root_fd, DB_FILE_NAME, mode=0o600)
    os.close(_checked_fd)
    _validate_top_namespace(root_fd)
    wal = _stat_at(root_fd, DB_FILE_NAME + '-wal')
    shm = _stat_at(root_fd, DB_FILE_NAME + '-shm')
    if wal is not None:
        fd, _ = _checked_regular_at(root_fd, DB_FILE_NAME + '-wal', mode=0o600)
        os.close(fd)
    if shm is not None:
        fd, _ = _checked_regular_at(root_fd, DB_FILE_NAME + '-shm', mode=0o600)
        os.close(fd)

class _LockedConnection:
    """SQLite connection retaining pinned root, marker, and store-lock FDs."""

    def __init__(self, conn: sqlite3.Connection, root_fd: int, parent_fd: int,
                 root_name: str, lock_fd: int, marker_fd: int, root_path: Path) -> None:
        self._conn = conn
        self.root_fd = root_fd
        self.parent_fd = parent_fd
        self.root_name = root_name
        self.lock_fd = lock_fd
        self.marker_fd = marker_fd
        self.root_path = root_path
        self._closed = False

    def __getattr__(self, name: str):
        return getattr(self._conn, name)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        error = None
        try:
            self._conn.close()
        except sqlite3.Error as exc:
            error = exc
        for fd in (self.marker_fd, self.lock_fd, self.root_fd, self.parent_fd):
            try:
                os.close(fd)
            except OSError:
                pass
        if error is not None:
            raise ConversationStoreError() from error

def open_existing_session(root: Path, *, lock_mode: Literal['shared', 'exclusive'] = 'shared') -> _LockedConnection:
    """Open an existing, validated v2 DB; never creates roots, DBs, or schema."""
    parent_fd, root_fd, root_name, absolute = _open_root(root, create=False)
    lock_fd = marker_fd = -1
    try:
        lock_fd, _ = _open_store_lock(root_fd)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_SH if lock_mode == 'shared' else fcntl.LOCK_EX)
        except OSError as exc:
            raise ConversationStoreError() from exc
        _verify_root_name(parent_fd, root_name, root_fd)
        _validate_top_namespace(root_fd)
        if _stat_at(root_fd, ROOT_MARKER_TEMP) is not None:
            raise ConversationStoreError()
        root_instance_id, marker_fd = _validate_root_artifacts(root_fd)
        _validate_db_file(root_fd)
        conn = _connect_raw(absolute)
        try:
            _validate_top_namespace(root_fd)
            _validate_db_instance(conn, root_instance_id)
        except BaseException:
            conn.close()
            raise
        locked = _LockedConnection(conn, root_fd, parent_fd, root_name,
                                   lock_fd, marker_fd, absolute)
        locked._root_instance_id = root_instance_id
        return locked
    except BaseException:
        if marker_fd >= 0:
            os.close(marker_fd)
        if lock_fd >= 0:
            os.close(lock_fd)
        os.close(root_fd)
        os.close(parent_fd)
        raise

def _validate_db_instance(conn: sqlite3.Connection, expected_root_id: str) -> None:
    try:
        rows = conn.execute('SELECT root_instance_id,state FROM root_state WHERE singleton=1').fetchall()
    except sqlite3.Error as exc:
        raise ConversationStoreError() from exc
    if len(rows) != 1 or rows[0][0] != expected_root_id or rows[0][1] not in ('pending', 'ready'):
        raise ConversationStoreError()

@contextmanager
def _transaction(conn: sqlite3.Connection, begin: str) -> Iterator[None]:
    """Run a transaction; sanitize every control-path error and never retry."""
    try:
        conn.execute(begin)
    except sqlite3.Error as exc:
        raise ConversationStoreError() from exc
    try:
        yield
    except BaseException as exc:
        try:
            conn.execute('ROLLBACK')
        except sqlite3.Error as rollback_exc:
            raise ConversationStoreError() from rollback_exc
        if isinstance(exc, sqlite3.Error):
            raise ConversationStoreError() from exc
        raise
    else:
        try:
            conn.execute('COMMIT')
        except sqlite3.Error as exc:
            try:
                conn.execute('ROLLBACK')
            except sqlite3.Error:
                # The commit may already be durable; callers must not retry.
                pass
            raise ConversationStoreError() from exc

@contextmanager
def _transaction_context(conn: sqlite3.Connection, begin: str) -> Iterator[None]:
    with _transaction(conn, begin):
        yield

def _ensure_root_ready(conn: sqlite3.Connection, root_instance_id: str) -> None:
    try:
        row = conn.execute('SELECT root_instance_id,state FROM root_state WHERE singleton=1').fetchone()
    except sqlite3.Error as exc:
        raise ConversationStoreError() from exc
    if row != (root_instance_id, 'ready'):
        raise ConversationStoreError()

def _read_door_row(conn: sqlite3.Connection, ref: ConversationRef):
    try:
        return conn.execute(
            "SELECT metadata,lifecycle_state,generation,event_high_water,logical_high_water "
            "FROM conversations WHERE kind=? AND id=?", (ref.kind, ref.id)
        ).fetchone()
    except sqlite3.Error as exc:
        raise _local_error(ref) from exc

def _validate_door_row(ref: ConversationRef, row):
    if row is None:
        raise _local_error(ref)
    metadata = _decode_metadata(row[0])
    try:
        validate_metadata(ref, metadata)
    except ValueError as exc:
        raise _local_error(ref, 'conversation corrupt') from exc
    lifecycle_state, generation, event_high_water, logical_high_water = row[1:]
    values = (generation, event_high_water, logical_high_water)
    if lifecycle_state != 'ready' or any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in values):
        raise _local_error(ref)
    return metadata, generation, event_high_water, logical_high_water

def _ensure_door_ready(conn: _LockedConnection, ref: ConversationRef,
                       actor: str | None = None):
    root_instance_id = _root_instance_id_from_conn(conn)
    _ensure_root_ready(conn, root_instance_id)
    row = _read_door_row(conn, ref)
    metadata, generation, event_high_water, logical_high_water = _validate_door_row(ref, row)
    if actor is not None:
        try:
            allowed = bot_is_participant(ref, metadata['participants'], actor)
        except ValueError as exc:
            raise _local_error(ref, 'conversation corrupt') from exc
        if not allowed:
            raise ConversationStoreError()
    if ref.kind == 'dm':
        marker = _read_dm_marker(conn.marker_fd, ref.id, root_instance_id)
        if marker['dm'] != 'ready':
            raise _local_error(ref)
    try:
        max_event, max_logical = conn.execute(
            'SELECT MAX(seq),MAX(logical_seq) FROM messages WHERE kind=? AND id=?',
            (ref.kind, ref.id),
        ).fetchone()
    except sqlite3.Error as exc:
        raise _local_error(ref) from exc
    if ((max_event is not None and max_event > event_high_water)
            or (max_logical is not None and max_logical > logical_high_water)):
        raise _local_error(ref, 'conversation corrupt')
    return metadata, generation, event_high_water, logical_high_water

def _root_instance_id_from_conn(conn: _LockedConnection) -> str:
    # The marker was parsed before SQLite opened and is stable under store lock.
    return conn._root_instance_id

def _metadata_for_result(metadata: dict) -> dict:
    return {**metadata, 'participants': list(metadata['participants'])}

def _raw_connection_for_resolution(conn: _LockedConnection) -> sqlite3.Connection:
    _verify_root_name(conn.parent_fd, conn.root_name, conn.root_fd)
    _validate_db_file(conn.root_fd)
    return _connect_raw(conn.root_path)

def _commit_final(conn: _LockedConnection, begin: str, mutation: Callable[[sqlite3.Connection], None],
                  resolver: Callable[[], bool]) -> None:
    """One final commit; on an exception only read state to resolve uncertainty."""
    try:
        conn.execute(begin)
        mutation(conn)
    except BaseException as exc:
        try:
            conn.execute('ROLLBACK')
        except sqlite3.Error:
            pass
        if isinstance(exc, sqlite3.Error):
            raise ConversationStoreError() from exc
        raise
    try:
        conn.execute('COMMIT')
    except sqlite3.Error as exc:
        try:
            conn.execute('ROLLBACK')
        except sqlite3.Error:
            pass
        try:
            if resolver():
                return
        except Exception:
            pass
        raise ConversationStoreError() from exc

def _create_pending_database(root_fd: int, root_path: Path, root_id: str) -> None:
    try:
        fd = os.open(DB_FILE_NAME,
                     os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=root_fd)
        os.fchmod(fd, 0o600)
        os.fsync(fd)
    except OSError as exc:
        raise ConversationStoreError() from exc
    else:
        os.close(fd)
    _fsync_dir(root_fd)
    conn = _connect_raw_for_init(root_path)
    try:
        try:
            mode = conn.execute('PRAGMA journal_mode=WAL').fetchone()[0]
            if str(mode).lower() != 'wal':
                raise ConversationStoreError()
            conn.execute('PRAGMA synchronous=FULL')
            with _transaction_context(conn, 'BEGIN IMMEDIATE'):
                for statement in _SCHEMA_STATEMENTS:
                    conn.execute(statement)
                conn.execute('PRAGMA user_version=2')
                conn.execute(
                    "INSERT INTO root_state(singleton,root_instance_id,state) VALUES(1,?,'pending')",
                    (root_id,),
                )
        except sqlite3.Error as exc:
            raise ConversationStoreError() from exc
        _validate_schema(conn)
    finally:
        conn.close()
    _fsync_dir(root_fd)

def _connect_raw_for_init(root_path: Path) -> sqlite3.Connection:
    uri = root_path.joinpath(DB_FILE_NAME).as_uri() + '?mode=rw'
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=BUSY_TIMEOUT_MS / 1000,
                               isolation_level=None)
        conn.execute(f'PRAGMA busy_timeout={BUSY_TIMEOUT_MS}')
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('PRAGMA synchronous=FULL')
        if conn.execute('PRAGMA synchronous').fetchone()[0] != 2:
            raise ConversationStoreError()
        return conn
    except ConversationStoreError:
        try:
            conn.close()
        except (UnboundLocalError, sqlite3.Error):
            pass
        raise
    except sqlite3.Error as exc:
        try:
            conn.close()
        except (UnboundLocalError, sqlite3.Error):
            pass
        raise ConversationStoreError() from exc

