"""Node tests for the dashboard cost surface — ui/dashboard/js/dashboard.js.

The old Chi phí view (cost-data.js / cost-view.js / cost-rows.js /
cost-bots.js and the shared journal pager) was not ported: pricing
estimates, per-model share denominators, the per-session cost journal and
the average-per-turn card are gone. The new Dashboard keeps cost on a
recorded basis: the "Chi phí" stat card, the "Token" card's
vào/cache/ra split, the by_day cost metric and the "Lượt gần nhất" table —
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

ROOT = Path(__file__).resolve().parents[1]
DASH_JS = ROOT / "thyca" / "webui" / "ui" / "dashboard" / "js" / "dashboard.js"
DASH_HTML = ROOT / "thyca" / "webui" / "ui" / "dashboard" / "index.html"

# Same stub surface as test_request_chart.py: real util.js semantics for
# $/$$/escapeHTML/icon, a per-id element registry, and the data-layer
# globals dashboard.js expects.
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
globalThis.getBot = (id) => globalThis.__roster[id] || null;
globalThis.avatar = (b) => `<ava:${b.id}/>`;
globalThis.toast = () => {};
globalThis.bootShell = async () => {};
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
    """Eval dashboard.js (classic script) with stubs, then run `body` inside
    the same scope so module-level functions/state are reachable."""
    src = DASH_JS.read_text(encoding="utf-8")
    program = (
        STUBS
        + "\nconst __src = " + json.dumps(src) + ";\n"
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


def test_no_per_session_journal_remains() -> None:
    """Static pins: the per-session cost journal was dropped with the old
    dashboard (Trace keeps its own session list); nothing in the new page
    re-introduces it."""
    html = DASH_HTML.read_text(encoding="utf-8")
    src = DASH_JS.read_text(encoding="utf-8")
    assert "Chi phí theo phiên" not in html
    assert "cost-sessions" not in html
    for gone in ("aggregateSessions", "renderSessions", "NO_SESSION_KEY",
                 "modelTurnCoverage", "estimatedCostSplit", "priceRates"):
        assert gone not in src, gone


def test_null_vs_zero_cost_semantics(node: str) -> None:
    """fmtCost: null/garbage/NaN render the em dash; a real recorded zero is
    a true $0 price, never confused with missing data."""
    result = _eval_dash(
        node,
        "console.log(JSON.stringify([fmtCost(null), fmtCost(undefined),"
        " fmtCost('nope'), fmtCost(NaN), fmtCost(0), fmtCost(0.5),"
        " fmtCost(2), fmtCost(0.000001)]))",
    )
    nothing, missing, garbage, nan, zero, half, two, tiny = result
    assert nothing == "—" and missing == "—" and garbage == "—" and nan == "—"
    assert zero == "$0.000000"
    assert half == "$0.5000"   # 4 decimals under $1
    assert two == "$2.00"      # 2 decimals at $1+
    assert tiny == "$0.000001"  # 6 decimals under $0.01


def test_share_denominator_and_vi_format(node: str) -> None:
    """share(): percent of the period total with vi-VN decimals — one
    decimal under 10%, integer above; an empty denominator reads 0%, never
    NaN or Infinity."""
    result = _eval_dash(
        node,
        "console.log(JSON.stringify([share(0.75, 1), share(0.25, 1),"
        " share(0.056, 1), share(1, 0), share(null, 10),"
        " share(0.9999, 1)]))",
    )
    big, small, frac, empty_den, null_num, almost = result
    assert big == "75%" and small == "25%"
    assert frac == "5,6%"          # vi-VN decimal comma, one decimal
    assert empty_den == "0%"       # no fake share on a zero total
    assert null_num == "0%"
    assert almost == "100%"        # rounded, not "99,99%" noise


def test_hero_cards_carry_the_cost_and_token_split(node: str) -> None:
    """The stat grid keeps: Lượt = model requests, Gọi tool, Token with the
    vào/cache/ra split (cache lives inside prompt — shown, not re-added),
    Chi phí with the per-request average, Độ trễ p50 with the p90 hint."""
    totals = {
        "requests": 5, "tool_calls": 7, "prompt_tokens": 150,
        "cached_tokens": 30, "completion_tokens": 15, "total_tokens": 165,
        "cost_usd": 0.03, "latency_ms_p50": 1200, "latency_ms_p90": 3400,
    }
    html = _eval_dash(
        node,
        f"renderStats({{ totals: {json.dumps(totals)} }});\n"
        "console.log(JSON.stringify(document.querySelector('#stat-grid').innerHTML));",
    )
    assert ">Lượt</p>" in html and ">5</p>" in html
    assert "request tới model" in html
    assert ">Gọi tool</p>" in html and ">7</p>" in html
    assert ">Token</p>" in html
    # Cache is a subset of prompt: both shown, and the card total is the
    # server total (165 = 150 + 15), never 150 + 30 + 15.
    assert "vào 150 · cache 30 · ra 15" in html
    assert ">165</p>" in html
    assert ">Chi phí</p>" in html and "$0.0300" in html
    assert "TB $0.006000 / request" in html  # 0.03 / 5 requests
    assert ">Độ trễ p50</p>" in html
    assert "1,2 giây" in html and "p90 3,4 giây" in html


def test_cost_card_without_any_priced_turn(node: str) -> None:
    """cost_usd == null on the totals means no turn recorded a price: the
    card shows the em dash + the explicit hint, never $0."""
    html = _eval_dash(
        node,
        "renderStats({ totals: { requests: 3, cost_usd: null,"
        " latency_ms_p50: null, latency_ms_p90: null } });\n"
        "console.log(JSON.stringify(document.querySelector('#stat-grid').innerHTML));",
    )
    assert "chưa có lượt nào ghi nhận giá" in html
    assert ">—</p>" in html
    assert "$0" not in html
    assert "TB " not in html


def test_multi_model_turn_tokens_split_not_double_counted(node: str) -> None:
    """A turn whose prompt side is all cache: the hint still shows
    input/cache/output once each — cache is inside prompt, never stacked
    on top."""
    html = _eval_dash(
        node,
        "renderStats({ totals: { requests: 1, prompt_tokens: 200,"
        " cached_tokens: 200, completion_tokens: 5, total_tokens: 205,"
        " cost_usd: null, latency_ms_p50: null, latency_ms_p90: null } });\n"
        "console.log(JSON.stringify(document.querySelector('#stat-grid').innerHTML));",
    )
    assert "vào 200 · cache 200 · ra 5" in html
    assert ">205</p>" in html  # prompt + completion, cache not re-added


def test_daily_costs_zero_fill_missing_days(node: str) -> None:
    """dayColumns: the range drives the columns — every day in [from, to]
    gets a bucket (sparse days fill with 0 requests / null cost), payload
    days outside the range are dropped, a broken 'day' key never lands."""
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
        {"day": "2026-09-21", "requests": 0, "cost_usd": None},
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


def test_day_chart_metric_switch(node: str) -> None:
    """The by_day card's Lượt/Chi phí toggle swaps the column metric:
    cost_usd mode bars scale on cost (null cost = zero height), requests
    mode on request counts."""
    stats = {
        "by_day": [
            {"day": "2026-09-20", "requests": 1, "cost_usd": 0.9},
            {"day": "2026-09-21", "requests": 9, "cost_usd": 0.1},
            {"day": "2026-09-22", "requests": 0, "cost_usd": None},
        ]
    }
    out = _eval_dash(
        node,
        f"statsData = {json.dumps(stats)};\n"
        "rangeFrom = ''; rangeTo = '';   // sparse buckets as-is\n"
        "dayMetric = 'requests'; renderDayChart();\n"
        "const req = document.querySelector('#chart-day').innerHTML;\n"
        "dayMetric = 'cost_usd'; renderDayChart();\n"
        "const cost = document.querySelector('#chart-day').innerHTML;\n"
        "console.log(JSON.stringify({ req, cost }))",
    )
    req_heights = [
        float(h) for h in
        [p.split('height:')[1].split('%')[0] for p in out["req"].split('dc-bar')[1:]]
    ]
    cost_heights = [
        float(h) for h in
        [p.split('height:')[1].split('%')[0] for p in out["cost"].split('dc-bar')[1:]]
    ]
    # Requests: 1/9, 9/9, 0 — middle day is the tallest.
    assert req_heights[0] == pytest.approx(1 / 9 * 100)
    assert req_heights[1] == 100.0
    # Cost: 0.9/0.9, 0.1/0.9 — the first day wins.
    assert cost_heights[0] == 100.0
    assert cost_heights[1] == pytest.approx(1 / 9 * 100)
    assert "Chi phí theo ngày" in out["cost"] or 'aria-label="Chi phí' in out["cost"]
    # Zero days keep a visible zero-height track, not a missing bar.
    assert "is-zero" in out["req"]


def test_paging_multiple_pages_and_midway_failure(node: str) -> None:
    """"Tải thêm" is server paging: page 1 is 15 rows, the next request asks
    limit=50&offset=<rows so far>, rows accumulate, and a midway failure is
    an explicit error state — never a silent partial list."""
    out = _eval_dash(
        node,
        """(async () => {
          const row = (i) => ({ session_id: 's' + i, turn_index: 0 });
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
            status: document.querySelector('#trace-status').textContent,
            statusHidden: document.querySelector('#trace-status').hidden };
          return { ok, afterError };
        })().then((v) => console.log(JSON.stringify(v)))""",
    )
    assert out["ok"]["rows"] == 65
    assert out["ok"]["total"] == 100
    assert out["ok"]["more"] is False  # still more to fetch
    assert out["ok"]["urls"][0].endswith("limit=15&offset=0")
    assert out["ok"]["urls"][1].endswith("limit=50&offset=15")
    # Midway failure: explicit error, rows kept — nothing silently partial.
    assert out["afterError"]["error"] == "backend died"
    assert out["afterError"]["rows"] == 65
    assert "Không tải được danh sách lượt" in out["afterError"]["status"]
    assert out["afterError"]["statusHidden"] is False


def test_load_more_hidden_at_total_and_rows_render(node: str) -> None:
    """"Hiển thị X trong Y lượt" tracks the loaded window; the button hides
    once rows reach total (and during loads), and each row links its turn
    to the trace page deep link."""
    out = _eval_dash(
        node,
        """(async () => {
          const row = (i) => ({ session_id: 's x', title: 'T', turn_index: i,
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
            html: document.querySelector('#trace-rows').innerHTML };
          await loadTraces(false);
          return { first,
            second: { count: document.querySelector('#trace-count').textContent,
              more: document.querySelector('#trace-more').hidden } };
        })().then((v) => console.log(JSON.stringify(v)))""",
    )
    assert out["first"]["count"] == "Hiển thị 15 trong 20 lượt"
    assert out["first"]["more"] is False
    # Deep-link convention: ../trace/index.html#<session>/<turn>, encoded.
    assert 'data-href="../trace/index.html#s%20x/0"' in out["first"]["html"]
    assert out["second"]["count"] == "Hiển thị 20 trong 20 lượt"
    assert out["second"]["more"] is True


def test_cost_bot_rows_have_avatar_slots(node: str) -> None:
    """Every by_bot row keeps an aligned avatar slot: a seal for roster
    bots, the dashed '?' chip for unattributed ('unknown') rows — and the
    roster patch names the row."""
    by_bot = [
        {"bot_id": "coder", "requests": 3, "tool_calls": 1,
         "total_tokens": 30, "cost_usd": 0.01,
         "last_started_at": "2026-09-20T09:00:00Z"},
        {"bot_id": "unknown", "requests": 1, "tool_calls": 0,
         "total_tokens": 10, "cost_usd": None,
         "last_started_at": "2026-09-18T09:00:00Z"},
    ]
    html = _eval_dash(
        node,
        "globalThis.__roster = { coder: { id: 'coder', name: 'Coder',"
        " color: '#123', state: 'idle' } };\n"
        f"statsData = {{ by_bot: {json.dumps(by_bot)}, totals: {{ requests: 4 }} }};\n"
        "renderBotRows();\n"
        "console.log(JSON.stringify(document.querySelector('#rows-bot').innerHTML));",
    )
    # Both rows keep an aligned slot; only the known bot gets a seal.
    assert html.count('<span class="row-avatar') == 2
    assert "<ava:coder/>" in html
    assert '<span class="row-avatar id-chip" title="unknown">?</span>' in html
    assert ">Coder</span>" in html
    assert "Chưa gắn bot" in html
