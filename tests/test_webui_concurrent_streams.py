"""Concurrent turns across sessions — webui/ui/chat/js/chat.js sendTurn.

Two turns can stream at once (send in A, switch to B, send there): ``runs``
is keyed by sessionId and sendTurn only refuses a second send on the *same*
session. Returning to a session whose turn is still in flight re-joins via
GET turn/stream replay (followRunningTurn) on a fresh live node — the stored
transcript replaces it when the terminal event lands. The old UI's
"reuse the card without a second reader" contract is deliberately replaced
by replay re-follow; this file pins the new one.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_TUI_TAIL = """return {
  sendTurn, loadSession, followRunningTurn, handleStreamEvent, renderTranscript,
  updateCard, card, normalizeBot, getBot, refreshSessions, busy, runs, bots,
  groups, histories,
  get currentSession() { return currentSession; },
  set currentSession(v) { currentSession = v; },
  get selectedBot() { return selectedBot; },
  set selectedBot(v) { selectedBot = v; },
  get run() { return run; },
};"""

_SCRIPT = r"""
import { installDom, loadScripts, CHAT_CHAIN } from './tests/webui_dom.mjs';
const doc = installDom();
const ui = loadScripts(CHAT_CHAIN, TAIL);
const result = {};
const settle = async () => { for (let i = 0; i < 6; i++) await new Promise(r => setImmediate(r)); };

ui.bots.push(ui.normalizeBot({ id: 'thyca', name: 'Thyca', role: 'bạn', agent_status: { state: 'idle' } }, 0));
doc.querySelector('#team-bot-list').innerHTML = ui.bots.map(ui.card).join('');
ui.updateCard(ui.bots[0]);
ui.selectedBot = 'thyca';
ui.currentSession = 'A';

// --- fetch routing: POST turn/stream (A held open, B completes),
//     GET turn/stream (follow replay), GET session detail + list. ---
const posts = [], follows = [];
const queueA = [];
let waiterA = null;
const readerA = {
  read: () => new Promise(resolve => {
    if (queueA.length) resolve(queueA.shift());
    else waiterA = resolve;
  }),
};
const pushA = (line) => {
  const chunk = { done: false, value: new TextEncoder().encode(line) };
  if (waiterA) { const w = waiterA; waiterA = null; w(chunk); } else queueA.push(chunk);
};
const ndjsonBody = (events) => {
  const bytes = new TextEncoder().encode(events.map(JSON.stringify).join('\n') + '\n');
  let sent = false;
  return { getReader: () => ({ read: async () => (sent ? { done: true } : (sent = true, { done: false, value: bytes })) }) };
};
const sessions = {
  A: { id: 'A', title: 'Phiên A', running: false, messages: [] },
  B: { id: 'B', title: 'Phiên B', running: false, messages: [] },
};
globalThis.fetch = async (url, opts = {}) => {
  const method = opts.method || 'GET';
  if (method === 'POST' && url.endsWith('/turn/stream')) {
    posts.push(url);
    if (url.includes('/A/')) return { ok: true, status: 200, body: { getReader: () => readerA } };
    return { ok: true, status: 200, body: ndjsonBody([
      { type: 'turn.accepted' },
      { type: 'llm.content', delta: 'B đang trả lời' },
      { type: 'turn.completed', detail: { id: 'B', running: false,
        messages: [{ role: 'assistant', content: 'done B', meta: { bot_id: 'thyca' } }] } },
    ]) };
  }
  if (method === 'GET' && url.endsWith('/turn/stream')) {
    follows.push(url);
    return { ok: true, status: 200, body: ndjsonBody([
      { type: 'turn.accepted' },
      { type: 'llm.content', delta: 'A vẫn đang viết' },
      { type: 'turn.completed', detail: { id: 'A', running: false,
        messages: [{ role: 'assistant', content: 'done A', meta: { bot_id: 'thyca' } }] } },
    ]) };
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

// 1. Send in A; the POST stream stays open mid-turn.
await ui.loadSession('A');
await settle();
const pendingA = ui.sendTurn('hi A');
await settle();
pushA(JSON.stringify({ type: 'turn.accepted' }) + '\n');
pushA(JSON.stringify({ type: 'llm.content', delta: 'A-one ' }) + '\n');
await settle();
result.sentA = {
  posts: posts.length,
  tracked: [...ui.runs.keys()],
  phase: ui.runs.get('A')?.phase,
  streamText: doc.querySelector('#message-list .stream-text')?.textContent ?? null,
  stopShown: doc.querySelector('#stop-button').hidden === false,
};
const tokenA = ui.runs.get('A');

// 2. Switch to B and send there: both turns stay tracked under their own key.
await ui.loadSession('B');
await settle();
await ui.sendTurn('hi B');
await settle();
result.sentB = {
  posts: posts.length,
  tracked: [...ui.runs.keys()],
  current: ui.currentSession,
  listHasDoneB: listText().includes('done B'),
};

// 3. Back to A while still running: a replay reader re-joins the turn —
//    no second POST, the runs token is replaced by the follow token.
sessions.A = { ...sessions.A, running: true };
await ui.loadSession('A');
await settle();
result.backToA = {
  follows: follows.length,
  posts: posts.length,
  replacedToken: ui.runs.get('A') !== tokenA,
};
await settle();
result.settledA = {
  tracked: [...ui.runs.keys()],
  listHasDoneA: listText().includes('done A'),
  liveNodes: doc.querySelectorAll('#message-list .running-response').length,
};
// Close the orphaned POST reader so nothing dangles.
if (waiterA) waiterA({ done: true });
await pendingA.catch(() => {});
console.log(JSON.stringify(result));
"""


@pytest.fixture(scope="module")
def concurrent() -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    script = _SCRIPT.replace("TAIL", json.dumps(_TUI_TAIL))
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        cwd=ROOT, check=True, capture_output=True, text=True, timeout=10,
    )
    return json.loads(result.stdout)


def test_two_sessions_stream_at_once(concurrent: dict) -> None:
    """runs keys by sessionId: the send in A stays tracked while B runs."""
    assert concurrent["sentA"]["tracked"] == ["A"]
    assert concurrent["sentA"]["phase"] == "writing"
    assert concurrent["sentA"]["streamText"] == "A-one "
    assert concurrent["sentA"]["stopShown"] is True
    assert concurrent["sentB"]["posts"] == 2
    assert concurrent["sentB"]["tracked"] == ["A"]
    assert concurrent["sentB"]["listHasDoneB"] is True


def test_returning_to_running_session_refollows_via_replay(concurrent: dict) -> None:
    """New contract: loadSession on a running session opens GET turn/stream
    (replay) once — never a second POST — and the follow token takes over."""
    assert concurrent["backToA"]["follows"] == 1
    assert concurrent["backToA"]["posts"] == 2
    assert concurrent["backToA"]["replacedToken"] is True


def test_replayed_terminal_renders_stored_transcript(concurrent: dict) -> None:
    assert concurrent["settledA"]["tracked"] == []
    assert concurrent["settledA"]["listHasDoneA"] is True
    assert concurrent["settledA"]["liveNodes"] == 0
