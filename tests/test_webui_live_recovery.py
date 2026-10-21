"""Live-turn recovery — webui/ui/chat/js/chat.js followRunningTurn.

The old UI polled GET /api/sessions/<id> on a timer when the follow stream
dropped and kept the live card alive across navigation. The rebuilt UI has
no poll fallback: recovery is the replay stream itself. Entering a session
whose detail says running:true appends one .running-response node and opens
GET turn/stream; if that stream ends without a terminal event, finishTurn
re-fetches the session and swaps in the stored transcript (unsaved deltas
are discarded); re-entering a still-running session later opens a fresh
replay on a new token — nothing is reused.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_TUI_TAIL = """return {
  handleStreamEvent, sendTurn, loadSession, followRunningTurn, finishTurn,
  cancelTurn, renderTranscript, updateCard, card, normalizeBot, getBot,
  refreshSessions, readNdjson, busy, runs, bots, groups,
  get currentSession() { return currentSession; },
  set currentSession(v) { currentSession = v; },
  get selectedBot() { return selectedBot; },
  set selectedBot(v) { selectedBot = v; },
};"""

_SCRIPT = r"""
import { installDom, loadScripts, CHAT_CHAIN } from './tests/webui_dom.mjs';
const doc = installDom();
const ui = loadScripts(CHAT_CHAIN, TAIL);
const result = {};
const settle = async () => { for (let i = 0; i < 8; i++) await new Promise(r => setImmediate(r)); };

ui.bots.push(ui.normalizeBot({ id: 'thyca', name: 'Thyca', role: 'bạn', agent_status: { state: 'idle' } }, 0));
doc.querySelector('#team-bot-list').innerHTML = ui.bots.map(ui.card).join('');
ui.updateCard(ui.bots[0]);
ui.selectedBot = 'thyca';

// One controllable replay reader per GET turn/stream call.
const follows = [];
const mkReader = () => {
  const queue = []; let waiter = null;
  const ctl = {
    reader: { read: () => new Promise(res => queue.length ? res(queue.shift()) : (waiter = res)) },
    push(line) {
      const chunk = line === null ? { done: true } : { done: false, value: new TextEncoder().encode(line) };
      if (waiter) { const w = waiter; waiter = null; w(chunk); } else queue.push(chunk);
    },
  };
  follows.push(ctl);
  return ctl;
};
const sessions = {
  A: { id: 'A', title: 'Phiên A', running: true, messages: [
    { role: 'user', content: 'hi' },
    { role: 'assistant', content: 'saved first round', meta: { bot_id: 'thyca' } },
  ] },
  B: { id: 'B', title: 'Phiên B', running: false, messages: [
    { role: 'assistant', content: 'b transcript', meta: { bot_id: 'thyca' } },
  ] },
};
globalThis.fetch = async (url, opts = {}) => {
  const method = opts.method || 'GET';
  if (method === 'GET' && url.endsWith('/turn/stream')) {
    const ctl = mkReader();
    return { ok: true, status: 200, body: { getReader: () => ctl.reader } };
  }
  if (method === 'GET' && url === '/api/sessions') {
    return { ok: true, json: async () => ({ sessions: [] }) };
  }
  if (method === 'GET' && url.startsWith('/api/sessions/')) {
    const id = decodeURIComponent(url.split('/').pop());
    return { ok: true, json: async () => sessions[id] };
  }
  return { ok: false, status: 404, json: async () => ({ error: 'no route ' + url }) };
};
const listText = () => doc.querySelector('#message-list').textContent;
const liveNodes = () => doc.querySelectorAll('#message-list .running-response').length;
const occurrences = (needle) => listText().split(needle).length - 1;

// 1. Enter a running session: stored transcript renders first, then a single
//    follow node attaches on top of it.
await ui.loadSession('A');
await settle();
result.entered = {
  follows: follows.length,
  tracked: ui.runs.has('A'),
  liveNodes: liveNodes(),
  savedVisible: listText().includes('saved first round'),
};

// 2. Replay streams live events into the fresh node.
follows[0].push(JSON.stringify({ type: 'turn.accepted' }) + '\n');
follows[0].push(JSON.stringify({ type: 'llm.content', delta: 'mid-turn text' }) + '\n');
await settle();
result.streaming = {
  streamText: doc.querySelector('#message-list .running-response .stream-text')?.textContent ?? null,
  phase: ui.runs.get('A')?.phase,
};

// 3. The replay stream drops mid-turn with no terminal event: finishTurn
//    re-fetches the session and restores the stored transcript — the
//    unsaved streamed text is gone, and nothing re-polls or re-follows.
sessions.A = { ...sessions.A, messages: [
  ...sessions.A.messages,
  { role: 'assistant', content: 'saved second round', meta: { bot_id: 'thyca' } },
] };
follows[0].push(null);   // server closed the stream mid-turn
await settle();
result.dropped = {
  follows: follows.length,
  tracked: ui.runs.has('A'),
  liveNodes: liveNodes(),
  occurrencesMid: occurrences('mid-turn text'),
  savedVisible: listText().includes('saved first round'),
  savedSecondVisible: listText().includes('saved second round'),
};

// 4. Leave for B (no follow — B is not running), then return to A while it
//    is still running: a brand-new replay opens on a fresh token.
await ui.loadSession('B');
await settle();
result.inB = { follows: follows.length, tracked: [...ui.runs.keys()], bVisible: listText().includes('b transcript') };
await ui.loadSession('A');
await settle();
result.returned = {
  follows: follows.length,
  tracked: ui.runs.has('A'),
  liveNodes: liveNodes(),
  savedSecondVisible: listText().includes('saved second round'),
};

// 5. The re-joined turn completes on the second replay: stored transcript
//    renders and every live structure is released.
sessions.A = { ...sessions.A, running: false };
follows[1].push(JSON.stringify({ type: 'turn.accepted' }) + '\n');
follows[1].push(JSON.stringify({ type: 'llm.content', delta: 'tail' }) + '\n');
follows[1].push(JSON.stringify({ type: 'turn.completed', detail: { id: 'A', running: false,
  messages: [...sessions.A.messages, { role: 'assistant', content: 'done A', meta: { bot_id: 'thyca' } }] } }) + '\n');
follows[1].push(null);
await settle();
result.stopped = {
  tracked: ui.runs.has('A'),
  liveNodes: liveNodes(),
  doneVisible: listText().includes('done A'),
  cardState: ui.bots[0].state,
};
console.log(JSON.stringify(result));
"""


@pytest.fixture(scope="module")
def recovery() -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    script = _SCRIPT.replace("TAIL", json.dumps(_TUI_TAIL))
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        cwd=ROOT, check=True, capture_output=True, text=True, timeout=10,
    )
    return json.loads(result.stdout)


def test_running_session_renders_transcript_then_one_follow_node(recovery: dict) -> None:
    assert recovery["entered"]["follows"] == 1
    assert recovery["entered"]["tracked"] is True
    assert recovery["entered"]["liveNodes"] == 1
    assert recovery["entered"]["savedVisible"] is True


def test_replay_streams_into_the_follow_node(recovery: dict) -> None:
    assert recovery["streaming"]["streamText"] == "mid-turn text"
    assert recovery["streaming"]["phase"] == "writing"


def test_dropped_stream_recovers_to_stored_transcript(recovery: dict) -> None:
    """No poll fallback exists in the new UI: when the replay stream ends
    mid-turn, finishTurn swaps the live node for the freshest stored
    transcript once — streamed-but-unsaved text does not survive."""
    assert recovery["dropped"]["follows"] == 1
    assert recovery["dropped"]["tracked"] is False
    assert recovery["dropped"]["liveNodes"] == 0
    assert recovery["dropped"]["occurrencesMid"] == 0
    assert recovery["dropped"]["savedVisible"] is True
    assert recovery["dropped"]["savedSecondVisible"] is True


def test_reentering_running_session_opens_fresh_replay(recovery: dict) -> None:
    """The old 'reuse the same live card' contract is gone — every entry into
    a running session starts a new replay with a new live node."""
    assert recovery["inB"]["follows"] == 1
    assert recovery["inB"]["tracked"] == []
    assert recovery["inB"]["bVisible"] is True
    assert recovery["returned"]["follows"] == 2
    assert recovery["returned"]["tracked"] is True
    assert recovery["returned"]["liveNodes"] == 1
    assert recovery["returned"]["savedSecondVisible"] is True


def test_terminal_event_releases_everything(recovery: dict) -> None:
    assert recovery["stopped"]["tracked"] is False
    assert recovery["stopped"]["liveNodes"] == 0
    assert recovery["stopped"]["doneVisible"] is True
    assert recovery["stopped"]["cardState"] == "done"
