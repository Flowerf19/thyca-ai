"""Chat markdown: GFM tables and safe HTML via marked."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEBUI = ROOT / "webui"
MARKDOWN_JS = WEBUI / "js" / "shared" / "markdown.js"
SHARED_INDEX = WEBUI / "js" / "shared" / "index.js"
MEMORIES_INDEX = WEBUI / "js" / "memories" / "index.js"
LEAF_JS = WEBUI / "js" / "memories" / "leaf.js"
SCORE_JS = WEBUI / "js" / "trace" / "score.js"


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
    view = (WEBUI / "js" / "chat" / "view.js").read_text(encoding="utf-8")
    css = "\n".join(
        p.read_text(encoding="utf-8") for p in sorted((WEBUI / "css" / "workspace").glob("*.css"))
    )
    assert 'from "../shared/markdown.js"' in view
    assert "formatMarkdown(content)" in view
    assert ".md-table-wrap" in css
    assert (WEBUI / "vendor" / "marked.esm.js").is_file()


def test_shared_barrel_is_node_clean() -> None:
    # shared/index.js chỉ re-export pure modules (không DOM): import trong
    # Node phải thành công để pin import graph của barrel.
    script = f"""
    import {json.dumps(SHARED_INDEX.as_uri())};
    process.stdout.write("shared-index-ok");
    """
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout == "shared-index-ok"


def test_memories_and_trace_score_import_clean() -> None:
    # memories/index.js và trace/score.js không chạm DOM lúc import.
    script = f"""
    import {json.dumps(MEMORIES_INDEX.as_uri())};
    import {{ traceScoreFromEvents }} from {json.dumps(SCORE_JS.as_uri())};
    if (typeof traceScoreFromEvents !== "function") throw new Error("no trace score");
    process.stdout.write("barrels-ok");
    """
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout == "barrels-ok"


def test_rank_leaves_caps_and_orders() -> None:
    script = f"""
    import {{ rankLeaves }} from {json.dumps(LEAF_JS.as_uri())};
    const leaves = [
      {{ get_count: 1, search_count: 0, chunk_id: "a" }},
      {{ get_count: 9, search_count: 1, chunk_id: "b" }},
      {{ get_count: 0, search_count: 4, chunk_id: "c" }},
    ];
    const get = rankLeaves(leaves, "get").map((l) => l.chunk_id);
    const search = rankLeaves(leaves, "search").map((l) => l.chunk_id);
    const least = rankLeaves(leaves, "least").map((l) => l.chunk_id);
    process.stdout.write(JSON.stringify({{ get, search, least, cap: rankLeaves(leaves.concat(leaves, leaves, leaves), "get").length }}));
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
    assert payload["cap"] == 8
