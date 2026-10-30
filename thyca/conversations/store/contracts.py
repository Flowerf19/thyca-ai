"""Public immutable conversation-store contracts and pure validation."""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Literal

from thyca.core.conversation import ConversationRef
from thyca.core.protocol import META_CAP_BYTES, Message

MAX_CONTENT_LEN = 256_000

MAX_METADATA_LEN = 64_000

MAX_JSON_DEPTH = 32

DEFAULT_PAGE_LIMIT = 500

MAX_PAGE_LIMIT = 5_000

MAX_STORED_MESSAGE_BYTES = MAX_CONTENT_LEN * 6 + META_CAP_BYTES + 4096

_ALLOWED_READ_ROLES = ('user', 'assistant', 'system')

_BOT_ID_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")

_ATTEMPT_RE = re.compile(r"[0-9a-f]{32}\Z")

class ConversationStoreError(RuntimeError):
    """Sanitized store failure, with an explicit global or local fault scope."""

    def __init__(self, message: str = 'conversation unavailable', *,
                 scope: Literal['global', 'local'] = 'global',
                 ref: ConversationRef | None = None) -> None:
        super().__init__(message)
        self.scope = scope
        self.ref = ref

@dataclass(frozen=True)
class RootInitializationIntent:
    """Caller assertion for a fresh-root init; this value is not authentication."""

    root_instance_id: str

    def __post_init__(self) -> None:
        _validate_uuid(self.root_instance_id)

@dataclass(frozen=True)
class DMBootstrapIntent:
    """Caller assertion for one DM bootstrap; this value is not authentication."""

    bot_id: str
    attempt_id: str

    def __post_init__(self) -> None:
        ConversationRef.dm(self.bot_id)
        if not isinstance(self.attempt_id, str) or not _ATTEMPT_RE.fullmatch(self.attempt_id):
            raise ValueError('invalid bootstrap attempt id')

@dataclass(frozen=True)
class InitializationResult:
    root_instance_id: str
    status: Literal['initialized', 'already_initialized']

@dataclass(frozen=True)
class BootstrapResult:
    conversation: Conversation
    status: Literal['initialized', 'already_initialized']

    @property
    def ref(self) -> ConversationRef:
        return self.conversation.ref

@dataclass(frozen=True)
class Conversation:
    ref: ConversationRef
    title: str | None
    title_source: str | None
    naming_attempted: bool
    participants: tuple[str, ...]
    messages: tuple[Message, ...]

@dataclass(frozen=True)
class PageCursor:
    ref: ConversationRef
    generation: int
    after_logical_seq: int

    def __post_init__(self) -> None:
        _validate_cursor(self, self.ref)

@dataclass(frozen=True)
class ConversationPage:
    conversation: Conversation
    cursor: PageCursor
    has_more: bool = False
    resync_required: bool = False

    @property
    def ref(self) -> ConversationRef:
        return self.conversation.ref

    @property
    def messages(self) -> tuple[Message, ...]:
        return self.conversation.messages

    @property
    def title(self) -> str | None:
        return self.conversation.title

    @property
    def title_source(self) -> str | None:
        return self.conversation.title_source

    @property
    def naming_attempted(self) -> bool:
        return self.conversation.naming_attempted

    @property
    def participants(self) -> tuple[str, ...]:
        return self.conversation.participants

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
    encoded = _encode_metadata(data)
    if (len(encoded) > MAX_METADATA_LEN
            or len(encoded.encode('utf-8')) > MAX_METADATA_LEN * 4):
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
    """Reject hostile nesting iteratively, without Python recursion."""
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

def _require_ref(ref: object) -> ConversationRef:
    if not isinstance(ref, ConversationRef):
        raise ValueError('expected conversation reference')
    return ref

def _require_actor(actor: object) -> str:
    if not isinstance(actor, str):
        raise ValueError('actor must be a bot id string')
    ConversationRef.dm(actor)
    return actor

def _validate_cursor(cursor: object, ref: ConversationRef) -> None:
    _require_ref(ref)
    if not isinstance(cursor, PageCursor) or cursor.ref != ref:
        raise ValueError('cursor reference mismatch')
    if (isinstance(cursor.generation, bool) or not isinstance(cursor.generation, int)
            or cursor.generation < 0):
        raise ValueError('invalid cursor generation')
    if (isinstance(cursor.after_logical_seq, bool)
            or not isinstance(cursor.after_logical_seq, int)
            or cursor.after_logical_seq < 0):
        raise ValueError('invalid cursor position')

def _validate_uuid(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError('invalid root instance id')
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ValueError('invalid root instance id') from exc
    if str(parsed) != value:
        raise ValueError('invalid root instance id')
    return value

def _local_error(ref: ConversationRef, message: str = 'conversation unavailable') -> ConversationStoreError:
    return ConversationStoreError(message, scope='local', ref=ref)

def _decode_metadata(raw: str) -> dict:
    if not isinstance(raw, str) or len(raw) > MAX_METADATA_LEN:
        raise ConversationStoreError('conversation corrupt')
    try:
        data = json.loads(raw, object_pairs_hook=_reject_duplicate_pairs)
        _check_depth(data)
    except (ValueError, RecursionError, TypeError) as exc:
        raise ConversationStoreError('conversation corrupt') from exc
    if not isinstance(data, dict):
        raise ConversationStoreError('conversation corrupt')
    return data

def _reject_duplicate_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result

def _decode_message(raw: str) -> Message:
    if not isinstance(raw, str) or len(raw) > MAX_STORED_MESSAGE_BYTES:
        raise ConversationStoreError('conversation corrupt')
    try:
        data = json.loads(raw, object_pairs_hook=_reject_duplicate_pairs)
        _check_depth(data)
        message = Message.from_dict(data)
    except (ValueError, RecursionError, TypeError) as exc:
        raise ConversationStoreError('conversation corrupt') from exc
    if message.role not in _ALLOWED_READ_ROLES:
        raise ConversationStoreError('conversation corrupt')
    return message

def _encode_canonical_message(message: Message) -> str:
    """Encode, parse, and validate exactly the shape the reader will decode."""
    try:
        payload = json.dumps(message.to_canonical_dict(), ensure_ascii=False,
                             allow_nan=False, sort_keys=True, separators=(',', ':'))
        decoded = json.loads(payload, object_pairs_hook=_reject_duplicate_pairs)
        _check_depth(decoded)
        Message.from_dict(decoded)
        if len(payload) > MAX_STORED_MESSAGE_BYTES:
            raise ValueError('message payload too large')
        return payload
    except (TypeError, RecursionError) as exc:
        raise ValueError('invalid message payload') from exc

