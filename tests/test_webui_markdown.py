"""Chat markdown: GFM tables and safe HTML via marked."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEBUI = ROOT / "thyca" / "webui"
BACKEND = WEBUI / "backend"
MARKDOWN_JS = BACKEND / "markdown.js"
MEMORY_DATA = BACKEND / "memory-data.js"
TRACE_DATA = BACKEND / "trace-data.js"
ANALYTICS_DATA = BACKEND / "analytics-data.js"
FORMAT_JS = BACKEND / "format.js"


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
    view = (BACKEND / "chat-view.js").read_text(encoding="utf-8")
    css = (WEBUI / "backend.css").read_text(encoding="utf-8")
    assert 'from "./markdown.js"' in view
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
    the end in both directions instead of reading as the cheapest.
    """
    rows = [
        {"model": "gpt-5.6-luna", "cost_usd": 2.4, "last_started_at": "2026-09-14T09:00:00Z"},
        {"model": "muse-spark-1.2-contributor", "cost_usd": 0.9, "last_started_at": "2026-09-12T10:00:00Z"},
        {"model": "foo/bar", "cost_usd": None, "last_started_at": "2026-09-14T11:00:00Z"},
        {"model": "gpt-4o-mini", "cost_usd": 0.2, "last_started_at": "2026-09-01T08:00:00Z"},
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


def test_cost_panel_splits_cache_and_uses_shared_toolbar() -> None:
    """The model breakdown is the one place that used raw prompt_tokens; it now
    splits through the shared helper, and the toolbar reuses the shared search
    and filter-pill classes instead of private copies."""
    script = (WEBUI / "cost.js").read_text(encoding="utf-8")
    html = (WEBUI / "dashboard.html").read_text(encoding="utf-8")
    css = (WEBUI / "cost.css").read_text(encoding="utf-8")
    shared = (WEBUI / "screens.css").read_text(encoding="utf-8")
    memories = (WEBUI / "memories.html").read_text(encoding="utf-8")
    memories_css = (WEBUI / "memories.css").read_text(encoding="utf-8")

    assert "splitPromptTokens(model.prompt_tokens, model.cached_tokens)" in script
    assert '["Input", model.prompt_tokens]' not in script
    assert "selectModels(stats.by_model" in script

    # The breakdown is open at rest: no <details>/<summary>, so nothing has to
    # be clicked (or pressed) before the token split is readable.
    assert "createElement(\"details\")" not in script
    assert "createElement(\"summary\")" not in script
    assert 'className = "fold-row cost-model-row"' in script

    # The toolbar markup is the shared shape, wired to the three orders.
    assert 'class="screen-filter-row"' in html
    assert 'class="screen-search"' in html
    for sort in ("cost-desc", "cost-asc", "recent"):
        assert f'data-sort="{sort}"' in html

    # Nhật ký switched to the promoted classes rather than keeping a copy.
    assert 'class="memory-search"' not in memories
    assert "memory-filters" not in memories_css
    assert ".sidebar .screen-search" in memories_css

    # One row of three equal columns, and it leaves the shared row padding in
    # .fold-row-body rather than re-declaring it.
    assert "grid-template-columns: repeat(3, minmax(0, 1fr));" in css
    assert ".cost-model-stats {" in css
    assert ".fold-row-body {" not in css
    # The head keeps the shared fold-row rhythm without the disclosure arrow,
    # which only <summary> gets.
    assert ".cost-model-row > .cost-model-head {" in css
    assert ".screen-filters .screen-button" in shared
    assert ".screen-search svg" in shared


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
