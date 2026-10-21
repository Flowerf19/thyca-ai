"""Shared theme tokens: ui/share/base.css owns the single :root palette every
rebuilt page inherits. Each ui/<page>/index.html links the same stack —
fonts.css, base.css, sidebar.css, the avatar sheets, then (except chat, which
keeps its own workspace chrome) app-shell.css plus its page sheet."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEBUI = ROOT / "thyca" / "webui"
UI = WEBUI / "ui"
BASE_CSS = UI / "share" / "base.css"
APP_SHELL_CSS = UI / "share" / "app-shell.css"
SIDEBAR_CSS = UI / "share" / "sidebar.css"
MARKDOWN_CSS = UI / "share" / "markdown.css"
SHELL_JS = UI / "share" / "shell.js"

# Pages that render inside emitShell()'s shared chrome all link app-shell.css;
# chat keeps its own static markup + chatcss/chat.css instead.
SHELL_PAGES = ("dashboard", "memories", "profile", "provider", "settings", "trace")
ALL_PAGES = ("chat",) + SHELL_PAGES

SHARED_LINKS = (
    "../share/fonts.css",
    "../share/base.css",
    "../share/sidebar.css",
    "../share/avatars/avatars.css",
    "../share/avatars/studio.css",
)


def _block(css: str, selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    return match.group(1) if match else ""


def test_base_tokens_define_the_paper_palette() -> None:
    css = BASE_CSS.read_text(encoding="utf-8")
    root = _block(css, ":root")
    for token in (
        "--paper:#fbf5e8", "--paper-deep:#f0e7d4", "--ink:#3c372c",
        "--muted:#877d69", "--line:#d9ccb4", "--forest:#315d4c",
        "--rust:#aa5638", "--sidebar-width:374px",
    ):
        assert token in root, token
    assert "'Thyca Serif'" in root and "--serif" in root
    assert "'Thyca Sans'" in root and "--sans" in root
    # The rebuild has one palette: no oklch theme overrides survive anywhere.
    for sheet in UI.rglob("*.css"):
        assert "oklch" not in sheet.read_text(encoding="utf-8"), sheet.name


def test_every_page_links_the_shared_stylesheets() -> None:
    for page in ALL_PAGES:
        html = (UI / page / "index.html").read_text(encoding="utf-8")
        for href in SHARED_LINKS:
            assert f'href="{href}"' in html, f"{page}: {href}"
        assert f'href="{page}css/{page}.css"' in html, page
    for page in SHELL_PAGES:
        html = (UI / page / "index.html").read_text(encoding="utf-8")
        assert 'href="../share/app-shell.css"' in html, page
    # Chat keeps its own workspace chrome — no app-shell, its own sheet.
    chat = (UI / "chat" / "index.html").read_text(encoding="utf-8")
    assert "../share/app-shell.css" not in chat
    assert 'href="../share/markdown.css"' in chat


def test_shell_artwork_and_page_ornaments_retained() -> None:
    """The paper world survives: the notebook shell keeps its gradient frame,
    and every workspace carries the same ornament + staff line art."""
    base = BASE_CSS.read_text(encoding="utf-8")
    shell = _block(base, ".notebook-shell")
    assert "radial-gradient" in shell
    assert "border-radius:24px" in shell
    app = APP_SHELL_CSS.read_text(encoding="utf-8")
    assert "linear-gradient" in _block(app, ".workspace")
    assert ".workspace>.ornament" in app
    assert ".workspace>.staff" in app
    # shell.js inserts the same two SVG ornaments chat draws inline.
    js = SHELL_JS.read_text(encoding="utf-8")
    assert 'class="ornament"' in js
    assert 'class="staff"' in js
    chat = (UI / "chat" / "index.html").read_text(encoding="utf-8")
    assert 'class="ornament"' in chat and 'class="staff"' in chat


def test_current_page_and_selected_bot_marks() -> None:
    """Selection states ride shared marks: the header links light up the
    current page, the sidebar marks the selected bot card."""
    app = APP_SHELL_CSS.read_text(encoding="utf-8")
    current = _block(app, ".page-links a.is-current")
    assert "var(--forest)" in current
    assert "inset 0 0 0 1px" in current
    js = SHELL_JS.read_text(encoding="utf-8")
    assert 'class="is-current" aria-current="page"' in js
    sidebar = SIDEBAR_CSS.read_text(encoding="utf-8")
    assert _block(sidebar, ".bot-card[data-selected=true]")


def test_inline_fields_use_the_paper_surface() -> None:
    """Search/scope inputs share one surface + focus treatment — the
    memories toolbar's #memory-search is an .inline-field."""
    app = APP_SHELL_CSS.read_text(encoding="utf-8")
    field = _block(app, ".field input,.field select,.field textarea,.inline-field")
    assert "background:#fff9ea" in field
    focus = _block(app, ".field input:focus,.field select:focus,.field textarea:focus,.inline-field:focus")
    assert "outline:2px solid" in focus
    memories = (UI / "memories" / "memoriescss" / "memories.css").read_text(encoding="utf-8")
    assert "#memory-search" in memories
    page_js = (UI / "memories" / "js" / "memories.js").read_text(encoding="utf-8")
    assert 'class="inline-field" id="memory-search"' in page_js


def test_trace_code_keeps_the_light_syntax_surface() -> None:
    """Trace payload code sits on a light inset surface; failed tool output
    gets the warm error pairing instead of a theme swap."""
    css = (UI / "trace" / "tracecss" / "trace.css").read_text(encoding="utf-8")
    code = _block(css, ".tl-code")
    assert "background:#fff8ec" in code
    assert "ui-monospace" in code
    err = _block(css, ".tl-code.tl-code-err")
    assert "background:#fdf0ea" in err
    assert "color:#944f3d" in err
    # Assistant replies keep the shared markdown surface.
    md = MARKDOWN_CSS.read_text(encoding="utf-8")
    assert ".md-table-wrap" in md
    assert "ui-monospace" in md
