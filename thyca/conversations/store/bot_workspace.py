"""Phase 4 fixture-only bot/workspace/A2A/memory routing (disposable roots only).

Live ``~/.thyca`` stays on the legacy JSONL path: every I/O entry point
here refuses a root that was not explicitly enabled via
:func:`enable_fixture_writes`. While a fixture root is enabled, legacy
``SessionStore`` mutations under it fail closed (see ``fixture_switch``),
so the two writers can never interleave on the same root. No live
bot/DM creation or deletion happens here: DMs/groups are seeded through
:mod:`admin`/:mod:`write_adapter` (which never mint live state), bot
cards are never created, and per-bot settings never touch a live bot
directory.

What this routes (fixture roots only):

- DM/group windows -> :func:`fixture_window_for` (pure projection of a
  store conversation onto the ``webui`` :class:`WindowRef`, mirroring
  ``WebUIChannelAdapter.window_for``) plus :func:`fixture_private_fallback`
  (the synthetic-room private view ``BotRuntime._window_for_room`` falls
  back to when no session exists).
- origin privacy -> :func:`fixture_origin_info` (membership-checked
  ``MemoryView._origin_info`` equivalent over store doors: a missing or
  unreadable door reads as unknown ``None``; a corrupt door raises
  instead of silently downgrading to "private").
- room/DM/card posts -> :func:`fixture_post_room_message`,
  :func:`fixture_post_dm_message`, :func:`fixture_post_card` (the
  ``BotPostingService`` + ``WebUIOutboundAdapter.land`` gates — own-DM
  privacy, participant membership, pending-marker dedupe — with rows
  appended through :class:`AgentStore` instead of the legacy session
  file).
- A2A recording -> :func:`fixture_record_bus_envelope` (the
  ``A2ADeliveryService.record_bus_events`` row shape: ``bus_event``,
  explicit ``recipient``, trace/span linkage, refs cap, truncation flag)
  plus :func:`fixture_collect_exchange_rooms` (per-conversation message
  lists for the pure :mod:`thyca.bots.exchanges` pairing over fixture
  history instead of legacy sessions).
- per-bot settings -> :func:`fixture_read_bot_settings` /
  :func:`fixture_write_bot_settings` (``bots/<id>/settings.json`` on the
  fixture root only; the directory must already exist — this never
  creates a bot, matching ``write_settings``).
- memory write-through -> :func:`fixture_memory_remember`
  (``MemoryFacade`` on the fixture root with an explicit owner, so the
  sidecar flush path lands under the disposable root, never ``~/.thyca``).
- canonical execution -> :func:`fixture_canonical_messages`
  (``WorkspaceTimeline.messages_for_window`` on the fixture root: the
  ``CanonicalSessionStore`` journal rows the operations/trace surfaces
  project, proving they survive store-door compaction).

Turn execution itself (``BotRuntime``/``AgentLoop``/``Observe``/
compaction) is covered by exercising those real classes against fixture
roots in tests; this module only routes their inputs/outputs. Phase 5
owns any deletion; nothing here deletes.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from thyca.core.channel import WindowRef
from thyca.core.conversation import ConversationRef
from thyca.core.protocol import Message

from .admin import AdminStore
from .agent import AgentStore
from .contracts import (
    Conversation,
    ConversationListing,
    ConversationStoreError,
)
from .fixture_switch import is_fixture_write_enabled
from .read_adapter import SyntheticReadIntent, assemble_history
from .write_adapter import SyntheticWriteIntent

__all__ = [
    'fixture_window_for',
    'fixture_private_fallback',
    'fixture_origin_info',
    'fixture_post_room_message',
    'fixture_post_dm_message',
    'fixture_post_card',
    'fixture_record_bus_envelope',
    'fixture_collect_exchange_rooms',
    'fixture_read_bot_settings',
    'fixture_write_bot_settings',
    'fixture_memory_remember',
    'fixture_canonical_messages',
]

#: Refs carried on an A2A bus row are clipped like the prompt render
#: (``BotRuntime._mailbox_text``): at most this many entries.
_MAX_BUS_REFS = 8

_WEBUI_CHANNEL = 'webui'


def _require_enabled_root(root: Path, intent=None) -> Path:
    if not isinstance(root, Path):
        raise ValueError('root must be a Path')
    from .runtime import RuntimeReadIntent as _RuntimeReadIntent
    from .runtime import RuntimeWriteIntent as _RuntimeWriteIntent
    from .runtime import is_runtime_ready as _is_runtime_ready

    if isinstance(intent, (_RuntimeReadIntent, _RuntimeWriteIntent)):
        if not _is_runtime_ready(root):
            raise ValueError('runtime store is not initialized for this root')
        return root
    # No intent (e.g. refresh helpers): allow either fixture or runtime.
    if intent is None:
        if is_fixture_write_enabled(root) or _is_runtime_ready(root):
            return root
        raise ValueError('fixture write route is not enabled for this root')
    if not is_fixture_write_enabled(root):
        raise ValueError('fixture write route is not enabled for this root')
    return root


def _require_read_intent(intent: object):
    from .runtime import RuntimeReadIntent as _RuntimeReadIntent

    if isinstance(intent, (SyntheticReadIntent, _RuntimeReadIntent)):
        return intent
    raise ValueError('explicit synthetic or runtime read intent required')


def _require_write_intent(intent: object):
    from .runtime import RuntimeWriteIntent as _RuntimeWriteIntent

    if isinstance(intent, (SyntheticWriteIntent, _RuntimeWriteIntent)):
        return intent
    raise ValueError('explicit synthetic or runtime write intent required')


def fixture_window_for(ref: ConversationRef, conversation: Conversation) -> WindowRef:
    """Project a store conversation onto its ``webui`` window (pure, no I/O).

    Mirrors ``WebUIChannelAdapter.window_for`` and the bound branch of
    ``BotRuntime._window_for_room``: the ref's kind decides dm/group, the
    conversation's participants are the window's participants. An empty
    participant list falls back to ``("thyca",)`` exactly like the legacy
    adapter; a ref/conversation kind mismatch fails instead of guessing.
    """
    if not isinstance(ref, ConversationRef):
        raise ValueError('expected conversation reference')
    if not isinstance(conversation, Conversation):
        raise ValueError('expected conversation')
    if conversation.ref != ref:
        raise ValueError('conversation does not belong to this reference')
    participants = tuple(conversation.participants) or ('thyca',)
    return WindowRef(
        window_id=ref.id,
        channel_id=_WEBUI_CHANNEL,
        kind=ref.kind,
        participants=participants,
    )


def fixture_private_fallback(room_id: str, bot_id: str) -> WindowRef:
    """Private view for a room with no session (pure, no I/O).

    Mirrors the synthetic-room fallback in ``BotRuntime``: when
    ``_window_for_room`` finds no session (A2A without a room), the bot
    turns in a DM-scoped window owned by itself, matching the
    private-view fallback. Never touches the filesystem.
    """
    if not isinstance(room_id, str) or not room_id:
        raise ValueError('room_id must be a non-empty string')
    ConversationRef.dm(bot_id)
    return WindowRef(
        window_id=room_id,
        channel_id=_WEBUI_CHANNEL,
        kind='dm',
        participants=(bot_id,),
    )


# Resolve a stored window id to its ref by database lookup only.
def _ref_for_window(root: Path, window_id: str) -> ConversationRef | None:
    """Map a stored window id to its ref via the conversations table.

    Kind comes from the stored row, never from identifier text: the id
    is looked up across both kinds and the row's own ``kind`` builds the
    ref. Unknown or malformed ids return ``None`` (unknown origin).
    """
    if not isinstance(window_id, str) or not window_id:
        return None
    from .database import (
        _ensure_root_ready,
        _root_instance_id_from_conn,
        _transaction_context,
        open_existing_session,
    )

    try:
        conn = open_existing_session(Path(root), lock_mode='shared')
        try:
            with _transaction_context(conn, 'BEGIN'):
                _ensure_root_ready(conn, _root_instance_id_from_conn(conn))
                rows = conn.execute(
                    "SELECT kind FROM conversations WHERE id=? AND lifecycle_state='ready'",
                    (window_id,),
                ).fetchall()
        finally:
            conn.close()
    except ConversationStoreError:
        raise
    for (kind,) in rows:
        try:
            return ConversationRef(kind, window_id)
        except ValueError:
            continue
    return None


def fixture_origin_info(
    root: Path,
    chat: str,
    *,
    intent: SyntheticReadIntent,
    actor: str,
) -> tuple[bool, str | None, tuple[str, ...]] | None:
    """``(is_dm, dm_bot, participants)`` of a fixture origin door.

    Membership-checked ``MemoryView._origin_info`` equivalent over store
    doors: *actor* must currently participate in the origin door it asks
    about. A missing door, a removed actor, or a malformed window id
    reads as unknown (``None``) — the caller denies, exactly like the
    legacy ``SessionNotFound -> None`` path. A corrupt door raises
    instead of silently downgrading to "private", matching the legacy
    corrupt-file behavior of propagating rather than hiding a data
    problem.
    """
    _require_read_intent(intent)
    _require_enabled_root(root, intent)
    ref = _ref_for_window(Path(root), chat)
    if ref is None:
        return None
    try:
        view = AgentStore(Path(root)).read(ref, actor)
    except ConversationStoreError as exc:
        # Missing doors and removed actors read as unknown (deny);
        # only a corrupt door propagates. Membership denial arrives as
        # a non-corrupt store error, so the message — not the scope —
        # is the discriminator, documented here next to the branch.
        if 'corrupt' in str(exc):
            raise
        return None
    if ref.kind == 'dm':
        return True, ref.id, (ref.id,)
    return False, None, tuple(view.participants)


def _read_view(root: Path, ref: ConversationRef, actor: str) -> Conversation:
    """Full door view for a membership-checked writer (fixture scope)."""
    from .contracts import PageCursor, _view

    store = AgentStore(root)
    collected: list[Message] = []
    cursor: PageCursor | None = None
    metadata = None
    restarts = 0
    while True:
        page = store.read(ref, actor, cursor=cursor)
        if page.resync_required:
            restarts += 1
            if restarts > 5:
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


def _deduped(messages: tuple[Message, ...], marker: dict) -> bool:
    """True when a pending-marker row was already recorded (crash resume)."""
    for item in reversed(messages):
        meta = item.meta or {}
        if not isinstance(meta, dict):
            continue
        if marker.get('pending_id') is not None and meta.get('pending_id') != marker['pending_id']:
            continue
        if all(meta.get(key) == value for key, value in marker.items()):
            return True
    return False


def fixture_post_room_message(
    root: Path,
    ref: ConversationRef,
    *,
    intent: SyntheticWriteIntent,
    actor: str,
    text: str,
    meta: dict | None = None,
) -> dict:
    """Post a status row into a fixture room as *actor* (own-DM safe).

    Mirrors ``BotPostingService.post_bot_room_message``: an unknown or
    unreadable room reads as ``unknown room``; another bot's DM refuses;
    a non-participant in a group refuses; a ``meta.pending_id`` marker
    already on disk dedupes instead of double-posting (crash-safe
    resume). The row lands as an ``assistant`` message stamped with
    ``bot_id``/``kind=status`` through :class:`AgentStore` — never a
    legacy session file.
    """
    _require_write_intent(intent)
    _require_enabled_root(root, intent)
    if not isinstance(ref, ConversationRef):
        raise ValueError('expected conversation reference')
    if not isinstance(text, str) or not text:
        raise ValueError('text must be a non-empty string')
    if meta is not None and not isinstance(meta, dict):
        raise ValueError('meta must be a dict or None')
    try:
        view = _read_view(Path(root), ref, actor)
    except ConversationStoreError:
        return {'ok': False, 'error': f'unknown room: {ref.id}'}
    if ref.kind == 'dm' and ref.id != actor:
        return {'ok': False, 'error': "room is another bot's DM"}
    if ref.kind == 'group' and actor not in view.participants:
        return {'ok': False, 'error': f'not a participant: {actor}'}
    marker = meta.get('pending_id') if isinstance(meta, dict) else None
    if marker is not None and _deduped(
        view.messages,
        {
            'pending_id': marker,
            'kind': (meta or {}).get('kind'),
            'proposal_revision': (meta or {}).get('proposal_revision'),
            'bot_id': actor,
        },
    ):
        return {'ok': True, 'session_id': ref.id, 'deduped': True}
    row_meta: dict = {'bot_id': actor, 'kind': 'status'}
    if meta:
        row_meta.update(meta)
    AgentStore(Path(root)).append(
        ref, actor, Message(role='assistant', content=text, meta=row_meta)
    )
    return {'ok': True, 'session_id': ref.id}


def fixture_post_dm_message(
    root: Path,
    ref: ConversationRef,
    *,
    intent: SyntheticWriteIntent,
    actor: str,
    text: str,
    meta: dict | None = None,
) -> dict:
    """Post into *actor*'s own fixture DM (never another bot's).

    Mirrors ``BotPostingService.post_bot_dm_message``: the target must be
    the actor's own DM door; a ``private_question`` row carrying the same
    ``pfq_marker`` dedupes instead of double-posting.
    """
    _require_write_intent(intent)
    _require_enabled_root(root, intent)
    if not isinstance(ref, ConversationRef):
        raise ValueError('expected conversation reference')
    if not isinstance(text, str) or not text:
        raise ValueError('text must be a non-empty string')
    if meta is not None and not isinstance(meta, dict):
        raise ValueError('meta must be a dict or None')
    if ref.kind != 'dm' or ref.id != actor:
        return {'ok': False, 'error': "room is another bot's DM"}
    try:
        view = _read_view(Path(root), ref, actor)
    except ConversationStoreError:
        return {'ok': False, 'error': f'unknown room: {ref.id}'}
    marker = (meta or {}).get('pfq_marker')
    if isinstance(meta, dict) and meta.get('kind') == 'private_question' and marker is not None:
        for item in reversed(view.messages):
            item_meta = item.meta or {}
            if (
                isinstance(item_meta, dict)
                and item_meta.get('pfq_marker') == marker
            ):
                return {'ok': True, 'session_id': ref.id, 'deduped': True}
    row_meta: dict = {'bot_id': actor, 'kind': 'message'}
    if meta:
        row_meta.update(meta)
    AgentStore(Path(root)).append(
        ref, actor, Message(role='assistant', content=text, meta=row_meta)
    )
    return {'ok': True, 'session_id': ref.id}


def fixture_post_card(
    root: Path,
    ref: ConversationRef,
    *,
    intent: SyntheticWriteIntent,
    actor: str,
    card: dict,
    content: str = '',
) -> dict:
    """Post a structured card row into a fixture room as *actor*.

    Mirrors ``BotPostingService.post_bot_card`` (the ``ask_user`` /
    ``report_error`` landing path): another bot's DM refuses, a group
    only accepts its participants, and the card rides on an assistant
    row's ``meta.card``. No channel-event stamping happens here — there
    is no in-flight turn on the fixture route — so ``channel``/``window``
    attribution stays absent rather than fabricated.
    """
    _require_write_intent(intent)
    _require_enabled_root(root, intent)
    if not isinstance(ref, ConversationRef):
        raise ValueError('expected conversation reference')
    if not isinstance(card, dict):
        raise ValueError('card must be a dict')
    if not isinstance(content, str):
        raise ValueError('content must be a string')
    try:
        view = _read_view(Path(root), ref, actor)
    except ConversationStoreError:
        return {'ok': False, 'error': f'unknown room: {ref.id}'}
    if ref.kind == 'dm' and ref.id != actor:
        return {'ok': False, 'error': 'current room is another bot\u2019s private DM'}
    if ref.kind == 'group' and actor not in view.participants:
        return {'ok': False, 'error': f'bot is not a participant of this room: {actor}'}
    AgentStore(Path(root)).append(
        ref,
        actor,
        Message(
            role='assistant',
            content=content,
            meta={'bot_id': actor, 'kind': 'card', 'card': dict(card)},
        ),
    )
    return {'ok': True, 'session_id': ref.id}


def fixture_record_bus_envelope(
    root: Path,
    ref: ConversationRef,
    *,
    intent: SyntheticWriteIntent,
    actor: str,
    sender: str,
    recipient: str,
    kind: str,
    payload: str,
    trace_id: str,
    span_id: str,
    parent_span_id: str | None = None,
    refs: tuple[str, ...] | list[str] = (),
    truncated: bool = False,
) -> dict:
    """Record one A2A envelope as a bus row in a fixture room.

    Mirrors ``A2ADeliveryService.record_bus_events`` row shape (sender in
    ``meta.bot_id``, explicit ``meta.recipient``, ``bus_event=True``,
    trace/span linkage, capped refs, truncation flag) with rows appended
    through :class:`AgentStore`. DM privacy matches the service: a DM
    records only when its owner is the sender or the recipient — a send
    explicitly targeted into a third bot's DM records nowhere
    (``recorded=False``) instead of leaking. Group rooms record every
    envelope. *actor* is the membership-checked reader/writer; it must
    currently participate in the door.
    """
    _require_write_intent(intent)
    _require_enabled_root(root, intent)
    if not isinstance(ref, ConversationRef):
        raise ValueError('expected conversation reference')
    ConversationRef.dm(sender)
    ConversationRef.dm(recipient)
    if not isinstance(kind, str) or not kind:
        raise ValueError('kind must be a non-empty string')
    if not isinstance(payload, str):
        raise ValueError('payload must be a string')
    if not isinstance(trace_id, str) or not trace_id:
        raise ValueError('trace_id must be a non-empty string')
    if not isinstance(span_id, str) or not span_id:
        raise ValueError('span_id must be a non-empty string')
    if parent_span_id is not None and (
        not isinstance(parent_span_id, str) or not parent_span_id
    ):
        raise ValueError('parent_span_id must be a string or None')
    refs = tuple(refs or ())
    for item in refs:
        if not isinstance(item, str):
            raise ValueError('refs must be strings')
    try:
        _read_view(Path(root), ref, actor)
    except ConversationStoreError:
        return {'ok': False, 'error': f'unknown room: {ref.id}'}
    if ref.kind == 'dm' and ref.id not in (sender, recipient):
        return {'ok': True, 'session_id': ref.id, 'recorded': False}
    meta: dict = {
        'bot_id': sender,
        'recipient': recipient,
        'kind': kind,
        'bus_event': True,
        'trace_id': trace_id,
        'parent_span_id': parent_span_id,
        'span_id': span_id,
    }
    if refs:
        meta['refs'] = list(refs[:_MAX_BUS_REFS])
    if truncated:
        meta['truncated'] = True
    seq = AgentStore(Path(root)).append(
        ref, actor, Message(role='assistant', content=payload, meta=meta)
    )
    return {'ok': True, 'session_id': ref.id, 'recorded': True, 'seq': seq}


def fixture_collect_exchange_rooms(
    root: Path,
    *,
    intent: SyntheticReadIntent,
    actor_for: Callable[[ConversationListing], str],
    page_limit: int = 500,
) -> list[list[Message]]:
    """Per-conversation message lists for exchange pairing (fixture only).

    The store-side ``collect_exchange_rooms``: newest-first listing via
    :class:`AdminStore`, one cursor-paged :func:`assemble_history` per
    door with the caller-supplied membership-checked reader. Pairing
    itself stays in the pure :mod:`thyca.bots.exchanges` helpers over
    the returned lists, so unrelated pairs never surface.
    """
    _require_read_intent(intent)
    _require_enabled_root(root, intent)
    if not callable(actor_for):
        raise ValueError('actor_for must be callable')
    from .contracts import MAX_PAGE_LIMIT

    if isinstance(page_limit, bool) or not isinstance(page_limit, int) or page_limit < 1:
        raise ValueError('page_limit must be a positive int')
    page_limit = min(page_limit, MAX_PAGE_LIMIT)
    admin = AdminStore(Path(root))
    listings, _ = admin.list()
    rooms: list[list[Message]] = []
    for listing in listings:
        actor = actor_for(listing)
        conversation = assemble_history(
            Path(root), listing.ref, actor, intent=intent, page_limit=page_limit
        )
        rooms.append(list(conversation.messages))
    return rooms


def fixture_read_bot_settings(
    root: Path, bot_id: str, *, intent: SyntheticReadIntent
):
    """Read one fixture bot's persisted model settings (no creation).

    Delegates to :func:`bots.settings.read_settings` on the fixture
    root: a missing file reads as ``None`` (fall back to the legacy card
    model); a present-but-invalid file raises instead of silently
    falling back. Never creates bot state.
    """
    from thyca.bots.settings import read_settings

    _require_read_intent(intent)
    _require_enabled_root(root, intent)
    return read_settings(Path(root), bot_id)


def fixture_write_bot_settings(
    root: Path, bot_id: str, model, *, intent: SyntheticWriteIntent
):
    """Persist one fixture bot's model settings (fixture bot dir only).

    Delegates to :func:`bots.settings.write_settings`: the bot directory
    must already exist (seeded by the test) — this never creates a bot,
    and the live root can never be enabled so live settings stay
    untouched.
    """
    from thyca.bots.settings import write_settings

    _require_write_intent(intent)
    _require_enabled_root(root, intent)
    return write_settings(Path(root), bot_id, model)


def fixture_memory_remember(
    root: Path,
    *,
    intent: SyntheticWriteIntent,
    actor: str,
    topic: str,
    summary: str,
    content: str = '',
    chat: str | None = None,
    timezone_name: str | None = None,
    now=None,
) -> str:
    """Write one memory entry owned by *actor* on the fixture root.

    The sidecar flush path (``BackgroundJobsService._flush_bot_memory``)
    writes through the Thyca-privileged facade; this is its fixture
    spelling: an explicit owner, an explicit origin window, and a facade
    rooted at the disposable fixture root — never ``~/.thyca``. Returns
    the memory entry id. ``now`` passes through to the facade: past
    timestamps archive for search-back (today's entries stay in the
    hot tail, never the search index — existing memory behavior).
    """
    from thyca.core.conversation import ConversationRef as _Ref

    from thyca.memory.facade import MemoryFacade

    _require_write_intent(intent)
    _require_enabled_root(root, intent)
    _Ref.dm(actor)
    if not isinstance(topic, str) or not topic.strip():
        raise ValueError('topic must be a non-empty string')
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError('summary must be a non-empty string')
    if not isinstance(content, str):
        raise ValueError('content must be a string')
    facade = MemoryFacade(Path(root), timezone_name=timezone_name)
    return facade.remember(
        topic, summary, content, bots=[actor], chat=chat, now=now,
    )


def fixture_canonical_messages(
    root: Path, bot_id: str, window: WindowRef, *, intent: SyntheticReadIntent
) -> list[Message]:
    """Original canonical execution rows for one fixture bot+window.

    Reads ``WorkspaceTimeline.messages_for_window`` without creating
    state: the exact journal rows ``CanonicalSessionStore`` projects, so
    operations/trace detail survives store-door compaction (which never
    touches the timeline). A missing timeline raises — an unreadable
    journal is unavailable, never empty.
    """
    from thyca.bots.timeline import WorkspaceTimeline

    _require_read_intent(intent)
    _require_enabled_root(root, intent)
    if not isinstance(window, WindowRef):
        raise ValueError('expected window reference')
    timeline = WorkspaceTimeline(Path(root), bot_id, create=False)
    return timeline.messages_for_window(window)
