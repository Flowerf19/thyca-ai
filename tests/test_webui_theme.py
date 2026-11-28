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
        "--rust:#aa5638", "--nav-full:", "--nav-rail:", "--nav-width:var(--nav-full)",
        "--sidebar-width:var(--sidebar-full)",
        "--sidebar-full:", "--header-avatar:", "--drawer-width:",
        "--header-h:", "--bot-avatar:", "--chat-content-max:",
        "--page-content-max:",
        "--fs-2xs:", "--fs-xs:", "--fs-sm:", "--fs-md:",
        "--fs-lg:", "--fs-xl:", "--fs-2xl:",
    ):
        assert token in root, token
    assert "'Thyca Serif'" in root and "--serif" in root
    assert "'Thyca Sans'" in root and "--sans" in root
    for sheet in UI.rglob("*.css"):
        assert "oklch" not in sheet.read_text(encoding="utf-8"), sheet.name


def test_unified_type_scale_and_heading_rules() -> None:
    """All WebUI stylesheets share one 7-step --fs-* scale and canonical
    h1-h4 rules in base.css, with zero hardcoded px font sizes outside
    base.css :root and no rem font overrides in markdown.css."""
    base = BASE_CSS.read_text(encoding="utf-8")
    for tag in ("h1{", "h2{", "h3{", "h4{"):
        assert tag in base, tag

    root_block = _block(base, ":root")
    px_font_re = re.compile(r"(?:^|[;{])\s*(?:font-size\s*:\s*[^;}]*?\dpx|font\s*:\s*[^;}]*?\dpx)")
    heading_re = re.compile(r"(?:^|[\s,>~+])h[1-6](?:$|[\s,.#:[>+~])")
    heading_font_re = re.compile(r"(?:^|[;{])\s*font(?:-size)?\s*:")
    for sheet in sorted(UI.rglob("*.css")):
        text = sheet.read_text(encoding="utf-8")
        if sheet == BASE_CSS:
            text = text.replace(root_block, "", 1)
        hits = px_font_re.findall(text)
        assert not hits, f"{sheet.name} has hardcoded px font declarations: {hits}"
        if sheet != BASE_CSS:
            for m in re.finditer(r"([^{}]*)\{([^}]*)\}", text):
                sel, body = m.group(1), m.group(2)
                if heading_re.search(sel) and heading_font_re.search(body):
                    raise AssertionError(f"{sheet.name} overrides heading font on {sel.strip()!r}")

    md = MARKDOWN_CSS.read_text(encoding="utf-8")
    assert not re.search(r"font(?:-size)?\s*:[^;}]*rem", md)


def test_spa_entry_links_the_shared_stylesheets() -> None:
    """The single entry links the shared stack; the router loads app-shell
    plus each page sheet on demand for non-chat workspaces."""
    chat = (UI / "chat" / "index.html").read_text(encoding="utf-8")
    for href in SHARED_LINKS:
        assert f'href="{href}"' in chat, href
    assert 'href="../share/markdown.css"' in chat
    assert 'href="chatcss/chat.css"' in chat
    assert "../share/app-shell.css" not in chat
    router = (UI / "share" / "router.js").read_text(encoding="utf-8")
    assert "styleLink('/ui/share/app-shell.css', 'app-shell')" in router
    assert "styleLink(entry.stylesheet, page)" in router
    for page in ("dashboard", "memories", "profile", "provider"):
        js = (UI / page / "js" / f"{page}.js").read_text(encoding="utf-8")
        assert f"stylesheet: '/ui/{page}/" in js, page
    assert 'src="../share/settings-dialog.js"' not in chat


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
    root = _block(base, ":root")
    shell = _block(base, ".notebook-shell")
    assert "radial-gradient" not in shell and "linear-gradient" not in shell
    assert "background:var(--bg-shell)" in shell and "--bg-shell:#f5eddd" in root
    layer = _block(base, ".notebook-shell::before")
    assert "images/thyca-bg-paper.png" in layer
    assert "var(--bg-shell-veil)" in layer and "--bg-shell-veil:#fbf4e5a6" in root
    assert "background-size:cover" in layer and "pointer-events:none" in layer
    assert "border-radius:inherit" in layer
    assert ".site-header,.main-grid{position:relative}" in base
    dialog = _block(base, ".screen-dialog")
    assert "images/thyca-bg-paper.png" in dialog and "var(--bg-shell-veil)" in dialog
    sidebar = _block(SIDEBAR_CSS.read_text(encoding="utf-8"), ".sidebar")
    assert "gradient" not in sidebar
    assert "background:var(--surface)" in sidebar and "--surface:#fffbf1b3" in root
    app = APP_SHELL_CSS.read_text(encoding="utf-8")
    assert "gradient" not in _block(app, ".workspace")
    assert "background:var(--surface-workspace)" in _block(app, ".workspace")
    assert "--surface-workspace:#fffcf3d9" in root
    assert ".workspace>.ornament" not in app and ".workspace>.staff" not in app
    chat_css = (UI / "chat" / "chatcss" / "chat.css").read_text(encoding="utf-8")
    assert "gradient" not in chat_css.split(".chat-title-head{", 1)[0]
    assert ".ornament{" not in chat_css and ".staff{" not in chat_css
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
    assert "view.dataset.ornaments" in shell_js or "workspace.dataset.ornaments" in shell_js


def test_narrow_drawer_stays_paper_backed_and_opaque() -> None:
    """At <=850px the sidebar becomes an overlay drawer: paper-backed like
    the shell (same image + 65% cream overlay) over an opaque cream base
    so workspace text never ghosts through, flush under the header (top:0)
    with full card layout (no rail collapse or vertical splitter)."""
    sidebar = SIDEBAR_CSS.read_text(encoding="utf-8")
    drawer = sidebar.split("@media(max-width:850px){", 1)[1]
    base = BASE_CSS.read_text(encoding="utf-8")
    root = _block(base, ":root")
    assert "images/thyca-bg-paper.png" in drawer
    assert "var(--bg-shell-veil)" in drawer and "background-size:cover" in drawer
    assert "background-color:var(--bg-drawer)" in drawer
    assert "--bg-drawer:#f8efde" in root and "--bg-shell-veil:#fbf4e5a6" in root
    assert "--sidebar-width:var(--sidebar-full)" in root
    assert "grid-template-columns:var(--nav-width) minmax(0,1fr) var(--sidebar-width)" in base
    assert "width:var(--drawer-width)" in drawer
    assert "top:0" in drawer
    assert "top:47px" not in sidebar and "top:47px" not in base
    assert "min(360px" not in drawer
    assert 'data-sidebar-collapsed="true"' not in drawer
    assert ".splitter" not in base and ".splitter" not in sidebar
    assert ".menu-button{display:inline-flex" in base
    assert "transform:translateX(calc(100% + 18px))" in drawer
    assert ".sidebar.mobile-open" in sidebar
    assert "transform:translateX(calc(-100% - 18px))" in base
    assert ".nav-sidebar.mobile-open" in base
    assert ".mobile-page-nav" not in sidebar and ".sidebar-footer" in sidebar
    avatars = (UI / "share" / "avatars" / "avatars.css").read_text(encoding="utf-8")
    pupil_rule = _block(avatars, ".crew-avatar .eye-pupil-group")
    assert "transition:opacity .14s ease" in pupil_rule and "transform ." not in pupil_rule, pupil_rule


def test_responsive_fluid_scale_guards() -> None:
    """Fluid tokens and media guards: avatars.css base rule precedes media
    overrides, sidebar.css does not enlarge .bot-name on narrower screens,
    sidebar.css carries short-height density rules, memories.css uses
    an adaptive grid instead of a fixed 7-column track, and downstream
    responsive rules follow their base declarations."""
    avatars = (UI / "share" / "avatars" / "avatars.css").read_text(encoding="utf-8")
    first_media = avatars.find("@media")
    base_crew = avatars.find(".crew-avatar{")
    assert base_crew != -1 and (first_media == -1 or base_crew < first_media)

    sidebar = SIDEBAR_CSS.read_text(encoding="utf-8")
    med_band = sidebar.split("@media(max-width:1150px){", 1)[1].split("}", 1)[0]
    assert ".bot-name{font-size:14px}" not in med_band
    assert "@media(max-height:680px){" in sidebar
    assert "max-width:120px" not in _block(sidebar, ".group-meta")

    memories = (UI / "memories" / "memoriescss" / "memories.css").read_text(encoding="utf-8")
    assert "repeat(7,minmax(0,1fr))" not in memories
    assert ".mem-sid" not in memories and "#memory-reload" not in memories
    assert "flex-wrap:wrap" in _block(memories, ".mem-use-tabs")
    assert "var(--danger-ink)" in memories and "var(--danger-bg)" in memories

    dashboard = (UI / "dashboard" / "dashboardcss" / "dashboard.css").read_text(encoding="utf-8")
    base_chart = dashboard.find(".day-chart{")
    resp_chart = dashboard.find(".day-chart.is-hourly{gap:2px}")
    assert base_chart != -1 and resp_chart > base_chart
    assert ".analysis-panels" not in dashboard and ".cost-analysis" not in dashboard

    studio = (UI / "share" / "avatars" / "studio.css").read_text(encoding="utf-8")
    assert "flex-wrap:wrap" in _block(studio, ".group-preview-members")
    assert studio.rfind("@media(max-width:520px){") > studio.find("#expression-grid")

    chat_css = (UI / "chat" / "chatcss" / "chat.css").read_text(encoding="utf-8")
    assert "scrollbar-gutter:stable both-edges" in chat_css
    mention = _block(chat_css, ".mention-popup")
    assert "max-width:" in mention and "overflow-y:auto" in mention

    base = BASE_CSS.read_text(encoding="utf-8")
    router_css = (UI / "share" / "router.css").read_text(encoding="utf-8")
    assert "--header-avatar:" in base and "--rail-width" not in base
    assert ".header-rail-wrap{" in base
    assert ".rail-scroll" not in sidebar
    assert "width:var(--header-avatar);height:var(--header-avatar)" in _block(sidebar, ".rail-avatar .crew-avatar,.rail-avatar .group-cluster")
    assert ".crew-avatar:where(.size-md)" in avatars
    assert "grid-column:2" in router_css and "grid-column:3" not in router_css


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
    base = BASE_CSS.read_text(encoding="utf-8")
    root = _block(base, ":root")
    dark = _block(base, 'html[data-theme="dark"]')
    field = _block(app, ".field input,.field select,.field textarea,.inline-field")
    assert "background:var(--surface-input)" in field
    assert "--surface-input:#fff9ea" in root and "--surface-input:#1b1814" in dark
    focus = _block(app, ".field input:focus,.field select:focus,.field textarea:focus,.inline-field:focus")
    assert "outline:2px solid" in focus
    memories = (UI / "memories" / "memoriescss" / "memories.css").read_text(encoding="utf-8")
    assert "#memory-search" in memories
    index_html = (UI / "chat" / "index.html").read_text(encoding="utf-8")
    assert 'class="inline-field" id="memory-search"' in index_html


def test_trace_code_keeps_the_light_syntax_surface() -> None:
    """Trace payload code sits on a semantic surface token; failed tool output
    gets the error tokens instead of a manual theme swap."""
    css = (UI / "dashboard" / "dashboardcss" / "dashboard.css").read_text(encoding="utf-8")
    base = BASE_CSS.read_text(encoding="utf-8")
    root = _block(base, ":root")
    dark = _block(base, 'html[data-theme="dark"]')
    code = _block(css, ".dashboard-shell .tl-code")
    assert "background:var(--surface-code)" in code
    assert "--surface-code:#fff8ec" in root and "--surface-code:#1a1713" in dark
    assert "ui-monospace" in code
    err = _block(css, ".dashboard-shell .tl-code.tl-code-err")
    assert "background:var(--danger-bg)" in err and "color:var(--danger-ink)" in err
    assert "--danger-bg:#fdf0ea" in root and "--danger-ink:#944f3d" in root
    assert "--danger-bg:#3a221d" in dark and "--danger-ink:#f2a090" in dark
    md = MARKDOWN_CSS.read_text(encoding="utf-8")
    assert ".md-table-wrap" in md
    assert "ui-monospace" in md


def test_no_hardcoded_hex_colors_in_workspace_stylesheets() -> None:
    """De-hardcoding guardrail: only ui/share/base.css may define raw hex color
    literals so Light and Warm Ink Dark themes switch cleanly without drift."""
    hex_re = re.compile(r"#[0-9a-fA-F]{3,8}\b")
    violations: list[str] = []
    for sheet in sorted(UI.rglob("*.css")):
        if sheet == BASE_CSS:
            continue
        text = sheet.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            hits = hex_re.findall(line)
            if hits:
                rel = sheet.relative_to(ROOT)
                violations.append(f"{rel}:{lineno} contains raw hex {hits}")
    assert not violations, (
        "Workspace stylesheet contains hardcoded hex colors instead of semantic tokens.\n"
        f"Violations: {violations}\n"
        "Fix: Replace hardcoded hex colors with semantic tokens from thyca/webui/ui/share/base.css "
        "(--surface-*, --ink-*, --border-*, --danger-*, etc.)."
    )


def test_dark_mode_tokens_and_theme_toggle_present() -> None:
    """base.css ships complete Warm Ink Dark tokens under html[data-theme='dark'],
    and chat/index.html + shell.js wire #theme-toggle and #pv-key-toggle."""
    base = BASE_CSS.read_text(encoding="utf-8")
    dark = _block(base, 'html[data-theme="dark"]')
    assert dark, "Missing html[data-theme=\"dark\"] block in base.css"
    for token in (
        "--paper:#231f1a", "--ink:#efe6d4", "--bg-shell:#1c1915",
        "--surface-workspace:", "--surface-code:", "--danger-bg:", "--danger-ink:",
        "color-scheme:dark",
    ):
        assert token in dark, token
    html = (UI / "chat" / "index.html").read_text(encoding="utf-8")
    shell = SHELL_JS.read_text(encoding="utf-8")
    assert 'data-theme="light"' in html
    assert 'id="theme-toggle"' in html and 'id="theme-toggle"' in shell
    assert 'id="pv-key-toggle"' in html
    assert "theme: 'light'" in shell and "syncThemeChrome" in shell


def test_workspace_uses_container_queries_and_cqi_tokens() -> None:
    """Workspaces declare a named inline-size container so inner widths,
    padding, and multi-column grids adapt across all screen aspect ratios
    and sidebar open/collapsed states via cqi + @container workspace."""
    base = BASE_CSS.read_text(encoding="utf-8")
    root = _block(base, ":root")
    ws_rule = _block(base, ".workspace")
    assert "container-type:inline-size" in ws_rule and "container-name:workspace" in ws_rule
    for token in ("--page-pad-x:", "--chat-pad-x:", "--chat-content-max:", "--page-content-max:"):
        start = root.index(token)
        decl = root[start : root.index(";", start)]
        assert "cqi" in decl and "vw" not in decl, (
            f"Workspace token {token} still depends on vw instead of cqi: {decl}. "
            "Fix: express workspace content widths/padding with cqi relative to .workspace."
        )
    vw_re = re.compile(r"[0-9.]vw\b")
    workspace_sheets = (
        UI / "share" / "app-shell.css",
        UI / "chat" / "chatcss" / "chat.css",
        UI / "profile" / "profilecss" / "profile.css",
        UI / "memories" / "memoriescss" / "memories.css",
        UI / "dashboard" / "dashboardcss" / "dashboard.css",
        UI / "provider" / "providercss" / "provider.css",
    )
    for sheet in workspace_sheets:
        text = sheet.read_text(encoding="utf-8")
        rel = sheet.relative_to(ROOT)
        assert "@container workspace" in text, (
            f"{rel} is missing @container workspace responsive rules. "
            "Fix: migrate inner workspace @media(max-width) rules to @container workspace."
        )
        assert not vw_re.search(text), (
            f"{rel} still uses viewport vw units inside a workspace sheet. "
            "Fix: replace vw padding/widths with cqi."
        )


def test_page_transitions_use_view_transition_and_unified_workspace_surface() -> None:
    """SPA page swaps use View Transitions scoped to the visible workspace
    with CSS fade fallback, smooth tab-pill transition, and unified
    --surface-workspace background across Chat and app pages."""
    base = BASE_CSS.read_text(encoding="utf-8")
    chat_css = (UI / "chat" / "chatcss" / "chat.css").read_text(encoding="utf-8")
    router_css = (UI / "share" / "router.css").read_text(encoding="utf-8")
    router_js = (UI / "share" / "router.js").read_text(encoding="utf-8")
    assert "--surface-chat" not in base
    assert "background:var(--surface-workspace)" in _block(chat_css, ".workspace")
    assert "transition:" in _block(base, ".page-links a")
    assert "view-transition-name:workspace" in router_css
    assert "::view-transition-old(workspace)" in router_css
    assert "::view-transition-new(workspace)" in router_css
    assert ".workspace.page-entering" in router_css
    assert "@keyframes thycaPageIn" in router_css and "@keyframes thycaPageOut" in router_css
    assert "prefers-reduced-motion:reduce" in router_css
    assert "startViewTransition" in router_js
    assert "preloadStyles" in router_js and "activateStyles" in router_js
