"""Usage/Trace merge contract — dashboard controller + Trace renderer.

The old trace surface (12/12/1 session/turn pagers in dashboard-today.js,
collect_trace_pages dedupe paging, scroll-reveal deep links, separate
session CRUD) was not ported. The new page is list+detail inside the shared
shell: GET /api/traces?from&to&limit&offset&q&model&status paged 50-at-a-time
behind "Tải thêm", sessions grouped by session_id in first-seen order, turn
detail at GET /api/traces/<sid>/<turn>, deep links #<sid>/<turn> and
?s=<sid>&t=<turn>, and seq guards on both the list and detail requests.
These tests pin that contract. The page is a classic script ('use strict',
shared globals), so the harness evals it in Node with a stubbed global
surface, mirroring the old import-based eval style.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from test_team_frontend import _unit_factory_source, _unit_renderer_source

ROOT = Path(__file__).resolve().parents[1]
WEBUI = ROOT / "thyca" / "webui" / "ui"
DASH_JS = WEBUI / "dashboard" / "js" / "dashboard.js"
TRACE_JS = WEBUI / "trace" / "js" / "trace.js"
TRACE_HTML = WEBUI / "trace" / "index.html"

# Same stub surface as test_trace_score.py plus: deferreds, toast capture,
# window listener capture (hashchange), and a getJson responder boot can see.
STUBS = r"""
class El {
  constructor(tag = 'div') {
    this.tag = tag; this.value = ''; this.textContent = ''; this._html = '';
    this.hidden = false; this.disabled = false; this.dataset = {}; this.attrs = {};
    this.listeners = {}; this.children = []; this.options = []; this.className = '';
    this.scrollTop = 0; this.style = {};
  }
  hasAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attrs, name); }
  focus() {}
  set innerHTML(v) { this._html = String(v); }
  get innerHTML() { return this._html; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  addEventListener(t, f) { (this.listeners[t] ||= []).push(f); }
  querySelectorAll() { return []; }
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
  addEventListener() {},
};
const __urls = [];
globalThis.location = { hash: '', search: '', pathname: '/trace/index.html' };
globalThis.history = { replaceState: (_a, _b, u) => __urls.push(String(u)) };
globalThis.window = {};
const __winListeners = {};
globalThis.addEventListener = (t, f) => { (__winListeners[t] ||= []).push(f); };
globalThis.$ = (s, r = document) => r.querySelector(s);
globalThis.$$ = (s, r = document) => [...r.querySelectorAll(s)];
globalThis.icon = (n) => `<svg data-i="${n}"></svg>`;
globalThis.escapeHTML = (str) => String(str).replace(/[&<>"']/g, (ch) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
globalThis.__roster = { coder: { id: 'coder', name: 'Coder', color: '#123', state: 'idle' } };
globalThis.getBot = (id) => globalThis.__roster[id] || null;
globalThis.avatar = (b) => `<ava:${b.id}/>`;
globalThis.paletteStyle = (b) => `--c:${b.color || '#000'}`;
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


def _eval_trace(node: str, body: str, setup: str = "") -> object:
    """Run the renderer and dashboard controller in their test wrappers."""
    dash = _unit_factory_source(DASH_JS, DASH_JS.read_text(encoding="utf-8"))
    renderer = _unit_renderer_source(TRACE_JS, TRACE_JS.read_text(encoding="utf-8"))
    program = (
        STUBS + setup + "\n"
        + "const ctx = { root: __workspace, route: { page: 'dashboard', search: location.search, hash: location.hash }, $: globalThis.$, $$: globalThis.$$, shellReady: __shellReady, navigate(page, parts = {}) { this.route = { page, search: this.route.search, hash: this.route.hash, ...parts }; __routes.push({ ...this.route }); return globalThis.__controller?.onRoute(this.route); }, updateRoute(parts) { Object.assign(this.route, parts); __urls.push({ ...this.route }); }, returnTo(parts) { __returnCalls.push(parts); Object.assign(this.route, parts); __urls.push({ ...this.route }); return globalThis.__controller?.onRoute(this.route); }, highlightBot() {}, renderTeam() {}, isActive: () => true, onRoute: () => () => {} };\n"
        + "const __routes = []; const __returnCalls = [];\n"
        + renderer + "\n"
        + "globalThis.ThycaRouter = { register(_page, spec) { globalThis.__controller = spec.mount(ctx); } };\n"
        + "const __src = " + json.dumps(dash) + ";\n"
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
    """The root SPA loads the Trace renderer before Dashboard, where both
    the overview and turn detail now live."""
    html = (WEBUI / "chat" / "index.html").read_text(encoding="utf-8")
    order = [
        '<script src="../share/util.js">',
        '<script src="../share/data.js">',
        '<script src="js/app.js">',
        '<script src="../trace/js/trace.js">',
        '<script src="../dashboard/js/dashboard.js">',
    ]
    pos = [html.index(s) for s in order]
    assert pos == sorted(pos)
    renderer = TRACE_JS.read_text(encoding="utf-8")
    assert 'window.ThycaTraceDetail = Object.freeze({' in renderer
    assert "ThycaRouter.register" not in renderer and "getJson(" not in renderer
    for gone in ("session-pager", "journal-pager", "pager-button", "turn-list", "session-list-view"):
        assert gone not in renderer


def test_usage_markup_and_trace_renderer_contract() -> None:
    dashboard = DASH_JS.read_text(encoding="utf-8")
    renderer = TRACE_JS.read_text(encoding="utf-8")
    for needle in (
        'id="stat-grid"', 'data-preset="1"', 'data-preset="7"',
        'data-preset="30"', 'data-preset="all"', 'id="analysis-details"',
        'id="filter-q"', 'id="filter-model"', 'id="filter-status"',
        'id="trace-sessions"', 'id="trace-more"', 'id="turn-detail-view"',
        'id="detail-back"', 'id="detail-status"', 'id="detail-body"',
        '/api/traces/stats?', '/api/traces?', '/api/traces/${encodeURIComponent(sid)}',
    ):
        assert needle in dashboard, needle
    assert "FIRST_PAGE = 15" in dashboard and "MORE_PAGE = 50" in dashboard
    assert "window.ThycaTraceDetail.render(detailData)" in dashboard
    assert "window.ThycaTraceDetail = Object.freeze({" in renderer
    assert "ThycaRouter.register" not in renderer and "getJson(" not in renderer
    assert not re.search(r"^\s*(import|export)\s", renderer, re.M)


def test_route_from_url_rejects_unsafe_hash_parts(node: str) -> None:
    out = _eval_trace(
        node,
        "console.log(JSON.stringify(['#abc/3','#abc/0','#abc','#/3','#abc/x','#a/b/c','#s%20x/0','#abc/123456789'].map(hash => parseDetailRoute({page:'dashboard',search:'',hash}))))",
    )
    assert out[:2] == [{"sid": "abc", "turn": 3}, {"sid": "abc", "turn": 0}]
    assert all(value is None for value in out[2:])


def test_boot_loads_direct_detail_without_loading_overview(node: str) -> None:
    detail = {"session_id": "s-x", "turn_index": 0, "title": "Routed",
              "status": "completed", "messages": [], "model": "m",
              "started_at": "2026-09-20T10:00:00"}
    out = _eval_trace(
        node,
        "(async () => { await __releaseShell(); await new Promise(r => setTimeout(r, 0));\n"
        "return { calls: __jsonCalls, mode: viewMode, key: detailKey,\n"
        " body: document.querySelector('#detail-body').innerHTML, route: ctx.route };\n"
        "})().then(v => console.log(JSON.stringify(v)))",
        setup="location.hash = '#s-x/0';\n"
        "__jsonResponder = async () => (" + json.dumps(detail) + ");\n",
    )
    assert out["calls"] == ["/api/traces/s-x/0"]
    assert out["mode"] == "detail" and out["key"] == "s-x/0"
    assert "Routed" in out["body"]
    assert out["route"] == {"page": "dashboard", "search": "", "hash": "#s-x/0"}


def test_boot_without_route_loads_overview_only(node: str) -> None:
    out = _eval_trace(
        node,
        "(async () => { await __releaseShell(); await new Promise(r => setTimeout(r, 0));\n"
        "return { calls: __jsonCalls, mode: viewMode, urls: __urls };\n"
        "})().then(v => console.log(JSON.stringify(v)))",
        setup="__jsonResponder = async (url) => url.includes('/stats') ? {totals:{}} : {traces:[],total:0};\n",
    )
    assert len(out["calls"]) == 2
    assert out["calls"][0].startswith("/api/traces/stats?")
    assert out["calls"][1].startswith("/api/traces?")
    assert "limit=15" in out["calls"][1] and "offset=0" in out["calls"][1]
    assert out["mode"] == "overview" and out["urls"] == []


def test_load_traces_pages_50_and_accumulates(node: str) -> None:
    """"Tải thêm" asks limit=50&offset=<rows so far>; rows concat, total
    tracks the server, and the footer hides the button at the total."""
    rows = lambda base, n: [
        {"session_id": "s", "turn_index": base + i, "title": "T",
         "started_at": "2026-09-20T10:00:00", "status": "completed"}
        for i in range(n)]
    out = _eval_trace(
        node,
        """(async () => {
          __jsonResponder = async (url) => {
            const q = new URLSearchParams(url.split('?')[1]);
            const off = Number(q.get('offset')), lim = Number(q.get('limit'));
            const mk = (b, n) => Array.from({ length: n }, (_, i) =>
              ({ session_id: 's', turn_index: b + i, title: 'T',
                 started_at: '2026-09-20T10:00:00', status: 'completed' }));
            return { traces: mk(off, Math.min(lim, 70 - off)), total: 70 };
          };
          await loadTraces(true);
          const first = { rows: traceState.rows.length,
            more: document.querySelector('#trace-more').hidden,
            count: document.querySelector('#trace-count').textContent };
          await loadTraces(false);
          return { first,
            second: { rows: traceState.rows.length,
              more: document.querySelector('#trace-more').hidden,
              count: document.querySelector('#trace-count').textContent },
            urls: __jsonCalls.slice(-2) };
        })().then((v) => console.log(JSON.stringify(v)))""",
    )
    assert out["first"]["rows"] == 15
    assert out["first"]["more"] is False
    assert out["first"]["count"] == "Hiển thị 15 trong 70 lượt"
    assert out["second"]["rows"] == 65
    assert out["second"]["more"] is False
    assert out["second"]["count"] == "Hiển thị 65 trong 70 lượt"
    assert "limit=15&offset=0" in out["urls"][0]
    assert "limit=50&offset=15" in out["urls"][1]


def test_load_traces_error_keeps_rows_and_shows_status(node: str) -> None:
    """A midway failure is an explicit error line — rows already loaded
    stay; "Tải thêm" hides while the error stands."""
    out = _eval_trace(
        node,
        """(async () => {
          __jsonResponder = async (url) => {
            const q = new URLSearchParams(url.split('?')[1]);
            if (Number(q.get('offset')) >= 65) throw new Error('backend died');
            return { traces: Array.from({ length: Number(q.get('limit')) }, (_, i) =>
              ({ session_id: 's', turn_index: i })), total: 80 };
          };
          await loadTraces(true);
          await loadTraces(false);
          await loadTraces(false);
          return { error: traceState.error, rows: traceState.rows.length,
            status: document.querySelector('#trace-status-text').textContent,
            statusHidden: document.querySelector('#trace-status').hidden,
            more: document.querySelector('#trace-more').hidden };
        })().then((v) => console.log(JSON.stringify(v)))""",
    )
    assert out["error"] == "backend died"
    assert out["rows"] == 65                       # partial pages kept
    assert "Không tải được danh sách lượt" in out["status"]
    assert out["statusHidden"] is False
    assert out["more"] is True                     # error hides the button


def test_filters_ride_the_query_and_q_trims(node: str) -> None:
    """Every filter lands on /api/traces — from/to/q/model/status; the
    search box trims whitespace; empty filters send nothing."""
    out = _eval_trace(
        node,
        """(async () => {
          const ids = ['range-from', 'range-to', 'filter-model', 'filter-status', 'filter-q'];
          const v = { 'range-from': '2026-09-01', 'range-to': '2026-09-30',
            'filter-model': 'glm-5', 'filter-status': 'failed', 'filter-q': '  tìm  ' };
          ids.forEach(id => { document.querySelector('#' + id).value = v[id]; });
          rangeFrom = v['range-from']; rangeTo = v['range-to'];
          syncListFilters();
          const before = __jsonCalls.length;
          await loadTraces(true);
          return { url: __jsonCalls[before], filters: { ...listFilters } };
        })().then((v) => console.log(JSON.stringify(v)))""",
    )
    q = out["url"]
    assert "from=2026-09-01" in q and "to=2026-09-30" in q
    assert "q=t%C3%ACm" in q or "q=t%C3%ACm%20" in q.replace("+", "%20")
    assert "model=glm-5" in q and "status=failed" in q
    assert out["filters"]["q"] == "tìm"


def test_reload_list_rejects_an_inverted_range(node: str) -> None:
    """Từ > Đến in the filter bar is a toast — the list is not refetched."""
    out = _eval_trace(
        node,
        """(async () => {
          document.querySelector('#range-from').value = '2026-09-22';
          document.querySelector('#range-to').value = '2026-09-20';
          const before = __jsonCalls.length;
          applyRange();
          return { calls: __jsonCalls.length - before, toasts: __toasts };
        })().then((v) => console.log(JSON.stringify(v)))""",
    )
    assert out["calls"] == 0
    assert out["toasts"] == ["Ngày bắt đầu phải trước hoặc bằng ngày kết thúc."]


def test_stale_list_response_is_dropped(node: str) -> None:
    """tr.request guard: a newer query wins — the in-flight older page
    resolves into the void, rows are exactly the newer result."""
    out = _eval_trace(
        node,
        """(async () => {
          __jsonResponder = (url) => {
            const p = __deferred(); __pending.push({ url, p }); return p; };
          const pOld = loadTraces(false);
          listFilters.q = 'new';
          const pNew = loadTraces(true);
          __pending[1].p.__res({ traces: [{ session_id: 's', turn_index: 0,
            title: 'new' }], total: 1 });
          await pNew;
          __pending[0].p.__res({ traces: [{ session_id: 'x', turn_index: 0,
            title: 'old' }], total: 9 });
          await pOld;
          return { rows: traceState.rows.map((r) => r.title), total: traceState.total };
        })().then((v) => console.log(JSON.stringify(v)))""",
    )
    assert out["rows"] == ["new"]
    assert out["total"] == 1


def test_open_turn_routes_within_dashboard_and_fetches_only_detail(node: str) -> None:
    detail = {"session_id": "s-x", "turn_index": 2, "title": "Deep",
              "status": "failed", "messages": [], "model": "m",
              "started_at": "2026-09-20T10:00:00", "error": {"code": "Boom", "message": "kaput"}}
    out = _eval_trace(
        node,
        f"(async () => {{ await __releaseShell(); await new Promise(r => setTimeout(r, 0));\n"
        f"__jsonResponder = async () => ({json.dumps(detail)});\n"
        "const before = __jsonCalls.length;\n"
        "await ctx.navigate('dashboard', { search: pageQuery(), hash: '#s-x/2' });\n"
        "await new Promise(r => setTimeout(r, 0));\n"
        "return { calls: __jsonCalls.slice(before), mode: viewMode, key: detailKey,\n"
        " route: ctx.route, routes: __routes, body: document.querySelector('#detail-body').innerHTML };\n"
        "})().then(v => console.log(JSON.stringify(v)))",
    )
    assert out["calls"] == ["/api/traces/s-x/2"]
    assert out["mode"] == "detail" and out["key"] == "s-x/2"
    assert out["route"]["page"] == "dashboard" and out["route"]["hash"] == "#s-x/2"
    assert out["routes"][-1]["page"] == "dashboard"
    assert "Deep" in out["body"] and "kaput" in out["body"] and "Boom" in out["body"]


def test_detail_404_keeps_route_and_shows_independent_error(node: str) -> None:
    out = _eval_trace(
        node,
        "(async () => { await __releaseShell(); await new Promise(r => setTimeout(r, 0));\n"
        "__jsonResponder = async () => { throw new Error('gone'); };\n"
        "await ctx.navigate('dashboard', { search: pageQuery(), hash: '#s/0' });\n"
        "await new Promise(r => setTimeout(r, 0));\n"
        "return { err: detailError, key: detailKey, route: ctx.route,\n"
        " status: document.querySelector('#detail-status-text').textContent,\n"
        " body: document.querySelector('#detail-body').innerHTML };\n"
        "})().then(v => console.log(JSON.stringify(v)))",
    )
    assert out["err"] == "gone" and out["key"] == "s/0"
    assert out["route"]["page"] == "dashboard" and out["route"]["hash"] == "#s/0"
    assert "Không đọc được lượt s/0" in out["status"]
    assert "Không mở được trace này" in out["body"]


def test_detail_seq_drops_a_stale_turn(node: str) -> None:
    """Opening turn B while turn A is in flight: A's late payload never
    paints — the detail is B, exactly once."""
    out = _eval_trace(
        node,
        """(async () => {
          __jsonResponder = (url) => {
            const p = __deferred(); __pending.push({ url, p }); return p; };
          const pa = loadDetail('a', 0);
          const pb = loadDetail('b', 1);
          __pending[1].p.__res({ session_id: 'b', turn_index: 1,
            title: 'B', status: 'completed', messages: [] });
          await pb;
          __pending[0].p.__res({ session_id: 'a', turn_index: 0,
            title: 'A', status: 'completed', messages: [] });
          await pa;
          return { key: detailKey, title: detailData && detailData.title };
        })().then((v) => console.log(JSON.stringify(v)))""",
    )
    assert out["key"] == "b/1"
    assert out["title"] == "B"


def test_explicit_back_returns_to_overview_and_restores_scroll(node: str) -> None:
    out = _eval_trace(
        node,
        """(async () => {
          await __releaseShell(); await new Promise(r => setTimeout(r, 0));
          __jsonResponder = async () => ({ session_id: 's', turn_index: 0, title: 'T', status: 'completed', messages: [] });
          document.querySelector('#usage-scroll').scrollTop = 42;
          await ctx.navigate('dashboard', { search: pageQuery(), hash: '#s/0' });
          await new Promise(r => setTimeout(r, 0));
          const reqs = detailSeq;
          document.querySelector('#usage-scroll').scrollTop = 0;
          returnFromDetail();
          await new Promise(r => setTimeout(r, 0));
          return { mode: viewMode, detail: detailData, key: detailKey,
            bumped: detailSeq > reqs, returnCalls: __returnCalls, route: ctx.route,
            scroll: document.querySelector('#usage-scroll').scrollTop };
        })().then(v => console.log(JSON.stringify(v)))""",
    )
    assert out["mode"] == "overview" and out["detail"] is None and out["key"] == ""
    assert out["bumped"] is True and out["returnCalls"]
    assert out["route"]["page"] == "dashboard" and out["route"]["hash"] == ""
    assert out["scroll"] == 42


def test_turn_click_delegation_and_hashchange(node: str) -> None:
    """A grouped turn click and route changes keep detail in Dashboard;
    clearing the hash returns to the Usage overview."""
    detail = {"session_id": "s", "turn_index": 1, "title": "X",
              "status": "completed", "messages": []}
    out = _eval_trace(
        node,
        f"(async () => {{ await __releaseShell(); await new Promise(r => setTimeout(r, 0));\n"
        f"__jsonResponder = async () => ({json.dumps(detail)});\n"
        "const click = document.querySelector('#trace-sessions').listeners.click[0];\n"
        "click({ target: { closest: (s) => s === 'button[data-session][data-turn]'\n"
        "  ? { dataset: { session: 's', turn: '1' } } : null } });\n"
        "await new Promise(r => setTimeout(r, 0));\n"
        "const afterClick = { key: detailKey, mode: viewMode };\n"
        "ctx.route.hash = '#z/3';\n"
        "await __controller.onRoute({ page: 'dashboard', search: ctx.route.search, hash: '#z/3' });\n"
        "const afterHash = { key: detailKey };\n"
        "ctx.route.hash = '';\n"
        "await __controller.onRoute({ page: 'dashboard', search: ctx.route.search, hash: '' });\n"
        "return { afterClick, afterHash, mode: viewMode };\n"
        "})().then((v) => console.log(JSON.stringify(v)))",
    )
    assert out["afterClick"] == {"key": "s/1", "mode": "detail"}
    assert out["afterHash"]["key"] == "z/3"
    assert out["mode"] == "overview"


def test_reload_button_re_reads_the_open_turn(node: str) -> None:
    """In detail mode the reload button re-fetches the SAME turn without
    touching the URL; on the picker it re-queries the list."""
    detail = {"session_id": "s", "turn_index": 0, "title": "T",
              "status": "completed", "messages": []}
    out = _eval_trace(
        node,
        f"(async () => {{ await __releaseShell(); await new Promise(r => setTimeout(r, 0));\n"
        f"__jsonResponder = async () => ({json.dumps(detail)});\n"
        "await ctx.navigate('dashboard', { search: pageQuery(), hash: '#s/0' });\n"
        "await new Promise(r => setTimeout(r, 0));\n"
        "const urlsBefore = __urls.length;\n"
        "const callsBefore = __jsonCalls.length;\n"
        "document.querySelector('#detail-refresh').listeners.click[0]();\n"
        "await new Promise(r => setTimeout(r, 0));\n"
        "return { newCalls: __jsonCalls.slice(callsBefore),\n"
        " newUrls: __urls.length - urlsBefore };\n"
        "})().then((v) => console.log(JSON.stringify(v)))",
    )
    assert out["newCalls"] == ["/api/traces/s/0"]
    assert out["newUrls"] == 0                       # push: false


def test_trace_list_is_flat_and_turn_rows_keep_context(node: str) -> None:
    """The Trace view has one flat row per API turn; it does not infer bot
    ownership from session membership or aggregate failures by session."""
    rows = [
        {"session_id": "g", "turn_index": 1, "title": "T1",
         "started_at": "2026-09-20T10:00:00", "status": "completed",
         "session_kind": "group", "bot_id": "coder",
         "session_bots": ["session-owner"],
         "bot_breakdown": [{"bot_id": "coder"}, {"bot_id": "ops"}],
         "model": "m", "requests": 2, "tool_calls": 1,
         "total_tokens": 10, "cost_usd": 0.01, "latency_ms": 40},
        {"session_id": "g", "turn_index": 0, "title": "T0",
         "started_at": "2026-09-20T09:00:00", "status": "failed",
         "session_kind": "dm", "session_dm": "dm-owner",
         "model": "m", "total_tokens": 5, "cost_usd": None, "latency_ms": 20},
    ]
    html = _eval_trace(
        node,
        f"traceState.rows = {json.dumps(rows)}; traceState.total = 2; traceState.loading = false;\n"
        "renderTraceTable();\n"
        "console.log(JSON.stringify(document.querySelector('#trace-sessions').innerHTML))",
    )
    assert html.count('<li><button') == 2
    assert 'data-session="g"' in html and 'data-turn="1"' in html
    assert "<b>Coder +1</b>" in html
    assert "Chat nhóm" in html and "Chat riêng" in html
    assert "Hoàn tất" in html and "Thất bại" in html
    assert "session-owner" not in html and "dm-owner" not in html
    assert 'class="paper-card tl-session"' not in html and "1 lượt lỗi" not in html
    assert "$0.0100" in html and ">—<" in html


def test_detail_head_renders_turn_meta_and_error(node: str) -> None:
    """The detail head: session mono, time range, bot seals + names, model,
    request/round/tool counts, the vào/cache/ra token split, cost,
    duration, and the recorded error block."""
    detail = {
        "session_id": "sess", "turn_index": 3, "title": "Đào sâu",
        "status": "failed", "model": "glm",
        "started_at": "2026-09-20T10:00:00", "ended_at": "2026-09-20T10:02:00",
        "bot_id": "coder",
        "bot_breakdown": [{"bot_id": "coder", "requests": 2,
                           "total_tokens": 30},
                          {"bot_id": "ops", "requests": 1,
                           "total_tokens": 10}],
        "requests": 3, "rounds": 2, "tool_calls": 4,
        "prompt_tokens": 30, "cached_tokens": 5, "completion_tokens": 10,
        "total_tokens": 40, "cost_usd": 0.02, "latency_ms": 120000,
        "trace_id": "tr-1", "work_id": "w-9",
        "error": {"code": "Boom", "message": "kaput"},
        "messages": [],
    }
    html = _eval_trace(
        node,
        f"console.log(JSON.stringify(detailHeadHtml({json.dumps(detail)})))",
    )
    assert "Lượt #3" in html and "Lỗi" in html
    assert ">Đào sâu</h2>" in html
    assert "sess" in html
    assert "Coder" in html and "ops" in html        # seal chips + id fallback
    assert "2 req" in html and "30 tok" in html     # breakdown bits
    assert "glm" in html
    assert "3 request · 2 vòng · 4 tool" in html
    assert "vào 30 · cache 5 · ra 10 · tổng 40" in html
    assert "$0.0200" in html
    assert "2 ph 0 giây" in html                    # fmtDur minutes
    assert "tr-1" in html and "w-9" in html
    assert "Boom" in html and "kaput" in html


def test_full_detail_bot_context_keeps_primary_and_breakdown(node: str) -> None:
    """The unchanged full-turn detail header retains its primary bot and
    additional breakdown entries; overview attribution is tested separately."""
    out = _eval_trace(
        node,
        "console.log(JSON.stringify(["
        " turnBotIds({ bot_id: 'primary', session_bots: ['member'],"
        "  bot_breakdown: [{ bot_id: 'a' }, { bot_id: 'b' }] }),"
        " turnBotIds({ bot_id: 'direct', session_dm: 'dm-owner' }),"
        " turnBotIds({ session_dm: 'dm-owner', session_bots: ['member'] })]))",
    )
    assert out == [["primary", "a", "b"], ["direct"], []]


def test_status_labels_and_formatters(node: str) -> None:
    """The page's own label set (Thành công/Lỗi/Hết ngữ cảnh/Chạm giới
    hạn) plus the shared vi-VN formatters and safeDecode edge cases."""
    out = _eval_trace(
        node,
        "console.log(JSON.stringify({"
        " chips: [statusChip('completed'), statusChip('failed'),"
        "  statusChip('context_limit'), statusChip('loop_limit'),"
        "  statusChip('weird')],"
        " t: [fmtTime('2026-09-20T10:30:00'), fmtTime('nope'), fmtTime()],"
        " dec: [safeDecode('a%20b'), safeDecode('%ZZ'), safeDecode('x')],"
        " j: [safeJson({ a: 1 }), safeJson('raw')] }))",
    )
    done, fail, ctx, loop, odd = out["chips"]
    assert "Thành công" in done and "s-ok" in done
    assert "Lỗi" in fail and "s-error" in fail
    assert "Hết ngữ cảnh" in ctx and "s-warn" in ctx
    assert "Chạm giới hạn" in loop and "s-warn" in loop
    assert "weird" in odd and 'class="tl-chip "' in odd
    assert out["t"][0].strip() != "" and out["t"][1] == "" and out["t"][2] == ""
    assert out["dec"] == ["a b", "%ZZ", "x"]        # bad escapes pass through
    assert '"a": 1' in out["j"][0] and out["j"][1] == '"raw"'


def test_fill_model_options_collects_from_loaded_rows(node: str) -> None:
    """Model options come from models actually seen in loaded rows,
    sorted; an active filter that left the window stays as an extra."""
    out = _eval_trace(
        node,
        "listFilters.model = 'zzz'; fillModelFilter(['glm', 'abc']);\n"
        "const sel = document.querySelector('#filter-model');\n"
        "console.log(JSON.stringify({ vals: sel.options.map(o => o.value),"
        " cur: sel.value }))",
    )
    assert out["vals"] == ["", "glm", "abc", "zzz"]
    assert out["cur"] == "zzz"


def test_filter_change_handlers_are_wired(node: str) -> None:
    """f-from/f-to/f-model/f-status all trigger a refilter on change; the
    form's submit is preventDefault'd into the same path."""
    # Structural pin: the handler list + debounced search input live in
    # wirePage — a wrong selector or missing binding shows up here.
    src = DASH_JS.read_text(encoding="utf-8")
    assert "$('#filter-model').onchange = $('#filter-status').onchange = applyListFilters" in src
    assert "$('#filter-q').addEventListener('input'" in src
    assert "qTimer = setTimeout(() =>" in src
    assert "ensureOverviewData(false)" in src
    assert "function syncListFilters()" in src
    assert "writeCurrentRoute('')" in src
