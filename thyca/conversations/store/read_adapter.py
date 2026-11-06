"""Read adapter: store -> wire payloads.

Reads only. Every entry point that touches the filesystem requires an
explicit :class:`RuntimeReadIntent` (runtime store path on any
initialized root, see ``runtime.py``). Without an explicit intent the
call fails before any I/O. All writes stay on their own paths.

Byte-compat contract (R1):
- ``summary`` mirrors ``sessions.wire.session_summary`` (same keys).
- ``detail`` mirrors ``sessions.wire.session_detail`` via ``message_dict``
  without ``include_args``: ``tool_calls``/``reasoning`` never appear.
- ``trace`` mirrors ``serve.trace``/``serve.trace_api`` via ``message_dict``
  with ``include_args=True``: ``tool_calls``/``tool_call_id``/``meta``
  (and ``reasoning`` when present) appear only here.
- ``updated_at`` is the last message ``ts``; empty conversations report the
  listing ``created_at`` stamp.
- Titles reuse ``sessions.wire.session_title`` (user titles verbatim, agent
  titles vetted, blank transcripts read ``"Phiên trống"``); the store
  already rejects ``content=None`` rows at write time.
- Full history is assembled across ``AgentStore.read`` pages with
  :class:`PageCursor`; a generation change mid-assembly resyncs from the
  start (bounded restarts, then a sanitized failure).
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from thyca.core.conversation import ConversationRef
from thyca.core.protocol import Message, parse_ts
from thyca.serve.trace import TurnSummary, turns_from_session
from thyca.conversations.runtime import Session, calibration_factor, context_usage
from thyca.conversations.runtime.wire import ask_remember, message_dict, session_title, turn_slices

from .agent import AgentStore
from .contracts import (
    MAX_PAGE_LIMIT,
    Conversation,
    ConversationStoreError,
    PageCursor,
)
from .runtime import RuntimeReadIntent

#: Bound on resync restarts while assembling one history. A concurrently
#: compacted/purged conversation bumps generation per page; without a bound
#: the assembler could chase it forever. Fixture readers without writers
#: never restart.
_MAX_ASSEMBLY_RESYNCS = 5


# Require an explicit runtime read intent before any store I/O.
def _require_intent(intent: object):
    if isinstance(intent, RuntimeReadIntent):
        return intent
    raise ValueError('explicit runtime read intent required')


def _check_page_limit(page_limit: object) -> int:
    if isinstance(page_limit, bool) or not isinstance(page_limit, int) or page_limit < 1:
        raise ValueError('page_limit must be a positive int')
    return min(page_limit, MAX_PAGE_LIMIT)


# Assemble one full conversation history across paged store reads.
def assemble_history(
    root: Path,
    ref: ConversationRef,
    actor: str,
    *,
    intent: RuntimeReadIntent,
    page_limit: int = 500,
) -> Conversation:
    """Assemble one full conversation across ``AgentStore.read`` pages.

    Returns the single :class:`Conversation` view (metadata from the final
    page, all messages in logical order). A mid-assembly generation change
    (purge/compaction under the reader) discards the partial prefix and
    restarts from the reset cursor, up to ``_MAX_ASSEMBLY_RESYNCS`` times.
    """
    _require_intent(intent)
    page_limit = _check_page_limit(page_limit)
    if not isinstance(ref, ConversationRef):
        raise ValueError('expected conversation reference')
    if not isinstance(actor, str) or not actor:
        raise ValueError('actor must be a non-empty bot id string')
    store = AgentStore(Path(root))
    collected: list[Message] = []
    cursor: PageCursor | None = None
    metadata = None
    restarts = 0
    while True:
        page = store.read(ref, actor, cursor=cursor, limit=page_limit)
        if page.resync_required:
            restarts += 1
            if restarts > _MAX_ASSEMBLY_RESYNCS:
                raise ConversationStoreError('conversation unavailable')
            collected = []
            metadata = None
            cursor = page.cursor
            continue
        if metadata is None:
            metadata = page.conversation
        else:
            # Titles may be renamed between pages (metadata writes do not
            # bump generation); the final page carries the newest stamp.
            metadata = page.conversation
        collected.extend(page.messages)
        if not page.has_more:
            break
        cursor = page.cursor
    assert metadata is not None  # read always returns a conversation view
    from .contracts import _view

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


# Project a store conversation onto the wire Session shape.
def _wire_session(
    conversation: Conversation,
    *,
    session_id: str | None = None,
) -> Session:
    """Project a store conversation onto the :class:`Session` shape.

    ``path`` is a placeholder: ``turns_from_session`` and the
    title/blank helpers never stat it. ``updated_at`` callers must not use
    this session's path; the adapter resolves timestamps explicitly.
    """
    ref = conversation.ref
    return Session(
        session_id or ref.id,
        Path(f'store://{ref.kind}/{ref.id}'),
        list(conversation.messages),
        title=conversation.title,
        title_source=conversation.title_source,
        naming_attempted=conversation.naming_attempted,
        bots=list(conversation.participants),
        dm_bot=ref.id if ref.kind == 'dm' else None,
    )


def _updated_at(messages: tuple[Message, ...] | list[Message], created_at: str | None) -> str:
    if messages:
        last_ts = messages[-1].ts
        parse_ts(last_ts)
        return last_ts
    if created_at is None:
        raise ValueError('created_at is required for an empty conversation')
    parse_ts(created_at)
    return created_at


# Build the wire summary with kind taken from the conversation ref.
def summary_for(conversation: Conversation, created_at: str) -> dict:
    """Wire-shaped summary for one assembled conversation (pure, no I/O)."""
    session = _wire_session(conversation)
    return {
        'id': session.id,
        'title': session_title(session),
        'updated_at': _updated_at(conversation.messages, created_at),
        'message_count': len(conversation.messages),
        'turns': len(turn_slices(list(conversation.messages))),
        'bots': list(session.bots) if session.bots else ['thyca'],
        'dm': conversation.ref.id if conversation.ref.kind == 'dm' else None,
        'kind': conversation.ref.kind,
        'channel': 'webui',
        'window_id': session.id,
    }


# Build the wire detail with kind taken from the conversation ref.
def detail_for(
    conversation: Conversation,
    cfg,
    *,
    skills_root: Path | None = None,
) -> dict:
    """Wire-shaped detail for one assembled conversation (pure, no I/O).

    ``message_dict`` runs without ``include_args`` so ``tool_calls`` and
    ``reasoning`` never appear here; they surface only in trace payloads.
    ``context`` is the transcript-only lower bound, matching the wire
    fallback for I/O-free callers.
    """
    session = _wire_session(conversation)
    context = context_usage(
        list(conversation.messages),
        cfg.effective_limits().contextTokens,
        calibration=calibration_factor(
            list(conversation.messages), model=cfg.provider.model
        ),
    )
    detail = {
        'id': session.id,
        'title': session_title(session),
        'model': cfg.provider.model,
        'bots': list(session.bots) if session.bots else ['thyca'],
        'dm': conversation.ref.id if conversation.ref.kind == 'dm' else None,
        'kind': conversation.ref.kind,
        'channel': 'webui',
        'window_id': session.id,
        'messages': [message_dict(item, skills_root) for item in conversation.messages],
        'ask_remember': ask_remember(list(conversation.messages), datetime.now(UTC)),
        'context': context,
    }
    detail['running'] = False
    return detail


# Build trace turns over one assembled conversation.
def trace_turns_for(conversation: Conversation) -> list[TurnSummary]:
    """Trace turns over one assembled conversation (pure, no I/O)."""
    return turns_from_session(_wire_session(conversation))


# Build one wire-shaped trace detail over an assembled conversation.
def trace_detail_for(
    conversation: Conversation,
    turn_index: int,
    *,
    skills_root: Path | None = None,
) -> dict:
    """Wire-shaped trace detail for one turn (pure, no I/O).

    Messages render with ``include_args=True``: ``tool_calls``,
    ``tool_call_id`` and ``meta`` always present, ``reasoning`` when set.
    Phase 1 has no canonical execution rows to merge; the public rows are
    the whole turn.
    """
    if isinstance(turn_index, bool) or not isinstance(turn_index, int) or turn_index < 0:
        raise ValueError('turn_index must be a non-negative int')
    for turn in trace_turns_for(conversation):
        if turn.turn_index == turn_index:
            payload = turn.to_payload()
            payload['messages'] = [
                message_dict(m, skills_root, include_args=True) for m in turn.messages
            ]
            return payload
    raise ValueError('trace not found')
