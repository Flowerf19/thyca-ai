"""SQLite conversation store: security boundaries and admin lifecycle (synthetic roots only)."""
from __future__ import annotations

import concurrent.futures
import inspect
import multiprocessing
import os
import sqlite3
import stat
import time
from pathlib import Path

import pytest

from thyca.conversations.store import (
    AdminStore,
    AgentStore,
    ConversationStoreError,
)
from thyca.core.conversation import ConversationRef
from thyca.core.protocol import Message


def _admin(root: Path) -> AdminStore:
    return AdminStore(root)


def _setup_dm(root: Path, bot: str = 'coder') -> ConversationRef:
    _admin(root).bootstrap_initial_dm(bot)
    return ConversationRef.dm(bot)


def _append_many(root: str, kind: str, cid: str, actor: str, start: int, count: int) -> str:
    store = AgentStore(Path(root))
    ref = ConversationRef(kind, cid)  # type: ignore[arg-type]
    for i in range(start, start + count):
        store.append(ref, actor, Message(role='user', content=f'msg-{i}'))
    return 'ok'


def _init_only(root: str) -> str:
    AgentStore(Path(root)).read(ConversationRef.dm('coder'), 'coder')
    return 'ok'


def _crasher(root: str) -> None:
    """Insert without commit, then die: rollback must discard the row."""
    conn = sqlite3.connect(str(Path(root) / 'conversations.db'), timeout=5.0)
    conn.execute('BEGIN IMMEDIATE')
    conn.execute(
        "INSERT INTO messages(kind,id,seq,role,content,data,created_at)"
        " VALUES('dm','coder',999,'user','partial','{}','t')")
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


# ---------- concurrent init ----------

def test_concurrent_init_16_processes(tmp_path: Path) -> None:
    _setup_dm(tmp_path)  # ensure schema + one door exist first
    ctx = multiprocessing.get_context('spawn')
    with concurrent.futures.ProcessPoolExecutor(max_workers=16, mp_context=ctx) as pool:
        assert list(pool.map(_init_only, [str(tmp_path)] * 16)) == ['ok'] * 16
    conn = sqlite3.connect(str(tmp_path / 'conversations.db'))
    try:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 1
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


# ---------- bad data ----------

def test_oversized_and_bad_typed_content_rejected(tmp_path: Path) -> None:
    from thyca.conversations import store as mod

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
    from thyca.conversations import store as mod

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

def test_read_pagination(tmp_path: Path) -> None:
    ref = _setup_dm(tmp_path)
    agents = AgentStore(tmp_path)
    for i in range(10):
        agents.append(ref, 'coder', Message(role='user', content=f'msg-{i}'))
    page1 = agents.read(ref, 'coder', limit=4)
    assert [m.content for m in page1.messages] == ['msg-0', 'msg-1', 'msg-2', 'msg-3']
    page2 = agents.read(ref, 'coder', after_seq=4, limit=4)
    assert [m.content for m in page2.messages] == ['msg-4', 'msg-5', 'msg-6', 'msg-7']
    with pytest.raises(ValueError):
        agents.read(ref, 'coder', after_seq=-1)
    with pytest.raises(ValueError):
        agents.read(ref, 'coder', limit=0)


# ---------- retention / compaction ----------

def test_purge_before_cutoff_and_vacuum(tmp_path: Path) -> None:
    admin = _admin(tmp_path)
    ref = _setup_dm(tmp_path)
    agents = AgentStore(tmp_path)
    agents.append(ref, 'coder', Message(role='user', content='old'))
    conn = sqlite3.connect(str(tmp_path / 'conversations.db'))
    try:
        conn.execute("DROP TRIGGER no_update_messages")
        conn.execute("UPDATE messages SET created_at='2000-01-01T00:00:00Z' WHERE content='old'")
        conn.commit()
    finally:
        conn.close()
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
    assert [m.content for m in got.messages] == ['msg-4', 'msg-5', 'racing', summary.content]
    # seq stays contiguous and unique; new appends keep working.
    conn = sqlite3.connect(str(tmp_path / 'conversations.db'))
    try:
        seqs = sorted(r[0] for r in conn.execute('SELECT seq FROM messages'))
    finally:
        conn.close()
    assert seqs == list(range(min(seqs), max(seqs) + 1))
    agents.append(ref, 'coder', Message(role='user', content='after'))
    assert len(agents.read(ref, 'coder').messages) == len(seqs) + 1
    empty = admin.bootstrap_initial_dm('quiet')
    with pytest.raises(ConversationStoreError):
        admin.compact_through_seq(empty.ref, 10, lambda old: 'nothing there')


# ---------- transaction discipline ----------

def test_no_callback_inside_transactions(tmp_path: Path) -> None:
    from thyca.conversations import store as mod

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
