"""Trace page contract — thyca/webui/ui/trace/js/trace.js + index.html.

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
from test_team_frontend import _unit_factory_source

ROOT = Path(__file__).resolve().parents[1]
WEBUI = ROOT / "thyca" / "webui" / "ui"
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
    """Eval the trace factory body with stubs and optional pre-boot setup."""
    src = _unit_factory_source(TRACE_JS, TRACE_JS.read_text(encoding="utf-8"))
    program = (
        STUBS
        + setup + "\n"
        + "\nconst ctx = { root: __workspace, route: { page: 'trace', search: location.search, hash: location.hash }, $: globalThis.$, $$: globalThis.$$, shellReady: __shellReady, navigate(page, parts = {}) { this.route = { page, search: '', hash: '', ...parts }; }, updateRoute(parts) { Object.assign(this.route, parts); __urls.push({ ...this.route }); }, highlightBot() {}, renderTeam() {}, isActive: () => true, onRoute: () => () => {} };\n"
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
    """index.html keeps the shared script order (markdown.js is the only
    module), emitShell('trace') before the bind-time scripts, one screen."""
    html = TRACE_HTML.read_text(encoding="utf-8")
    order = [
        '<script src="../share/util.js">',
        '<script type="module" src="../share/markdown.js"></script>',
        '<script src="../share/data.js">',
        '<script src="../share/shell.js">',
        "<script>emitShell('trace');</script>",
        '<script src="../share/avatars/avatars.js">',
        '<script src="../share/sidebar.js">',
        '<script src="../share/avatars/studio.js">',
        '<script src="../share/shell-app.js">',
        '<script src="js/trace.js">',
    ]
    pos = [html.index(s) for s in order]
    assert pos == sorted(pos), "shared shell scripts must load in order"
    for gone in ("session-pager", "journal-pager", "pager-button",
                 "turn-list", "session-list-view"):
        assert gone not in html, gone


def test_page_markup_covers_list_and_detail() -> None:
    """PAGE_HTML carries the filter bar, session list + 'Tải thêm' footer,
    the hidden detail section with its back button and both status lines —
    plus the API + deep-link pins in comments."""
    src = TRACE_JS.read_text(encoding="utf-8")
    for needle in (
        'id="trace-reload"', 'id="trace-scroll"', 'id="trace-list-view"',
        'id="trace-status"', 'id="trace-filters"',
        'id="f-from"', 'id="f-to"', 'id="f-model"', 'id="f-status"',
        'id="f-q"', 'id="trace-sessions"', 'id="trace-count"',
        'id="trace-more"', 'id="trace-detail-view"', 'id="trace-back"',
        'id="trace-detail-status"', 'id="trace-detail-body"',
        'value="completed"', 'value="failed"',
        'value="context_limit"', 'value="loop_limit"',
        '/api/traces?', '/api/traces/',
        '#<session_id>/<turn_index>', '?s=<session_id>&t=<turn_index>',
    ):
        assert needle in src, needle
    assert 'hidden>' in src                       # detail view starts hidden
    assert not re.search(r"^\s*(import|export)\s", src, re.M)  # classic script
    # Page size is the 50-row "Tải thêm" contract, not the old 12/12/1.
    assert "TRACE_PAGE_SIZE = 50" in src


def test_route_from_url_parses_both_forms(node: str) -> None:
    """#<sid>/<turn> (encoded sid allowed) and ?s=<sid>&t=<turn> both
    resolve; malformed halves return null — never a bogus fetch."""
    out = _eval_trace(
        node,
        """(() => {
          const probe = (hash, search) => {
            location.hash = hash; location.search = search; ctx.route.hash = hash; ctx.route.search = search;
            return routeFromUrl(); };
          console.log(JSON.stringify([
            probe('#abc/3', ''), probe('#s%20x/0', ''), probe('#abc', ''),
            probe('#/3', ''), probe('#abc/x', ''), probe('#a/b/c', ''),
            probe('', '?s=abc&t=4'), probe('', '?s=abc'),
            probe('', '?s=abc&t=x'), probe('', '?t=4'), probe('', ''),
            probe('#h/1', '?s=abc&t=4'),
          ]));
        })()""",
    )
    assert out[0] == {"sid": "abc", "turn": 3}
    assert out[1] == {"sid": "s x", "turn": 0}     # sid decoded, turn kept
    assert out[2] is None and out[3] is None        # no slash / empty sid
    assert out[4] is None                           # turn must be digits
    assert out[5] is None                           # 'b/c' is not a turn
    assert out[6] == {"sid": "abc", "turn": 4}
    assert out[7] is None and out[8] is None and out[9] is None
    assert out[10] is None
    assert out[11] == {"sid": "h", "turn": 1}       # hash wins over query


def test_boot_loads_list_and_follows_hash_route(node: str) -> None:
    """boot() queries the list AND opens the routed turn: one
    /api/traces?limit=50&offset=0 plus /api/traces/<sid>/<turn>, mode
    switches to detail, the URL hash stays canonical."""
    detail = {"session_id": "s x", "turn_index": 0, "title": "Routed",
              "status": "completed", "messages": [],
              "model": "m", "started_at": "2026-09-20T10:00:00"}
    out = _eval_trace(
        node,
        "(async () => {\n"
        "await __releaseShell();\n"
        "await new Promise(r => setTimeout(r, 0));\n"
        "return { calls: __jsonCalls, mode: tr.mode, key: tr.detailKey,\n"
        " body: document.querySelector('#trace-detail-body').innerHTML,\n"
        " route: ctx.route, urls: __urls };\n"
        "})().then((v) => console.log(JSON.stringify(v)))",
        setup="location.hash = '#s%20x/0';\n"
        "__jsonResponder = async (url) => url.includes('traces/s')"
        f" ? {json.dumps(detail)} : {{ traces: [], total: 0 }};\n",
    )
    list_call = next(u for u in out["calls"] if "?" in u)
    detail_call = next(u for u in out["calls"] if "traces/s" in u)
    assert list_call.startswith("/api/traces?")
    assert "limit=50" in list_call and "offset=0" in list_call
    assert detail_call == "/api/traces/s%20x/0"
    assert out["mode"] == "detail" and out["key"] == "s x/0"
    assert "Routed" in out["body"]
    assert out["route"] == {"page": "trace", "search": "", "hash": "#s%20x/0"}


def test_boot_without_route_stays_on_the_picker(node: str) -> None:
    """No hash/query -> only the list fetch, mode stays 'list', no detail
    request and no URL rewrite."""
    out = _eval_trace(
        node,
        "(async () => {\n"
        "await __releaseShell();\n"
        "await new Promise(r => setTimeout(r, 0));\n"
        "return { calls: __jsonCalls, mode: tr.mode, urls: __urls,\n"
        " body: document.querySelector('#trace-detail-body').innerHTML };\n"
        "})().then((v) => console.log(JSON.stringify(v)))",
        setup="__jsonResponder = async () => ({ traces: [], total: 0 });\n",
    )
    assert len(out["calls"]) == 1
    assert out["calls"][0].startswith("/api/traces?")
    assert out["mode"] == "list" and out["urls"] == []


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
          const first = { rows: tr.rows.length,
            more: document.querySelector('#trace-more').hidden,
            count: document.querySelector('#trace-count').textContent };
          await loadTraces(false);
          return { first,
            second: { rows: tr.rows.length,
              more: document.querySelector('#trace-more').hidden,
              count: document.querySelector('#trace-count').textContent },
            urls: __jsonCalls.slice(-2) };
        })().then((v) => console.log(JSON.stringify(v)))""",
    )
    assert out["first"]["rows"] == 50
    assert out["first"]["more"] is False  # 50 < 70 — still more
    assert out["first"]["count"] == "Hiển thị 50 trong 70 lượt"
    assert out["second"]["rows"] == 70
    assert out["second"]["more"] is True  # rows == total
    assert out["second"]["count"] == "Hiển thị 70 trong 70 lượt"
    assert "limit=50&offset=0" in out["urls"][0]
    assert "limit=50&offset=50" in out["urls"][1]


def test_load_traces_error_keeps_rows_and_shows_status(node: str) -> None:
    """A midway failure is an explicit error line — rows already loaded
    stay; "Tải thêm" hides while the error stands."""
    out = _eval_trace(
        node,
        """(async () => {
          __jsonResponder = async (url) => {
            const q = new URLSearchParams(url.split('?')[1]);
            if (Number(q.get('offset')) >= 50) throw new Error('backend died');
            return { traces: Array.from({ length: 50 }, (_, i) =>
              ({ session_id: 's', turn_index: i })), total: 80 };
          };
          await loadTraces(true);
          await loadTraces(false);
          return { error: tr.error, rows: tr.rows.length,
            status: document.querySelector('#trace-status').textContent,
            statusHidden: document.querySelector('#trace-status').hidden,
            more: document.querySelector('#trace-more').hidden };
        })().then((v) => console.log(JSON.stringify(v)))""",
    )
    assert out["error"] == "backend died"
    assert out["rows"] == 50                       # partial page kept
    assert "Không tải được danh sách lượt" in out["status"]
    assert out["statusHidden"] is False
    assert out["more"] is True                     # error hides the button


def test_filters_ride_the_query_and_q_trims(node: str) -> None:
    """Every filter lands on /api/traces — from/to/q/model/status; the
    search box trims whitespace; empty filters send nothing."""
    out = _eval_trace(
        node,
        """(async () => {
          const ids = ['f-from', 'f-to', 'f-model', 'f-status', 'f-q'];
          const v = { 'f-from': '2026-09-01', 'f-to': '2026-09-30',
            'f-model': 'glm-5', 'f-status': 'failed', 'f-q': '  tìm  ' };
          ids.forEach(id => { document.querySelector('#' + id).value = v[id]; });
          syncFilters();
          const before = __jsonCalls.length;
          await loadTraces(true);
          return { url: __jsonCalls[before], filters: { ...tr.filters } };
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
          tr.filters.from = '2026-09-22'; tr.filters.to = '2026-09-20';
          const before = __jsonCalls.length;
          reloadList();
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
          const pNew = loadTraces(true);
          __pending[1].p.__res({ traces: [{ session_id: 's', turn_index: 0,
            title: 'new' }], total: 1 });
          await pNew;
          __pending[0].p.__res({ traces: [{ session_id: 'x', turn_index: 0,
            title: 'old' }], total: 9 });
          await pOld;
          return { rows: tr.rows.map((r) => r.title), total: tr.total };
        })().then((v) => console.log(JSON.stringify(v)))""",
    )
    assert out["rows"] == ["new"]
    assert out["total"] == 1


def test_open_turn_fetches_detail_and_rewrites_the_url(node: str) -> None:
    """openTurn(sid, turn): detail endpoint is encodeURIComponent'd, mode
    flips to detail, and history.replaceState writes #<sid>/<turn> — no
    push, no reload."""
    detail = {"session_id": "s x", "turn_index": 2, "title": "Deep",
              "status": "failed", "messages": [],
              "model": "m", "started_at": "2026-09-20T10:00:00",
              "error": {"code": "Boom", "message": "kaput"}}
    out = _eval_trace(
        node,
        f"(async () => {{\n"
        f"__jsonResponder = async () => ({json.dumps(detail)});\n"
        "await openTurn('s x', 2);\n"
        "return { calls: __jsonCalls.slice(-1), mode: tr.mode,\n"
        " key: tr.detailKey, urls: __urls,\n"
        " body: document.querySelector('#trace-detail-body').innerHTML };\n"
        "})().then((v) => console.log(JSON.stringify(v)))",
    )
    assert out["calls"] == ["/api/traces/s%20x/2"]
    assert out["mode"] == "detail" and out["key"] == "s x/2"
    assert out["urls"] == [{"page": "trace", "search": "", "hash": "#s%20x/2"}]
    assert "Deep" in out["body"] and "kaput" in out["body"]
    assert "Boom" in out["body"]


def test_open_turn_error_shows_note_but_keeps_the_url(node: str) -> None:
    """A failed detail fetch leaves the deep link in the URL (reload can
    retry), shows the scoped error + the empty note, keeps detailKey."""
    out = _eval_trace(
        node,
        "(async () => {\n"
        "__jsonResponder = async () => { throw new Error('gone'); };\n"
        "await openTurn('s', 0);\n"
        "return { err: tr.detailError, key: tr.detailKey, urls: __urls,\n"
        " status: document.querySelector('#trace-detail-status').textContent,\n"
        " body: document.querySelector('#trace-detail-body').innerHTML };\n"
        "})().then((v) => console.log(JSON.stringify(v)))",
    )
    assert out["err"] == "gone"
    assert out["key"] == "s/0"
    assert out["urls"] == [{"page": "trace", "search": "", "hash": "#s/0"}]
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
          const pa = openTurn('a', 0);
          const pb = openTurn('b', 1);
          __pending[1].p.__res({ session_id: 'b', turn_index: 1,
            title: 'B', status: 'completed', messages: [] });
          await pb;
          __pending[0].p.__res({ session_id: 'a', turn_index: 0,
            title: 'A', status: 'completed', messages: [] });
          await pa;
          return { key: tr.detailKey,
            title: tr.detail && tr.detail.title };
        })().then((v) => console.log(JSON.stringify(v)))""",
    )
    assert out["key"] == "b/1"
    assert out["title"] == "B"


def test_back_to_list_restores_mode_scroll_and_url(node: str) -> None:
    """Back returns to the picker: mode 'list', detail cleared, the saved
    scroll restored, the URL back to the bare page — and any in-flight
    detail fetch is orphaned via detailRequest."""
    out = _eval_trace(
        node,
        """(async () => {
          __jsonResponder = async () => ({ session_id: 's', turn_index: 0,
            title: 'T', status: 'completed', messages: [] });
          document.querySelector('#trace-scroll').scrollTop = 42;
          await openTurn('s', 0);
          const reqs = tr.detailRequest;
          document.querySelector('#trace-scroll').scrollTop = 0;
          backToList();
          return { mode: tr.mode, detail: tr.detail, key: tr.detailKey,
            bumped: tr.detailRequest > reqs, urls: __urls,
            scroll: document.querySelector('#trace-scroll').scrollTop };
        })().then((v) => console.log(JSON.stringify(v)))""",
    )
    assert out["mode"] == "list"
    assert out["detail"] is None and out["key"] == ""
    assert out["bumped"] is True
    assert out["urls"][-1] == {"page": "trace", "search": "", "hash": ""}   # SPA route hash cleared
    assert out["scroll"] == 42                       # list scroll restored


def test_turn_click_delegation_and_hashchange(node: str) -> None:
    """wirePage: a click on a .tl-turn inside #trace-sessions opens that
    turn; hashchange reopens the routed turn or drops back to the list."""
    detail = {"session_id": "s", "turn_index": 1, "title": "X",
              "status": "completed", "messages": []}
    out = _eval_trace(
        node,
        f"(async () => {{\n"
        f"__jsonResponder = async () => ({json.dumps(detail)});\n"
        "const click = document.querySelector('#trace-sessions').listeners.click[0];\n"
        "click({ target: { closest: (s) => s === '.tl-turn'\n"
        "  ? { dataset: { session: 's', turn: '1' } } : null } });\n"
        "await new Promise(r => setTimeout(r, 0));\n"
        "const afterClick = { key: tr.detailKey, mode: tr.mode };\n"
        "location.hash = '#z/3'; ctx.route.hash = '#z/3';\n"
        "await __controller.onRoute({ page: 'trace', search: '', hash: '#z/3' });\n"
        "const afterHash = { key: tr.detailKey };\n"
        "location.hash = ''; ctx.route.hash = '';\n"
        "await __controller.onRoute({ page: 'trace', search: '', hash: '' });\n"
        "return { afterClick, afterHash, mode: tr.mode };\n"
        "})().then((v) => console.log(JSON.stringify(v)))",
    )
    assert out["afterClick"] == {"key": "s/1", "mode": "detail"}
    assert out["afterHash"]["key"] == "z/3"
    assert out["mode"] == "list"


def test_reload_button_re_reads_the_open_turn(node: str) -> None:
    """In detail mode the reload button re-fetches the SAME turn without
    touching the URL; on the picker it re-queries the list."""
    detail = {"session_id": "s", "turn_index": 0, "title": "T",
              "status": "completed", "messages": []}
    out = _eval_trace(
        node,
        f"(async () => {{\n"
        f"__jsonResponder = async () => ({json.dumps(detail)});\n"
        "await openTurn('s', 0);\n"
        "const urlsBefore = __urls.length;\n"
        "const callsBefore = __jsonCalls.length;\n"
        "document.querySelector('#trace-reload').listeners.click[0]();\n"
        "await new Promise(r => setTimeout(r, 0));\n"
        "return { newCalls: __jsonCalls.slice(callsBefore),\n"
        " newUrls: __urls.length - urlsBefore };\n"
        "})().then((v) => console.log(JSON.stringify(v)))",
    )
    assert out["newCalls"] == ["/api/traces/s/0"]
    assert out["newUrls"] == 0                       # push: false


def test_render_list_groups_sessions_and_rows(node: str) -> None:
    """renderList paints session cards in first-seen order; each turn row
    carries #turn index, bot text (union, '+N'), title, time, model, tok,
    cost, dur, the status chip and the data-session/data-turn hook."""
    rows = [
        {"session_id": "g", "turn_index": 1, "title": "T1",
         "started_at": "2026-09-20T10:00:00", "status": "completed",
         "session_kind": "group", "bot_id": "coder",
         "session_bots": ["coder", "ops"],
         "bot_breakdown": [{"bot_id": "coder"}, {"bot_id": "ops"}],
         "model": "m",
         "total_tokens": 10, "cost_usd": 0.01, "latency_ms": 40},
        {"session_id": "g", "turn_index": 0, "title": "T0",
         "started_at": "2026-09-20T09:00:00", "status": "failed",
         "session_kind": "group", "bot_id": "coder",
         "session_bots": ["coder", "ops"],
         "bot_breakdown": [{"bot_id": "coder"}, {"bot_id": "ops"}],
         "model": "m",
         "total_tokens": 5, "cost_usd": None, "latency_ms": 20},
    ]
    html = _eval_trace(
        node,
        f"tr.rows = {json.dumps(rows)}; tr.total = 2; tr.loading = false;\n"
        "renderList();\n"
        "console.log(JSON.stringify(document.querySelector('#trace-sessions').innerHTML))",
    )
    assert html.count('class="paper-card tl-session"') == 1   # one session card
    assert 'data-session="g"' in html and 'data-turn="1"' in html
    assert "<b>Coder +1</b>" in html                          # union +N
    assert "#1" in html and "#0" in html
    assert "Thành công" in html and "Lỗi" in html             # status chips
    assert "1 lượt lỗi" in html                               # failed chip
    assert "$0.0100" in html and ">—<" in html                # cost + null


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


def test_turn_bot_ids_prefers_bot_id_then_breakdown(node: str) -> None:
    """turnBotIds: bot_id first, then breakdown order, deduped — empty
    turns yield none."""
    out = _eval_trace(
        node,
        "console.log(JSON.stringify(["
        " turnBotIds({ bot_id: 'a', bot_breakdown:"
        "  [{ bot_id: 'a' }, { bot_id: 'b' }] }),"
        " turnBotIds({ bot_breakdown: [{ bot_id: 'b' }] }),"
        " turnBotIds({})]))",
    )
    assert out[0] == ["a", "b"]
    assert out[1] == ["b"]
    assert out[2] == []


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
        "tr.models = new Set(['glm', 'abc']); tr.filters.model = 'zzz';\n"
        "fillModelOptions();\n"
        "const sel = document.querySelector('#f-model');\n"
        "console.log(JSON.stringify({ vals: sel.options.map(o => o.value),"
        " cur: sel.value }))",
    )
    assert out["vals"] == ["", "abc", "glm", "zzz"]
    assert out["cur"] == "zzz"


def test_filter_change_handlers_are_wired(node: str) -> None:
    """f-from/f-to/f-model/f-status all trigger a refilter on change; the
    form's submit is preventDefault'd into the same path."""
    # Structural pin: the handler list + debounced search input live in
    # wirePage — a wrong selector or missing binding shows up here.
    src = TRACE_JS.read_text(encoding="utf-8")
    assert "['f-from', 'f-to', 'f-model', 'f-status']" in src
    assert "addEventListener('change', onFilter)" in src
    assert "addEventListener('input'" in src
    assert "setTimeout(onFilter, 350)" in src      # 350 ms debounce on q
    assert "addEventListener('submit'" in src
    assert "preventDefault()" in src
