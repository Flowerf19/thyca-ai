"""Node tests for the dashboard cost surface — ui/dashboard/js/dashboard.js.

The old Chi phí view (cost-data.js / cost-view.js / cost-rows.js /
cost-bots.js and the shared journal pager) was not ported: pricing
estimates, per-model share denominators, the per-conversation cost journal and
the average-per-turn card are gone. The new Dashboard keeps cost on a
recorded basis: a dedicated Chi phí view with daily cost and bot/model
breakdowns, plus shared token/scope cards and the flat Trace turn list —
all fed by GET /api/traces/stats + GET /api/traces. These tests pin that
replacement contract. The page is a classic script ('use strict', shared
globals), so the harness evals it in Node with a stubbed global surface,
mirroring the old import-based eval style.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from test_team_frontend import _unit_factory_source

ROOT = Path(__file__).resolve().parents[1]
DASH_JS = ROOT / "thyca" / "webui" / "ui" / "dashboard" / "js" / "dashboard.js"

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
globalThis.toast = () => {};
globalThis.bootShell = async () => {};
let __releaseShell;
const __shellReady = new Promise(resolve => { __releaseShell = async () => { await bootShell(); resolve(); }; });
const __jsonCalls = [];
let __jsonResponder = async (url) => { throw new Error('unexpected ' + url); };
globalThis.getJson = (url) => { __jsonCalls.push(String(url)); return __jsonResponder(String(url)); };
"""


@pytest.fixture(scope="module")
def node() -> str:
    binary = shutil.which("node")
    if not binary:
        pytest.skip("node not installed")
    return binary


def _eval_dash(node: str, body: str) -> object:
    """Eval the dashboard factory body with stubs, then run `body` in scope."""
    src = _unit_factory_source(DASH_JS, DASH_JS.read_text(encoding="utf-8"))
    program = (
        STUBS
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


def test_share_denominator_and_vi_format(node: str) -> None:
    """share(): percent of the period total with vi-VN decimals — one
    decimal under 10%, integer above; an empty denominator reads 0%, never
    NaN or Infinity."""
    result = _eval_dash(
        node,
        "console.log(JSON.stringify([share(0.75, 1), share(0.25, 1),"
        " share(0.056, 1), share(0, 0), share(null, 10),"
        " share(0.9999, 1)]))",
    )
    big, small, frac, empty_den, null_num, almost = result
    assert big == "75%" and small == "25%"
    assert frac == "5,6%"
    assert empty_den == "0%"
    assert null_num == "—"
    assert almost == "100%"


def test_tab_headers_carry_own_token_cost_and_trace_totals(node: str) -> None:
    """Each of the 3 tabs owns its own summary without a shared stat-grid:
    Usage owns total tokens + Input/Cache/Output, Cost owns total cost (hiding
    coverage when 100% priced), and Trace owns turns + latency summary."""
    totals = {
        "requests": 5, "tool_calls": 7, "turns": 4, "priced_turns": 4,
        "prompt_tokens": 150, "cached_tokens": 30, "completion_tokens": 15,
        "total_tokens": 165, "cost_usd": 0.03,
        "latency_ms_p50": 1200, "latency_ms_p90": 3400,
    }
    out = _eval_dash(
        node,
        f"renderStats({{ totals: {json.dumps(totals)} }});\n"
        "console.log(JSON.stringify({"
        " usageTotal: document.querySelector('#usage-chart-total').textContent,"
        " legend: document.querySelector('#usage-chart-legend').innerHTML,"
        " costTotal: document.querySelector('#cost-chart-total').textContent,"
        " costCoverage: document.querySelector('#cost-coverage').textContent,"
        " costCoverageHidden: document.querySelector('#cost-coverage').hidden,"
        " traceSummary: document.querySelector('#trace-head-summary').textContent,"
        " traceTitle: document.querySelector('#trace-head-summary').title || '' }));",
    )
    assert out["usageTotal"] == "165"
    assert 'id="usage-input-total">120</strong>' in out["legend"]
    assert 'id="usage-cache-total">30</strong>' in out["legend"]
    assert 'id="usage-output-total">15</strong>' in out["legend"]
    assert out["costTotal"] == "$0.0300"
    assert out["costCoverageHidden"] is True and out["costCoverage"] == ""
    assert out["traceSummary"] == "4 lượt · ~1,2 giây"
    assert "3,4 giây" in out["traceTitle"]


def test_cost_card_without_any_priced_turn(node: str) -> None:
    """cost_usd == null means no turn recorded a price: the Cost tab header
    shows an em dash + explicit 'Chưa có giá', never $0."""
    out = _eval_dash(
        node,
        "renderStats({ totals: { requests: 3, cost_usd: null,"
        " latency_ms_p50: null, latency_ms_p90: null } });\n"
        "console.log(JSON.stringify({"
        " total: document.querySelector('#cost-chart-total').textContent,"
        " coverage: document.querySelector('#cost-coverage').textContent,"
        " hidden: document.querySelector('#cost-coverage').hidden }));",
    )
    assert out["total"] == "—"
    assert out["coverage"] == "Chưa có giá" and out["hidden"] is False
    assert "$0" not in out["total"]


def test_multi_model_turn_tokens_split_not_double_counted(node: str) -> None:
    """A turn whose prompt side is all cache: Input drops to uncached
    remainder (0 here), Cache stays 200, Output 5, total 205."""
    out = _eval_dash(
        node,
        "renderStats({ totals: { requests: 1, prompt_tokens: 200,"
        " cached_tokens: 200, completion_tokens: 5, total_tokens: 205,"
        " cost_usd: null, latency_ms_p50: null, latency_ms_p90: null } });\n"
        "console.log(JSON.stringify({"
        " total: document.querySelector('#usage-chart-total').textContent,"
        " legend: document.querySelector('#usage-chart-legend').innerHTML }));",
    )
    assert out["total"] == "205"
    assert 'id="usage-input-total">0</strong>' in out["legend"]
    assert 'id="usage-cache-total">200</strong>' in out["legend"]
    assert 'id="usage-output-total">5</strong>' in out["legend"]


def test_daily_costs_zero_fill_missing_days(node: str) -> None:
    """dayColumns: the range drives the columns — every day in [from, to]
    gets a bucket (inactive missing days fill with 0 requests / $0 cost,
    keeping active unpriced days as null), payload days outside the range
    are dropped, and a broken 'day' key never lands."""
    by_day = [
        {"day": "2026-09-20", "requests": 4, "cost_usd": 0.5},
        {"day": "2026-09-22", "requests": 1, "cost_usd": None},
        {"day": "nope", "requests": 99, "cost_usd": 99},
        {"day": "2026-10-01", "requests": 7, "cost_usd": 0.7},
    ]
    days = _eval_dash(
        node,
        f"rangeFrom = '2026-09-20'; rangeTo = '2026-09-22';\n"
        f"console.log(JSON.stringify(dayColumns({json.dumps(by_day)})))",
    )
    assert days == [
        {"day": "2026-09-20", "requests": 4, "cost_usd": 0.5},
        {"day": "2026-09-21", "requests": 0, "prompt_tokens": 0, "cached_tokens": 0, "completion_tokens": 0, "cost_usd": 0},
        {"day": "2026-09-22", "requests": 1, "cost_usd": None},
    ]


def test_day_columns_fall_back_to_sparse_buckets_on_wide_ranges(node: str) -> None:
    """Past the column cap (or with an unset/inverted range) the sparse
    by_day buckets render as-is instead of fabricating hundreds of empty
    days."""
    by_day = [{"day": "2026-01-01", "requests": 1, "cost_usd": 0.1}]
    same = _eval_dash(
        node,
        f"rangeFrom = '2026-01-01'; rangeTo = '2026-06-01';\n"
        f"console.log(JSON.stringify(dayColumns({json.dumps(by_day)})))",
    )
    assert same == by_day
    inverted = _eval_dash(
        node,
        f"rangeFrom = '2026-09-22'; rangeTo = '2026-09-20';\n"
        f"console.log(JSON.stringify(dayColumns({json.dumps(by_day)})))",
    )
    assert inverted == by_day
    unset = _eval_dash(
        node,
        f"rangeFrom = ''; rangeTo = '';\n"
        f"console.log(JSON.stringify(dayColumns({json.dumps(by_day)})))",
    )
    assert unset == by_day


def test_daily_usage_and_cost_charts_keep_distinct_metrics(node: str) -> None:
    """Usage switches between token composition and model calls; recorded
    cost is a separate chart where null remains unavailable and zero is real."""
    stats = {"by_day": [
        {"day": "2026-09-20", "requests": 1, "prompt_tokens": 10, "cached_tokens": 2, "completion_tokens": 3, "cost_usd": 0.9},
        {"day": "2026-09-21", "requests": 9, "prompt_tokens": 9, "cached_tokens": 2, "completion_tokens": 1, "cost_usd": 0.1},
        {"day": "2026-09-22", "requests": 0, "prompt_tokens": 0, "cached_tokens": 0, "completion_tokens": 0, "cost_usd": 0},
        {"day": "2026-09-23", "requests": 0, "prompt_tokens": 0, "cached_tokens": 0, "completion_tokens": 0, "cost_usd": None},
    ]}
    out = _eval_dash(
        node,
        f"statsData = {json.dumps(stats)}; rangeFrom = ''; rangeTo = '';\n"
        "dayMetric = 'requests'; renderDayChart(); const req = document.querySelector('#chart-day').innerHTML;\n"
        "dayMetric = 'tokens'; renderDayChart(); const tokens = document.querySelector('#chart-day').innerHTML;\n"
        "renderCostChart(); const cost = document.querySelector('#chart-cost').innerHTML;\n"
        "console.log(JSON.stringify({ req, tokens, cost }))",
    )
    req_heights = [float(p.split('height:')[1].split('%')[0]) for p in out["req"].split('dc-bar-requests')[1:]]
    assert req_heights[:2] == pytest.approx([1 / 9 * 100, 100])
    assert 'dc-seg-input' in out['tokens'] and 'dc-seg-cache' in out['tokens'] and 'dc-seg-output' in out['tokens']
    cost_heights = [float(p.split('height:')[1].split('%')[0]) for p in out["cost"].split('dc-bar-cost')[1:]]
    assert cost_heights[:2] == pytest.approx([100, 1 / 9 * 100])
    assert 'dc-bar-cost is-zero' in out['cost'] and 'dc-unavailable' in out['cost']
    assert 'aria-label="Chi phí theo ngày"' in out['cost']


def test_cost_chart_distinguishes_inactive_unpriced_and_partial_days(node: str) -> None:
    """Cost chart separates 3 distinct states cleanly: inactive missing days
    render a zero baseline ('không có hoạt động', no '—'), active unpriced days
    render '—' ('chưa có giá'), and partially priced days note X/Y coverage."""
    stats = {
        "totals": {"requests": 7, "turns": 5, "priced_turns": 3, "cost_usd": 0.42},
        "by_day": [
            {"day": "2026-09-20", "requests": 5, "turns": 4, "priced_turns": 2, "cost_usd": 0.42},
            {"day": "2026-09-22", "requests": 2, "turns": 1, "priced_turns": 0, "cost_usd": None},
        ],
    }
    html = _eval_dash(
        node,
        f"rangeFrom = '2026-09-20'; rangeTo = '2026-09-22'; statsData = {json.dumps(stats)};\n"
        "renderCostChart();\n"
        "console.log(JSON.stringify(document.querySelector('#chart-cost').innerHTML))",
    )
    assert "(2/4 lượt có giá)" in html
    assert "không có hoạt động" in html
    assert "2 lần gọi · chưa có giá" in html
    assert html.count("dc-unavailable") == 1
    assert html.count("dc-bar-cost is-zero") == 1


def test_paging_multiple_pages_and_midway_failure(node: str) -> None:
    """"Tải thêm" is server paging: page 1 is 15 rows, the next request asks
    limit=50&offset=<rows so far>, rows accumulate, and a midway failure is
    an explicit error state — never a silent partial list."""
    out = _eval_dash(
        node,
        """(async () => {
          const row = (i) => ({ kind: 'dm', bot_id: 'thyca', turn_index: 0 });
          const pages = {
            0: Array.from({ length: 15 }, (_, i) => row(i)),
            15: Array.from({ length: 50 }, (_, i) => row(15 + i)),
          };
          __jsonResponder = async (url) => {
            const q = new URLSearchParams(url.split('?')[1]);
            const off = Number(q.get('offset'));
            if (off >= 65) throw new Error('backend died');
            return { traces: pages[off] || [], total: 100 };
          };
          await loadTraces(true);
          await loadTraces(false);
          const ok = { rows: traceState.rows.length, total: traceState.total,
            more: document.querySelector('#trace-more').hidden,
            urls: __jsonCalls.slice(-2) };
          await loadTraces(false);
          const afterError = { error: traceState.error,
            rows: traceState.rows.length,
            status: document.querySelector('#trace-status-text').textContent,
            statusHidden: document.querySelector('#trace-status').hidden };
          return { ok, afterError };
        })().then((v) => console.log(JSON.stringify(v)))""",
    )
    assert out["ok"]["rows"] == 65
    assert out["ok"]["total"] == 100
    assert out["ok"]["more"] is False
    assert out["ok"]["urls"][0].endswith("limit=15&offset=0")
    assert out["ok"]["urls"][1].endswith("limit=50&offset=15")
    assert out["afterError"]["error"] == "backend died"
    assert out["afterError"]["rows"] == 65
    assert "Không tải được danh sách lượt" in out["afterError"]["status"]
    assert out["afterError"]["statusHidden"] is False


def test_load_more_hidden_at_total_and_rows_render(node: str) -> None:
    """"Hiển thị X trong Y lượt" tracks the loaded window; the button hides
    once rows reach total and turn rows expose actionable turns."""
    out = _eval_dash(
        node,
        """(async () => {
          const row = (i) => ({ kind: 'dm', bot_id: 'thyca', title: 'T', turn_index: i,
            started_at: '2026-09-20T10:00:00', model: 'm', status: 'completed',
            requests: 1, tool_calls: 0, total_tokens: 5, cost_usd: 0.001,
            latency_ms: 40 });
          __jsonResponder = async (url) => {
            const q = new URLSearchParams(url.split('?')[1]);
            const off = Number(q.get('offset'));
            const lim = Number(q.get('limit'));
            return { traces: Array.from(
              { length: Math.min(lim, 20 - off) }, (_, i) => row(off + i)),
              total: 20 };
          };
          await loadTraces(true);
          const first = {
            count: document.querySelector('#trace-count').textContent,
            more: document.querySelector('#trace-more').hidden,
            html: document.querySelector('#trace-conversations').innerHTML };
          await loadTraces(false);
          return { first,
            second: { count: document.querySelector('#trace-count').textContent,
              more: document.querySelector('#trace-more').hidden } };
        })().then((v) => console.log(JSON.stringify(v)))""",
    )
    assert out["first"]["count"] == "Hiển thị 15 trong 20 lượt"
    assert out["first"]["more"] is False
    assert 'data-conv="dm:thyca"' in out["first"]["html"]
    assert 'data-turn="0"' in out["first"]["html"]
    assert out["second"]["count"] == "Hiển thị 20 trong 20 lượt"
    assert out["second"]["more"] is True


def test_cost_bot_legend_scopes_roster_bots_and_labels_unknown(node: str) -> None:
    """Cost and Usage bot legends paint roster bots as clickable scope buttons
    with their signature color, while unattributed ('unknown') stays plain text."""
    by_bot = [
        {"bot_id": "coder", "requests": 3, "tool_calls": 1,
         "total_tokens": 30, "cost_usd": 0.01,
         "last_started_at": "2026-09-20T09:00:00Z"},
        {"bot_id": "unknown", "requests": 1, "tool_calls": 0,
         "total_tokens": 10, "cost_usd": None,
         "last_started_at": "2026-09-18T09:00:00Z"},
    ]
    out = _eval_dash(
        node,
        "globalThis.__roster = { coder: { id: 'coder', name: 'Coder',"
        " color: '#123', state: 'idle' } };\n"
        f"statsData = {{ by_bot: {json.dumps(by_bot)}, totals: {{ requests: 4, total_tokens: 40, cost_usd: 0.01 }} }};\n"
        "setUsageGroup('bot'); setCostGroup('bot');\n"
        "console.log(JSON.stringify({ usage: document.querySelector('#usage-chart-legend').innerHTML,"
        " cost: document.querySelector('#cost-chart-legend').innerHTML }));",
    )
    for html in (out["usage"], out["cost"]):
        assert 'class="legend-item legend-bot-btn" data-action="select-bot" data-bot-id="coder"' in html
        assert "background:#123" in html
        assert ">Coder</span>" in html
        assert "Chưa gắn bot" in html
        assert 'data-bot-id="unknown"' not in html
        assert "background:#a08f68" in html
    assert "$0.0100" in out["cost"] and "<strong>—</strong>" in out["cost"]
