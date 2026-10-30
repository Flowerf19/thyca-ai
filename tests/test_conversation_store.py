"""SQLite conversation store: security boundaries and admin lifecycle (synthetic roots only)."""
from __future__ import annotations

import concurrent.futures
import inspect
import json
import multiprocessing
import os
import sqlite3
import stat
import time
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest

from thyca.conversations.store import (
    AdminStore,
    AgentStore,
    ConversationStoreError,
    DMBootstrapIntent,
    PageCursor,
    RootInitializationIntent,
)
from thyca.core.conversation import ConversationRef
from thyca.core.protocol import Message


def _admin(root: Path) -> AdminStore:
    return AdminStore(root)


def _root_instance_id(root: Path) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, str(root.absolute())))


def _initialize(root: Path, root_instance_id: str | None = None):
    instance_id = root_instance_id or _root_instance_id(root)
    return _admin(root).initialize(RootInitializationIntent(instance_id))


def _bootstrap(root: Path, bot: str):
    attempt_id = uuid.uuid5(uuid.NAMESPACE_DNS, f'{root.absolute()}:{bot}').hex
    return _admin(root).bootstrap_initial_dm(
        bot, intent=DMBootstrapIntent(bot, attempt_id)
    )


def _setup_dm(root: Path, bot: str = 'coder') -> ConversationRef:
    _initialize(root)
    _bootstrap(root, bot)
    return ConversationRef.dm(bot)


def test_public_store_package_exports_expected_api() -> None:
    from thyca.conversations import store as package
    from thyca.conversations.store import (
        AgentStore,
        AdminStore,
        BootstrapResult,
        BUSY_TIMEOUT_MS,
        Conversation,
        ConversationPage,
        ConversationStoreError,
        DB_FILE_NAME,
        DEFAULT_PAGE_LIMIT,
        DMBootstrapIntent,
        InitializationResult,
        MAX_CONTENT_LEN,
        MAX_JSON_DEPTH,
        MAX_METADATA_LEN,
        MAX_PAGE_LIMIT,
        PageCursor,
        RootInitializationIntent,
        SCHEMA_VERSION,
        validate_message,
        validate_metadata,
    )

    expected = {
        'AgentStore': AgentStore,
        'AdminStore': AdminStore,
        'ConversationStoreError': ConversationStoreError,
        'RootInitializationIntent': RootInitializationIntent,
        'DMBootstrapIntent': DMBootstrapIntent,
        'InitializationResult': InitializationResult,
        'BootstrapResult': BootstrapResult,
        'Conversation': Conversation,
        'ConversationPage': ConversationPage,
        'PageCursor': PageCursor,
        'validate_metadata': validate_metadata,
        'validate_message': validate_message,
        'DB_FILE_NAME': DB_FILE_NAME,
        'BUSY_TIMEOUT_MS': BUSY_TIMEOUT_MS,
        'SCHEMA_VERSION': SCHEMA_VERSION,
        'MAX_CONTENT_LEN': MAX_CONTENT_LEN,
        'MAX_METADATA_LEN': MAX_METADATA_LEN,
        'MAX_JSON_DEPTH': MAX_JSON_DEPTH,
        'DEFAULT_PAGE_LIMIT': DEFAULT_PAGE_LIMIT,
        'MAX_PAGE_LIMIT': MAX_PAGE_LIMIT,
    }
    assert set(package.__all__) == set(expected)
    assert all(getattr(package, name) is value for name, value in expected.items())


def _deep_meta() -> dict:
    from thyca.conversations.store import contracts as mod
    from thyca.core.protocol import META_CAP_BYTES

    meta: dict = {}
    cursor = meta
    for _ in range(mod.MAX_JSON_DEPTH + 1):
        child: dict = {}
        cursor['nested'] = child
        cursor = child
    assert len(json.dumps(meta, ensure_ascii=False).encode('utf-8')) < META_CAP_BYTES
    return meta


def _deep_tuple_meta() -> dict:
    from thyca.conversations.store import contracts as mod
    from thyca.core.protocol import META_CAP_BYTES

    nested: object = 'x'
    for _ in range(mod.MAX_JSON_DEPTH + 1):
        nested = (nested,)
    meta = {'nested': nested}
    assert len(json.dumps(meta, ensure_ascii=False).encode('utf-8')) < META_CAP_BYTES
    return meta


def _append_many(root: str, kind: str, cid: str, actor: str, start: int, count: int) -> str:
    store = AgentStore(Path(root))
    ref = ConversationRef(kind, cid)  # type: ignore[arg-type]
    for i in range(start, start + count):
        store.append(ref, actor, Message(role='user', content=f'msg-{i}'))
    return 'ok'


def _init_only(arguments: tuple[str, str]) -> str:
    root, root_instance_id = arguments
    result = AdminStore(Path(root)).initialize(RootInitializationIntent(root_instance_id))
    return result.status


def _crasher(root: str) -> None:
    """Insert without commit, then die: rollback must discard the row."""
    conn = sqlite3.connect(str(Path(root) / 'conversations.db'), timeout=5.0)
    conn.execute('BEGIN IMMEDIATE')
    conn.execute(
        "INSERT INTO messages(kind,id,seq,logical_seq,role,content,data,created_at)"
        " VALUES('dm','coder',999,999,'user','partial','{}','2000-01-01T00:00:00Z')")
    os._exit(1)


# ---------- agent/admin separation ----------

def test_agent_facade_exposes_no_admin_api(tmp_path: Path) -> None:
    for name in ('create_group', 'set_metadata', 'bootstrap_initial_dm',
                 'purge_before', 'compact_through_seq', 'vacuum', 'agent'):
        assert not hasattr(AgentStore, name), name
    assert hasattr(AgentStore, 'read') and hasattr(AgentStore, 'append')
    for name in ('update', 'delete', 'remove', 'execute', 'executemany', 'executescript'):
        assert not hasattr(AgentStore, name), name


def test_triggers_block_direct_update_delete(tmp_path: Path) -> None:
    ref = _setup_dm(tmp_path)
    AgentStore(tmp_path).append(ref, 'coder', Message(role='user', content='keep'))
    conn = sqlite3.connect(str(tmp_path / 'conversations.db'))
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE messages SET content='hacked' WHERE kind='dm' AND id='coder'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM messages WHERE kind='dm' AND id='coder'")
        conn.rollback()
        assert conn.execute('SELECT content FROM messages').fetchone()[0] == 'keep'
    finally:
        conn.close()


# ---------- actor membership ----------

def test_cross_dm_read_write_blocked_with_generic_error(tmp_path: Path) -> None:
    _setup_dm(tmp_path, 'alice')
    _setup_dm(tmp_path, 'bob')
    alice, bob = ConversationRef.dm('alice'), ConversationRef.dm('bob')
    AgentStore(tmp_path).append(alice, 'alice', Message(role='user', content='private'))
    for op in (lambda: AgentStore(tmp_path).read(alice, 'bob'),
               lambda: AgentStore(tmp_path).append(alice, 'bob', Message(role='user', content='x')),
               lambda: AgentStore(tmp_path).read(bob, 'alice')):
        with pytest.raises(ConversationStoreError, match='unavailable'):
            op()
    # Same generic message as a truly missing door: existence is not leaked.
    try:
        AgentStore(tmp_path).read(ConversationRef.dm('ghost'), 'ghost')
    except ConversationStoreError as exc:
        assert str(exc) == 'conversation unavailable'


def test_group_membership_and_former_member_blocked(tmp_path: Path) -> None:
    _initialize(tmp_path)
    admin = _admin(tmp_path)
    group = admin.create_group(['alice', 'bob'])
    agents = AgentStore(tmp_path)
    agents.append(group.ref, 'alice', Message(role='user', content='hi'))
    with pytest.raises(ConversationStoreError, match='unavailable'):
        agents.append(group.ref, 'mallory', Message(role='user', content='x'))
    with pytest.raises(ConversationStoreError, match='unavailable'):
        agents.read(group.ref, 'mallory')
    # Removing bob revokes both read and write.
    admin.set_metadata(group.ref, title=None, title_source=None,
                       naming_attempted=False, participants=['alice'])
    with pytest.raises(ConversationStoreError, match='unavailable'):
        agents.append(group.ref, 'bob', Message(role='user', content='late'))
    with pytest.raises(ConversationStoreError, match='unavailable'):
        agents.read(group.ref, 'bob')
    assert [m.content for m in agents.read(group.ref, 'alice').messages] == ['hi']
    with pytest.raises(ValueError):
        agents.read(group.ref, 'not a bot id!!')


def test_repeated_dm_bootstrap_intent_is_idempotent(tmp_path: Path) -> None:
    admin = _admin(tmp_path)
    _initialize(tmp_path)
    intent = DMBootstrapIntent('alice', 'a' * 32)

    first = admin.bootstrap_initial_dm('alice', intent=intent)
    assert first.status == 'initialized'
    ref = first.ref
    AgentStore(tmp_path).append(ref, 'alice', Message(role='user', content='keep this turn'))

    marker_path = tmp_path / 'dm-markers' / 'alice.json'
    markers_before = {path.name: path.read_bytes() for path in marker_path.parent.iterdir()}
    conn = sqlite3.connect(str(tmp_path / 'conversations.db'))
    try:
        before = (
            tuple(conn.execute(
                'SELECT kind,id,metadata,created_at,lifecycle_state,generation,'
                'event_high_water,logical_high_water FROM conversations ORDER BY kind,id'
            ).fetchall()),
            tuple(conn.execute(
                'SELECT kind,id,seq,logical_seq,role,content,data,created_at '
                'FROM messages ORDER BY kind,id,seq'
            ).fetchall()),
        )
    finally:
        conn.close()
    content_before = AgentStore(tmp_path).read(ref, 'alice').messages

    second = admin.bootstrap_initial_dm('alice', intent=intent)

    assert second.status == 'already_initialized'
    assert second.ref == ref
    assert {path.name: path.read_bytes() for path in marker_path.parent.iterdir()} == markers_before
    conn = sqlite3.connect(str(tmp_path / 'conversations.db'))
    try:
        after = (
            tuple(conn.execute(
                'SELECT kind,id,metadata,created_at,lifecycle_state,generation,'
                'event_high_water,logical_high_water FROM conversations ORDER BY kind,id'
            ).fetchall()),
            tuple(conn.execute(
                'SELECT kind,id,seq,logical_seq,role,content,data,created_at '
                'FROM messages ORDER BY kind,id,seq'
            ).fetchall()),
        )
    finally:
        conn.close()
    assert after == before
    assert AgentStore(tmp_path).read(ref, 'alice').messages == content_before


# ---------- concurrent init ----------

def test_concurrent_init_16_processes_cold_root(tmp_path: Path) -> None:
    root = tmp_path / 'cold-root'
    root_instance_id = str(uuid.uuid4())
    ctx = multiprocessing.get_context('spawn')
    with concurrent.futures.ProcessPoolExecutor(max_workers=16, mp_context=ctx) as pool:
        statuses = list(pool.map(_init_only,
                                 [(str(root), root_instance_id)] * 16))
    assert statuses.count('initialized') == 1
    assert statuses.count('already_initialized') == 15
    conn = sqlite3.connect(str(root / 'conversations.db'))
    try:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 2
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall()
    finally:
        conn.close()


# ---------- file permissions / symlinks ----------

def test_root_and_db_permissions_and_symlink_refusal(tmp_path: Path) -> None:
    root = tmp_path / 'vault'
    ref = _setup_dm(root)
    AgentStore(root).append(ref, 'coder', Message(role='user', content='x'))
    assert stat.S_IMODE(os.stat(root).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(root / 'conversations.db').st_mode) == 0o600
    linkdir = tmp_path / 'linked'
    linkdir.symlink_to(root)
    with pytest.raises(ConversationStoreError):
        AgentStore(linkdir).read(ref, 'coder')
    # Symlinked DB file itself is refused.
    other = tmp_path / 'vault2'
    other.mkdir()
    (other / 'conversations.db').symlink_to(root / 'conversations.db')
    with pytest.raises(ConversationStoreError):
        AgentStore(other).read(ref, 'coder')


def test_hardlinked_database_alias_is_refused_without_exposing_messages(tmp_path: Path) -> None:
    original_root = tmp_path / 'original'
    alias_root = tmp_path / 'alias'
    ref = _setup_dm(original_root, 'alice')
    AgentStore(original_root).append(ref, 'alice', Message(role='user', content='private fake'))
    alias_root.mkdir(mode=0o700)
    os.link(original_root / 'conversations.db', alias_root / 'conversations.db')

    conversation = None
    error = None
    try:
        conversation = AgentStore(alias_root).read(ref, 'alice')
    except Exception as exc:
        error = exc
    assert isinstance(error, ConversationStoreError) and conversation is None, (
        f'hardlink alias returned data={conversation is not None}, '
        f'error={type(error).__name__ if error else "none"}'
    )


# ---------- bad data ----------

def test_oversized_and_bad_typed_content_rejected(tmp_path: Path) -> None:
    from thyca.conversations.store import contracts as mod

    ref = _setup_dm(tmp_path)
    agents = AgentStore(tmp_path)
    with pytest.raises(ValueError):
        agents.append(ref, 'coder', Message(role='user', content='x' * (mod.MAX_CONTENT_LEN + 1)))
    with pytest.raises(ValueError):
        agents.append(ref, 'coder', Message(role='user', content=None))  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        agents.append(ref, 'coder', Message(role='user', content='a\x00b'))
    with pytest.raises(ValueError):
        agents.append(ref, 'coder', Message(role='user', content='a\ud800b'))
    assert agents.read(ref, 'coder').messages == ()


def test_bad_metadata_shapes_and_sizes_rejected(tmp_path: Path) -> None:
    admin = _admin(tmp_path)
    with pytest.raises(ValueError):
        admin.create_group(['coder'], title='x' * 100_000)
    ref = _setup_dm(tmp_path)
    with pytest.raises(ValueError):
        admin.set_metadata(ref, title='T', title_source='user',
                           naming_attempted='yes', participants=['coder'])  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        admin.set_metadata(ConversationRef.dm('coder'), title='T', title_source='user',
                           naming_attempted=False, participants=['mallory'])
    with pytest.raises(ConversationStoreError):
        admin.set_metadata(ConversationRef.dm('ghost'), title='T', title_source=None,
                           naming_attempted=False, participants=['ghost'])


@pytest.mark.parametrize('meta_factory', [_deep_meta, _deep_tuple_meta], ids=['dict', 'tuple'])
def test_deep_message_metadata_is_rejected_without_creating_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, meta_factory: Callable[[], dict],
) -> None:
    from thyca.conversations.store import agent as agent_module

    root = tmp_path / 'missing-root'
    message = Message(role='user', content='safe text', meta=meta_factory())
    open_calls = []
    real_open_db = agent_module.open_existing_session

    def track_open_db(*args, **kwargs):
        open_calls.append((args, kwargs))
        return real_open_db(*args, **kwargs)

    monkeypatch.setattr(agent_module, 'open_existing_session', track_open_db)
    error = None
    try:
        AgentStore(root).append(ConversationRef.dm('coder'), 'coder', message)
    except Exception as exc:
        error = exc
    assert isinstance(error, ValueError) and not open_calls and not root.exists(), (
        f'expected pre-I/O ValueError; got {type(error).__name__ if error else "success"}, '
        f'open_calls={len(open_calls)}, root_exists={root.exists()}'
    )


def test_open_session_factory_seams_intercept_valid_operations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from thyca.conversations.store import admin as admin_module
    from thyca.conversations.store import agent as agent_module

    ref = _setup_dm(tmp_path)
    agent_calls = []
    admin_calls = []
    real_agent_open = agent_module.open_existing_session
    real_admin_open = admin_module.open_existing_session

    def track_agent_open(*args, **kwargs):
        agent_calls.append((args, kwargs))
        return real_agent_open(*args, **kwargs)

    def track_admin_open(*args, **kwargs):
        admin_calls.append((args, kwargs))
        return real_admin_open(*args, **kwargs)

    monkeypatch.setattr(agent_module, 'open_existing_session', track_agent_open)
    monkeypatch.setattr(admin_module, 'open_existing_session', track_admin_open)
    AgentStore(tmp_path).append(ref, 'coder', Message(role='user', content='seam check'))
    group = _admin(tmp_path).create_group(['coder'])

    assert len(agent_calls) == 1 and len(admin_calls) == 1
    assert group.ref.kind == 'group'


@pytest.mark.parametrize('meta_factory', [_deep_meta, _deep_tuple_meta], ids=['dict', 'tuple'])
def test_deep_message_metadata_cannot_be_written_then_read_as_corrupt(
    tmp_path: Path, meta_factory: Callable[[], dict],
) -> None:
    ref = _setup_dm(tmp_path)
    agents = AgentStore(tmp_path)
    agents.append(ref, 'coder', Message(role='user', content='keep'))
    append_error = None
    try:
        agents.append(ref, 'coder', Message(role='user', content='deep', meta=meta_factory()))
    except Exception as exc:
        append_error = exc
    read_error = None
    messages = ()
    try:
        messages = agents.read(ref, 'coder').messages
    except Exception as exc:
        read_error = exc
    assert isinstance(append_error, ValueError) and read_error is None and [m.content for m in messages] == ['keep'], (
        f'append_error={type(append_error).__name__ if append_error else "success"}, '
        f'read_error={type(read_error).__name__ if read_error else "none"}, '
        f'messages={[m.content for m in messages]!r}'
    )


def test_corrupt_rows_raise_wrapped_errors(tmp_path: Path) -> None:
    ref = _setup_dm(tmp_path)
    AgentStore(tmp_path).append(ref, 'coder', Message(role='user', content='ok'))
    # Direct UPDATE is blocked by the append-only trigger; simulate on-disk
    # corruption the way an out-of-band writer could leave it (triggers live
    # in the same file, so drop-then-rewrite models post-crash residue).
    conn = sqlite3.connect(str(tmp_path / 'conversations.db'))
    try:
        conn.execute('DROP TRIGGER no_update_messages')
        conn.execute("UPDATE conversations SET metadata='not json' WHERE id='coder'")
        conn.execute("UPDATE messages SET data='[1,2' WHERE id='coder'")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(ConversationStoreError):
        AgentStore(tmp_path).read(ref, 'coder')


def test_crash_mid_transaction_leaves_no_partial_row(tmp_path: Path) -> None:
    ref = _setup_dm(tmp_path)
    AgentStore(tmp_path).append(ref, 'coder', Message(role='user', content='first'))

    ctx = multiprocessing.get_context('spawn')
    proc = ctx.Process(target=_crasher, args=(str(tmp_path),))
    proc.start()
    proc.join(15)
    assert proc.exitcode == 1
    seq = AgentStore(tmp_path).append(ref, 'coder', Message(role='user', content='second'))
    assert seq == 2
    assert [m.content for m in AgentStore(tmp_path).read(ref, 'coder').messages] == ['first', 'second']


# ---------- pagination ----------

def test_read_pagination_uses_bound_logical_cursor(tmp_path: Path) -> None:
    ref = _setup_dm(tmp_path)
    agents = AgentStore(tmp_path)
    for i in range(10):
        agents.append(ref, 'coder', Message(role='user', content=f'msg-{i}'))
    page1 = agents.read(ref, 'coder', limit=4)
    assert [m.content for m in page1.messages] == ['msg-0', 'msg-1', 'msg-2', 'msg-3']
    assert page1.cursor == PageCursor(ref, 0, 4) and page1.has_more
    page2 = agents.read(ref, 'coder', cursor=page1.cursor, limit=4)
    assert [m.content for m in page2.messages] == ['msg-4', 'msg-5', 'msg-6', 'msg-7']
    assert page2.cursor == PageCursor(ref, 0, 8) and page2.has_more
    with pytest.raises(ValueError):
        PageCursor(ref, True, 0)
    with pytest.raises(ValueError):
        PageCursor(ref, 0, False)
    with pytest.raises(ValueError):
        agents.read(ref, 'coder', cursor=PageCursor(ConversationRef.dm('other'), 0, 0))
    with pytest.raises(ValueError):
        agents.read(ref, 'coder', limit=True)
    with pytest.raises(ValueError):
        agents.read(ref, 'coder', limit=0)
    empty = agents.read(ref, 'coder', cursor=PageCursor(ref, 0, 10))
    assert not empty.messages and empty.cursor == PageCursor(ref, 0, 10)


# ---------- retention / compaction ----------

@pytest.mark.parametrize('cutoff', ['z', '9999-02-30T00:00:00Z'])
def test_invalid_purge_cutoff_is_rejected_before_creating_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cutoff: str,
) -> None:
    from thyca.conversations.store import admin as admin_module

    root = tmp_path / 'missing-root'
    open_calls = []
    real_open_db = admin_module.open_existing_session

    def track_open_db(*args, **kwargs):
        open_calls.append((args, kwargs))
        return real_open_db(*args, **kwargs)

    monkeypatch.setattr(admin_module, 'open_existing_session', track_open_db)
    error = None
    try:
        AdminStore(root).purge_before(cutoff)
    except Exception as exc:
        error = exc
    assert isinstance(error, ValueError) and not open_calls and not root.exists(), (
        f'cutoff={cutoff!r}, got={type(error).__name__ if error else "success"}, '
        f'open_calls={len(open_calls)}, root_exists={root.exists()}'
    )


@pytest.mark.parametrize('cutoff', ['z', '9999-02-30T00:00:00Z'])
def test_invalid_purge_cutoff_does_not_delete_messages(tmp_path: Path, cutoff: str) -> None:
    ref = _setup_dm(tmp_path)
    agents = AgentStore(tmp_path)
    agents.append(ref, 'coder', Message(role='user', content='keep'))
    error = None
    try:
        _admin(tmp_path).purge_before(cutoff)
    except Exception as exc:
        error = exc
    messages = agents.read(ref, 'coder').messages
    assert isinstance(error, ValueError) and [m.content for m in messages] == ['keep'], (
        f'cutoff={cutoff!r}, got={type(error).__name__ if error else "success"}, '
        f'messages={[m.content for m in messages]!r}'
    )


def test_purge_before_cutoff_and_vacuum(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from thyca.conversations.store import agent as agent_module

    admin = _admin(tmp_path)
    ref = _setup_dm(tmp_path)
    agents = AgentStore(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(agent_module, 'utc_now_ts', lambda: '2000-01-01T00:00:00Z')
        agents.append(ref, 'coder', Message(role='user', content='old'))
    agents.append(ref, 'coder', Message(role='user', content='new'))
    removed = admin.purge_before('2020-01-01T00:00:00Z')
    assert removed == 1
    assert [m.content for m in agents.read(ref, 'coder').messages] == ['new']
    # Triggers survive the admin purge.
    conn = sqlite3.connect(str(tmp_path / 'conversations.db'))
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM messages")
        conn.rollback()
    finally:
        conn.close()
    admin.vacuum()
    assert [m.content for m in agents.read(ref, 'coder').messages] == ['new']


def test_compact_summarizer_runs_outside_transaction(tmp_path: Path) -> None:
    admin = _admin(tmp_path)
    ref = _setup_dm(tmp_path)
    agents = AgentStore(tmp_path)
    for i in range(6):
        agents.append(ref, 'coder', Message(role='user', content=f'msg-{i}'))
    proof: dict = {}

    def summarize(old: tuple) -> str:
        # If compact held the write lock now, this racing append would block
        # until summarize returns. It must finish first instead.
        done: dict = {}

        def racing() -> None:
            AgentStore(tmp_path).append(ref, 'coder', Message(role='user', content='racing'))
            done['ok'] = True

        import threading
        thread = threading.Thread(target=racing)
        thread.start()
        thread.join(5)
        proof['raced'] = done.get('ok', False)
        return f'summary of {len(old)} messages'

    summary = admin.compact_through_seq(ref, 4, summarize)
    assert proof['raced'] is True
    assert summary.role == 'system' and 'summary of 4 messages' in (summary.content or '')
    got = agents.read(ref, 'coder')
    assert [m.content for m in got.messages] == [summary.content, 'msg-4', 'msg-5', 'racing']
    # seq stays contiguous and unique; new appends keep working.
    conn = sqlite3.connect(str(tmp_path / 'conversations.db'))
    try:
        seqs = sorted(r[0] for r in conn.execute('SELECT seq FROM messages'))
    finally:
        conn.close()
    assert seqs == list(range(min(seqs), max(seqs) + 1))
    agents.append(ref, 'coder', Message(role='user', content='after'))
    assert len(agents.read(ref, 'coder').messages) == len(seqs) + 1
    empty = _bootstrap(tmp_path, 'quiet')
    called = []
    with pytest.raises(ValueError, match='high-water'):
        admin.compact_through_seq(empty.ref, 10, lambda old: called.append(old) or 'nothing there')
    assert called == []


# ---------- transaction discipline ----------

def test_writer_lock_timeout_is_sanitized_and_does_not_change_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from thyca.conversations.store import database as database_module

    ref = _setup_dm(tmp_path)
    agents = AgentStore(tmp_path)
    agents.append(ref, 'coder', Message(role='user', content='keep'))
    lock = sqlite3.connect(str(tmp_path / 'conversations.db'), isolation_level=None)
    lock.execute('BEGIN IMMEDIATE')
    monkeypatch.setattr(database_module, 'BUSY_TIMEOUT_MS', 40)
    error = None
    try:
        try:
            agents.append(ref, 'coder', Message(role='user', content='blocked'))
        except Exception as exc:
            error = exc
    finally:
        lock.execute('ROLLBACK')
        lock.close()
    messages = agents.read(ref, 'coder').messages
    assert (type(error) is ConversationStoreError and str(error) == 'conversation unavailable'
            and [m.content for m in messages] == ['keep']), (
        f'expected sanitized error with unchanged data; got '
        f'{type(error).__name__ if error else "success"}: {error}; '
        f'messages={[m.content for m in messages]!r}'
    )


@pytest.mark.parametrize('operation', [
    'read', 'append', 'set_metadata', 'purge', 'compact', 'create_group',
])
@pytest.mark.parametrize('phase', ['begin', 'commit-before', 'rollback'])
def test_pre_commit_transaction_failures_are_sanitized_without_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, phase: str,
) -> None:
    from thyca.conversations.store import database as database_module

    ref = _setup_dm(tmp_path)
    agents = AgentStore(tmp_path)
    admin = _admin(tmp_path)
    agents.append(ref, 'coder', Message(role='user', content='keep'))
    real_connect = sqlite3.connect
    failing_statement = {
        'read': 'SELECT DATA,LOGICAL_SEQ FROM MESSAGES',
        'append': 'INSERT INTO MESSAGES',
        'set_metadata': 'UPDATE CONVERSATIONS',
        'purge': 'DROP TRIGGER NO_DELETE_MESSAGES',
        'compact': 'DROP TRIGGER NO_DELETE_MESSAGES',
        'create_group': 'INSERT INTO CONVERSATIONS',
    }[operation]

    def snapshot():
        conn = real_connect(str(tmp_path / 'conversations.db'))
        try:
            conversations = tuple(conn.execute(
                'SELECT kind,id,metadata,created_at,lifecycle_state,generation,'
                'event_high_water,logical_high_water FROM conversations ORDER BY kind,id'
            ).fetchall())
            messages = tuple(conn.execute(
                'SELECT kind, id, seq, role, content, data, created_at '
                'FROM messages ORDER BY kind, id, seq'
            ).fetchall())
            return conversations, messages
        finally:
            conn.close()

    before = snapshot()

    class InjectingConnection:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self._connection = connection

        def execute(self, statement: str, *args):
            sql = statement.strip().upper()
            if phase == 'begin' and sql in ('BEGIN', 'BEGIN IMMEDIATE'):
                raise sqlite3.OperationalError('injected begin failure')
            if phase == 'commit-before' and sql == 'COMMIT':
                raise sqlite3.OperationalError('injected commit-before failure')
            if phase == 'rollback' and sql.startswith(failing_statement):
                raise sqlite3.OperationalError('injected work failure')
            if phase == 'rollback' and sql == 'ROLLBACK':
                raise sqlite3.OperationalError('injected rollback failure')
            return self._connection.execute(statement, *args)

        def __getattr__(self, name: str):
            return getattr(self._connection, name)

    def connect(*args, **kwargs):
        return InjectingConnection(real_connect(*args, **kwargs))

    def perform() -> None:
        if operation == 'read':
            agents.read(ref, 'coder')
        elif operation == 'append':
            agents.append(ref, 'coder', Message(role='user', content='not committed'))
        elif operation == 'set_metadata':
            admin.set_metadata(ref, title='changed', title_source='user',
                               naming_attempted=True, participants=['coder'])
        elif operation == 'purge':
            admin.purge_before('9999-12-31T23:59:59Z')
        elif operation == 'compact':
            admin.compact_through_seq(ref, 1, lambda _messages: 'summary')
        else:
            admin.create_group(['coder'])

    error = None
    with monkeypatch.context() as patch:
        patch.setattr(database_module.sqlite3, 'connect', connect)
        try:
            perform()
        except Exception as exc:
            error = exc
    after = snapshot()
    expected_cause = (f'injected {phase} failure' if phase != 'rollback'
                      else 'injected rollback failure')
    assert (type(error) is ConversationStoreError and str(error) == 'conversation unavailable'
            and isinstance(error.__cause__, sqlite3.OperationalError)
            and str(error.__cause__) == expected_cause and after == before), (
        f'operation={operation}, phase={phase}, expected sanitized failure, '
        f'cause={type(error.__cause__).__name__ if error and error.__cause__ else "none"}: '
        f'{error}; data_unchanged={after == before}'
    )


def test_commit_unknown_is_sanitized_and_does_not_retry_committed_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from thyca.conversations.store import database as database_module

    ref = _setup_dm(tmp_path)
    agents = AgentStore(tmp_path)
    agents.append(ref, 'coder', Message(role='user', content='keep'))
    real_connect = sqlite3.connect
    commits = []

    class CommitThenRaiseConnection:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self._connection = connection

        def execute(self, statement: str, *args):
            if statement.strip().upper() == 'COMMIT':
                cursor = self._connection.execute(statement, *args)
                commits.append('committed')
                raise sqlite3.OperationalError('injected post-commit error')
            return self._connection.execute(statement, *args)

        def __getattr__(self, name: str):
            return getattr(self._connection, name)

    def connect(*args, **kwargs):
        return CommitThenRaiseConnection(real_connect(*args, **kwargs))

    error = None
    with monkeypatch.context() as patch:
        patch.setattr(database_module.sqlite3, 'connect', connect)
        try:
            agents.append(ref, 'coder', Message(role='user', content='committed once'))
        except Exception as exc:
            error = exc
    messages = agents.read(ref, 'coder').messages
    assert (type(error) is ConversationStoreError and str(error) == 'conversation unavailable'
            and commits == ['committed']
            and [message.content for message in messages] == ['keep', 'committed once']), (
        f'expected one committed append with sanitized uncertain outcome; '
        f'error={type(error).__name__ if error else "success"}: {error}; '
        f'commits={commits!r}, messages={[message.content for message in messages]!r}'
    )


def test_no_callback_inside_transactions(tmp_path: Path) -> None:
    for cls in (AgentStore, AdminStore):
        for name, fn in inspect.getmembers(cls, predicate=inspect.isfunction):
            if name.startswith('_'):
                continue
            for param in inspect.signature(fn).parameters.values():
                if (cls, name, param.name) == (AdminStore, 'compact_through_seq', 'summarize'):
                    continue  # single documented exception: runs outside any
                # transaction (proven by the racing-append test above).
                assert 'Callable' not in str(param.annotation) and 'callable' not in param.name.lower(), \
                    (cls.__name__, name, param.name)


def test_concurrent_storm_finishes_within_bound(tmp_path: Path) -> None:
    refs = [_setup_dm(tmp_path, f'bot-{i}') for i in range(4)]
    start = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futs = [pool.submit(_append_many, str(tmp_path), 'dm', r.id, r.id, w * 10, 10)
                for w, r in enumerate(refs) for _ in range(2)]
        assert [f.result() for f in futs] == ['ok'] * 8
    assert time.monotonic() - start < 30
    for r in refs:
        assert len(AgentStore(tmp_path).read(r, r.id).messages) == 20


def test_concurrent_group_appends_multiprocess_no_loss(tmp_path: Path) -> None:
    _initialize(tmp_path)
    admin = _admin(tmp_path)
    ref = admin.create_group(['coder', 'thyca']).ref
    ctx = multiprocessing.get_context('spawn')
    with concurrent.futures.ProcessPoolExecutor(max_workers=4, mp_context=ctx) as pool:
        futs = [pool.submit(_append_many, str(tmp_path), 'group', ref.id, 'coder', w * 25, 25)
                for w in range(4)]
        assert [f.result() for f in futs] == ['ok'] * 4
    contents = {m.content for m in AgentStore(tmp_path).read(ref, 'coder').messages}
    assert contents == {f'msg-{i}' for i in range(100)}
    conn = sqlite3.connect(str(tmp_path / 'conversations.db'))
    try:
        seqs = sorted(r[0] for r in conn.execute('SELECT seq FROM messages'))
    finally:
        conn.close()
    assert seqs == list(range(1, 101))
