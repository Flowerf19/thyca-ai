"""Execute ui/memories/js/memories.js with a minimal DOM harness.

The rebuilt Nhật ký page keeps one leaf list grouped by day, a bot-scope
<select> in the toolbar and a single edit dialog — the old per-section
editor copies, category views, ?bot_id= deep links and pending-mutation
guards are gone. These scenarios cover what remains: scoped stats loads
with echo validation, stale-answer dropping, and the three leaf actions
(reinforce / forget / update) plus the roster-driven scope options.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MEM_JS = ROOT / "thyca" / "webui" / "ui" / "memories" / "js" / "memories.js"

# Elements auto-vivify per selector; innerHTML/textContent stay plain strings
# the scenarios read back. Listeners are captured so tests can fire them.
_HARNESS = r"""
import fs from 'node:fs';
function makeEl(tag = 'div') {
  return {
    tag, attrs: {}, dataset: {}, listeners: {}, children: [],
    style: {}, value: '', hidden: false, disabled: false, open: false,
    _html: '', _text: '', _qs: new Map(),
    setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return this.attrs[k] ?? null; },
    removeAttribute(k) { delete this.attrs[k]; },
    addEventListener(t, fn) { (this.listeners[t] ??= []).push(fn); },
    insertAdjacentHTML(_p, h) { this._html += h; },
    append(...n) { this.children.push(...n); },
    replaceChildren(...n) { this.children = [...n]; this._html = ''; },
    showModal() { this.open = true; },
    close() { this.open = false; },
    focus() {},
    querySelector(s) { if (!this._qs.has(s)) this._qs.set(s, makeEl()); return this._qs.get(s); },
    querySelectorAll() { return []; },
    closest() { return null; },
    get options() { return this.children; },
    get innerHTML() { return this._html; },
    set innerHTML(v) { this._html = String(v); this.children = []; },
    get textContent() { return this._text; },
    set textContent(v) { this._text = String(v); this.children = []; },
  };
}
const document = makeEl('document');
document.body = makeEl('body');
document.documentElement = makeEl('html');
document.createElement = t => makeEl(t);
document.createElementNS = (_n, t) => makeEl(t);

const calls = { gets: [], posts: [], toasts: [], confirms: 0 };
const hooks = {
  getJson: async () => ({ bot_id: null, total: 0, leaves: [] }),
  postJson: async () => ({ ok: true }),
};
const ROSTER = [
  { id: 'coder', name: 'Coder', role: 'dev', state: 'idle', palette: {} },
  { id: 'researcher', name: 'Researcher', role: 'research', state: 'idle', palette: {} },
];
const deps = {
  $: (s, root) => (root || document).querySelector(s),
  $$: () => [],
  document,
  window: {},
  icon: () => '',
  escapeHTML: s => String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;'),
  bots: ROSTER,
  getBot: id => ROSTER.find(b => b.id === id) || null,
  states: { idle: ['idle', 'Rảnh'] },
  avatar: () => '',
  paletteStyle: () => '',
  getJson: async (u, o) => { calls.gets.push(u); return hooks.getJson(u, o); },
  postJson: async (u, b) => { calls.posts.push({ url: u, body: b }); return hooks.postJson(u, b); },
  toast: t => calls.toasts.push(t),
  confirm: () => { calls.confirms += 1; return true; },
  bootShell: async () => {},
};
const settle = async () => { for (let i = 0; i < 6; i++) await new Promise(r => setImmediate(r)); };
const fire = (el, type, event = {}) => {
  event.preventDefault ??= () => {};
  for (const fn of el.listeners[type] ?? []) fn(event);
};
/* Leaf-action delegation lives on #memory-leaves; the scenario fabricates
   the clicked button + owning article, like a real event target would. */
const fireAct = act => {
  const article = { dataset: { chunk: 'c1' } };
  const btn = { dataset: { act }, disabled: false,
    closest: s => (s === '[data-act]' ? btn : s === '[data-chunk]' ? article : null) };
  fire(document.querySelector('#memory-leaves'), 'click', { target: btn });
  return btn;
};
const LEAF = {
  chunk_id: 'c1', session_id: 's1', heading: '## 09:30 — Trang một',
  snippet: '- Nội dung một', timeline_day: '2026-09-20',
  get_count: 1, search_count: 2, bots: ['coder'], kind: 'habit',
  habit: 'viết', status: 'open',
};
"""

# Eval runs AFTER the scenario's setup: loadStats() fires during the page
# script's own boot, so hooks must already answer by then.
_EVAL = r"""
const source = fs.readFileSync(process.env.MEM_JS, 'utf8');
const page = new Function(...Object.keys(deps), source + `
  return { memState, loadStats, mutate, openEdit, fillBotOptions,
           renderAll, renderLeaves, filteredLeaves, splitHeading };
`)(...Object.values(deps));
"""

_SCENARIOS = {
    "stats": (r"""
hooks.getJson = async url => (url === '/api/memory/stats'
  ? { bot_id: null, total: 3, used: 2, unused: 1, searched: 1, untouched: 1,
      expiring: [], suggest_removal: [], leaves: [LEAF] }
  : {});
""", r"""
await settle();
console.log(JSON.stringify({
  gets: calls.gets,
  stats: document.querySelector('#memory-stats').innerHTML,
  leaves: document.querySelector('#memory-leaves').innerHTML,
  count: document.querySelector('#memory-count').textContent,
  scopeCardHidden: document.querySelector('#memory-scope-card').hidden,
  selected: document.querySelector('#memory-bot').value,
}));
"""),

    "scope": ("", r"""
await settle();
const sel = document.querySelector('#memory-bot');
// A payload echoing a different scope than the one asked for is rejected.
hooks.getJson = async () => ({ bot_id: null, total: 81, leaves: [LEAF] });
sel.onchange({ target: { value: 'coder' } });
await settle();
const mismatch = {
  status: document.querySelector('#memory-status').textContent,
  statusCls: document.querySelector('#memory-status').className,
  statsNull: page.memState.stats === null,
  leaves: document.querySelector('#memory-leaves').innerHTML,
};
// A matching echo renders and the scope card shows the bot.
hooks.getJson = async url => (url.includes('bot_id=coder')
  ? { bot_id: 'coder', total: 1, used: 1, unused: 0, searched: 1, untouched: 0,
      expiring: [], suggest_removal: [], leaves: [LEAF] }
  : { bot_id: null, total: 0, leaves: [] });
sel.onchange({ target: { value: 'coder' } });
await settle();
console.log(JSON.stringify({
  gets: calls.gets,
  mismatch,
  status: document.querySelector('#memory-status').textContent,
  cardHidden: document.querySelector('#memory-scope-card').hidden,
  card: document.querySelector('#memory-scope-card').innerHTML,
  count: document.querySelector('#memory-count').textContent,
}));
"""),

    "scope_race": ("", r"""
await settle();
const pending = [];
hooks.getJson = url => new Promise(resolve => pending.push({ url, resolve }));
const sel = document.querySelector('#memory-bot');
sel.onchange({ target: { value: 'coder' } });
sel.onchange({ target: { value: 'researcher' } });
await settle();
// The coder answer lands late — the request counter must drop it.
pending[0].resolve({ bot_id: 'coder', total: 9, leaves: [LEAF] });
await settle();
const stale = { total: page.memState.stats?.total ?? null };
pending[1].resolve({ bot_id: 'researcher', total: 2, used: 0, unused: 2,
                     searched: 0, untouched: 2, expiring: [], suggest_removal: [], leaves: [] });
await settle();
console.log(JSON.stringify({
  gets: calls.gets,
  stale,
  finalTotal: page.memState.stats.total,
  scope: page.memState.scope,
}));
"""),

    "actions": (r"""
hooks.getJson = async url => (url === '/api/memory/stats'
  ? { bot_id: null, total: 1, used: 1, unused: 0, searched: 1, untouched: 0,
      expiring: [], suggest_removal: [], leaves: [LEAF] }
  : {});
""", r"""
await settle();
const getsAfterBoot = calls.gets.length;

// Gia hạn: posts session_id, then refreshes that leaf's meta in place —
// no stats reload for a single-field change.
hooks.postJson = async () => ({ ok: true, expires_at: '2026-10-20T00:00:00Z' });
fireAct('reinforce');
await settle();
const meta = document.querySelector('#memory-leaves [data-chunk="c1"]')
  .querySelector('.mem-meta').innerHTML;

// Quên: asks first, posts, then reloads the stats.
hooks.postJson = async () => ({ ok: true });
fireAct('forget');
await settle();

// Sửa: the dialog prefills from the leaf, and the submitted summary is
// whitespace-collapsed like the writer's one-line leaf format.
fireAct('edit');
const opened = {
  dialogOpen: document.querySelector('#memory-edit-dialog').open,
  topic: document.querySelector('#mem-edit-topic').value,
  summary: document.querySelector('#mem-edit-summary').value,
};
document.querySelector('#mem-edit-summary').value = 'dòng  một\n   hai';
await document.querySelector('#memory-edit-form').onsubmit({ preventDefault() {} });
await settle();
const closed = !document.querySelector('#memory-edit-dialog').open;

// A failed update keeps the dialog open and toasts the error.
hooks.postJson = async () => { throw new Error('server down'); };
fireAct('edit');
await document.querySelector('#memory-edit-form').onsubmit({ preventDefault() {} });
await settle();
console.log(JSON.stringify({
  posts: calls.posts,
  gets: calls.gets,
  getsAfterBoot,
  meta,
  expires: LEAF.expires_at,
  opened, closed,
  dialogOpenAfterFailure: document.querySelector('#memory-edit-dialog').open,
  toasts: calls.toasts,
  confirms: calls.confirms,
}));
"""),

    "options": ("", r"""
await settle();
const sel = document.querySelector('#memory-bot');
const options = sel.children.map(o => ({ value: o.value, text: o.textContent }));
// A scope left pointing at a bot that is gone falls back to all bots.
page.memState.scope = 'ghost';
page.fillBotOptions();
const stale = { scope: page.memState.scope, value: sel.value };
page.memState.scope = 'coder';
page.fillBotOptions();
console.log(JSON.stringify({
  options, stale, kept: { scope: page.memState.scope, value: sel.value },
}));
"""),
}


def _run_scenario(name: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    setup, body = _SCENARIOS[name]
    result = subprocess.run(
        [node, "--input-type=module", "-e", _HARNESS + setup + _EVAL + body],
        cwd=ROOT, check=True, capture_output=True, text=True, timeout=15,
        env={**os.environ, "MEM_JS": str(MEM_JS)},
    )
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def stats() -> dict:
    return _run_scenario("stats")


@pytest.fixture(scope="module")
def scope() -> dict:
    return _run_scenario("scope")


@pytest.fixture(scope="module")
def scope_race() -> dict:
    return _run_scenario("scope_race")


@pytest.fixture(scope="module")
def actions() -> dict:
    return _run_scenario("actions")


@pytest.fixture(scope="module")
def options() -> dict:
    return _run_scenario("options")


def test_stats_load_renders_tiles_leaves_and_global_scope(stats: dict) -> None:
    """Boot loads the global stats — no bot_id — into tiles + the leaf list;
    the bot scope card stays hidden until a scope is chosen."""
    assert stats["gets"] == ["/api/memory/stats"]
    for tile in ("Tổng lá", "Đã dùng", "Được tìm", "Sắp hết hạn", "Gợi ý xóa"):
        assert tile in stats["stats"], tile
    assert "Trang một" in stats["leaves"]
    assert "Nội dung một" in stats["leaves"]
    assert "mem-leaf" in stats["leaves"]
    assert stats["count"] == "1 lá"
    assert stats["scopeCardHidden"] is True
    assert stats["selected"] == ""


def test_scope_echo_mismatch_rejected(scope: dict) -> None:
    """The stats endpoint echoes the applied scope — a mismatched answer is
    treated as stale: status shows the mismatch, no leaves render."""
    assert "/api/memory/stats?bot_id=coder" in scope["gets"]
    assert "không khớp" in scope["mismatch"]["status"]
    assert "s-error" in scope["mismatch"]["statusCls"]
    assert scope["mismatch"]["statsNull"] is True
    assert scope["mismatch"]["leaves"] == ""


def test_matching_scope_echo_renders_scope_card(scope: dict) -> None:
    """A matching echo renders the scoped journal and the scope card
    introduces the bot — the new form of the old scope title."""
    assert scope["cardHidden"] is False
    assert "Coder" in scope["card"]
    assert "1 lá nhớ" in scope["card"]
    assert "không khớp" not in scope["status"]
    assert scope["count"] == "1 lá"


def test_scope_switch_drops_stale_answers(scope_race: dict) -> None:
    """Switching scope mid-flight bumps the request counter — the late coder
    answer lands nowhere, the researcher answer applies."""
    assert scope_race["gets"] == [
        "/api/memory/stats",
        "/api/memory/stats?bot_id=coder",
        "/api/memory/stats?bot_id=researcher",
    ]
    assert scope_race["stale"]["total"] == 0   # boot stats still in place
    assert scope_race["finalTotal"] == 2
    assert scope_race["scope"] == "researcher"


def test_leaf_actions_post_the_right_endpoints(actions: dict) -> None:
    """Gia hạn renews the TTL (in-place meta refresh, no reload), Quên asks
    then drops the leaf, Sửa posts the collapsed summary — each exactly once."""
    posts = actions["posts"]
    assert posts[0] == {"url": "/api/memory/reinforce", "body": {"session_id": "s1"}}
    assert posts[1] == {"url": "/api/memory/forget", "body": {"session_id": "s1"}}
    assert posts[2] == {
        "url": "/api/memory/update",
        "body": {"session_id": "s1", "topic": "Trang một", "summary": "dòng một hai"},
    }
    assert actions["confirms"] == 1            # only Quên asks
    # Reinforce refreshes the leaf's own meta; forget + update reload stats.
    assert actions["expires"] == "2026-10-20T00:00:00Z"
    assert "hết hạn" in actions["meta"]
    reloads = actions["gets"][actions["getsAfterBoot"]:]
    assert reloads == ["/api/memory/stats", "/api/memory/stats"]
    assert "Đã gia hạn trang." in actions["toasts"]
    assert "Đã quên trang." in actions["toasts"]


def test_edit_dialog_prefills_collapses_and_closes(actions: dict) -> None:
    """Sửa opens the shared dialog prefilled from the leaf (heading title and
    the de-bulleted snippet); a successful update closes it and toasts."""
    assert actions["opened"]["dialogOpen"] is True
    assert actions["opened"]["topic"] == "Trang một"
    assert actions["opened"]["summary"] == "Nội dung một"
    assert actions["closed"] is True
    assert "Đã cập nhật trang." in actions["toasts"]


def test_update_failure_toasts_and_keeps_dialog_open(actions: dict) -> None:
    """A failed save keeps the dialog open with the draft fields intact and
    surfaces the error as a toast — nothing is silently dropped."""
    assert actions["dialogOpenAfterFailure"] is True
    assert "Không lưu được: server down" in actions["toasts"]


def test_scope_options_come_from_roster(options: dict) -> None:
    """The scope select is 'Tất cả các bot' plus the live roster; a stale
    scope (bot deleted elsewhere) falls back to all bots, a valid one keeps."""
    assert [o["text"] for o in options["options"]] == [
        "Tất cả các bot", "Coder", "Researcher"]
    assert [o["value"] for o in options["options"]] == ["", "coder", "researcher"]
    assert options["stale"] == {"scope": "", "value": ""}
    assert options["kept"] == {"scope": "coder", "value": "coder"}
