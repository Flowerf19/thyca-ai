"""Phase 1 read adapter: paginated list, cursor assembly, wire payloads (synthetic roots only)."""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from thyca.conversations.store import (
    AdminStore,
    AgentStore,
    ConversationStoreError,
    DMBootstrapIntent,
    RootInitializationIntent,
    SyntheticReadIntent,
    assemble_history,
    detail_for,
    get_detail,
    get_summary,
    get_trace_detail,
    get_trace_turns,
    list_summaries,
    summary_for,
    trace_detail_for,
    trace_turns_for,
)
from thyca.conversations.store.read_adapter import _MAX_ASSEMBLY_RESYNCS
from thyca.core.conversation import ConversationRef
from thyca.core.protocol import Message
from thyca.sessions import Session
from thyca.sessions.wire import message_dict, session_detail, session_summary, session_title


def _root_id(root: Path) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, str(root.absolute())))


def _init(root: Path) -> None:
    AdminStore(root).initialize(RootInitializationIntent(_root_id(root)))


def _dm(root: Path, bot: str) -> ConversationRef:
    attempt = uuid.uuid5(uuid.NAMESPACE_DNS, f'{root.absolute()}:{bot}').hex
    return AdminStore(root).bootstrap_initial_dm(
        bot, intent=DMBootstrapIntent(bot, attempt)).ref


def _intent() -> SyntheticReadIntent:
    return SyntheticReadIntent('fixture-read')


def _msg(role: str, text: str, day: int) -> Message:
    return Message(role=role, content=text, ts=f'2020-01-{day:02d}T00:00:00Z')


def _seed_dm(root: Path, bot: str = 'coder') -> ConversationRef:
    _init(root)
    return _dm(root, bot)


def _legacy_session(ref: ConversationRef, messages: list[Message],
                    title=None, source=None, attempted: bool = False) -> Session:
    return Session(
        ref.id, Path(f'synthetic://{ref.kind}/{ref.id}'), list(messages),
        title=title, title_source=source, naming_attempted=attempted,
        bots=[ref.id] if ref.kind == 'dm' else ['coder', 'thyca'],
        dm_bot=ref.id if ref.kind == 'dm' else None,
    )


# ---------- AdminStore.list pagination ----------

def test_list_is_paginated_newest_first_and_includes_empty(tmp_path: Path) -> None:
    _init(tmp_path)
    admin = AdminStore(tmp_path)
    first = _dm(tmp_path, 'alice')
    second = _dm(tmp_path, 'bob')
    group = admin.create_group(['coder', 'thyca']).ref
    agents = AgentStore(tmp_path)
    agents.append(first, 'alice', _msg('user', 'hi', 3))

    items, has_more = admin.list(limit=2, offset=0)
    assert has_more is True
    assert len(items) == 2

    items2, has_more2 = admin.list(limit=2, offset=2)
    assert has_more2 is False
    assert len(items2) == 1
    # Pages tile the full newest-first ordering (created_at DESC, then
    # kind/id ASC for same-second ties); every door appears exactly once.
    full, full_more = admin.list(limit=10, offset=0)
    assert full_more is False
    assert [row.ref for row in items + items2] == [row.ref for row in full]
    assert {row.ref for row in full} == {first, second, group}
    expect = sorted(full, key=lambda row: (row.ref.kind, row.ref.id))
    expect = sorted(expect, key=lambda row: row.created_at, reverse=True)
    assert [row.ref for row in full] == [row.ref for row in expect]
    # Empty conversations are listed with their creation stamp and metadata.
    empty = next(row for row in items2 + items if row.ref == second)
    assert empty.participants == ('bob',)
    assert empty.created_at and empty.title is None


def test_list_rejects_bad_pagination_before_touching_disk(tmp_path: Path) -> None:
    root = tmp_path / 'missing'
    for kwargs in ({'limit': 0}, {'limit': True}, {'limit': '2'},
                   {'offset': -1}, {'offset': True}):
        with pytest.raises(ValueError):
            AdminStore(root).list(**kwargs)  # type: ignore[arg-type]
    assert not root.exists()


def test_list_skips_nothing_on_ready_fixture(tmp_path: Path) -> None:
    ref = _seed_dm(tmp_path)
    items, has_more = AdminStore(tmp_path).list()
    assert has_more is False
    assert [row.ref for row in items] == [ref]


# ---------- cursor-based full-history assembly ----------

def test_assembly_across_pages_matches_single_read(tmp_path: Path) -> None:
    ref = _seed_dm(tmp_path)
    agents = AgentStore(tmp_path)
    for i in range(9):
        role = 'user' if i % 2 == 0 else 'assistant'
        agents.append(ref, 'coder', _msg(role, f'msg-{i}', 4))
    one_shot = agents.read(ref, 'coder').messages
    for page_limit in (1, 2, 4, 500):
        assembled = assemble_history(
            tmp_path, ref, 'coder', intent=_intent(), page_limit=page_limit)
        assert [m.content for m in assembled.messages] == [m.content for m in one_shot]
        assert assembled.title is None and assembled.participants == ('coder',)


def test_assembly_requires_intent_and_valid_page_limit(tmp_path: Path) -> None:
    ref = _seed_dm(tmp_path)
    with pytest.raises(ValueError):
        assemble_history(tmp_path, ref, 'coder', intent='synthetic')  # type: ignore[arg-type]
    for bad in (0, True, '10'):
        with pytest.raises(ValueError):
            assemble_history(tmp_path, ref, 'coder', intent=_intent(),
                             page_limit=bad)  # type: ignore[arg-type]


def test_assembly_resyncs_after_compaction_generation_bump(tmp_path: Path) -> None:
    ref = _seed_dm(tmp_path)
    agents = AgentStore(tmp_path)
    admin = AdminStore(tmp_path)
    for i in range(4):
        agents.append(ref, 'coder', _msg('user' if i % 2 == 0 else 'assistant', f'm-{i}', 5))
    # Compact the prefix: generation bumps, logical positions shift to a
    # summary row plus the suffix.
    admin.compact_through_seq(ref, 2, lambda _old: 'summary')
    assembled = assemble_history(tmp_path, ref, 'coder', intent=_intent(), page_limit=1)
    assert [m.content for m in assembled.messages] == ['summary', 'm-2', 'm-3']


# ---------- summary/detail byte-compat ----------

def test_summary_matches_wire_shape_and_values(tmp_path: Path) -> None:
    from seeded_config import seeded_config

    cfg = seeded_config()
    ref = _seed_dm(tmp_path)
    agents = AgentStore(tmp_path)
    agents.append(ref, 'coder', _msg('user', 'ping', 6))
    agents.append(ref, 'coder', _msg('assistant', 'pong', 7))
    conversation = assemble_history(tmp_path, ref, 'coder', intent=_intent())
    listing, _ = AdminStore(tmp_path).list()
    created = next(row.created_at for row in listing if row.ref == ref)

    summary = summary_for(conversation, created)
    legacy = session_summary(_legacy_session(ref, list(conversation.messages)))
    assert set(summary) == set(legacy)
    assert summary == {**legacy, 'id': ref.id, 'window_id': ref.id}

    detail = detail_for(conversation, cfg)
    legacy_detail = session_detail(_legacy_session(ref, list(conversation.messages)), cfg)
    assert set(detail) == set(legacy_detail)
    assert detail['messages'] == legacy_detail['messages']
    assert detail['context'] == legacy_detail['context']
    assert detail['running'] is False and 'started_at' not in detail


def test_detail_never_carries_tool_calls_or_reasoning(tmp_path: Path) -> None:
    from seeded_config import seeded_config

    ref = _seed_dm(tmp_path)
    agents = AgentStore(tmp_path)
    agents.append(ref, 'coder', _msg('user', 'ping', 8))
    agents.append(ref, 'coder', _msg('assistant', 'pong', 8))
    detail = get_detail(tmp_path, ref, 'coder', seeded_config(), intent=_intent())
    assert all('tool_calls' not in item for item in detail['messages'])
    assert all('reasoning' not in item for item in detail['messages'])
    assert all('tool_call_id' not in item for item in detail['messages'])


def test_empty_conversation_uses_created_at_and_blank_title(tmp_path: Path) -> None:
    ref = _seed_dm(tmp_path)
    listing, _ = AdminStore(tmp_path).list()
    created = next(row.created_at for row in listing if row.ref == ref)
    conversation = assemble_history(tmp_path, ref, 'coder', intent=_intent())
    assert conversation.messages == ()
    summary = summary_for(conversation, created)
    assert summary['updated_at'] == created
    assert summary['message_count'] == 0 and summary['turns'] == 0
    assert summary['title'] == 'Phiên trống'
    with pytest.raises(ValueError):
        summary_for(conversation, 'not-a-ts')


def test_title_read_semantics_match_wire(tmp_path: Path) -> None:
    admin = AdminStore(tmp_path)
    ref = _seed_dm(tmp_path)
    agents = AgentStore(tmp_path)
    agents.append(ref, 'coder', _msg('user', 'hello world', 9))
    # User titles are verbatim; agent echo titles are rejected by display rules.
    admin.set_metadata(ref, title='Kế hoạch tuần này', title_source='user',
                       naming_attempted=True, participants=['coder'])
    conversation = assemble_history(tmp_path, ref, 'coder', intent=_intent())
    listing, _ = admin.list()
    created = next(row.created_at for row in listing if row.ref == ref)
    assert summary_for(conversation, created)['title'] == 'Kế hoạch tuần này'
    assert session_title(_legacy_session(ref, list(conversation.messages),
                                         'Kế hoạch tuần này', 'user', True)) == 'Kế hoạch tuần này'

    admin.set_metadata(ref, title='hello world', title_source='agent',
                       naming_attempted=True, participants=['coder'])
    echo = assemble_history(tmp_path, ref, 'coder', intent=_intent())
    assert summary_for(echo, created)['title'] == session_title(
        _legacy_session(ref, list(echo.messages), 'hello world', 'agent', True))


def test_prototype_rejects_content_none(tmp_path: Path) -> None:
    ref = _seed_dm(tmp_path)
    with pytest.raises(ValueError):
        AgentStore(tmp_path).append(
            ref, 'coder', Message(role='user', content=None))  # type: ignore[arg-type]


# ---------- trace payloads: tool_calls only in Trace ----------

def test_trace_lists_match_turns_from_session(tmp_path: Path) -> None:
    ref = _seed_dm(tmp_path)
    agents = AgentStore(tmp_path)
    agents.append(ref, 'coder', _msg('user', 'q1', 10))
    agents.append(ref, 'coder', _msg('assistant', 'a1', 11))
    agents.append(ref, 'coder', _msg('user', 'q2', 12))
    conversation = assemble_history(tmp_path, ref, 'coder', intent=_intent())
    turns = trace_turns_for(conversation)
    legacy = session_summary(_legacy_session(ref, list(conversation.messages)))
    assert legacy['turns'] == len(turns) == 2
    payloads = [t.to_payload() for t in turns]
    assert all(set(p) == set(payloads[0]) for p in payloads)
    via_io = get_trace_turns(tmp_path, ref, 'coder', intent=_intent(), page_limit=1)
    assert [t.to_payload() for t in via_io] == payloads


def test_trace_detail_carries_tool_shape_while_detail_does_not(tmp_path: Path) -> None:
    from seeded_config import seeded_config

    cfg = seeded_config()
    ref = _seed_dm(tmp_path)
    agents = AgentStore(tmp_path)
    agents.append(ref, 'coder', _msg('user', 'ping', 13))
    agents.append(ref, 'coder', _msg('assistant', 'pong', 13))
    conversation = assemble_history(tmp_path, ref, 'coder', intent=_intent())

    trace = trace_detail_for(conversation, 0)
    assert trace['kind'] == 'dm'
    assert trace['bot_id'] == ref.id
    assert 'session_id' not in trace
    assert all('tool_calls' in item for item in trace['messages'])
    assert all('tool_call_id' in item for item in trace['messages'])
    assert all('meta' in item for item in trace['messages'])
    with pytest.raises(ValueError):
        trace_detail_for(conversation, 7)
    via_io = get_trace_detail(tmp_path, ref, 'coder', 0, intent=_intent())
    assert via_io == trace_detail_for(conversation, 0)

    detail = detail_for(conversation, cfg)
    assert all('tool_calls' not in item for item in detail['messages'])
    # Same source rows, two shapes: chat stays lean, trace carries the calls.
    assert [m['content'] for m in detail['messages']] == [m['content'] for m in trace['messages']]


# ---------- synthetic-only gate ----------

def test_adapter_refuses_non_synthetic_callers_before_io(tmp_path: Path) -> None:
    root = tmp_path / 'missing'
    ref = ConversationRef.dm('coder')
    for call in (
        lambda: assemble_history(root, ref, 'coder', intent=None),  # type: ignore[arg-type]
        lambda: get_summary(root, ref, 'coder', intent='fixture'),  # type: ignore[arg-type]
        lambda: list_summaries(root, intent=None,  # type: ignore[arg-type]
                               actor_for=lambda _row: 'coder'),
    ):
        with pytest.raises(ValueError):
            call()
    assert not root.exists()


def test_list_summaries_pages_with_per_row_actors(tmp_path: Path) -> None:
    _init(tmp_path)
    admin = AdminStore(tmp_path)
    alice = _dm(tmp_path, 'alice')
    bob = _dm(tmp_path, 'bob')
    AgentStore(tmp_path).append(alice, 'alice', _msg('user', 'hi alice', 14))

    def actor_for(row) -> str:
        assert isinstance(row, object)
        return row.ref.id

    first, has_more = list_summaries(
        tmp_path, intent=_intent(), actor_for=actor_for, limit=1, offset=0)
    assert has_more is True and len(first) == 1
    second, has_more2 = list_summaries(
        tmp_path, intent=_intent(), actor_for=actor_for, limit=1, offset=1)
    assert has_more2 is False and len(second) == 1
    ids = {first[0]['id'], second[0]['id']}
    assert ids == {alice.id, bob.id}
    by_id = {item['id']: item for item in first + second}
    assert by_id['alice']['message_count'] == 1
    assert by_id['bob']['message_count'] == 0
    assert by_id['bob']['title'] == 'Phiên trống'
    with pytest.raises(ValueError):
        list_summaries(tmp_path, intent=_intent(), actor_for='x')  # type: ignore[arg-type]


def test_writes_stay_on_legacy_path(tmp_path: Path) -> None:
    """The adapter exposes reads only: no append/create/set_metadata surface."""
    from thyca.conversations.store import read_adapter as adapter

    for name in ('append', 'create_group', 'set_metadata', 'bootstrap_initial_dm',
                 'initialize', 'purge_before', 'compact_through_seq', 'vacuum'):
        assert not hasattr(adapter, name), name
    assert _MAX_ASSEMBLY_RESYNCS >= 1
