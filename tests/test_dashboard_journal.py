"""Tests for the rebuilt Dashboard — thyca/webui/ui/dashboard/.

The old four-view dashboard (overview.js entity selector, hash routing
#su-dung/#request/#chi-phi/#hom-nay -> #trace, fetchAllTraces dedupe
paging in dashboard-today.js) was not ported. The new page is one screen —
"Sử dụng & chi phí": range picker + stat cards + by_day/by_bot/by_model/
by_hour charts + the paged "Lượt gần nhất" table, all from
GET /api/traces/stats and GET /api/traces, inside the shared emitShell/
bootShell contract. Trace detail renders in the same Dashboard workspace through the route hash.
The page is a classic script ('use strict', shared globals), so the harness
evals it in Node with a stubbed global surface, mirroring the old
import-based eval style.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from test_team_frontend import _unit_factory_source

ROOT = Path(__file__).resolve().parents[1]
WEBUI = ROOT / "thyca" / "webui" / "ui"
DASH_JS = WEBUI / "dashboard" / "js" / "dashboard.js"
UTIL_JS = WEBUI / "share" / "util.js"

STUBS = r"""
class El {
  constructor(tag = 'div') {
    this.tag = tag; this.value = ''; this.textContent = ''; this._html = '';
    this.hidden = false; this.disabled = false; this.dataset = {}; this.attrs = {};
    this.listeners = {}; this.children = []; this.options = []; this.className = '';
    this.style = {};
  }
  set innerHTML(v) { this._html = String(v); }
  get innerHTML() { return this._html; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  addEventListener(t, f) { (this.listeners[t] ||= []).push(f); }
  replaceChildren(...n) { this.children = n; this.options = n; }
  insertAdjacentHTML(_p, h) { this._html = (this._html || '') + h; }
  set onclick(f) { this.listeners.click = [f]; }
  set onchange(f) { this.listeners.change = [f]; }
}
const __els = {};
const __el = (id) => __els[id] ||= new El();
const __workspace = new El();
__workspace.querySelector = (s) => s.startsWith('#') ? __el(s.slice(1)) : new El();
__workspace.querySelectorAll = () => [];
globalThis.document = {
  querySelector: (s) => s === '.workspace' ? __workspace
    : s.startsWith('#') ? __el(s.slice(1)) : null,
  querySelectorAll: () => [],
  createElement: (t) => new El(t),
};
globalThis.location = { hash: '', search: '', pathname: '/dashboard/index.html', href: '' };
globalThis.window = {};
globalThis.$ = (s, r = document) => r.querySelector(s);
globalThis.$$ = (s, r = document) => [...r.querySelectorAll(s)];
globalThis.icon = (n) => `<svg data-i="${n}"></svg>`;
globalThis.escapeHTML = (str) => String(str).replace(/[&<>"']/g, (ch) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
globalThis.__roster = {};
globalThis.dmKey = (botId) => 'dm:' + botId;
globalThis.groupKey = (groupId) => 'group:' + groupId;
globalThis.convParts = (key) => {
  const text = String(key || '');
  if (text.startsWith('dm:')) return { kind: 'dm', bot_id: text.slice(3) };
  if (text.startsWith('group:')) return { kind: 'group', group_id: text.slice(6) };
  return null;
};
globalThis.getBot = (id) => globalThis.__roster[id] || null;
globalThis.avatar = (b) => `<ava:${b.id}/>`;
const __toasts = [];
globalThis.toast = (m) => __toasts.push(String(m));
globalThis.bootShell = async () => {
  globalThis.__roster.coder = { id: 'coder', name: 'Coder', color: '#123', state: 'idle' };
};
let __releaseShell;
const __shellReady = new Promise(resolve => { __releaseShell = async () => { await bootShell(); resolve(); }; });
const __jsonCalls = [];
const __pending = [];
let __jsonResponder = async (url) => { throw new Error('unexpected ' + url); };
globalThis.getJson = (url) => { __jsonCalls.push(String(url)); return __jsonResponder(String(url)); };
const __deferred = () => { let res; const p = new Promise(r => { res = r; }); p.__res = res; return p; };
"""


@pytest.fixture(scope="module")
def node() -> str:
    binary = shutil.which("node")
    if not binary:
        pytest.skip("node not installed")
    return binary


def _eval_dash(node: str, body: str, setup: str = "") -> object:
    """Eval the dashboard factory body with stubs and optional pre-boot setup."""
    src = _unit_factory_source(DASH_JS, DASH_JS.read_text(encoding="utf-8"))
    program = (
        STUBS
        + setup + "\n"
        + "\nconst ctx = { root: __workspace, route: { page: 'dashboard', search: location.search, hash: location.hash }, $: globalThis.$, $$: globalThis.$$, shellReady: __shellReady, navigate(page, parts = {}) { this.route = { page, search: '', hash: '', ...parts }; }, updateRoute(parts) { Object.assign(this.route, parts); }, highlightBot() {}, renderTeam() {}, isActive: () => true, onRoute: () => () => {} };\n"
        + "globalThis.ThycaRouter = { register(_page, spec) { globalThis.__controller = spec.mount(ctx); } };\n"
        + "const __src = " + json.dumps(src) + ";\n"
        + "const __body = " + json.dumps(body) + ";\n"
        + "eval(__src + '\\n;' + __body);\n"
    )
    result = subprocess.run(
        [node, "--input-type=module", "-e", program],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return json.loads(result.stdout)


def test_page_is_a_single_screen_shell_document() -> None:
    """The SPA entry mounts the dashboard module, which keeps three views
    in one shared workspace (the standalone document is gone; script order
    for the shared shell is locked by the chat entry test)."""
    entry = (WEBUI / "chat" / "index.html").read_text(encoding="utf-8")
    assert 'src="../dashboard/js/dashboard.js"' in entry
    for gone in ("overview-nav", "cost-view", "#su-dung",
                 "conversation-pager", "journal-pager"):
        assert gone not in entry, gone
    src = DASH_JS.read_text(encoding="utf-8")
    for gone in ("overview-nav", "cost-view", "#su-dung",
                 "conversation-pager", "journal-pager"):
        assert gone not in src, gone
    combined = src + "\n" + entry
    for view in ("usage", "cost", "trace"):
        assert f'data-usage-view="{view}"' in combined
        assert f'id="usage-panel-{view}"' in combined


def test_page_markup_covers_every_region() -> None:
    """Dashboard template + factory carry the unified 1-chart-per-tab contract:
    1/7/30-day presets, inline metric/group toggles on Usage and Cost, dynamic
    chart legends (no collapsed analysis drawer or duplicate breakdown cards),
    and the paged Trace list."""
    js_src = DASH_JS.read_text(encoding="utf-8")
    src = js_src + "\n" + (WEBUI / "chat" / "index.html").read_text(encoding="utf-8")
    for needle in (
        'id="range-note"',
        'data-preset="1"', 'data-preset="7"', 'data-preset="30"',
        'id="usage-chart-mode"', 'value="tokens:parts"', 'value="tokens:model"',
        'value="tokens:bot"', 'value="requests:model"', 'value="requests:bot"',
        'id="cost-chart-mode"',
        'id="usage-chart-total"', 'id="cost-chart-total"', 'id="trace-head-summary"',
        'id="usage-chart-footer"', 'id="usage-chart-legend"', 'id="usage-chart-summary"',
        'id="cost-chart-footer"', 'id="cost-chart-legend"', 'id="cost-chart-summary"',
        'id="chart-day"', 'id="chart-cost"',
        'id="filter-q"', 'id="filter-model"', 'id="filter-status"', 'id="trace-refresh"',
        'id="trace-status"', 'id="trace-wrap"', 'id="trace-conversations"',
        'id="trace-empty"', 'id="trace-empty-hint"',
        'id="trace-count"', 'id="trace-more"', 'id="turn-detail-view"',
        'id="detail-back"', 'id="detail-body"',
        '/api/traces/stats?', '/api/traces?',
    ):
        assert needle in src, needle
    for removed in (
        'id="stat-grid"', 'id="range-custom-toggle"', 'id="range-custom-bar"',
        'id="range-from"', 'id="range-to"', 'id="range-apply"',
        'data-preset="all"', 'id="chart-day-note"', 'id="chart-cost-note"',
        'id="usage-scope-note"', 'class="token-summary-grid"',
        'id="analysis-details"', 'id="card-bot"', 'id="card-bot-cost"',
        'id="rows-bot"', 'id="rows-model"', 'id="rows-bot-cost"',
        'id="rows-model-cost"', 'id="chart-hour"',
        'id="usage-metric-toggle"', 'id="usage-group-toggle"', 'id="cost-group-toggle"',
        'usage-overview-scope',
    ):
        assert removed not in src, removed
    assert re.search(
        r'data-preset="7"[^>]*aria-pressed="true"', src)
    assert not re.search(
        r'data-preset="(?:1|30)"[^>]*aria-pressed="true"', src)
    assert not re.search(r"^\s*(import|export)\s", js_src, re.MULTILINE)
    assert "'s-error'" in js_src


def test_shell_navigation_lists_dashboard_without_trace_workspace() -> None:
    src = UTIL_JS.read_text(encoding="utf-8")
    assert "['dashboard', 'Sử dụng', '../dashboard/index.html', 'nav-dashboard']" in src
    assert "['trace', 'Trace', '../trace/index.html'" not in src
    assert 'is-current' in src and 'aria-current="page"' in src


def test_boot_fetches_stats_and_first_page_for_last_7_days(node: str) -> None:
    """boot() defaults to the last 7 days, fetches stats + the first
    15-row page, records rangeFrom/rangeTo and runs bootShell() for the roster."""
    stats = {"totals": {"requests": 1, "total_tokens": 5}, "by_bot": [
        {"bot_id": "coder", "requests": 1, "total_tokens": 5,
         "cost_usd": 0.0, "last_started_at": "2026-09-20T09:00:00Z"}]}
    out = _eval_dash(
        node,
        "(async () => {\n"
        "await __releaseShell();\n"
        "await new Promise(r => setTimeout(r, 0));\n"
        "setUsageGroup('bot');\n"
        "return {"
        " calls: __jsonCalls.slice(0, 2),"
        " from: rangeFrom, to: rangeTo,"
        " note: document.querySelector('#range-note').textContent,"
        " botLegend: document.querySelector('#usage-chart-legend').innerHTML };\n"
        "})().then((v) => console.log(JSON.stringify(v)))",
        setup="__jsonResponder = async (url) => url.includes('stats')"
        f" ? {json.dumps(stats)} : {{ traces: [], total: 0 }};\n",
    )
    stats_url, traces_url = out["calls"]
    assert stats_url.startswith("/api/traces/stats?")
    assert "from=" in stats_url and "to=" in stats_url
    assert "model=" not in stats_url and "status=" not in stats_url
    assert traces_url.startswith("/api/traces?")
    assert "limit=15" in traces_url and "offset=0" in traces_url
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", out["from"])
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", out["to"])
    assert out["from"] <= out["to"]
    assert out["note"].startswith("Kỳ ")
    assert 'data-bot-id="coder"' in out["botLegend"] and ">Coder</span>" in out["botLegend"]
    assert "Chưa gắn bot" not in out["botLegend"]


def test_set_preset_accepts_only_1_7_30_and_refetches_both_surfaces(node: str) -> None:
    """Only 1, 7, and 30-day presets are valid; invalid/removed presets
    such as 'all' are ignored without fetching, while a valid preset refetches
    both /api/traces/stats and /api/traces from offset=0."""
    out = _eval_dash(
        node,
        "(async () => {\n"
        "await new Promise(r => setTimeout(r, 0));\n"
        "const beforeInvalid = __jsonCalls.length;\n"
        "setPreset('all');\n"
        "const afterInvalid = __jsonCalls.length;\n"
        "setPreset(30);\n"
        "await new Promise(r => setTimeout(r, 0));\n"
        "return { beforeInvalid, afterInvalid, urls: __jsonCalls.slice(afterInvalid), from: rangeFrom, to: rangeTo, rangeAll };\n"
        "})().then((v) => console.log(JSON.stringify(v)))",
        setup="__jsonResponder = async url => url.includes('stats') ? { totals: {}, models: [], by_status: [] } : { traces: [], total: 0 };",
    )
    assert out["afterInvalid"] == out["beforeInvalid"]
    assert out["rangeAll"] is False
    assert len(out["urls"]) == 2
    stats_url, traces_url = out["urls"]
    assert stats_url == f"/api/traces/stats?from={out['from']}&to={out['to']}"
    assert traces_url.startswith(f"/api/traces?from={out['from']}&to={out['to']}")
    assert "limit=15" in traces_url and "offset=0" in traces_url


def test_trace_request_carries_filters_stats_does_not(node: str) -> None:
    """The model/status quick filters are table-only by design: they ride
    /api/traces but never /api/traces/stats."""
    out = _eval_dash(
        node,
        "(async () => {\n"
        "await new Promise(r => setTimeout(r, 0));\n"
        "document.querySelector('#filter-model').value = 'glm-5';\n"
        "document.querySelector('#filter-status').value = 'failed';\n"
        "syncListFilters();\n"
        "const before = __jsonCalls.length;\n"
        "loadStats(); loadTraces(true);\n"
        "await new Promise(r => setTimeout(r, 0));\n"
        "return { urls: __jsonCalls.slice(before) };\n"
        "})().then((v) => console.log(JSON.stringify(v)))",
    )
    stats_url, traces_url = out["urls"]
    assert stats_url.startswith("/api/traces/stats?")
    assert "model=" not in stats_url and "status=" not in stats_url
    assert "model=glm-5" in traces_url and "status=failed" in traces_url


def test_empty_success_is_cached_on_reentry(node: str) -> None:
    out = _eval_dash(
        node,
        "(async () => {\n"
        "await __releaseShell(); await __controller.ready;\n"
        "await new Promise(r => setTimeout(r, 0));\n"
        "const before = __jsonCalls.length;\n"
        "loadStats(); loadTraces(true); await new Promise(r => setTimeout(r, 0));\n"
        "return { before, after: __jsonCalls.length, statsLoaded, listLoaded, statsKey, listKey, rows: traceState.rows.length, total: traceState.total };\n"
        "})().then(v => console.log(JSON.stringify(v)))",
        setup="__jsonResponder = async url => url.startsWith('/api/traces/stats') ? { totals: {}, models: [], by_status: [] } : { traces: [], total: 0 };",
    )
    assert out["before"] == out["after"] == 2
    assert out["statsLoaded"] and out["listLoaded"]
    assert out["statsKey"] is not None and out["listKey"] is not None
    assert out["rows"] == out["total"] == 0


def test_initial_trace_failure_retry_uses_the_real_control(node: str) -> None:
    out = _eval_dash(
        node,
        "(async () => {\n"
        "await __releaseShell(); await __controller.ready; await new Promise(r => setTimeout(r, 0));\n"
        "const failed = { error: traceState.error, request: traceState.failedRequest, visible: !document.querySelector('#trace-retry').hidden };\n"
        "document.querySelector('#trace-retry').listeners.click[0](); await new Promise(r => setTimeout(r, 0));\n"
        "return { failed, calls: __jsonCalls.filter(url => url.startsWith('/api/traces?')), rows: traceState.rows.length, total: traceState.total, error: traceState.error, retryHidden: document.querySelector('#trace-retry').hidden };\n"
        "})().then(v => console.log(JSON.stringify(v)))",
        setup="let attempts = 0; __jsonResponder = async url => { if (url.startsWith('/api/traces/stats')) return { totals: {}, models: [], by_status: [] }; if (++attempts === 1) throw Error('injected initial failure'); return { traces: [], total: 0 }; };",
    )
    assert "injected initial failure" in out["failed"]["error"]
    assert out["failed"]["request"] == {"reset": True, "offset": 0}
    assert out["failed"]["visible"] is True
    assert len(out["calls"]) == 2
    assert "offset=0" in out["calls"][1] and "limit=15" in out["calls"][1]
    assert out["rows"] == out["total"] == 0 and out["error"] == ""
    assert out["retryHidden"] is True


def test_failed_more_retries_from_the_failed_offset(node: str) -> None:
    out = _eval_dash(
        node,
        "(async () => {\n"
        "await __releaseShell(); await __controller.ready; await new Promise(r => setTimeout(r, 0));\n"
        "await loadTraces(false, true); await loadTraces(false, true);\n"
        "const failed = { rows: traceState.rows.length, error: traceState.error, request: traceState.failedRequest, retryVisible: !document.querySelector('#trace-retry').hidden };\n"
        "document.querySelector('#trace-retry').listeners.click[0](); await new Promise(r => setTimeout(r, 0));\n"
        "return { failed, calls: __jsonCalls.filter(url => url.startsWith('/api/traces?')), rows: traceState.rows.length, total: traceState.total, error: traceState.error, moreHidden: document.querySelector('#trace-more').hidden };\n"
        "})().then(v => console.log(JSON.stringify(v)))",
        setup="let pages = 0; const rows = (start, count) => Array.from({length:count}, (_, i) => ({kind:'dm', bot_id:'thyca', turn_index:0, title:'Turn ' + (start+i)})); __jsonResponder = async url => { if (url.startsWith('/api/traces/stats')) return { totals: {}, models: [], by_status: [] }; const p = new URL(url, 'http://local'); const offset = Number(p.searchParams.get('offset')); pages++; if (offset === 65 && pages === 3) throw Error('injected append failure'); return { traces: offset === 0 ? rows(0,15) : offset === 15 ? rows(15,50) : rows(65,50), total: 120 }; };",
    )
    assert out["failed"] == {"rows": 65, "error": "injected append failure", "request": {"reset": False, "offset": 65}, "retryVisible": True}
    assert len(out["calls"]) == 4
    assert "offset=0" in out["calls"][0] and "limit=15" in out["calls"][0]
    assert "offset=15" in out["calls"][1] and "limit=50" in out["calls"][1]
    assert "offset=65" in out["calls"][2] and "limit=50" in out["calls"][2]
    assert "offset=65" in out["calls"][3] and "limit=50" in out["calls"][3]
    assert out["rows"] == 115 and out["total"] == 120 and out["error"] == ""
    assert out["moreHidden"] is False


def test_trace_seq_drops_a_stale_page(node: str) -> None:
    """A newer request wins: an in-flight 'Tải thêm' page resolves AFTER a
    reset and must not append — the rows are exactly the reset page."""
    out = _eval_dash(
        node,
        """(async () => {
          __jsonResponder = (url) => {
            const p = __deferred(); __pending.push({ url, p }); return p; };
          const p2 = loadTraces(false);          // in-flight page
          listFilters.q = 'new';
          const p3 = loadTraces(true);           // new filter reset wins
          const reset = __pending.pop();
          reset.p.__res({ traces: [{ kind: 'dm', bot_id: 'thyca', turn_index: 0,
            title: 'A' }], total: 9 });
          await p3;
          const stale = __pending.pop();
          stale.p.__res({ traces: [{ kind: 'dm', bot_id: 'thyca', turn_index: 0,
            title: 'B' }], total: 9 });
          await p2;
          return { titles: traceState.rows.map((r) => r.title),
                   total: traceState.total };
        })().then((v) => console.log(JSON.stringify(v)))""",
    )
    assert out["titles"] == ["A"]
    assert out["total"] == 9


def test_trace_row_escapes_and_deep_links(node: str) -> None:
    """Every untrusted field in a turn row is escaped; conversation and
    turn identity remain separate data attributes for router navigation."""
    row = {
        "kind": "dm", "bot_id": "s <x>", "title": '<img src=x onerror=alert(1)>',
        "turn_index": 0, "model": '<b>m</b>', "status": "<i>odd</i>",
        "started_at": "2026-09-20T10:00:00",
        "requests": 1, "tool_calls": 0, "total_tokens": 5,
        "cost_usd": 0.001, "latency_ms": 40,
    }
    html = _eval_dash(
        node,
        f"console.log(JSON.stringify(turnRowHtml({json.dumps(row)})))",
    )
    assert '<img src=x' not in html and '<b>m</b>' not in html
    assert '&lt;img src=x onerror=alert(1)&gt;' in html
    assert '&lt;b&gt;m&lt;/b&gt;' in html
    assert 'data-conv="dm:s &lt;x&gt;"' in html and 'data-turn="0"' in html
    assert "s &lt;x&gt;" in html
    assert "0 gọi tool" not in html and "0 tool" not in html


def test_trace_row_avoids_duplicate_dm_name_and_hides_zero_tools(node: str) -> None:
    """DM turns whose title matches the bot name omit the redundant title
    instead of printing 'march7th march7th'; zero tool calls stay silent while
    non-zero tool calls render beside request count."""
    dm_zero = {
        "kind": "dm", "bot_id": "march7th", "title": "march7th", "turn_index": 21,
        "model": "muse-ember-1.0", "status": "completed",
        "started_at": "2026-11-28T08:09:00Z",
        "requests": 1, "tool_calls": 0, "total_tokens": 37087,
        "cost_usd": 0.003815, "latency_ms": 1900,
    }
    dm_tools = {**dm_zero, "turn_index": 24, "requests": 2, "tool_calls": 1}
    out = _eval_dash(
        node,
        "globalThis.__roster.march7th = { id: 'march7th', name: 'march7th', color: '#c46', state: 'idle' };\n"
        f"console.log(JSON.stringify([turnRowHtml({json.dumps(dm_zero)}), turnRowHtml({json.dumps(dm_tools)})]))",
    )
    zero_html, tool_html = out
    assert 'class="tl-turn-title"' not in zero_html
    assert "<b>march7th</b>" in zero_html and "Chat riêng" in zero_html
    assert "1 lần gọi" in zero_html and "tool" not in zero_html
    assert "2 lần gọi · 1 tool" in tool_html


def test_status_chip_maps_the_four_statuses(node: str) -> None:
    """TurnSummary.status -> label + scoped chip class; anything else
    renders its raw text with no status color."""
    out = _eval_dash(
        node,
        "console.log(JSON.stringify([statusChip('completed'),"
        " statusChip('failed'), statusChip('context_limit'),"
        " statusChip('loop_limit'), statusChip('weird')]))",
    )
    done, fail, ctx, loop, odd = out
    assert "Hoàn tất" in done and "s-ok" in done
    assert "Thất bại" in fail and "s-error" in fail
    assert "Hết ngữ cảnh" in ctx and "s-warn" in ctx
    assert "Quá vòng lặp" in loop and "s-warn" in loop
    assert "weird" in odd and 'class="st-chip "' in odd


def test_turn_bot_ids_uses_only_explicit_turn_attribution(node: str) -> None:
    out = _eval_dash(
        node,
        "console.log(JSON.stringify(["
        " turnBotIds({ conversation_bots: ['conversation-member'], conversation_dm: 'dm-owner' }),"
        " turnBotIds({ bot_id: 'direct', conversation_bots: ['conversation-member'] }),"
        " turnBotIds({ bot_id: 'fallback', bot_breakdown: [{ bot_id: 'attributed' }] })]))",
    )
    assert out == [[], ["direct"], ["attributed"]]


def test_render_trace_table_states(node: str) -> None:
    """Three states stay distinct: rows -> table + footer; error -> the
    scoped error line, table hidden, no empty note; empty -> the note with
    a hint that changes when a filter is on."""
    seen = _eval_dash(
        node,
        """(() => {
          const seen = {};
          const snap = (k) => { seen[k] = {
            wrap: !document.querySelector('#trace-wrap').hidden,
            empty: !document.querySelector('#trace-empty').hidden,
            err: !document.querySelector('#trace-status').hidden,
            errText: document.querySelector('#trace-status-text').textContent,
            hint: document.querySelector('#trace-empty-hint').textContent }; };
          traceState.rows = [{ kind: 'dm', bot_id: 'thyca', turn_index: 0 }];
          traceState.error = ''; traceState.loading = false;
          renderTraceTable(); snap('rows');
          traceState.rows = []; traceState.error = 'boom';
          renderTraceTable(); snap('err');
          traceState.error = ''; renderTraceTable(); snap('empty');
          document.querySelector('#filter-model').value = 'glm';
          listFilters.model = 'glm';
          renderTraceTable(); snap('filtered');
          console.log(JSON.stringify(seen));
        })()""",
    )
    assert seen["rows"]["wrap"] is True
    assert seen["rows"]["empty"] is False and seen["rows"]["err"] is False
    assert seen["err"]["err"] is True
    assert "Không tải được danh sách lượt" in seen["err"]["errText"]
    assert seen["err"]["wrap"] is False and seen["err"]["empty"] is False
    assert seen["empty"]["empty"] is True and seen["empty"]["wrap"] is False
    assert "Đổi khoảng ngày" in seen["empty"]["hint"]
    assert "Bộ lọc lượt đang bật" in seen["filtered"]["hint"]


def test_open_trace_row_navigates_to_dashboard_detail(node: str) -> None:
    out = _eval_dash(
        node,
        "openTraceRow({ target: { closest: (s) =>"
        " s === 'button[data-conv][data-turn]'"
        "   ? { dataset: { conv: 'dm:thyca', turn: '0' } } : null } });\n"
        "console.log(JSON.stringify(ctx.route))",
    )
    assert out["page"] == "dashboard" and out["hash"] == "#dm/thyca/0"


def test_fill_filters_keep_selection_and_add_extra_statuses(node: str) -> None:
    """Refills keep the user's selection when still present; the status
    select always offers the four known statuses plus any extra the stats
    payload reports."""
    out = _eval_dash(
        node,
        "const sel = document.querySelector('#filter-model');\n"
        "listFilters.model = 'm2'; sel.value = 'm2';\n"
        "fillModelFilter(['m1', 'm2']);\n"
        "const mVals = sel.options.map(o => o.value);\n"
        "const mCur = sel.value;\n"
        "const ssel = document.querySelector('#filter-status');\n"
        "fillStatusFilter([{ status: 'paused' }, { status: 'failed' }]);\n"
        "const sVals = ssel.options.map(o => o.value);\n"
        "fillStatusFilter([]);\n"
        "console.log(JSON.stringify({ mVals, mCur, sVals,"
        " sValsAfter: ssel.options.map(o => o.value) }))",
    )
    assert out["mVals"] == ["", "m1", "m2"]
    assert out["mCur"] == "m2"
    assert out["sVals"] == [
        "", "completed", "failed", "context_limit", "loop_limit", "paused"]
    assert out["sValsAfter"] == [
        "", "completed", "failed", "context_limit", "loop_limit"]


def test_formatters_vi_vn_and_edges(node: str) -> None:
    """vi-VN grouping/decimals everywhere; bad input renders '—' never a
    crash; relative time buckets degrade to an absolute date past 31 days."""
    out = _eval_dash(
        node,
        "const now = Date.now();\n"
        "console.log(JSON.stringify({"
        " ints: [fmtInt(1200000), fmtInt(70607522), fmtInt(0), fmtInt(null), fmtInt('x')],"
        " cost: [fmtCost(0.012345), fmtCost(0.001234), fmtCost(0), fmtCost(0.5),"
        "  fmtCost(12.345), fmtCost(2), fmtCost(0.000001), fmtCost(null),"
        "  fmtCost(undefined), fmtCost('nope'), fmtCost(NaN)],"
        " share: [share(1, 4), share(3, 40), share(0, 0)],"
        " durs: [fmtDur(999), fmtDur(1000), fmtDur(1500), fmtDur(90000),"
        "  fmtDur(-1), fmtDur('x')],"
        " dt: [fmtDateTime('nope'), fmtDay('2026-09-08'), fmtDay('')],"
        " rel: [relTime(new Date(now - 20e3).toISOString()),"
        "  relTime(new Date(now - 5 * 60e3).toISOString()),"
        "  relTime(new Date(now - 3 * 3600e3).toISOString()),"
        "  relTime(new Date(now - 2 * 86400e3).toISOString()),"
        "  relTime(new Date(now - 40 * 86400e3).toISOString()),"
        "  relTime('nope')] }))",
    )
    assert out["ints"] == ["1.200.000", "70.607.522", "0", "—", "—"]
    assert out["cost"] == ["$0.0123", "$0.001234", "$0.000000", "$0.5000",
                            "$12.35", "$2.00", "$0.000001",
                            "—", "—", "—", "—"]
    assert out["share"] == ["25%", "7,5%", "0%"]
    assert out["durs"] == ["999 ms", "1 giây", "1,5 giây",
                           "1 ph 30 giây", "-1 ms", "—"]
    assert out["dt"][0] == "—"
    assert out["dt"][1] == "08/09" and out["dt"][2] == "—"
    assert out["rel"][0] == "vừa xong"
    assert out["rel"][1] == "5 phút trước"
    assert out["rel"][2] == "3 giờ trước"
    assert out["rel"][3] == "2 ngày trước"
    assert "thg" in out["rel"][4]
    assert out["rel"][5] == "—"


def test_render_stats_paints_every_region(node: str) -> None:
    """One stats payload paints daily charts on multi-day ranges, switches
    automatically to 24-hour stacked charts on a 1-day range, populates model
    and bot legends, and fills both Trace filter selects."""
    stats = {
        "totals": {"requests": 2, "turns": 1, "tool_calls": 1, "prompt_tokens": 10,
                   "cached_tokens": 2, "completion_tokens": 4,
                   "total_tokens": 14, "cost_usd": 0.5,
                   "latency_ms_p50": 500, "latency_ms_p90": 900},
        "by_model": [{"model": "m", "requests": 2, "total_tokens": 14,
                      "cost_usd": 0.5, "latency_ms_p50": 500,
                      "last_started_at": "2026-09-20T09:00:00Z"}],
        "by_bot": [{"bot_id": "coder", "requests": 2, "tool_calls": 1,
                    "total_tokens": 14, "cost_usd": 0.5,
                    "last_started_at": "2026-09-20T09:00:00Z"}],
        "by_day": [{"day": "2026-09-20", "requests": 2, "prompt_tokens": 10,
                    "cached_tokens": 2, "completion_tokens": 4, "total_tokens": 14,
                    "cost_usd": 0.5,
                    "by_model": [{"model": "m", "requests": 2, "total_tokens": 14, "cost_usd": 0.5}],
                    "by_bot": [{"bot_id": "coder", "requests": 2, "total_tokens": 14, "cost_usd": 0.5}]}],
        "by_hour": [{"hour": "09", "requests": 2, "prompt_tokens": 10,
                     "cached_tokens": 2, "completion_tokens": 4, "total_tokens": 14,
                     "cost_usd": 0.5,
                     "by_model": [{"model": "m", "requests": 2, "total_tokens": 14, "cost_usd": 0.5}],
                     "by_bot": [{"bot_id": "coder", "requests": 2, "total_tokens": 14, "cost_usd": 0.5}]}],
        "by_status": [{"status": "completed", "requests": 2}],
        "models": ["m"],
    }
    out = _eval_dash(
        node,
        f"rangeFrom = '2026-09-14'; rangeTo = '2026-09-20'; statsData = {json.dumps(stats)};\n"
        "renderStats(statsData);\n"
        "const dayHtml = document.querySelector('#chart-day').innerHTML;\n"
        "const costLegend = document.querySelector('#cost-chart-legend').innerHTML;\n"
        "setUsageGroup('bot'); const botLegend = document.querySelector('#usage-chart-legend').innerHTML;\n"
        "rangeFrom = '2026-09-20'; rangeTo = '2026-09-20'; renderStats(statsData);\n"
        "console.log(JSON.stringify({"
        " usageTotal: document.querySelector('#usage-chart-total').textContent,"
        " costTotal: document.querySelector('#cost-chart-total').textContent,"
        " traceSummary: document.querySelector('#trace-head-summary').textContent,"
        " dayHtml, costLegend, botLegend,"
        " hourTitle: document.querySelector('#usage-chart-title').textContent,"
        " hourCostTitle: document.querySelector('#cost-chart-title').textContent,"
        " hourHtml: document.querySelector('#chart-day').innerHTML,"
        " mOpts: document.querySelector('#filter-model').options.length,"
        " sOpts: document.querySelector('#filter-status').options.length }))",
    )
    assert out["usageTotal"] == "14"
    assert out["costTotal"] == "$0.5000"
    assert "500 ms" in out["traceSummary"]
    assert 'class="day-chart"' in out["dayHtml"]
    assert out["hourTitle"] == "Token theo giờ"
    assert out["hourCostTitle"] == "Chi phí theo giờ"
    assert 'class="day-chart is-hourly"' in out["hourHtml"]
    assert out["hourHtml"].count('class="dc-col') == 24
    assert "09:00–09:59" in out["hourHtml"]
    assert "Coder" in out["botLegend"] or "coder" in out["botLegend"]
    assert ">m</span>" in out["costLegend"]
    assert out["mOpts"] == 2
    assert out["sOpts"] == 5


def test_usage_input_total_excludes_cache_read(node: str) -> None:
    """Usage Token + Loại shows prompt_tokens minus cached_tokens in the legend;
    switching to Lần gọi defaults to stacked-by-Model with a model legend."""
    src = DASH_JS.read_text(encoding="utf-8") + "\n" + (WEBUI / "chat" / "index.html").read_text(encoding="utf-8")
    assert "Không bao gồm Cache read." in src
    assert "Đã bao gồm Cache read." not in src
    assert "Tính riêng, không nằm trong Input." in src
    assert "Nằm trong tổng Input." not in src
    stats = {
        "totals": {"requests": 2, "turns": 1, "tool_calls": 1, "prompt_tokens": 1000,
                   "cached_tokens": 100, "completion_tokens": 200,
                   "total_tokens": 1200, "cost_usd": 0.5,
                   "latency_ms_p50": 10, "latency_ms_p90": 20},
        "by_model": [{"model": "model-a", "requests": 2, "total_tokens": 1200, "cost_usd": 0.5}],
        "by_bot": [], "by_day": [], "by_hour": [],
        "by_status": [], "models": ["model-a"],
    }
    out = _eval_dash(
        node,
        f"rangeFrom = '2026-09-14'; rangeTo = '2026-09-20'; statsData = {json.dumps(stats)};\n"
        "renderStats(statsData);\n"
        "const tokState = {"
        " total: document.querySelector('#usage-chart-total').textContent,"
        " legend: document.querySelector('#usage-chart-legend').innerHTML,"
        " group: effectiveUsageGroup() };\n"
        "setDayMetric('requests');\n"
        "const reqState = {"
        " title: document.querySelector('#usage-chart-title').textContent,"
        " total: document.querySelector('#usage-chart-total').textContent,"
        " sub: document.querySelector('#usage-chart-sub').textContent,"
        " legend: document.querySelector('#usage-chart-legend').innerHTML,"
        " group: effectiveUsageGroup() };\n"
        "console.log(JSON.stringify({ tokState, reqState }))",
    )
    assert out["tokState"]["total"] == "1.200"
    assert out["tokState"]["group"] == "parts"
    assert 'id="usage-input-total">900</strong>' in out["tokState"]["legend"]
    assert 'id="usage-cache-total">100</strong>' in out["tokState"]["legend"]
    assert 'id="usage-output-total">200</strong>' in out["tokState"]["legend"]
    assert out["reqState"]["title"] == "Lần gọi theo ngày"
    assert out["reqState"]["total"] == "2"
    assert out["reqState"]["sub"] == "1 lượt"
    assert out["reqState"]["group"] == "model"
    assert "model-a" in out["reqState"]["legend"] and "<strong>2</strong>" in out["reqState"]["legend"]
