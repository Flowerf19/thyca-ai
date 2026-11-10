"""Runtime write route: delete/retry/error/metadata/resync/fail-closed.

Runtime roots only. No live data, no commit. Each test initializes the
runtime store for its own ``tmp_path``.
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
    RuntimeWriteIntent,
    append_message,
    append_turn_error,
    create_group,
    prepare_retry,
    set_conversation_metadata,
)
from thyca.conversations.store import write_adapter
from thyca.core.conversation import ConversationRef
from thyca.core.protocol import Message
from thyca.conversations.runtime.store import ConversationStore


def _root_id(root: Path) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, str(root.absolute())))


def _init(root: Path) -> None:
    AdminStore(root).initialize(RootInitializationIntent(_root_id(root)))


def _dm(root: Path, bot: str) -> ConversationRef:
    attempt = uuid.uuid5(uuid.NAMESPACE_DNS, f'{root.absolute()}:{bot}').hex
    return AdminStore(root).bootstrap_initial_dm(
        bot, intent=DMBootstrapIntent(bot, attempt)).ref


def _intent() -> RuntimeWriteIntent:
    return RuntimeWriteIntent('runtime-write')


def _msg(role: str, text: str) -> Message:
    return Message(role=role, content=text)


@pytest.fixture
def switched(tmp_path: Path):
    _init(tmp_path)
    yield tmp_path


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

def test_write_requires_intent_and_initialized_root_before_io(tmp_path: Path) -> None:
    _init(tmp_path)
    ref = _dm(tmp_path, 'coder')
    missing = tmp_path / 'missing-root'
    # No intent: fails before touching the filesystem.
    with pytest.raises(ValueError):
        append_message(tmp_path, ref, 'coder', _msg('user', 'hi'), intent=None)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        prepare_retry(tmp_path, ref, intent='x', actor='coder')  # type: ignore[arg-type]
    # Uninitialized root: fails before I/O even with a valid intent.
    with pytest.raises(ValueError, match='not initialized'):
        append_message(missing, ref, 'coder', _msg('user', 'hi'), intent=_intent())
    assert not missing.exists()


# Check that a wrong bot is rejected while the second member writes as itself.
def test_group_actor_is_acting_bot_not_first_participant(switched: Path) -> None:
    from thyca.conversations.store.contracts import ConversationStoreError
    from thyca.conversations.store.read_adapter import assemble_history
    from thyca.conversations.store.runtime import RuntimeReadIntent

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
        intent=RuntimeReadIntent('actor-regression'))
    assert [m.content for m in conv.messages] == ['bob speaking']
    assert conv.messages[0].meta['bot_id'] == 'bob'


def test_explicit_actor_chain_has_no_fallback(switched: Path) -> None:
    # No current_bot_id import or call fallback anywhere in the write path.
    # (Docstrings may name the forbidden fallback; code must not use it.)
    for module in (write_adapter,):
        source = inspect.getsource(module)
        assert 'from thyca.memories.identity' not in source
        assert 'import current_bot_id' not in source
        assert 'current_bot_id(' not in source
    import thyca.conversations.store.admin as admin_module
    admin_source = inspect.getsource(admin_module)
    assert 'from thyca.memories.identity' not in admin_source
    assert 'current_bot_id(' not in admin_source
    # Every write entry point requires an explicit actor with no default.
    for fn in (append_message, set_conversation_metadata,
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


# ---------- explicit delete ----------

def test_delete_conversation_removes_door_atomically(switched: Path) -> None:
    admin = AdminStore(switched)
    group = admin.create_group(['alice', 'bob']).ref
    agents = AgentStore(switched)
    agents.append(group, 'alice', _msg('user', 'g1'))
    agents.append(group, 'bob', _msg('assistant', 'g2'))

    AdminStore(switched).delete(group)
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
        AdminStore(switched).delete(group)


def test_delete_dm_keeps_marker_tombstone_against_reuse(switched: Path) -> None:
    ref = _dm(switched, 'coder')
    AgentStore(switched).append(ref, 'coder', _msg('user', 'keep? no - whole door goes'))
    marker = switched / 'dm-markers' / 'coder.json'
    assert marker.is_file()

    AdminStore(switched).delete(ref)
    assert marker.is_file(), 'DM marker must survive as a reuse-blocking tombstone'
    with pytest.raises(ConversationStoreError):
        AgentStore(switched).read(ref, 'coder')
    # Same bot id cannot silently mint a replacement DM.
    with pytest.raises(ConversationStoreError, match='already known|unavailable'):
        AdminStore(switched).bootstrap_initial_dm(
            'coder', intent=DMBootstrapIntent('coder', 'a' * 32))


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
        participants=['alice', 'bob'],
    )
    assert view.participants == ('alice', 'bob')
    assert [m.content for m in view.messages] == ['hello']
    # Messages untouched by the metadata-only write.
    assert _snapshot(switched, group)[1] == before_msgs

    # Failed validation changes nothing.
    before = _snapshot(switched, group)
    with pytest.raises(ValueError):
        set_conversation_metadata(
            switched, group, intent=_intent(), actor='alice',
            participants=[],
        )
    with pytest.raises(ValueError):
        set_conversation_metadata(
            switched, group, intent=_intent(), actor='alice',
            participants=['alice', 'mallory with spaces !!'],
        )
    assert _snapshot(switched, group) == before
    # Removed actor cannot authorize its own stale write.
    set_conversation_metadata(
        switched, group, intent=_intent(), actor='alice',
        participants=['alice'],
    )
    with pytest.raises(ConversationStoreError):
        set_conversation_metadata(
            switched, group, intent=_intent(), actor='bob',
            participants=['alice'],
        )
    assert AgentStore(switched).read(group, 'alice').messages[0].content == 'hello'


# ---------- fail closed on never-initialized roots ----------

def test_public_conversations_fail_closed_without_runtime_store(tmp_path: Path) -> None:
    from thyca.conversations.runtime import ConversationError
    from thyca.conversations.runtime import ConversationManager

    conversations_dir = tmp_path / 'sessions'
    store = ConversationStore(conversations_dir)
    with pytest.raises(ConversationError, match='runtime store not initialized'):
        store.create('group-' + 'a' * 32)
    with pytest.raises(ConversationError, match='runtime store not initialized'):
        store.load('coder')
    with pytest.raises(ConversationError, match='runtime store not initialized'):
        store.list_paths()
    with pytest.raises(ConversationError, match='runtime store not initialized'):
        ConversationManager(conversations_dir).create()
    with pytest.raises(ConversationError, match='runtime store not initialized'):
        ConversationManager(conversations_dir).ensure_dm('coder')
    # Nothing was created on the legacy file path.
    assert not list(tmp_path.rglob('*.jsonl'))


def test_write_adapter_refuses_uninitialized_root(tmp_path: Path) -> None:
    ref = ConversationRef.dm('coder')
    with pytest.raises(ValueError, match='not initialized'):
        append_message(tmp_path, ref, 'coder', _msg('user', 'nope'), intent=_intent())
    assert not (tmp_path / 'conversations.db').exists()
