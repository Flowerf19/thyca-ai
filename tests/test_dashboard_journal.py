"""Tests for the rebuilt Dashboard — thyca/webui/ui/dashboard/.

The old four-view dashboard (overview.js entity selector, hash routing
#su-dung/#request/#chi-phi/#hom-nay -> #trace, fetchAllTraces dedupe
paging in dashboard-today.js) was not ported. The new page is one screen —
"Sử dụng & chi phí": range picker + stat cards + by_day/by_bot/by_model/
by_hour charts + the paged "Lượt gần nhất" table, all from
GET /api/traces/stats and GET /api/traces, inside the shared emitShell/
bootShell contract. Turn rows deep-link to ../trace/index.html#<sid>/<turn>.
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

ROOT = Path(__file__).resolve().parents[1]
WEBUI = ROOT / "thyca" / "webui" / "ui"
DASH_JS = WEBUI / "dashboard" / "js" / "dashboard.js"
DASH_HTML = WEBUI / "dashboard" / "index.html"
UTIL_JS = WEBUI / "share" / "util.js"

# Same stub surface as test_cost_journal.py / test_request_chart.py plus
# toast capture: real util.js semantics for $/$$/escapeHTML/icon, a per-id
# element registry, and the data-layer globals dashboard.js expects.
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
const __toasts = [];
globalThis.toast = (m) => __toasts.push(String(m));
globalThis.bootShell = async () => {
  globalThis.__roster.coder = { id: 'coder', name: 'Coder', color: '#123', state: 'idle' };
};
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
    """Eval dashboard.js (classic script) with stubs, then run `body` inside
    the same scope so module-level functions/state are reachable. `setup`
    runs before the source — use it to install a getJson responder the
    boot-time fetches should see."""
    src = DASH_JS.read_text(encoding="utf-8")
    program = (
        STUBS
        + setup + "\n"
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


def test_page_is_a_single_screen_shell_document() -> None:
    """index.html keeps the shared script order — emitShell() must run
    before sidebar.js/studio.js bind — and the page is one screen with no
    leftover view switcher."""
    html = DASH_HTML.read_text(encoding="utf-8")
    order = [
        '<script src="../share/util.js">',
        '<script src="../share/data.js">',
        '<script src="../share/shell.js">',
        "<script>emitShell('dashboard');</script>",
        '<script src="../share/avatars/avatars.js">',
        '<script src="../share/sidebar.js">',
        '<script src="../share/avatars/studio.js">',
        '<script src="../share/shell-app.js">',
        '<script src="js/dashboard.js">',
    ]
    pos = [html.index(s) for s in order]
    assert pos == sorted(pos), "shared shell scripts must load in order"
    assert '<script type="module"' not in html
    for gone in ("overview-nav", "data-view=", "cost-view", "#su-dung",
                 "session-pager", "journal-pager"):
        assert gone not in html, gone


def test_page_markup_covers_every_region() -> None:
    """PAGE_HTML (eval'd into .workspace) carries the full contract: range
    picker + presets, five stat cards, day/hour charts, bot/model rows and
    the paged trace table with its deep-link convention."""
    src = DASH_JS.read_text(encoding="utf-8")
    for needle in (
        'id="stat-grid"', 'id="range-note"', 'id="range-from"',
        'id="range-to"', 'id="range-apply"',
        'data-preset="1"', 'data-preset="7"', 'data-preset="30"',
        'data-day-metric="requests"', 'data-day-metric="cost_usd"',
        'id="chart-day"', 'id="chart-day-note"', 'id="rows-bot"',
        'id="rows-model"', 'id="chart-hour"',
        'id="filter-model"', 'id="filter-status"', 'id="trace-refresh"',
        'id="trace-status"', 'id="trace-wrap"', 'id="trace-rows"',
        'id="trace-empty"', 'id="trace-empty-hint"',
        'id="trace-count"', 'id="trace-more"',
        '/api/traces/stats?', '/api/traces?',
        '../trace/index.html#',
    ):
        assert needle in src, needle
    # Default range preset is "7 ngày" — only that chip starts pressed.
    assert re.search(
        r'data-preset="7"[^>]*aria-pressed="true"', src)
    assert not re.search(
        r'data-preset="(?:1|30)"[^>]*aria-pressed="true"', src)
    # Classic script — no ESM syntax.
    assert not re.search(r"^\s*(import|export)\s", src, re.M)
    # Errors surface in the page's own scoped red, not chat's global class.
    assert "'s-error'" in src


def test_shell_navigation_lists_dashboard_and_trace() -> None:
    """The shared SHELL_PAGES nav keeps Sử dụng and Trace as sibling pages
    (no standalone journal routes) and marks the current page. The table
    lives in util.js so every page — emitShell pages and chat's static
    markup alike — renders the same nav."""
    src = UTIL_JS.read_text(encoding="utf-8")
    assert "['dashboard', 'Sử dụng', '../dashboard/index.html']" in src
    assert "['trace', 'Trace', '../trace/index.html']" in src
    assert 'is-current' in src and 'aria-current="page"' in src


def test_boot_fetches_stats_and_first_page_for_last_7_days(node: str) -> None:
    """boot() defaults to the last 7 days, fetches stats + the first
    15-row page, fills the range inputs and runs bootShell() for the roster."""
    stats = {"totals": {"requests": 1}, "by_bot": [
        {"bot_id": "coder", "requests": 1, "total_tokens": 5,
         "cost_usd": 0.0, "last_started_at": "2026-09-20T09:00:00Z"}]}
    out = _eval_dash(
        node,
        "(async () => {\n"
        "await new Promise(r => setTimeout(r, 0));\n"
        "return {"
        " calls: __jsonCalls.slice(0, 2),"
        " from: document.querySelector('#range-from').value,"
        " to: document.querySelector('#range-to').value,"
        " note: document.querySelector('#range-note').textContent,"
        " botHtml: document.querySelector('#rows-bot').innerHTML };\n"
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
    # Last-7-days local range landed in the inputs and the note.
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", out["from"])
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", out["to"])
    assert out["from"] <= out["to"]
    assert out["note"].startswith("Kỳ ")
    # After bootShell() the roster patched the already-rendered bot row:
    # seal + display name, not the id-chip fallback.
    assert "<ava:coder/>" in out["botHtml"] and ">Coder</span>" in out["botHtml"]
    assert "Chưa gắn bot" not in out["botHtml"]


def test_apply_range_rejects_inverted_dates_without_fetching(node: str) -> None:
    """Từ > Đến is a toast, not a request — the range (and both fetches)
    stay untouched."""
    out = _eval_dash(
        node,
        "(async () => {\n"
        "await new Promise(r => setTimeout(r, 0));\n"
        "const before = __jsonCalls.length;\n"
        "document.querySelector('#range-from').value = '2026-09-22';\n"
        "document.querySelector('#range-to').value = '2026-09-20';\n"
        "applyRange();\n"
        "return { before, after: __jsonCalls.length,"
        " toasts: __toasts, from: rangeFrom, to: rangeTo };\n"
        "})().then((v) => console.log(JSON.stringify(v)))",
    )
    assert out["after"] == out["before"]
    assert out["toasts"] == ["Ngày bắt đầu phải trước hoặc bằng ngày kết thúc."]


def test_apply_range_refetches_both_surfaces(node: str) -> None:
    """A valid Áp dụng sends the new range to BOTH endpoints and restarts
    the table at offset 0 with the 15-row first page."""
    out = _eval_dash(
        node,
        "(async () => {\n"
        "await new Promise(r => setTimeout(r, 0));\n"
        "const before = __jsonCalls.length;\n"
        "document.querySelector('#range-from').value = '2026-09-01';\n"
        "document.querySelector('#range-to').value = '2026-09-30';\n"
        "applyRange();\n"
        "await new Promise(r => setTimeout(r, 0));\n"
        "return { urls: __jsonCalls.slice(before) };\n"
        "})().then((v) => console.log(JSON.stringify(v)))",
    )
    assert len(out["urls"]) == 2
    stats_url, traces_url = out["urls"]
    assert stats_url == "/api/traces/stats?from=2026-09-01&to=2026-09-30"
    assert traces_url.startswith("/api/traces?from=2026-09-01&to=2026-09-30")
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


def test_trace_seq_drops_a_stale_page(node: str) -> None:
    """A newer request wins: an in-flight 'Tải thêm' page resolves AFTER a
    reset and must not append — the rows are exactly the reset page."""
    out = _eval_dash(
        node,
        """(async () => {
          __jsonResponder = (url) => {
            const p = __deferred(); __pending.push({ url, p }); return p; };
          const p2 = loadTraces(false);          // in-flight "more" page
          const p3 = loadTraces(true);           // newer reset wins
          const reset = __pending.pop();
          reset.p.__res({ traces: [{ session_id: 's', turn_index: 0,
            title: 'A' }], total: 9 });
          await p3;
          const stale = __pending.pop();
          stale.p.__res({ traces: [{ session_id: 's2', turn_index: 0,
            title: 'B' }], total: 9 });
          await p2;
          return { titles: traceState.rows.map((r) => r.title),
                   total: traceState.total };
        })().then((v) => console.log(JSON.stringify(v)))""",
    )
    assert out["titles"] == ["A"]
    assert out["total"] == 9


def test_trace_row_escapes_and_deep_links(node: str) -> None:
    """Every untrusted field in a turn row is escaped; the row links to the
    trace page's #<session>/<turn> convention with the session id
    URL-encoded."""
    row = {
        "session_id": "s <x>", "title": '<img src=x onerror=alert(1)>',
        "turn_index": 0, "model": '<b>m</b>', "status": "<i>odd</i>",
        "started_at": "2026-09-20T10:00:00", "bot_id": 'a"><script>',
        "requests": 1, "tool_calls": 0, "total_tokens": 5,
        "cost_usd": 0.001, "latency_ms": 40,
    }
    html = _eval_dash(
        node,
        f"console.log(JSON.stringify(traceRow({json.dumps(row)})))",
    )
    assert '<img src=x' not in html and '<b>m</b>' not in html
    assert '&lt;img src=x onerror=alert(1)&gt;' in html
    assert '&lt;b&gt;m&lt;/b&gt;' in html
    assert 'data-href="../trace/index.html#s%20%3Cx%3E/0"' in html
    assert "a&quot;&gt;&lt;script&gt;" in html  # escaped in title attr


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


def test_trace_bot_cell_unions_ids_and_falls_back(node: str) -> None:
    """The Bot cell unions bot_id/session_dm/session_bots (deduped): a
    roster bot gets name + mini avatar + '+N'; a missing bot renders plain
    id text; none renders '—'."""
    out = _eval_dash(
        node,
        "globalThis.__roster = { coder: { id: 'coder', name: 'Coder',"
        " color: '#123', state: 'idle' } };\n"
        "console.log(JSON.stringify(["
        " traceBotCell({ bot_id: 'coder', session_bots: ['coder', 'ops'] }),"
        " traceBotCell({ bot_id: 'ghost' }),"
        " traceBotCell({})]))",
    )
    rostered, missing, empty = out
    assert "<ava:coder/>" in rostered and "Coder +1" in rostered
    assert 'title="coder, ops"' in rostered
    assert "ghost" in missing and "bot-id" in missing
    assert "<ava:" not in missing
    assert "—" in empty


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
            errText: document.querySelector('#trace-status').textContent,
            hint: document.querySelector('#trace-empty-hint').textContent }; };
          traceState.rows = [{ session_id: 's', turn_index: 0 }];
          traceState.error = ''; traceState.loading = false;
          renderTraceTable(); snap('rows');
          traceState.rows = []; traceState.error = 'boom';
          renderTraceTable(); snap('err');
          traceState.error = ''; renderTraceTable(); snap('empty');
          document.querySelector('#filter-model').value = 'glm';
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
    assert "Bộ lọc model/trạng thái đang bật" in seen["filtered"]["hint"]


def test_open_trace_row_navigates(node: str) -> None:
    """Clicking/Enter on a row navigates to its data-href deep link."""
    out = _eval_dash(
        node,
        "openTraceRow({ target: { closest: (s) =>"
        " s === 'tr[data-href]'"
        "   ? { dataset: { href: '../trace/index.html#s/0' } } : null } });\n"
        "console.log(JSON.stringify(location.href))",
    )
    assert out == "../trace/index.html#s/0"


def test_fill_filters_keep_selection_and_add_extra_statuses(node: str) -> None:
    """Refills keep the user's selection when still present; the status
    select always offers the four known statuses plus any extra the stats
    payload reports."""
    out = _eval_dash(
        node,
        "const sel = document.querySelector('#filter-model');\n"
        "sel.value = 'm2';\n"
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
    # A second fill without extras drops them again, statuses stay.
    assert out["sValsAfter"] == [
        "", "completed", "failed", "context_limit", "loop_limit"]


def test_formatters_vi_vn_and_edges(node: str) -> None:
    """vi-VN grouping/decimals everywhere; bad input renders '—' never a
    crash; relative time buckets degrade to an absolute date past 31 days."""
    out = _eval_dash(
        node,
        "const now = Date.now();\n"
        "console.log(JSON.stringify({"
        " ints: [fmtInt(1200000), fmtInt(null), fmtInt('x')],"
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
    assert out["ints"] == ["1.200.000", "—", "—"]
    # A negative number is still a finite number — printed as-is.
    assert out["durs"] == ["999 ms", "1 giây", "1,5 giây",
                           "1 ph 30 giây", "-1 ms", "—"]
    assert out["dt"][0] == "—"
    assert out["dt"][1] == "08/09" and out["dt"][2] == "—"
    assert out["rel"][0] == "vừa xong"
    assert out["rel"][1] == "5 phút trước"
    assert out["rel"][2] == "3 giờ trước"
    assert out["rel"][3] == "2 ngày trước"
    assert "thg" in out["rel"][4]      # absolute date past 31 days
    assert out["rel"][5] == "—"


def test_render_stats_paints_every_region(node: str) -> None:
    """One stats payload fans out to all six regions: stat cards, day
    chart, bot rows, model rows, the 24-column hour chart and both filter
    selects — the overview has no dead panels."""
    stats = {
        "totals": {"requests": 2, "tool_calls": 1, "prompt_tokens": 10,
                   "cached_tokens": 2, "completion_tokens": 4,
                   "total_tokens": 14, "cost_usd": 0.5,
                   "latency_ms_p50": 500, "latency_ms_p90": 900},
        "by_model": [{"model": "m", "requests": 2, "total_tokens": 14,
                      "cost_usd": 0.5, "latency_ms_p50": 500,
                      "last_started_at": "2026-09-20T09:00:00Z"}],
        "by_bot": [{"bot_id": "coder", "requests": 2, "tool_calls": 1,
                    "total_tokens": 14, "cost_usd": 0.5,
                    "last_started_at": "2026-09-20T09:00:00Z"}],
        "by_day": [{"day": "2026-09-20", "requests": 2, "cost_usd": 0.5}],
        "by_hour": [{"hour": "09", "requests": 2, "cost_usd": 0.5}],
        "by_status": [{"status": "completed", "requests": 2}],
        "models": ["m"],
    }
    out = _eval_dash(
        node,
        f"statsData = {json.dumps(stats)};\n"
        "renderStats(statsData);\n"
        "console.log(JSON.stringify({"
        " grid: document.querySelector('#stat-grid').innerHTML.length,"
        " day: document.querySelector('#chart-day').innerHTML,"
        " hour: document.querySelector('#chart-hour').innerHTML,"
        " bot: document.querySelector('#rows-bot').innerHTML,"
        " model: document.querySelector('#rows-model').innerHTML,"
        " mOpts: document.querySelector('#filter-model').options.length,"
        " sOpts: document.querySelector('#filter-status').options.length }))",
    )
    assert out["grid"] > 0
    assert 'class="day-chart"' in out["day"]
    assert 'class="hour-chart"' in out["hour"]
    assert out["hour"].count('class="hc-col"') == 24      # always 24 UTC cols
    assert "09:00–09:59" in out["hour"]
    assert "Coder" in out["bot"] or "coder" in out["bot"]
    assert '>m</span>' in out["model"] or '"m"' in out["model"]
    assert out["mOpts"] == 2                             # all-models + m
    assert out["sOpts"] == 5                             # all + 4 statuses
