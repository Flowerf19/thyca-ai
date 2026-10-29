"""SQLite-backed target public transcripts (not wired to the runtime).

One database file per bot root (``<root>/conversations.db``). The agent API is
append-only for messages: agents may INSERT conversation rows (create) and
message rows (append), and read them back. There is no agent-facing UPDATE or
DELETE for messages; metadata changes go through the separate system/admin
``set_metadata`` path. No data migration from the old journal files is
provided: the old data was confirmed to have no value.

Concurrency: writers take a short ``BEGIN IMMEDIATE`` transaction only around
the ``MAX(seq)+1`` + INSERT pair. No LLM calls or other outside computation
happens while a transaction is open. ``busy_timeout`` absorbs brief writer
contention instead of surfacing ``database is locked``.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from thyca.core.conversation import ConversationRef
from thyca.core.protocol import Message, utc_now_ts

DB_FILE_NAME = "conversations.db"
BUSY_TIMEOUT_MS = 5000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations(
    kind TEXT NOT NULL CHECK(kind IN ('dm','group')),
    id TEXT NOT NULL,
    metadata TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(kind, id)
);
CREATE TABLE IF NOT EXISTS messages(
    kind TEXT NOT NULL,
    id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(kind, id, seq),
    FOREIGN KEY(kind, id) REFERENCES conversations(kind, id)
);
"""


class ConversationStoreError(RuntimeError):
    """Conversation unavailable or an explicit create conflicted."""


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
    if data['title'] is not None and not isinstance(data['title'], str):
        raise ValueError('invalid title')
    if data['title_source'] not in (None, 'user', 'agent') or not isinstance(data['naming_attempted'], bool):
        raise ValueError('invalid title metadata')
    participants = data['participants']
    if not isinstance(participants, list) or not participants:
        raise ValueError('invalid participants')
    for bot in participants:
        ConversationRef.dm(bot)
    if len(set(participants)) != len(participants) or (ref.kind == 'dm' and participants != [ref.id]):
        raise ValueError('invalid participants')


def validate_message(message: Message) -> None:
    if (message.role not in ('user', 'assistant') or message.tool_calls is not None
            or message.tool_call_id is not None or message.reasoning or message.reasoning_details):
        raise ValueError('not a public transcript message')


def _metadata(participants, title=None):
    return {'title': title, 'title_source': 'user' if title is not None else None,
            'naming_attempted': False, 'participants': participants}


def _view(ref, metadata, messages):
    return Conversation(ref, metadata['title'], metadata['title_source'],
                        metadata['naming_attempted'], tuple(metadata['participants']), tuple(messages))


def _encode_metadata(metadata: dict) -> str:
    return json.dumps(metadata, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':'))


def _require_ref(ref):
    if not isinstance(ref, ConversationRef):
        raise ValueError('expected conversation reference')


def _connect(root: Path) -> sqlite3.Connection:
    """Open the per-root database with WAL, contention timeout and FK checks."""
    conn = sqlite3.connect(str(Path(root) / DB_FILE_NAME), timeout=BUSY_TIMEOUT_MS / 1000)
    try:
        conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(_SCHEMA)
        return conn
    except BaseException:
        conn.close()
        raise


class ConversationStore:
    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def read(self, ref: ConversationRef) -> Conversation:
        _require_ref(ref)
        conn = _connect(self._root)
        try:
            row = conn.execute(
                "SELECT metadata FROM conversations WHERE kind=? AND id=?",
                (ref.kind, ref.id),
            ).fetchone()
            if row is None:
                raise ConversationStoreError('conversation unavailable')
            metadata = json.loads(row[0])
            messages = tuple(
                Message.from_dict(json.loads(data))
                for (data,) in conn.execute(
                    "SELECT data FROM messages WHERE kind=? AND id=? ORDER BY seq",
                    (ref.kind, ref.id),
                )
            )
            return _view(ref, metadata, messages)
        finally:
            conn.close()

    def bootstrap_initial_dm(self, bot_id: str) -> Conversation:
        """Create once; caller must establish genuine bootstrap authority."""
        ref = ConversationRef.dm(bot_id)
        metadata = _metadata([bot_id])
        validate_metadata(ref, metadata)
        conn = _connect(self._root)
        try:
            try:
                conn.execute(
                    "INSERT INTO conversations(kind, id, metadata, created_at) VALUES(?,?,?,?)",
                    (ref.kind, ref.id, _encode_metadata(metadata), utc_now_ts()),
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                raise ConversationStoreError('DM already known') from exc
            return _view(ref, metadata, ())
        finally:
            conn.close()

    def create_group(self, participants: list[str], *, title: str | None = None) -> Conversation:
        metadata = _metadata(participants, title)
        conn = _connect(self._root)
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
            raise ConversationStoreError('group identifier collision')
        finally:
            conn.close()

    def set_metadata(self, ref: ConversationRef, *, title: str | None,
                     title_source: str | None, naming_attempted: bool,
                     participants: list[str]) -> Conversation:
        """System/admin-only metadata rewrite; messages stay append-only."""
        metadata = {'title': title, 'title_source': title_source,
                    'naming_attempted': naming_attempted, 'participants': participants}
        _require_ref(ref)
        validate_metadata(ref, metadata)
        conn = _connect(self._root)
        try:
            cur = conn.execute(
                "UPDATE conversations SET metadata=? WHERE kind=? AND id=?",
                (_encode_metadata(metadata), ref.kind, ref.id),
            )
            if cur.rowcount == 0:
                raise ConversationStoreError('conversation unavailable')
            conn.commit()
            messages = tuple(
                Message.from_dict(json.loads(data))
                for (data,) in conn.execute(
                    "SELECT data FROM messages WHERE kind=? AND id=? ORDER BY seq",
                    (ref.kind, ref.id),
                )
            )
            return _view(ref, metadata, messages)
        finally:
            conn.close()

    def append(self, ref: ConversationRef, message: Message) -> None:
        _require_ref(ref)
        if not isinstance(message, Message):
            raise ValueError('expected message')
        validate_message(message)
        # Encode before opening the transaction so no outside work happens inside it.
        payload = json.dumps(message.to_canonical_dict(), ensure_ascii=False,
                             allow_nan=False, sort_keys=True, separators=(',', ':'))
        conn = _connect(self._root)
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                exists = conn.execute(
                    "SELECT 1 FROM conversations WHERE kind=? AND id=?",
                    (ref.kind, ref.id),
                ).fetchone()
                if exists is None:
                    raise ConversationStoreError('conversation unavailable')
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
                conn.commit()
            except BaseException:
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                raise
        finally:
            conn.close()
