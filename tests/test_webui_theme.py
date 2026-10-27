"""Shared theme tokens: ui/share/base.css owns the single :root palette every
workspace inherits. The single SPA entry (ui/chat/index.html) links the
shared stack — fonts.css, base.css, sidebar.css, the avatar sheets, plus
markdown.css and its own sheet; the router loads app-shell.css and each
page sheet on demand for non-chat workspaces."""
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


def test_spa_entry_links_the_shared_stylesheets() -> None:
    """The single entry links the shared stack; the router loads app-shell
    plus each page sheet on demand for non-chat workspaces."""
    chat = (UI / "chat" / "index.html").read_text(encoding="utf-8")
    for href in SHARED_LINKS:
        assert f'href="{href}"' in chat, href
    assert 'href="../share/markdown.css"' in chat
    assert 'href="chatcss/chat.css"' in chat
    # Chat keeps its own workspace chrome — no static app-shell link.
    assert "../share/app-shell.css" not in chat
    router = (UI / "share" / "router.js").read_text(encoding="utf-8")
    assert "styleLink('/ui/share/app-shell.css', 'app-shell')" in router
    assert "styleLink(entry.stylesheet, page)" in router
    for page in ("dashboard", "memories", "profile", "provider", "settings"):
        js = (UI / page / "js" / f"{page}.js").read_text(encoding="utf-8")
        assert f"stylesheet: '/ui/{page}/" in js, page


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
    current page (the rule lives in base.css so chat's hydrated nav gets it
    too), the sidebar marks the selected bot card."""
    base = BASE_CSS.read_text(encoding="utf-8")
    current = _block(base, ".page-links a.is-current")
    assert "var(--forest)" in current
    assert "inset 0 0 0 1px" in current
    util = (UI / "share" / "util.js").read_text(encoding="utf-8")
    assert 'class="is-current" aria-current="page"' in util
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
    gets the warm error pairing instead of a theme swap. The standalone
    trace sheet is gone — Dashboard's scoped sheet owns the .tl-code rules."""
    css = (UI / "dashboard" / "dashboardcss" / "dashboard.css").read_text(encoding="utf-8")
    code = _block(css, ".dashboard-shell .tl-code")
    assert "background:#fff8ec" in code
    assert "ui-monospace" in code
    err = _block(css, ".dashboard-shell .tl-code.tl-code-err")
    assert "background:#fdf0ea" in err
    assert "color:#944f3d" in err
    # Assistant replies keep the shared markdown surface.
    md = MARKDOWN_CSS.read_text(encoding="utf-8")
    assert ".md-table-wrap" in md
    assert "ui-monospace" in md
