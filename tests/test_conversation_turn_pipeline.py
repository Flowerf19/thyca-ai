"""Runtime turn pipeline on initialized store roots only.

No live server, no live data. Each test initializes the runtime store
for its own ``tmp_path``. The HTTP path is unchanged — the
``serve_chat`` error/busy/cancel/retry/follow subsets below pin the
400/404/409/503 mapping, single terminal NDJSON, failed-turn markers, and
Trace tool-row expansion it must keep.
"""
from __future__ import annotations

import io
import queue
import uuid
from pathlib import Path

import pytest

from thyca.agents.events import TurnEvent
from thyca.agents.reply import ContentDelta
from thyca.conversations.store import (
    AdminStore,
    AgentStore,
    ConversationStoreError,
    DMBootstrapIntent,
    RootInitializationIntent,
    RuntimeReadIntent,
    RuntimeWriteIntent,
    append_turn_error,
    assemble_history,
    detail_for,
    mark_naming_attempted,
    prepare_retry,
    project_for_trace,
    record_turn_error,
    store_title_if_missing,
    trace_detail_for,
    trace_turns_for,
    trace_turns_projected,
)
from thyca.core.conversation import ConversationRef
from thyca.core.protocol import Message, ToolCall
from thyca.serve.bridge import SENTINEL, bridge_worker, pump_stream
from thyca.serve.errors import public_turn_error
from thyca.serve.turn_state import TurnHub, TurnState
from thyca.conversations.runtime import SessionBusy, SessionCorrupt, SessionError, SessionNotFound
from thyca.conversations.runtime.store import SessionStore


def _root_id(root: Path) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, str(root.absolute())))


def _init(root: Path) -> None:
    AdminStore(root).initialize(RootInitializationIntent(_root_id(root)))


def _dm(root: Path, bot: str) -> ConversationRef:
    attempt = uuid.uuid5(uuid.NAMESPACE_DNS, f'{root.absolute()}:{bot}').hex
    return AdminStore(root).bootstrap_initial_dm(
        bot, intent=DMBootstrapIntent(bot, attempt)).ref


def _write_intent() -> RuntimeWriteIntent:
    return RuntimeWriteIntent('runtime-turn')


def _read_intent() -> RuntimeReadIntent:
    return RuntimeReadIntent('runtime-trace')


def _msg(role: str, text: str) -> Message:
    return Message(role=role, content=text)


@pytest.fixture
def switched(tmp_path: Path):
    _init(tmp_path)
    yield tmp_path


# Count completed turns over stored messages with error projection.
def _completed_count(conversation) -> int:
    from thyca.conversations.runtime.title import completed_turn_count

    return completed_turn_count(list(project_for_trace(conversation).messages))


# Read the stored title pair for one door through the runtime store.
def _stored_title(root: Path, ref, actor: str):
    conv = assemble_history(root, ref, actor, intent=_read_intent())
    return conv.title, conv.title_source


# Build projected trace turns for one door through the runtime store.
def _trace_turns(root: Path, ref, actor: str):
    return trace_turns_projected(
        assemble_history(root, ref, actor, intent=_read_intent()))


# Build one projected trace detail for one door through the runtime store.
def _trace_detail(root: Path, ref, actor: str, turn_index: int):
    return trace_detail_for(
        project_for_trace(assemble_history(root, ref, actor, intent=_read_intent())),
        turn_index)


# ---------- error mapping: 400/404/409/503 preserved + store covered ----------

def test_public_turn_error_preserves_contract_and_maps_store() -> None:
    from thyca.app.chat_app import InvalidTurnOption, InvalidTurnText, TurnCancelled
    from thyca.llms.llm_base import LLMError

    assert public_turn_error(InvalidTurnText('empty')) == (
        400, 'invalid_text', 'invalid text: empty')
    assert public_turn_error(InvalidTurnOption('invalid model')) == (
        400, 'invalid_model', 'invalid model')
    assert public_turn_error(SessionBusy('s')) == (409, 'session_busy', 'conversation busy')
    assert public_turn_error(SessionNotFound(Path('/tmp/x'))) == (
        404, 'session_not_found', 'conversation not found')
    assert public_turn_error(SessionCorrupt(Path('/tmp/x'), 1, 'bad')) == (
        503, 'session_unreadable', 'conversation unreadable')
    assert public_turn_error(SessionError('x')) == (
        503, 'session_unavailable', 'conversation unavailable')
    assert public_turn_error(LLMError('provider HTTP 503: down')) == (
        503, 'llm_error', 'provider HTTP 503: down')
    assert public_turn_error(TurnCancelled()) == (200, 'cancelled', 'cancelled')
    assert public_turn_error(ValueError('boom')) == (
        500, 'internal_error', 'chat unavailable')
    assert public_turn_error(RuntimeError('boom')) == (
        503, 'chat_unavailable', 'chat unavailable')

    local = ConversationStoreError('gone', scope='local',
                                   ref=ConversationRef.dm('coder'))
    assert public_turn_error(local) == (
        404, 'session_not_found', 'conversation not found')
    glob = ConversationStoreError('down')
    status, code, message = public_turn_error(glob)
    assert (status, code, message) == (
        503, 'session_unavailable', 'conversation unavailable')
    # Sanitized: the bot id never leaks into the public message.
    assert 'coder' not in message


# ---------- failed-turn markers + projected trace ----------

def test_error_marker_appends_without_rewrite_and_trace_fails(switched: Path) -> None:
    ref = _dm(switched, 'coder')
    agents = AgentStore(switched)
    agents.append(ref, 'coder', _msg('user', 'run this'))
    agents.append(ref, 'coder', _msg('assistant', 'partial'))
    before = [m.content for m in agents.read(ref, 'coder').messages]

    seq = record_turn_error(
        switched, ref, intent=_write_intent(), actor='coder',
        code='llm_error', message='boom unavailable')
    assert seq == 3

    history = agents.read(ref, 'coder').messages
    assert [m.content for m in history] == before + ['boom unavailable']
    assert history[0].content == 'run this'
    assert (history[0].meta or {}).get('error') is None
    marker = history[-1]
    assert marker.role == 'system'
    assert marker.meta == {'error': {'code': 'llm_error', 'message': 'boom unavailable'}}

    # Chat detail keeps the marker row; trace projection stamps the user.
    from seeded_config import seeded_config

    conversation = assemble_history(
        switched, ref, 'coder', intent=_read_intent())
    assert conversation.messages[-1].role == 'system'
    detail = detail_for(conversation, seeded_config())
    assert detail['messages'][-1]['role'] == 'system'

    turns = _trace_turns(switched, ref, 'coder')
    assert len(turns) == 1
    assert turns[0].status == 'failed'
    assert turns[0].error == {'code': 'llm_error', 'message': 'boom unavailable'}
    payload = _trace_detail(switched, ref, 'coder', 0)
    assert payload['status'] == 'failed'
    assert payload['error'] == {'code': 'llm_error', 'message': 'boom unavailable'}
    # Trace detail carries the tool-call shape (always present with
    # include_args), chat detail never does.
    assert all('tool_calls' in item for item in payload['messages'])
    assert all('tool_calls' not in item for item in detail['messages'])
    # A failed turn never satisfies the naming threshold.
    assert _completed_count(conversation) == 0


def test_project_for_trace_is_memory_only_and_drops_orphan_markers(switched: Path) -> None:
    ref = _dm(switched, 'coder')
    agents = AgentStore(switched)
    agents.append(ref, 'coder', _msg('user', 'q'))
    stored_before = agents.read(ref, 'coder').messages
    conversation = assemble_history(
        switched, ref, 'coder', intent=_read_intent())
    projected = project_for_trace(conversation)
    # No markers: identical object, no copy.
    assert projected is conversation

    record_turn_error(
        switched, ref, intent=_write_intent(), actor='coder',
        code='llm_error', message='down')
    stored_after = agents.read(ref, 'coder').messages
    assert len(stored_after) == len(stored_before) + 1
    assert stored_after[-1].role == 'system'

    conversation = assemble_history(
        switched, ref, 'coder', intent=_read_intent())
    projected = project_for_trace(conversation)
    assert len(projected.messages) == len(conversation.messages) - 1
    assert (projected.messages[0].meta or {})['error'] == {
        'code': 'llm_error', 'message': 'down'}
    # Stored bytes untouched by the projection.
    assert (agents.read(ref, 'coder').messages[0].meta or {}).get('error') is None


def test_second_failure_appends_second_marker(switched: Path) -> None:
    ref = _dm(switched, 'coder')
    AgentStore(switched).append(ref, 'coder', _msg('user', 'q'))
    record_turn_error(
        switched, ref, intent=_write_intent(), actor='coder',
        code='llm_error', message='first')
    record_turn_error(
        switched, ref, intent=_write_intent(), actor='coder',
        code='chat_unavailable', message='second')
    history = AgentStore(switched).read(ref, 'coder').messages
    assert [m.content for m in history] == ['q', 'first', 'second']
    turns = _trace_turns(switched, ref, 'coder')
    assert len(turns) == 1 and turns[0].status == 'failed'


# ---------- retry without truncation ----------

def test_retry_reports_last_user_without_mutation(switched: Path) -> None:
    ref = _dm(switched, 'coder')
    agents = AgentStore(switched)
    agents.append(ref, 'coder', _msg('user', 'q1'))
    agents.append(ref, 'coder', _msg('assistant', 'a1'))
    agents.append(ref, 'coder', _msg('user', 'q2'))
    before = [m.content for m in agents.read(ref, 'coder').messages]

    plan = prepare_retry(switched, ref, intent=_write_intent(), actor='coder')
    assert plan.has_user is True
    assert plan.last_user_logical_seq == 3
    assert plan.message_count == 3
    assert [m.content for m in agents.read(ref, 'coder').messages] == before

    record_turn_error(
        switched, ref, intent=_write_intent(), actor='coder',
        code='llm_error', message='boom')
    rerun = prepare_retry(switched, ref, intent=_write_intent(), actor='coder')
    assert rerun.has_user is True
    assert rerun.message_count == 4
    # No truncation: the failed assistant row is still history.
    assert [m.content for m in agents.read(ref, 'coder').messages] == before + ['boom']


def test_retry_without_user_reports_empty(switched: Path) -> None:
    ref = _dm(switched, 'coder')
    plan = prepare_retry(switched, ref, intent=_write_intent(), actor='coder')
    assert plan.has_user is False
    assert plan.last_user_logical_seq is None
    assert plan.message_count == 0


# ---------- naming/title one-shot semantics ----------

def test_naming_attempted_once_and_title_if_missing(switched: Path) -> None:
    ref = _dm(switched, 'coder')
    agents = AgentStore(switched)
    agents.append(ref, 'coder', _msg('user', 'first question here'))
    agents.append(ref, 'coder', _msg('assistant', 'first answer here'))
    agents.append(ref, 'coder', _msg('user', 'second question here'))
    agents.append(ref, 'coder', _msg('assistant', 'second answer here'))
    assert _completed_count(
        assemble_history(switched, ref, 'coder', intent=_read_intent())) == 2

    first = mark_naming_attempted(
        switched, ref, intent=_write_intent(), actor='coder')
    assert first.naming_attempted is True
    second = mark_naming_attempted(
        switched, ref, intent=_write_intent(), actor='coder')
    assert second.naming_attempted is True

    stored = store_title_if_missing(
        switched, ref, intent=_write_intent(), actor='coder',
        title='  Weekly plan  ')
    assert stored == 'Weekly plan'
    assert _stored_title(switched, ref, 'coder') == ('Weekly plan', None)
    # Second automatic title never overwrites the first.
    assert store_title_if_missing(
        switched, ref, intent=_write_intent(), actor='coder',
        title='Another title') is None
    assert _stored_title(switched, ref, 'coder') == ('Weekly plan', None)
    # Blank titles store nothing.
    assert store_title_if_missing(
        switched, ref, intent=_write_intent(), actor='coder', title='   ') is None


def test_title_if_missing_loses_to_concurrent_rename(switched: Path) -> None:
    from thyca.conversations.store import set_conversation_metadata

    ref = _dm(switched, 'coder')
    AgentStore(switched).append(ref, 'coder', _msg('user', 'hello'))
    set_conversation_metadata(
        switched, ref, intent=_write_intent(), actor='coder',
        title='User name', title_source='user',
        naming_attempted=True, participants=['coder'])
    assert store_title_if_missing(
        switched, ref, intent=_write_intent(), actor='coder',
        title='Auto title') is None
    assert _stored_title(switched, ref, 'coder') == ('User name', 'user')


def test_naming_helpers_require_intent_and_initialized_root(tmp_path: Path) -> None:
    missing = tmp_path / 'missing'
    ref = ConversationRef.dm('coder')
    # Uninitialized root fails closed even with a valid runtime intent.
    with pytest.raises(ValueError, match='not initialized'):
        record_turn_error(
            missing, ref, intent=_write_intent(), actor='coder',
            code='e', message='m')
    with pytest.raises(ValueError, match='not initialized'):
        prepare_retry(missing, ref, intent=_write_intent(), actor='coder')
    with pytest.raises(ValueError, match='not initialized'):
        mark_naming_attempted(
            missing, ref, intent=_write_intent(), actor='coder')
    assert not missing.exists()
    # A non-runtime intent fails on an initialized root too.
    _init(tmp_path)
    ref = _dm(tmp_path, 'coder')
    AgentStore(tmp_path).append(ref, 'coder', _msg('user', 'hi'))
    with pytest.raises(ValueError):
        record_turn_error(
            tmp_path, ref, intent='x', actor='coder',  # type: ignore[arg-type]
            code='e', message='m')


# ---------- trace slices + tool-row expansion ----------

def test_trace_slices_mirror_turn_slices(switched: Path) -> None:
    ref = _dm(switched, 'coder')
    agents = AgentStore(switched)
    agents.append(ref, 'coder', _msg('user', 'q1'))
    agents.append(ref, 'coder', _msg('assistant', 'a1'))
    agents.append(ref, 'coder', _msg('user', 'q2'))
    agents.append(ref, 'coder', _msg('assistant', 'a2'))
    conversation = assemble_history(switched, ref, 'coder', intent=_read_intent())
    turns = trace_turns_projected(conversation)
    legacy = trace_turns_for(conversation)
    assert [t.to_payload()['turn_index'] for t in turns] == [0, 1]
    assert [t.status for t in turns] == [t.status for t in legacy] == ['completed'] * 2
    via_io = _trace_turns(switched, ref, 'coder')
    assert [t.to_payload() for t in via_io] == [t.to_payload() for t in turns]
    detail = trace_detail_for(project_for_trace(conversation), 1)
    assert detail['turn_index'] == 1
    assert [m['content'] for m in detail['messages']] == ['q2', 'a2']
    with pytest.raises(ValueError):
        trace_detail_for(project_for_trace(conversation), 9)


def test_trace_tool_row_expansion_stays_in_trace_only(switched: Path) -> None:
    from seeded_config import seeded_config

    # Stored rows are plain (the store rejects tool_calls): chat
    # stays lean while trace always carries the call shape.
    ref = _dm(switched, 'coder')
    AgentStore(switched).append(ref, 'coder', _msg('user', 'ping'))
    AgentStore(switched).append(ref, 'coder', _msg('assistant', 'pong'))
    stored = assemble_history(switched, ref, 'coder', intent=_read_intent())
    chat_stored = detail_for(stored, seeded_config())
    assert all('tool_calls' not in item for item in chat_stored['messages'])
    trace_stored = trace_detail_for(project_for_trace(stored), 0)
    assert all('tool_calls' in item for item in trace_stored['messages'])
    assert all('tool_call_id' in item for item in trace_stored['messages'])
    assert all('meta' in item for item in trace_stored['messages'])

    # Render layer: arguments ride only the trace shape (chat keeps
    # id/name so replay can label the call, never the input JSON).
    call = ToolCall(id='call-1', name='read', arguments={'path': 'a.txt'})
    conversation = assemble_history_like(
        [Message(role='user', content='open a'),
         Message(role='assistant', content='reading', tool_calls=[call],
                 tool_call_id=None, meta={'bot_id': 'coder'})])
    chat = detail_for(conversation, seeded_config())
    by_role = {item['role']: item for item in chat['messages']}
    assert by_role['assistant']['tool_calls'][0]['name'] == 'read'
    assert 'arguments' not in by_role['assistant']['tool_calls'][0]
    trace = trace_detail_for(conversation, 0)
    by_role = {item['role']: item for item in trace['messages']}
    assert by_role['assistant']['tool_calls'][0]['name'] == 'read'
    assert by_role['assistant']['tool_calls'][0]['arguments'] == {'path': 'a.txt'}
    assert 'tool_call_id' in by_role['assistant']
    assert 'meta' in by_role['assistant']


def assemble_history_like(messages: list[Message]):
    from thyca.conversations.store.contracts import _view

    return _view(
        ConversationRef.dm('coder'),
        {'title': None, 'title_source': None, 'naming_attempted': False,
         'participants': ['coder']},
        tuple(messages),
    )


# ---------- TurnState busy/cancel/follow on store ids ----------

def test_turnstate_busy_cancel_follow_on_store_ids(switched: Path) -> None:
    ref = _dm(switched, 'coder')
    group = AdminStore(switched).create_group(['coder', 'thyca']).ref
    turns = TurnState()

    hub = turns.claim(group.id)
    assert isinstance(hub, TurnHub)
    with pytest.raises(SessionBusy):
        turns.claim(group.id)
    status, code, _message = public_turn_error(SessionBusy(group.id))
    assert (status, code) == (409, 'session_busy')
    # Another conversation stays free while the first is claimed.
    assert isinstance(turns.claim(ref.id), TurnHub)
    turns.release(ref.id)
    turns.release(group.id)
    assert turns.hub(group.id) is None

    # Follow: the hub replays buffered events to a late subscriber.
    hub = turns.claim(group.id)
    hub.publish(TurnEvent(type='turn.accepted', bot_id='thyca'))
    hub.publish(TurnEvent(type='llm.started', round=1, bot_id='thyca'))
    late = hub.subscribe()
    assert late.get(timeout=2).to_dict() == {'type': 'turn.accepted', 'bot_id': 'thyca'}
    hub.publish(('completed', {'reply': 'late'}))
    hub.close()
    seen: list = []
    while True:
        item = late.get(timeout=2)
        if item is TurnHub.SENTINEL:
            break
        seen.append(item)
    assert seen[0].to_dict()['type'] == 'llm.started'
    assert seen[1] == ('completed', {'reply': 'late'})
    turns.release(group.id)


def test_bridge_worker_cancel_maps_to_single_cancelled_terminal() -> None:
    from thyca.app.chat_app import TurnCancelled

    class CancelApp:
        def turn(self, *args, **kwargs):
            raise TurnCancelled()

    items: queue.Queue = queue.Queue()
    bridge_worker(CancelApp(), 's', 'hi', items, {'disconnected': False})  # type: ignore[arg-type]
    assert items.get(timeout=2) == ('cancelled', None)
    assert items.get(timeout=2) is SENTINEL


class _FakeHandler:
    def __init__(self) -> None:
        self.buf = io.BytesIO()
        self.wfile = self.buf
        self.json_sent: list = []
        self.headers_sent = False

    def _stream_headers(self) -> None:
        self.headers_sent = True

    def _json(self, status: int, body: dict) -> None:
        self.json_sent.append((status, body))

    def lines(self) -> list[dict]:
        import json as _json

        return [_json.loads(line.decode('utf-8'))
                for line in self.buf.getvalue().splitlines() if line.strip()]


def _drain(handler: _FakeHandler) -> list[dict]:
    return handler.lines()


def test_pump_stream_pre_accept_errors_are_http_json() -> None:
    handler = _FakeHandler()
    items: queue.Queue = queue.Queue()
    items.put(('failed', SessionNotFound(Path('/tmp/x'))))
    pump_stream(handler, items, {'disconnected': False})
    assert handler.json_sent == [(404, {'error': 'conversation not found'})]
    assert _drain(handler) == []

    handler = _FakeHandler()
    items = queue.Queue()
    items.put(('failed', ConversationStoreError('gone', scope='local',
                                                ref=ConversationRef.dm('coder'))))
    pump_stream(handler, items, {'disconnected': False})
    assert handler.json_sent == [(404, {'error': 'conversation not found'})]

    handler = _FakeHandler()
    items = queue.Queue()
    items.put(('failed', ConversationStoreError('down')))
    pump_stream(handler, items, {'disconnected': False})
    assert handler.json_sent == [(503, {'error': 'conversation unavailable'})]


def test_pump_stream_single_terminal_and_failed_cancel_shapes() -> None:
    # Completed: exactly one turn.completed, extras after it are dropped.
    handler = _FakeHandler()
    items: queue.Queue = queue.Queue()
    state = {'disconnected': False}
    items.put(TurnEvent(type='turn.accepted', bot_id='thyca'))
    items.put(ContentDelta(round=1, delta='hi', bot_id='thyca'))
    items.put(('completed', {'reply': 'done'}))
    items.put(TurnEvent(type='llm.started', round=9, bot_id='thyca'))
    items.put(SENTINEL)
    pump_stream(handler, items, state)
    lines = _drain(handler)
    assert [item['type'] for item in lines] == [
        'turn.accepted', 'llm.content', 'turn.completed']
    assert lines[-1] == {'type': 'turn.completed', 'detail': {'reply': 'done'}}

    # Failed: single turn.failed with the mapped code/message.
    from thyca.llms.llm_base import LLMError

    handler = _FakeHandler()
    items = queue.Queue()
    items.put(TurnEvent(type='turn.accepted', bot_id='thyca'))
    items.put(('failed', LLMError('provider HTTP 503: down')))
    items.put(SENTINEL)
    pump_stream(handler, items, {'disconnected': False})
    lines = _drain(handler)
    assert lines[-1]['type'] == 'turn.failed'
    assert lines[-1]['code'] == 'llm_error'
    assert [item['type'] for item in lines].count('turn.failed') == 1

    # Cancelled: single turn.cancelled.
    handler = _FakeHandler()
    items = queue.Queue()
    items.put(TurnEvent(type='turn.accepted', bot_id='thyca'))
    items.put(('cancelled', None))
    items.put(SENTINEL)
    pump_stream(handler, items, {'disconnected': False})
    assert _drain(handler)[-1] == {'type': 'turn.cancelled'}

    # Sentinel with no terminal: constant public failure, never a hang.
    handler = _FakeHandler()
    items = queue.Queue()
    items.put(TurnEvent(type='turn.accepted', bot_id='thyca'))
    items.put(SENTINEL)
    pump_stream(handler, items, {'disconnected': False},
                app=None, session_id='s', model=None)
    lines = _drain(handler)
    assert lines[-1] == {'type': 'turn.failed', 'code': 'chat_unavailable',
                         'message': 'chat unavailable'}


def test_public_store_exports_turn_pipeline_api() -> None:
    import thyca.conversations.store as store

    for name in ('record_turn_error', 'prepare_retry',
                 'mark_naming_attempted', 'store_title_if_missing',
                 'project_for_trace', 'trace_turns_projected'):
        assert callable(getattr(store, name)), name
    # append_turn_error stays exported (Phase 2); record_turn_error routes it.
    assert store.append_turn_error is append_turn_error
