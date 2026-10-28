"""Bot<->bot exchange UI: room collapse + DM group entries + thread modal.

Render layer only (node harness): the exchanges/thread endpoints are
covered by tests/test_bot_exchanges.py. Click dispatch cannot run here
(the harness stubs addEventListener); the entry buttons carry the
partner hook the app click path reads, and the modal is exercised by
calling openExchangeModal directly.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_TUI_TAIL = """return {
  fmtTime,
  renderTranscript, renderExchangeGroups, openExchangeModal,
  normalizeBot, getBot, bots, groups,
  setSession: v => { currentSession = v; },
};"""

_SCRIPT = r"""
import { installDom, loadScripts, CHAT_CHAIN } from './tests/webui_dom.mjs';
const doc = installDom();
const ui = loadScripts(CHAT_CHAIN, TAIL);
const result = {};

ui.bots.push(ui.normalizeBot({ id: 'thyca', name: 'Thyca', role: 'bạn', agent_status: { state: 'idle' } }, 0));
ui.bots.push(ui.normalizeBot({ id: 'helper', name: 'Helper', role: 'giúp việc', agent_status: { state: 'idle' } }, 0));

// Room: consecutive same-pair rows collapse; a lone exchange row stays plain.
const bus1 = { role: 'assistant', content: 'làm giúp mình',
  meta: { bot_id: 'thyca', kind: 'message', bus_event: true, trace_id: 't1', span_id: 'sp1', recipient: 'helper' } };
const res1 = { role: 'assistant', content: 'xong',
  meta: { bot_id: 'helper', kind: 'result', trace_id: 't1', parent_span_id: 'sp1', span_id: 'sp2' } };
const bus2 = { role: 'assistant', content: 'old way',
  meta: { bot_id: 'helper', kind: 'message', bus_event: true, trace_id: 't2', span_id: 'sp3' } };
const res2 = { role: 'assistant', content: 'ok',
  meta: { bot_id: 'thyca', kind: 'result', trace_id: 't2', parent_span_id: 'sp3', span_id: 'sp4' } };
const lone = { role: 'assistant', content: 'chờ đã',
  meta: { bot_id: 'thyca', kind: 'message', bus_event: true, trace_id: 't3', span_id: 'sp5', recipient: 'ghost' } };
doc.querySelector('#message-list').innerHTML = ui.renderTranscript({ messages: [
  { role: 'user', content: 'bắt đầu' }, bus1, res1, bus2, res2,
  { role: 'assistant', content: 'việc thường', meta: { bot_id: 'thyca' } },
  lone,
] });
const folds = [...doc.querySelectorAll('#message-list .peer-exchange')];
result.room = {
  folds: folds.length,
  lead: folds[0]?.querySelector('.peer-exchange-lead')?.textContent ?? null,
  rows: folds[0] ? [...folds[0].querySelectorAll('.message-assistant')].length : 0,
  bubbles: doc.querySelectorAll('#message-list .message-assistant').length,
};

// DM: group entries render per partner with the hook the modal opener reads.
globalThis.fetch = async (url, opts = {}) => {
  if (String(url).endsWith('/api/bots/thyca/exchanges')) {
    return { ok: true, json: async () => ({ exchanges: [
      { partner: 'helper', count: 4, last_ts: '2026-10-26T10:03:00Z' },
    ] }) };
  }
  throw new Error('unexpected fetch ' + url);
};
ui.setSession('dm-thyca');
doc.querySelector('#message-list').innerHTML = ui.renderTranscript({
  dm: 'thyca', messages: [
    { role: 'user', content: 'trước', ts: '2026-10-26T09:00:00Z' },
    { role: 'assistant', content: 'sau', ts: '2026-10-26T11:00:00Z', meta: { bot_id: 'thyca' } },
  ] });
await ui.renderExchangeGroups({ dm: 'thyca' });
const groups = [...doc.querySelectorAll('#message-list [data-exchange-partner]')];
result.dm = {
  groups: groups.map(g => [g.getAttribute('data-exchange-partner'), g.getAttribute('aria-label')]),
  owners: groups.map(g => g.getAttribute('data-exchange-owner')),
  paint: groups.map(g => g.getAttribute('style') || ''),
  helperInk: ui.getBot('helper').palette.ink,
  times: groups.map(g => [g.querySelector('time')?.textContent ?? null,
                          g.querySelector('time')?.getAttribute('datetime') ?? null]),
  expectedTime: ui.fmtTime('2026-10-26T10:03:00Z'),
  // The entry slots in at the partner's last exchange time (10:03),
  // between the 09:00 and 11:00 messages -- not forced to the end.
  order: [...doc.querySelector('#message-list').children].map(
    el => el.getAttribute('data-exchange-partner') || el.textContent.trim().slice(0, 12)),
};

// A partner whose latest exchange is newer than every message still
// lands at the very end, like a normal newest message.
globalThis.fetch = async (url, opts = {}) => {
  if (String(url).endsWith('/api/bots/thyca/exchanges')) {
    return { ok: true, json: async () => ({ exchanges: [
      { partner: 'helper', count: 5, last_ts: '2026-10-26T12:00:00Z' },
    ] }) };
  }
  throw new Error('unexpected fetch ' + url);
};
doc.querySelector('#message-list').innerHTML = ui.renderTranscript({
  dm: 'thyca', messages: [
    { role: 'user', content: 'trước', ts: '2026-10-26T09:00:00Z' },
    { role: 'assistant', content: 'sau', ts: '2026-10-26T11:00:00Z', meta: { bot_id: 'thyca' } },
  ] });
await ui.renderExchangeGroups({ dm: 'thyca' });
result.dmNewest = {
  order: [...doc.querySelector('#message-list').children].map(
    el => el.getAttribute('data-exchange-partner') || el.textContent.trim().slice(0, 12)),
};

// Perspective switch: the helper's DM shows exactly the reciprocal entry
// (owner helper, partner thyca) with the partner palette; the self-partner
// row is defensively skipped and no stale entry survives the switch.
globalThis.fetch = async (url, opts = {}) => {
  if (String(url).endsWith('/api/bots/helper/exchanges')) {
    return { ok: true, json: async () => ({ exchanges: [
      { partner: 'thyca', count: 3, last_ts: '2026-10-26T10:05:00Z' },
      { partner: 'helper', count: 9, last_ts: '2026-10-26T10:06:00Z' },
    ] }) };
  }
  if (String(url).endsWith('/api/bots/thyca/exchanges')) {
    return { ok: true, json: async () => ({ exchanges: [
      { partner: 'helper', count: 4, last_ts: '2026-10-26T10:03:00Z' },
    ] }) };
  }
  throw new Error('unexpected fetch ' + url);
};
doc.querySelector('#message-list').innerHTML = ui.renderTranscript({
  dm: 'helper', messages: [
    { role: 'user', content: 'trước', ts: '2026-10-26T09:00:00Z' },
  ] });
ui.setSession('dm-helper');
await ui.renderExchangeGroups({ dm: 'helper' });
const swap = [...doc.querySelectorAll('#message-list [data-exchange-partner]')];
result.dmSwap = {
  entries: swap.map(g => [g.getAttribute('data-exchange-owner'), g.getAttribute('data-exchange-partner')]),
  labels: swap.map(g => [g.getAttribute('aria-label'), g.querySelector('strong')?.textContent ?? null]),
  paint: swap.map(g => g.getAttribute('style') || ''),
  thycaInk: ui.getBot('thyca').palette.ink,
};

// DM-origin rows stay hidden in the DM transcript: no inline collapse
// and no plain bubbles — the grouped entry + modal own the DM view.
doc.querySelector('#message-list').innerHTML = ui.renderTranscript({
  dm: 'thyca', messages: [
    { role: 'user', content: 'chào' }, bus1, res1,
    { role: 'assistant', content: 'việc thường', meta: { bot_id: 'thyca' } },
  ] });
await ui.renderExchangeGroups({ dm: 'thyca' });
result.dmHidden = {
  folds: doc.querySelectorAll('#message-list .peer-exchange').length,
  bubbles: doc.querySelectorAll('#message-list .message-assistant').length,
  groups: [...doc.querySelectorAll('#message-list [data-exchange-partner]')]
    .map(g => g.getAttribute('data-exchange-partner')),
};

// Modal: header A ⇄ B, bubbles per author side, closes cleanly.
globalThis.fetch = async (url, opts = {}) => {
  if (String(url).includes('/exchanges/')) {
    return { ok: true, json: async () => ({ messages: [
      { sender: 'thyca', partner: 'helper', author: 'thyca', kind: 'message', content: 'làm giúp', ts: '2026-10-26T10:00:00Z', trace_id: 't1', span_id: 'sp1' },
      { sender: 'thyca', partner: 'helper', author: 'helper', kind: 'result', content: 'xong', ts: '2026-10-26T10:01:00Z', trace_id: 't1', span_id: 'sp2' },
    ] }) };
  }
  throw new Error('unexpected fetch ' + url);
};
await ui.openExchangeModal('thyca', 'helper');
const dialog = doc.querySelector('#exchange-modal');
result.modal = {
  open: !!dialog?.open,
  title: dialog?.querySelector('#exchange-modal-title')?.textContent ?? null,
  rows: [...(dialog?.querySelectorAll('.exchange-row') ?? [])].map(
    r => [r.className, r.querySelector('header span')?.textContent ?? null,
          r.querySelector('.exchange-bubble')?.textContent ?? null]),
};
dialog?.close();
result.modalClosed = !dialog?.open;
console.log(JSON.stringify(result));
"""


@pytest.fixture(scope="module")
def node() -> str:
    binary = shutil.which("node")
    if binary is None:
        pytest.skip("node not installed")
    return binary


@pytest.fixture(scope="module")
def exchange_ui(node: str) -> dict:
    script = _SCRIPT.replace("TAIL", json.dumps(_TUI_TAIL))
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        check=True, capture_output=True, text=True, cwd=ROOT,
    )
    return json.loads(result.stdout)


def test_room_groups_consecutive_same_pair_rows(exchange_ui: dict) -> None:
    room = exchange_ui["room"]
    assert room["folds"] == 1
    assert room["lead"] == "Thyca và Helper đã trao đổi 4 tin"
    assert room["rows"] == 4
    # 4 folded rows + 1 plain assistant row + 1 lone exchange row.
    assert room["bubbles"] == 6


def test_dm_renders_group_entry_with_partner_hook(exchange_ui: dict) -> None:
    assert exchange_ui["dm"]["groups"] == [
        ["helper", "4 tin nhắn với Helper"],
    ]
    assert exchange_ui["dm"]["owners"] == ["thyca"]
    assert len(exchange_ui["dm"]["paint"]) == 1
    assert exchange_ui["dm"]["helperInk"] in exchange_ui["dm"]["paint"][0]


def test_dm_exchange_perspective_switches_and_paints_partner(exchange_ui: dict) -> None:
    """TASK D: switching DM owner renders exactly the reciprocal entry
    (datasets owner/partner, localized label/aria, partner palette) —
    the self-partner row is skipped and no stale entry survives."""
    swap = exchange_ui["dmSwap"]
    assert swap["entries"] == [["helper", "thyca"]]
    assert swap["labels"] == [["3 tin nhắn với Thyca", "3 tin nhắn"]]
    assert len(swap["paint"]) == 1 and swap["thycaInk"] in swap["paint"][0]


def test_dm_group_entry_shows_last_exchange_time(exchange_ui: dict) -> None:
    # last_ts 2026-10-26T10:03:00Z renders as HH:MM like message times,
    # with the full timestamp kept in the datetime attribute.
    assert exchange_ui["dm"]["times"] == [
        [exchange_ui["dm"]["expectedTime"], "2026-10-26T10:03:00Z"]
    ]


def test_dm_group_entry_sits_at_its_last_exchange_time(exchange_ui: dict) -> None:
    """The DM exchange entry behaves like a normal message: it sits at
    the partner's last_ts in the transcript and only reaches the end
    when the exchange is actually the newest activity."""
    order = exchange_ui["dm"]["order"]
    assert order[0].startswith("trước")
    assert order[1] == "helper"
    assert order[2].startswith("Thyca")  # the 11:00 assistant message
    newest = exchange_ui["dmNewest"]["order"]
    assert newest[-1] == "helper"


def test_dm_hides_exchange_rows_behind_group_entry(exchange_ui: dict) -> None:
    hidden = exchange_ui["dmHidden"]
    # No inline peer-exchange block and no plain exchange bubbles:
    # only the owner's ordinary reply bubble plus the group entry.
    assert hidden["folds"] == 0
    assert hidden["bubbles"] == 1
    assert hidden["groups"] == ["helper"]


def test_modal_shows_both_sides_and_closes(exchange_ui: dict) -> None:
    modal = exchange_ui["modal"]
    assert modal["open"] is True
    assert "Thyca" in modal["title"] and "Helper" in modal["title"]
    assert [r[1] for r in modal["rows"]] == ["Thyca", "Helper"]
    assert [r[2] for r in modal["rows"]] == ["làm giúp", "xong"]
    assert any("is-owner" in r[0] for r in modal["rows"])
    assert any("is-partner" in r[0] for r in modal["rows"])
    assert exchange_ui["modalClosed"] is True
