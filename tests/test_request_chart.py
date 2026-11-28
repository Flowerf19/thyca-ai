"""Node tests for the dashboard "Theo model" / "Theo bot" rows and the
stale-response guards — thyca/webui/ui/dashboard/js/dashboard.js.

The old Request view (request.js: one SVG bar chart, model search, sort
buttons, full-data share denominator) was not ported: the new Dashboard
renders by_model/by_bot straight from GET /api/traces/stats as simple rows
(sorted server data, escaped names, roster avatars) and guards every load
with a latest-request-wins sequence. These tests cover that replacement
contract. The new files are classic scripts ('use strict', shared globals),
so the harness evals dashboard.js in Node with a stubbed global surface —
the successor of the import-based eval style.
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


@pytest.fixture(scope="module")
def node() -> str:
    binary = shutil.which("node")
    if not binary:
        pytest.skip("node not installed")
    return binary


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
// Roster stub: tests register bots here; avatar() marks roster bots.
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


def _eval_dash(node: str, body: str) -> object:
    """Eval dashboard.js (classic script) with stubs, then run `body` inside
    the same scope so module-level functions/state are reachable."""
    src = _unit_factory_source(DASH_JS, DASH_JS.read_text(encoding="utf-8"))
    program = (
        STUBS
        + "\nconst ctx = { root: __workspace, route: { page: 'dashboard', search: location.search, hash: location.hash }, $: globalThis.$, $$: globalThis.$$, shellReady: __shellReady, navigate(page, parts) { globalThis.__routes ||= []; globalThis.__routes.push({ page, ...parts }); }, updateRoute(parts) { Object.assign(this.route, parts); globalThis.__routes ||= []; globalThis.__routes.push({ ...this.route }); }, highlightBot() {}, renderTeam() {}, isActive: () => true, onRoute: () => () => {} };\n"
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


def test_model_series_sorted_stacked_and_folded_past_top_4(node: str) -> None:
    """Model grouping ranks named models by the active metric, stacks bars
    by model color, and folds models beyond the top 4 into 'Khác'."""
    models = [
        {"model": "m-small", "requests": 10, "total_tokens": 100, "cost_usd": 0.01, "latency_ms_p50": 300},
        {"model": "gpt-5.6-luna", "requests": 50, "total_tokens": 5000, "cost_usd": 0.05, "latency_ms_p50": 1200},
        {"model": "muse-spark", "requests": 80, "total_tokens": 300, "cost_usd": 0.02, "latency_ms_p50": 900},
        {"model": "m-three", "requests": 20, "total_tokens": 400, "cost_usd": 0.03},
        {"model": "m-four", "requests": 15, "total_tokens": 200, "cost_usd": 0.01},
        {"model": "m-five", "requests": 5, "total_tokens": 50, "cost_usd": 0.005},
    ]
    by_day = [{
        "day": "2026-09-20", "requests": 180, "total_tokens": 6050, "cost_usd": 0.125,
        "by_model": [{"model": m["model"], "requests": m["requests"], "total_tokens": m["total_tokens"], "cost_usd": m["cost_usd"]} for m in models],
    }]
    out = _eval_dash(
        node,
        f"rangeFrom = '2026-09-14'; rangeTo = '2026-09-20';\n"
        f"statsData = {{ totals: {{ total_tokens: 6050, requests: 180, cost_usd: 0.125 }}, by_model: {json.dumps(models)}, by_day: {json.dumps(by_day)} }};\n"
        "setUsageGroup('model');\n"
        "const tokLegend = document.querySelector('#usage-chart-legend').innerHTML;\n"
        "const tokChart = document.querySelector('#chart-day').innerHTML;\n"
        "setDayMetric('requests');\n"
        "const reqLegend = document.querySelector('#usage-chart-legend').innerHTML;\n"
        "const reqChart = document.querySelector('#chart-day').innerHTML;\n"
        "console.log(JSON.stringify({ tokLegend, tokChart, reqLegend, reqChart }));",
    )
    tok_labels = [
        chunk.split("</span>")[0]
        for chunk in out["tokLegend"].split('<span class="legend-label">')[1:]
    ]
    assert tok_labels == ["gpt-5.6-luna", "m-three", "muse-spark", "m-four", "Khác"]
    assert "5.000" in out["tokLegend"]
    assert "~1,2 giây" in out["tokLegend"]
    assert out["tokChart"].count("dc-seg-series") == 5
    req_labels = [
        chunk.split("</span>")[0]
        for chunk in out["reqLegend"].split('<span class="legend-label">')[1:]
    ]
    assert req_labels == ["muse-spark", "gpt-5.6-luna", "m-three", "m-four", "Khác"]
    assert out["reqChart"].count("dc-seg-series") == 5


def test_empty_zero_and_missing_models_are_honest(node: str) -> None:
    """An empty period shows the empty chart note; zero recorded cost renders
    honestly as $0.000000 in the Cost legend."""
    out_empty = _eval_dash(
        node,
        "statsData = { by_model: [], by_day: [] };\n"
        "setUsageGroup('model');\n"
        "console.log(JSON.stringify(document.querySelector('#chart-day').innerHTML));",
    )
    assert "Chưa có lượt nào trong kỳ." in out_empty
    out = _eval_dash(
        node,
        'statsData = { totals: { total_tokens: 70, requests: 7, cost_usd: 0 },'
        ' by_model: [{model: "solo-model", requests: 7, total_tokens: 70, cost_usd: 0, latency_ms_p50: 40}],'
        ' by_day: [{day: "2026-09-20", requests: 7, total_tokens: 70, cost_usd: 0}] };\n'
        "setUsageGroup('model'); renderCostChart();\n"
        "console.log(JSON.stringify({ usage: document.querySelector('#usage-chart-legend').innerHTML,"
        " cost: document.querySelector('#cost-chart-legend').innerHTML }));",
    )
    assert "solo-model" in out["usage"] and "~40 ms" in out["usage"]
    assert "$0.000000" in out["cost"]


def test_long_and_hostile_names_stay_text(node: str) -> None:
    """A hostile model id is escaped everywhere it renders — legend label,
    title tooltip and bar tooltip — never parsed into markup."""
    out = _eval_dash(
        node,
        "rangeFrom = '2026-09-14'; rangeTo = '2026-09-20';\n"
        'statsData = { totals: { total_tokens: 30, requests: 3 },'
        ' by_model: [{model: "<img src=x onerror=alert(1)>", requests: 3, total_tokens: 30}],'
        ' by_day: [{day: "2026-09-20", requests: 3, total_tokens: 30,'
        '   by_model: [{model: "<img src=x onerror=alert(1)>", requests: 3, total_tokens: 30}]}] };\n'
        "setUsageGroup('model');\n"
        "console.log(JSON.stringify({ legend: document.querySelector('#usage-chart-legend').innerHTML,"
        " chart: document.querySelector('#chart-day').innerHTML }));",
    )
    for html in (out["legend"], out["chart"]):
        assert "<img src=x onerror=alert(1)>" not in html
        assert "&lt;img src=x onerror=alert(1)&gt;" in html


def test_period_change_never_keeps_a_stale_response(node: str) -> None:
    """Range apply issues a NEW stats request; a late response from the
    previous range is dropped by the statsSeq guard and never repaints."""
    outcome = _eval_dash(
        node,
        """(async () => {
          const pending = [];
          __jsonResponder = (url) => new Promise((resolve) => {
            pending.push({ url, resolve });
          });
          setUsageGroup('model');
          rangeFrom = '2026-09-01'; rangeTo = '2026-09-30';
          const first = loadStats();
          rangeFrom = '2026-09-20'; rangeTo = '2026-09-26';
          const second = loadStats();
          pending[1].resolve({ totals: { requests: 5, total_tokens: 50 }, by_model: [
            { model: 'fresh-7d', requests: 5, total_tokens: 50 }] });
          await second;
          const afterFast = document.querySelector('#usage-chart-legend').innerHTML;
          pending[0].resolve({ totals: { requests: 900, total_tokens: 9000 }, by_model: [
            { model: 'stale-30d', requests: 900, total_tokens: 9000 }] });
          await new Promise((r) => setTimeout(r, 5));
          return { afterFast,
            final: document.querySelector('#usage-chart-legend').innerHTML,
            stats: statsData.totals.requests,
            statUrls: __jsonCalls.filter((u) => u.includes('stats')) };
        })().then((v) => console.log(JSON.stringify(v)))""",
    )
    assert "fresh-7d" in outcome["afterFast"]
    assert "stale-30d" not in outcome["final"]
    assert outcome["stats"] == 5
    assert outcome["statUrls"][-2:] == [
        "/api/traces/stats?from=2026-09-01&to=2026-09-30",
        "/api/traces/stats?from=2026-09-20&to=2026-09-26",
    ]


def test_changed_scope_failure_keeps_stats_unknown_and_retryable(node: str) -> None:
    outcome = _eval_dash(
        node,
        r"""(async () => {
          const previous = {
            totals: { requests: 7, tool_calls: 2, prompt_tokens: 70, cached_tokens: 10,
              completion_tokens: 20, total_tokens: 90, cost_usd: 0.25,
              latency_ms_p50: 40, latency_ms_p90: 60 },
            by_day: [{ day: '2026-09-20', requests: 7, cost_usd: 0.25 }],
            by_hour: [{ hour: '09', requests: 7, cost_usd: 0.25 }],
            by_bot: [{ bot_id: 'coder', requests: 7, tool_calls: 2, total_tokens: 90,
              cost_usd: 0.25, last_started_at: '2026-09-20T09:00:00Z' }],
            by_model: [{ model: 'old-model', requests: 7, total_tokens: 90,
              cost_usd: 0.25, latency_ms_p50: 40 }],
            models: ['old-model'], by_status: [{ status: 'completed' }],
          };
          rangeFrom = '2026-09-20'; rangeTo = '2026-09-20';
          statsKey = statsRequestKey(); statsData = previous; statsLoaded = true;
          renderStats(previous);
          listFilters.model = 'old-model'; listFilters.status = 'completed';
          rangeFrom = '2026-10-20'; rangeTo = '2026-10-20';
          __jsonResponder = async () => { throw new Error('scope unavailable'); };
          await loadStats();
          dayMetric = 'tokens'; renderDayChart();
          return {
            totals: [
              document.querySelector('#usage-chart-total').textContent,
              document.querySelector('#cost-chart-total').textContent,
            ],
            usageLegend: document.querySelector('#usage-chart-legend').innerHTML,
            costLegend: document.querySelector('#cost-chart-legend').innerHTML,
            coverageHidden: document.querySelector('#cost-coverage').hidden,
            coverageText: document.querySelector('#cost-coverage').textContent,
            status: document.querySelector('#stats-status-text').textContent,
            statusHidden: document.querySelector('#page-status').hidden,
            retryHidden: document.querySelector('#stats-retry').hidden,
            day: document.querySelector('#chart-day').innerHTML,
            cost: document.querySelector('#chart-cost').innerHTML,
            modelValue: document.querySelector('#filter-model').value,
            modelOptions: document.querySelector('#filter-model').options.map(option => option.value),
            statusValue: document.querySelector('#filter-status').value,
            statsData: statsData,
          };
        })().then(value => console.log(JSON.stringify(value)))""",
    )
    assert outcome["totals"] == ["—", "—"]
    assert 'id="usage-input-total">—</strong>' in outcome["usageLegend"]
    assert outcome["costLegend"] == ""
    assert outcome["coverageHidden"] is True and outcome["coverageText"] == ""
    assert outcome["statusHidden"] is False and "scope unavailable" in outcome["status"]
    assert outcome["retryHidden"] is False
    assert outcome["day"] == "" and outcome["cost"] == ""
    assert outcome["modelValue"] == "old-model" and "old-model" in outcome["modelOptions"]
    assert outcome["statusValue"] == "completed"
    assert outcome["statsData"] is None


def test_stats_and_trace_responses_have_independent_guards(node: str) -> None:
    """loadStats and loadTraces keep separate sequences: a stale LIST answer
    is dropped without disturbing a just-rendered stats card."""
    outcome = _eval_dash(
        node,
        """(async () => {
          const pending = [];
          __jsonResponder = (url) => new Promise((resolve) => {
            pending.push({ url, resolve });
          });
          loadStats(true);
          loadTraces(true, true);
          loadTraces(true, true);
          pending[0].resolve({ totals: { requests: 9 }, by_model: [] });
          pending[2].resolve({ traces: [{ kind: 'dm', bot_id: 's2', turn_index: 0 }],
                               total: 1 });
          await new Promise((r) => setTimeout(r, 5));
          pending[1].resolve({ traces: [{ kind: 'dm', bot_id: 'old', turn_index: 0 }],
                               total: 99 });
          await new Promise((r) => setTimeout(r, 5));
          return { rows: traceState.rows.map((t) => t.bot_id),
            total: traceState.total, stats: statsData.totals.requests,
            html: document.querySelector('#trace-conversations').innerHTML };
        })().then((v) => console.log(JSON.stringify(v)))""",
    )
    assert outcome["rows"] == ["s2"]
    assert outcome["total"] == 1
    assert outcome["stats"] == 9
    assert "old" not in outcome["html"]


def _bot_legend_eval(node: str, by_bot: list, roster: dict) -> str:
    return _eval_dash(
        node,
        f"globalThis.__roster = {json.dumps(roster)};\n"
        f"statsData = {{ by_bot: {json.dumps(by_bot)},"
        "  totals: { requests: 10, total_tokens: 100 } };\n"
        "setUsageGroup('bot');\n"
        "console.log(JSON.stringify(document.querySelector('#usage-chart-legend').innerHTML));",
    )


def test_request_bots_sort_color_and_unknown(node: str) -> None:
    """Bot legend ranks by active metric desc, renders roster bots as
    clickable scope buttons in their signature color, and labels unattributed
    turns 'Chưa gắn bot' with the neutral fallback color."""
    by_bot = [
        {"bot_id": "designer", "requests": 2, "tool_calls": 1,
         "total_tokens": 20, "cost_usd": 0.001,
         "last_started_at": "2026-09-19T09:00:00Z"},
        {"bot_id": "coder", "requests": 7, "tool_calls": 3,
         "total_tokens": 70, "cost_usd": 0.007,
         "last_started_at": "2026-09-20T09:00:00Z"},
        {"bot_id": "unknown", "requests": 1, "tool_calls": 0,
         "total_tokens": 10, "cost_usd": None,
         "last_started_at": "2026-09-18T09:00:00Z"},
    ]
    roster = {
        "coder": {"id": "coder", "name": "Coder", "color": "#123", "state": "idle"},
        "designer": {"id": "designer", "name": "Designer", "color": "#456", "state": "idle"},
    }
    legend = _bot_legend_eval(node, by_bot, roster)
    assert legend.index("Coder") < legend.index("Designer") < legend.index("Chưa gắn bot")
    assert 'data-bot-id="coder"' in legend and "background:#123" in legend
    assert 'data-bot-id="designer"' in legend and "background:#456" in legend
    assert 'data-bot-id="unknown"' not in legend and "background:#a08f68" in legend
    assert "Chưa gắn bot" in legend
    assert "70%" in legend and "20%" in legend and "10%" in legend


def test_request_unknown_id_in_roster_renders_as_real_bot(node: str) -> None:
    """If /api/bots really contains id 'unknown', botDisplayName resolves to
    its card name instead of 'Chưa gắn bot'."""
    by_bot = [{"bot_id": "unknown", "requests": 1, "tool_calls": 0,
               "total_tokens": 10, "cost_usd": None,
               "last_started_at": "2026-09-18T09:00:00Z"}]
    legend = _bot_legend_eval(
        node, by_bot,
        {"unknown": {"id": "unknown", "name": "Nemo", "color": "#789", "state": "idle"}},
    )
    assert "Nemo" in legend
    assert "Chưa gắn bot" not in legend


def test_bot_legend_tooltip_and_shares(node: str) -> None:
    """Usage bot legend carries concise values + percentages inline and full
    turn/request counts in the hover tooltip."""
    by_bot = [
        {"bot_id": "coder", "turns": 4, "requests": 8, "tool_calls": 2,
         "total_tokens": 80, "cost_usd": 0.004,
         "last_started_at": "2026-09-20T09:00:00Z"},
        {"bot_id": "designer", "requests": 2, "tool_calls": 0,
         "total_tokens": 20, "cost_usd": 0.001,
         "last_started_at": "2026-09-19T09:00:00Z"},
    ]
    legend = _bot_legend_eval(node, by_bot, {})
    assert "4 lượt · 8 lần gọi" in legend
    assert "2 lần gọi" in legend
    assert "$0.0040" not in legend
    assert "80%" in legend and "20%" in legend


def test_model_series_folds_unknown_without_leaking_raw_id(node: str) -> None:
    """When named models coexist with 'unknown', 'unknown' folds into 'Khác'
    so stacked heights match totals while raw 'unknown' never renders."""
    by_model = [
        {"model": "unknown", "requests": 3, "total_tokens": 30,
         "cost_usd": 0.03, "latency_ms_p50": 50},
        {"model": "known-model", "requests": 7, "total_tokens": 70,
         "cost_usd": 0.07, "latency_ms_p50": 60},
    ]
    out = _eval_dash(
        node,
        f"statsData = {{ by_model: {json.dumps(by_model)},"
        "  totals: { total_tokens: 100, cost_usd: 0.10 } };\n"
        "setUsageGroup('model');\n"
        "renderCostChart();\n"
        "console.log(JSON.stringify({"
        " usage: document.querySelector('#usage-chart-legend').innerHTML,"
        " cost: document.querySelector('#cost-chart-legend').innerHTML,"
        " kept: statsData.by_model.length }))",
    )
    for key in ("usage", "cost"):
        assert "known-model" in out[key]
        assert "Khác" in out[key]
        assert "unknown" not in out[key]
        assert "70%" in out[key] and "30%" in out[key]
    assert out["kept"] == 2


def test_only_unknown_model_labels_honestly(node: str) -> None:
    """An unknown-only period labels the legend 'Không rõ model' (never raw
    'unknown'), while bot grouping keeps 'Chưa gắn bot'."""
    out = _eval_dash(
        node,
        "statsData = { by_model: [{ model: 'unknown', requests: 1,"
        " total_tokens: 10, cost_usd: 0.01 }],"
        " by_bot: [{ bot_id: 'unknown', requests: 1, total_tokens: 10,"
        " cost_usd: 0.01 }],"
        " totals: { total_tokens: 10, cost_usd: 0.01 } };\n"
        "setUsageGroup('model');\n"
        "const usageModel = document.querySelector('#usage-chart-legend').innerHTML;\n"
        "renderCostChart();\n"
        "const costModel = document.querySelector('#cost-chart-legend').innerHTML;\n"
        "setUsageGroup('bot');\n"
        "const bots = document.querySelector('#usage-chart-legend').innerHTML;\n"
        "console.log(JSON.stringify({ usageModel, costModel, bots }))",
    )
    assert "Không rõ model" in out["usageModel"] and "unknown" not in out["usageModel"]
    assert "Không rõ model" in out["costModel"] and "unknown" not in out["costModel"]
    assert "Chưa gắn bot" in out["bots"]


def test_genuinely_empty_charts_keep_no_data_notes(node: str) -> None:
    """A period with no buckets shows the empty note in both charts and
    leaves series legends clean."""
    out = _eval_dash(
        node,
        "statsData = { by_model: [], by_day: [], by_hour: [],"
        " totals: { total_tokens: 0, cost_usd: null } };\n"
        "setUsageGroup('model');\n"
        "renderCostChart();\n"
        "console.log(JSON.stringify({"
        " usage: document.querySelector('#chart-day').innerHTML,"
        " cost: document.querySelector('#chart-cost').innerHTML,"
        " usageLegend: document.querySelector('#usage-chart-legend').innerHTML,"
        " costLegend: document.querySelector('#cost-chart-legend').innerHTML }))",
    )
    assert "Chưa có lượt nào trong kỳ." in out["usage"]
    assert "Chưa có lượt nào trong kỳ." in out["cost"]
    assert out["usageLegend"] == "" and out["costLegend"] == ""
