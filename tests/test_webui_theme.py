"""Static v5 palette/reuse contracts, not screenshot or visual acceptance proof.

Targets come from the approved webui-v5-small-tasks palette/background sections.
Distinct desk, frame, paper, sidebar, selected and input roles stay shared.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEBUI = ROOT / "thyca" / "webui"
TOKENS_CSS = WEBUI / "shared" / "css" / "tokens.css"
KIT_CSS = WEBUI / "shared" / "css" / "kit.css"
TRACE_CSS = WEBUI / "pages" / "dashboard" / "trace.css"
ROOT_PAGES = ("index.html", "dashboard.html", "memories.html",
              "profile.html", "provider.html", "settings.html")


PALETTE = {
    "--color-bg-desk": "#eeeae2",
    "--color-bg-frame": "#f1ece2",
    "--color-bg-surface": "#faf7f0",
    "--color-bg-sidebar": "#f9f5eb",
    "--color-bg-selected": "#eee5d7",
    "--color-bg-input": "#fdfbf6",
    "--color-ink": "#3b332a",
    "--color-reading": "#3b332a",
    "--color-muted": "#8a7c69",
    "--color-muted-soft": "#8a7c69",
    "--color-border": "#e0d6c6",
    "--color-divider": "#e0d6c6",
    "--color-paper-edge": "#e0d6c6",
    "--color-accent": "#a85b43",
    "--color-accent-deep": "#a85b43",
    "--composer-border": "#d8cbb8",
}
ALIASES = {
    "--color-desk-top": "--color-bg-desk",
    "--color-desk-mid": "--color-bg-desk",
    "--color-desk-bottom": "--color-bg-desk",
    "--color-paper": "--color-bg-frame",
    "--color-paper-chat": "--color-bg-surface",
    "--color-paper-card": "--color-bg-surface",
    "--color-paper-input": "--color-bg-input",
    "--composer-bg": "--color-bg-input",
}


def _blocks(css: str, selector: str) -> list[str]:
    # Restrict assertions to real declarations, not comments or longer selectors.
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return re.findall(
        r"(?:^|[{}])\s*" + re.escape(selector) + r"\s*\{([^{}]*)\}",
        css, re.S)


def _declarations(blocks: list[str]) -> dict[str, str]:
    assert blocks, "expected CSS rule missing"
    return {name: value.strip() for block in blocks for name, value in
            re.findall(r"([\w-]+)\s*:\s*([^;{}]+);", block)}


def test_shared_background_primitives_and_aliases() -> None:
    root = _declarations(_blocks(TOKENS_CSS.read_text(encoding="utf-8"), ":root"))
    for token, expected in PALETTE.items():
        assert root.get(token, "").lower() == expected, token
    for alias, primitive in ALIASES.items():
        assert root.get(alias) == f"var({primitive})", alias


def test_dusk_aliases_same_shared_backgrounds() -> None:
    css = TOKENS_CSS.read_text(encoding="utf-8")
    root = _declarations(_blocks(css, ":root"))
    blocks = _blocks(css, 'html[data-theme="dusk"]')
    assert blocks, "dusk override block missing"
    dusk = _declarations(blocks)
    effective = root | dusk
    for token, expected in PALETTE.items():
        assert effective.get(token, "").lower() == expected, token
    for alias, primitive in ALIASES.items():
        assert effective.get(alias) == f"var({primitive})", alias
    assert all("oklch" not in block for block in blocks), (
        "dusk must not keep the old palette values")


def test_semantic_status_colors_retained() -> None:
    root = _declarations(_blocks(TOKENS_CSS.read_text(encoding="utf-8"), ":root"))
    for token, expected in {
        "--status-done": "#4a8759", "--status-waiting": "#3e6b94",
        "--status-error": "#b84239", "--status-offline": "#b5a898",
        "--color-success-ink": "oklch(43% 0.085 155)",
        "--color-status-waiting": "oklch(51% 0.11 250)",
    }.items():
        assert root.get(token, "").lower() == expected, token


def test_shadow_color_tokens_remain_colors() -> None:
    root = _declarations(_blocks(TOKENS_CSS.read_text(encoding="utf-8"), ":root"))
    # Consumers supply their own offsets, and dialog backdrops use these as
    # background colors. A whole shadow list here invalidates both properties.
    for token in ("--color-shell-shadow", "--color-shell-shadow-soft"):
        assert re.fullmatch(r"rgba\([\d\s.,]+\)", root.get(token, "")), token
    assert root.get("--wood-offline-base") == "#9E9080"
    assert root.get("--wood-offline-side") == "#5E5348"


def test_root_pages_share_tokens_stylesheet() -> None:
    for page in ROOT_PAGES:
        html = (WEBUI / page).read_text(encoding="utf-8")
        for stylesheet in ("tokens", "kit", "states", "markdown"):
            assert f'href="./shared/css/{stylesheet}.css"' in html, page
    shim = (WEBUI / "trace.html").read_text(encoding="utf-8")
    assert "location.replace" in shim or "url=./dashboard.html" in shim


def test_artwork_and_frame_imagery_retained() -> None:
    kit = KIT_CSS.read_text(encoding="utf-8")
    tokens = TOKENS_CSS.read_text(encoding="utf-8")
    root = _declarations(_blocks(tokens, ":root"))
    assert root.get("--background-artwork") == 'url("../../images/thyca-bg-outer.png")'
    assert tokens.count("thyca-bg-outer.png") == 1, "define artwork only once"
    for image in ("thyca-bg-paper.png", "thyca-bg-menu.png"):
        assert image not in tokens, image
    assert not re.search(r"thyca-bg-(?:outer|paper|menu)\.png", kit), (
        "shell, paper and dialogs must reuse the shared image token")
    # The approved 65% is a starting point, not final screenshot acceptance.
    overlay = root.get("--background-overlay", "")
    rgba = re.fullmatch(
        r"rgba\(\s*250\s*,\s*247\s*,\s*240\s*,\s*(0?\.\d+)\s*\)", overlay)
    assert rgba and 0 < float(rgba.group(1)) < 1, (
        "one translucent main-paper overlay must keep the artwork visible")
    for selector in (".notebook-shell", ".notebook-shell::before", ".screen-dialog"):
        declarations = _declarations(_blocks(kit, selector))
        assert declarations.get("background-image") == (
            "linear-gradient(var(--background-overlay), var(--background-overlay)), "
            "var(--background-artwork)"), selector
    assert "background-image" in _declarations(_blocks(tokens, "body"))


def test_original_background_png_assets_retained() -> None:
    # Original tracked bytes, verified against HEAD before the v5 test update.
    for image, digest in {
        "thyca-bg-outer.png": "90acd3fa765c3caac47ef45a58e4f2d56ee6cda050f7f4c85bc25480b47b169f",
        "thyca-bg-paper.png": "2ebcac7fd1566197afa9addc3eed9f8125ca86c4b7f58872116420ce5fc774cf",
        "thyca-bg-menu.png": "55612a9bedb10729079786cb7bf0b1788b8c688c0b0ae1d04c6ba9828d12806b",
    }.items():
        assert hashlib.sha256((WEBUI / "images" / image).read_bytes()).hexdigest() == digest, image


def test_selected_rows_use_shared_selected_token() -> None:
    kit = KIT_CSS.read_text(encoding="utf-8")
    chat = (ROOT / "thyca" / "webui" / "pages" / "chat" /
            "chat.css").read_text(encoding="utf-8")
    team = re.search(
        r"#team-bot-list \.session-item\.is-active\s*\{([^}]*)\}", chat)
    assert team and "background: var(--color-bg-selected)" in team.group(1)
    assert "team-tint" not in team.group(1), (
        "selected bot row must not tint-mix the card colour")
    screen = re.search(
        r"\.screen-shell \.session-item\.is-active\s*\{([^}]*)\}", kit)
    assert screen and "background: var(--color-bg-selected)" in screen.group(1)
    selected_washes = re.findall(r"\.session-item\.is-active::before\s*\{[^}]*\}", kit)
    assert selected_washes, "shared selected-row wash missing"
    for block in selected_washes:
        assert "background: var(--color-bg-selected)" in block, block


def test_search_widget_uses_surface_tokens() -> None:
    kit = KIT_CSS.read_text(encoding="utf-8")
    for gone in ("#f5e9da", "#fffcfa", "rgba(245, 233, 218"):
        assert gone not in kit, gone
    search = _declarations(_blocks(kit, ".thyca-search"))
    for alias, token in {
        "--fill": "--color-paper-input", "--ink": "--color-ink",
        "--terra": "--color-accent", "--border": "--color-border",
        "--muted": "--color-muted",
    }.items():
        assert search.get(alias) == f"var({token})", alias
    assert search.get("background") == "var(--fill)"
    assert "var(--color-accent)" in search.get("--border-strong", "")
    assert "var(--color-border)" in search.get("--border-strong", "")
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
    assert "--trace-code-ink: oklch(28% 0.05 44)" in light.group(1)
    dusk = re.search(r"html\[data-theme=\"dusk\"\]\s*#trace\s*\{([^}]*)\}", css)
    assert dusk, "dusk trace block missing"
    assert re.search(r"--trace-code-bg:\s*oklch\(24%", dusk.group(1)), (
        "dusk keeps its dark syntax surface")
    assert re.search(r"--trace-code-ink:\s*oklch\(89%", dusk.group(1))
    code = _declarations(_blocks(css, "#trace .trace-code"))
    assert code.get("background") == "var(--trace-code-bg)"
    assert code.get("color") == "var(--trace-code-ink)"
