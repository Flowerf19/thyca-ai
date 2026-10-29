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


# Minimal global surface for dashboard.js: real util.js semantics for
# $/$$/escapeHTML/icon, a per-id element registry, and data-layer stubs the
# page expects (getBot/getJson/toast/avatar/bootShell). boot() is stripped:
# tests drive the individual render/load functions directly.
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


def test_model_rows_sorted_by_tokens_desc(node: str) -> None:
    """"Model trong kỳ": one row per model, sorted by total_tokens desc,
    name/requests/tokens/p50/cost as real text."""
    models = [
        {"model": "muse-spark-1.2-contributor", "requests": 30,
         "total_tokens": 300, "cost_usd": 0.01, "latency_ms_p50": 900,
         "last_started_at": "2026-09-19T09:00:00Z"},
        {"model": "gpt-5.6-luna", "requests": 50,
         "total_tokens": 5000, "cost_usd": 0.02, "latency_ms_p50": 1200,
         "last_started_at": "2026-09-20T09:00:00Z"},
        {"model": "a-very-long-model-name-with-date-suffix-20250929",
         "requests": 20, "total_tokens": 200, "cost_usd": None,
         "latency_ms_p50": None, "last_started_at": ""},
    ]
    rows = _eval_dash(
        node,
        f"statsData = {{ by_model: {json.dumps(models)} }};\n"
        "renderModelRows();\n"
        "console.log(JSON.stringify(document.querySelector('#rows-model').innerHTML));",
    )
    names = [
        m.split('title="')[1].split('"')[0]
        for m in rows.split('<span class="model-name"')[1:]
    ]
    assert names == [
        "gpt-5.6-luna",
        "muse-spark-1.2-contributor",
        "a-very-long-model-name-with-date-suffix-20250929",
    ]
    assert "50 request" in rows
    assert "5.000 token" in rows  # vi-VN grouping
    assert "p50 1,2 giây" in rows
    assert "$0.0200" in rows
    assert "$0.0100" in rows
    # Null cost is preserved in the model metadata rather than rendered as $0.
    assert "200 token · — · p50 —" in rows


def test_empty_zero_and_missing_models_are_honest(node: str) -> None:
    """An empty by_model renders the empty note; a model with no p50/cost
    keeps its em dashes."""
    rows = _eval_dash(
        node,
        "statsData = { by_model: [] };\n"
        "renderModelRows();\n"
        "console.log(JSON.stringify(document.querySelector('#rows-model').innerHTML));",
    )
    assert "Chưa có lượt nào trong kỳ." in rows
    one = _eval_dash(
        node,
        'statsData = { by_model: [{model: "solo-model", requests: 7,'
        ' total_tokens: 70, cost_usd: 0, latency_ms_p50: 40,'
        ' last_started_at: "2026-09-20T09:00:00Z"}] };\n'
        "renderModelRows();\n"
        "console.log(JSON.stringify(document.querySelector('#rows-model').innerHTML));",
    )
    assert one.count('class="model-row"') == 1
    assert "solo-model" in one
    assert "p50 40 ms" in one
    # A real zero cost is recorded pricing, not missing data.
    assert "$0.000000" in one


def test_long_and_hostile_names_stay_text(node: str) -> None:
    """A hostile model id is escaped everywhere it renders — name and the
    title attribute — never parsed into markup."""
    rows = _eval_dash(
        node,
        'statsData = { by_model: [{model: "<img src=x onerror=alert(1)>",'
        ' requests: 3, total_tokens: 30}] };\n'
        "renderModelRows();\n"
        "console.log(JSON.stringify(document.querySelector('#rows-model').innerHTML));",
    )
    assert "<img src=x onerror=alert(1)>" not in rows
    assert "&lt;img src=x onerror=alert(1)&gt;" in rows


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
          rangeFrom = '2026-09-01'; rangeTo = '2026-09-30';
          const first = loadStats();
          rangeFrom = '2026-09-20'; rangeTo = '2026-09-26';
          const second = loadStats();
          // The newer request resolves first…
          pending[1].resolve({ totals: { requests: 5 }, by_model: [
            { model: 'fresh-7d', requests: 5, total_tokens: 50 }] });
          await second;
          const afterFast = document.querySelector('#rows-model').innerHTML;
          // …then the older response lands late and must be ignored.
          pending[0].resolve({ totals: { requests: 900 }, by_model: [
            { model: 'stale-30d', requests: 900, total_tokens: 9000 }] });
          await new Promise((r) => setTimeout(r, 5));
          return { afterFast,
            final: document.querySelector('#rows-model').innerHTML,
            stats: statsData.totals.requests,
            statUrls: __jsonCalls.filter((u) => u.includes('stats')) };
        })().then((v) => console.log(JSON.stringify(v)))""",
    )
    assert "fresh-7d" in outcome["afterFast"]
    assert "stale-30d" not in outcome["final"]
    assert outcome["stats"] == 5
    # The two range-bound stats requests, in order (boot's own stats call
    # already landed or errored out before this scenario runs).
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
          const grid = document.querySelector('#stat-grid').innerHTML;
          return {
            values: [...grid.matchAll(/class="stat-value">([^<]+)<\/p>/g)].map(match => match[1]),
            grid,
            status: document.querySelector('#stats-status-text').textContent,
            statusHidden: document.querySelector('#page-status').hidden,
            retryHidden: document.querySelector('#stats-retry').hidden,
            day: document.querySelector('#chart-day').innerHTML,
            dayNote: document.querySelector('#chart-day-note').textContent,
            hour: document.querySelector('#chart-hour').innerHTML,
            bots: document.querySelector('#rows-bot').innerHTML,
            models: document.querySelector('#rows-model').innerHTML,
            modelValue: document.querySelector('#filter-model').value,
            modelOptions: document.querySelector('#filter-model').options.map(option => option.value),
            statusValue: document.querySelector('#filter-status').value,
            statsData: statsData,
          };
        })().then(value => console.log(JSON.stringify(value)))""",
    )
    assert outcome["values"] == ["—"] * 7
    assert "chưa có lượt nào ghi nhận giá" not in outcome["grid"].lower()
    assert outcome["statusHidden"] is False and "scope unavailable" in outcome["status"]
    assert outcome["retryHidden"] is False
    assert all(outcome[key] == "" for key in ("day", "dayNote", "hour", "bots", "models"))
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
          loadTraces(true, true);   // the user re-filters: a newer list request wins
          pending[0].resolve({ totals: { requests: 9 }, by_model: [] });
          pending[2].resolve({ traces: [{ session_id: 's2', turn_index: 0 }],
                               total: 1 });
          await new Promise((r) => setTimeout(r, 5));
          // The stale first list page arrives late and must be dropped.
          pending[1].resolve({ traces: [{ session_id: 'old', turn_index: 0 }],
                               total: 99 });
          await new Promise((r) => setTimeout(r, 5));
          return { rows: traceState.rows.map((t) => t.session_id),
            total: traceState.total, stats: statsData.totals.requests,
            html: document.querySelector('#trace-sessions').innerHTML };
        })().then((v) => console.log(JSON.stringify(v)))""",
    )
    assert outcome["rows"] == ["s2"]
    assert outcome["total"] == 1
    assert outcome["stats"] == 9
    assert "old" not in outcome["html"]


def _bot_rows_eval(node: str, by_bot: list, roster: dict) -> str:
    return _eval_dash(
        node,
        f"globalThis.__roster = {json.dumps(roster)};\n"
        f"statsData = {{ by_bot: {json.dumps(by_bot)},"
        "  totals: { requests: 10, total_tokens: 100 } };\n"
        "renderBotRows();\n"
        "console.log(JSON.stringify(document.querySelector('#rows-bot').innerHTML));",
    )


def test_request_bots_avatar_sort_and_unknown(node: str) -> None:
    """by_bot rows: requests desc, roster bots get a seal + card name,
    unattributed turns render 'Chưa gắn bot' with the '?' chip, and the
    share label uses the period total."""
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
    rows = _bot_rows_eval(node, by_bot, roster)
    order = [n for n in ("Coder", "Designer", "Chưa gắn bot")]
    assert rows.index("Coder") < rows.index("Designer") < rows.index("Chưa gắn bot")
    assert "<ava:coder/>" in rows and "<ava:designer/>" in rows
    # Unattributed: no seal, dashed '?' chip carrying the raw bucket id.
    assert '<span class="row-avatar id-chip" title="unknown">?</span>' in rows
    assert "Chưa gắn bot" in rows
    # Shares are against the authoritative 100-token period total.
    assert "70%" in rows and "20%" in rows and "10%" in rows
    assert 'aria-label="70% tổng token"' in rows


def test_request_unknown_id_in_roster_renders_as_real_bot(node: str) -> None:
    """If /api/bots really contains id 'unknown', it is a bot: seal + card
    name instead of the '?' fallback."""
    by_bot = [{"bot_id": "unknown", "requests": 1, "tool_calls": 0,
               "total_tokens": 10, "cost_usd": None,
               "last_started_at": "2026-09-18T09:00:00Z"}]
    rows = _bot_rows_eval(
        node, by_bot,
        {"unknown": {"id": "unknown", "name": "Nemo", "color": "#789", "state": "idle"}},
    )
    assert "<ava:unknown/>" in rows
    assert "Nemo" in rows
    assert "id-chip" not in rows


def test_bot_row_meta_and_share_bar(node: str) -> None:
    """Each bot row keeps the compact meta line (requests · tool · tokens ·
    cost · last activity) and a share bar scaled to the largest bot."""
    by_bot = [
        {"bot_id": "coder", "requests": 8, "tool_calls": 2,
         "total_tokens": 80, "cost_usd": 0.004,
         "last_started_at": "2026-09-20T09:00:00Z"},
        {"bot_id": "designer", "requests": 2, "tool_calls": 0,
         "total_tokens": 20, "cost_usd": 0.001,
         "last_started_at": "2026-09-19T09:00:00Z"},
    ]
    rows = _bot_rows_eval(node, by_bot, {})
    assert "8 request · 2 tool · 80 token · $0.0040" in rows
    # Biggest bot takes the full bar; the smaller keeps a visible minimum.
    assert 'style="width:100%' in rows or "width:100;" in rows or "width:100%" in rows
    assert "80% tổng token" in rows
    assert "20% tổng token" in rows


def test_model_rows_hide_unknown_but_keep_totals(node: str) -> None:
    """The 'unknown' model bucket never renders in either by-model
    listing, while totals/shares still count it and bot attribution is
    untouched."""
    by_model = [
        {"model": "unknown", "requests": 3, "total_tokens": 30,
         "cost_usd": 0.03, "latency_ms_p50": 50,
         "last_started_at": "2026-09-21T09:00:00Z"},
        {"model": "known-model", "requests": 7, "total_tokens": 70,
         "cost_usd": 0.07, "latency_ms_p50": 60,
         "last_started_at": "2026-09-20T09:00:00Z"},
    ]
    out = _eval_dash(
        node,
        f"statsData = {{ by_model: {json.dumps(by_model)},"
        "  totals: { total_tokens: 100, cost_usd: 0.10 } };\n"
        "renderModelRows();\n"
        "renderCostRows();\n"
        "console.log(JSON.stringify({"
        " usage: document.querySelector('#rows-model').innerHTML,"
        " cost: document.querySelector('#rows-model-cost').innerHTML,"
        " kept: statsData.by_model.length }))",
    )
    for key in ("usage", "cost"):
        assert "known-model" in out[key]
        assert "unknown" not in out[key]
    # Shares stay against the authoritative period totals (70/100, $0.07/$0.10).
    assert "70%" in out["usage"]
    assert "70%" in out["cost"]
    # Underlying payload is preserved for totals/filters, not deleted.
    assert out["kept"] == 2


def test_only_unknown_model_says_no_identifiable_model(node: str) -> None:
    """An unknown-only period hides the row and says no model is
    identifiable (not that there are no turns); bot rows still keep their
    own 'unknown' attribution."""
    out = _eval_dash(
        node,
        "statsData = { by_model: [{ model: 'unknown', requests: 1,"
        " total_tokens: 10, cost_usd: 0.01 }],"
        " by_bot: [{ bot_id: 'unknown', requests: 1, total_tokens: 10,"
        " cost_usd: 0.01, last_started_at: '2026-09-20T09:00:00Z' }],"
        " totals: { total_tokens: 10, cost_usd: 0.01 } };\n"
        "renderModelRows();\n"
        "renderCostRows();\n"
        "renderBotRows();\n"
        "console.log(JSON.stringify({"
        " usage: document.querySelector('#rows-model').innerHTML,"
        " cost: document.querySelector('#rows-model-cost').innerHTML,"
        " bots: document.querySelector('#rows-bot').innerHTML }))",
    )
    assert "Không có model xác định trong kỳ." in out["usage"]
    assert "Không có model xác định trong kỳ." in out["cost"]
    assert "Chưa có lượt nào trong kỳ." not in out["usage"]
    assert "Chưa có dữ liệu chi phí theo model." not in out["cost"]
    assert "Chưa gắn bot" in out["bots"]


def test_genuinely_empty_model_lists_keep_no_data_notes(node: str) -> None:
    """A period with no by_model entries keeps the existing no-data
    messages in both by-model listings."""
    out = _eval_dash(
        node,
        "statsData = { by_model: [],"
        " totals: { total_tokens: 0, cost_usd: null } };\n"
        "renderModelRows();\n"
        "renderCostRows();\n"
        "console.log(JSON.stringify({"
        " usage: document.querySelector('#rows-model').innerHTML,"
        " cost: document.querySelector('#rows-model-cost').innerHTML }))",
    )
    assert "Chưa có lượt nào trong kỳ." in out["usage"]
    assert "Chưa có dữ liệu chi phí theo model." in out["cost"]
    assert "Không có model xác định trong kỳ." not in out["usage"]
    assert "Không có model xác định trong kỳ." not in out["cost"]
