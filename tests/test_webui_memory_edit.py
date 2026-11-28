"""Execute ui/memories/js/memories.js with a minimal DOM harness.

The streamlined Nhật ký page uses one compact status tab strip + kind/search
controls (no reload button or duplicate dropdown) above a collapsed-by-default
`<details>` leaf list grouped by day and paged 60 leaves at a time. Flagged
leaves (expiring / suggest-removal) highlight in red (`is-alert`), clicking a
row expands its content + clean metadata (no technical ID) + actions, and
typing a search auto-expands matching rows.
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
INDEX_HTML = ROOT / "thyca" / "webui" / "ui" / "chat" / "index.html"

_HARNESS = r"""
import fs from 'node:fs';
const elementRegistry = new Map();
function makeEl(tag = 'div') {
  const classes = new Set();
  return {
    tag, attrs: {}, dataset: {}, listeners: {}, children: [],
    style: {}, value: '', hidden: false, disabled: false, open: false,
    _html: '', _text: '', _qs: new Map(),
    classList: {
      add(c) { classes.add(c); },
      remove(c) { classes.delete(c); },
      contains(c) { return classes.has(c); },
      toggle(c, force) {
        const on = force === undefined ? !classes.has(c) : Boolean(force);
        if (on) classes.add(c); else classes.delete(c);
        return on;
      },
    },
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
    querySelector(s) { if (!elementRegistry.has(s)) elementRegistry.set(s, makeEl()); return elementRegistry.get(s); },
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
const indexHtml = fs.readFileSync(process.env.INDEX_HTML, 'utf8');
for (const match of indexHtml.matchAll(/<template id="([^"]+)">([\s\S]*?)<\/template>/g)) {
  const tpl = makeEl('template');
  tpl._html = match[2];
  elementRegistry.set(`template#${match[1]}`, tpl);
}

const calls = { gets: [], posts: [], toasts: [], confirms: 0, urls: [], teamRenders: 0 };
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
  /* Sidebar globals the page drives: scope clicks mark the selected card
     (selectedBot) and re-render the roster (renderTeam). */
  selectedBot: '',
  renderTeam: () => { calls.teamRenders += 1; },
  /* selectBot() deep-links ?bot= via history/location — the stub records it. */
  location: { pathname: '/memories/index.html', search: '' },
  history: { replaceState(_s, _t, u) { calls.urls.push(u); } },
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
  chunk_id: 'c1', memory_id: 's1', heading: '## 09:30 — Trang một',
  snippet: '- Nội dung một', timeline_day: '2026-09-20',
  get_count: 1, search_count: 2, bots: ['coder'], kind: 'habit',
  habit: 'viết', status: 'open',
};
"""

_EVAL = r"""
const source = fs.readFileSync(process.env.MEM_JS, 'utf8');
const marker = 'mount(ctx) {';
const start = source.indexOf(marker) + marker.length;
const end = source.lastIndexOf('\n} });');
const body = source.slice(start, end);
const tail = /\n\s*const ready = boot\(\);\n\s*return \{ ready,[\s\S]*?\};\s*$/.exec(body);
if (!tail) throw new Error('unrecognized retained memories factory tail');
const ctx = {
  root: document.querySelector('.workspace'),
  route: { page: 'memories', search: deps.location.search, hash: '' },
  shellReady: Promise.resolve(),
  updateRoute(parts = {}) { if ('search' in parts) this.route.search = parts.search; if ('hash' in parts) this.route.hash = parts.hash; deps.history.replaceState({ thycaSpa: this.route }, '', '/'); },
  highlighted: '',
  highlightBot(id) { this.highlighted = id || ''; deps.selectedBot = this.highlighted; },
  renderTeam: deps.renderTeam,
  isActive: () => true,
  onRoute() { return () => {}; },
};
const depNames = Object.keys(deps).filter(key => !['$', '$$', 'selectedBot'].includes(key));
const depValues = depNames.map(key => deps[key]);
const page = new Function(...depNames, 'ctx', body.slice(0, tail.index) + `
  boot();
  return { memState, loadStats, mutate, openEdit, selectBot, loadConversation,
           renderAll, renderLeaves, renderScopeCard, filteredLeaves,
           splitHeading };
`)(...depValues, ctx);
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
  workspace: document.querySelector('.workspace').innerHTML,
}));
"""),

    "scope": ("", r"""
await settle();
// A payload echoing a different scope than the one asked for is rejected.
hooks.getJson = async () => ({ bot_id: null, total: 81, leaves: [LEAF] });
page.selectBot('coder');
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
await page.loadStats();
await settle();
console.log(JSON.stringify({
  gets: calls.gets,
  urls: calls.urls,
  route: ctx.route,
  selectedBot: ctx.highlighted,
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
page.selectBot('coder');
page.selectBot('researcher');
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
  urls: calls.urls,
  route: ctx.route,
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

// Gia hạn: posts memory_id, then refreshes that leaf's meta in place —
// no stats reload for a single-field change.
hooks.postJson = async () => ({ ok: true, expires_at: '2026-10-20T00:00:00Z' });
fireAct('reinforce');
await settle();
const meta = document.querySelector('#memory-leaves [data-chunk="c1"]')
  .querySelector('.mem-meta').innerHTML;
const expires = page.memState.byChunk.get('c1').expires_at;

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
  expires,
  opened, closed,
  dialogOpenAfterFailure: document.querySelector('#memory-edit-dialog').open,
  toasts: calls.toasts,
  confirms: calls.confirms,
}));
"""),

    "routing": ("", r"""
await settle();
hooks.getJson = async url => {
  const m = url.match(/bot_id=(\w+)/);
  return { bot_id: m ? m[1] : null, total: m ? 1 : 3, used: 1, unused: 0,
           searched: 1, untouched: 0, expiring: [], suggest_removal: [],
           leaves: m ? [LEAF] : [] };
};
// Sidebar bot click: scope + ?bot= deep link + scope card.
page.selectBot('coder');
await settle();
const scoped = {
  scope: page.memState.scope, selected: ctx.highlighted,
  url: ctx.route.search,
  cardHidden: document.querySelector('#memory-scope-card').hidden,
  card: document.querySelector('#memory-scope-card').innerHTML,
};
// Clicking the scoped bot again toggles the filter off.
page.selectBot('coder');
await settle();
const toggled = {
  scope: page.memState.scope, url: calls.urls[calls.urls.length - 1],
  cardHidden: document.querySelector('#memory-scope-card').hidden,
};
// A drawer session routes through loadConversation to the owning bot's scope.
page.loadConversation('s9', 'researcher');
await settle();
const fromSession = {
  scope: page.memState.scope, selected: ctx.highlighted,
  url: ctx.route.search,
  card: document.querySelector('#memory-scope-card').innerHTML,
};
// The scope card's "Bỏ lọc" button clears the filter in place.
fire(document.querySelector('#memory-scope-card'), 'click',
     { target: { closest: s => (s === '.mem-scope-clear' ? {} : null) } });
await settle();
console.log(JSON.stringify({
  scoped, toggled, fromSession,
  cleared: {
    scope: page.memState.scope, url: ctx.route.search,
    cardHidden: document.querySelector('#memory-scope-card').hidden,
  },
  gets: calls.gets, teamRenders: calls.teamRenders,
}));
"""),

    "deeplink": (r"""
deps.location.search = '?bot=researcher';
hooks.getJson = async url => (url.includes('bot_id=researcher')
  ? { bot_id: 'researcher', total: 1, used: 0, unused: 1, searched: 0,
      untouched: 1, expiring: [], suggest_removal: [], leaves: [LEAF] }
  : { bot_id: null, total: 0, leaves: [] });
""", r"""
await settle();
console.log(JSON.stringify({
  scope: page.memState.scope,
  selected: ctx.highlighted,
  route: ctx.route,
  gets: calls.gets,
  cardHidden: document.querySelector('#memory-scope-card').hidden,
  card: document.querySelector('#memory-scope-card').innerHTML,
}));
"""),

    "deeplink_stale": (r"""
deps.location.search = '?bot=ghost';
hooks.getJson = async url => (url.includes('bot_id=ghost')
  ? { bot_id: 'ghost', total: 0, leaves: [] }
  : { bot_id: null, total: 0, leaves: [] });
""", r"""
await settle();
console.log(JSON.stringify({
  scope: page.memState.scope,
  urls: calls.urls,
  route: ctx.route,
  cardHidden: document.querySelector('#memory-scope-card').hidden,
}));
"""),

    "filters": (r"""
const many = [];
for (let i = 0; i < 65; i++) many.push({
  chunk_id: 'k' + i, memory_id: 'sk' + i,
  heading: '## 09:30 — Lá ' + i, snippet: '- nội dung ' + i,
  timeline_day: '2026-09-2' + (i % 3), get_count: i % 2, search_count: 0,
  bots: ['coder'], kind: i % 3 === 0 ? 'habit' : (i % 3 === 1 ? 'checkpoint' : ''),
});
hooks.getJson = async url => (url === '/api/memory/stats'
  ? { bot_id: null, total: many.length, leaves: many } : {});
""", r"""
await settle();
const leafCount = () => (document.querySelector('#memory-leaves').innerHTML
  .match(/mem-leaf-title/g) || []).length;
const pageOne = {
  leaves: leafCount(),
  pager: document.querySelector('#memory-leaves').innerHTML.includes('mem-pager'),
  info: (document.querySelector('#memory-leaves').innerHTML.match(/Trang \d+\/\d+/) || [''])[0],
  collapsedByDefault: !document.querySelector('#memory-leaves').innerHTML.includes(' open>'),
};
const fireStat = filter => {
  const btn = { dataset: { statFilter: filter }, closest: s => (s === '[data-stat-filter]' ? btn : null) };
  fire(document.querySelector('#memory-stats'), 'click', { target: btn });
};
document.querySelector('#memory-kind').onchange({ target: { value: 'habit' } });
const habit = { leaves: leafCount(),
  count: document.querySelector('#memory-count').textContent };
fireStat('used');
const habitUsed = { leaves: leafCount(),
  count: document.querySelector('#memory-count').textContent };
document.querySelector('#memory-kind').onchange({ target: { value: '' } });
fireStat('');
fireAct('next');
const pageTwo = {
  leaves: leafCount(), memPage: page.memState.page,
  info: (document.querySelector('#memory-leaves').innerHTML.match(/Trang \d+\/\d+/) || [''])[0],
};
const usedBtnHtml = document.querySelector('#memory-leaves').innerHTML;
const hasSingleBulletParagraph = !usedBtnHtml.includes('<ul><li>') && usedBtnHtml.includes('<p>nội dung');
fireStat('used');
const statUsed = { leaves: leafCount(), useVal: page.memState.use };
fireStat('expiring');
const statExpiring = { leaves: leafCount(), useVal: page.memState.use };
fireStat('expiring');
const statReset = { leaves: leafCount(), useVal: page.memState.use };
fire(document.querySelector('#memory-search'), 'input', { target: { value: 'Lá 12' } });
const searchExpanded = {
  leaves: leafCount(),
  opened: document.querySelector('#memory-leaves').innerHTML.includes(' open>'),
};
fire(document.querySelector('#memory-search'), 'input', { target: { value: '' } });
const searchClearedCollapsed = !document.querySelector('#memory-leaves').innerHTML.includes(' open>');
console.log(JSON.stringify({
  pageOne, habit, habitUsed, pageTwo, hasSingleBulletParagraph, statUsed, statExpiring, statReset,
  searchExpanded, searchClearedCollapsed, gets: calls.gets,
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
        env={**os.environ, "MEM_JS": str(MEM_JS), "INDEX_HTML": str(INDEX_HTML)},
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
def routing() -> dict:
    return _run_scenario("routing")


@pytest.fixture(scope="module")
def deeplink() -> dict:
    return _run_scenario("deeplink")


@pytest.fixture(scope="module")
def deeplink_stale() -> dict:
    return _run_scenario("deeplink_stale")


@pytest.fixture(scope="module")
def filters() -> dict:
    return _run_scenario("filters")


def test_stats_load_renders_tiles_leaves_and_global_scope(stats: dict) -> None:
    """Boot loads global stats into compact filter tabs + collapsed `<details>`
    leaves without technical memory IDs, reload buttons, or duplicate selects."""
    assert stats["gets"] == ["/api/memory/stats"]
    for tab in ("Tất cả", "Đã dùng", "Sắp hết hạn", "Gợi ý xóa"):
        assert tab in stats["stats"], tab
    assert "Trang một" in stats["leaves"]
    assert "Nội dung một" in stats["leaves"]
    assert '<details class="mem-leaf"' in stats["leaves"]
    assert ' open>' not in stats["leaves"]
    assert "mem-sid" not in stats["leaves"]
    assert ">s1<" not in stats["leaves"]
    assert "Thói quen" in stats["leaves"]
    assert "· viết" not in stats["leaves"]
    assert stats["count"] == "1 lá"
    assert stats["scopeCardHidden"] is True
    ws = stats["workspace"]
    for dropped in ('id="memory-bot"', 'id="memory-use"', 'id="memory-reload"'):
        assert dropped not in ws, dropped
    for marker in ('id="memory-stats"', 'id="memory-kind"', 'id="memory-search"'):
        assert marker in ws, marker
    assert 'value="habit"' in ws and 'value="checkpoint"' in ws


def test_scope_echo_mismatch_rejected(scope: dict) -> None:
    """selectBot() scopes through stats?bot_id= — the endpoint echoes the
    applied scope and a mismatched answer is treated as stale: status shows
    the mismatch, no leaves render."""
    assert "/api/memory/stats?bot_id=coder" in scope["gets"]
    assert scope["route"]["search"] == "?bot=coder"
    assert scope["urls"] == ["/"]
    assert "không khớp" in scope["mismatch"]["status"]
    assert "s-error" in scope["mismatch"]["statusCls"]
    assert scope["mismatch"]["statsNull"] is True
    assert scope["mismatch"]["leaves"] == ""


def test_matching_scope_echo_renders_scope_card(scope: dict) -> None:
    """A matching echo renders the scoped journal and the scope card
    introduces the bot — with the "Bỏ lọc" button that clears the filter."""
    assert scope["cardHidden"] is False
    assert scope["selectedBot"] == "coder"
    assert "Coder" in scope["card"]
    assert "1 lá nhớ" in scope["card"]
    assert "Bỏ lọc" in scope["card"]
    assert "không khớp" not in scope["status"]
    assert scope["count"] == "1 lá"


def test_scope_switch_drops_stale_answers(scope_race: dict) -> None:
    """Switching scope mid-flight bumps the request counter — the late coder
    answer lands nowhere, the researcher answer applies and ?bot= follows."""
    assert scope_race["gets"] == [
        "/api/memory/stats",
        "/api/memory/stats?bot_id=coder",
        "/api/memory/stats?bot_id=researcher",
    ]
    assert scope_race["stale"]["total"] == 0
    assert scope_race["finalTotal"] == 2
    assert scope_race["scope"] == "researcher"
    assert scope_race["route"]["search"] == "?bot=researcher"
    assert scope_race["urls"] == ["/", "/"]


def test_leaf_actions_post_the_right_endpoints(actions: dict) -> None:
    """Gia hạn renews the TTL (in-place meta refresh, no reload), Quên asks
    then drops the leaf, Sửa posts the collapsed summary — each exactly once."""
    posts = actions["posts"]
    assert posts[0] == {"url": "/api/memory/reinforce", "body": {"memory_id": "s1"}}
    assert posts[1] == {"url": "/api/memory/forget", "body": {"memory_id": "s1"}}
    assert posts[2] == {
        "url": "/api/memory/update",
        "body": {"memory_id": "s1", "topic": "Trang một", "summary": "dòng một hai"},
    }
    assert actions["confirms"] == 1
    assert actions["expires"] == "2026-10-20T00:00:00Z"
    assert "Hết hạn" in actions["meta"]
    assert "s1" not in actions["meta"]
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


def test_scope_comes_from_sidebar_clicks_and_url(routing: dict) -> None:
    """Bot scope moved out of the deleted toolbar <select> into the sidebar:
    selectBot() toggles the filter + rewrites ?bot=, drawer sessions route
    through loadConversation(), and the scope card's "Bỏ lọc" clears it — each
    re-render keeps the roster highlight in sync."""
    assert routing["scoped"]["scope"] == "coder"
    assert routing["scoped"]["selected"] == "coder"
    assert routing["scoped"]["url"].endswith("?bot=coder")
    assert routing["scoped"]["cardHidden"] is False
    assert "Coder" in routing["scoped"]["card"]

    assert routing["toggled"]["scope"] == ""
    assert routing["toggled"]["cardHidden"] is True
    assert "?bot=" not in routing["toggled"]["url"]

    assert routing["fromSession"]["scope"] == "researcher"
    assert routing["fromSession"]["url"].endswith("?bot=researcher")
    assert "Researcher" in routing["fromSession"]["card"]

    assert routing["cleared"]["scope"] == ""
    assert routing["cleared"]["cardHidden"] is True
    assert "?bot=" not in routing["cleared"]["url"]

    assert routing["gets"].count("/api/memory/stats?bot_id=coder") == 1
    assert "/api/memory/stats?bot_id=researcher" in routing["gets"]
    assert routing["teamRenders"] > 0


def test_bot_deep_link_scopes_the_journal(deeplink: dict) -> None:
    """?bot=<id> opens the journal already scoped — the sidebar highlight
    follows selectedBot and the scope card shows the bot."""
    assert deeplink["scope"] == "researcher"
    assert deeplink["selected"] == "researcher"
    assert deeplink["gets"] == ["/api/memory/stats?bot_id=researcher"]
    assert deeplink["cardHidden"] is False
    assert "Researcher" in deeplink["card"]


def test_stale_bot_deep_link_clears_after_boot(deeplink_stale: dict) -> None:
    """A ?bot=<id> naming a bot missing from the roster clears itself once
    bootShell() answers — the URL loses the param, no scope card renders."""
    assert deeplink_stale["scope"] == ""
    assert deeplink_stale["cardHidden"] is True
    assert deeplink_stale["urls"] == ["/"]
    assert deeplink_stale["route"]["search"] == ""


def test_kind_use_filters_page_through_60_leaves(filters: dict) -> None:
    """Compact status tabs + kind/search filter client-side without refetching,
    rows stay collapsed by default, and active search auto-expands matches."""
    assert filters["pageOne"]["leaves"] == 60
    assert filters["pageOne"]["pager"] is True
    assert filters["pageOne"]["info"] == "Trang 1/2"
    assert filters["pageOne"]["collapsedByDefault"] is True
    assert filters["habit"] == {"leaves": 22, "count": "22/65 lá"}
    assert filters["habitUsed"] == {"leaves": 11, "count": "11/65 lá"}
    assert filters["pageTwo"]["leaves"] == 5
    assert filters["pageTwo"]["info"] == "Trang 2/2"
    assert filters["pageTwo"]["memPage"] == 2
    assert filters["hasSingleBulletParagraph"] is True
    assert filters["statUsed"] == {"leaves": 32, "useVal": "used"}
    assert filters["statExpiring"] == {"leaves": 0, "useVal": "expiring"}
    assert filters["statReset"] == {"leaves": 60, "useVal": ""}
    assert filters["searchExpanded"] == {"leaves": 1, "opened": True}
    assert filters["searchClearedCollapsed"] is True
    assert filters["gets"] == ["/api/memory/stats"]


def test_expiring_kpi_reinforce_and_empty_filter() -> None:
    """Clicking a 0-count KPI shows the filter-empty message (not 'Chưa có lá nhớ
    nào'), and reinforcing an expiring leaf removes it from the expiring filter."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    tomorrow = "new Date(Date.now() + 864e5).toISOString()"
    yesterday = "new Date(Date.now() - 864e5).toISOString().slice(0, 10)"
    setup = f"""
        const EXP_LEAF = {{
            chunk_id: 'e1', memory_id: 'me1', heading: '## 10:00 — Ghi chú sắp hạn',
            snippet: '- Sắp hết hạn', timeline_day: {yesterday},
            get_count: 0, search_count: 0, bots: ['thyca'], expires_at: {tomorrow},
        }};
        hooks.getJson = async url => (url === '/api/memory/stats'
            ? {{ bot_id: null, total: 1, used: 0, unused: 1, searched: 0, untouched: 1,
                 expiring: [EXP_LEAF], suggest_removal: [], leaves: [EXP_LEAF] }}
            : {{}});
    """
    body = """
        await settle();
        const clickStat = filter => {
            const btn = { dataset: { statFilter: filter }, closest: s => (s === '[data-stat-filter]' ? btn : null) };
            fire(document.querySelector('#memory-stats'), 'click', { target: btn });
        };
        clickStat('suggest');
        const emptySuggestHtml = document.querySelector('#memory-leaves').innerHTML;
        clickStat('expiring');
        const beforeReinforceHtml = document.querySelector('#memory-leaves').innerHTML;
        const countBefore = document.querySelector('#memory-count').textContent;
        const farFuture = new Date(Date.now() + 90 * 864e5).toISOString();
        hooks.postJson = async () => ({ ok: true, expires_at: farFuture });
        const article = { dataset: { chunk: 'e1' } };
        const btn = { dataset: { act: 'reinforce' }, disabled: false,
            closest: s => (s === '[data-act]' ? btn : s === '[data-chunk]' ? article : null) };
        fire(document.querySelector('#memory-leaves'), 'click', { target: btn });
        await settle();
        const afterReinforceHtml = document.querySelector('#memory-leaves').innerHTML;
        const countAfter = document.querySelector('#memory-count').textContent;
        const statsAfter = document.querySelector('#memory-stats').innerHTML;
        clickStat('expiring');
        const afterClearHtml = document.querySelector('#memory-leaves').innerHTML;
        console.log(JSON.stringify({
            emptySuggestHtml, beforeReinforceHtml, countBefore,
            afterReinforceHtml, countAfter, statsAfter, afterClearHtml, gets: calls.gets,
        }));
    """
    result = subprocess.run(
        [node, "--input-type=module", "-e", _HARNESS + setup + _EVAL + body],
        cwd=ROOT, check=True, capture_output=True, text=True, timeout=15,
        env={**os.environ, "MEM_JS": str(MEM_JS), "INDEX_HTML": str(INDEX_HTML)},
    )
    out = json.loads(result.stdout)
    assert "Không tìm thấy lá nào" in out["emptySuggestHtml"]
    assert "Chưa có lá nhớ nào" not in out["emptySuggestHtml"]
    assert "Ghi chú sắp hạn" in out["beforeReinforceHtml"] and out["countBefore"] == "1 lá"
    assert 'class="mem-leaf is-alert"' in out["beforeReinforceHtml"]
    assert "Ghi chú sắp hạn" not in out["afterReinforceHtml"] and out["countAfter"] == "0/1 lá"
    assert "Sắp hết hạn" in out["beforeReinforceHtml"]
    assert 'data-stat-filter="expiring" aria-pressed="true"><span>Sắp hết hạn</span><b>0</b>' in out["statsAfter"]
    assert 'class="mem-leaf"' in out["afterClearHtml"]
    assert "is-alert" not in out["afterClearHtml"]
    assert "me1" not in out["afterClearHtml"]
    assert out["gets"] == ["/api/memory/stats"]


def test_chunks_sharing_memory_id_merge_into_one_row() -> None:
    """Multiple chunks from the same memory_id collapse into one row, summing
    usage counts, combining unique snippets, and syncing tab counts."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    setup = """
        const CHUNKS = [
            { chunk_id: 'm1#1', memory_id: '2026-11-25#81078be7',
              heading: '## 10:59 — Top model ngày 25/11/2026', snippet: '- Đoạn một',
              timeline_day: '2026-11-25', get_count: 1, search_count: 2, bots: ['march7th'] },
            { chunk_id: 'm1#2', memory_id: '2026-11-25#81078be7',
              heading: '## 10:59 — Top model ngày 25/11/2026', snippet: '- Đoạn hai',
              timeline_day: '2026-11-25', get_count: 2, search_count: 0, bots: ['march7th'] },
            { chunk_id: 'm2#1', memory_id: '2026-11-25#aaaaaaaa',
              heading: '## 11:00 — Ghi chú riêng', snippet: '- Ghi chú khác',
              timeline_day: '2026-11-25', get_count: 0, search_count: 0, bots: ['thyca'] },
        ];
        hooks.getJson = async () => ({
            bot_id: null, total: 3, used: 2, unused: 1, searched: 1, untouched: 1,
            expiring: [], suggest_removal: [{ chunk_id: 'm2#1' }], leaves: CHUNKS,
        });
    """
    body = """
        await settle();
        const html = document.querySelector('#memory-leaves').innerHTML;
        console.log(JSON.stringify({
            rows: (html.match(/<details class="mem-leaf/g) || []).length,
            count: document.querySelector('#memory-count').textContent,
            stats: document.querySelector('#memory-stats').innerHTML,
            html,
        }));
    """
    result = subprocess.run(
        [node, "--input-type=module", "-e", _HARNESS + setup + _EVAL + body],
        cwd=ROOT, check=True, capture_output=True, text=True, timeout=15,
        env={**os.environ, "MEM_JS": str(MEM_JS), "INDEX_HTML": str(INDEX_HTML)},
    )
    out = json.loads(result.stdout)
    assert out["rows"] == 2
    assert out["count"] == "2 lá"
    assert "<span>Tất cả</span><b>2</b>" in out["stats"]
    assert "<span>Đã dùng</span><b>1</b>" in out["stats"]
    assert "<span>Gợi ý xóa</span><b>1</b>" in out["stats"]
    assert "Đoạn một" in out["html"] and "Đoạn hai" in out["html"]
    assert "Đã dùng 3" in out["html"] and "Tìm thấy 2" in out["html"]
