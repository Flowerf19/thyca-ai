"""Chat markdown: GFM tables and safe HTML via marked."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEBUI = ROOT / "thyca" / "webui"
SHARED_JS = WEBUI / "shared" / "js"
MARKDOWN_JS = SHARED_JS / "markdown.js"
MEMORY_DATA = SHARED_JS / "memory-data.js"
TRACE_DATA = WEBUI / "pages" / "dashboard" / "trace-data.js"
ANALYTICS_DATA = SHARED_JS / "analytics-data.js"
FORMAT_JS = SHARED_JS / "format.js"


def _render(src: str) -> str:
    script = f"""
    import {{ formatMarkdown }} from {json.dumps(MARKDOWN_JS.as_uri())};
    process.stdout.write(formatMarkdown({json.dumps(src)}));
    """
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def test_table_renders_cells() -> None:
    html = _render("| Phần | Ý nghĩa |\n|---|---|\n| `u` | timestamp |\n| c | CDR |")
    assert '<div class="md-table-wrap"><table>' in html
    assert "<th>Phần</th>" in html
    assert "<code>u</code>" in html
    assert "<td>CDR</td>" in html


def test_heading_fence_and_break() -> None:
    html = _render("## Title\n\nline one\nline two\n\n```\necho hi\n```")
    assert "<h2>Title</h2>" in html
    assert "<br>" in html
    assert "<pre>" in html
    assert "echo hi" in html


def test_escapes_raw_html_and_unsafe_url() -> None:
    html = _render("click <script>alert(1)</script> [x](javascript:alert(1))")
    assert "<script>" not in html
    assert "javascript:" not in html
    assert "&lt;script&gt;" in html
    assert ">x</a>" not in html


def test_chat_js_uses_formatter() -> None:
    view = (WEBUI / "pages" / "chat" / "chat-view.js").read_text(encoding="utf-8")
    css = (WEBUI / "shared" / "css" / "backend.css").read_text(encoding="utf-8")
    assert 'from "../../shared/js/markdown.js"' in view
    assert "formatMarkdown(segment.content)" in view
    assert ".md-table-wrap" in css
    assert (WEBUI / "vendor" / "marked.esm.js").is_file()


def test_backend_mapping_modules_are_node_clean() -> None:
    script = f"""
    import {json.dumps(FORMAT_JS.as_uri())};
    import {json.dumps(MEMORY_DATA.as_uri())};
    import {json.dumps(TRACE_DATA.as_uri())};
    import {json.dumps(ANALYTICS_DATA.as_uri())};
    process.stdout.write("backend-modules-ok");
    """
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout == "backend-modules-ok"


def test_memory_and_trace_mappers_import_clean() -> None:
    script = f"""
    import {{ selectMemories }} from {json.dumps(MEMORY_DATA.as_uri())};
    import {{ groupTraceTurns }} from {json.dumps(TRACE_DATA.as_uri())};
    if (typeof selectMemories !== "function") throw new Error("no memory mapper");
    if (typeof groupTraceTurns !== "function") throw new Error("no trace mapper");
    process.stdout.write("mappers-ok");
    """
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout == "mappers-ok"


def test_select_memories_orders_backend_leaves() -> None:
    script = f"""
    import {{ selectMemories }} from {json.dumps(MEMORY_DATA.as_uri())};
    const leaves = [
      {{ get_count: 1, search_count: 0, chunk_id: "a", heading: "a" }},
      {{ get_count: 9, search_count: 1, chunk_id: "b", heading: "b" }},
      {{ get_count: 0, search_count: 4, chunk_id: "c", heading: "c" }},
    ];
    const get = selectMemories(leaves, {{ view: "used-more" }}).map((l) => l.id);
    const search = selectMemories(leaves, {{ view: "searched-more" }}).map((l) => l.id);
    const least = selectMemories(leaves, {{ view: "used-less" }}).map((l) => l.id);
    process.stdout.write(JSON.stringify({{ get, search, least }}));
    """
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert payload["get"] == ["b", "a", "c"]
    assert payload["search"] == ["c", "b", "a"]
    assert payload["least"] == ["a", "c", "b"]


def test_select_models_filters_and_orders_by_cost() -> None:
    """"Chi phí theo mô hình": search by name, then rank.

    A model with no configured price cannot be ranked by cost, so it sinks to
    the end in both directions instead of reading as the cheapest. A bucket
    with no requests and no tokens has nothing to show, so it never becomes a
    row (measured: the backend emits an all-zero "unknown" bucket).
    """
    rows = [
        {"model": "gpt-5.6-luna", "cost_usd": 2.4, "requests": 12, "total_tokens": 3400, "last_started_at": "2026-09-14T09:00:00Z"},
        {"model": "muse-spark-1.2-contributor", "cost_usd": 0.9, "requests": 4, "total_tokens": 900, "last_started_at": "2026-09-12T10:00:00Z"},
        {"model": "foo/bar", "cost_usd": None, "requests": 2, "total_tokens": 150, "last_started_at": "2026-09-14T11:00:00Z"},
        {"model": "gpt-4o-mini", "cost_usd": 0.2, "requests": 30, "total_tokens": 12000, "last_started_at": "2026-09-01T08:00:00Z"},
        {"model": "unknown", "cost_usd": None, "requests": 0, "total_tokens": 0, "last_started_at": "2026-09-14T12:00:00Z"},
    ]
    script = f"""
    import {{ selectModels }} from {json.dumps(ANALYTICS_DATA.as_uri())};
    const rows = {json.dumps(rows)};
    const names = (sort, query) => selectModels(rows, {{ sort, query }}).map((r) => r.model);
    process.stdout.write(JSON.stringify({{
      desc: names("cost-desc", ""),
      asc: names("cost-asc", ""),
      recent: names("recent", ""),
      upper: names("cost-desc", "GPT"),
      miss: names("cost-desc", "zzz"),
    }}));
    """
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert payload["desc"] == ["gpt-5.6-luna", "muse-spark-1.2-contributor", "gpt-4o-mini", "foo/bar"]
    assert payload["asc"] == ["gpt-4o-mini", "muse-spark-1.2-contributor", "gpt-5.6-luna", "foo/bar"]
    assert payload["recent"] == ["foo/bar", "gpt-5.6-luna", "muse-spark-1.2-contributor", "gpt-4o-mini"]
    # Search is a name match, case-insensitive.
    assert payload["upper"] == ["gpt-5.6-luna", "gpt-4o-mini"]
    assert payload["miss"] == []
    # The all-zero "unknown" bucket is noise: filtered out in every order.
    assert "unknown" not in payload["desc"] + payload["asc"] + payload["recent"]


def test_split_prompt_tokens_removes_cache() -> None:
    """prompt_tokens already contains the cached part, so a panel that prints
    both raw double-counts the input side (measured: 1000 + 800 for a prompt of
    1000 with 800 cached)."""
    script = f"""
    import {{ splitPromptTokens }} from {json.dumps(ANALYTICS_DATA.as_uri())};
    process.stdout.write(JSON.stringify({{
      split: splitPromptTokens(1000, 800),
      noCache: splitPromptTokens(1000, 0),
      missing: splitPromptTokens(1000, null),
      clamped: splitPromptTokens(100, 500),
      absent: splitPromptTokens(null, null),
    }}));
    """
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert payload["split"] == {"input": 200, "cache": 800}
    assert payload["noCache"] == {"input": 1000, "cache": 0}
    assert payload["missing"] == {"input": 1000, "cache": 0}
    # A provider that over-reports cache must not push input negative.
    assert payload["clamped"] == {"input": 0, "cache": 100}
    assert payload["absent"] == {"input": 0, "cache": 0}


def test_trace_typography_matches_profile_screen() -> None:
    """Trace is a view inside dashboard.html (TASK-025/026): the #trace block
    renders the session list in the main area and one journal entry per turn
    with a single "Xem các bước & dữ liệu" disclosure (numbered steps, one
    Input/Output block); trace.html survives only as a param-preserving
    redirect and trace.css is scoped to #trace."""
    trace = (WEBUI / "pages" / "dashboard" / "trace.css").read_text(encoding="utf-8")
    profile = (WEBUI / "pages" / "profile" / "profile.css").read_text(encoding="utf-8")
    shared = (WEBUI / "shared" / "css" / "screens.css").read_text(encoding="utf-8")
    html = (WEBUI / "dashboard.html").read_text(encoding="utf-8")
    redirect = (WEBUI / "trace.html").read_text(encoding="utf-8")
    script = (WEBUI / "pages" / "dashboard" / "trace.js").read_text(encoding="utf-8")

    # Trace lives in dashboard.html as a switched view next to the other
    # dashboard blocks; the sidebar has a data-view="trace" toggle.
    assert '<section class="dashboard-block" id="trace"' in html
    assert 'data-view="trace"' in html
    assert 'id="trace-sessions"' in html
    assert 'id="trace-turns"' in html
    # Old trace.html links keep working: a thin redirect that preserves the
    # deep-link params and loads no app script of its own.
    assert 'location.replace("./dashboard.html" + location.search + "#trace")' in redirect
    assert "./trace.js" not in redirect
    assert '<noscript><meta http-equiv="refresh" content="0; url=./dashboard.html#trace"></noscript>' in redirect

    # Typography still matches the profile screen through the shared
    # .journal-* kit (Source Serif body, Fraunces display) scoped to the
    # dashboard shell; trace.css carries no private font rules of its own.
    assert ":is(.dashboard-shell, .trace-shell) .journal-value {" in shared
    assert ":is(.dashboard-shell, .trace-shell) .journal-row-title {" in shared
    assert "--font-display" not in trace
    assert "font-family: var(--font-reading)" not in trace
    assert "#canonical-content" in profile
    # trace.css is scoped to the #trace block only.
    assert "#trace .trace-meta {" in trace
    assert "#trace .trace-turn-fold {" in trace
    assert "#trace .trace-steps {" in trace
    assert "#trace .trace-step {" in trace
    assert "#trace .trace-io {" in trace
    assert "#trace .trace-code {" in trace
    assert "flex-wrap: wrap;" in trace

    # The session list renders in the MAIN AREA of the view (no session
    # sidebar); selecting a session renders the full turn journal, one
    # .journal-entry per turn with per-turn view state keyed by
    # `${sessionId}:${turn_index}`.
    assert 'sessions: document.querySelector("#trace-sessions")' in script
    assert "turns: new Map()," in script
    assert 'item.className = "journal-entry trace-turn"' in script
    assert 'summaryLine.textContent = "Xem các bước & dữ liệu"' in script
    # Errors are marked on the line via the shared status kit.
    assert 'status.className = "journal-status is-error"' in script

    # Inside the disclosure: numbered steps "01 — Tên bước" with absolute
    # indices, tool rows indented under the round that issued them, and ONE
    # labelled Input/Output code block at the end (textContent only).
    assert 'num.className = "trace-step-num"' in script
    assert 'num.textContent = String(index + 1).padStart(2, "0")' in script
    assert 'item.classList.add("is-nested")' in script
    assert 'wrap.className = "trace-io"' in script
    assert '["Input", firstUserText(detail)]' in script
    assert '["Output", finalAssistantText(detail)]' in script
    assert 'pre.className = "trace-code"' in script
    assert "executionStepsFromDetail(detail)" in script

    # The old standalone-trace furniture is gone everywhere.
    assert ".trace-tool-dialog" not in trace
    assert ".trace-record-card" not in trace
    assert ".trace-section" not in trace
    assert ".activity-log" not in trace
    assert "trace-dot" not in script
    assert "renderProgress" not in script
    assert "record-flow" not in script
    assert "turn-progress" not in script
    assert "tool-dialog" not in script
    # The lighter mark + arrow fold furniture is one shared kit now.
    assert ".fold-section > summary :is(h2, h3)::before" in shared


def test_overview_typography_matches_profile_screen() -> None:
    overview = (WEBUI / "pages" / "dashboard" / "dashboard.css").read_text(encoding="utf-8")

    assert ".dashboard-surface {" in overview
    assert "font-family: var(--font-reading);" in overview
    # Inner cards are flattened (no private h3 rule): section headings keep
    # the Fraunces display face, and section cards render on the shell —
    # hairline border and focus rings stay, paper does not.
    assert ".dashboard-surface .screen-card h3" not in overview
    assert ".dashboard-surface .cost-model-heading {" in overview
    assert "font-family: var(--font-display);" in overview
    # TASK-036 removed the cost-model-cost count (no more per-model cards);
    # the remaining primary-amount selectors keep the reading size.
    assert ".dashboard-surface .cost-period," in overview
    assert "font-size: 1rem;" in overview
    assert ".dashboard-section .screen-card {" in overview
    assert "background: transparent;" in overview
    assert "border-radius: 0;" in overview
    assert "box-shadow: none;" in overview


def test_cost_panel_renders_bar_rows_and_uses_shared_toolbar() -> None:
    """Chi phí is a snapshot journal now: one .journal-entry per model and per
    session on the shared .journal-* kit (title + amount, share meter, meta
    line), and the toolbar reuses the shared search and filter-pill classes
    instead of private copies."""
    script = (WEBUI / "pages" / "dashboard" / "cost.js").read_text(encoding="utf-8")
    html = (WEBUI / "dashboard.html").read_text(encoding="utf-8")
    shared = (WEBUI / "shared" / "css" / "screens.css").read_text(encoding="utf-8")
    memories = (WEBUI / "memories.html").read_text(encoding="utf-8")
    memories_css = (WEBUI / "pages" / "memories" / "memories.css").read_text(encoding="utf-8")

    # Rows come from the snapshot's by_model ranked by the shared selectModels
    # with the live search query; the share denominator is fixed on the full
    # snapshot before filtering.
    assert 'selectModels(models, { sort, query: el.search?.value || "" })' in script
    assert "knownCostTotal(models)" in script

    # One journal entry per model and per session: dated gutter, title +
    # amount, share meter, and a meta line with the Input/Cache/Output split
    # from the bảng. Per-model rates fold into a <details> marked Đơn giá.
    assert 'className = "journal-entry"' in script
    assert 'className = "journal-date"' in script
    assert 'className = "journal-row-title"' in script
    assert 'className = "journal-amount"' in script
    assert 'className = "journal-meter"' in script
    assert 'className = "journal-meta"' in script
    assert 'fill.style.setProperty("--share", ratio)' in script
    assert "splitPromptTokens(model.prompt_tokens, model.cached_tokens)" in script
    assert 'details.className = "cost-pricing"' in script
    assert 'summary.textContent = "Đơn giá"' in script

    # The journal look comes from the shared screens.css kit, not a private
    # cost.css copy.
    assert '<ul class="journal" id="cost-models">' in html
    assert ":is(.dashboard-shell, .trace-shell) .journal-meter > span {" in shared
    assert ":is(.dashboard-shell, .trace-shell) .journal-row-title {" in shared

    # The toolbar markup is the shared shape, wired to the three orders and
    # the #model-search input.
    assert 'class="screen-filter-row"' in html
    assert 'class="thyca-search thyca-search--sm"' in html
    assert 'id="model-search"' in html
    for sort in ("cost-desc", "cost-asc", "recent"):
        assert f'data-sort="{sort}"' in html

    # Nhật ký switched to the promoted classes rather than keeping a copy.
    assert 'class="memory-search"' not in memories
    assert "memory-filters" not in memories_css
    assert ".sidebar .memory-search-cluster" in memories_css

    # Nhật ký keeps the promoted classes; Chi phí left the shared card kit —
    # one grouped kit rule in screens.css for memory only, no private copy in
    # cost.css (which owns the bar rows).
    assert ".memory-card {" in shared
    assert ".cost-model-card" not in shared
    assert "display: contents;" in shared
    assert "border-radius: var(--radius-chat);" in shared
    assert "background: var(--chat-brand-wash);" in shared
    assert ".screen-filters .screen-button" in shared
    assert ".thyca-search__icon" in shared
    assert ".screen-search" not in shared


def test_request_panel_mirrors_cost_layout() -> None:
    """The four-view dashboard (Sử dụng token / Request / Chi phí / Trace,
    GOAL-010) keeps Request and Sử dụng token as real switch views
    (#request/#su-dung hashes). Since TASK-036, request.js renders ONE flat
    horizontal bar chart (shared scale, text labels) instead of per-model
    cost cards. The old first Dashboard subview (#hom-nay) is gone; the
    legacy hash falls back to Request (behavior tests in
    test_dashboard_journal.py)."""
    html = (WEBUI / "dashboard.html").read_text(encoding="utf-8")
    script = (WEBUI / "pages" / "dashboard" / "request.js").read_text(encoding="utf-8")
    css = (WEBUI / "pages" / "dashboard" / "cost.css").read_text(encoding="utf-8")
    dash = (WEBUI / "pages" / "dashboard" / "dashboard.js").read_text(encoding="utf-8")

    # Sidebar: exactly four destinations in the Usage/Request/Cost/Trace
    # order, all in-page view toggles (Trace included since it moved into the
    # dashboard); no today view and no decorative numeric prefixes (TASK-036).
    assert '<span class="session-name">Sử dụng token</span>' in html
    assert '<span class="session-name">Request</span>' in html
    assert '<span class="session-name">Chi phí</span>' in html
    assert '<span class="session-name">Trace</span>' in html
    assert "01 / " not in html
    assert 'data-view="today"' not in html
    assert 'id="hom-nay"' not in html
    assert 'id="cost-range"' not in html
    order = [
        html.index('data-view="usage"'),
        html.index('data-view="request"'),
        html.index('data-view="cost"'),
        html.index('data-view="trace"'),
    ]
    assert order == sorted(order)

    # Request and Sử dụng token are their own switch blocks again; the
    # dashboard-section class keeps the flat screen-card rules applying.
    assert 'class="dashboard-block dashboard-section" id="request"' in html
    assert 'class="dashboard-block dashboard-section" id="su-dung"' in html
    assert 'id="request-total"' in html
    assert 'id="request-chart"' in html
    assert "./pages/dashboard/request.js" in html

    # The view switcher covers the four toggles and their hashes; the legacy
    # #hom-nay hash maps to nothing (Request is the fallback default).
    hashes = {
        "request": "#request",
        "usage": "#su-dung",
        "trace": "#trace",
        "cost": "#chi-phi",
    }
    for view, hash in hashes.items():
        assert f'{view}:' in dash
        assert f'"{hash}"' in dash
    assert '"#hom-nay"' not in dash
    assert 'node.hidden = key !== next' in dash
    assert "stack ? false" not in dash

    # Request now renders one shared-scale bar chart (TASK-036), scoped by
    # the request-model-* classes; behavior tests live in
    # test_request_chart.py.
    assert "selectRequestModels" in script
    assert 'className = "request-model-chart"' in script
    assert "request-model-fill" in script
    assert 'className = "cost-model-row"' not in script
    # The old per-model card styles are gone now that Request is one chart;
    # only the #request heading margin rule remains (it serves dashboard.html).
    assert ".cost-model-track" not in css
    assert ".cost-model-fill" not in css
    assert ".cost-model-row" not in css
    assert "#request h2.cost-model-heading" in css


def test_usage_loads_every_trace_page() -> None:
    script = (WEBUI / "pages" / "dashboard" / "usage.js").read_text(encoding="utf-8")
    assert "async function loadAllTraces" in script
    assert "offset=${offset}" in script
    assert "Đang hiển thị" not in script
    assert 'traceRangeUrl("/api/traces", days, 200)' in script


def test_usage_mapper_splits_cached_prompt_tokens() -> None:
    script = f"""
    import {{ aggregateUsage }} from {json.dumps(ANALYTICS_DATA.as_uri())};
    const usage = aggregateUsage([{{
      started_at: "2026-08-20T10:00:00Z",
      prompt_tokens: 100,
      cached_tokens: 40,
      completion_tokens: 20,
      total_tokens: 120,
      requests: 2,
    }}]);
    process.stdout.write(JSON.stringify(usage));
    """
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert payload["totals"] == {
        "input": 60,
        "cache": 40,
        "output": 20,
        "total": 120,
        "turns": 1,
        "requests": 2,
    }


def test_chat_sidebar_pager_markers() -> None:
    """TASK-020: the chat session list paginates at 12 per page. The pager
    math is the marker-delimited pure block (executed in Node by the journal
    tests); here we lock the wiring markers. The DOM behavior (active row
    stays marked, jumps on create/rename/open) is browser-test territory."""
    script = (WEBUI / "pages" / "chat" / "app.js").read_text(encoding="utf-8")
    assert "const SESSIONS_PAGE_SIZE = 12;" in script
    assert "sessionsPageCount(state.sessions.length)" in script
    assert "state.sessionPage = clampPage(state.sessionPage, pages)" in script
    assert "state.sessions.slice(start, start + SESSIONS_PAGE_SIZE)" in script
    # Create (both send paths), rename and open flip to the page holding the
    # session; delete only clamps via renderSessions. The send paths guard the
    # reveal: a background completion must not hijack the page the user paged
    # or navigated to (behavioral regression: tests/test_webui_concurrent_streams.py).
    assert script.count('state.activeId === sessionId && state.sessionPage === pageAtSend ? sessionId : ""') == 2
    assert "await refreshSessions(\n      state.activeId === sessionId" in script
    assert "await refreshSessions(id); // renamed row may sit on another page" in script
    assert "revealSession(sessionId); // opening a session shows the page that holds it" in script
    assert "function revealSession(id)" in script
    # Pager reuses shared button styles and Vietnamese labels.
    assert 'setAttribute("aria-label", "Trang trước")' in script
    assert 'setAttribute("aria-label", "Trang sau")' in script
    assert 'className = "screen-button session-pager-step"' in script
