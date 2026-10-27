"""Chat markdown: GFM tables and safe HTML via marked (ui/share/markdown.js),
plus structure/behavior checks for the rebuilt pages that render it.

The page scripts are classic scripts ('use strict', shared globals), so the
behavior tests eval them in Node with a tiny auto-vivifying DOM stub instead
of importing them."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from test_team_frontend import _unit_factory_source

ROOT = Path(__file__).resolve().parents[1]
WEBUI = ROOT / "thyca" / "webui"
UI = WEBUI / "ui"
MARKDOWN_JS = UI / "share" / "markdown.js"
MARKDOWN_CSS = UI / "share" / "markdown.css"
MARKED_ESM = UI / "share" / "vendor" / "marked.esm.js"
CHAT_JS = UI / "chat" / "js" / "chat.js"
CHAT_CSS = UI / "chat" / "chatcss" / "chat.css"
DASH_JS = UI / "dashboard" / "js" / "dashboard.js"
DASH_CSS = UI / "dashboard" / "dashboardcss" / "dashboard.css"
TRACE_JS = UI / "trace" / "js" / "trace.js"
MEM_JS = UI / "memories" / "js" / "memories.js"


def _render(src: str) -> str:
    """ui/share/markdown.js is still ESM — it just publishes window.formatMarkdown
    instead of exporting the symbol."""
    script = f"""
    globalThis.window = globalThis;
    await import({json.dumps(MARKDOWN_JS.as_uri())});
    process.stdout.write(window.formatMarkdown({json.dumps(src)}));
    """
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def test_table_renders_cells() -> None:
    html = _render("| Phần | Ý nghĩa |\n|---|---|\n| `u` | timestamp |\n| c | CDR |")
    assert '<div class="md-table-wrap"><table>' in html
    assert "<th>Phần</th>" in html
    assert "<code>u</code>" in html
    assert "<td>CDR</td>" in html


def test_heading_fence_and_break() -> None:
    html = _render("## Title\n\nline one\nline two\n\n```\necho hi\n```")
    assert "<h2>Title</h2>" in html
    assert "<br>" in html
    assert "<pre>" in html
    assert "echo hi" in html


def test_escapes_raw_html_and_unsafe_url() -> None:
    html = _render("click <script>alert(1)</script> [x](javascript:alert(1))")
    assert "<script>" not in html
    assert "javascript:" not in html
    assert "&lt;script&gt;" in html
    assert ">x</a>" not in html


def test_chat_js_uses_formatter() -> None:
    """Assistant replies render through the shared sanitizer: every markdown
    page loads share/markdown.js as a module and calls window.formatMarkdown
    (with an escaped-paragraph fallback while the module evaluates)."""
    chat = CHAT_JS.read_text(encoding="utf-8")
    assert "window.formatMarkdown(text)" in chat
    assert "contentHtml(m.content)" in chat
    entry = (UI / "chat" / "index.html").read_text(encoding="utf-8")
    assert '<script type="module" src="../share/markdown.js">' in entry
    css = MARKDOWN_CSS.read_text(encoding="utf-8")
    assert ".md-table-wrap" in css
    assert MARKED_ESM.is_file()


def test_ui_classic_scripts_parse() -> None:
    """The old mapper modules are gone — page logic lives in classic scripts
    now. They can't be import-checked, but every one must parse, and the one
    remaining module (share/markdown.js) must still import clean in Node."""
    bad = []
    for js in sorted(UI.rglob("*.js")):
        if "vendor/" in js.as_posix() or js == MARKDOWN_JS:
            continue
        src = js.read_text(encoding="utf-8")
        assert not src.lstrip().startswith(("import ", "export ")), js.name
        result = subprocess.run(
            ["node", "-e", f"new Function({json.dumps(src)})"],
            capture_output=True, text=True,
        )
        if result.returncode:
            bad.append(f"{js.relative_to(UI)}: {result.stderr.splitlines()[0]}")
    assert not bad, bad
    script = f"""
    globalThis.window = globalThis;
    await import({json.dumps(MARKDOWN_JS.as_uri())});
    if (typeof window.formatMarkdown !== 'function') throw new Error('no formatter');
    process.stdout.write('markdown-module-ok');
    """
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True, capture_output=True, text=True,
    )
    assert result.stdout == "markdown-module-ok"


# ---------------------------------------------------------------------------
# Minimal DOM harness for the page scripts: elements auto-vivify per selector
# and innerHTML stays a plain string the tests can read back.
# ---------------------------------------------------------------------------

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

const calls = { gets: [], posts: [], toasts: [], urls: [] };
const hooks = {
  getJson: async () => ({}),
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
  location: { hash: '', search: '', pathname: '/index.html', href: '' },
  history: { replaceState(_s, _t, u) { calls.urls.push(u); } },
  addEventListener() {},
  matchMedia: () => ({ matches: false, addEventListener() {} }),
  localStorage: { getItem: () => null, setItem() {} },
  bots: ROSTER,
  groups: [],
  /* Sidebar globals page scripts may drive (memories re-renders the roster
     and marks the scoped card on selectBot). */
  selectedBot: '',
  renderTeam() {},
  getBot: id => ROSTER.find(b => b.id === id) || null,
  states: { idle: ['idle', 'Rảnh'] },
  avatar: () => '',
  paletteStyle: () => '',
  icon: () => '',
  escapeHTML: s => String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;'),
  getJson: async (u, o) => { calls.gets.push(u); return hooks.getJson(u, o); },
  postJson: async (u, b) => { calls.posts.push({ url: u, body: b }); return hooks.postJson(u, b); },
  toast: t => calls.toasts.push(t),
  confirm: () => true,
  bootShell: async () => {},
};
const ctx = { root: document, route: { page: 'dashboard', search: deps.location.search, hash: deps.location.hash }, shellReady: deps.bootShell(), $: (s) => s === '.workspace' ? document : deps.$(s, document), $$: (s) => deps.$$(s, document), navigate(page, parts = {}) { this.route = { page, search: '', hash: '', ...parts }; }, updateRoute(parts) { Object.assign(this.route, parts); calls.urls.push({ ...this.route }); }, highlightBot() {}, renderTeam() {}, isActive: () => true, onRoute: () => () => {} };
deps.ctx = ctx;
const settle = async () => { for (let i = 0; i < 6; i++) await new Promise(r => setImmediate(r)); };
const fire = (el, type, event = {}) => {
  event.preventDefault ??= () => {};
  for (const fn of el.listeners[type] ?? []) fn(event);
};
"""


def _node() -> str:
    binary = shutil.which("node")
    if not binary:
        pytest.skip("node not installed")
    return binary


def _run_page(page_js: Path, ret: str, scenario: str, setup: str = "") -> dict:
    source = page_js.read_text(encoding="utf-8")
    if page_js in (DASH_JS, TRACE_JS, MEM_JS):
        source = _unit_factory_source(page_js, source)
    script = (
        _HARNESS
        + setup
        + f"\nctx.route.page = {json.dumps(page_js.parent.parent.name)};\nconst source = {json.dumps(source)};\n"
        + f"const page = new Function(...Object.keys(deps), source + {json.dumps(ret)})"
          "(...Object.values(deps));\n"
        + "if (globalThis.__controller?.ready) await globalThis.__controller.ready;\n"
        + scenario
        + "\n"
    )
    result = subprocess.run(
        [_node(), "--input-type=module", "-e", script],
        cwd=ROOT, check=True, capture_output=True, text=True, timeout=15,
    )
    return json.loads(result.stdout)


# ---------------------------------------------------------------------------
# Dashboard page
# ---------------------------------------------------------------------------

_DASH_SETUP = r"""
const STATS = {
  totals: { requests: 12, tool_calls: 3, prompt_tokens: 1000, cached_tokens: 100,
            completion_tokens: 200, total_tokens: 1200, cost_usd: 1.2345,
            latency_ms_p50: 800, latency_ms_p90: 2100 },
  by_day: [{ day: '2026-09-14', requests: 4, cost_usd: 0.5 },
           { day: '2026-09-15', requests: 0, cost_usd: null }],
  by_hour: [{ hour: '09', requests: 2, cost_usd: 0.1 }],
  by_bot: [
    { bot_id: 'coder', requests: 9, tool_calls: 2, total_tokens: 900,
      cost_usd: 0.9, last_started_at: '2026-09-14T09:00:00Z' },
    { bot_id: 'ghost', requests: 3, tool_calls: 1, total_tokens: 300,
      cost_usd: null, last_started_at: '2026-09-13T09:00:00Z' },
  ],
  by_model: [
    { model: 'cheap-model', requests: 2, total_tokens: 100, cost_usd: 0.02,
      latency_ms_p50: 300, last_started_at: '2026-09-14T09:00:00Z' },
    { model: 'big-model', requests: 8, total_tokens: 5000, cost_usd: null,
      latency_ms_p50: 900, last_started_at: '2026-09-13T09:00:00Z' },
    { model: 'mid-model', requests: 5, total_tokens: 2000, cost_usd: 1.5,
      latency_ms_p50: 500, last_started_at: '2026-09-12T09:00:00Z' },
  ],
  models: ['big-model', 'mid-model', 'cheap-model'],
  by_status: [{ status: 'completed', requests: 10 }],
};
const TRACES = { traces: [{
    session_id: 's-1', turn_index: 2, title: 'Phiên A',
    started_at: '2026-09-14T10:00:00Z', status: 'completed', bot_id: 'coder',
    model: 'mid-model', requests: 2, tool_calls: 1, total_tokens: 300,
    cost_usd: 0.05, latency_ms: 900,
  }], total: 1 };
hooks.getJson = async url => {
  if (url.startsWith('/api/traces/stats')) return STATS;
  if (url.startsWith('/api/traces')) return TRACES;
  return {};
};
"""

_DASH_RET = """; return {
  get statsData() { return statsData; },
  get dayMetric() { return dayMetric; },
  setDayMetric(v) { dayMetric = v; },
  renderStats, renderModelRows, renderDayChart, renderCostChart, renderBotRows, renderTraceTable,
  setRange(from, to) { rangeFrom = from; rangeTo = to; },
  fmtCost, fmtInt, fmtDur, traceState, loadTraces, dayColumns,
};"""

_DASH_SCENARIO = r"""
await settle();
const rowsModel = document.querySelector('#rows-model').innerHTML;
// Keep boot's default seven-day requests intact; set the local chart range to
// the fixture dates without changing controls or triggering another request.
page.setRange('2026-09-14', '2026-09-15');
page.setDayMetric('requests');
page.renderDayChart();
page.renderCostChart();
console.log(JSON.stringify({
  statGrid: document.querySelector('#stat-grid').innerHTML,
  rowsBot: document.querySelector('#rows-bot').innerHTML,
  rowsModel,
  chartDay: document.querySelector('#chart-day').innerHTML,
  chartCost: document.querySelector('#chart-cost').innerHTML,
  chartHour: document.querySelector('#chart-hour').innerHTML,
  traceRows: document.querySelector('#trace-sessions').innerHTML,
  filterModel: document.querySelector('#filter-model').children.map(o => o.textContent),
  gets: calls.gets,
}));
"""


def test_dashboard_renders_stats_models_and_paged_rows() -> None:
    """Usage renders hero stats, ranked model/bot rows and flat turn rows;
    daily model calls and recorded costs use separate charts. A model without
    a configured price shows '—' (never an invented $0); rows rank by tokens."""
    out = _run_page(DASH_JS, _DASH_RET, _DASH_SCENARIO, _DASH_SETUP)

    assert "stat-value" in out["statGrid"]
    assert "$1.23" in out["statGrid"]          # fmtCost(1.2345)

    # by_model ranks by total_tokens desc; unpriced model keeps '—'.
    assert out["rowsModel"].index("big-model") < out["rowsModel"].index("mid-model")
    assert out["rowsModel"].index("mid-model") < out["rowsModel"].index("cheap-model")
    assert "—" in out["rowsModel"]
    assert "$1.50" in out["rowsModel"]

    # by_bot resolves roster names; missing bots keep the raw id in a chip.
    assert "Coder" in out["rowsBot"]
    assert "ghost" in out["rowsBot"]
    assert "id-chip" in out["rowsBot"]

    assert 'aria-label="Lượt gọi model theo ngày"' in out["chartDay"] and "dc-bar" in out["chartDay"]
    assert "dc-bar" in out["chartCost"] and "Chi phí" in out["chartCost"]
    assert "$0.5000" in out["chartCost"] and "is-unavailable" in out["chartCost"]
    assert "hc-col" in out["chartHour"]
    assert "Tất cả model" in out["filterModel"]
    assert "big-model" in out["filterModel"]

    # Usage owns flat turn rows; detail is fetched only after selection.
    assert 'class="tl-turn"' in out["traceRows"]
    assert 'data-session="s-1" data-turn="2"' in out["traceRows"]
    assert 'class="tl-session"' not in out["traceRows"]

    # Paged loading: stats + first trace page requested on boot.
    stats_url = next(u for u in out["gets"] if u.startswith("/api/traces/stats"))
    assert "from=" in stats_url and "to=" in stats_url
    traces_url = next(u for u in out["gets"] if u.startswith("/api/traces?"))
    assert "limit=15" in traces_url and "offset=0" in traces_url
    assert not any(u.startswith("/api/traces/s-") for u in out["gets"])


def test_dashboard_page_structure() -> None:
    """The rebuilt Sử dụng page has route-persisted views, range presets,
    separate daily token/request and cost charts, ranked rows and filters."""
    js = DASH_JS.read_text(encoding="utf-8")
    css = DASH_CSS.read_text(encoding="utf-8")
    entry = (UI / "chat" / "index.html").read_text(encoding="utf-8")

    assert 'src="../dashboard/js/dashboard.js"' in entry

    # Range controls: manual dates + Hôm nay/7 ngày/30 ngày presets.
    for marker in ('id="range-from"', 'id="range-to"', 'id="range-apply"',
                   'data-preset="1"', 'data-preset="7"', 'data-preset="30"'):
        assert marker in js, marker

    # Usage toggles token/request measures; Cost has its own chart and view.
    assert "let dayMetric = 'tokens'" in js
    assert 'data-day-metric="requests"' in js
    assert 'data-day-metric="tokens"' in js
    assert 'id="chart-cost"' in js
    assert all(js.count(f'data-usage-view="{view}"') == 1 for view in ("usage", "cost", "trace"))
    assert "dc-col" in js and "dc-bar" in js
    assert ".day-chart" in css and ".dc-bar" in css

    # Ranked row surfaces: share meter for bots, cost column for models.
    for marker in ('id="rows-bot"', 'id="rows-model"', "share-bar", "model-cost"):
        assert marker in js, marker
    assert re_block(css, ".bot-row", "display:grid", "align-items:center")
    assert ".share-bar" in css and ".model-cost" in css

    # Flat turn list keeps model/status filters and bounded paging.
    assert 'id="filter-model"' in js and 'id="filter-status"' in js
    assert "q.set('offset', offset)" in js
    assert "const offset = reset ? 0 : (offsetOverride ?? traceState.rows.length)" in js
    assert "q.set('limit', reset ? FIRST_PAGE : MORE_PAGE)" in js
    assert "#trace-more').onclick" in js and "loadTraces(false)" in js


def re_block(css: str, selector: str, *needles: str) -> bool:
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    return bool(match) and all(n in match.group(1) for n in needles)


# ---------------------------------------------------------------------------
# Trace page
# ---------------------------------------------------------------------------

_TRACE_SETUP = r"""
const TRACES = { traces: [
    { session_id: 's-1', turn_index: 0, title: 'Phiên A', session_kind: 'dm',
      started_at: '2026-09-14T10:00:00Z', status: 'completed', bot_id: 'coder',
      model: 'mid-model', total_tokens: 120, cost_usd: 0.02, latency_ms: 600 },
    { session_id: 's-1', turn_index: 1, title: 'Phiên A', session_kind: 'dm',
      started_at: '2026-09-14T10:05:00Z', status: 'completed', bot_id: 'coder',
      model: 'mid-model', total_tokens: 140, cost_usd: 0.01, latency_ms: 900 },
  ], total: 2 };
const TURN = {
  session_id: 's-1', turn_index: 1, title: 'Phiên A', bot_id: 'coder',
  model: 'mid-model', requests: 2, rounds: 1, tool_calls: 1,
  prompt_tokens: 100, cached_tokens: 10, completion_tokens: 40,
  total_tokens: 140, cost_usd: 0.01, latency_ms: 900,
  started_at: '2026-09-14T10:05:00Z', ended_at: '2026-09-14T10:05:06Z',
  status: 'completed',
  bot_breakdown: [{ bot_id: 'coder', requests: 2, total_tokens: 140 }],
  messages: [
    { role: 'user', content: 'Xin chào', ts: '2026-09-14T10:05:00Z', meta: {} },
    { role: 'assistant', content: '**Chào** bạn', ts: '2026-09-14T10:05:01Z',
      meta: { bot_id: 'coder', model: 'mid-model' },
      tool_calls: [{ id: 'call-1', skill: 'bash', name: 'bash',
                     arguments: { cmd: 'ls' } }] },
    { role: 'tool', tool_call_id: 'call-1', content: 'file.txt',
      ts: '2026-09-14T10:05:02Z', meta: {} },
  ],
};
hooks.getJson = async url => {
  if (url.startsWith('/api/traces/')) return TURN;
  if (url.startsWith('/api/traces')) return TRACES;
  return {};
};
deps.window.formatMarkdown = s => '<md>' + s + '</md>';
"""

_TRACE_RET = "; return { render: detail => window.ThycaTraceDetail.render(detail) };"

_TRACE_SCENARIO = r"""
console.log(JSON.stringify({ detailHtml: page.render(TURN) }));
"""


def test_trace_detail_renderer_renders_turn_content() -> None:
    """The retained detail renderer preserves markdown and numbered tool
    steps without owning a route, list, or API request."""
    out = _run_page(TRACE_JS, _TRACE_RET, _TRACE_SCENARIO, _TRACE_SETUP)
    detail = out["detailHtml"]
    assert "tl-detail-head" in detail
    assert "Coder" in detail
    assert "tl-body markdown-body" in detail
    assert "<md>**Chào** bạn</md>" in detail
    assert "tl-step-num" in detail and ">01</span>" in detail
    assert ">Input</p>" in detail and ">Output</p>" in detail
    assert "file.txt" in detail


def test_trace_page_structure() -> None:
    """Static contract: the SPA entry mounts the detail renderer, both
    deep-link forms parse, and its typography mirrors the chat bubbles
    it replays."""
    js = TRACE_JS.read_text(encoding="utf-8")
    entry = (UI / "chat" / "index.html").read_text(encoding="utf-8")
    chat = CHAT_CSS.read_text(encoding="utf-8")

    assert 'src="../trace/js/trace.js"' in entry
    assert '<script type="module" src="../share/markdown.js">' in entry

    # No standalone Trace document anymore; this source is only the
    # load-once renderer used by the Dashboard controller.
    assert "window.ThycaTraceDetail" in js and "render(detail)" in js
    assert "ThycaRouter.register" not in js and "getJson(" not in js
    assert "window.formatMarkdown(text)" in js
    assert "contentHtml(m.content)" in js
    assert "tl-body markdown-body" in js
    dashboard_css = DASH_CSS.read_text(encoding="utf-8")
    assert ".dashboard-shell .tl-detail-head" in dashboard_css

    # Bubbles and bylines mirror chat's message furniture.
    for sel, needles in (
        (".dashboard-shell .tl-user", ("border-radius:14px 14px 3px 14px", "background:#eee6d56b")),
        (".message-user", ("border-radius:14px 14px 3px 14px", "background:#eee6d56b")),
        (".dashboard-shell .tl-byline", ("var(--bot-ink,var(--forest))", "var(--serif)")),
        (".message-byline", ("var(--bot-ink,var(--forest))", "var(--serif)")),
        (".dashboard-shell .tl-body", ("var(--serif)", "color:#433d30")),
        (".live-copy", ("var(--serif)", "color:#433d30")),
    ):
        sheet = dashboard_css if sel.startswith(".dashboard-shell") else chat
        assert re_block(sheet, sel, *needles), sel
    assert "ui-monospace" in re.search(
        r"\.dashboard-shell \.tl-code\s*\{([^}]*)\}", dashboard_css).group(1)


# ---------------------------------------------------------------------------
# Memories page (leaf grouping lives where the old selectMemories stood)
# ---------------------------------------------------------------------------

_MEM_SETUP = r"""
const STATS = {
  bot_id: null, total: 3, used: 2, unused: 1, searched: 1, untouched: 1,
  expiring: [], suggest_removal: [],
  leaves: [
    { chunk_id: 'a', session_id: 's1', heading: '## 09:30 — Trang một',
      snippet: '- Nội dung một', timeline_day: '2026-09-19',
      get_count: 1, search_count: 0, bots: ['coder'] },
    { chunk_id: 'b', session_id: 's2', heading: '## 10:00 — Trang hai',
      snippet: '- Nội dung hai', timeline_day: '2026-09-21',
      get_count: 4, search_count: 1, bots: ['coder'] },
    { chunk_id: 'c', session_id: 's3', heading: '## 11:00 — Trang ba',
      snippet: '- Nội dung ba', timeline_day: '2026-09-21',
      get_count: 0, search_count: 2, bots: ['researcher'] },
  ],
};
hooks.getJson = async url => (url === '/api/memory/stats' ? STATS : {});
"""

_MEM_RET = """; return {
  memState, loadStats, renderAll, renderLeaves, filteredLeaves,
  splitHeading, dayLabel,
};"""

_MEM_SCENARIO = r"""
await settle();
const leavesHtml = document.querySelector('#memory-leaves').innerHTML;
const count = document.querySelector('#memory-count').textContent;
fire(document.querySelector('#memory-search'), 'input', { target: { value: 'hai' } });
console.log(JSON.stringify({
  stats: document.querySelector('#memory-stats').innerHTML,
  count,
  countAfter: document.querySelector('#memory-count').textContent,
  leavesHtml,
  filteredHtml: document.querySelector('#memory-leaves').innerHTML,
}));
"""


def test_memories_groups_leaves_by_day_and_filters() -> None:
    """The leaf list replaces the old view picker: payload order is kept
    inside day groups sorted newest-first, and the toolbar search filters
    across title/snippet/session instead of re-sorting."""
    out = _run_page(MEM_JS, _MEM_RET, _MEM_SCENARIO, _MEM_SETUP)

    assert "Tổng lá" in out["stats"]
    assert out["count"] == "3 lá"

    html = out["leavesHtml"]
    assert html.count('class="mem-day"') == 2
    # 21/9 group before 19/9; inside a day the API order is kept.
    day21, day19 = html.index("21/9/2026"), html.index("19/9/2026")
    assert day21 < day19
    assert html.index("Trang hai") < html.index("Trang ba") < day19
    for title in ("Trang một", "Trang hai", "Trang ba"):
        assert title in html

    # Search narrows to the matching leaf, and the counter follows it.
    assert "Trang hai" in out["filteredHtml"]
    assert "Trang một" not in out["filteredHtml"]
    assert out["countAfter"] == "1/3 lá"


# ---------------------------------------------------------------------------
# Shared sidebar + navigation (replaces the chat session pager)
# ---------------------------------------------------------------------------

def test_shared_sidebar_renders_team_and_groups() -> None:
    """The shared sidebar lists the roster as bot cards on every page; the
    "Hội thoại chung" group section now only ships in chat's static markup,
    so sidebar.js guards its #group-list lookups. Card actions still
    delegate to the page's selectBot/loadSession, whose shared defaults
    route back into the chat page."""
    sidebar = (UI / "share" / "sidebar.js").read_text(encoding="utf-8")
    shell = (UI / "share" / "shell.js").read_text(encoding="utf-8")
    shell_app = (UI / "share" / "shell-app.js").read_text(encoding="utf-8")

    chat = (UI / "chat" / "index.html").read_text(encoding="utf-8")

    # Roster cards render into the shell's sidebar markup.
    assert 'id="team-bot-list"' in shell
    assert "bots.map(card)" in sidebar
    assert "bot-card" in sidebar

    # Group rows still render — but only where a #group-list exists. The
    # shared shell no longer emits one; chat's static sidebar does, and
    # sidebar.js guards every lookup so non-chat pages skip the section.
    assert 'id="group-list"' not in shell
    assert "groups.map(g=>" in sidebar or "groups.map(g =>" in sidebar
    assert "group-row" in sidebar
    assert "if(!gl)return" in sidebar
    assert 'id="group-list"' in chat
    assert 'id="team-shared-create"' in chat
    assert "Hội thoại chung" in chat
    assert "if(teamSharedCreate)" in sidebar

    # Click delegation: bot select / group open go through the page's
    # selectBot/loadSession; defaults navigate into the chat page.
    assert "$('#team-bot-list').addEventListener('click'" in sidebar
    assert "$('#group-list')?.addEventListener('click'" in sidebar
    assert "selectBot(b.id)" in sidebar
    assert "loadSession(g.id" in sidebar
    assert "ThycaRouter.dispatch('selectBot', id)" in shell_app
    assert "ThycaRouter.dispatch('loadSession', id, title, botId)" in shell_app
