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
    for page in ("dashboard", "memories", "profile", "provider"):
        js = (UI / page / "js" / f"{page}.js").read_text(encoding="utf-8")
        assert f"stylesheet: '/ui/{page}/" in js, page
    assert 'src="../share/settings-dialog.js"' in chat   # shared dialog module loads on the single entry


def test_shared_paper_background_spans_the_shell() -> None:
    """One seamless paper world: the restored thyca-bg-paper.png lives once on
    .notebook-shell::before (65% cream overlay, cover, pointer-transparent)
    so the artwork continues across header, sidebar and workspace. The panes
    keep only flat translucent cream veils — no directional gradients — and
    no per-pane SVG ornament/staff rules, markup or injections survive."""
    for name in ("thyca-bg-paper.png", "thyca-bg-menu.png", "thyca-bg-outer.png"):
        art = WEBUI / "images" / name
        assert art.is_file(), name
        assert art.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", name
    base = BASE_CSS.read_text(encoding="utf-8")
    shell = _block(base, ".notebook-shell")
    assert "radial-gradient" not in shell and "linear-gradient" not in shell
    assert "background:#f5eddd" in shell
    layer = _block(base, ".notebook-shell::before")
    assert "images/thyca-bg-paper.png" in layer
    assert "#fbf4e5a6" in layer  # 65% cream overlay over the artwork
    assert "background-size:cover" in layer and "pointer-events:none" in layer
    assert "border-radius:inherit" in layer
    assert ".site-header,.main-grid{position:relative}" in base
    dialog = _block(base, ".screen-dialog")
    assert "images/thyca-bg-paper.png" in dialog and "#fbf4e5a6" in dialog
    sidebar = _block(SIDEBAR_CSS.read_text(encoding="utf-8"), ".sidebar")
    assert "gradient" not in sidebar
    assert "background:#fffbf1b3" in sidebar  # flat translucent veil
    app = APP_SHELL_CSS.read_text(encoding="utf-8")
    assert "gradient" not in _block(app, ".workspace")
    assert "background:#fffcf3d9" in _block(app, ".workspace")
    assert ".workspace>.ornament" not in app and ".workspace>.staff" not in app
    chat_css = (UI / "chat" / "chatcss" / "chat.css").read_text(encoding="utf-8")
    assert "gradient" not in chat_css.split(".chat-title-head{", 1)[0]
    assert ".ornament{" not in chat_css and ".staff{" not in chat_css
    # No per-pane injections: shell.js/router.js no longer reference the SVGs,
    # and chat's static markup carries no ornament/staff nodes either.
    shell_js = SHELL_JS.read_text(encoding="utf-8")
    assert "WORKSPACE_ART" not in shell_js and 'class="ornament"' not in shell_js
    router = (UI / "share" / "router.js").read_text(encoding="utf-8")
    assert "WORKSPACE_ART" not in router
    chat = (UI / "chat" / "index.html").read_text(encoding="utf-8")
    assert 'class="ornament"' not in chat and 'class="staff"' not in chat


def test_decoration_preference_drives_the_shared_artwork() -> None:
    """The saved 'show decorations' preference still works: shell.js applies
    it from 'thyca.ui.preferences' on every load, the shared Settings dialog
    applies it live, and off shows flat cream (no paper image on the shell
    layer, dialogs or drawer)."""
    base = BASE_CSS.read_text(encoding="utf-8")
    for target in (".notebook-shell::before", ".screen-dialog", ".sidebar"):
        assert f'html[data-ornaments="off"] {target}' in base, target
    assert "background-image:none" in base
    shell_js = SHELL_JS.read_text(encoding="utf-8")
    assert "thyca.ui.preferences" in shell_js
    assert "document.documentElement" in shell_js and 'dataset.ornaments' in shell_js
    assert "function applyPrefs" in shell_js
    dialog_js = (UI / "share" / "settings-dialog.js").read_text(encoding="utf-8")
    assert "applyPrefs(collect())" in dialog_js
    # Shared hooks stay writable for compatibility.
    assert "view.dataset.ornaments" in shell_js or "workspace.dataset.ornaments" in shell_js


def test_narrow_drawer_stays_paper_backed_and_opaque() -> None:
    """At <=850px the sidebar becomes an overlay drawer: paper-backed like
    the shell (same image + 65% cream overlay) but over an opaque cream base
    so workspace text never ghosts through the drawer."""
    sidebar = SIDEBAR_CSS.read_text(encoding="utf-8")
    drawer = sidebar.split("@media(max-width:850px){", 1)[1]
    assert "images/thyca-bg-paper.png" in drawer
    assert "#fbf4e5a6" in drawer and "background-size:cover" in drawer
    assert "background-color:#f8efde" in drawer  # opaque base: no ghosted chat text


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
