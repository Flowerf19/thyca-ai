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
STAFF_INDEX = WEBUI / "js" / "staff" / "index.js"
SCORE_JS = WEBUI / "js" / "trace" / "score.js"
STAFF_MAP = WEBUI / "js" / "staff" / "map.js"


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


def test_memories_and_staff_barrels_import_clean() -> None:
    # memories/index.js (overview/leaf/canonical) và staff pure modules
    # (map/draw/catalog/status/replay + trace/score) đều không chạm DOM lúc
    # import. trace/index.js và shared dom/drawer là DOM-only nên KHÔNG
    # import ở đây — xem ghi chú trong webui/js/shared/index.js.
    script = f"""
    import {json.dumps(MEMORIES_INDEX.as_uri())};
    import {{ scoreFromEvents }} from {json.dumps(STAFF_MAP.as_uri())};
    import {{ traceScoreFromMessages }} from {json.dumps(SCORE_JS.as_uri())};
    import {json.dumps(STAFF_INDEX.as_uri())};
    if (typeof scoreFromEvents !== "function") throw new Error("no score");
    if (typeof traceScoreFromMessages !== "function") throw new Error("no trace score");
    process.stdout.write("barrels-ok");
    """
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout == "barrels-ok"
