"""Question/error/consent card rendering + answer wiring in webui/ui/chat.

Backend posts structured cards on assistant rows (meta.kind='card',
meta.card={type:'question'|'error',...}; DM consent cards add
card.private_followup). The rebuilt chat client renders them as titled
cards with clickable chips: question/error chips send the visible label
as a normal turn via the composer send path; consent chips POST the
option id to this DM's /private-followup endpoint. Chips disable once a
later user message exists (the answer), so nothing double-submits.

Replaces the old removal-pin: the wiring now exists, so this file pins
the NEW contract instead of the gap.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "thyca" / "webui" / "ui"

_TUI_TAIL = """return {
  renderTranscript, normalizeBot, getBot, bots, groups,
  answerCardLabel, decidePrivateFollowup, runs,
  setSession: v => { currentSession = v; },
};"""

_RENDER_SCRIPT = r"""
import { installDom, loadScripts, CHAT_CHAIN } from './tests/webui_dom.mjs';
const doc = installDom();
const ui = loadScripts(CHAT_CHAIN, TAIL);
const result = {};

ui.bots.push(ui.normalizeBot({ id: 'thyca', name: 'Thyca', role: 'bạn', agent_status: { state: 'idle' } }, 0));

// Room question card: ask_user posts content:"" with the full payload in meta.
const roomDetail = { messages: [
  { role: 'user', content: 'triển khai giúp mình' },
  { role: 'assistant', content: '',
    meta: { bot_id: 'thyca', kind: 'card', card: {
      type: 'question', title: 'Chọn cách "triển" khai', subtitle: 'Pick <i>one</i>',
      options: ['A', { id: 'b2', label: 'B & C' }, 'Dài "quá"'] } } },
] };
doc.querySelector('#message-list').innerHTML = ui.renderTranscript(roomDetail);
const q = doc.querySelector('#message-list .card-question');
result.question = {
  sections: doc.querySelectorAll('#message-list .card-question').length,
  title: q?.querySelector('h3')?.textContent ?? null,
  sub: q?.querySelector('.card-sub')?.textContent ?? null,
  // Quotes in the title must not break out of the aria-label attribute:
  // the parsed value is exactly the raw title text.
  aria: q?.getAttribute('aria-label') ?? null,
  chips: [...doc.querySelectorAll('#message-list .card-choice')].map(
    b => [b.textContent, b.getAttribute('data-card-label'), b.getAttribute('data-card-bot'), b.getAttribute('disabled')]),
};

// Same card with an answering user message after it: chips must disable.
const answeredDetail = { messages: [...roomDetail.messages,
  { role: 'user', content: 'A' }] };
doc.querySelector('#message-list').innerHTML = ui.renderTranscript(answeredDetail);
result.answered = {
  chips: [...doc.querySelectorAll('#message-list .card-choice')].map(
    b => b.getAttribute('disabled')),
};

// Error card: report_error posts the summary as content + progress/choices.
const errorDetail = { messages: [
  { role: 'assistant', content: 'Kẹt ở bước 2',
    meta: { bot_id: 'thyca', kind: 'card', card: {
      type: 'error', progress: [{ label: 'Bước 1', state: 'done' }, { label: 'Bước 2', state: 'stuck' }, 'Bước 3'],
      hint: 'Chọn hướng đi', choices: ['Thử lại', 'Bỏ qua'] } } },
] };
doc.querySelector('#message-list').innerHTML = ui.renderTranscript(errorDetail);
const e = doc.querySelector('#message-list .card-error');
result.error = {
  sections: doc.querySelectorAll('#message-list .card-error').length,
  lead: doc.querySelector('#message-list .message-assistant .live-copy')?.textContent ?? null,
  steps: [...doc.querySelectorAll('#message-list .card-progress-row')].map(
    li => li.textContent),
  chips: [...doc.querySelectorAll('#message-list .card-choice')].map(
    b => [b.textContent, b.getAttribute('data-card-label'), b.getAttribute('disabled')]),
};

// DM consent card: content repeats the title; chips carry option ids.
const dmDetail = { messages: [
  { role: 'assistant', content: 'Cho phép gửi bản này về nhóm?',
    meta: { bot_id: 'thyca', kind: 'card', card: {
      type: 'question', title: 'Cho phép gửi bản này về nhóm?',
      private_followup: { pending_id: 'pf-9', revision: 2 },
      options: [
        { id: 'approve', label: 'Cho phép gửi bản này' },
        { id: 'keep_private', label: 'Giữ riêng' },
        { id: 'cancel', label: 'Huỷ yêu cầu' },
      ] } } },
] };
doc.querySelector('#message-list').innerHTML = ui.renderTranscript(dmDetail);
const d = doc.querySelector('#message-list .card-question');
result.consent = {
  sections: doc.querySelectorAll('#message-list .card-question').length,
  h3Count: doc.querySelectorAll('#message-list .card-question h3').length,
  leadParas: doc.querySelectorAll('#message-list .message-assistant .live-copy > p').length,
  chips: [...doc.querySelectorAll('#message-list .card-choice')].map(
    b => [b.textContent, b.getAttribute('data-pf-choice'), b.getAttribute('data-pending-id'), b.getAttribute('data-revision'), b.getAttribute('disabled')]),
};

// Unknown card types fall through to the plain assistant row, never crash.
const oddDetail = { messages: [
  { role: 'assistant', content: 'nội dung thường',
    meta: { bot_id: 'thyca', kind: 'card', card: { type: 'poll', options: ['x'] } } },
] };
doc.querySelector('#message-list').innerHTML = ui.renderTranscript(oddDetail);
result.unknown = {
  sections: doc.querySelectorAll('#message-list .user-card').length,
  copy: doc.querySelector('#message-list .message-assistant .live-copy')?.textContent ?? null,
};
console.log(JSON.stringify(result));
"""

_ACTION_SCRIPT = r"""
import { installDom, loadScripts, CHAT_CHAIN } from './tests/webui_dom.mjs';
const doc = installDom();
const ui = loadScripts(CHAT_CHAIN, TAIL);
const result = {};
ui.bots.push(ui.normalizeBot({ id: 'thyca', name: 'Thyca', role: 'bạn', agent_status: { state: 'idle' } }, 0));

// Guards: nothing may send without a session, a label, or while a turn runs.
ui.setSession('');
result.noSession = ui.answerCardLabel('A', null);
ui.setSession('s1');
result.emptyLabel = ui.answerCardLabel('   ', null);
ui.runs.set('s1', {});
result.busyRoom = ui.answerCardLabel('A', null);
result.busyConsent = await ui.decidePrivateFollowup('pf-9', 2, 'approve', null);
ui.runs.delete('s1');

// Consent group: failed POST re-enables the whole chip group.
const DM = { role: 'assistant', content: 'Cho phép gửi bản này về nhóm?',
  meta: { bot_id: 'thyca', kind: 'card', card: {
    type: 'question', title: 'Cho phép gửi bản này về nhóm?',
    private_followup: { pending_id: 'pf-9', revision: 2 },
    options: [{ id: 'approve', label: 'Cho phép' }] } } };
doc.querySelector('#message-list').innerHTML = ui.renderTranscript({ messages: [DM] });
const btns = [...doc.querySelectorAll('#message-list .card-choice')];
result.chipCount = btns.length;
globalThis.fetch = async (url, opts = {}) => {
  if ((opts.method || 'GET') === 'POST') return { ok: false, json: async () => ({ error: 'stale' }) };
  throw new Error('no GET expected on failure');
};
ui.setSession('dm1');
result.failed = await ui.decidePrivateFollowup('pf-9', 2, 'approve', btns[0]);
result.restored = btns.every(b => b.disabled === false);
// Successful POST: option id goes out, card stays disabled via the
// decided set — the backend never echoes a user row, so the fresh
// detail below carries the card alone and chips must still disable.
const calls = [];
globalThis.fetch = async (url, opts = {}) => {
  calls.push({ url: String(url), method: opts.method || 'GET',
               body: opts.body ? JSON.parse(opts.body) : null });
  if ((opts.method || 'GET') === 'POST') return { ok: true, json: async () => ({ ok: true }) };
  return { ok: true, json: async () => ({ messages: [DM] }) };
};
ui.setSession('dm1');
result.decided = await ui.decidePrivateFollowup('pf-9', 2, 'approve', btns[0]);
result.post = calls.find(c => c.method === 'POST') ?? null;
result.rerendered = [...doc.querySelectorAll('#message-list .card-choice')].map(b => b.getAttribute('disabled'));
console.log(JSON.stringify(result));
"""


@pytest.fixture(scope="module")
def node() -> str:
    binary = shutil.which("node")
    if binary is None:
        pytest.skip("node not installed")
    return binary


def _run_script(node: str, script: str) -> dict:
    script = script.replace("TAIL", json.dumps(_TUI_TAIL))
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        check=True, capture_output=True, text=True, cwd=ROOT,
    )
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def card_render(node: str) -> dict:
    return _run_script(node, _RENDER_SCRIPT)


@pytest.fixture(scope="module")
def card_actions(node: str) -> dict:
    return _run_script(node, _ACTION_SCRIPT)


def test_new_card_wiring_exists_in_ui() -> None:
    """The consent/question wiring is back: chat.js renders cards from
    meta.card and answers via the composer send path (labels) and the
    DM private-followup endpoint (consent ids). The OLD module names
    stay gone — this is a fresh implementation, not the old port."""
    blob = "\n".join(
        path.read_text(encoding="utf-8")
        for path in UI.rglob("*")
        if path.is_file() and path.suffix in {".js", ".html"}
    )
    assert "data-card-label" in blob
    assert "data-pf-choice" in blob
    assert "answerCardLabel" in blob
    assert "decidePrivateFollowup" in blob
    assert "private-followup" in blob
    assert "questionCard" not in blob
    assert "team-choice" not in blob
    assert "sendChoiceText" not in blob


def test_room_question_card_renders_title_and_label_chips(card_render: dict) -> None:
    """ask_user rows (content:'') render the card, not an empty bubble."""
    q = card_render["question"]
    assert q["sections"] == 1
    assert q["title"] == 'Chọn cách "triển" khai'
    assert q["sub"] == "Pick <i>one</i>"
    assert q["aria"] == 'Chọn cách "triển" khai'
    assert q["chips"] == [
        ["A", "A", "thyca", None],
        ["B & C", "B & C", "thyca", None],
        ['Dài "quá"', 'Dài "quá"', "thyca", None],
    ]


def test_answered_card_disables_chips(card_render: dict) -> None:
    """A user message after the card is the answer: chips disable."""
    assert card_render["answered"]["chips"] == ["", "", ""]


def test_error_card_renders_progress_and_choices(card_render: dict) -> None:
    err = card_render["error"]
    assert err["sections"] == 1
    assert err["lead"] is not None and "Kẹt ở bước 2" in err["lead"]
    assert err["steps"] == ["✓Bước 1", "!Bước 2", "○Bước 3"]
    assert err["chips"] == [
        ["Thử lại", "Thử lại", None],
        ["Bỏ qua", "Bỏ qua", None],
    ]


def test_dm_consent_card_posts_option_ids(card_render: dict) -> None:
    """Consent cards render the title once and hook chips to option ids."""
    c = card_render["consent"]
    assert c["sections"] == 1
    assert c["h3Count"] == 1 and c["leadParas"] == 0
    assert c["chips"] == [
        ["Cho phép gửi bản này", "approve", "pf-9", "2", None],
        ["Giữ riêng", "keep_private", "pf-9", "2", None],
        ["Huỷ yêu cầu", "cancel", "pf-9", "2", None],
    ]


def test_unknown_card_type_falls_back_to_plain_row(card_render: dict) -> None:
    assert card_render["unknown"]["sections"] == 0
    assert card_render["unknown"]["copy"] == "nội dung thường"


def test_label_answers_are_guarded(card_actions: dict) -> None:
    """No session / no label / busy room → nothing sends, no network."""
    assert card_actions["noSession"] is False
    assert card_actions["emptyLabel"] is False
    assert card_actions["busyRoom"] is False
    assert card_actions["busyConsent"] is False


def test_consent_posts_id_and_rerenders_answered(card_actions: dict) -> None:
    """Consent answers POST {pending_id, revision, choice id} to this DM's
    endpoint. The backend never echoes a user row, so the fresh detail
    carries the card alone — chips stay disabled via the decided set,
    and a failed POST re-enables the whole group."""
    assert card_actions["chipCount"] == 1
    assert card_actions["failed"] is False
    assert card_actions["restored"] is True
    assert card_actions["decided"] is True
    post = card_actions["post"]
    assert post["url"].endswith("/api/sessions/dm1/private-followup")
    assert post["body"] == {"pending_id": "pf-9", "revision": 2, "choice": "approve"}
    assert card_actions["rerendered"] == [""]
