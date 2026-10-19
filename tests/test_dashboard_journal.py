"""Tests for the unified Dashboard contract (GOAL-010 TASK-028/029,
TASK-039, overview redesign).

The four separate views (Sử dụng token / Request / Chi phí / Trace) are
gone: the Dashboard is one overview screen (Chung / bot / group, rendered
by overview.js into #overview-content from the #overview-nav entity
selector) plus the Trace detail screen (#trace). Any legacy view hash
(#su-dung/#request/#chi-phi/#hom-nay, or unknown) falls back to the
overview, and ?session=/?turn= deep links open Trace. dashboard-today.js
keeps only the /api/traces paging helper consumed by the overview snapshot
(fetchAllTraces: dedupe on (session_id, turn_index), explicit incomplete
error). The boot behavior runs against a minimal fake DOM in Node; static
checks pin the entity selector and the two-state switching contract.
Mirrors the eval style of test_webui_format.py.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEBUI = ROOT / "thyca" / "webui"
SCRIPT = WEBUI / "shared" / "js" / "dashboard-today.js"
DASH_JS = WEBUI / "pages" / "dashboard" / "dashboard.js"
DASH_HTML = WEBUI / "dashboard.html"
NAV_JS = WEBUI / "shared" / "js" / "navigation.js"


@pytest.fixture(scope="module")
def node() -> str:
    binary = shutil.which("node")
    if not binary:
        pytest.skip("node not installed")
    return binary


def _eval(node: str, expression: str) -> object:
    source = (
        f"import {{ fetchAllTraces }} from '{SCRIPT.as_posix()}';\n"
        f"console.log(JSON.stringify(await ({expression})));\n"
    )
    result = subprocess.run(
        [node, "--input-type=module", "-e", source],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return json.loads(result.stdout)


def _error_name(node: str, expression: str) -> str:
    source = (
        f"import {{ fetchAllTraces }} from '{SCRIPT.as_posix()}';\n"
        f"try {{\n  await ({expression});\n  console.log('no-error');\n}}"
        f" catch (error) {{ console.log(error.constructor.name); }}\n"
    )
    result = subprocess.run(
        [node, "--input-type=module", "-e", source],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return result.stdout.strip()


def _boot_dashboard(node: str, *, hash: str = "", search: str = "", actions: str = "[]") -> dict:
    """Import dashboard.js against a minimal fake DOM and report the resulting
    view state. `actions` is a JSON array of {type: "back"} /
    {type: "hashchange", hash} replayed after boot. The cache-busting query
    gives every boot a fresh module instance."""
    script = """
    const blocks = Object.fromEntries(
      ["overview", "trace"].map((view) => {{
        const el = {{
          hidden: false,
          scrollTop: 0,
          dataset: {{}},
          attrs: {{}},
          classes: new Set(),
          listeners: {{}},
          classList: {{
            toggle(cls, on) {{ if (on) el.classes.add(cls); else el.classes.delete(cls); }},
          }},
          setAttribute(name, value) {{ el.attrs[name] = value; }},
          addEventListener(type, fn) {{ el.listeners[type] = fn; }},
        };
        return [view, el];
      }}),
    );
    const surface = {{ scrollTop: 0 }};
    const backs = ["back", "trace-back"].map((name) => {{
      const el = {{
        listeners: {{}},
        addEventListener(type, fn) {{ el.listeners[type] = fn; }},
      };
      return el;
    }});
    const shell = {{
      classes: new Set(),
      classList: {{
        toggle(cls, on) {{ if (on) shell.classes.add(cls); else shell.classes.delete(cls); }},
      }},
    }};
    const bySelector = {{
      "#overview": blocks.overview,
      "#trace": blocks.trace,
      ".dashboard-surface": surface,
      ".dashboard-shell": shell,
    }};
    globalThis.document = {{
      querySelector: (sel) => bySelector[sel] || null,
      querySelectorAll: (sel) => (sel === ".dashboard-shell .screen-back, #trace-back" ? backs : []),
    }};
    const location = {{ hash: __HASH__, search: __SEARCH__, pathname: "/dashboard.html" }};
    globalThis.location = location;
    const urlCalls = [];
    globalThis.history = {{
      replaceState: (_a, _b, url) => {{
        const u = String(url);
        urlCalls.push(u);
        // Mirror the browser: a path-bearing URL replaces the query; a bare
        // "#hash" leaves it alone.
        const query = u.match(/\\?([^#]*)/);
        if (query) location.search = `?${{query[1]}}`;
        else if (u.startsWith("/")) location.search = "";
        location.hash = u.startsWith("#") ? u : (u.includes("#") ? `#${{u.split("#")[1]}}` : "");
      }},
    }};
    const windowListeners = {{}};
    globalThis.window = {{ addEventListener: (type, fn) => {{ windowListeners[type] = fn; }} }};
    globalThis.requestAnimationFrame = (fn) => fn();
    await import("__DASH_URI__?boot=1");
    for (const action of __ACTIONS__) {{
      if (action.type === "back") {{
        for (const back of backs) back.listeners.click();
      }} else if (action.type === "hashchange") {{
        location.hash = action.hash;
        windowListeners.hashchange();
      }}
    }}
    const visible = Object.entries(blocks).filter(([, b]) => !b.hidden).map(([k]) => k);
    process.stdout.write(JSON.stringify({{ visible, hash: location.hash,
      search: location.search, urls: urlCalls }}));
    """
    script = (
        # Un-double the braces that were written for an f-string, then inject
        # the dynamic values (afterwards, so JSON braces stay untouched).
        script.replace("{{", "{").replace("}}", "}")
        .replace("__HASH__", json.dumps(hash))
        .replace("__SEARCH__", json.dumps(search))
        .replace("__ACTIONS__", actions)
        .replace("__DASH_URI__", DASH_JS.as_uri())
    )
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return json.loads(result.stdout)


def test_navigation_menu_drops_the_standalone_trace_route() -> None:
    """TASK-028: Trace lives inside the Dashboard, so Mục lục has no Trace
    entry; the dashboard destination is labeled Dashboard."""
    routes = [
        line.strip().rstrip(",")
        for line in NAV_JS.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith('["')
    ]
    assert all(not route.startswith('["trace.html"') for route in routes)
    assert any(route.startswith('["dashboard.html", "Dashboard"') for route in routes)
    # The remaining destinations are unchanged apart from the Trace entry.
    assert routes == [
        '["index.html", "Trò chuyện", "M4 5h16v11H9l-5 4V5Z"]',
        '["memories.html", "Nhật ký", "M12 6C9 4 6 4 3 5v14c3-1 6-1 9 1 3-2 6-2 9-1V5c-3-1-6-1-9 1Zm0 0v14"]',
        '["profile.html", "Hồ sơ", "M12 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8Zm-7 18a7 7 0 0 1 14 0"]',
        '["dashboard.html", "Dashboard", "M4 20V10h4v9M10 20V5h4v14M16 19v-7h4v7"]',
        '["settings.html", "Cài đặt", "M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1 1-3Zm3 5a4 4 0 1 0 0 8 4 4 0 0 0 0-8"]',
    ]


def test_dashboard_sidebar_is_the_entity_selector() -> None:
    """The Dashboard sidebar is the entity selector rendered by overview.js
    into #overview-nav (Chung / bot / group) — the four data-view toggles
    are gone, and only the overview + trace sections survive."""
    html = DASH_HTML.read_text(encoding="utf-8")
    assert 'data-view="' not in html
    assert 'id="overview-nav"' in html
    assert 'id="overview"' in html
    assert 'id="overview-period"' in html
    assert 'id="overview-range"' in html
    assert 'id="overview-content"' in html
    assert 'id="overview-status"' in html
    assert '<section class="dashboard-block" id="trace"' in html
    for gone in ('id="request"', 'id="su-dung"', 'id="chi-phi"',
                 'id="request-total"', 'id="request-chart"', 'id="usage-chart"',
                 'id="cost-chart"', 'id="cost-total"', 'id="usage-total"'):
        assert gone not in html, gone
    # Old entry scripts are off the page; the overview and trace entries stay.
    assert './pages/dashboard/overview.js' in html
    assert './pages/dashboard/overview.css' in html
    assert './pages/dashboard/trace.js' in html
    for gone in ('./pages/dashboard/cost.js', './pages/dashboard/request.js',
                 './pages/dashboard/usage.js'):
        assert gone not in html, gone


def test_dashboard_boot_defaults_to_overview(node: str) -> None:
    state = _boot_dashboard(node)
    assert state["visible"] == ["overview"]
    assert state["hash"] == ""
    assert state["urls"] == []


def test_dashboard_boot_sends_legacy_and_unknown_hashes_to_overview(node: str) -> None:
    """Legacy view hashes (#su-dung/#request/#chi-phi/#hom-nay) and any
    unknown hash fall back to the overview, and the stale hash is stripped
    so the URL matches the screen."""
    for hash in ("#su-dung", "#request", "#chi-phi", "#hom-nay", "#khong-hop-le"):
        state = _boot_dashboard(node, hash=hash)
        assert state["visible"] == ["overview"], hash
        assert state["hash"] == "", hash


def test_dashboard_boot_opens_trace_hash(node: str) -> None:
    state = _boot_dashboard(node, hash="#trace")
    assert state["visible"] == ["trace"]
    assert state["hash"] == "#trace"


def test_dashboard_boot_opens_trace_for_deep_links(node: str) -> None:
    """A ?session=/?turn= deep link always opens Trace — with or without the
    #trace hash — and never the overview. The deep-link params stay on the
    URL."""
    for search in ("?session=s1&turn=2", "?session=s1", "?turn=0"):
        state = _boot_dashboard(node, search=search)
        assert state["visible"] == ["trace"], search
        assert state["search"] == search, search
    state = _boot_dashboard(node, hash="#trace", search="?session=s1&turn=2")
    assert state["visible"] == ["trace"]
    assert state["hash"] == "#trace"  # already correct: no rewrite
    assert state["search"] == "?session=s1&turn=2"


def test_dashboard_back_from_trace_returns_to_overview(node: str) -> None:
    """The Trace back button returns to the overview and strips ?session=/
    ?turn= in the same replaceState (plus any stale hash), so the URL always
    matches the screen and Back returns to the caller page (replaceState, not
    pushState)."""
    state = _boot_dashboard(
        node,
        search="?session=s1&turn=2",
        actions='[{"type": "back"}]',
    )
    assert state["visible"] == ["overview"]
    assert state["hash"] == ""
    assert state["search"] == ""
    assert state["urls"][-1] == "/dashboard.html"


def test_dashboard_keeps_other_query_params_when_leaving_trace(node: str) -> None:
    """Only the Trace deep-link params and the hash are dropped; anything
    else on the query survives the trip back to the overview."""
    state = _boot_dashboard(
        node,
        search="?session=s1&keep=1",
        actions='[{"type": "back"}]',
    )
    assert state["visible"] == ["overview"]
    assert state["search"] == "?keep=1"
    assert state["urls"][-1] == "/dashboard.html?keep=1"


def test_trace_period_has_the_unfiltered_window_option() -> None:
    """The Trace period filter offers the unfiltered API window (200 newest
    session files) so history older than the rolling ranges stays reachable;
    the other views' filters are unchanged."""
    html = DASH_HTML.read_text(encoding="utf-8")
    assert '<option value="all">200 phiên mới nhất</option>' in html
    assert html.count('value="all"') == 1
    # The decorative "— Trace —" divider is gone; the per-view headings carry
    # the surface name.
    assert 'class="trace-head"' not in html


def test_dashboard_navigation_back_and_hashchange(node: str) -> None:
    """The Trace back button returns to the overview, and a later #trace
    hashchange reopens Trace (the deep-link boot state is not sticky)."""
    state = _boot_dashboard(
        node,
        search="?session=s1&turn=2",
        actions='[{"type": "back"}, {"type": "hashchange", "hash": "#trace"}]',
    )
    assert state["visible"] == ["trace"]
    assert state["hash"] == "#trace"


def test_dashboard_switcher_covers_exactly_the_two_views() -> None:
    dash = DASH_JS.read_text(encoding="utf-8")
    assert '"#hom-nay"' not in dash
    assert '"#su-dung"' not in dash
    assert '"#request"' not in dash
    assert '"#chi-phi"' not in dash
    assert '"#trace"' in dash
    assert 'const DEFAULT_VIEW = "overview";' in dash
    assert 'node.hidden = key !== next' in dash
    # Deep-link detection covers both deep-link params.
    assert 'params.has("session") || params.has("turn")' in dash


def test_dashboard_mobile_pick_pattern_survives() -> None:
    """Mobile pick pattern (same as Hồ sơ/Cài đặt): the entity list shows
    first; choosing (in overview.js) reveals the overview, ‹ Dashboard
    returns. No pill override survives in dashboard.css — kit's pick rules
    own the layout."""
    dash_css = (WEBUI / "pages" / "dashboard" / "dashboard.css").read_text(encoding="utf-8")
    dash_js = (WEBUI / "pages" / "dashboard" / "dashboard.js").read_text(encoding="utf-8")
    overview_js = (WEBUI / "pages" / "dashboard" / "overview.js").read_text(encoding="utf-8")
    pick_html = DASH_HTML.read_text(encoding="utf-8")
    assert "pick-shell is-picking" in pick_html
    assert pick_html.count('class="screen-back"') == 1
    assert "syncPick" in dash_js
    assert 'classList.toggle("is-picking"' in dash_js
    # Entity selection hands off to the overview content on mobile too.
    assert 'classList.remove("is-picking")' in overview_js
    assert ".dashboard-shell .sessions" not in dash_css
    # By-model charts sit inset from their section headers on both sides.
    assert "padding-inline: 1rem" in dash_css


def test_mobile_filter_search_resets_row_basis() -> None:
    """The filter-row search basis (20rem) is a width on desktop but becomes
    a 320px height once the row stacks vertically — mobile must reset it so
    the box renders at its slim --search-h like the memories search."""
    kit = (WEBUI / "shared" / "css" / "kit.css").read_text(encoding="utf-8")
    mobile = kit[kit.index("@media (max-width: 56rem)"):]
    assert ".screen-shell .screen-filter-row .thyca-search" in mobile
    assert "flex: none" in mobile


def test_dashboard_sections_are_flat_and_errors_use_scoped_red() -> None:
    dash_css = (WEBUI / "pages" / "dashboard" / "dashboard.css").read_text(encoding="utf-8")
    screens_css = (WEBUI / "shared" / "css" / "kit.css").read_text(encoding="utf-8")
    # Decorative card background/radius/shadow go; no global root token change.
    assert ".dashboard-section .screen-card" in dash_css
    assert "background: transparent" in dash_css
    assert "--journal-error-ink:" in screens_css
    assert "color: var(--journal-error-ink)" in screens_css
    assert screens_css.count("--journal-error-ink:") == 1


def test_today_only_helpers_are_gone_but_paging_stays_for_overview() -> None:
    """dashboard-today.js keeps only the /api/traces paging helper; the
    overview snapshot imports it (tests/test_cost_journal.py pins the
    behavior)."""
    helper = SCRIPT.read_text(encoding="utf-8")
    for gone in ("todaySummary", "dayKeyInZone", "periodRange", "rowsInPeriod", "formatRunStamp", "recentTraces"):
        assert gone not in helper
    assert "export async function fetchAllTraces" in helper
    overview = (WEBUI / "pages" / "dashboard" / "overview.js").read_text(encoding="utf-8")
    assert 'import { fetchAllTraces } from "../../shared/js/dashboard-today.js";' in overview


def test_fetch_all_traces_pages_until_total_and_dedupes(node: str) -> None:
    """Overlapping pages are expected (rows can shift between requests); the
    (session_id, turn_index) pair dedupes them without a cap on rows."""
    result = _eval(
        node,
        "(async () => {"
        "const calls = [];"
        "const pages = ["
        "  { traces: [{ session_id: 's1', turn_index: 0 }, { session_id: 's1', turn_index: 1 }], total: 6 },"
        "  { traces: [{ session_id: 's1', turn_index: 1 }, { session_id: 's2', turn_index: 0 }], total: 6 },"
        "  { traces: [{ session_id: 's2', turn_index: 0 }, { session_id: 's3', turn_index: 5 }], total: 4 }"
        "];"
        "const rows = await fetchAllTraces(async (offset) => { calls.push(offset); return pages[offset / 2] ?? { traces: [], total: 0 }; });"
        "return { rows, calls };"
        "})()",
    )
    assert [(row["session_id"], row["turn_index"]) for row in result["rows"]] == [
        ("s1", 0), ("s1", 1), ("s2", 0), ("s3", 5),
    ]
    assert result["calls"] == [0, 2, 4]


def test_fetch_all_traces_reports_incomplete_when_the_server_stops_early(node: str) -> None:
    """The backend caps each answer at 200 session files: `total` can claim
    more than any page will ever return. That is an explicit incomplete state,
    not a shorter day presented as the whole day."""
    name = _error_name(
        node,
        "(async () => await fetchAllTraces(async (offset) => {"
        "  if (offset === 0) return { traces: [{ session_id: 's1', turn_index: 0 }], total: 999 };"
        "  return { traces: [], total: 999 };"
        "}))()",
    )
    assert name == "TracesIncompleteError"


def test_fetch_all_traces_reports_incomplete_on_a_repeat_page(node: str) -> None:
    """A server that keeps answering with the same nonempty page must not end
    as a deduped partial day: zero new rows while `total` claims more is an
    explicit incomplete error."""
    name = _error_name(
        node,
        "(async () => await fetchAllTraces(async () => ({"
        "  traces: [{ session_id: 's1', turn_index: 0 }, { session_id: 's1', turn_index: 1 }],"
        "  total: 999"
        "})))()",
    )
    assert name == "TracesIncompleteError"


def test_fetch_all_traces_rejects_a_served_page_without_a_total(node: str) -> None:
    """The API always answers { traces, total }: rows without a readable
    positive total are malformed, not an empty remainder to end the loop."""
    for bad_total in ("undefined", "null", "'0'", "-5"):
        name = _error_name(
            node,
            "(async () => await fetchAllTraces(async () => ("
            f"{{ traces: [{{ session_id: 's1', turn_index: 0 }}], total: {bad_total} }}"
            ")))()",
        )
        assert name == "TracesIncompleteError", bad_total


USAGE_JS = ROOT / "thyca" / "webui" / "pages" / "dashboard" / "usage.js"


def _usage_eval(node: str, expression: str) -> object:
    source = (
        "globalThis.document = { createElement: () => ({}),"
        " querySelector: () => null, querySelectorAll: () => [],"
        " body: { prepend() {} } };\n"
        "globalThis.matchMedia = () => ({ matches: false,"
        " addEventListener() {} });\n"
        f"const {{ aggregateBotTokens }} = await import('{USAGE_JS.as_posix()}');\n"
        f"console.log(JSON.stringify(await ({expression})));\n"
    )
    result = subprocess.run(
        [node, "--input-type=module", "-e", source],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return json.loads(result.stdout)


def test_usage_bot_tokens_breakdown_fallback_and_sort(node: str) -> None:
    traces = [
        {"bot_id": "coder", "requests": 1, "prompt_tokens": 10,
         "cached_tokens": 2, "completion_tokens": 5,
         "bot_breakdown": [
             {"bot_id": "coder", "requests": 1, "prompt_tokens": 10,
              "cached_tokens": 2, "completion_tokens": 5},
             {"bot_id": "helper", "requests": 2, "prompt_tokens": 20,
              "cached_tokens": 0, "completion_tokens": 4},
         ]},
        {"bot_id": "solo", "requests": 3, "prompt_tokens": 30,
         "cached_tokens": 30, "completion_tokens": 0},
        {"bot_id": None, "requests": 1, "prompt_tokens": 4,
         "cached_tokens": 0, "completion_tokens": 1},
        {"bot_id": "unknown", "requests": 1, "prompt_tokens": 4,
         "cached_tokens": 0, "completion_tokens": 1},
    ]
    groups = _usage_eval(
        node, f"aggregateBotTokens({json.dumps(traces)})")
    by_id = {g["bot_id"]: g for g in groups}
    # Breakdown entries credit their own bot, not the row's bot_id.
    assert by_id["coder"]["prompt_tokens"] == 10
    assert by_id["helper"]["requests"] == 2
    assert by_id["helper"]["turns"] == 1
    # Fallback rows use their own numbers; null/unknown merge by key.
    assert by_id["solo"]["completion_tokens"] == 0
    assert "" in by_id and "unknown" in by_id
    # Totals: prompt + completion (cache inside prompt, never doubled).
    assert by_id["coder"]["prompt_tokens"] + by_id["coder"]["completion_tokens"] == 15
    # Sort: tokens desc.
    totals = [(g["bot_id"], g["prompt_tokens"] + g["completion_tokens"])
              for g in groups]
    assert totals == sorted(totals, key=lambda kv: -kv[1])


_USAGE_DOM = r"""
class Element {
  constructor(tag = 'div') {
    this.tag = tag;
    this.children = [];
    this.className = '';
    this.textContent = '';
    this.innerHTML = '';
    this.dataset = {};
    this.attributes = {};
    this.value = '30';
    this.hidden = false;
    this.style = { _props: {}, setProperty(k, v) { this._props[k] = v; } };
  }
  setAttribute(k, v) { this.attributes[k] = String(v); }
  append(...nodes) { for (const n of nodes.flat()) this.children.push(n); }
  replaceChildren(...nodes) { this.children = [...nodes.flat()]; }
  addEventListener() {}
  querySelectorAll(sel) {
    const cls = sel.startsWith('.') ? sel.slice(1) : null;
    const out = [];
    const walk = (node) => {
      for (const child of node.children || []) {
        if (typeof child === 'string') continue;
        if (cls && String(child.className || '').split(' ').includes(cls)) out.push(child);
        walk(child);
      }
    };
    walk(this);
    return out;
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] ?? null; }
}
const mount = new Element('div');
mount.isConnected = true;
const modelsMount = new Element('div');
globalThis.document = {
  createElement: (tag) => new Element(tag),
  querySelector: (sel) => sel === '#usage-bots' ? mount
    : sel === '#usage-models' ? modelsMount : null,
  querySelectorAll: () => [],
  body: { prepend() {} },
};
globalThis.matchMedia = () => ({ matches: false, addEventListener() {} });
globalThis.ResizeObserver = class { observe() {} };
globalThis.fetch = async () => ({ ok: true, json: async () => ({ bots: [] }) });
""".strip()


def _usage_entry(node: str, bot, denominator, roster) -> object:
    source = (
        _USAGE_DOM + "\n"
        "const { botTokenEntry } = await import('./thyca/webui/pages/dashboard/usage.js');\n"
        "const main = async () => {"
        f" const _roster = {json.dumps(roster)};"
        " const el = botTokenEntry("
        f" {json.dumps(bot)}, {denominator},"
        " _roster === null ? null : new Map(Object.entries(_roster)));"
        " const slot = el.querySelector('.bot-avatar-slot');"
        " const name = el.querySelector('.request-model-name');"
        " return [el.tagName || el.tag, slot.innerHTML.includes('mk-wax'),"
        "  name.textContent, el.querySelector('.request-model-count').textContent];"
        " };\n"
        "console.log(JSON.stringify(await main()));\n"
    )
    result = subprocess.run(
        [node, "--input-type=module", "-e", source],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return json.loads(result.stdout)


def test_usage_bot_entry_avatar_roster_and_unknown(node: str) -> None:
    bot = {"bot_id": "coder", "turns": 2, "requests": 3, "prompt_tokens": 10,
           "cached_tokens": 2, "completion_tokens": 5}
    tag, mark, name, count = _usage_entry(
        node, bot, 15, {"coder": {"id": "coder", "name": "Coder"}})
    assert mark is True and name == "Coder" and count == "15 token"
    tag, mark, name, count = _usage_entry(node, bot, 15, None)
    assert mark is True and name == "coder"
    ghost = {"bot_id": "unknown", "turns": 1, "requests": 1,
             "prompt_tokens": 4, "cached_tokens": 0, "completion_tokens": 1}
    tag, mark, name, count = _usage_entry(node, ghost, 5, None)
    assert mark is False and name == "chưa gắn bot"
    real = _usage_entry(
        node, ghost, 5, {"unknown": {"id": "unknown", "name": "Nemo"}})
    assert real[1] is True and real[2] == "Nemo"




OVERVIEW_JS = WEBUI / "pages" / "dashboard" / "overview.js"


def _overview_eval(node: str, expression: str) -> object:
    source = (
        "globalThis.document = { querySelector: () => null,"
        " querySelectorAll: () => [] };\n"
        f"const ov = await import('{OVERVIEW_JS.as_posix()}');\n"
        f"console.log(JSON.stringify(await ({expression})));\n"
    )
    result = subprocess.run(
        [node, "--input-type=module", "-e", source],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return json.loads(result.stdout)


def test_overview_aggregate_totals_split_cache(node: str) -> None:
    rows = [
        {"requests": 2, "prompt_tokens": 100, "cached_tokens": 30,
         "completion_tokens": 10, "cost_usd": 0.01},
        {"requests": 1, "prompt_tokens": 50, "cached_tokens": 0,
         "completion_tokens": 5, "cost_usd": None},
    ]
    totals = _overview_eval(
        node, f"ov.aggregateOverview({json.dumps(rows)})")
    assert totals["requests"] == 3
    assert totals["turns"] == 2
    assert totals["prompt"] == 150
    assert totals["cached"] == 30
    assert totals["completion"] == 15
    assert totals["total"] == 165  # cache lives inside prompt, never doubled
    assert totals["cost"] == pytest.approx(0.01)
    assert totals["pricedTurns"] == 1
    assert totals["unpricedTurns"] == 1


def test_overview_aggregate_by_bot_credits_breakdown(node: str) -> None:
    rows = [
        {"bot_id": "coder", "requests": 1, "prompt_tokens": 10,
         "cached_tokens": 2, "completion_tokens": 5, "cost_usd": 0.01,
         "bot_breakdown": [
             {"bot_id": "coder", "requests": 1, "prompt_tokens": 10,
              "cached_tokens": 2, "completion_tokens": 5, "cost_usd": 0.008},
             {"bot_id": "helper", "requests": 2, "prompt_tokens": 20,
              "cached_tokens": 0, "completion_tokens": 4, "cost_usd": 0.002},
         ]},
        {"bot_id": "solo", "requests": 3, "prompt_tokens": 30,
         "cached_tokens": 30, "completion_tokens": 0, "cost_usd": 0.005},
        {"bot_id": None, "requests": 1, "prompt_tokens": 4,
         "cached_tokens": 0, "completion_tokens": 1, "cost_usd": 0.001},
    ]
    groups = _overview_eval(
        node, f"ov.aggregateByBot({json.dumps(rows)})")
    by_id = {g["botId"]: g for g in groups}
    # Breakdown entries credit their own bot, not the row's bot_id.
    assert by_id["coder"]["prompt_tokens"] == 10
    assert by_id["coder"]["cost"] == pytest.approx(0.008)
    assert by_id["helper"]["requests"] == 2
    assert by_id["helper"]["turns"] == 1
    # Fallback rows use their own numbers; unmapped rows land in "".
    assert by_id["solo"]["completion_tokens"] == 0
    assert by_id[""]["requests"] == 1
    assert "unknown" not in by_id
    # Sort: cost desc.
    costs = [g["botId"] for g in groups]
    assert costs[0] == "coder"


def test_overview_groups_only_real_group_sessions(node: str) -> None:
    rows = [
        {"session_id": "g1", "turn_index": 0, "session_kind": "group",
         "session_bots": ["a", "b"], "title": "Nhóm AB",
         "requests": 1, "prompt_tokens": 10, "completion_tokens": 2,
         "latency_ms": 1000, "cost_usd": 0.01},
        {"session_id": "g1", "turn_index": 1, "session_kind": "group",
         "session_bots": ["a", "b"], "title": "Nhóm AB",
         "requests": 1, "prompt_tokens": 10, "completion_tokens": 2,
         "latency_ms": 2000, "cost_usd": None},
        {"session_id": "d1", "turn_index": 0, "session_kind": "dm",
         "session_bots": ["a"], "title": "DM A",
         "requests": 1, "prompt_tokens": 5, "completion_tokens": 1,
         "latency_ms": 500, "cost_usd": 0.001},
    ]
    session_map = {
        "g1": {"kind": "group", "bots": ["a", "b"], "title": "Nhóm AB"},
        "d1": {"kind": "dm", "bots": ["a"], "title": "DM A"},
    }
    groups = _overview_eval(
        node, f"ov.aggregateGroups({json.dumps(rows)}, {json.dumps(session_map)})")
    assert [g["sessionId"] for g in groups] == ["g1"]
    group = groups[0]
    assert group["turns"] == 2
    assert group["latencyMs"] == 3000
    assert group["cost"] == pytest.approx(0.01)
    assert group["pricedTurns"] == 1
    assert group["unpricedTurns"] == 1
    assert group["participants"] == ["a", "b"]


def test_overview_models_share_token_basis(node: str) -> None:
    rows = [
        {"model": "m1", "requests": 1, "prompt_tokens": 100,
         "cached_tokens": 10, "completion_tokens": 20, "cost_usd": 0.02},
        {"model": "m2", "requests": 1, "prompt_tokens": 10,
         "cached_tokens": 0, "completion_tokens": 2, "cost_usd": 0.001},
    ]
    models = _overview_eval(
        node, f"ov.aggregateModels({json.dumps(rows)})")
    assert [m["model"] for m in models] == ["m1", "m2"]
    assert models[0]["cost"] == pytest.approx(0.02)


def test_overview_timelines_respect_owner_and_cap(node: str) -> None:
    rows = [
        {"session_id": "g1", "turn_index": i, "started_at": f"2026-10-1{i}T00:00:00",
         "bot_id": "a", "model": "m", "status": "ok", "title": "Nhóm",
         "requests": 1, "prompt_tokens": 10, "cached_tokens": 0,
         "completion_tokens": 2, "latency_ms": 100, "cost_usd": 0.001,
         "bot_breakdown": [
             {"bot_id": "a", "requests": 1, "prompt_tokens": 6,
              "cached_tokens": 0, "completion_tokens": 1, "cost_usd": 0.0006},
             {"bot_id": "b", "requests": 1, "prompt_tokens": 4,
              "cached_tokens": 0, "completion_tokens": 1, "cost_usd": 0.0004},
         ]}
        for i in range(3)
    ]
    timeline = _overview_eval(
        node, f"ov.timelineForBot({json.dumps(rows)}, 'b', 2)")
    assert len(timeline) == 2  # newest first, capped
    assert timeline[0]["turnIndex"] == 2
    assert timeline[0]["slice"]["prompt"] == 4
    assert timeline[0]["slice"]["cost"] == pytest.approx(0.0004)
    group_turns = _overview_eval(
        node, f"ov.timelineForGroup({json.dumps(rows)}, 'g1')")
    assert [t["turn_index"] for t in group_turns] == [2, 1, 0]
    parts = _overview_eval(
        node, f"ov.aggregateGroupBots({json.dumps(rows)}, 'g1')")
    by_id = {p["botId"]: p for p in parts}
    # Cost parts sum exactly to the group total; multi-bot turn latency is
    # attributed once to the turn's actor, never double-counted.
    assert by_id["a"]["cost"] + by_id["b"]["cost"] == pytest.approx(0.003)
    assert by_id["a"]["latencyMs"] == 300
    assert by_id["b"]["latencyMs"] == 0
    solo = _overview_eval(
        node, "ov.aggregateGroupBots("
        + json.dumps([{"session_id": "g1", "turn_index": 0, "bot_id": "a",
                       "requests": 1, "latency_ms": 700, "cost_usd": 0.002}])
        + ", 'g1')")
    assert solo[0]["latencyMs"] == 700


def test_overview_entity_bots_include_history_bots(node: str) -> None:
    """Sidebar entities = roster order first, then history bots seen only in
    the snapshot aggregates (cost-desc); the "" bucket never becomes an
    entity; a history bot still resolves a timeline from traces."""
    roster = [{"id": "thyca", "name": "Thyca"}, {"id": "quiet", "name": "Quiet"}]
    aggregates = [
        {"botId": "be-bay", "cost": 0.005, "requests": 2},
        {"botId": "thyca", "cost": 0.07, "requests": 37},
        {"botId": "", "cost": 0.012, "requests": 13},
        {"botId": "nhac-viec", "cost": 0.001, "requests": 2},
    ]
    entities = _overview_eval(
        node,
        f"ov.entityBotList({json.dumps(roster)}, {json.dumps(aggregates)})")
    assert [b["id"] for b in entities] == ["thyca", "quiet", "be-bay", "nhac-viec"]
    # History bots carry no card name (display falls back to id); roster bots
    # without period data keep their row.
    assert entities[2] == {"id": "be-bay", "name": ""}
    assert entities[1] == {"id": "quiet", "name": "Quiet"}
    rows = [
        {"session_id": "g1", "turn_index": 0, "started_at": "2026-10-16T10:00:00",
         "bot_id": "be-bay", "model": "m", "status": "ok", "title": "T",
         "requests": 1, "prompt_tokens": 10, "cached_tokens": 0,
         "completion_tokens": 2, "latency_ms": 100, "cost_usd": 0.005},
    ]
    timeline = _overview_eval(
        node, f"ov.timelineForBot({json.dumps(rows)}, 'be-bay', 20)")
    assert len(timeline) == 1
    assert timeline[0]["slice"]["cost"] == pytest.approx(0.005)


def test_overview_type_scale_follows_chat() -> None:
    """Overview type follows the chat scale: sidebar names/meta match the
    chat session rows, the content root sets reading/1rem, and summary
    figures use the large-number size."""
    css = (WEBUI / "pages" / "dashboard" / "overview.css").read_text(encoding="utf-8")

    def block(selector: str) -> str:
        head = css[css.index(selector):]
        return head[:head.index("}")]

    assert "font-size: 0.94rem" in block("#overview-nav .srow b")
    assert "font-size: 0.72rem" in block("#overview-nav .srow .meta")
    assert "font-family: var(--font-reading)" in block("#overview-nav .srow")
    root = block("#overview {")
    assert "font-family: var(--font-reading)" in root
    assert "font-size: 1rem" in root
    assert "font-size: 1.5rem" in block("#overview .value")
    assert "font-size: 0.72rem" in block("#overview-nav .side-label")

    # The mini Trace lists (bot/group views) share the Chat scale too:
    # reading 0.9375rem/1.8 body, display h3 1.25rem, UI-font secondary
    # metadata 0.75rem/1.5 (Chat's footer metadata uses --font-ui).
    trace_root = block("#overview .trace {")
    assert "font-size: 0.9375rem" in trace_root
    assert "line-height: 1.8" in trace_root
    assert "font-size: 1.25rem" in block("#overview .trace h3")
    tact = block("#overview .trace .tact")
    assert "font-size: 0.9375rem" in tact
    assert "line-height: 1.8" in tact
    for sel in (
        "#overview .trace .ttime", "#overview .trace .tmeta",
        "#overview .trace .tval", "#overview .trace .ph,",
    ):
        role = block(sel)
        assert "font-family: var(--font-ui)" in role
        assert "font-size: 0.75rem" in role
        assert "line-height: 1.5" in role
    # The cost <small> inherits the row's 0.75rem instead of shrinking.
    assert "font-size: inherit" in block("#overview .trace .tval small")


def test_overview_group_badge_only_for_partial_pricing(node: str) -> None:
    """Fully priced groups carry no badge (no dangling separator); partially
    priced groups keep the coverage warning."""
    full = _overview_eval(node, "ov.badgeFor({unpricedTurns: 0})")
    assert full == ""
    assert "badge ok" not in (WEBUI / "pages" / "dashboard" / "overview.js").read_text(encoding="utf-8")
    assert ".badge.ok" not in (WEBUI / "pages" / "dashboard" / "overview.css").read_text(encoding="utf-8")
    part = _overview_eval(node, "ov.badgeFor({unpricedTurns: 3})")
    assert "badge part" in part
    assert "3 lượt chưa định giá" in part
    single = _overview_eval(node, "ov.badgeFor({unpricedTurns: 1})")
    assert "1 lượt chưa định giá" in single


def _overview_render_eval(node: str, expression: str) -> object:
    source = (
        "globalThis.document = { querySelector: () => null,"
        " querySelectorAll: () => [], body: {} };\n"
        f"const ov = await import('{OVERVIEW_JS.as_posix()}');\n"
        f"console.log(JSON.stringify(await ({expression})));\n"
    )
    result = subprocess.run(
        [node, "--input-type=module", "-e", source],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return json.loads(result.stdout)


def test_overview_aggregate_counts_tool_calls(node: str) -> None:
    rows = [
        {"requests": 2, "prompt_tokens": 100, "cached_tokens": 30,
         "completion_tokens": 10, "cost_usd": 0.01, "tool_calls": 3,
         "bot_breakdown": [
             {"bot_id": "a", "requests": 1, "prompt_tokens": 60,
              "cached_tokens": 10, "completion_tokens": 5,
              "cost_usd": 0.006, "tool_calls": 2},
             {"bot_id": "b", "requests": 1, "prompt_tokens": 40,
              "cached_tokens": 20, "completion_tokens": 5,
              "cost_usd": 0.004, "tool_calls": 1},
         ]},
        {"bot_id": "solo", "requests": 1, "prompt_tokens": 50,
         "cached_tokens": 0, "completion_tokens": 5, "cost_usd": None,
         "tool_calls": 4},
    ]
    totals = _overview_eval(
        node, f"ov.aggregateOverview({json.dumps(rows)})")
    assert totals["toolCalls"] == 7
    assert totals["input"] == 120  # prompt minus cache, one source
    assert totals["cache"] == 30
    by_bot = _overview_eval(
        node, f"ov.aggregateByBot({json.dumps(rows)})")
    tools = {g["botId"]: g["toolCalls"] for g in by_bot}
    assert tools == {"a": 2, "b": 1, "solo": 4}


def test_overview_summary_has_four_cells_without_turns(node: str) -> None:
    snap = (
        "{ totals: ov.aggregateOverview(["
        " {requests: 2, prompt_tokens: 100, cached_tokens: 30,"
        " completion_tokens: 10, cost_usd: 0.01, tool_calls: 3}]),"
        " rows: [], byBot: [], groups: [], models: [],"
        " roster: new Map(), rosterList: [], sessionMap: new Map() }"
    )
    html = _overview_render_eval(node, f"ov.renderChung({snap})")
    cells = re.findall(r'<div class="label"><span class="dot"></span>([^<]+)</div>', html)
    assert cells == ["Token", "Request", "Gọi tool", "Chi phí"]
    assert ">Lượt<" not in html
    assert "TB $0.0050 / request" in html
    # Copy cleanup: no migration note, and empty hints are dropped entirely
    # (Token keeps its split hint, Chi phí keeps the per-request average).
    assert "sec-note" not in html
    assert "Một màn duy nhất" not in html
    assert html.count('<div class="hint">') == 2
    # Chart legends keep only the token-segment explanation — no chooser
    # instructions for controls that are already visible.
    assert "Phần riêng từng bot · Token: vào · cache · ra" in html
    assert "Token: vào · cache · ra" in html
    assert "chọn chỉ số" not in html


def test_overview_bot_summary_has_tool_calls(node: str) -> None:
    snap = (
        "{ totals: ov.aggregateOverview(["
        " {bot_id: 'a', requests: 2, prompt_tokens: 100, cached_tokens: 30,"
        " completion_tokens: 10, cost_usd: 0.01, tool_calls: 3}]),"
        " rows: [],"
        " byBot: ov.aggregateByBot(["
        " {bot_id: 'a', requests: 2, prompt_tokens: 100, cached_tokens: 30,"
        " completion_tokens: 10, cost_usd: 0.01, tool_calls: 3}]),"
        " groups: [], models: [],"
        " roster: new Map([['a', {id: 'a', name: 'A'}]]),"
        " rosterList: [{id: 'a', name: 'A'}], sessionMap: new Map() }"
    )
    html = _overview_render_eval(node, f"ov.renderBot({snap}, 'a')")
    # Compact unboxed header — no 4-cell .summary in the bot view.
    assert 'class="summary"' not in html
    # The bot name is the page header now — no name/badge inside the view,
    # and no floating totals list above the Cách tính frame.
    assert 'bot-overview-name' not in html
    assert '>bot</span>' not in html
    assert 'bot-overview-total' not in html
    assert html.startswith('<div class="bot-cost"')
    # No configured rates in this snapshot: the Cách tính table falls
    # back to plain token counts with dash amounts; the model header
    # carries tokens · requests · recorded average, and the recorded
    # total sits in a separate footer with its share.
    assert '<p class="bot-cost-heading">Cách tính</p>' in html
    assert '110 token · 2 request · 3 tool · TB ghi nhận $0.0050/request' in html
    assert '<table class="bot-cost-table">' in html
    assert '<th class="bot-cost-label">Loại</th>' in html
    assert '<th class="bot-cost-expr">Token × đơn giá</th>' in html
    assert '<th class="bot-cost-amount">Thành tiền</th>' in html
    assert '<td class="bot-cost-label">Vào</td>' in html
    assert '<td class="bot-cost-expr">70 token</td>' in html
    assert '<td class="bot-cost-label">Cache</td>' in html
    assert '<td class="bot-cost-label">Ra</td>' in html
    assert '<td class="bot-cost-amount">—</td>' in html
    assert 'bot-cost-row' not in html
    # Recorded footer, distinct from the estimate table.
    assert 'class="bot-cost-recorded"' in html
    assert 'Đã ghi nhận' in html
    assert '$0.0100' in html
    assert '100% tổng chi phí' in html
    assert '(100%)' not in html
    assert 'Trung bình' not in html
    assert 'bot-cost-avg' not in html
    # Native disclosure shows the per-bot share of period totals.
    assert '<details class="bot-overview-details"><summary>Tỷ trọng</summary>' in html
    assert '<summary>Chi tiết</summary>' not in html
    assert '100% tổng token · 100% tổng request · 100% tổng gọi tool' in html
    assert 'Chỉ tính phần riêng của bot.' in html
    # Trace keeps a descriptive aria-label but the visible title is short.
    assert 'aria-label="Trace của A"' in html
    assert '<h3>Trace</h3>' in html
    assert 'Trace của A</h3>' not in html
    assert '20 lượt gần nhất' in html
    assert 'Chưa có lượt trong kỳ.' in html
    assert ">Lượt<" not in html


def test_overview_bot_cost_formula_uses_model_rates(node: str) -> None:
    """With configured rates the formula prices each token side with its
    model's rate (three dash-led lines); the total and the average stay
    on the recorded cost. A second model gets its own formula group."""
    rows = (
        "[{bot_id: 'a', model: 'm1', requests: 2, prompt_tokens: 100,"
        " cached_tokens: 30, completion_tokens: 10, cost_usd: 0.01},"
        " {bot_id: 'a', model: 'm2', requests: 1, prompt_tokens: 10,"
        " cached_tokens: 0, completion_tokens: 5, cost_usd: 0.02}]"
    )
    snap = (
        f"{{ totals: ov.aggregateOverview({rows}), rows: {rows},"
        f" byBot: ov.aggregateByBot({rows}), groups: [], models: [],"
        " config: {models: {m1: {input: 1, cache: 2, output: 3},"
        " m2: {input: 4, cache: 5, output: 6}}},"
        " roster: new Map([['a', {id: 'a', name: 'A'}]]),"
        " rosterList: [{id: 'a', name: 'A'}], sessionMap: new Map() }"
    )
    html = _overview_render_eval(node, f"ov.renderBot({snap}, 'a')")
    assert '<p class="bot-cost-heading">Cách tính</p>' in html
    assert '<span class="bot-cost-model-name">m1 · ước tính theo đơn giá hiện tại</span>' in html
    assert '110 token · 2 request · TB ghi nhận $0.0050/request' in html
    assert '<span class="bot-cost-model-name">m2 · ước tính theo đơn giá hiện tại</span>' in html
    assert '15 token · 1 request · TB ghi nhận $0.0200/request' in html
    # One table per model, exact integer counts in each expression cell.
    assert html.count('<table class="bot-cost-table">') == 2
    assert '<td class="bot-cost-expr">70 × $1/1M</td>' in html
    assert '<td class="bot-cost-amount">$0.00007</td>' in html
    assert '<td class="bot-cost-expr">30 × $2/1M</td>' in html
    assert '<td class="bot-cost-amount">$0.00006</td>' in html
    assert '<td class="bot-cost-expr">10 × $3/1M</td>' in html
    assert '<td class="bot-cost-amount">$0.00003</td>' in html
    assert '<td class="bot-cost-expr">10 × $4/1M</td>' in html
    # Recorded total ($0.03) lives in the footer below the estimates;
    # the recorded average lives in the model headers.
    assert 'class="bot-cost-recorded"' in html
    assert 'Đã ghi nhận' in html
    assert '<strong>$0.0300</strong>' in html
    assert '100% tổng chi phí' in html
    assert 'bot-cost-label">Tổng<' not in html
    assert 'Trung bình' not in html


def test_overview_bot_cost_estimate_differs_from_recorded(node: str) -> None:
    """Estimate rows price the recorded tokens at TODAY'S rates; the
    recorded total in the footer can legitimately differ — it must never
    be presented as a sum of the estimates."""
    rows = (
        "[{bot_id: 'a', model: 'm1', requests: 2, prompt_tokens: 100,"
        " cached_tokens: 30, completion_tokens: 10, cost_usd: 0.01}]"
    )
    snap = (
        f"{{ totals: ov.aggregateOverview({rows}), rows: {rows},"
        f" byBot: ov.aggregateByBot({rows}), groups: [], models: [],"
        " config: {models: {m1: {input: 1, cache: 2, output: 3}}},"
        " roster: new Map([['a', {id: 'a', name: 'A'}]]),"
        " rosterList: [{id: 'a', name: 'A'}], sessionMap: new Map() }"
    )
    html = _overview_render_eval(node, f"ov.renderBot({snap}, 'a')")
    assert 'ước tính theo đơn giá hiện tại' in html
    assert 'TB ghi nhận $0.0050/request' in html
    table = html.split('<table class="bot-cost-table">')[1].split('</table>')[0]
    assert '<td class="bot-cost-expr">70 × $1/1M</td>' in table
    assert '<td class="bot-cost-amount">$0.00007</td>' in table
    assert '<td class="bot-cost-amount">$0.00006</td>' in table
    assert '<td class="bot-cost-amount">$0.00003</td>' in table
    # The recorded figure is outside the table, labelled as recorded.
    assert '$0.0100' not in table
    recorded = html.split('class="bot-cost-recorded"')[1]
    assert 'Đã ghi nhận' in recorded
    assert '<strong>$0.0100</strong>' in recorded
    assert '100% tổng chi phí' in recorded


def test_overview_bot_cost_formula_integer_token_counts(node: str) -> None:
    """Expression cells show full integer token counts (formatInteger),
    never the compact K-abbreviations used for summary figures."""
    rows = (
        "[{bot_id: 'a', model: 'm1', requests: 2, prompt_tokens: 10340,"
        " cached_tokens: 7616, completion_tokens: 1083, cost_usd: 0.01}]"
    )
    snap = (
        f"{{ totals: ov.aggregateOverview({rows}), rows: {rows},"
        f" byBot: ov.aggregateByBot({rows}), groups: [], models: [],"
        " config: {models: {m1: {input: 1, cache: 1, output: 1}}},"
        " roster: new Map([['a', {id: 'a', name: 'A'}]]),"
        " rosterList: [{id: 'a', name: 'A'}], sessionMap: new Map() }"
    )
    html = _overview_render_eval(node, f"ov.renderBot({snap}, 'a')")
    table = html.split('<table class="bot-cost-table">')[1].split('</table>')[0]
    # splitPromptTokens: input = 10340 - 7616 = 2724 (vi-VN grouping).
    assert '<td class="bot-cost-expr">2.724 × $1/1M</td>' in table
    assert '<td class="bot-cost-expr">7.616 × $1/1M</td>' in table
    assert '<td class="bot-cost-expr">1.083 × $1/1M</td>' in table
    assert '10,34K' not in table and '7,62K' not in table


def test_overview_bot_model_tokens_groups_the_bot_slice(node: str) -> None:
    """Model chart data: input/cache/output grouped by model from the
    bot's own slice only, biggest model first."""
    rows = (
        "[{bot_id: 'a', model: 'm1', requests: 1, prompt_tokens: 100,"
        " cached_tokens: 30, completion_tokens: 10},"
        " {bot_id: 'b', model: 'm1', requests: 1, prompt_tokens: 900,"
        " cached_tokens: 0, completion_tokens: 90},"
        " {bot_id: 'a', model: 'm2', requests: 1, prompt_tokens: 50,"
        " cached_tokens: 10, completion_tokens: 5},"
        " {bot_id: 'a', model: 'm1', requests: 1, prompt_tokens: 20,"
        " cached_tokens: 0, completion_tokens: 2}]"
    )
    groups = _overview_eval(node, f"ov.botModelTokens({rows}, 'a')")
    assert [(g["model"], g["input"], g["cache"], g["output"]) for g in groups] == [
        ("m1", 90, 30, 12), ("m2", 40, 10, 5)]


def test_overview_bot_view_places_the_token_model_chart(node: str) -> None:
    """Bot view carries one horizontal stacked bar per model (no day
    columns); an empty period — no tokens — gets no chart."""
    rows = (
        "[{bot_id: 'a', model: 'm1', requests: 1, prompt_tokens: 100,"
        " cached_tokens: 30, completion_tokens: 10, cost_usd: 0.01},"
        " {bot_id: 'a', model: 'm2', requests: 1, prompt_tokens: 50,"
        " cached_tokens: 10, completion_tokens: 5, cost_usd: 0.02}]"
    )
    snap = (
        f"{{ totals: ov.aggregateOverview({rows}), rows: {rows},"
        f" byBot: ov.aggregateByBot({rows}), groups: [], models: [],"
        " roster: new Map([['a', {id: 'a', name: 'A'}]]),"
        " rosterList: [{id: 'a', name: 'A'}], sessionMap: new Map() }"
    )
    html = _overview_render_eval(node, f"ov.renderBot({snap}, 'a')")
    assert 'Token theo mô hình' in html
    assert 'lg-in">Vào<' in html
    assert '<span class="name mn">m1</span>' in html
    assert '<span class="name mn">m2</span>' in html
    assert 'bot-token-chart' not in html
    assert 'Token theo ngày' not in html
    # Chart sits between the details disclosure and the Trace panel.
    assert html.index('Token theo mô hình') > html.index('bot-overview-details')
    assert html.index('Token theo mô hình') < html.index('<h3>Trace</h3>')
    # Two models: shares and the stacked legend stay.
    assert '<small>67%</small>' in html
    assert 'bot-chart-legend' in html
    # One model only: nothing to compare — no chart at all (the split
    # already lives in the Cách tính table).
    one = (
        "{bot_id: 'a', model: 'm1', requests: 1, prompt_tokens: 100,"
        " cached_tokens: 30, completion_tokens: 10, cost_usd: 0.01}"
    )
    snap_one = (
        f"{{ totals: ov.aggregateOverview([{one}]), rows: [{one}],"
        f" byBot: ov.aggregateByBot([{one}]), groups: [], models: [],"
        " roster: new Map([['a', {id: 'a', name: 'A'}]]),"
        " rosterList: [{id: 'a', name: 'A'}], sessionMap: new Map() }"
    )
    html_one = _overview_render_eval(node, f"ov.renderBot({snap_one}, 'a')")
    assert 'Token theo mô hình' not in html_one
    assert 'bot-chart' not in html_one


def test_dashboard_overview_has_a_visible_screen_heading() -> None:
    """Like the other screens, the Dashboard overview shows a real
    screen-heading; the h1 names the entity (Tổng quan by default, the
    bot/group when selected — set from overview.js render())."""
    html = DASH_HTML.read_text(encoding="utf-8")
    assert '<header class="screen-heading">' in html
    assert '<h1 id="overview-heading">Tổng quan</h1>' in html
    assert 'id="overview-heading" hidden' not in html
    assert html.index('class="screen-heading"') < html.index('class="headrow"')
    overview = (WEBUI / "pages" / "dashboard" / "overview.js").read_text(encoding="utf-8")
    assert 'el.heading.textContent' in overview
    assert 'rosterName(selection.id, snapshot.roster)' in overview


def test_overview_bot_trace_row_keeps_token_only_at_right(node: str) -> None:
    """Bot trace rows: the meta line carries only status (no repeated token
    count); the right cell keeps duration · token · cost with a scope title,
    and the deeplink attrs stay intact."""
    row = (
        "{session_id: 's1', turn_index: 0, started_at: '2026-10-01T10:00:00Z',"
        " title: 'chat', model: 'm1', status: 'ok', bot_id: 'a', requests: 1,"
        " prompt_tokens: 10, cached_tokens: 0, completion_tokens: 5,"
        " cost_usd: 0.001, latency_ms: 1200, tool_calls: 0}"
    )
    snap = (
        f"{{ totals: ov.aggregateOverview([{row}]), rows: [{row}],"
        f" byBot: ov.aggregateByBot([{row}]), groups: [], models: [],"
        " roster: new Map([['a', {id: 'a', name: 'A'}]]),"
        " rosterList: [], sessionMap: new Map() }"
    )
    html = _overview_render_eval(node, f"ov.renderBot({snap}, 'a')")
    assert "token phần của bot" not in html
    assert '<span class="tmeta"><span class="sdot"></span>Thành công</span>' in html
    assert 'title="Token phần riêng của bot"' in html
    assert '15 token' in html
    assert 'data-session="s1" data-turn="0"' in html


def test_overview_bot_header_empty_period(node: str) -> None:
    """Bot without period data: the Cách tính frame has no model rows,
    the total/share render as em dash — never NaN or Infinity."""
    snap = (
        "{ totals: ov.aggregateOverview([]), rows: [], byBot: [],"
        " groups: [], models: [],"
        " roster: new Map([['a', {id: 'a', name: 'A'}]]),"
        " rosterList: [{id: 'a', name: 'A'}], sessionMap: new Map() }"
    )
    html = _overview_render_eval(node, f"ov.renderBot({snap}, 'a')")
    assert '<p class="bot-cost-heading">Cách tính</p>' in html
    assert 'bot-cost-table' not in html
    assert 'bot-cost-model' not in html
    assert 'Token theo mô hình' not in html
    assert '0 tool' not in html
    assert '<strong>—</strong>' in html
    assert 'bot-cost-share' not in html
    assert 'Trung bình' not in html
    assert 'chi phí chung' not in html
    assert '— tổng token · — tổng request · — tổng gọi tool' in html
    assert "NaN" not in html and "Infinity" not in html


def test_overview_bot_header_null_cost_is_not_zero_percent(node: str) -> None:
    """An unpriced bot amid priced totals shows em dash, not a fake 0%
    of the period cost — while a real zero cost renders as $0.0000."""
    rows = (
        "[{bot_id: 'a', requests: 1, prompt_tokens: 40, cached_tokens: 0,"
        " completion_tokens: 10},"
        " {bot_id: 'b', requests: 1, prompt_tokens: 10, cached_tokens: 0,"
        " completion_tokens: 2, cost_usd: 0.05}]"
    )
    snap = (
        f"{{ totals: ov.aggregateOverview({rows}), rows: [],"
        f" byBot: ov.aggregateByBot({rows}), groups: [], models: [],"
        " roster: new Map([['a', {id: 'a', name: 'A'}],"
        " ['b', {id: 'b', name: 'B'}]]),"
        " rosterList: [], sessionMap: new Map() }"
    )
    html = _overview_render_eval(node, f"ov.renderBot({snap}, 'a')")
    assert '<strong>—</strong>' in html
    assert 'bot-cost-share' not in html
    assert '(0%)' not in html
    assert "NaN" not in html and "Infinity" not in html
    zero = (
        "[{bot_id: 'a', requests: 1, prompt_tokens: 40, cached_tokens: 0,"
        " completion_tokens: 10, cost_usd: 0}]"
    )
    snap_zero = (
        f"{{ totals: ov.aggregateOverview({zero}), rows: [],"
        f" byBot: ov.aggregateByBot({zero}), groups: [], models: [],"
        " roster: new Map([['a', {id: 'a', name: 'A'}]]),"
        " rosterList: [], sessionMap: new Map() }"
    )
    html_zero = _overview_render_eval(node, f"ov.renderBot({snap_zero}, 'a')")
    assert '<strong>$0.0000</strong>' in html_zero
    assert 'TB ghi nhận $0.0000/request' in html_zero
    assert 'Trung bình' not in html_zero


def test_overview_bot_header_escapes_name(node: str) -> None:
    """A malicious or long bot name is escaped everywhere it renders."""
    snap = (
        "{ totals: ov.aggregateOverview([]), rows: [], byBot: [],"
        " groups: [], models: [],"
        " roster: new Map([['a', {id: 'a',"
        " name: '<img src=x onerror=1> ' + 'd'.repeat(120)}]]),"
        " rosterList: [], sessionMap: new Map() }"
    )
    html = _overview_render_eval(node, f"ov.renderBot({snap}, 'a')")
    assert "<img src=x onerror=1>" not in html
    assert "&lt;img src=x onerror=1&gt;" in html
    assert 'aria-label="Trace của &lt;img' in html


def test_overview_agent_chart_metric_switch(node: str) -> None:
    """The Chung agent panel is one horizontal bar chart with a 4-way
    metric switch (Token default): rank + avatar + tinted bar per row,
    sorted by the active metric; the leader gets the badge; token mode
    stacks Input/Cache/Output; cost mode re-sorts with solid bars."""
    snap = (
        "{ totals: ov.aggregateOverview(["
        " {bot_id: 'a', requests: 4, prompt_tokens: 100, cached_tokens: 30,"
        " completion_tokens: 20, cost_usd: 0.05, tool_calls: 1},"
        " {bot_id: 'b', requests: 1, prompt_tokens: 10, cached_tokens: 0,"
        " completion_tokens: 2, cost_usd: 0.001, tool_calls: 6},"
        " {bot_id: null, requests: 1, prompt_tokens: 5, cached_tokens: 0,"
        " completion_tokens: 1, cost_usd: 0.002, tool_calls: 0}]),"
        " rows: [],"
        " byBot: ov.aggregateByBot(["
        " {bot_id: 'a', requests: 4, prompt_tokens: 100, cached_tokens: 30,"
        " completion_tokens: 20, cost_usd: 0.05, tool_calls: 1},"
        " {bot_id: 'b', requests: 1, prompt_tokens: 10, cached_tokens: 0,"
        " completion_tokens: 2, cost_usd: 0.001, tool_calls: 6},"
        " {bot_id: null, requests: 1, prompt_tokens: 5, cached_tokens: 0,"
        " completion_tokens: 1, cost_usd: 0.002, tool_calls: 0}]),"
        " groups: [], models: [],"
        " roster: new Map([['a', {id: 'a', name: 'A', tint: ''}],"
        " ['b', {id: 'b', name: 'B', tint: 'note-re'}]]),"
        " rosterList: [], sessionMap: new Map() }"
    )
    html = _overview_render_eval(node, f"ov.renderAgentChart({snap})")
    # Segmented control: 4 pressed-state options, Token active.
    assert len(re.findall(r'data-metric="(token|cost|requests|tools)"', html)) == 4
    assert 'data-metric="token" aria-pressed="true"' in html
    assert "so với chung" not in html
    assert "agent-table" not in html
    # Token mode: sorted by tokens desc, leader badged, stacked bar.
    names = re.findall(r'<span class="name">([^<]+)</span>', html)
    assert names == ["A", "B", "Chưa gắn bot"]
    assert 'A</span><span class="badge top">cao nhất</span>' in html
    stacks = re.findall(
        r'<i class="seg-in" style="width:([^%]+)%"></i>'
        r'<i class="seg-cache" style="width:([^%]+)%"></i>'
        r'<i class="seg-out" style="width:([^%]+)%"></i>',
        html,
    )
    assert len(stacks) == 3
    first = [float(v) for v in stacks[0]]
    assert first == pytest.approx([70 / 138 * 100, 30 / 138 * 100, 20 / 138 * 100])
    assert "4 request · 1 gọi tool" in html
    # Cost mode: re-sorted by cost (unknown beats b), solid tinted bars,
    # badge moves to the cost leader, no stacked segments.
    cost = _overview_render_eval(node, f"ov.renderAgentChart({snap}, 'cost')")
    assert re.findall(r'<span class="name">([^<]+)</span>', cost) == [
        "A", "Chưa gắn bot", "B"]
    assert 'A</span><span class="badge top">cao nhất</span>' in cost
    assert 'class="metric-bar"' in cost
    assert "token-stack" not in cost
    # Unattributed row: empty slot, neutral tint fallback.
    assert "Chưa gắn bot" in html
    assert "bot-avatar-slot is-empty" in html
    chung = _overview_render_eval(node, f"ov.renderChung({snap})")
    assert "so với chung" not in chung
    assert 'class="agent-chart"' in chung
    bot = _overview_render_eval(node, f"ov.renderBot({snap}, 'a')")
    assert "so với chung" not in bot
    assert "<h3>Trace</h3>" in bot
    assert "Trace của A</h3>" not in bot
    assert "1 tool" in bot
