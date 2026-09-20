"""Tests for the Dashboard four-view contract (GOAL-010 TASK-028/029,
TASK-039).

The "Hôm nay" overview view is gone: the sidebar is Sử dụng token / Request /
Chi phí / Trace, the default view is Sử dụng token, the legacy #hom-nay hash
(and any unknown hash) falls back to Sử dụng token, and ?session=/?turn=
deep links open Trace. dashboard-today.js keeps only the /api/traces paging
helper consumed by the Cost journal (fetchAllTraces: dedupe on
(session_id, turn_index), explicit incomplete error). The boot behavior runs
against a minimal fake DOM in Node; static checks pin the navigation menu and
the flat-surface/mobile switching contract. Mirrors the eval style of
test_webui_format.py.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEBUI = ROOT / "thyca" / "webui"
SCRIPT = WEBUI / "backend" / "dashboard-today.js"
DASH_JS = WEBUI / "dashboard.js"
DASH_HTML = WEBUI / "dashboard.html"
NAV_JS = WEBUI / "navigation.js"


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
    view state. `actions` is a JSON array of {type: "click", view} /
    {type: "hashchange", hash} replayed after boot. The cache-busting query
    gives every boot a fresh module instance."""
    script = """
    const blocks = Object.fromEntries(
      ["request", "usage", "cost", "trace"].map((view) => {{
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
    const buttons = ["request", "usage", "trace", "cost"].map((view) => {{
      const el = {{
        dataset: {{ view }},
        attrs: {{}},
        classes: new Set(),
        listeners: {{}},
        classList: {{
          toggle(cls, on) {{ if (on) el.classes.add(cls); else el.classes.delete(cls); }},
        }},
        setAttribute(name, value) {{ el.attrs[name] = value; }},
        addEventListener(type, fn) {{ el.listeners[type] = fn; }},
      };
      return el;
    }});
    const bySelector = {{
      "#request": blocks.request,
      "#su-dung": blocks.usage,
      "#chi-phi": blocks.cost,
      "#trace": blocks.trace,
      ".dashboard-surface": surface,
    }};
    globalThis.document = {{
      querySelector: (sel) => bySelector[sel] || null,
      querySelectorAll: (sel) => (sel === "[data-view]" ? buttons : []),
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
      if (action.type === "click") {{
        buttons.find((b) => b.dataset.view === action.view).listeners.click();
      }} else if (action.type === "hashchange") {{
        location.hash = action.hash;
        windowListeners.hashchange();
      }}
    }}
    const visible = Object.entries(blocks).filter(([, b]) => !b.hidden).map(([k]) => k);
    const active = buttons.filter((b) => b.classes.has("is-active")).map((b) => b.dataset.view);
    const pressed = Object.fromEntries(buttons.map((b) => [b.dataset.view, b.attrs["aria-pressed"]]));
    process.stdout.write(JSON.stringify({{ visible, active, pressed, hash: location.hash,
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


def test_dashboard_sidebar_has_exactly_four_views() -> None:
    """TASK-029/036/039: the first Dashboard subview is gone; the sidebar is
    exactly Sử dụng token / Request / Chi phí / Trace (no decorative numeric
    prefixes), with Sử dụng token active initially, and no today block/markup
    survives."""
    html = DASH_HTML.read_text(encoding="utf-8")
    assert 'data-view="today"' not in html
    assert 'id="hom-nay"' not in html
    assert 'id="today-metrics"' not in html
    assert 'id="today-recent"' not in html
    assert 'id="today-status"' not in html
    assert '<span class="session-name">Sử dụng token</span>' in html
    assert '<span class="session-name">Request</span>' in html
    assert '<span class="session-name">Chi phí</span>' in html
    assert '<span class="session-name">Trace</span>' in html
    # TASK-036: decorative numeric prefixes are gone; step numbers and the
    # x/y pager live elsewhere and are untouched.
    assert "01 / " not in html
    assert "02 / " not in html
    order = [
        html.index('data-view="usage"'),
        html.index('data-view="request"'),
        html.index('data-view="cost"'),
        html.index('data-view="trace"'),
    ]
    assert order == sorted(order)
    # Sử dụng token starts active so the static markup matches the JS default.
    assert 'class="session-item is-active" type="button" data-view="usage"' in html
    assert 'class="session-item is-active" type="button" data-view="today"' not in html
    # TASK-039: the cost range line is gone from the markup.
    assert 'id="cost-range"' not in html


def test_dashboard_boot_defaults_to_usage(node: str) -> None:
    state = _boot_dashboard(node)
    assert state["visible"] == ["usage"]
    assert state["active"] == ["usage"]
    assert state["pressed"] == {"request": "false", "usage": "true", "trace": "false", "cost": "false"}


def test_dashboard_boot_sends_legacy_and_unknown_hashes_to_request(node: str) -> None:
    """#hom-nay and any unknown hash fall back to Sử dụng token, and the URL
    is rewritten to its hash."""
    for hash in ("#hom-nay", "#khong-hop-le"):
        state = _boot_dashboard(node, hash=hash)
        assert state["visible"] == ["usage"]
        assert state["hash"] == "#su-dung"


def test_dashboard_boot_opens_the_matching_hash(node: str) -> None:
    for view, hash in (("usage", "#su-dung"), ("cost", "#chi-phi"), ("trace", "#trace")):
        state = _boot_dashboard(node, hash=hash)
        assert state["visible"] == [view], hash
        assert state["hash"] == hash


def test_dashboard_boot_opens_trace_for_deep_links(node: str) -> None:
    """A ?session=/?turn= deep link always opens Trace — with or without the
    #trace hash — and never the default Sử dụng token view. The deep-link
    params stay on the URL."""
    for search in ("?session=s1&turn=2", "?session=s1", "?turn=0"):
        state = _boot_dashboard(node, search=search)
        assert state["visible"] == ["trace"], search
        assert state["search"] == search, search
    state = _boot_dashboard(node, hash="#trace", search="?session=s1&turn=2")
    assert state["visible"] == ["trace"]
    assert state["hash"] == "#trace"  # already correct: no rewrite
    assert state["search"] == "?session=s1&turn=2"


def test_dashboard_drops_trace_params_when_leaving_trace(node: str) -> None:
    """Switching from a deep-linked Trace to another view strips ?session= and
    ?turn= in the same replaceState that lands the new hash, so the URL always
    matches the screen and Back returns to the caller page (replaceState, not
    pushState)."""
    state = _boot_dashboard(
        node,
        search="?session=s1&turn=2",
        actions='[{"type": "click", "view": "cost"}]',
    )
    assert state["visible"] == ["cost"]
    assert state["hash"] == "#chi-phi"
    assert state["search"] == ""
    assert state["urls"][-1] == "/dashboard.html#chi-phi"


def test_dashboard_keeps_other_query_params_when_leaving_trace(node: str) -> None:
    """Only the Trace deep-link params are dropped; anything else on the query
    survives the view switch."""
    state = _boot_dashboard(
        node,
        search="?session=s1&keep=1",
        actions='[{"type": "click", "view": "usage"}]',
    )
    assert state["visible"] == ["usage"]
    assert state["search"] == "?keep=1"
    assert state["urls"][-1] == "/dashboard.html?keep=1#su-dung"


def test_trace_period_has_the_unfiltered_window_option() -> None:
    """The Trace period filter offers the unfiltered API window (200 newest
    session files) so history older than the rolling ranges stays reachable;
    the other views' filters are unchanged."""
    html = DASH_HTML.read_text(encoding="utf-8")
    assert '<option value="all">Toàn bộ cửa sổ (200 phiên mới nhất)</option>' in html
    assert html.count('value="all"') == 1


def test_dashboard_navigation_clicks_and_hashchange(node: str) -> None:
    """Clicking a sidebar entry and a later hashchange both switch the visible
    view (the deep-link boot state is not sticky)."""
    state = _boot_dashboard(node, actions='[{"type": "click", "view": "cost"}]')
    assert state["visible"] == ["cost"]
    assert state["active"] == ["cost"]
    state = _boot_dashboard(
        node,
        actions='[{"type": "click", "view": "cost"}, {"type": "hashchange", "hash": "#trace"}]',
    )
    assert state["visible"] == ["trace"]
    assert state["active"] == ["trace"]
    assert state["hash"] == "#trace"


def test_dashboard_hash_switcher_covers_exactly_the_four_views() -> None:
    dash = DASH_JS.read_text(encoding="utf-8")
    assert '"#hom-nay"' not in dash
    assert '"today"' not in dash
    for hash in ("#request", "#su-dung", "#chi-phi", "#trace"):
        assert f'"{hash}"' in dash
    assert 'const DEFAULT_VIEW = "usage";' in dash
    assert 'node.hidden = key !== next' in dash
    assert "stack ? false" not in dash
    # Deep-link detection covers both deep-link params.
    assert 'params.has("session") || params.has("turn")' in dash


def test_dashboard_switches_views_on_every_viewport() -> None:
    """Actual switching on mobile too; the sidebar must opt back into the
    shared 56rem rule that hides .sidebar > *."""
    dash_css = (WEBUI / "dashboard.css").read_text(encoding="utf-8")
    assert ".dashboard-shell .sessions" in dash_css
    assert "display: flex !important" in dash_css


def test_dashboard_sections_are_flat_and_errors_use_scoped_red() -> None:
    dash_css = (WEBUI / "dashboard.css").read_text(encoding="utf-8")
    screens_css = (WEBUI / "screens.css").read_text(encoding="utf-8")
    # Decorative card background/radius/shadow go; no global root token change.
    assert ".dashboard-section .screen-card" in dash_css
    assert "background: transparent" in dash_css
    assert "--journal-error-ink:" in screens_css
    assert "color: var(--journal-error-ink)" in screens_css
    assert screens_css.count("--journal-error-ink:") == 1


def test_today_only_helpers_are_gone_but_paging_stays_for_cost() -> None:
    """dashboard-today.js keeps only the /api/traces paging helper; the Cost
    journal still imports it (tests/test_cost_journal.py pins the behavior)."""
    helper = SCRIPT.read_text(encoding="utf-8")
    for gone in ("todaySummary", "dayKeyInZone", "periodRange", "rowsInPeriod", "formatRunStamp", "recentTraces"):
        assert gone not in helper
    assert "export async function fetchAllTraces" in helper
    cost = (WEBUI / "cost.js").read_text(encoding="utf-8")
    assert 'import { fetchAllTraces } from "./backend/dashboard-today.js";' in cost


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
