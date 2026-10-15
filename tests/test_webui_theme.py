"""Shared background sync: two primitives (--color-bg-frame for outer/frame,
--color-bg-surface for content/sidebar/chat/panels) drive every surface
token across all root pages, including the dusk overrides."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEBUI = ROOT / "thyca" / "webui"
TOKENS_CSS = WEBUI / "shared" / "css" / "tokens.css"
KIT_CSS = WEBUI / "shared" / "css" / "kit.css"
TRACE_CSS = WEBUI / "pages" / "dashboard" / "trace.css"
ROOT_PAGES = ("index.html", "dashboard.html", "memories.html",
              "profile.html", "provider.html", "settings.html")


def test_shared_background_primitives_and_aliases() -> None:
    css = TOKENS_CSS.read_text(encoding="utf-8")
    assert "--color-bg-frame: #E8DAC3" in css
    assert "--color-bg-surface: #FBF4E5" in css
    assert "--color-bg-selected: #EADBC8" in css
    for alias in ("--color-desk-top", "--color-desk-mid", "--color-desk-bottom",
                  "--color-paper"):
        assert f"{alias}: var(--color-bg-frame)" in css, alias
    for alias in ("--color-paper-chat", "--color-paper-card",
                  "--color-paper-input", "--composer-bg"):
        assert f"{alias}: var(--color-bg-surface)" in css, alias
    root = re.search(r":root\s*\{(.*?)\}", css, re.S).group(1)
    assert "oklch" not in re.search(
        r"--color-(?:desk|paper|composer)[^:]*:[^;]*;", root).group(0)


def test_dusk_aliases_same_shared_backgrounds() -> None:
    css = TOKENS_CSS.read_text(encoding="utf-8")
    dusk = re.search(
        r"html\[data-theme=\"dusk\"\]\s*\{([^}]*)\}", css)
    assert dusk, "dusk override block missing"
    block = dusk.group(1)
    for alias in ("--color-desk-top", "--color-desk-mid", "--color-paper"):
        assert f"{alias}: var(--color-bg-frame)" in block, alias
    assert "--color-paper-chat: var(--color-bg-surface)" in block
    assert "oklch" not in block, "dusk must not keep the old palette values"


def test_root_pages_share_tokens_stylesheet() -> None:
    for page in ROOT_PAGES:
        html = (WEBUI / page).read_text(encoding="utf-8")
        assert 'href="./shared/css/tokens.css"' in html, page
    shim = (WEBUI / "trace.html").read_text(encoding="utf-8")
    assert "location.replace" in shim or "url=./dashboard.html" in shim


def test_artwork_and_frame_imagery_retained() -> None:
    kit = KIT_CSS.read_text(encoding="utf-8")
    assert kit.count("thyca-bg-paper.png") == 3, (
        "shell, paper layer, and dialogs share the same artwork")
    assert "thyca-bg-outer.png" not in kit
    assert "thyca-bg-menu.png" not in kit
    assert re.search(r"\.notebook-shell\s*\{[^}]*url\(", kit)
    assert re.search(r"\.notebook-shell::before\s*\{[^}]*url\(", kit)
    assert re.search(r"\.screen-dialog\s*\{[^}]*url\(", kit)
    overlay = "var(--color-paper-chat) 65%, transparent"
    assert kit.count(overlay) == 6, (
        "all shared paper overlays keep artwork visible at 65%")
    tokens = TOKENS_CSS.read_text(encoding="utf-8")
    blocks = re.findall(r"body\s*\{[^}]*\}", tokens)
    assert any("background-image" in block for block in blocks)


def test_selected_rows_use_shared_selected_token() -> None:
    kit = KIT_CSS.read_text(encoding="utf-8")
    chat = (ROOT / "thyca" / "webui" / "pages" / "chat" /
            "chat.css").read_text(encoding="utf-8")
    team = re.search(
        r"#team-bot-list \.session-item\.is-active\s*\{([^}]*)\}", chat)
    assert team and "background-color: transparent" in team.group(1), (
        "selected bot row stays transparent — the tint glow/bar pseudo-"
        "elements carry the active state")
    before = re.search(
        r"#team-bot-list \.session-item\.is-active::before\s*\{([^}]*)\}",
        chat)
    assert before and "var(--team-tint)" in before.group(1)
    after = re.search(
        r"#team-bot-list \.session-item\.is-active::after\s*\{([^}]*)\}",
        chat)
    assert after and "var(--team-tint)" in after.group(1)
    screen = re.search(
        r"\.screen-shell \.session-item\.is-active\s*\{([^}]*)\}", kit)
    assert screen and "background: var(--color-bg-selected)" in screen.group(1)
    for block in re.findall(r"\.session-item\.is-active::before\s*\{[^}]*\}", kit):
        assert "var(--color-bg-selected)" in block, block


def test_search_widget_uses_surface_tokens() -> None:
    kit = KIT_CSS.read_text(encoding="utf-8")
    for gone in ("#f5e9da", "#fffcfa", "rgba(245, 233, 218"):
        assert gone not in kit, gone
    assert "--fill: var(--color-paper-input)" in kit
    hover = re.search(r"\.thyca-search:hover\s*\{([^}]*)\}", kit)
    assert hover and "var(--color-bg-surface)" in hover.group(1)
    assert "var(--color-bg-frame)" in hover.group(1)
    focus = re.search(r"\.thyca-search:focus-within\s*\{([^}]*)\}", kit)
    assert focus and "background: var(--color-bg-surface)" in focus.group(1)
    assert "box-shadow" in focus.group(1)


def test_trace_code_bg_frame_light_dark_pairing_kept() -> None:
    css = TRACE_CSS.read_text(encoding="utf-8")
    light = re.search(r"#trace\s*\{([^}]*)\}", css)
    assert light and "--trace-code-bg: var(--color-bg-frame)" in light.group(1)
    assert "--trace-code-ink" in light.group(1)
    dusk = re.search(r"html\[data-theme=\"dusk\"\]\s*#trace\s*\{([^}]*)\}", css)
    assert dusk, "dusk trace block missing"
    assert re.search(r"--trace-code-bg:\s*oklch\(24%", dusk.group(1)), (
        "dusk keeps its dark syntax surface")
    assert re.search(r"--trace-code-ink:\s*oklch\(89%", dusk.group(1))
