"""Node tests for live turn status — webui/ui/chat/js/chat.js handleStreamEvent.

The old chat-status.js helpers (brandState, SEND_ERROR_STATUS, usageLine) are
gone with the UI rebuild. The equivalents now live in the classic-script
chain: handleStreamEvent maps NDJSON events to shared bot states via setState
(sidebar card), interruption copy is passed inline to cancelTurn as a
.stream-interruption note, and the tool-usage tally renders as a 'đã dùng …'
feed entry on the bot card instead of a usage row on the message. Running
calls say 'đang dùng'; only settled calls say 'đã dùng'.

Runs the real script chain against the real chat page markup (tests/
webui_dom.mjs); only fetch is stubbed.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_TUI_TAIL = """return {
  handleStreamEvent, sendTurn, cancelTurn, finishTurn, renderTranscript,
  setState, updateCard, card, normalizeBot, getBot, refreshSessions,
  loadSession, followRunningTurn, readNdjson, busy, runs, bots, groups,
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
globalThis.fetch = async () => { throw new Error('unstubbed fetch'); };

const cardLabel = () => doc.querySelector('#bot-thyca .header-status-label').textContent;
const feed = () => b.feed.filter(f => !f.exiting).map(f => ({ kind: f.kind, text: f.text, done: !!f.done }));
const mkToken = (sessionId) => {
  const node = doc.createElement('article');
  node.innerHTML = `<header class="message-byline"><span>Thyca</span><time>đang lắng nghe</time></header><div class="live-copy"></div>`;
  const token = { botId: 'thyca', sessionId, start: Date.now(), node, phase: 'waiting', timers: [], tools: [] };
  ui.runs.set(sessionId, token);
  return token;
};

// --- stream event -> bot state (was brandState) ---
const t1 = mkToken('s1');
const states = [];
for (const ev of [
  { type: 'turn.accepted' }, { type: 'llm.started' },
  { type: 'tool.started', name: 'bash' }, { type: 'tool.finished', name: 'bash' },
  { type: 'llm.content', delta: 'Xin ' }, { type: 'llm.content', delta: 'chào' },
]) { ui.handleStreamEvent(t1, ev); states.push(b.state); }
result.states = states;
result.streamText = t1.node.querySelector('.stream-text')?.textContent ?? null;
result.caret = !!t1.node.querySelector('.streaming-caret');
ui.handleStreamEvent(t1, { type: 'turn.completed', detail: { id: 's1', messages: [] } });
await settle();
result.completed = { state: b.state, phase: t1.phase, tracked: ui.runs.has('s1'), card: cardLabel(), feed: feed() };

// --- tool usage folds into one feed line (was usageLine) ---
const t2 = mkToken('s2');
ui.handleStreamEvent(t2, { type: 'turn.accepted' });
ui.handleStreamEvent(t2, { type: 'tool.started', name: 'bash' });
ui.handleStreamEvent(t2, { type: 'tool.started', name: 'bash' });
ui.handleStreamEvent(t2, { type: 'tool.started', name: 'create-skill' });
result.usageRunning = feed();
ui.handleStreamEvent(t2, { type: 'tool.finished', name: 'bash' });
ui.handleStreamEvent(t2, { type: 'tool.finished', name: 'bash' });
ui.handleStreamEvent(t2, { type: 'tool.finished', name: 'create-skill' });
result.usageSettled = feed();
// A tools line after a thinking row starts a fresh block below it.
ui.handleStreamEvent(t2, { type: 'llm.thinking' });
ui.handleStreamEvent(t2, { type: 'tool.started', name: 'read' });
result.usageAfterThink = feed();
ui.handleStreamEvent(t2, { type: 'turn.completed', detail: { id: 's2', messages: [] } });
await settle();

// --- terminal interruption notes (was TURN_FAILED_STATUS / SEND_ERROR_STATUS) ---
const t3 = mkToken('s3');
ui.handleStreamEvent(t3, { type: 'turn.cancelled' });
result.cancelled = {
  state: b.state,
  note: t3.node.querySelector('.stream-interruption')?.textContent ?? null,
  byline: t3.node.querySelector('.message-byline time')?.textContent ?? null,
  caret: !!t3.node.querySelector('.streaming-caret'),
};

const t4 = mkToken('s4');
ui.handleStreamEvent(t4, { type: 'turn.failed', message: 'hết hạn mức' });
result.failed = {
  state: b.state, card: cardLabel(),
  note: t4.node.querySelector('.stream-interruption')?.textContent ?? null,
  byline: t4.node.querySelector('.message-byline time')?.textContent ?? null,
};

const t5 = mkToken('s5');
ui.handleStreamEvent(t5, { type: 'turn.failed' });
result.failedDefault = t5.node.querySelector('.stream-interruption')?.textContent ?? null;

// --- send failure surfaces on the live node + error state ---
b.state = 'idle';
ui.currentSession = 's6';
globalThis.fetch = async () => { throw new Error('mất kết nối'); };
await ui.sendTurn('xin chào');
await settle();
const failedNode = doc.querySelector('#message-list .message-assistant');
result.sendError = {
  state: b.state,
  note: failedNode?.querySelector('.stream-interruption')?.textContent ?? null,
  tracked: ui.runs.size,
};

console.log(JSON.stringify(result));
"""


@pytest.fixture(scope="module")
def status() -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    script = _SCRIPT.replace("TAIL", json.dumps(_TUI_TAIL))
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        cwd=ROOT, check=True, capture_output=True, text=True, timeout=10,
    )
    return json.loads(result.stdout)


def test_stream_events_drive_bot_states(status: dict) -> None:
    """turn.accepted→waiting, llm.*→thinking, tool.*→working/thinking,
    llm.content→writing — the shared states the bot card renders."""
    assert status["states"] == ["waiting", "thinking", "working", "thinking", "writing", "writing"]
    assert status["streamText"] == "Xin chào"
    assert status["caret"] is True


def test_completion_settles_state_feed_and_card(status: dict) -> None:
    assert status["completed"]["state"] == "done"
    assert status["completed"]["phase"] == "settled"
    assert status["completed"]["tracked"] is False
    assert status["completed"]["card"] == "Đã hoàn tất"
    assert status["completed"]["feed"] == [
        {"kind": "tools", "text": "đã dùng bash", "done": True},
        {"kind": "write", "text": "đã gửi phản hồi", "done": True},
    ]


def test_tool_usage_folds_into_one_running_line(status: dict) -> None:
    assert status["usageRunning"] == [
        {"kind": "tools", "text": "đang dùng bash · 2 lần, create-skill", "done": False}
    ]


def test_tool_line_marks_done_when_calls_settle(status: dict) -> None:
    assert status["usageSettled"] == [
        {"kind": "tools", "text": "đã dùng bash · 2 lần, create-skill", "done": True}
    ]


def test_tools_after_thinking_start_a_fresh_line(status: dict) -> None:
    assert status["usageAfterThink"] == [
        {"kind": "tools", "text": "đã dùng bash · 2 lần, create-skill", "done": True},
        {"kind": "think", "text": "đang suy nghĩ", "done": False},
        {"kind": "tools", "text": "đang dùng read", "done": False},
    ]


def test_turn_cancelled_appends_interruption_note(status: dict) -> None:
    assert status["cancelled"] == {
        "state": "idle",
        "note": "Đã dừng lượt trả lời.",
        "byline": "đã dừng",
        "caret": False,
    }


def test_turn_failed_surfaces_event_message(status: dict) -> None:
    assert status["failed"] == {
        "state": "error",
        "card": "Có lỗi xảy ra",
        "note": "hết hạn mức",
        "byline": "gặp lỗi",
    }
    assert status["failedDefault"] == "Lượt trả lời gặp lỗi."


def test_send_failure_marks_turn_and_card_error(status: dict) -> None:
    assert status["sendError"]["state"] == "error"
    assert status["sendError"]["note"] == "Lượt trả lời gặp lỗi: mất kết nối"
    assert status["sendError"]["tracked"] == 0
