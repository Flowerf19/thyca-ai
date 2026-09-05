"""Chat markdown: GFM tables and safe HTML via marked."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEBUI = ROOT / "webui"
MARKDOWN_JS = WEBUI / "js" / "markdown.js"


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
    assert 'from "../markdown.js"' in view
    assert "formatMarkdown(content)" in view
    assert ".md-table-wrap" in css
    assert (WEBUI / "vendor" / "marked.esm.js").is_file()
