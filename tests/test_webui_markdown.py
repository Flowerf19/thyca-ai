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
    assert "formatMarkdown(message.content)" in view
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
