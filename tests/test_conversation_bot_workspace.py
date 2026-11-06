"""Bot/workspace/A2A/memory consumers over runtime store roots.

No live server, no live ``~/.thyca``, no live bot/DM creation or
deletion. Every store write goes through an initialized runtime store
root under ``tmp_path``; the real consumers (BotRuntime, MemoryView,
BotPostingService, WebUI adapters, CLI, A2A) run unchanged.
"""
from __future__ import annotations

import uuid
from io import StringIO
from pathlib import Path

import pytest

from thyca.conversations.store import (
    AdminStore,
    AgentStore,
    DMBootstrapIntent,
    RootInitializationIntent,
    RuntimeReadIntent,
    RuntimeWriteIntent,
)
from thyca.core.conversation import ConversationRef
from thyca.core.protocol import Message
from thyca.conversations.runtime import SessionManager


def _root_id(root: Path) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, str(root.absolute())))


def _init(root: Path) -> None:
    AdminStore(root).initialize(RootInitializationIntent(_root_id(root)))


def _dm(root: Path, bot: str) -> ConversationRef:
    attempt = uuid.uuid5(uuid.NAMESPACE_DNS, f'{root.absolute()}:{bot}').hex
    return AdminStore(root).bootstrap_initial_dm(
        bot, intent=DMBootstrapIntent(bot, attempt)).ref


def _read_intent() -> RuntimeReadIntent:
    return RuntimeReadIntent('runtime-bots')


def _write_intent() -> RuntimeWriteIntent:
    return RuntimeWriteIntent('runtime-bots')


def _msg(role: str, text: str, meta: dict | None = None) -> Message:
    return Message(role=role, content=text, meta=meta)


@pytest.fixture
def switched(tmp_path: Path):
    _init(tmp_path)
    yield tmp_path


# Build a session manager over an initialized runtime root.
def _manager(root: Path) -> SessionManager:
    from thyca.conversations.store.runtime import ensure_runtime_store

    ensure_runtime_store(root)
    return SessionManager(root / 'sessions')


# Create one group session with the given participants.
def _group(manager: SessionManager, bots: list[str]):
    session = manager.create()
    manager.store.append_bots(session.path, bots)
    return manager.load(session.id)


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



def test_runtime_window_for_room_on_runtime_root(tmp_path: Path) -> None:
    """The real ``BotRuntime._window_for_room`` over runtime store sessions.

    Switch stays off: these are legacy reads, proving the consumer this
    phase inventories without changing it.
    """
    from thyca.bots.bus import Bus
    from thyca.bots.registry import BotRegistry
    from thyca.bots.runtime import BotRuntime
    from thyca.conversations.runtime import SessionManager

    manager = _manager(tmp_path)
    runtime = BotRuntime(tmp_path, BotRegistry(tmp_path), Bus())
    assert runtime._window_for_room('2026-10-31T00-00-00_ab12') is None

    dm_session = manager.ensure_dm('coder')
    window = runtime._window_for_room(dm_session.id)
    assert window is not None
    assert window.kind == 'dm'
    assert window.window_id == dm_session.id

    group_session = _group(manager, ['coder', 'thyca'])
    window = runtime._window_for_room(group_session.id)
    assert window is not None
    assert window.kind == 'group'
    assert set(window.participants) == {'coder', 'thyca'}



def test_memory_view_origin_rules_on_runtime_root(tmp_path: Path) -> None:
    """The real ``MemoryView`` privacy gates over runtime store sessions."""
    from thyca.core.channel import WindowRef
    from thyca.memories.visibility import MemoryView
    from thyca.conversations.runtime import SessionManager

    manager = _manager(tmp_path)
    dm_session = manager.ensure_dm('coder')
    team = _group(manager, ['coder', 'thyca'])
    narrow = _group(manager, ['coder'])

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



def test_bot_posting_service_writers_on_runtime_root(tmp_path: Path) -> None:
    """The real ``BotPostingService`` gates over runtime store sessions."""
    from thyca.conversations.runtime import SessionManager

    manager = _manager(tmp_path)
    dm_session = manager.ensure_dm('coder')
    group_session = _group(manager, ['coder', 'thyca'])
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



def test_webui_channel_adapter_land_on_runtime_root(tmp_path: Path) -> None:
    """The real WebUI channel adapter gates over runtime store sessions."""
    from thyca.app.webui_channel import WebUIChannelAdapter
    from thyca.core.channel import ChannelAction
    from thyca.core.protocol import Message as _Message
    from thyca.conversations.runtime import SessionManager

    adapter = WebUIChannelAdapter()
    manager = _manager(tmp_path)
    dm_session = manager.ensure_dm('coder')
    group_session = _group(manager, ['coder', 'thyca'])

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
    other_dm = manager.ensure_dm('thyca')

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
    other_group = _group(manager, ['coder'])
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



def test_cli_oneshot_routes_through_chat_turn_on_runtime_root(tmp_path: Path) -> None:
    """The CLI channel adapts through ``ChatApp.turn`` on a fixture root."""
    from seeded_config import seeded_config
    from thyca.app.cli import Cli
    from thyca.configs import save
    from thyca.llms.llm_base import ChatReply

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



def test_a2a_record_bus_events_on_runtime_root(tmp_path: Path) -> None:
    """The real ``A2ADeliveryService.record_bus_events`` on runtime rooms."""
    from thyca.app.a2a_delivery import A2ADeliveryDeps, A2ADeliveryService
    from thyca.app.webui_channel import WebUIChannelAdapter
    from thyca.bots.bus import Bus
    from thyca.bots.card import BotCard
    from thyca.bots.registry import BotRegistry
    from thyca.bots.runtime import BotRuntime
    from thyca.conversations.runtime import SessionManager

    manager = _manager(tmp_path)
    group_session = _group(manager, ['coder', 'thyca'])
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



def test_private_followup_lifecycle_on_runtime_root(tmp_path: Path) -> None:
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



def test_observe_and_compaction_plan_on_runtime_history(
        switched: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    """``Observe`` + compaction planning over store-assembled history."""
    from thyca.agents.observe import Observe
    from thyca.conversations.store import assemble_history
    from thyca.conversations.runtime import SessionManager

    ref = _dm(switched, 'coder')
    agents = AgentStore(switched)
    agents.append(ref, 'coder', _msg('user', 'q1'))
    agents.append(ref, 'coder', _msg('assistant', 'a1'))
    conversation = assemble_history(switched, ref, 'coder', intent=_read_intent())

    # Runtime sessions live on a separate initialized root.
    legacy_root = tmp_path_factory.mktemp('runtime-observe')
    manager = _manager(legacy_root)
    manager.create()
    for item in conversation.messages:
        manager.append(Message(role=item.role, content=item.content or ''), actor='thyca')
    assert Observe(manager).compact() is False
    assert [m.content for m in manager.current.messages] == ['q1', 'a1']
    plan = manager.compactor.plan(
        list(manager.current.messages),
        manager.limits.contextTokens if hasattr(manager, 'limits') else 200000,
    )
    assert plan is None
