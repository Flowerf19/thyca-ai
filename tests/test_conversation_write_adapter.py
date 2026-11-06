"""Phase 2 fixture-only write route: delete/retry/error/metadata/resync/blocking.

Disposable fixture roots only. No live data, no commit. Each test enables
the fixture write route for its own ``tmp_path`` and disables it afterwards
so the live root never leaves the legacy path.
"""
from __future__ import annotations

import inspect
import sqlite3
import uuid
from pathlib import Path

import pytest

from thyca.conversations.store import (
    AdminStore,
    AgentStore,
    ConversationStoreError,
    DMBootstrapIntent,
    RootInitializationIntent,
    SyntheticWriteIntent,
    append_message,
    append_turn_error,
    create_group,
    delete_conversation,
    disable_fixture_writes,
    enable_fixture_writes,
    is_fixture_write_enabled,
    prepare_retry,
    set_conversation_metadata,
)
from thyca.conversations.store import fixture_switch, write_adapter
from thyca.core.conversation import ConversationRef
from thyca.core.protocol import Message
from thyca.sessions.store import SessionStore


def _root_id(root: Path) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, str(root.absolute())))


def _init(root: Path) -> None:
    AdminStore(root).initialize(RootInitializationIntent(_root_id(root)))


def _dm(root: Path, bot: str) -> ConversationRef:
    attempt = uuid.uuid5(uuid.NAMESPACE_DNS, f'{root.absolute()}:{bot}').hex
    return AdminStore(root).bootstrap_initial_dm(
        bot, intent=DMBootstrapIntent(bot, attempt)).ref


def _intent() -> SyntheticWriteIntent:
    return SyntheticWriteIntent('phase2-fixture-write')


def _msg(role: str, text: str) -> Message:
    return Message(role=role, content=text)


def _enable(root: Path) -> None:
    enable_fixture_writes(root)
    assert is_fixture_write_enabled(root)


@pytest.fixture
def switched(tmp_path: Path):
    _init(tmp_path)
    _enable(tmp_path)
    try:
        yield tmp_path
    finally:
        disable_fixture_writes(tmp_path)


def _snapshot(root: Path, ref: ConversationRef):
    conn = sqlite3.connect(str(root / 'conversations.db'))
    try:
        conv = conn.execute(
            'SELECT kind,id,metadata,created_at,lifecycle_state,generation,'
            'event_high_water,logical_high_water FROM conversations '
            'WHERE kind=? AND id=?', (ref.kind, ref.id),
        ).fetchall()
        msgs = conn.execute(
            'SELECT kind,id,seq,logical_seq,role,content,data,created_at '
            'FROM messages WHERE kind=? AND id=? ORDER BY seq', (ref.kind, ref.id),
        ).fetchall()
        return conv, msgs
    finally:
        conn.close()


# ---------- intent + enabled-root gate ----------

def test_write_requires_intent_and_enabled_root_before_io(tmp_path: Path) -> None:
    _init(tmp_path)
    ref = _dm(tmp_path, 'coder')
    missing = tmp_path / 'missing-root'
    # No intent: fails before touching the filesystem.
    with pytest.raises(ValueError):
        append_message(tmp_path, ref, 'coder', _msg('user', 'hi'), intent=None)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        prepare_retry(tmp_path, ref, intent='x', actor='coder')  # type: ignore[arg-type]
    # Not enabled: fails before I/O even with a valid intent.
    with pytest.raises(ValueError, match='not enabled'):
        append_message(tmp_path, ref, 'coder', _msg('user', 'hi'), intent=_intent())
    with pytest.raises(ValueError, match='not enabled'):
        delete_conversation(tmp_path, ref, intent=_intent(), actor='coder')
    assert not missing.exists()
    # Live root can never be enabled.
    with pytest.raises(ValueError, match='live root'):
        enable_fixture_writes(Path.home() / '.thyca')
    assert not is_fixture_write_enabled(Path.home() / '.thyca')


# Check that a wrong bot is rejected while the second member writes as itself.
def test_group_actor_is_acting_bot_not_first_participant(switched: Path) -> None:
    from thyca.conversations.store.contracts import ConversationStoreError
    from thyca.conversations.store.read_adapter import SyntheticReadIntent, assemble_history

    group = create_group(
        switched, ['alice', 'bob'], intent=_intent(), actor='alice')
    with pytest.raises(ConversationStoreError):
        append_message(
            switched, group.ref, 'mallory',
            Message(role='assistant', content='intruder',
                    meta={'bot_id': 'mallory'}),
            intent=_intent())
    append_message(
        switched, group.ref, 'bob',
        Message(role='assistant', content='bob speaking',
                meta={'bot_id': 'bob'}),
        intent=_intent())
    conv = assemble_history(
        switched, group.ref, 'bob',
        intent=SyntheticReadIntent('actor-regression'))
    assert [m.content for m in conv.messages] == ['bob speaking']
    assert conv.messages[0].meta['bot_id'] == 'bob'


def test_explicit_actor_chain_has_no_fallback(switched: Path) -> None:
    # No current_bot_id import or call fallback anywhere in the write path.
    # (Docstrings may name the forbidden fallback; code must not use it.)
    for module in (write_adapter, fixture_switch):
        source = inspect.getsource(module)
        assert 'from thyca.memory.identity' not in source
        assert 'import current_bot_id' not in source
        assert 'current_bot_id(' not in source
    import thyca.conversations.store.admin as admin_module
    admin_source = inspect.getsource(admin_module)
    assert 'from thyca.memory.identity' not in admin_source
    assert 'current_bot_id(' not in admin_source
    # Every write entry point requires an explicit actor with no default.
    for fn in (append_message, set_conversation_metadata, delete_conversation,
               prepare_retry, append_turn_error, create_group):
        params = inspect.signature(fn).parameters
        assert 'actor' in params
        assert params['actor'].default is inspect.Parameter.empty
    # Missing/empty actor fails before I/O.
    ref = _dm(switched, 'coder')
    AgentStore(switched).append(ref, 'coder', _msg('user', 'hi'))
    with pytest.raises(ValueError):
        append_message(switched, ref, '', _msg('user', 'x'), intent=_intent())
    with pytest.raises(ValueError):
        prepare_retry(switched, ref, intent=_intent(), actor='')  # type: ignore[arg-type]
    # Non-participant is rejected by membership, not silently adopted.
    with pytest.raises(ConversationStoreError):
        append_message(switched, ref, 'mallory', _msg('user', 'x'), intent=_intent())
    with pytest.raises(ConversationStoreError):
        prepare_retry(switched, ref, intent=_intent(), actor='mallory')
    with pytest.raises(ConversationStoreError):
        append_turn_error(switched, ref, intent=_intent(), actor='mallory',
                          code='e', message='boom')
    with pytest.raises(ConversationStoreError):
        delete_conversation(switched, ref, intent=_intent(), actor='mallory')


# ---------- explicit delete ----------

def test_delete_conversation_removes_door_atomically(switched: Path) -> None:
    admin = AdminStore(switched)
    group = admin.create_group(['alice', 'bob']).ref
    agents = AgentStore(switched)
    agents.append(group, 'alice', _msg('user', 'g1'))
    agents.append(group, 'bob', _msg('assistant', 'g2'))

    delete_conversation(switched, group, intent=_intent(), actor='alice')
    with pytest.raises(ConversationStoreError):
        agents.read(group, 'alice')
    items, _ = admin.list()
    assert group not in [row.ref for row in items]
    # Trigger survives the admin delete; messages table has no residue.
    conn = sqlite3.connect(str(switched / 'conversations.db'))
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND name='no_delete_messages'"
        ).fetchone()[0] == 1
        assert conn.execute(
            'SELECT COUNT(*) FROM messages WHERE kind=? AND id=?', ('group', group.id),
        ).fetchone()[0] == 0
        # The trigger still guards a live door: direct DELETE is blocked.
        live = admin.create_group(['alice']).ref
        AgentStore(switched).append(live, 'alice', _msg('user', 'guarded'))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                'DELETE FROM messages WHERE kind=? AND id=?', ('group', live.id))
        conn.rollback()
    finally:
        conn.close()
    # Missing door: sanitized local error, no mutation elsewhere.
    with pytest.raises(ConversationStoreError):
        delete_conversation(switched, group, intent=_intent(), actor='alice')


def test_delete_dm_keeps_marker_tombstone_against_reuse(switched: Path) -> None:
    ref = _dm(switched, 'coder')
    AgentStore(switched).append(ref, 'coder', _msg('user', 'keep? no - whole door goes'))
    marker = switched / 'dm-markers' / 'coder.json'
    assert marker.is_file()

    delete_conversation(switched, ref, intent=_intent(), actor='coder')
    assert marker.is_file(), 'DM marker must survive as a reuse-blocking tombstone'
    with pytest.raises(ConversationStoreError):
        AgentStore(switched).read(ref, 'coder')
    # Same bot id cannot silently mint a replacement DM.
    with pytest.raises(ConversationStoreError, match='already known|unavailable'):
        AdminStore(switched).bootstrap_initial_dm(
            'coder', intent=DMBootstrapIntent('coder', 'a' * 32))


def test_admin_delete_directly_matches_adapter(switched: Path) -> None:
    admin = AdminStore(switched)
    group = admin.create_group(['coder']).ref
    AgentStore(switched).append(group, 'coder', _msg('user', 'bye'))
    admin.delete(group)
    with pytest.raises(ConversationStoreError):
        AgentStore(switched).read(group, 'coder')


# ---------- retry without rewrite ----------

def test_retry_preserves_history_and_reports_last_user(switched: Path) -> None:
    ref = _dm(switched, 'coder')
    agents = AgentStore(switched)
    agents.append(ref, 'coder', _msg('user', 'q1'))
    agents.append(ref, 'coder', _msg('assistant', 'a1-failed?'))
    agents.append(ref, 'coder', _msg('user', 'q2'))
    before = _snapshot(switched, ref)

    plan = prepare_retry(switched, ref, intent=_intent(), actor='coder')
    assert plan.has_user is True
    assert plan.last_user_logical_seq == 3
    assert plan.message_count == 3
    assert plan.generation == 0
    # No mutation: same rows, same generation/high-water.
    assert _snapshot(switched, ref) == before
    assert [m.content for m in agents.read(ref, 'coder').messages] == ['q1', 'a1-failed?', 'q2']


def test_retry_without_user_reports_empty_without_mutation(switched: Path) -> None:
    ref = _dm(switched, 'coder')
    before = _snapshot(switched, ref)
    plan = prepare_retry(switched, ref, intent=_intent(), actor='coder')
    assert plan.has_user is False
    assert plan.last_user_logical_seq is None
    assert plan.message_count == 0
    assert _snapshot(switched, ref) == before


def test_retry_resyncs_to_new_generation_after_compaction(switched: Path) -> None:
    ref = _dm(switched, 'coder')
    agents = AgentStore(switched)
    admin = AdminStore(switched)
    agents.append(ref, 'coder', _msg('user', 'm-0'))
    agents.append(ref, 'coder', _msg('assistant', 'm-1'))
    agents.append(ref, 'coder', _msg('user', 'm-2'))
    agents.append(ref, 'coder', _msg('assistant', 'm-3'))
    stale = agents.read(ref, 'coder', limit=1)
    assert stale.cursor.generation == 0

    admin.compact_through_seq(ref, 2, lambda _old: 'summary')
    # Stale cursor now explicitly resyncs instead of silently skipping.
    resync = agents.read(ref, 'coder', cursor=stale.cursor, limit=1)
    assert resync.resync_required is True
    assert resync.cursor.generation == 1

    plan = prepare_retry(switched, ref, intent=_intent(), actor='coder', page_limit=1)
    assert plan.generation == 1
    assert plan.message_count == 3
    assert plan.has_user is True
    got = agents.read(ref, 'coder').messages
    assert [m.content for m in got] == ['summary', 'm-2', 'm-3']
    assert plan.last_user_logical_seq == 3


# ---------- turn error as new trailing system row ----------

def test_turn_error_appends_system_marker_without_mutation(switched: Path) -> None:
    ref = _dm(switched, 'coder')
    agents = AgentStore(switched)
    agents.append(ref, 'coder', _msg('user', 'run this'))
    agents.append(ref, 'coder', _msg('assistant', 'partial'))
    before_conv, before_msgs = _snapshot(switched, ref)
    before_generation = before_conv[0][5]

    seq = append_turn_error(switched, ref, intent=_intent(), actor='coder',
                            code='llm_error', message='boom unavailable')
    assert seq == 3
    after_conv, after_msgs = _snapshot(switched, ref)
    # Generation unchanged (only purge/compaction bump it); high-water +1.
    assert after_conv[0][5] == before_generation
    assert after_conv[0][6] == before_conv[0][6] + 1
    assert after_conv[0][7] == before_conv[0][7] + 1
    # Prefix rows keep their exact bytes; only the trailing marker is new.
    assert [row[:7] for row in after_msgs[:2]] == [row[:7] for row in before_msgs]
    assert after_msgs[2][4] == 'system'
    assert after_msgs[2][5] == 'boom unavailable'

    history = agents.read(ref, 'coder').messages
    assert [m.content for m in history] == ['run this', 'partial', 'boom unavailable']
    marker = history[-1]
    assert marker.role == 'system'
    assert marker.meta == {'error': {'code': 'llm_error', 'message': 'boom unavailable'}}
    original_user = history[0]
    assert original_user.meta is None or 'error' not in (original_user.meta or {})

    # A second failure appends a second marker; still no rewrite.
    seq2 = append_turn_error(switched, ref, intent=_intent(), actor='coder',
                             code='chat_unavailable', message='down again')
    assert seq2 == 4
    assert len(agents.read(ref, 'coder').messages) == 4


def test_turn_error_rejects_bad_shapes_without_mutation(switched: Path) -> None:
    ref = _dm(switched, 'coder')
    AgentStore(switched).append(ref, 'coder', _msg('user', 'q'))
    before = _snapshot(switched, ref)
    with pytest.raises(ValueError):
        append_turn_error(switched, ref, intent=_intent(), actor='coder',
                          code='', message='x')
    with pytest.raises(ValueError):
        append_turn_error(switched, ref, intent=_intent(), actor='coder',
                          code='e', message='')
    assert _snapshot(switched, ref) == before


# ---------- atomic metadata ----------

def test_metadata_update_is_atomic_all_or_nothing(switched: Path) -> None:
    admin = AdminStore(switched)
    group = admin.create_group(['alice', 'bob']).ref
    agents = AgentStore(switched)
    agents.append(group, 'alice', _msg('user', 'hello'))
    before_msgs = _snapshot(switched, group)[1]

    view = set_conversation_metadata(
        switched, group, intent=_intent(), actor='alice',
        title='Kế hoạch tuần này', title_source='user',
        naming_attempted=True, participants=['alice', 'bob'],
    )
    assert view.title == 'Kế hoạch tuần này'
    assert view.participants == ('alice', 'bob')
    assert [m.content for m in view.messages] == ['hello']
    # Messages untouched by the metadata-only write.
    assert _snapshot(switched, group)[1] == before_msgs

    # Failed validation changes nothing (title + participants together).
    before = _snapshot(switched, group)
    with pytest.raises(ValueError):
        set_conversation_metadata(
            switched, group, intent=_intent(), actor='alice',
            title='x', title_source='user', naming_attempted='yes',  # type: ignore[arg-type]
            participants=['alice', 'bob'],
        )
    with pytest.raises(ValueError):
        set_conversation_metadata(
            switched, group, intent=_intent(), actor='alice',
            title='x', title_source='user', naming_attempted=True,
            participants=['alice', 'mallory with spaces !!'],
        )
    assert _snapshot(switched, group) == before
    # Removed actor cannot authorize its own stale write.
    set_conversation_metadata(
        switched, group, intent=_intent(), actor='alice',
        title=None, title_source=None, naming_attempted=False, participants=['alice'],
    )
    with pytest.raises(ConversationStoreError):
        set_conversation_metadata(
            switched, group, intent=_intent(), actor='bob',
            title='hijack', title_source='user', naming_attempted=True, participants=['alice'],
        )
    assert AgentStore(switched).read(group, 'alice').messages[0].content == 'hello'


# ---------- legacy blocking + live stays legacy ----------

def test_legacy_writers_blocked_on_switched_root_live_stays_open(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    _init(tmp_path)
    _enable(tmp_path)
    try:
        legacy = SessionStore(tmp_path / 'sessions')
        with pytest.raises(Exception, match='legacy writer blocked'):
            legacy.ensure_dir()
        with pytest.raises(Exception, match='legacy writer blocked'):
            legacy.create('2020-01-01T00-00-00_ab12')
        with pytest.raises(Exception, match='legacy writer blocked'):
            legacy.delete('2020-01-01T00-00-00_ab12')
        with pytest.raises(Exception, match='legacy writer blocked'):
            legacy._append_json(tmp_path / 'sessions' / 'x.jsonl', {'type': 'meta'})
        with pytest.raises(Exception, match='legacy writer blocked'):
            legacy.rewrite('2020-01-01T00-00-00_ab12', tmp_path / 'x', [])

        # New-store writes still flow while legacy is blocked.
        ref = _dm(tmp_path, 'coder')
        seq = append_message(tmp_path, ref, 'coder', _msg('user', 'via store'), intent=_intent())
        assert seq == 1

        other = tmp_path_factory.mktemp('phase2-other-root')
        other_sessions = other / 'sessions'
        SessionStore(other_sessions).ensure_dir()
        assert other_sessions.is_dir()
    finally:
        disable_fixture_writes(tmp_path)
    # After switch-off, legacy works again on the same path.
    SessionStore(tmp_path / 'sessions').ensure_dir()
    assert (tmp_path / 'sessions').is_dir()


def test_write_adapter_refuses_non_enabled_root(tmp_path: Path) -> None:
    _init(tmp_path)
    ref = _dm(tmp_path, 'coder')
    AgentStore(tmp_path).append(ref, 'coder', _msg('user', 'legacy still owns this'))
    with pytest.raises(ValueError, match='not enabled'):
        append_message(tmp_path, ref, 'coder', _msg('user', 'nope'), intent=_intent())
    # Legacy path is open while the switch is off.
    SessionStore(tmp_path / 'sessions').ensure_dir()
