"""Live turn across LLM rounds — webui/ui/chat/js/chat.js handleStreamEvent.

The compact card shows only active tool/skill calls, while reply text
accumulates in one live node and completion reconciles the transcript.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_TUI_TAIL = """return {
  handleStreamEvent, sendTurn, followRunningTurn, finishTurn, cancelTurn,
  renderTranscript, updateCard, card, normalizeBot, getBot, refreshSessions,
  readNdjson, busy, runs, bots, groups,
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
const settle = async () => { for (let i = 0; i < 6; i++) await new Promise(r => setImmediate(r)); };

ui.bots.push(ui.normalizeBot({ id: 'thyca', name: 'Thyca', role: 'bạn', agent_status: { state: 'idle' } }, 0));
doc.querySelector('#team-bot-list').innerHTML = ui.bots.map(ui.card).join('');
ui.updateCard(ui.bots[0]);
const b = ui.bots[0];
ui.selectedBot = 'thyca';
ui.currentSession = 's1';
globalThis.fetch = async (url) => {
  if (url === '/api/sessions') return { ok: true, json: async () => ({ sessions: [] }) };
  return { ok: false, status: 404, json: async () => ({ error: 'no route ' + url }) };
};

const feed = () => b.liveCalls.map(f => ({ kind: f.kind, name: f.name, call_id: f.call_id }));
const node = doc.createElement('article');
node.innerHTML = `<header class="message-byline"><span>Thyca</span><time>đang lắng nghe</time></header><div class="live-copy"></div>`;
doc.querySelector('#message-list').append(node);
const token = { botId: 'thyca', sessionId: 's1', start: Date.now(), node, phase: 'waiting', timers: [], tools: [] };
ui.runs.set('s1', token);
const fire = (ev) => ui.handleStreamEvent(token, ev);
const streamText = () => node.querySelector('.stream-text')?.textContent ?? null;
const streamNodes = () => node.querySelectorAll('.stream-text').length;

// Round 1: thinking and reply never create live calls.
fire({ type: 'turn.accepted' });
fire({ type: 'llm.started', round: 1 });
fire({ type: 'llm.thinking', round: 1, delta: 'round one ' });
fire({ type: 'llm.thinking', round: 1, delta: 'thoughts' });
fire({ type: 'llm.content', round: 1, delta: 'Xin ' });
fire({ type: 'llm.content', round: 1, delta: 'chào' });
result.midWrite = { feed: feed(), streamText: streamText(), streamNodes: streamNodes() };
fire({ type: 'llm.finished', round: 1 });
result.afterRound1 = { feed: feed() };

// A tool appears only while its call is active.
fire({ type: 'tool.started', name: 'bash', call_id: 'b1' });
result.bashActive = feed();
fire({ type: 'tool.finished', name: 'bash', call_id: 'b1' });
result.afterTools1 = { feed: feed() };

// Round 2 thinks only.
fire({ type: 'llm.started', round: 2 });
fire({ type: 'llm.thinking', round: 2, delta: 'round two' });
fire({ type: 'llm.finished', round: 2 });
result.afterRound2 = { feed: feed() };

// Round 3 thinking stays out of calls; content shares the one .stream-text.
fire({ type: 'llm.started', round: 3 });
fire({ type: 'llm.thinking', round: 3, delta: 'round three' });
result.round3Think = { feed: feed() };
fire({ type: 'llm.content', round: 3, delta: 'round three reply' });
result.round3Write = { feed: feed(), streamText: streamText(), streamNodes: streamNodes() };
fire({ type: 'llm.finished', round: 3 });
fire({ type: 'tool.started', name: 'read', call_id: 'r3' });
result.readActive = feed();
fire({ type: 'tool.finished', name: 'read', call_id: 'r3' });
result.finalFeed = { feed: feed() };

fire({ type: 'turn.completed', detail: { id: 's1', running: false, messages: [
  { role: 'assistant', content: 'done s1', meta: { bot_id: 'thyca' } },
] } });
await settle();
result.afterCompleted = {
  feed: feed(),
  listHasDone: doc.querySelector('#message-list').textContent.includes('done s1'),
  tracked: ui.runs.has('s1'),
};

// --- re-follow: the replay stream builds one fresh live node, deltas apply
//     once, and the stored transcript swaps in at turn.completed ---
ui.currentSession = 's2';
const queue = [];
let waiter = null;
const reader = { read: () => new Promise(resolve => {
  if (queue.length) resolve(queue.shift()); else waiter = resolve;
}) };
const push = (line) => {
  const chunk = line === null ? { done: true } : { done: false, value: new TextEncoder().encode(line) };
  if (waiter) { const w = waiter; waiter = null; w(chunk); } else queue.push(chunk);
};
globalThis.fetch = async (url) => {
  if (url.endsWith('/turn/stream')) return { ok: true, status: 200, body: { getReader: () => reader } };
  if (url === '/api/sessions') return { ok: true, json: async () => ({ sessions: [] }) };
  return { ok: true, json: async () => ({ id: 's2', running: false, messages: [] }) };
};
ui.followRunningTurn('s2');
await settle();
push(JSON.stringify({ type: 'turn.accepted' }) + '\n');
push(JSON.stringify({ type: 'llm.content', delta: 'replay-one' }) + '\n');
await settle();
const followNode = doc.querySelector('#message-list .running-response');
result.refollowMid = {
  liveNodes: doc.querySelectorAll('#message-list .running-response').length,
  streamNodes: followNode?.querySelectorAll('.stream-text').length ?? -1,
  streamText: followNode?.querySelector('.stream-text')?.textContent ?? null,
};
push(JSON.stringify({ type: 'turn.completed', detail: { id: 's2', running: false,
  messages: [{ role: 'assistant', content: 'replay-one', meta: { bot_id: 'thyca' } }] } }) + '\n');
push(null);
await settle();
const listText = doc.querySelector('#message-list').textContent;
result.refollowDone = {
  liveNodes: doc.querySelectorAll('#message-list .running-response').length,
  occurrences: listText.split('replay-one').length - 1,
  tracked: ui.runs.has('s2'),
};

// --- stored transcript: one article per assistant message, usage pill per
//     message's tool_calls; reasoning/system/tool/kind-filtered entries out ---
const detail = { messages: [
  { role: 'system', content: 'sys' },
  { role: 'user', content: 'hỏi thời tiết' },
  { role: 'assistant', reasoning: 'User wants sequential: search, reply, check.',
    tool_calls: [{ name: 'tavily-search__web_search' }] },
  { role: 'assistant', tool_calls: [{ name: 'bash' }] },
  { role: 'assistant', content: 'Search xong rồi. Vài tin hôm nay:', meta: { bot_id: 'thyca' } },
  { role: 'assistant', tool_calls: [{ name: 'bash' }] },
  { role: 'assistant', content: 'Nhiệt độ: 45°C — vẫn mát.', meta: { bot_id: 'thyca' } },
  { role: 'assistant', content: 'internal', meta: { kind: 'naming', bot_id: 'thyca' } },
  { role: 'tool', content: 'tool out' },
] };
doc.querySelector('#message-list').innerHTML = ui.renderTranscript(detail);
const articles = [...doc.querySelectorAll('#message-list .message-assistant')];
result.transcript = {
  users: doc.querySelectorAll('#message-list .message-user').length,
  assistants: articles.length,
  pills: articles.map(a => a.querySelector('.usage-pill')?.textContent.trim() ?? null),
  copies: articles.map(a => a.querySelector('.live-copy')?.textContent ?? null),
  hasReasoning: doc.querySelector('#message-list').textContent.includes('User wants sequential'),
};
// --- nested A2A turn: the child bot's deltas (stamped bot_id='thyca')
//     ride the same stream as the primary bot's turn, but must never
//     land in the primary bot's live bubble -- otherwise Thyca's
//     working text shows under march7th's byline until turn.completed.
ui.bots.push(ui.normalizeBot({ id: 'march7th', name: 'march7th', role: 'Bảy', agent_status: { state: 'idle' } }, 1));
ui.selectedBot = 'march7th';
ui.currentSession = 's3';
const node2 = doc.createElement('article');
node2.innerHTML = `<header class="message-byline"><span>march7th</span><time>đang lắng nghe</time></header><div class="live-copy"></div>`;
doc.querySelector('#message-list').append(node2);
const token2 = { botId: 'march7th', sessionId: 's3', start: Date.now(), node: node2, phase: 'waiting', timers: [], tools: [] };
ui.runs.set('s3', token2);
const fire2 = (ev) => ui.handleStreamEvent(token2, ev);
const childBot = ui.bots.find(x => x.id === 'thyca');

fire2({ type: 'llm.started', bot_id: 'thyca', round: 1 });
fire2({ type: 'llm.thinking', bot_id: 'thyca', round: 1, delta: 'chị xem lại giúp Bảy' });
fire2({ type: 'llm.content', bot_id: 'thyca', round: 1, delta: 'Để chị kiểm tra lại share tool cho Bảy ngay.' });
fire2({ type: 'llm.finished', bot_id: 'thyca', round: 1 });
result.nestedChildOnly = {
  streamText: node2.querySelector('.stream-text')?.textContent ?? null,
  byline: node2.querySelector('.message-byline time')?.textContent ?? null,
  mdBuf: token2.mdBuf || '',
};
fire2({ type: 'llm.content', bot_id: 'march7th', round: 1, delta: 'Em kiểm tra liền nè.' });
result.nestedChildFeed = childBot.liveCalls;
result.nestedAfterPrimary = {
  streamText: node2.querySelector('.stream-text')?.textContent ?? null,
  byline: node2.querySelector('.message-byline time')?.textContent ?? null,
  mdBuf: token2.mdBuf || '',
};

console.log(JSON.stringify(result));
"""


@pytest.fixture(scope="module")
def rounds() -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    script = _SCRIPT.replace("TAIL", json.dumps(_TUI_TAIL))
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        cwd=ROOT, check=True, capture_output=True, text=True, timeout=10,
    )
    return json.loads(result.stdout)


def test_round_one_streams_think_and_reply(rounds: dict) -> None:
    assert rounds["midWrite"]["feed"] == []
    assert rounds["midWrite"]["streamText"] == "Xin chào"
    assert rounds["midWrite"]["streamNodes"] == 1
    assert rounds["afterRound1"]["feed"] == []


def test_tool_calls_appear_only_while_running(rounds: dict) -> None:
    assert rounds["bashActive"] == [{"kind": "tool", "name": "bash", "call_id": "b1"}]
    assert rounds["afterTools1"]["feed"] == []
    assert rounds["readActive"] == [{"kind": "tool", "name": "read", "call_id": "r3"}]
    assert rounds["finalFeed"]["feed"] == []


def test_thinking_rounds_do_not_create_calls(rounds: dict) -> None:
    assert rounds["afterRound2"]["feed"] == []
    assert rounds["round3Think"]["feed"] == []


def test_round_two_shares_the_same_stream_text(rounds: dict) -> None:
    assert rounds["round3Write"]["streamText"] == "Xin chàoround three reply"
    assert rounds["round3Write"]["streamNodes"] == 1
    assert rounds["round3Write"]["feed"] == []


def test_completion_clears_calls_and_swaps_in_transcript(rounds: dict) -> None:
    assert rounds["afterCompleted"]["feed"] == []
    assert rounds["afterCompleted"]["listHasDone"] is True
    assert rounds["afterCompleted"]["tracked"] is False


def test_refollow_rebuilds_live_node_without_duplicating(rounds: dict) -> None:
    mid = rounds["refollowMid"]
    assert mid["liveNodes"] == 1
    assert mid["streamNodes"] == 1
    assert mid["streamText"] == "replay-one"
    done = rounds["refollowDone"]
    assert done["liveNodes"] == 0
    assert done["occurrences"] == 1  # only the stored transcript shows it
    assert done["tracked"] is False


def test_nested_child_deltas_stay_out_of_primary_live_bubble(rounds: dict) -> None:
    """Child-bot (A2A) deltas ride the primary turn's stream stamped with
    the child's bot_id; only the primary bot's own text may render into
    the live bubble -- the child's working text must not leak under the
    primary bot's byline."""
    child = rounds["nestedChildOnly"]
    assert child["streamText"] is None
    assert child["mdBuf"] == ""
    assert child["byline"] == "đang lắng nghe"
    primary = rounds["nestedAfterPrimary"]
    assert primary["streamText"] == "Em kiểm tra liền nè."
    assert primary["mdBuf"] == "Em kiểm tra liền nè."


def test_nested_child_text_does_not_create_tool_calls(rounds: dict) -> None:
    assert rounds["nestedChildFeed"] == []


def test_transcript_renders_articles_and_tool_pills(rounds: dict) -> None:
    assert rounds["transcript"]["users"] == 1
    assert rounds["transcript"]["assistants"] == 5
    assert rounds["transcript"]["pills"] == [
        "Đã dùng: tavily-search__web_search",
        "Đã dùng: bash",
        None,
        "Đã dùng: bash",
        None,
    ]
    assert rounds["transcript"]["copies"][2] == "Search xong rồi. Vài tin hôm nay:"
    assert rounds["transcript"]["copies"][4] == "Nhiệt độ: 45°C — vẫn mát."
    # Reasoning is a live-card concern; the stored transcript drops it.
    assert rounds["transcript"]["hasReasoning"] is False
