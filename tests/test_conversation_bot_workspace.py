"""Phase 4 fixture-only bot/workspace/A2A/memory routing (disposable roots only).

No live server, no live ``~/.thyca``, no live bot/DM creation or
deletion. Every store write goes through an explicitly enabled fixture
root; every legacy-consumer check seeds legacy sessions under its own
``tmp_path`` with the fixture switch off. Rollback: reseed the fixture
roots (nothing here touches production state or the runtime wiring).
"""
from __future__ import annotations

import inspect
import uuid
from io import StringIO
from pathlib import Path

import pytest

from thyca.conversations.store import (
    AdminStore,
    AgentStore,
    ConversationStoreError,
    DMBootstrapIntent,
    RootInitializationIntent,
    SyntheticReadIntent,
    SyntheticWriteIntent,
    disable_fixture_writes,
    enable_fixture_writes,
    fixture_canonical_messages,
    fixture_collect_exchange_rooms,
    fixture_memory_remember,
    fixture_origin_info,
    fixture_post_card,
    fixture_post_dm_message,
    fixture_post_room_message,
    fixture_private_fallback,
    fixture_read_bot_settings,
    fixture_record_bus_envelope,
    fixture_window_for,
    fixture_write_bot_settings,
    is_fixture_write_enabled,
)
from thyca.conversations.store import bot_workspace
from thyca.core.conversation import ConversationRef
from thyca.core.protocol import Message


def _root_id(root: Path) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, str(root.absolute())))


def _init(root: Path) -> None:
    AdminStore(root).initialize(RootInitializationIntent(_root_id(root)))


def _dm(root: Path, bot: str) -> ConversationRef:
    attempt = uuid.uuid5(uuid.NAMESPACE_DNS, f'{root.absolute()}:{bot}').hex
    return AdminStore(root).bootstrap_initial_dm(
        bot, intent=DMBootstrapIntent(bot, attempt)).ref


def _read_intent() -> SyntheticReadIntent:
    return SyntheticReadIntent('phase4-fixture-bots')


def _write_intent() -> SyntheticWriteIntent:
    return SyntheticWriteIntent('phase4-fixture-bots')


def _msg(role: str, text: str, meta: dict | None = None) -> Message:
    return Message(role=role, content=text, meta=meta)


@pytest.fixture
def switched(tmp_path: Path):
    _init(tmp_path)
    enable_fixture_writes(tmp_path)
    assert is_fixture_write_enabled(tmp_path)
    try:
        yield tmp_path
    finally:
        disable_fixture_writes(tmp_path)


# ---------- module hygiene: no live creation paths, explicit gates ----------

def test_bot_workspace_has_no_live_creation_or_implicit_actor() -> None:
    source = inspect.getsource(bot_workspace)
    assert 'current_bot_id(' not in source
    assert 'BotRegistry' not in source
    assert 'ChatApp' not in source
    # No live-root filesystem access: the live home dir is never resolved
    # here (the docstring names it only to state it stays untouched).
    assert 'Path.home()' not in source
    assert 'bootstrap_initial_dm(' not in source
    assert 'create_group(' not in source
    # Every I/O entry point takes an explicit intent with no default.
    for name in (
        'fixture_origin_info', 'fixture_post_room_message',
        'fixture_post_dm_message', 'fixture_post_card',
        'fixture_record_bus_envelope', 'fixture_collect_exchange_rooms',
        'fixture_read_bot_settings', 'fixture_write_bot_settings',
        'fixture_memory_remember', 'fixture_canonical_messages',
    ):
        params = inspect.signature(getattr(bot_workspace, name)).parameters
        assert 'intent' in params
        assert params['intent'].default is inspect.Parameter.empty


def test_fixture_gates_fail_before_io(tmp_path: Path) -> None:
    _init(tmp_path)
    ref = _dm(tmp_path, 'coder')
    AgentStore(tmp_path).append(ref, 'coder', _msg('user', 'hi'))
    missing = tmp_path / 'no-such-root'
    # No intent: fails before touching the filesystem.
    with pytest.raises(ValueError):
        fixture_origin_info(tmp_path, ref.id, intent=None, actor='coder')  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        fixture_post_room_message(tmp_path, ref, intent='x', actor='coder', text='t')  # type: ignore[arg-type]
    # Not enabled: fails before I/O even with a valid intent.
    with pytest.raises(ValueError, match='not enabled'):
        fixture_post_room_message(tmp_path, ref, intent=_write_intent(), actor='coder', text='t')
    with pytest.raises(ValueError, match='not enabled'):
        fixture_origin_info(tmp_path, ref.id, intent=_read_intent(), actor='coder')
    with pytest.raises(ValueError, match='not enabled'):
        fixture_collect_exchange_rooms(tmp_path, intent=_read_intent(), actor_for=lambda row: 'coder')
    assert not missing.exists()
    # Live root can never be enabled.
    with pytest.raises(ValueError, match='live root'):
        enable_fixture_writes(Path.home() / '.thyca')


# ---------- window projection: DM/group/private-fallback ----------

def test_fixture_window_for_dm_group_and_mismatch(switched: Path) -> None:
    from thyca.conversations.store import create_group

    dm_ref = _dm(switched, 'coder')
    dm_view = AgentStore(switched).read(dm_ref, 'coder').conversation
    window = fixture_window_for(dm_ref, dm_view)
    assert window.window_id == 'coder'
    assert window.channel_id == 'webui'
    assert window.kind == 'dm'
    assert window.visibility == 'private'
    assert window.participants == ('coder',)

    group = create_group(switched, ['coder', 'thyca'], intent=_write_intent(), actor='coder')
    window = fixture_window_for(group.ref, group)
    assert window.kind == 'group'
    assert window.visibility == 'public'
    assert window.participants == ('coder', 'thyca')

    other = create_group(switched, ['coder'], intent=_write_intent(), actor='coder')
    with pytest.raises(ValueError):
        fixture_window_for(group.ref, other)


def test_fixture_private_fallback_matches_synthetic_room_shape(switched: Path) -> None:
    window = fixture_private_fallback('a2a:coder', 'coder')
    assert window.window_id == 'a2a:coder'
    assert window.kind == 'dm'
    assert window.visibility == 'private'
    assert window.participants == ('coder',)
    with pytest.raises(ValueError):
        fixture_private_fallback('', 'coder')
    with pytest.raises(ValueError):
        fixture_private_fallback('room', 'Bad Bot!!')


def test_runtime_window_for_room_on_fixture_root(tmp_path: Path) -> None:
    """The real ``BotRuntime._window_for_room`` over fixture legacy sessions.

    Switch stays off: these are legacy reads, proving the consumer this
    phase inventories without changing it.
    """
    from thyca.bots.bus import Bus
    from thyca.bots.registry import BotRegistry
    from thyca.bots.runtime import BotRuntime
    from thyca.sessions import SessionManager

    runtime = BotRuntime(tmp_path, BotRegistry(tmp_path), Bus())
    assert runtime._window_for_room('2026-10-31T00-00-00_ab12') is None

    manager = SessionManager(tmp_path / 'sessions')
    dm_session = manager.create()
    manager.store.append_dm(dm_session.path, 'coder')
    window = runtime._window_for_room(dm_session.id)
    assert window is not None
    assert window.kind == 'dm'
    assert window.window_id == dm_session.id

    group_session = manager.create()
    manager.store.append_bots(group_session.path, ['coder', 'thyca'])
    window = runtime._window_for_room(group_session.id)
    assert window is not None
    assert window.kind == 'group'
    assert set(window.participants) == {'coder', 'thyca'}


# ---------- origin privacy on fixture roots ----------

def test_fixture_origin_info_dm_group_and_unknown(switched: Path) -> None:
    from thyca.conversations.store import create_group

    dm_ref = _dm(switched, 'coder')
    AgentStore(switched).append(dm_ref, 'coder', _msg('user', 'secret'))
    group = create_group(switched, ['coder', 'thyca'], intent=_write_intent(), actor='coder')
    AgentStore(switched).append(group.ref, 'coder', _msg('user', 'team news'))

    assert fixture_origin_info(
        switched, dm_ref.id, intent=_read_intent(), actor='coder') == (
            True, 'coder', ('coder',))
    assert fixture_origin_info(
        switched, group.ref.id, intent=_read_intent(), actor='coder') == (
            False, None, ('coder', 'thyca'))
    # Unknown window id: unknown origin, caller denies.
    assert fixture_origin_info(
        switched, '2026-10-31T00-00-00_ab12',
        intent=_read_intent(), actor='coder') is None
    # Removed/non-member actor: unknown origin, caller denies.
    assert fixture_origin_info(
        switched, group.ref.id, intent=_read_intent(), actor='mallory') is None
    # Malformed window id: unknown, never a guess.
    assert fixture_origin_info(
        switched, '!!!', intent=_read_intent(), actor='coder') is None
    # DM of another bot is unreadable to this actor.
    assert fixture_origin_info(
        switched, dm_ref.id, intent=_read_intent(), actor='thyca') is None


def test_fixture_origin_info_corrupt_door_raises(switched: Path) -> None:
    import sqlite3

    ref = _dm(switched, 'coder')
    AgentStore(switched).append(ref, 'coder', _msg('user', 'hi'))
    conn = sqlite3.connect(str(switched / 'conversations.db'))
    try:
        conn.execute(
            'UPDATE conversations SET metadata=? WHERE kind=? AND id=?',
            ('{corrupt', ref.kind, ref.id),
        )
        conn.commit()
    finally:
        conn.close()
    # Global scope: a corrupt door is a data problem, never "private".
    with pytest.raises(ConversationStoreError):
        fixture_origin_info(switched, ref.id, intent=_read_intent(), actor='coder')


def test_memory_view_origin_rules_on_fixture_root(tmp_path: Path) -> None:
    """The real ``MemoryView`` privacy gates over fixture legacy sessions."""
    from thyca.core.channel import WindowRef
    from thyca.memory.visibility import MemoryView
    from thyca.sessions import SessionManager

    manager = SessionManager(tmp_path / 'sessions')
    dm_session = manager.create()
    manager.store.append_dm(dm_session.path, 'coder')
    team = manager.create()
    manager.store.append_bots(team.path, ['coder', 'thyca'])
    narrow = manager.create()
    manager.store.append_bots(narrow.path, ['coder'])

    view = MemoryView(
        window=WindowRef(
            window_id=team.id, channel_id='webui', kind='group',
            participants=('coder', 'thyca')),
        bot_id='coder', thyca_dir=tmp_path,
    )
    assert view._origin_info('2026-10-31T00-00-00_ab12') is None
    assert view._origin_info(dm_session.id)[0] is True
    # Group window: another group covering our participants reads fine.
    assert view.can_read_origin(team.id) is True
    # Group window: every DM origin is denied, own DM included.
    assert view.can_read_origin(dm_session.id) is False
    # Group window: a group missing a current participant is denied.
    assert view.can_read_origin(narrow.id) is False

    own_dm_window = WindowRef(
        window_id=dm_session.id, channel_id='webui', kind='dm',
        participants=('coder',))
    private = MemoryView(window=own_dm_window, bot_id='coder', thyca_dir=tmp_path)
    assert private.can_read_origin(dm_session.id) is True
    # Private view: a group origin is readable when this bot participates.
    assert private.can_read_origin(team.id) is True


# ---------- posting writers: DM/group gates + dedupe on fixtures ----------

def test_fixture_post_room_message_gates(switched: Path) -> None:
    from thyca.conversations.store import create_group

    group = create_group(switched, ['coder', 'thyca'], intent=_write_intent(), actor='coder')
    ok = fixture_post_room_message(
        switched, group.ref, intent=_write_intent(), actor='coder',
        text='status update', meta={'pending_id': 'p1', 'kind': 'status',
                                    'proposal_revision': 3})
    assert ok == {'ok': True, 'session_id': group.ref.id}
    rows = AgentStore(switched).read(group.ref, 'coder').messages
    assert rows[-1].role == 'assistant'
    assert rows[-1].meta['bot_id'] == 'coder'
    assert rows[-1].meta['kind'] == 'status'

    # Crash-safe resume: same marker dedupes, no second row.
    dup = fixture_post_room_message(
        switched, group.ref, intent=_write_intent(), actor='coder',
        text='status update', meta={'pending_id': 'p1', 'kind': 'status',
                                    'proposal_revision': 3})
    assert dup == {'ok': True, 'session_id': group.ref.id, 'deduped': True}
    assert len(AgentStore(switched).read(group.ref, 'coder').messages) == len(rows)

    # Another bot's DM refuses; non-participant refuses.
    dm_ref = _dm(switched, 'coder')
    assert fixture_post_room_message(
        switched, dm_ref, intent=_write_intent(), actor='thyca',
        text='snoop')['ok'] is False
    assert fixture_post_room_message(
        switched, group.ref, intent=_write_intent(), actor='mallory',
        text='hijack')['ok'] is False
    # Valid-shaped but missing room reads as unknown, never created.
    ghost = ConversationRef.new_group()
    assert fixture_post_room_message(
        switched, ghost, intent=_write_intent(), actor='coder',
        text='x') == {'ok': False, 'error': f'unknown room: {ghost.id}'}


def test_fixture_post_dm_and_card_gates(switched: Path) -> None:
    from thyca.conversations.store import create_group

    dm_ref = _dm(switched, 'coder')
    ok = fixture_post_dm_message(
        switched, dm_ref, intent=_write_intent(), actor='coder',
        text='private answer')
    assert ok == {'ok': True, 'session_id': dm_ref.id}
    # Another bot's DM window refuses.
    assert fixture_post_dm_message(
        switched, dm_ref, intent=_write_intent(), actor='thyca',
        text='snoop')['ok'] is False
    # A group door is not a DM.
    group = create_group(switched, ['coder'], intent=_write_intent(), actor='coder')
    assert fixture_post_dm_message(
        switched, group.ref, intent=_write_intent(), actor='coder',
        text='x')['ok'] is False
    # Private-question marker dedupes.
    first = fixture_post_dm_message(
        switched, dm_ref, intent=_write_intent(), actor='coder',
        text='follow up?', meta={'kind': 'private_question', 'pfq_marker': 'm1'})
    assert first['ok'] is True
    again = fixture_post_dm_message(
        switched, dm_ref, intent=_write_intent(), actor='coder',
        text='follow up?', meta={'kind': 'private_question', 'pfq_marker': 'm1'})
    assert again == {'ok': True, 'session_id': dm_ref.id, 'deduped': True}

    card = {'type': 'question', 'pending_id': 'pf-aaaabbbbcccc', 'revision': 1}
    posted = fixture_post_card(
        switched, group.ref, intent=_write_intent(), actor='coder',
        card=card, content='need input')
    assert posted == {'ok': True, 'session_id': group.ref.id}
    rows = AgentStore(switched).read(group.ref, 'coder').messages
    assert rows[-1].meta['kind'] == 'card'
    assert rows[-1].meta['card'] == card
    # Cards never land in another bot's DM.
    assert fixture_post_card(
        switched, dm_ref, intent=_write_intent(), actor='thyca',
        card=card)['ok'] is False
    # Non-participant cards refuse on groups.
    assert fixture_post_card(
        switched, group.ref, intent=_write_intent(), actor='mallory',
        card=card)['ok'] is False


def _posting_service(root: Path, sessions, dm_id: str):
    from thyca.app.bot_posting import BotPostingDeps, BotPostingService
    from thyca.bots.card import BotCard
    from thyca.bots.registry import BotRegistry
    from seeded_config import seeded_config

    bots = BotRegistry(root)
    bots.create(BotCard(bot_id='coder', name='coder', role='Coder',
                        description='Codes.', persona='PERSONA-coder'))

    class _Status:
        def note_card_posted(self, *args, **kwargs) -> None:
            return None

    return BotPostingService(BotPostingDeps(
        root=root,
        sessions=sessions,
        bots=bots,
        current_cfg=seeded_config,
        bot_dm=lambda _bot_id: {'id': dm_id},
        agent_status=_Status(),
    ))


def test_bot_posting_service_writers_on_fixture_root(tmp_path: Path) -> None:
    """The real ``BotPostingService`` gates over fixture legacy sessions."""
    from thyca.sessions import SessionManager

    manager = SessionManager(tmp_path / 'sessions')
    dm_session = manager.create()
    manager.store.append_dm(dm_session.path, 'coder')
    group_session = manager.create()
    manager.store.append_bots(group_session.path, ['coder', 'thyca'])
    service = _posting_service(tmp_path, manager, dm_session.id)

    first = service.post_bot_room_message(
        'coder', 'hello', group_session.id,
        meta={'pending_id': 'p1', 'kind': 'status', 'proposal_revision': 1,
              'bot_id': 'coder'})
    assert first == {'ok': True, 'session_id': group_session.id}
    assert service.post_bot_room_message(
        'thyca', 'snoop', dm_session.id)['ok'] is False
    assert service.post_bot_room_message(
        'mallory', 'hijack', group_session.id)['ok'] is False
    dup = service.post_bot_room_message(
        'coder', 'hello', group_session.id,
        meta={'pending_id': 'p1', 'kind': 'status', 'proposal_revision': 1,
              'bot_id': 'coder'})
    assert dup['deduped'] is True

    dm_ok = service.post_bot_dm_message('coder', 'private hi')
    assert dm_ok == {'ok': True, 'session_id': dm_session.id}
    card_ok = service.post_bot_card(
        'coder', {'type': 'question'}, current_room_id=group_session.id)
    assert card_ok == {'ok': True, 'session_id': group_session.id}
    assert service.post_bot_card(
        'thyca', {'type': 'question'},
        current_room_id=dm_session.id)['ok'] is False


def test_webui_channel_adapter_land_on_fixture_root(tmp_path: Path) -> None:
    """The real WebUI channel adapter gates over fixture legacy sessions."""
    from thyca.app.webui_channel import WebUIChannelAdapter
    from thyca.core.channel import ChannelAction
    from thyca.core.protocol import Message as _Message
    from thyca.sessions import SessionManager

    adapter = WebUIChannelAdapter()
    manager = SessionManager(tmp_path / 'sessions')
    dm_session = manager.create()
    manager.store.append_dm(dm_session.path, 'coder')
    group_session = manager.create()
    manager.store.append_bots(group_session.path, ['coder', 'thyca'])

    dm_window = adapter.window_for(manager.store.load(dm_session.id))
    assert (dm_window.kind, dm_window.visibility) == ('dm', 'private')
    group_window = adapter.window_for(manager.store.load(group_session.id))
    assert (group_window.kind, group_window.visibility) == ('group', 'public')
    event = adapter.event_for(manager.store.load(dm_session.id), 'hi')
    assert event.actor.kind == 'user'

    calls: list = []

    class _Ctx:
        sender_id = 'coder'
        room_id = group_session.id

    def _session_for(window_id: str):
        try:
            return manager.store.load(window_id)
        except Exception:
            return None

    outbound_holder: dict = {}

    from thyca.app.webui_channel import WebUIOutboundAdapter
    outbound = WebUIOutboundAdapter(
        dm_post=lambda *a, **k: calls.append(('dm_post', a, k)) or {'ok': True},
        dm_card_post=lambda *a, **k: calls.append(('dm_card', a, k)) or {'ok': True},
        room_post=lambda *a, **k: calls.append(('room_post', a, k)) or {'ok': True},
        card_post=lambda *a, **k: calls.append(('card_post', a, k)) or {'ok': True},
        session_for=_session_for,
    )
    outbound_holder['outbound'] = outbound

    unknown = outbound.land(
        ChannelAction(kind='post', target_window='2026-10-31T00-00-00_ab12',
                      text='x'), ctx=_Ctx())
    assert unknown == {'ok': False, 'error': f'unknown room: 2026-10-31T00-00-00_ab12'}
    # Another bot's DM refuses; non-participant refuses on groups.
    other_dm = manager.create()
    manager.store.append_dm(other_dm.path, 'thyca')

    class _ThycaCtx:
        sender_id = 'coder'
        room_id = other_dm.id

    denied = outbound.land(
        ChannelAction(kind='post', target_window=other_dm.id, text='x'),
        ctx=_ThycaCtx())
    assert denied['ok'] is False
    # A group turn writing into the sender's own DM is the message_user
    # landing path: allowed, and routed to dm_post (no cross-window bar
    # for DMs — the bar covers public rooms only, asserted below).
    mine_from_group = outbound.land(
        ChannelAction(kind='post', target_window=dm_session.id, text='x'),
        ctx=_Ctx())
    assert mine_from_group == {'ok': True}
    assert calls[-1][0] == 'dm_post'
    # Cross-window public action refuses.
    other_group = manager.create()
    manager.store.append_bots(other_group.path, ['coder'])
    cross = outbound.land(
        ChannelAction(kind='post', target_window=other_group.id, text='x'),
        ctx=_Ctx())
    assert cross['ok'] is False
    # Own-DM post routes to dm_post; group post routes to room_post.
    mine = outbound.land(
        ChannelAction(kind='post', target_window=group_session.id, text='team'),
        ctx=_Ctx())
    assert mine == {'ok': True}
    assert calls[-1][0] == 'room_post'

    class _DmCtx:
        sender_id = 'coder'
        room_id = dm_session.id

    mine_dm = outbound.land(
        ChannelAction(kind='post', target_window=dm_session.id, text='priv'),
        ctx=_DmCtx())
    assert mine_dm == {'ok': True}
    assert calls[-1][0] == 'dm_post'
    ask = outbound.land(
        ChannelAction(kind='ask_private', target_window=dm_session.id, text='q?'),
        ctx=_DmCtx())
    assert ask == {'ok': True}
    choice = outbound.land(
        ChannelAction(kind='ask_choice', target_window=group_session.id,
                      text='pick', card={'type': 'question'}),
        ctx=_Ctx())
    assert choice == {'ok': True}
    assert calls[-1][0] == 'card_post'


def test_cli_oneshot_routes_through_chat_turn_on_fixture(tmp_path: Path) -> None:
    """The CLI channel adapts through ``ChatApp.turn`` on a fixture root."""
    from seeded_config import seeded_config
    from thyca.app.cli import Cli
    from thyca.config import save
    from thyca.llm.llm_base import ChatReply

    save(seeded_config(), tmp_path / 'config.json')

    class _FakeConnect:
        async def chat(self, messages, tools=None):
            return ChatReply(content='fixture reply', model='m',
                             usage={'prompt_tokens': 2, 'completion_tokens': 1,
                                    'total_tokens': 3})

    out, err = StringIO(), StringIO()
    cli = Cli(thyca_dir=tmp_path, connect=_FakeConnect(),
              stdin=StringIO(''), stdout=out, stderr=err)
    assert cli.main(['-p', 'hello']) == 0
    assert 'fixture reply' in out.getvalue()


# ---------- A2A recording + exchange pairing on fixtures ----------

def test_fixture_bus_envelope_dm_privacy_and_group_record(switched: Path) -> None:
    from thyca.conversations.store import create_group

    group = create_group(switched, ['coder', 'thyca'], intent=_write_intent(), actor='coder')
    dm_ref = _dm(switched, 'coder')

    recorded = fixture_record_bus_envelope(
        switched, group.ref, intent=_write_intent(), actor='coder',
        sender='coder', recipient='thyca', kind='delegate',
        payload='please review', trace_id='t1', span_id='s1',
        refs=('r1', 'r2'), truncated=True)
    assert recorded['ok'] is True and recorded['recorded'] is True
    row = AgentStore(switched).read(group.ref, 'coder').messages[-1]
    assert row.meta['bus_event'] is True
    assert row.meta['recipient'] == 'thyca'
    assert row.meta['refs'] == ['r1', 'r2']
    assert row.meta['truncated'] is True

    # Third-party tap into a DM records nowhere (privacy preserved).
    skipped = fixture_record_bus_envelope(
        switched, dm_ref, intent=_write_intent(), actor='coder',
        sender='mallory', recipient='thyca', kind='message',
        payload='tap', trace_id='t9', span_id='s9')
    assert skipped == {'ok': True, 'session_id': dm_ref.id, 'recorded': False}
    assert len(AgentStore(switched).read(dm_ref, 'coder').messages) == 0

    # DM owner pair records into the DM transcript.
    owned = fixture_record_bus_envelope(
        switched, dm_ref, intent=_write_intent(), actor='coder',
        sender='coder', recipient='thyca', kind='message',
        payload='dm work', trace_id='t2', span_id='s2',
        parent_span_id='s0')
    assert owned['recorded'] is True


def test_fixture_exchange_pairing_over_store_history(switched: Path) -> None:
    from thyca.bots.exchanges import pair_thread, partners_for
    from thyca.conversations.store import create_group

    group = create_group(switched, ['coder', 'thyca'], intent=_write_intent(), actor='coder')
    fixture_record_bus_envelope(
        switched, group.ref, intent=_write_intent(), actor='coder',
        sender='coder', recipient='thyca', kind='delegate',
        payload='review this', trace_id='t1', span_id='bus-1')
    # The recipient authors the result row (real A2A recording stamps the
    # reply with the recipient's own bot id, parented to the envelope).
    fixture_post_room_message(
        switched, group.ref, intent=_write_intent(), actor='thyca',
        text='done', meta={'kind': 'result', 'trace_id': 't1',
                           'parent_span_id': 'bus-1', 'span_id': 'res-1'})
    # Unrelated pair in another room never surfaces for coder/thyca.
    other = create_group(switched, ['mallory', 'thyca'], intent=_write_intent(), actor='mallory')
    fixture_record_bus_envelope(
        switched, other.ref, intent=_write_intent(), actor='mallory',
        sender='mallory', recipient='thyca', kind='message',
        payload='other work', trace_id='t2', span_id='bus-2')

    rooms = fixture_collect_exchange_rooms(
        switched, intent=_read_intent(),
        actor_for=lambda listing: listing.participants[0])
    assert len(rooms) == 2
    partners = partners_for('coder', rooms)
    assert partners == [{'partner': 'thyca', 'count': 2,
                         'last_ts': partners[0]['last_ts']}]
    thread = pair_thread('coder', 'thyca', rooms)
    assert [row['kind'] for row in thread] == ['delegate', 'result']
    assert [row['sender'] for row in thread] == ['coder', 'coder']
    assert thread[1]['author'] == 'thyca'
    assert partners_for('nobody', rooms) == []
    with pytest.raises(ValueError):
        fixture_collect_exchange_rooms(
            switched, intent=_read_intent(), actor_for='not-callable')  # type: ignore[arg-type]


def test_a2a_record_bus_events_on_fixture_root(tmp_path: Path) -> None:
    """The real ``A2ADeliveryService.record_bus_events`` on fixture rooms."""
    from thyca.app.a2a_delivery import A2ADeliveryDeps, A2ADeliveryService
    from thyca.app.webui_channel import WebUIChannelAdapter
    from thyca.bots.bus import Bus
    from thyca.bots.card import BotCard
    from thyca.bots.registry import BotRegistry
    from thyca.bots.runtime import BotRuntime
    from thyca.sessions import SessionManager

    manager = SessionManager(tmp_path / 'sessions')
    group_session = manager.create()
    manager.store.append_bots(group_session.path, ['coder', 'thyca'])
    bots = BotRegistry(tmp_path)
    bots.create(BotCard(bot_id='coder', name='coder', role='Coder',
                        description='Codes.', persona='PERSONA-coder'))
    bus = Bus()
    bus.register('coder')
    bus.register('thyca')
    bus.send(sender='coder', recipient='thyca', kind='delegate',
             session_id=group_session.id, payload='review this',
             trace_id='t1', span_id='bus-1')
    service = A2ADeliveryService(A2ADeliveryDeps(
        root=tmp_path,
        sessions_store=manager.store,
        window_for=WebUIChannelAdapter().window_for,
        bots=bots,
        bus=bus,
        runtime=BotRuntime(tmp_path, bots, bus),
        injected_connect=None,
        display_meta=lambda _bot_id: {},
        room_dm_owner=lambda _sid: None,
        room_delta_for=lambda *a: ('', None),
        room_bots=lambda _sid: ['coder', 'thyca'],
        set_room_bots=lambda *a: [],
    ))
    room = SessionManager(tmp_path / 'sessions')
    room.load(group_session.id)
    service.record_bus_events(room, group_session.id)
    rows = room.current.messages
    assert rows[-1].meta['bus_event'] is True
    assert rows[-1].meta['recipient'] == 'thyca'
    # Drained exactly once: a second record adds nothing.
    service.record_bus_events(room, group_session.id)
    assert len(room.current.messages) == len(rows)


# ---------- per-bot settings on fixture roots ----------

def test_fixture_bot_settings_roundtrip_and_per_bot_resolution(switched: Path) -> None:
    from thyca.app.turn_options import resolve_bot_cfg
    from thyca.bots.card import BotCard, BotModel
    from thyca.bots.settings import SettingsError
    from seeded_config import seeded_config

    (switched / 'bots' / 'coder').mkdir(parents=True)
    assert fixture_read_bot_settings(switched, 'coder', intent=_read_intent()) is None

    stored = fixture_write_bot_settings(
        switched, 'coder', BotModel(id='default', thinking=None),
        intent=_write_intent())
    assert stored == BotModel(id='default', thinking=None)
    assert fixture_read_bot_settings(
        switched, 'coder', intent=_read_intent()) == stored

    # Per-bot resolution: the saved settings select the turn model.
    card = BotCard(bot_id='coder', name='coder', role='Coder',
                   description='Codes.', persona='PERSONA-coder',
                   model=fixture_read_bot_settings(
                       switched, 'coder', intent=_read_intent()))
    cfg = resolve_bot_cfg(seeded_config(), card)
    assert cfg.defaultModel == seeded_config().defaultModel

    # Corrupt settings never silently fall back.
    (switched / 'bots' / 'coder' / 'settings.json').write_text(
        '{"model": {"id": 1}}', encoding='utf-8')
    with pytest.raises(SettingsError):
        fixture_read_bot_settings(switched, 'coder', intent=_read_intent())
    # Saving settings for a missing bot is an error, never a creation.
    with pytest.raises(SettingsError):
        fixture_write_bot_settings(
            switched, 'ghost', BotModel(id='default', thinking=None),
            intent=_write_intent())


# ---------- memory write-through on fixture roots ----------

def test_fixture_memory_write_through_and_owner_visibility(switched: Path) -> None:
    from thyca.conversations.store import create_group
    from thyca.memory.facade import MemoryFacade
    from thyca.memory.identity import bind_bot_id, reset_bot_id

    from datetime import datetime, timedelta, timezone

    group = create_group(switched, ['coder', 'thyca'], intent=_write_intent(), actor='coder')
    past = datetime.now(timezone.utc) - timedelta(days=2)
    entry_id = fixture_memory_remember(
        switched, intent=_write_intent(), actor='coder',
        topic='Fixture routing', summary='proof notes alpha zulu',
        content='details stay on the fixture root', chat=group.ref.id,
        timezone_name='Asia/Ho_Chi_Minh', now=past)
    assert isinstance(entry_id, str) and entry_id
    # A second write with the current clock archives the earlier day so
    # the search index (which always skips today) picks it up.
    fixture_memory_remember(
        switched, intent=_write_intent(), actor='coder',
        topic='nudge', summary='index nudge',
        timezone_name='Asia/Ho_Chi_Minh')

    facade = MemoryFacade(switched, timezone_name='Asia/Ho_Chi_Minh')
    token = bind_bot_id('coder')
    try:
        hits = facade.search('zulu', limit=5)
    finally:
        reset_bot_id(token)
    assert any('Fixture routing' in hit.heading for hit in hits.hits)
    assert hits.hits[0].bots == ('coder',)

    # Another bot cannot read coder-owned entries.
    token = bind_bot_id('mallory')
    try:
        other = facade.search('zulu', limit=5)
    finally:
        reset_bot_id(token)
    assert all('Fixture routing' not in hit.heading for hit in other.hits)


# ---------- canonical execution survives store compaction ----------

def test_fixture_canonical_survives_store_compaction(switched: Path) -> None:
    from thyca.bots.timeline import WorkspaceTimeline
    from thyca.conversations.store import create_group

    group = create_group(switched, ['coder', 'thyca'], intent=_write_intent(), actor='coder')
    agents = AgentStore(switched)
    agents.append(group.ref, 'coder', _msg('user', 'm-0'))
    agents.append(group.ref, 'coder', _msg('assistant', 'm-1'))
    agents.append(group.ref, 'coder', _msg('user', 'm-2'))

    window = fixture_window_for(
        group.ref, agents.read(group.ref, 'coder').conversation)
    timeline = WorkspaceTimeline(switched, 'coder')
    timeline.append_message(
        window, _msg('assistant', 'canonical work',
                     meta={'span_id': 'span-1', 'bot_id': 'coder'}),
        event_id='ev-1')
    before = fixture_canonical_messages(
        switched, 'coder', window, intent=_read_intent())
    assert [m.content for m in before] == ['canonical work']

    AdminStore(switched).compact_through_seq(group.ref, 2, lambda _old: 'summary')
    compacted = agents.read(group.ref, 'coder').messages
    assert [m.content for m in compacted] == ['summary', 'm-2']
    # The canonical journal is untouched by the public-door compaction.
    after = fixture_canonical_messages(
        switched, 'coder', window, intent=_read_intent())
    assert [m.content for m in after] == ['canonical work']

    # A missing timeline is unavailable, never an empty success.
    with pytest.raises(FileNotFoundError):
        fixture_canonical_messages(
            switched, 'ghost', window, intent=_read_intent())


# ---------- private follow-ups + observe/compaction on fixture roots ----------

def test_private_followup_lifecycle_on_fixture_root(tmp_path: Path) -> None:
    from thyca.bots.private_followups import PrivateFollowupStore
    from thyca.core.conversation import ConversationRef

    # Hard-cut door ids: a group origin and the bot's own DM window.
    origin_id = ConversationRef.new_group().id
    dm_window_id = 'coder'
    store = PrivateFollowupStore(tmp_path)
    pending = store.create('coder', work_id='task-aabb1122',
                           origin_window=origin_id, dm_window=dm_window_id,
                           question='share the log?',
                           idempotency_key='key-1')
    assert store.active('coder').pending_id == pending.pending_id
    store.mark_waiting('coder', pending.pending_id)
    store.receive('coder', pending.pending_id, answer_event_id='ev-1',
                  answer_ref=f'{dm_window_id}:3', idempotency_key='key-2')
    store.decide('coder', pending.pending_id, action='resume',
                 redacted='redacted log', idempotency_key='key-3')
    store.approve('coder', pending.pending_id, revision=1,
                  idempotency_key='approve-1')
    resumed = store.finalize_resumed('coder', pending.pending_id,
                                     posted_marker='approve-1')
    assert resumed.status == 'resumed'
    assert store.active('coder') is None

    # Second pending: user declines sharing, then cancels.
    second = store.create('coder', work_id='task-bbcc2233',
                          origin_window=origin_id, dm_window=dm_window_id,
                          question='share this too?')
    store.mark_waiting('coder', second.pending_id)
    store.receive('coder', second.pending_id, answer_event_id='ev-9',
                  answer_ref=f'{dm_window_id}:5')
    store.keep_private('coder', second.pending_id)
    assert store.active('coder').requested_outcome == 'ready'
    assert store.cancel('coder', second.pending_id).status == 'cancelled'
    assert store.active('coder') is None


def test_observe_and_compaction_plan_on_fixture_history(
        switched: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    """``Observe`` + compaction planning over fixture-assembled history."""
    from thyca.agent.observe import Observe
    from thyca.conversations.store import assemble_history
    from thyca.sessions import SessionManager

    ref = _dm(switched, 'coder')
    agents = AgentStore(switched)
    agents.append(ref, 'coder', _msg('user', 'q1'))
    agents.append(ref, 'coder', _msg('assistant', 'a1'))
    conversation = assemble_history(switched, ref, 'coder', intent=_read_intent())

    # Legacy sessions live on a separate disposable root: the switched
    # root blocks every legacy writer while the fixture route is enabled.
    legacy_root = tmp_path_factory.mktemp('phase4-legacy-observe')
    manager = SessionManager(legacy_root / 'sessions')
    manager.create()
    for item in conversation.messages:
        manager.append(Message(role=item.role, content=item.content or ''))
    assert Observe(manager).compact() is False
    assert [m.content for m in manager.current.messages] == ['q1', 'a1']
    plan = manager.compactor.plan(
        list(manager.current.messages),
        manager.limits.contextTokens if hasattr(manager, 'limits') else 200000,
    )
    assert plan is None


def test_public_store_exports_bot_workspace_api() -> None:
    import thyca.conversations.store as store

    for name in ('fixture_window_for', 'fixture_private_fallback',
                 'fixture_origin_info', 'fixture_post_room_message',
                 'fixture_post_dm_message', 'fixture_post_card',
                 'fixture_record_bus_envelope',
                 'fixture_collect_exchange_rooms',
                 'fixture_read_bot_settings', 'fixture_write_bot_settings',
                 'fixture_memory_remember', 'fixture_canonical_messages'):
        assert callable(getattr(store, name)), name
