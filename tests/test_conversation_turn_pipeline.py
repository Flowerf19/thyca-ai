"""Runtime turn pipeline on initialized store roots only.

No live server, no live data. Each test initializes the runtime store
for its own ``tmp_path``. The HTTP path is unchanged — the
``serve_chat`` error/busy/cancel/retry/follow subsets below pin the
400/404/409/503 mapping, single terminal NDJSON, failed-turn markers, and
Trace tool-row expansion it must keep.
"""
from __future__ import annotations

import io
import json as _json
import queue
import uuid
from pathlib import Path

import pytest
from seeded_config import seeded_config

import thyca.conversations.store as store
from thyca.agents.events import TurnEvent
from thyca.agents.reply import ContentDelta
from thyca.app.chat_app import InvalidTurnOption, InvalidTurnText, TurnCancelled
from thyca.conversations.runtime import (
    ConversationBusy,
    ConversationCorrupt,
    ConversationError,
    ConversationNotFound,
)
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
    prepare_retry,
    project_for_trace,
    record_turn_error,
    trace_detail_for,
    trace_turns_for,
    trace_turns_projected,
)
from thyca.conversations.store.contracts import _view
from thyca.core.conversation import ConversationRef
from thyca.core.protocol import Message, ToolCall
from thyca.llms.llm_base import LLMError
from thyca.serve.bridge import SENTINEL, bridge_worker, pump_stream
from thyca.serve.errors import public_turn_error
from thyca.serve.turn_state import TurnHub, TurnState


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


def _trace_turns(root: Path, ref, actor: str):
    return trace_turns_projected(
        assemble_history(root, ref, actor, intent=_read_intent()))


def _trace_detail(root: Path, ref, actor: str, turn_index: int):
    return trace_detail_for(
        project_for_trace(assemble_history(root, ref, actor, intent=_read_intent())),
        turn_index)


def test_public_turn_error_preserves_contract_and_maps_store() -> None:

    assert public_turn_error(InvalidTurnText('empty')) == (
        400, 'invalid_text', 'invalid text: empty')
    assert public_turn_error(InvalidTurnOption('invalid model')) == (
        400, 'invalid_model', 'invalid model')
    assert public_turn_error(ConversationBusy('s')) == (409, 'session_busy', 'conversation busy')
    assert public_turn_error(ConversationNotFound(Path('/tmp/x'))) == (
        404, 'conversation_not_found', 'conversation not found')
    assert public_turn_error(ConversationCorrupt(Path('/tmp/x'), 1, 'bad')) == (
        503, 'session_unreadable', 'conversation unreadable')
    assert public_turn_error(ConversationError('x')) == (
        503, 'conversation_unavailable', 'conversation unavailable')
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
        404, 'conversation_not_found', 'conversation not found')
    glob = ConversationStoreError('down')
    status, code, message = public_turn_error(glob)
    assert (status, code, message) == (
        503, 'conversation_unavailable', 'conversation unavailable')
    assert 'coder' not in message


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
    assert [m.content for m in history] == [*before, 'boom unavailable']
    assert history[0].content == 'run this'
    assert (history[0].meta or {}).get('error') is None
    marker = history[-1]
    assert marker.role == 'system'
    assert marker.meta == {'error': {'code': 'llm_error', 'message': 'boom unavailable'}}


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
    assert all('tool_calls' in item for item in payload['messages'])
    assert all('tool_calls' not in item for item in detail['messages'])


def test_project_for_trace_is_memory_only_and_drops_orphan_markers(switched: Path) -> None:
    ref = _dm(switched, 'coder')
    agents = AgentStore(switched)
    agents.append(ref, 'coder', _msg('user', 'q'))
    stored_before = agents.read(ref, 'coder').messages
    conversation = assemble_history(
        switched, ref, 'coder', intent=_read_intent())
    projected = project_for_trace(conversation)
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
    assert [m.content for m in agents.read(ref, 'coder').messages] == [*before, 'boom']


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

    return _view(
        ConversationRef.dm('coder'),
        {'participants': ['coder']},
        tuple(messages),
    )


def test_turnstate_busy_cancel_follow_on_store_ids(switched: Path) -> None:
    ref = _dm(switched, 'coder')
    group = AdminStore(switched).create_group(['coder', 'thyca']).ref
    turns = TurnState()

    hub = turns.claim(group.id)
    assert isinstance(hub, TurnHub)
    with pytest.raises(ConversationBusy):
        turns.claim(group.id)
    status, code, _message = public_turn_error(ConversationBusy(group.id))
    assert (status, code) == (409, 'session_busy')
    assert isinstance(turns.claim(ref.id), TurnHub)
    turns.release(ref.id)
    turns.release(group.id)
    assert turns.hub(group.id) is None

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

    class CancelApp:
        def turn(self, *args, **kwargs):
            raise TurnCancelled()

    items: queue.Queue = queue.Queue()
    bridge_worker(CancelApp(), 's', 'hi', items, {'disconnected': False})  # type: ignore[arg-type] -- reason: intentional invalid-type or test-double argument
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

        return [_json.loads(line.decode('utf-8'))
                for line in self.buf.getvalue().splitlines() if line.strip()]


def _drain(handler: _FakeHandler) -> list[dict]:
    return handler.lines()


def test_pump_stream_pre_accept_errors_are_http_json() -> None:
    handler = _FakeHandler()
    items: queue.Queue = queue.Queue()
    items.put(('failed', ConversationNotFound(Path('/tmp/x'))))
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

    handler = _FakeHandler()
    items = queue.Queue()
    items.put(TurnEvent(type='turn.accepted', bot_id='thyca'))
    items.put(('cancelled', None))
    items.put(SENTINEL)
    pump_stream(handler, items, {'disconnected': False})
    assert _drain(handler)[-1] == {'type': 'turn.cancelled'}

    handler = _FakeHandler()
    items = queue.Queue()
    items.put(TurnEvent(type='turn.accepted', bot_id='thyca'))
    items.put(SENTINEL)
    pump_stream(handler, items, {'disconnected': False},
                app=None, conversation_id='s', model=None)
    lines = _drain(handler)
    assert lines[-1] == {'type': 'turn.failed', 'code': 'chat_unavailable',
                         'message': 'chat unavailable'}


def test_public_store_exports_turn_pipeline_api() -> None:

    for name in ('record_turn_error', 'prepare_retry',
                 'project_for_trace', 'trace_turns_projected'):
        assert callable(getattr(store, name)), name
    assert store.append_turn_error is append_turn_error
