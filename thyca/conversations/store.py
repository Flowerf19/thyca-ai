"""SQLite-backed conversation transcripts with agent/admin separation.

One database file per bot root (``<root>/conversations.db``). Two facades share
one schema and one file:

- :class:`AgentStore` — what agent code may hold: ``read`` and ``append`` only,
  both bound to an explicit ``actor`` bot. Every call re-checks current
  membership inside the transaction; non-members (including removed former
  participants) get the same generic ``unavailable`` error as a missing door,
  so existence is not leaked.
- :class:`AdminStore` — system/admin operations only: conversation creation,
  metadata rewrites, retention purge, compaction and ``VACUUM``. Agent code
  must never receive this object.

Messages are append-only by construction: SQLite triggers abort any UPDATE or
DELETE on ``messages``. Admin purge/compaction temporarily drop those triggers
inside their own short transaction and recreate them before commit, so a crash
or rollback always leaves the guards in place.

Transactions never span outside computation: payload encoding, membership input
validation and the compaction summarizer all run before the transaction opens.
``seq`` is allocated as ``MAX(seq)+1`` under ``BEGIN IMMEDIATE``; ``busy_timeout``
absorbs brief writer contention instead of surfacing ``database is locked``.
"""
from __future__ import annotations

import json
import os
import sqlite3
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable

from thyca.core.conversation import ConversationRef, bot_is_participant
from thyca.core.protocol import Message, utc_now_ts

DB_FILE_NAME = "conversations.db"
BUSY_TIMEOUT_MS = 5000
SCHEMA_VERSION = 1

MAX_CONTENT_LEN = 256_000
MAX_METADATA_LEN = 64_000
MAX_JSON_DEPTH = 32
DEFAULT_PAGE_LIMIT = 500
MAX_PAGE_LIMIT = 5_000

_ALLOWED_READ_ROLES = ('user', 'assistant', 'system')

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations(
    kind TEXT NOT NULL CHECK(kind IN ('dm','group')),
    id TEXT NOT NULL,
    metadata TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(kind, id)
) STRICT;
CREATE TABLE IF NOT EXISTS messages(
    kind TEXT NOT NULL,
    id TEXT NOT NULL,
    seq INTEGER NOT NULL CHECK(seq > 0),
    role TEXT NOT NULL CHECK(role IN ('user','assistant','system')),
    content TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(kind, id, seq),
    FOREIGN KEY(kind, id) REFERENCES conversations(kind, id)
) STRICT;
CREATE TRIGGER IF NOT EXISTS no_update_messages
BEFORE UPDATE ON messages
BEGIN
    SELECT RAISE(ABORT, 'messages are append-only');
END;
CREATE TRIGGER IF NOT EXISTS no_delete_messages
BEFORE DELETE ON messages
BEGIN
    SELECT RAISE(ABORT, 'messages are append-only');
END;
"""


class ConversationStoreError(RuntimeError):
    """Conversation unavailable, corrupt, or an explicit create conflicted."""


@dataclass(frozen=True)
class Conversation:
    ref: ConversationRef
    title: str | None
    title_source: str | None
    naming_attempted: bool
    participants: tuple[str, ...]
    messages: tuple[Message, ...]


def validate_metadata(ref: ConversationRef, data: dict) -> None:
    if not isinstance(data, dict) or set(data) != {'title', 'title_source', 'naming_attempted', 'participants'}:
        raise ValueError('invalid metadata')
    if data['title'] is not None:
        _check_text(data['title'], 'title')
    if data['title_source'] not in (None, 'user', 'agent') or not isinstance(data['naming_attempted'], bool):
        raise ValueError('invalid title metadata')
    participants = data['participants']
    if not isinstance(participants, list) or not participants:
        raise ValueError('invalid participants')
    for bot in participants:
        ConversationRef.dm(bot)
    if len(set(participants)) != len(participants) or (ref.kind == 'dm' and participants != [ref.id]):
        raise ValueError('invalid participants')
    if len(json.dumps(data, ensure_ascii=False, separators=(',', ':'))) > MAX_METADATA_LEN:
        raise ValueError('metadata too large')


def validate_message(message: Message) -> None:
    if (message.role not in ('user', 'assistant') or message.tool_calls is not None
            or message.tool_call_id is not None or message.reasoning or message.reasoning_details):
        raise ValueError('not a public transcript message')
    _check_text(message.content, 'content')


def _check_text(value: object, name: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f'{name} must be str')
    if len(value) > MAX_CONTENT_LEN:
        raise ValueError(f'{name} too large')
    if '\x00' in value:
        raise ValueError(f'{name} must not contain NUL')
    if any('\ud800' <= ch <= '\udfff' for ch in value):
        raise ValueError(f'{name} must not contain surrogates')


def _check_depth(value: object, limit: int = MAX_JSON_DEPTH) -> None:
    """Reject hostile nesting before trusting decoded JSON (iterative, no recursion)."""
    stack = [(value, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > limit:
            raise ValueError('JSON too deeply nested')
        if isinstance(node, dict):
            stack.extend((child, depth + 1) for child in node.values())
        elif isinstance(node, list):
            stack.extend((child, depth + 1) for child in node)


def _metadata(participants, title=None):
    return {'title': title, 'title_source': 'user' if title is not None else None,
            'naming_attempted': False, 'participants': participants}


def _view(ref, metadata, messages):
    return Conversation(ref, metadata['title'], metadata['title_source'],
                        metadata['naming_attempted'], tuple(metadata['participants']), tuple(messages))


def _encode_metadata(metadata: dict) -> str:
    return json.dumps(metadata, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':'))


def _require_ref(ref) -> None:
    if not isinstance(ref, ConversationRef):
        raise ValueError('expected conversation reference')


def _require_actor(actor: object) -> str:
    if not isinstance(actor, str):
        raise ValueError('actor must be a bot id string')
    ConversationRef.dm(actor)  # validates bot-id shape, raises ValueError
    return actor


def _decode_metadata(raw: str) -> dict:
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise ConversationStoreError('conversation corrupt') from exc
    try:
        _check_depth(data)
    except ValueError as exc:
        raise ConversationStoreError('conversation corrupt') from exc
    if not isinstance(data, dict):
        raise ConversationStoreError('conversation corrupt')
    return data


def _decode_message(raw: str) -> Message:
    try:
        data = json.loads(raw)
        _check_depth(data)
        message = Message.from_dict(data)
    except (ValueError, RecursionError) as exc:
        raise ConversationStoreError('conversation corrupt') from exc
    if message.role not in _ALLOWED_READ_ROLES:
        raise ConversationStoreError('conversation corrupt')
    return message


def _ensure_root(root: Path) -> Path:
    """Fail closed unless root is an owned real directory; enforce 0700."""
    path = Path(root)
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        try:
            os.mkdir(path, 0o700)
        except OSError as exc:
            raise ConversationStoreError('conversation unavailable') from exc
        return path
    except OSError as exc:
        raise ConversationStoreError('conversation unavailable') from exc
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ConversationStoreError('conversation unavailable')
    if info.st_uid != os.geteuid():
        raise ConversationStoreError('conversation unavailable')
    try:
        os.chmod(path, 0o700)
    except OSError as exc:
        raise ConversationStoreError('conversation unavailable') from exc
    return path


def _db_path(root: Path) -> Path:
    return Path(root) / DB_FILE_NAME


def _check_db_file(path: Path, *, must_exist: bool) -> bool:
    """Refuse symlinks/wrong owners; enforce 0600. Returns True if the file exists."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        if must_exist:
            raise ConversationStoreError('conversation unavailable')
        return False
    except OSError as exc:
        raise ConversationStoreError('conversation unavailable') from exc
    if not stat.S_ISREG(info.st_mode):
        raise ConversationStoreError('conversation unavailable')
    if info.st_uid != os.geteuid():
        raise ConversationStoreError('conversation unavailable')
    try:
        probe = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise ConversationStoreError('conversation unavailable') from exc
    try:
        if not stat.S_ISREG(os.fstat(probe).st_mode):
            raise ConversationStoreError('conversation unavailable')
    finally:
        os.close(probe)
    try:
        os.chmod(path, 0o600)
    except OSError as exc:
        raise ConversationStoreError('conversation unavailable') from exc
    return True


def _open_db(root: Path) -> sqlite3.Connection:
    """Connect with retries/backoff; initialize WAL + schema exactly once."""
    path = _db_path(_ensure_root(root))
    exists = _check_db_file(path, must_exist=False)
    last: BaseException | None = None
    deadline = time.monotonic() + BUSY_TIMEOUT_MS / 1000
    delay = 0.005
    while True:
        try:
            if not exists:
                old_umask = os.umask(0o077)
                try:
                    conn = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_MS / 1000)
                finally:
                    os.umask(old_umask)
                try:
                    os.chmod(path, 0o600)
                except OSError:
                    conn.close()
                    raise
            else:
                conn = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_MS / 1000)
            try:
                conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute("PRAGMA synchronous=NORMAL")
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                if version == 0:
                    conn.executescript(_SCHEMA)
                    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                    conn.commit()
                elif version != SCHEMA_VERSION:
                    raise ConversationStoreError('conversation unavailable')
                return conn
            except BaseException:
                conn.close()
                raise
        except sqlite3.OperationalError as exc:
            last = exc
            if 'locked' not in str(exc).lower() or time.monotonic() >= deadline:
                raise ConversationStoreError('conversation unavailable') from exc
            time.sleep(delay)
            delay = min(delay * 2, 0.2)
        except sqlite3.Error as exc:
            raise ConversationStoreError('conversation unavailable') from exc


def _membership_or_unavailable(conn: sqlite3.Connection, ref: ConversationRef, actor: str) -> dict:
    """Load metadata and prove actor membership; missing/non-member look identical."""
    try:
        row = conn.execute(
            "SELECT metadata FROM conversations WHERE kind=? AND id=?",
            (ref.kind, ref.id),
        ).fetchone()
    except sqlite3.Error as exc:
        raise ConversationStoreError('conversation unavailable') from exc
    if row is None:
        raise ConversationStoreError('conversation unavailable')
    metadata = _decode_metadata(row[0])
    try:
        validate_metadata(ref, metadata)
        allowed = bot_is_participant(ref, metadata['participants'], actor)
    except ValueError:
        raise ConversationStoreError('conversation corrupt')
    if not allowed:
        raise ConversationStoreError('conversation unavailable')
    return metadata


class AgentStore:
    """Agent-facing facade: membership-checked ``read`` and ``append`` only.

    The store never exposes conversation creation, metadata rewrites,
    retention, compaction or SQL handles, so agent code cannot reach admin
    operations through this object.
    """

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def read(self, ref: ConversationRef, actor: str, *,
             after_seq: int = 0, limit: int = DEFAULT_PAGE_LIMIT) -> Conversation:
        _require_ref(ref)
        _require_actor(actor)
        if not isinstance(after_seq, int) or after_seq < 0:
            raise ValueError('after_seq must be a non-negative int')
        if not isinstance(limit, int) or limit < 1:
            raise ValueError('limit must be a positive int')
        limit = min(limit, MAX_PAGE_LIMIT)
        conn = _open_db(self._root)
        try:
            conn.execute("BEGIN")
            try:
                metadata = _membership_or_unavailable(conn, ref, actor)
                try:
                    rows = conn.execute(
                        "SELECT data FROM messages WHERE kind=? AND id=? AND seq>? "
                        "ORDER BY seq LIMIT ?",
                        (ref.kind, ref.id, after_seq, limit),
                    ).fetchall()
                except sqlite3.Error as exc:
                    raise ConversationStoreError('conversation unavailable') from exc
                messages = tuple(_decode_message(data) for (data,) in rows)
                conn.execute("COMMIT")
                return _view(ref, metadata, messages)
            except BaseException:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
        finally:
            conn.close()

    def append(self, ref: ConversationRef, actor: str, message: Message) -> int:
        """Append one message; returns its allocated ``seq``."""
        _require_ref(ref)
        _require_actor(actor)
        if not isinstance(message, Message):
            raise ValueError('expected message')
        validate_message(message)
        # Encode before the transaction: no outside work happens inside it.
        payload = json.dumps(message.to_canonical_dict(), ensure_ascii=False,
                             allow_nan=False, sort_keys=True, separators=(',', ':'))
        conn = _open_db(self._root)
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                _membership_or_unavailable(conn, ref, actor)
                try:
                    top = conn.execute(
                        "SELECT MAX(seq) FROM messages WHERE kind=? AND id=?",
                        (ref.kind, ref.id),
                    ).fetchone()[0]
                    seq = 1 if top is None else top + 1
                    conn.execute(
                        "INSERT INTO messages(kind, id, seq, role, content, data, created_at)"
                        " VALUES(?,?,?,?,?,?,?)",
                        (ref.kind, ref.id, seq, message.role, message.content,
                         payload, utc_now_ts()),
                    )
                except sqlite3.Error as exc:
                    raise ConversationStoreError('conversation unavailable') from exc
                conn.execute("COMMIT")
                return seq
            except BaseException:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
        finally:
            conn.close()


class AdminStore:
    """System/admin facade: creation, metadata, retention, compaction, VACUUM.

    Never hand this object to agent code.
    """

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def agent(self) -> AgentStore:
        """Derive the agent-facing facade over the same root."""
        return AgentStore(self._root)

    def bootstrap_initial_dm(self, bot_id: str) -> Conversation:
        """Create once; caller must establish genuine bootstrap authority."""
        ref = ConversationRef.dm(bot_id)
        metadata = _metadata([bot_id])
        validate_metadata(ref, metadata)
        conn = _open_db(self._root)
        try:
            try:
                conn.execute(
                    "INSERT INTO conversations(kind, id, metadata, created_at) VALUES(?,?,?,?)",
                    (ref.kind, ref.id, _encode_metadata(metadata), utc_now_ts()),
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                raise ConversationStoreError('DM already known') from exc
            except sqlite3.Error as exc:
                raise ConversationStoreError('conversation unavailable') from exc
            return _view(ref, metadata, ())
        finally:
            conn.close()

    def create_group(self, participants: list[str], *, title: str | None = None) -> Conversation:
        metadata = _metadata(participants, title)
        conn = _open_db(self._root)
        try:
            for _ in range(10):
                ref = ConversationRef.new_group()
                validate_metadata(ref, metadata)
                try:
                    conn.execute(
                        "INSERT INTO conversations(kind, id, metadata, created_at) VALUES(?,?,?,?)",
                        (ref.kind, ref.id, _encode_metadata(metadata), utc_now_ts()),
                    )
                    conn.commit()
                    return _view(ref, metadata, ())
                except sqlite3.IntegrityError:
                    continue
                except sqlite3.Error as exc:
                    raise ConversationStoreError('conversation unavailable') from exc
            raise ConversationStoreError('group identifier collision')
        finally:
            conn.close()

    def set_metadata(self, ref: ConversationRef, *, title: str | None,
                     title_source: str | None, naming_attempted: bool,
                     participants: list[str]) -> Conversation:
        """Metadata rewrite with same-transaction read-your-write."""
        metadata = {'title': title, 'title_source': title_source,
                    'naming_attempted': naming_attempted, 'participants': participants}
        _require_ref(ref)
        validate_metadata(ref, metadata)
        conn = _open_db(self._root)
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                try:
                    cur = conn.execute(
                        "UPDATE conversations SET metadata=? WHERE kind=? AND id=?",
                        (_encode_metadata(metadata), ref.kind, ref.id),
                    )
                except sqlite3.Error as exc:
                    raise ConversationStoreError('conversation unavailable') from exc
                if cur.rowcount == 0:
                    raise ConversationStoreError('conversation unavailable')
                try:
                    rows = conn.execute(
                        "SELECT data FROM messages WHERE kind=? AND id=? ORDER BY seq",
                        (ref.kind, ref.id),
                    ).fetchall()
                except sqlite3.Error as exc:
                    raise ConversationStoreError('conversation unavailable') from exc
                messages = tuple(_decode_message(data) for (data,) in rows)
                conn.execute("COMMIT")
                return _view(ref, metadata, messages)
            except BaseException:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
        finally:
            conn.close()

    def purge_before(self, cutoff: str) -> int:
        """Delete messages older than ``cutoff`` (ISO ts) across all doors.

        Retention runs inside one short transaction; triggers are dropped and
        recreated within it so a rollback always leaves the guards in place.
        """
        if not isinstance(cutoff, str) or not cutoff:
            raise ValueError('cutoff must be a non-empty timestamp string')
        conn = _open_db(self._root)
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                try:
                    conn.execute("DROP TRIGGER IF EXISTS no_delete_messages")
                    cur = conn.execute("DELETE FROM messages WHERE created_at < ?", (cutoff,))
                    removed = cur.rowcount
                    conn.execute(
                        "CREATE TRIGGER IF NOT EXISTS no_delete_messages "
                        "BEFORE DELETE ON messages BEGIN "
                        "SELECT RAISE(ABORT, 'messages are append-only'); END")
                except sqlite3.Error as exc:
                    raise ConversationStoreError('conversation unavailable') from exc
                conn.execute("COMMIT")
                return removed
            except BaseException:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
        finally:
            conn.close()

    def compact_through_seq(
        self,
        ref: ConversationRef,
        through_seq: int,
        summarize: Callable[[tuple[Message, ...]], str],
    ) -> Message:
        """Replace messages ``seq <= through_seq`` with one summary row.

        ``summarize`` runs OUTSIDE any transaction (it may be slow); only the
        delete + summary insert hold a short ``BEGIN IMMEDIATE``. The summary
        is appended at the current end with fresh ``seq`` so concurrent
        appends can never collide with it.
        """
        _require_ref(ref)
        if not isinstance(through_seq, int) or through_seq < 1:
            raise ValueError('through_seq must be a positive int')
        if not callable(summarize):
            raise ValueError('summarize must be callable')
        conn = _open_db(self._root)
        try:
            try:
                row = conn.execute(
                    "SELECT metadata FROM conversations WHERE kind=? AND id=?",
                    (ref.kind, ref.id),
                ).fetchone()
            except sqlite3.Error as exc:
                raise ConversationStoreError('conversation unavailable') from exc
            if row is None:
                raise ConversationStoreError('conversation unavailable')
            metadata = _decode_metadata(row[0])
            try:
                validate_metadata(ref, metadata)
            except ValueError:
                raise ConversationStoreError('conversation corrupt')
        finally:
            conn.close()
        # --- outside any transaction from here until the final swap ---
        conn = _open_db(self._root)
        try:
            try:
                rows = conn.execute(
                    "SELECT data FROM messages WHERE kind=? AND id=? AND seq<=? ORDER BY seq",
                    (ref.kind, ref.id, through_seq),
                ).fetchall()
            except sqlite3.Error as exc:
                raise ConversationStoreError('conversation unavailable') from exc
            old = tuple(_decode_message(data) for (data,) in rows)
        finally:
            conn.close()
        if not old:
            raise ConversationStoreError('nothing to compact')
        summary_text = summarize(old)
        _check_text(summary_text, 'summary')
        summary = Message(role='system', content=summary_text,
                          meta={'compacted': True, 'through_seq': through_seq})
        payload = json.dumps(summary.to_canonical_dict(), ensure_ascii=False,
                             allow_nan=False, sort_keys=True, separators=(',', ':'))
        conn = _open_db(self._root)
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                try:
                    conn.execute("DROP TRIGGER IF EXISTS no_delete_messages")
                    conn.execute(
                        "DELETE FROM messages WHERE kind=? AND id=? AND seq<=?",
                        (ref.kind, ref.id, through_seq),
                    )
                    top = conn.execute(
                        "SELECT MAX(seq) FROM messages WHERE kind=? AND id=?",
                        (ref.kind, ref.id),
                    ).fetchone()[0]
                    seq = 1 if top is None else top + 1
                    conn.execute(
                        "INSERT INTO messages(kind, id, seq, role, content, data, created_at)"
                        " VALUES(?,?,?,?,?,?,?)",
                        (ref.kind, ref.id, seq, summary.role, summary.content,
                         payload, utc_now_ts()),
                    )
                    conn.execute(
                        "CREATE TRIGGER IF NOT EXISTS no_delete_messages "
                        "BEFORE DELETE ON messages BEGIN "
                        "SELECT RAISE(ABORT, 'messages are append-only'); END")
                except sqlite3.Error as exc:
                    raise ConversationStoreError('conversation unavailable') from exc
                conn.execute("COMMIT")
                return summary
            except BaseException:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
        finally:
            conn.close()

    def vacuum(self) -> None:
        """Reclaim space after retention/compaction. Runs outside transactions."""
        conn = _open_db(self._root)
        try:
            try:
                conn.execute("VACUUM")
            except sqlite3.Error as exc:
                raise ConversationStoreError('conversation unavailable') from exc
        finally:
            conn.close()
