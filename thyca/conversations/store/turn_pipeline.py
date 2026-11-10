"""Runtime turn pipeline routing over the conversation store.

Every entry point here requires an explicit runtime intent and an
initialized runtime store root.

What this routes:

- turn failures -> :func:`append_turn_error` (new trailing ``system`` row
  with ``meta.error``; the failed user/assistant rows keep their bytes —
  no history rewrite).
- trace -> :mod:`read_adapter` slices (``turns_from_conversation`` +
  ``message_dict(include_args=True)`` so ``tool_calls`` appear only in
  Trace, never in chat detail).

The store rejects ``content=None`` and ``tool_calls``/``reasoning`` rows at
write time. Trace projection below stamps a trailing ``system`` error
marker onto its turn's user message in memory only, so the shared
``_turn_status`` predicate (which ignores ``system`` rows) reports
``failed`` — without touching stored bytes.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from thyca.core.conversation import ConversationRef
from thyca.core.protocol import Message

from .contracts import (
    Conversation,
    _view,
)
from .read_adapter import trace_turns_for
from .runtime import RuntimeWriteIntent
from .write_adapter import (
    append_turn_error,
)

__all__ = [
    'record_turn_error',
    'project_for_trace',
    'trace_turns_projected',
]


# Require an initialized runtime store root before any write.
def _require_enabled_root(root: Path, intent=None) -> Path:
    if not isinstance(root, Path):
        raise ValueError('root must be a Path')
    from .runtime import is_runtime_ready as _is_runtime_ready

    if isinstance(intent, RuntimeWriteIntent):
        if not _is_runtime_ready(root):
            raise ValueError('runtime store is not initialized for this root')
        return root
    raise ValueError('explicit runtime write intent required')


# Require an explicit runtime write intent before any store I/O.
def _require_write_intent(intent: object):
    if isinstance(intent, RuntimeWriteIntent):
        return intent
    raise ValueError('explicit runtime write intent required')


# Record one turn failure as a trailing store marker.
def record_turn_error(
    root: Path,
    ref: ConversationRef,
    *,
    intent: RuntimeWriteIntent,
    actor: str,
    code: str,
    message: str,
) -> int:
    """Record a turn failure as a trailing ``system`` marker; no rewrite.

    Thin route to :func:`write_adapter.append_turn_error` so the turn
    pipeline spells the runtime path explicitly. Membership-checked; one
    ``INSERT`` under ``BEGIN IMMEDIATE``; generation unchanged.
    """
    _require_write_intent(intent)
    _require_enabled_root(root, intent)
    return append_turn_error(
        root, ref, intent=intent, actor=actor, code=code, message=message
    )


# Project stored error markers onto their turns for trace views.
def project_for_trace(conversation: Conversation) -> Conversation:
    """In-memory trace projection: stamp system error markers onto users.

    The store keeps failures as trailing ``system`` rows (append-only);
    the shared ``turn_slices``/``_turn_status`` predicates skip ``system``
    rows, so an unprojected view would read a failed turn as completed.
    This walks the stored order once: each ``system`` marker's ``meta.error``
    moves onto the nearest preceding ``user`` message (legacy
    ``mark_turn_error`` shape) and the marker rows are removed. Stored
    bytes are untouched; chat detail keeps showing the marker row, while
    trace sees the failed turn.
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
            'participants': list(conversation.participants),
        },
        tuple(stamped),
    )


# Build projected trace turns over one assembled conversation.
def trace_turns_projected(conversation: Conversation):
    """Trace turns over a conversation, with error projection."""
    return trace_turns_for(project_for_trace(conversation))
