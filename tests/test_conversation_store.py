"""SQLite-backed conversation store: contracts and concurrency (synthetic roots only)."""
from __future__ import annotations

import concurrent.futures
import multiprocessing
import sqlite3
from pathlib import Path

import pytest

from thyca.conversations.store import ConversationStore, ConversationStoreError
from thyca.core.conversation import ConversationRef
from thyca.core.protocol import Message, ToolCall


def _append_many(root: str, kind: str, cid: str, start: int, count: int) -> str:
    store = ConversationStore(Path(root))
    ref = ConversationRef(kind, cid)  # type: ignore[arg-type]
    for i in range(start, start + count):
        store.append(ref, Message(role='user', content=f'msg-{i}'))
    return 'ok'


def _dm(root: Path, bot: str = 'coder') -> ConversationRef:
    ConversationStore(root).bootstrap_initial_dm(bot)
    return ConversationRef.dm(bot)


def test_bootstrap_dm_and_duplicate_rejected(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    conv = store.bootstrap_initial_dm('coder')
    assert conv.ref == ConversationRef.dm('coder')
    assert conv.participants == ('coder',)
    assert conv.messages == ()
    with pytest.raises(ConversationStoreError):
        store.bootstrap_initial_dm('coder')
    # DM vs group namespaces are separate doors even for same text.
    group = store.create_group(['coder', 'thyca'])
    assert group.ref.kind == 'group'
    assert store.read(ConversationRef.dm('coder')).participants == ('coder',)


def test_group_create_and_metadata_round_trip(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    group = store.create_group(['coder', 'thyca'], title='Ops')
    assert group.title == 'Ops' and group.title_source == 'user'
    store.append(group.ref, Message(role='user', content='hello'))
    updated = store.set_metadata(
        group.ref, title='Ops v2', title_source='agent',
        naming_attempted=True, participants=['coder', 'thyca'],
    )
    assert updated.title == 'Ops v2'
    assert [m.content for m in updated.messages] == ['hello']
    reread = store.read(group.ref)
    assert reread.title == 'Ops v2' and [m.content for m in reread.messages] == ['hello']


def test_append_orders_and_rejects_bad_input(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    ref = _dm(tmp_path)
    store.append(ref, Message(role='user', content='one'))
    store.append(ref, Message(role='assistant', content='two'))
    got = store.read(ref)
    assert [(m.role, m.content) for m in got.messages] == [('user', 'one'), ('assistant', 'two')]
    assert [m.ts for m in got.messages]  # timestamps survive the round trip
    with pytest.raises(ConversationStoreError):
        store.append(ConversationRef.dm('ghost'), Message(role='user', content='x'))
    with pytest.raises(ConversationStoreError):
        store.read(ConversationRef.dm('ghost'))
    with pytest.raises(ValueError):
        store.append(ref, Message(role='assistant', content=None, tool_calls=[ToolCall(id='call-1', name='t', arguments={})]))
    with pytest.raises(ValueError):
        store.append('not-a-ref', Message(role='user', content='x'))  # type: ignore[arg-type]


def test_messages_are_append_only_no_update_delete_api(tmp_path: Path) -> None:
    # The agent-facing store must not expose any message mutation beyond append.
    for name in ('update', 'delete', 'remove', 'rewrite', 'edit_message', 'delete_message'):
        assert not hasattr(ConversationStore, name), name
    store = ConversationStore(tmp_path)
    ref = _dm(tmp_path)
    store.append(ref, Message(role='user', content='keep me'))
    before = store.read(ref)
    store.set_metadata(ref, title='T', title_source='user',
                       naming_attempted=False, participants=['coder'])
    after = store.read(ref)
    assert [(m.role, m.content) for m in after.messages] == [(m.role, m.content) for m in before.messages]


def test_wal_mode_and_schema(tmp_path: Path) -> None:
    _dm(tmp_path)
    conn = sqlite3.connect(str(tmp_path / 'conversations.db'))
    try:
        assert conn.execute('PRAGMA journal_mode').fetchone()[0].lower() == 'wal'
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {'conversations', 'messages'} <= tables
    finally:
        conn.close()


def test_concurrent_appends_to_different_conversations(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    refs = [_dm(tmp_path, f'bot-{i}') for i in range(4)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futs = [pool.submit(_append_many, str(tmp_path), 'dm', r.id, 0, 25) for r in refs]
        assert [f.result() for f in futs] == ['ok'] * 4
    for r in refs:
        seqs = {m.content for m in store.read(r).messages}
        assert seqs == {f'msg-{i}' for i in range(25)}


def test_concurrent_appends_to_same_group_multiprocess(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    ref = store.create_group(['coder', 'thyca']).ref
    ctx = multiprocessing.get_context('spawn')
    with concurrent.futures.ProcessPoolExecutor(max_workers=4, mp_context=ctx) as pool:
        futs = [pool.submit(_append_many, str(tmp_path), 'group', ref.id, w * 25, 25) for w in range(4)]
        assert [f.result() for f in futs] == ['ok'] * 4
    contents = {m.content for m in store.read(ref).messages}
    assert contents == {f'msg-{i}' for i in range(100)}
    # seq values are unique with no gaps or duplicates.
    conn = sqlite3.connect(str(tmp_path / 'conversations.db'))
    try:
        seqs = sorted(r[0] for r in conn.execute(
            'SELECT seq FROM messages WHERE kind=? AND id=?', (ref.kind, ref.id)))
    finally:
        conn.close()
    assert seqs == list(range(1, 101))


def test_read_during_concurrent_writes(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    ref = _dm(tmp_path)
    errors: list[str] = []

    def reader() -> int:
        try:
            convo = ConversationStore(tmp_path).read(ref)
            # Concurrent writers interleave, so a read sees an arbitrary but
            # consistent snapshot: valid rows only, never duplicates or tears.
            contents = [m.content for m in convo.messages]
            assert len(set(contents)) == len(contents)
            assert set(contents) <= {f'msg-{i}' for i in range(40)}
            return len(convo.messages)
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))
            return -1

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        futs = [pool.submit(_append_many, str(tmp_path), 'dm', ref.id, w * 10, 10) for w in range(4)]
        futs += [pool.submit(reader) for _ in range(8)]
        results = [f.result() for f in futs]
    assert errors == []
    assert all(r >= 0 for r in results[4:])
    assert len(store.read(ref).messages) == 40
