"""Phase 3 fixture-only turn pipeline routing (disposable roots only).

Live ``~/.thyca`` stays on the legacy JSONL path: every entry point here
refuses a root that was not explicitly enabled via
:func:`enable_fixture_writes`. While a fixture root is enabled, legacy
``SessionStore`` mutations under it fail closed (see ``fixture_switch``),
so the two writers can never interleave on the same root.

What this routes (fixture roots only):

- turn failures -> :func:`append_turn_error` (new trailing ``system`` row
  with ``meta.error``; the failed user/assistant rows keep their bytes —
  no history rewrite, unlike legacy ``mark_turn_error`` which stamps the
  user row in place).
- retry -> :func:`prepare_retry` (no-mutation readiness: last-user
  position plus generation; a concurrent purge/compaction surfaces as a
  bounded resync, never as a rewrite).
- naming/title -> atomic metadata updates that preserve the legacy
  one-shot semantics (naming fires once; the automatic title stores only
  when no title is on disk, atomically against a concurrent rename).
- trace -> :mod:`read_adapter` slices (``turns_from_session`` +
  ``message_dict(include_args=True)`` so ``tool_calls`` appear only in
  Trace, never in chat detail).

TurnState, bridge ``pump_stream``/``stream_turn``, and the 400/404/409/503
mapping need no fixture branch: ``TurnState`` is string-keyed (fixture
``group-...``/DM ids claim exactly like session ids), the bridge maps
failures through :func:`serve.errors.public_turn_error` (which now also
maps :class:`ConversationStoreError`: local scope -> 404, global -> 503),
and the NDJSON single-terminal contract is unchanged. Those surfaces are
pinned by fixture tests, not by new branches here.

The store rejects ``content=None`` and ``tool_calls``/``reasoning`` rows at
write time, so the legacy naming sidecar (meta-only assistant row) is
intentionally not persisted on fixture roots: the attempt flag in metadata
is the record. Trace projection below stamps a trailing ``system`` error
marker onto its turn's user message in memory only, so the shared
``_turn_status``/``_completed_turns`` predicates (which ignore ``system``
rows) report ``failed`` exactly like the legacy user-stamped marker —
without touching stored bytes.
"""
from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

from thyca.core.conversation import ConversationRef
from thyca.core.protocol import Message

from .agent import AgentStore
from .contracts import (
    MAX_PAGE_LIMIT,
    Conversation,
    ConversationStoreError,
    PageCursor,
    _decode_message,
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
from .fixture_switch import is_fixture_write_enabled
from .read_adapter import (
    SyntheticReadIntent,
    assemble_history,
    trace_detail_for,
    trace_turns_for,
)
from .write_adapter import (
    RetryReadiness,
    SyntheticWriteIntent,
    append_turn_error,
    prepare_retry,
    set_conversation_metadata,
)

__all__ = [
    'record_turn_error',
    'plan_turn_retry',
    'mark_fixture_naming_attempted',
    'store_fixture_title_if_missing',
    'refresh_fixture_title',
    'project_for_trace',
    'fixture_trace_turns_for',
    'fixture_trace_detail_for',
    'fixture_trace_turns',
    'fixture_trace_detail',
    'fixture_completed_turn_count',
]

#: Bound on paged re-reads while assembling one fixture view for a write.
_MAX_FIXTURE_READ_RESYNCS = 5


def _require_enabled_root(root: Path, intent=None) -> Path:
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


def _require_write_intent(intent: object):
    from .runtime import RuntimeWriteIntent as _RuntimeWriteIntent

    if isinstance(intent, (SyntheticWriteIntent, _RuntimeWriteIntent)):
        return intent
    raise ValueError('explicit synthetic or runtime write intent required')


def _check_page_limit(page_limit: object) -> int:
    if isinstance(page_limit, bool) or not isinstance(page_limit, int) or page_limit < 1:
        raise ValueError('page_limit must be a positive int')
    return min(page_limit, MAX_PAGE_LIMIT)


def _read_full(root: Path, ref: ConversationRef, actor: str,
               page_limit: int = 500) -> Conversation:
    """Paged full-history view for a metadata-preserving write (no intent).

    The caller already asserted fixture scope with a
    :class:`SyntheticWriteIntent` plus an enabled root; this internal read
    stays inside that scope. A mid-read generation change (purge/compaction
    under the reader) resyncs from the reset cursor, bounded.
    """
    _require_ref(ref)
    _require_actor(actor)
    page_limit = _check_page_limit(page_limit)
    store = AgentStore(root)
    collected: list[Message] = []
    cursor: PageCursor | None = None
    metadata = None
    restarts = 0
    while True:
        page = store.read(ref, actor, cursor=cursor, limit=page_limit)
        if page.resync_required:
            restarts += 1
            if restarts > _MAX_FIXTURE_READ_RESYNCS:
                raise ConversationStoreError('conversation unavailable')
            collected = []
            metadata = None
            cursor = page.cursor
            continue
        metadata = page.conversation
        collected.extend(page.messages)
        if not page.has_more:
            break
        cursor = page.cursor
    assert metadata is not None
    return _view(
        metadata.ref,
        {
            'title': metadata.title,
            'title_source': metadata.title_source,
            'naming_attempted': metadata.naming_attempted,
            'participants': list(metadata.participants),
        },
        tuple(collected),
    )


def record_turn_error(
    root: Path,
    ref: ConversationRef,
    *,
    intent: SyntheticWriteIntent,
    actor: str,
    code: str,
    message: str,
) -> int:
    """Record a turn failure as a trailing ``system`` marker; no rewrite.

    Thin route to :func:`write_adapter.append_turn_error` so the turn
    pipeline spells the fixture path explicitly. Membership-checked; one
    ``INSERT`` under ``BEGIN IMMEDIATE``; generation unchanged.
    """
    _require_write_intent(intent)
    _require_enabled_root(root, intent)
    return append_turn_error(
        root, ref, intent=intent, actor=actor, code=code, message=message
    )


def plan_turn_retry(
    root: Path,
    ref: ConversationRef,
    *,
    intent: SyntheticWriteIntent,
    actor: str,
    page_limit: int = 500,
) -> RetryReadiness:
    """No-mutation retry readiness for a fixture turn (no truncation)."""
    _require_write_intent(intent)
    _require_enabled_root(root, intent)
    return prepare_retry(
        root, ref, intent=intent, actor=actor, page_limit=page_limit
    )


def mark_fixture_naming_attempted(
    root: Path,
    ref: ConversationRef,
    *,
    intent: SyntheticWriteIntent,
    actor: str,
) -> Conversation:
    """Persist the automatic naming step's one attempt (fixture metadata).

    Mirrors ``SessionManager.mark_naming_attempted`` without a JSONL
    rewrite: flips ``naming_attempted`` in the door metadata atomically,
    preserving title/source/participants. Idempotent: an already-attempted
    door returns its current view with no write.
    """
    _require_write_intent(intent)
    _require_enabled_root(root, intent)
    _require_ref(ref)
    _require_actor(actor)
    current = _read_full(root, ref, actor)
    if current.naming_attempted:
        return current
    return set_conversation_metadata(
        root,
        ref,
        intent=intent,
        actor=actor,
        title=current.title,
        title_source=current.title_source,
        naming_attempted=True,
        participants=list(current.participants),
    )


def store_fixture_title_if_missing(
    root: Path,
    ref: ConversationRef,
    *,
    intent: SyntheticWriteIntent,
    actor: str,
    title: str,
) -> str | None:
    """Store the automatic title only when no title is on disk (atomic).

    Mirrors ``SessionStore.append_title_if_missing``: the check and the
    append share one ``BEGIN IMMEDIATE`` transaction with the membership
    check, so a concurrent sidebar rename landing between them wins and
    this call stores nothing (returns ``None``). Titles are vetted with
    the shared :func:`sessions.title.sanitize_title` rule; ``None``
    (blank/echo) stores nothing.
    """
    from thyca.sessions.title import sanitize_title

    _require_write_intent(intent)
    _require_enabled_root(root, intent)
    _require_ref(ref)
    _require_actor(actor)
    cleaned = sanitize_title(title)
    if cleaned is None:
        return None
    conn = open_existing_session(root, lock_mode='exclusive')
    try:
        with _transaction_context(conn, 'BEGIN IMMEDIATE'):
            _ensure_root_ready(conn, _root_instance_id_from_conn(conn))
            metadata, _generation, _ev, _log = _ensure_door_ready(conn, ref, actor)
            if metadata['title']:
                # A title landed first (e.g. a concurrent rename): leave it.
                return None
            next_metadata = {
                'title': cleaned,
                'title_source': None,
                'naming_attempted': bool(metadata['naming_attempted']),
                'participants': list(metadata['participants']),
            }
            validate_metadata(ref, next_metadata)
            try:
                cur = conn.execute(
                    'UPDATE conversations SET metadata=? WHERE kind=? AND id=?',
                    (_encode_metadata(next_metadata), ref.kind, ref.id),
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
            _view(ref, next_metadata, messages)
            return cleaned
    finally:
        conn.close()


def refresh_fixture_title(
    root: Path,
    ref: ConversationRef,
    *,
    actor: str,
) -> tuple[str | None, str | None]:
    """Current ``(title, title_source)`` for a fixture door (no intent).

    Mirrors ``sessions.title.refresh_title``: fixture views always re-read
    metadata, so a title that landed mid-turn is visible on the next read.
    Membership-checked; never writes. The fixture root must be enabled.
    """
    _require_enabled_root(root)
    _require_ref(ref)
    _require_actor(actor)
    current = _read_full(root, ref, actor)
    return current.title, current.title_source


def project_for_trace(conversation: Conversation) -> Conversation:
    """In-memory trace projection: stamp system error markers onto users.

    The store keeps failures as trailing ``system`` rows (append-only);
    the shared ``turn_slices``/``_turn_status``/``_completed_turns``
    predicates skip ``system`` rows, so an unprojected view would read a
    failed turn as completed. This walks the stored order once: each
    ``system`` marker's ``meta.error`` moves onto the nearest preceding
    ``user`` message (legacy ``mark_turn_error`` shape) and the marker
    rows are removed. Stored bytes are untouched; chat detail keeps
    showing the marker row, while trace/naming see the failed turn.
    Markers with no preceding user have no turn to fail and are dropped.
    """
    messages = list(conversation.messages)
    if not any(
        item.role == 'system'
        and isinstance((item.meta or {}).get('error'), dict)
        for item in messages
    ):
        return conversation
    stamped: list[Message] = []
    last_user_pos: int | None = None
    for index, item in enumerate(messages):
        if item.role == 'system' and isinstance((item.meta or {}).get('error'), dict):
            # A later assistant reply means the turn recovered (retry):
            # the failure is history, not the turn's current outcome.
            if any(later.role == 'assistant' for later in messages[index + 1:]):
                continue
            error = dict((item.meta or {})['error'])
            if last_user_pos is None:
                continue
            target = stamped[last_user_pos]
            meta = dict(target.meta or {})
            if 'error' not in meta:
                meta['error'] = {
                    'code': error.get('code'),
                    'message': error.get('message'),
                }
                stamped[last_user_pos] = replace(target, meta=meta)
            continue
        stamped.append(item)
        if item.role == 'user':
            last_user_pos = len(stamped) - 1
    return _view(
        conversation.ref,
        {
            'title': conversation.title,
            'title_source': conversation.title_source,
            'naming_attempted': conversation.naming_attempted,
            'participants': list(conversation.participants),
        },
        tuple(stamped),
    )


def fixture_trace_turns_for(conversation: Conversation):
    """Trace turns over a fixture conversation, with error projection."""
    return trace_turns_for(project_for_trace(conversation))


def fixture_trace_detail_for(
    conversation: Conversation,
    turn_index: int,
    *,
    skills_root=None,
):
    """Wire-shaped trace detail over a fixture conversation (projected)."""
    return trace_detail_for(
        project_for_trace(conversation), turn_index, skills_root=skills_root
    )


def fixture_trace_turns(
    root: Path,
    ref: ConversationRef,
    actor: str,
    *,
    intent,
    page_limit: int = 500,
):
    """Assemble history on a fixture root and return projected trace turns."""
    from .runtime import RuntimeReadIntent as _RuntimeReadIntent
    from .runtime import is_runtime_ready as _is_runtime_ready

    if isinstance(intent, _RuntimeReadIntent):
        if not _is_runtime_ready(root):
            raise ValueError('runtime store is not initialized for this root')
    elif isinstance(intent, SyntheticReadIntent):
        if not is_fixture_write_enabled(root):
            raise ValueError('fixture write route is not enabled for this root')
    else:
        raise ValueError('explicit synthetic or runtime read intent required')
    return fixture_trace_turns_for(
        assemble_history(root, ref, actor, intent=intent, page_limit=page_limit)
    )


def fixture_trace_detail(
    root: Path,
    ref: ConversationRef,
    actor: str,
    turn_index: int,
    *,
    intent,
    page_limit: int = 500,
    skills_root=None,
):
    """Assemble history on a fixture root and return one projected detail."""
    from .runtime import RuntimeReadIntent as _RuntimeReadIntent
    from .runtime import is_runtime_ready as _is_runtime_ready

    if isinstance(intent, _RuntimeReadIntent):
        if not _is_runtime_ready(root):
            raise ValueError('runtime store is not initialized for this root')
    elif isinstance(intent, SyntheticReadIntent):
        if not is_fixture_write_enabled(root):
            raise ValueError('fixture write route is not enabled for this root')
    else:
        raise ValueError('explicit synthetic or runtime read intent required')
    return fixture_trace_detail_for(
        assemble_history(root, ref, actor, intent=intent, page_limit=page_limit),
        turn_index,
        skills_root=skills_root,
    )


def fixture_completed_turn_count(messages) -> int:
    """Completed-turn count over fixture messages, failed turns excluded.

    Failed turns (trailing ``system`` error markers) must not satisfy the
    automatic-naming threshold, matching ``title.completed_turn_count`` on
    legacy user-stamped transcripts. Counts on the trace projection.
    """
    from thyca.sessions.title import completed_turn_count

    if not isinstance(messages, (list, tuple)):
        raise ValueError('messages must be a list or tuple')
    probe = _view(
        ConversationRef.dm('thyca'),
        {
            'title': None,
            'title_source': None,
            'naming_attempted': False,
            'participants': ['thyca'],
        },
        tuple(messages),
    )
    return completed_turn_count(list(project_for_trace(probe).messages))
