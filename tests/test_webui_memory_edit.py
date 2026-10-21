"""Execute ui/memories/js/memories.js with a minimal DOM harness.

The rebuilt Nhật ký page keeps one leaf list grouped by day and paged 60
leaves at a time. Bot scope is no longer a toolbar <select>: sidebar bot
cards and drawer sessions route through selectBot()/loadSession(), which
toggle the scope, rewrite ?bot= in the URL, hit stats?bot_id= and fill the
scope card (with a "Bỏ lọc" button). Kind/use filtering and search stay
client-side via the #memory-kind / #memory-use ghost selects. These
scenarios cover scoped stats loads with echo validation, stale-answer
dropping, the three leaf actions (reinforce / forget / update), the
filters + pager, and ?bot= deep links.
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
  return { memState, loadStats, mutate, openEdit, selectBot, loadSession,
           renderAll, renderLeaves, renderScopeCard, filteredLeaves,
           splitHeading, get selectedBot() { return selectedBot; } };
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
  selectedBot: page.selectedBot,
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
  scope: page.memState.scope, selected: page.selectedBot,
  url: calls.urls[calls.urls.length - 1],
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
// A drawer session routes through loadSession to the owning bot's scope.
page.loadSession('s9', 'Phiên mới', 'researcher');
await settle();
const fromSession = {
  scope: page.memState.scope, selected: page.selectedBot,
  url: calls.urls[calls.urls.length - 1],
  card: document.querySelector('#memory-scope-card').innerHTML,
};
// The scope card's "Bỏ lọc" button clears the filter in place.
fire(document.querySelector('#memory-scope-card'), 'click',
     { target: { closest: s => (s === '.mem-scope-clear' ? {} : null) } });
await settle();
console.log(JSON.stringify({
  scoped, toggled, fromSession,
  cleared: {
    scope: page.memState.scope, url: calls.urls[calls.urls.length - 1],
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
  selected: page.selectedBot,
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
  cardHidden: document.querySelector('#memory-scope-card').hidden,
}));
"""),

    "filters": (r"""
const many = [];
for (let i = 0; i < 65; i++) many.push({
  chunk_id: 'k' + i, session_id: 'sk' + i,
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
};
// The kind + use ghost selects filter client-side — no refetch.
document.querySelector('#memory-kind').onchange({ target: { value: 'habit' } });
const habit = { leaves: leafCount(),
  count: document.querySelector('#memory-count').textContent };
document.querySelector('#memory-use').onchange({ target: { value: 'untouched' } });
const habitUntouched = { leaves: leafCount(),
  count: document.querySelector('#memory-count').textContent };
// Reset both, then page two via the delegated pager button.
document.querySelector('#memory-kind').onchange({ target: { value: '' } });
document.querySelector('#memory-use').onchange({ target: { value: '' } });
fireAct('next');
const pageTwo = {
  leaves: leafCount(), memPage: page.memState.page,
  info: (document.querySelector('#memory-leaves').innerHTML.match(/Trang \d+\/\d+/) || [''])[0],
};
console.log(JSON.stringify({
  pageOne, habit, habitUntouched, pageTwo, gets: calls.gets,
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
    """Boot loads the global stats — no bot_id — into tiles + the leaf list;
    the scope card stays hidden until a sidebar bot is chosen. The toolbar
    carries kind/use/search filters — the old #memory-bot select is gone."""
    assert stats["gets"] == ["/api/memory/stats"]
    for tile in ("Tổng lá", "Đã dùng", "Được tìm", "Sắp hết hạn", "Gợi ý xóa"):
        assert tile in stats["stats"], tile
    assert "Trang một" in stats["leaves"]
    assert "Nội dung một" in stats["leaves"]
    assert "mem-leaf" in stats["leaves"]
    assert stats["count"] == "1 lá"
    assert stats["scopeCardHidden"] is True
    ws = stats["workspace"]
    assert 'id="memory-bot"' not in ws
    for marker in ('id="memory-kind"', 'id="memory-use"',
                   'id="memory-search"', 'id="memory-reload"'):
        assert marker in ws, marker
    assert 'value="habit"' in ws and 'value="checkpoint"' in ws
    assert 'value="used"' in ws and 'value="untouched"' in ws


def test_scope_echo_mismatch_rejected(scope: dict) -> None:
    """selectBot() scopes through stats?bot_id= — the endpoint echoes the
    applied scope and a mismatched answer is treated as stale: status shows
    the mismatch, no leaves render."""
    assert "/api/memory/stats?bot_id=coder" in scope["gets"]
    assert any(u.endswith("?bot=coder") for u in scope["urls"])
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
    assert scope_race["stale"]["total"] == 0   # boot stats still in place
    assert scope_race["finalTotal"] == 2
    assert scope_race["scope"] == "researcher"
    assert scope_race["urls"][-1].endswith("?bot=researcher")


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


def test_scope_comes_from_sidebar_clicks_and_url(routing: dict) -> None:
    """Bot scope moved out of the deleted toolbar <select> into the sidebar:
    selectBot() toggles the filter + rewrites ?bot=, drawer sessions route
    through loadSession(), and the scope card's "Bỏ lọc" clears it — each
    re-render keeps the roster highlight in sync."""
    assert routing["scoped"]["scope"] == "coder"
    assert routing["scoped"]["selected"] == "coder"
    assert routing["scoped"]["url"].endswith("?bot=coder")
    assert routing["scoped"]["cardHidden"] is False
    assert "Coder" in routing["scoped"]["card"]

    # Same bot again toggles back to the team journal.
    assert routing["toggled"]["scope"] == ""
    assert routing["toggled"]["cardHidden"] is True
    assert "?bot=" not in routing["toggled"]["url"]

    # Drawer sessions open the owning bot's scope, not the chat page.
    assert routing["fromSession"]["scope"] == "researcher"
    assert routing["fromSession"]["url"].endswith("?bot=researcher")
    assert "Researcher" in routing["fromSession"]["card"]

    # Bỏ lọc clears the filter without leaving the page.
    assert routing["cleared"]["scope"] == ""
    assert routing["cleared"]["cardHidden"] is True
    assert "?bot=" not in routing["cleared"]["url"]

    assert routing["gets"].count("/api/memory/stats?bot_id=coder") == 1
    assert "/api/memory/stats?bot_id=researcher" in routing["gets"]
    assert routing["teamRenders"] > 0     # sidebar highlight follows scope


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
    assert deeplink_stale["urls"] == ["/memories/index.html"]


def test_kind_use_filters_page_through_60_leaves(filters: dict) -> None:
    """The ghost selects + pager replaced the old category views: kind and
    use filter the loaded leaves client-side (count follows 'shown/total')
    and .mem-pager buttons walk 60-leaf pages — all without a refetch."""
    assert filters["pageOne"]["leaves"] == 60
    assert filters["pageOne"]["pager"] is True
    assert filters["pageOne"]["info"] == "Trang 1/2"
    assert filters["habit"] == {"leaves": 22, "count": "22/65 lá"}
    assert filters["habitUntouched"] == {"leaves": 11, "count": "11/65 lá"}
    assert filters["pageTwo"]["leaves"] == 5
    assert filters["pageTwo"]["info"] == "Trang 2/2"
    assert filters["pageTwo"]["memPage"] == 2
    assert filters["gets"] == ["/api/memory/stats"]
