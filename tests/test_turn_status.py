"""Node tests for live turn status and activity — chat.js handleStreamEvent.

Lifecycle events map to shared bot states, stream text and interruption
notes. Active tool/skill events use call and invocation identities to build
the closed card's grouped preview; thinking is state only, not a task row.
There is no duplicated thought/tool feed on the card.

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
  previewLine, previewExtraCount, livePreviewGroups, openLiveList,
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
const callSnapshot = () => b.liveCalls.map(f => ({
  kind: f.kind, name: f.name, call_id: f.call_id, invocation_id: f.invocation_id,
}));
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
  { type: 'tool.started', name: 'bash', call_id: 'state-call', invocation_id: 'state-invocation' },
  { type: 'tool.finished', name: 'bash', call_id: 'state-call', invocation_id: 'state-invocation' },
  { type: 'llm.content', delta: 'Xin ' }, { type: 'llm.content', delta: 'chào' },
]) { ui.handleStreamEvent(t1, ev); states.push(b.state); }
result.states = states;
result.streamText = t1.node.querySelector('.stream-text')?.textContent ?? null;
result.caret = !!t1.node.querySelector('.streaming-caret');
ui.handleStreamEvent(t1, { type: 'turn.completed', detail: { id: 's1', messages: [] } });
await settle();
result.completed = {
  state: b.state, phase: t1.phase, tracked: ui.runs.has('s1'),
  card: cardLabel(), liveCalls: b.liveCalls.length,
};

// --- real tool/skill events drive grouped compact previews, not feed rows ---
ui.currentSession = 's2';
const t2 = mkToken('s2');
const liveEvents = [
  { type: 'turn.accepted' },
  { type: 'tool.started', name: 'read', call_id: 'read-tool', invocation_id: 'read-tool-1' },
  { type: 'skill.started', name: 'read', call_id: 'read-skill', invocation_id: 'read-skill-1' },
  { type: 'skill.started', name: 'bash', call_id: 'bash-skill', invocation_id: 'bash-skill-1' },
  { type: 'tool.started', name: 'bash', call_id: 'bash-tool-1', invocation_id: 'bash-tool-1' },
  { type: 'tool.started', name: 'bash', call_id: 'bash-tool-2', invocation_id: 'bash-tool-2' },
  // A replay of the first call is idempotent: it does not inflate ×N.
  { type: 'tool.started', name: 'bash', call_id: 'bash-tool-1', invocation_id: 'bash-tool-1' },
];
for (const ev of liveEvents) ui.handleStreamEvent(t2, ev);
const beforeAcceptedReplay = callSnapshot();
ui.handleStreamEvent(t2, { type: 'turn.accepted', bot_id: 'thyca' });
const afterAcceptedReplay = callSnapshot();
const beforeWrongFinish = callSnapshot();
ui.handleStreamEvent(t2, { type: 'tool.finished', name: 'bash', call_id: 'wrong-id', invocation_id: 'bash-tool-1' });
const afterWrongFinish = callSnapshot();
const previewBeforeThinking = ui.previewLine(b);
ui.handleStreamEvent(t2, { type: 'llm.thinking', delta: 'private thought' });
const thinkingCalls = callSnapshot();
const groups = ui.livePreviewGroups(b, 's2');
const moreButton = doc.querySelector('#bot-thyca .live-more');
ui.openLiveList('thyca');
result.livePreview = {
  calls: callSnapshot(),
  groups: { shown: groups.shown.map(g => [g.kind, g.name, g.active]), extra: groups.extra, total: groups.total },
  preview: ui.previewLine(b),
  extraCount: ui.previewExtraCount(b),
  moreText: moreButton?.textContent ?? null,
  moreLabel: moreButton?.getAttribute('aria-label') ?? null,
  acceptedReplayPreservedCalls: JSON.stringify(beforeAcceptedReplay) === JSON.stringify(afterAcceptedReplay),
  wrongFinishPreserved: JSON.stringify(beforeWrongFinish) === JSON.stringify(afterWrongFinish),
  replayDidNotDuplicate: beforeWrongFinish.length === 5,
  thinkingDidNotChangeCalls: JSON.stringify(beforeWrongFinish) === JSON.stringify(thinkingCalls),
  thinkingDidNotChangePreview: previewBeforeThinking === ui.previewLine(b),
  thinkingDidNotCreateTaskRow: doc.querySelectorAll('#bot-thyca .progress-list li').length === 0,
  fullListText: doc.querySelector('#live-list-rows').textContent,
};

// Finishing one duplicate call and then replaying its start must not reopen
// it. Other identities (including the same-name skill) remain independent.
ui.handleStreamEvent(t2, { type: 'tool.finished', name: 'bash', call_id: 'bash-tool-1', invocation_id: 'bash-tool-1' });
ui.handleStreamEvent(t2, { type: 'tool.started', name: 'bash', call_id: 'bash-tool-1', invocation_id: 'bash-tool-1' });
result.afterOneFinishReplay = callSnapshot();
for (const ev of [
  { type: 'tool.finished', name: 'bash', call_id: 'bash-tool-2', invocation_id: 'bash-tool-2' },
  { type: 'skill.finished', name: 'bash', call_id: 'bash-skill', invocation_id: 'bash-skill-1' },
  { type: 'tool.finished', name: 'read', call_id: 'read-tool', invocation_id: 'read-tool-1' },
  { type: 'skill.finished', name: 'read', call_id: 'read-skill', invocation_id: 'read-skill-1' },
]) ui.handleStreamEvent(t2, ev);
result.callsAfterFinishes = callSnapshot();
ui.handleStreamEvent(t2, { type: 'turn.completed', detail: { id: 's2', messages: [] } });
await settle();
result.settledCalls = { liveCalls: b.liveCalls.length, tracked: ui.runs.has('s2') };

// --- terminal interruption notes (was TURN_FAILED_STATUS / SEND_ERROR_STATUS) ---
const t3 = mkToken('s3');
ui.handleStreamEvent(t3, { type: 'turn.cancelled' });
result.cancelled = {
  state: b.state,
  note: t3.node.querySelector('.stream-interruption')?.textContent ?? null,
  byline: t3.node.querySelector('.message-byline time')?.textContent ?? null,
  caret: !!t3.node.querySelector('.streaming-caret'),
  tracked: ui.runs.has('s3'),
};

const t4 = mkToken('s4');
ui.handleStreamEvent(t4, { type: 'turn.failed', message: 'hết hạn mức' });
result.failed = {
  state: b.state, card: cardLabel(),
  note: t4.node.querySelector('.stream-interruption')?.textContent ?? null,
  byline: t4.node.querySelector('.message-byline time')?.textContent ?? null,
  tracked: ui.runs.has('s4'),
};

const t5 = mkToken('s5');
ui.handleStreamEvent(t5, { type: 'turn.failed' });
result.failedDefault = {
  note: t5.node.querySelector('.stream-interruption')?.textContent ?? null,
  tracked: ui.runs.has('s5'),
};

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


def test_completion_settles_state_and_cleans_live_calls(status: dict) -> None:
    assert status["completed"]["state"] == "done"
    assert status["completed"]["phase"] == "settled"
    assert status["completed"]["tracked"] is False
    assert status["completed"]["card"] == "Đã hoàn tất"
    assert status["completed"]["liveCalls"] == 0


def test_live_tool_skill_preview_groups_calls_not_events(status: dict) -> None:
    preview = status["livePreview"]
    assert len(preview["calls"]) == 5  # duplicate replay of bash-tool-1 is idempotent
    assert preview["groups"] == {
        "shown": [["tool", "bash", 2], ["skill", "bash", 1]],
        "extra": 2,
        "total": 4,
    }
    assert preview["preview"] == "bash ×2, skill bash"  # same name, distinct kinds
    assert preview["extraCount"] == 2
    assert preview["moreText"] == "+2"
    assert preview["moreLabel"] == "Xem toàn bộ 4 việc đang chạy của Thyca"
    assert "skill bash" in preview["fullListText"]
    assert "×2" in preview["fullListText"]


def test_wrong_finish_and_thinking_do_not_mutate_live_call_previews(status: dict) -> None:
    preview = status["livePreview"]
    assert preview["acceptedReplayPreservedCalls"] is True
    assert preview["wrongFinishPreserved"] is True
    assert preview["replayDidNotDuplicate"] is True
    assert preview["thinkingDidNotChangeCalls"] is True
    assert preview["thinkingDidNotChangePreview"] is True
    assert preview["thinkingDidNotCreateTaskRow"] is True


def test_finished_calls_disappear_and_replayed_start_stays_settled(status: dict) -> None:
    remaining = status["afterOneFinishReplay"]
    assert len(remaining) == 4
    assert sum(c["kind"] == "tool" and c["name"] == "bash" for c in remaining) == 1
    assert not any(c["call_id"] == "bash-tool-1" for c in remaining)
    assert status["callsAfterFinishes"] == []
    assert status["settledCalls"] == {"liveCalls": 0, "tracked": False}


def test_turn_cancelled_appends_interruption_note(status: dict) -> None:
    assert status["cancelled"] == {
        "state": "idle",
        "note": "Đã dừng lượt trả lời.",
        "byline": "đã dừng",
        "caret": False,
        "tracked": False,
    }


def test_turn_failed_surfaces_event_message(status: dict) -> None:
    assert status["failed"] == {
        "state": "error",
        "card": "Có lỗi xảy ra",
        "note": "hết hạn mức",
        "byline": "gặp lỗi",
        "tracked": False,
    }
    assert status["failedDefault"] == {"note": "Lượt trả lời gặp lỗi.", "tracked": False}


def test_send_failure_marks_turn_and_card_error(status: dict) -> None:
    assert status["sendError"]["state"] == "error"
    assert status["sendError"]["note"] == "Lượt trả lời gặp lỗi: mất kết nối"
    assert status["sendError"]["tracked"] == 0
