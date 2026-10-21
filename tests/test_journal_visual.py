"""Static CSS contracts for the rewritten WebUI (``thyca/webui/ui/``).

The old pager styles lived in ``shared/css/kit.css`` + per-page files that no
longer exist; the new UI pages ``Tải thêm`` through a plain ``.quiet-button``
and renders Trace code payloads through a scoped ``.tl-code`` surface in
``ui/trace/tracecss/trace.css``. These assertions pin the scoped stylesheet
contract only — real Chrome screenshots (desktop 1440 / mobile 320-414,
light and dusk) remain the gate for how the surfaces look after integration.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEBUI = ROOT / "thyca" / "webui"
UI = WEBUI / "ui"


def _read(rel: str) -> str:
    return (UI / rel).read_text(encoding="utf-8")


def _css_files() -> list[Path]:
    return sorted(UI.rglob("*.css"))


def test_no_legacy_pager_or_screen_button_styles() -> None:
    """The 12/page .journal-pager/.session-pager navs and the card
    .screen-button look were dropped with the old UI: no stylesheet under
    ui/ may still select them. Pagination is a server-driven "Tải thêm"
    button (ui/trace + ui/dashboard #trace-more)."""
    for path in _css_files():
        css = path.read_text(encoding="utf-8")
        assert ".journal-pager" not in css, path.name
        assert ".session-pager" not in css, path.name
        assert ".screen-button" not in css, path.name


def test_load_more_is_a_plain_quiet_button() -> None:
    """Both paged lists reuse the shared .quiet-button for "Tải thêm"; the
    footer keeps its own styles only."""
    for rel in ("dashboard/js/dashboard.js", "trace/js/trace.js"):
        src = _read(rel)
        assert 'class="quiet-button" id="trace-more"' in src, rel
        assert "Tải thêm" in src, rel
    shell_css = _read("share/app-shell.css")
    assert ".quiet-button" in shell_css
    # The button base style stays global — no page-scoped button resets.
    assert ".quiet-button:hover" in shell_css


def test_trace_code_uses_opaque_scoped_surface() -> None:
    """Input/Output code blocks (.tl-code, shared by tool steps and orphan
    results) sit on an opaque flat surface with a border — never the paper
    token — and the error variant keeps its own tint."""
    trace = _read("trace/tracecss/trace.css")
    assert ".tl-code{" in trace
    block = trace[trace.index(".tl-code{"):]
    block = block[: block.index("}")]
    assert "background:#fff8ec" in block
    assert "border:1px solid" in block
    assert "white-space:pre-wrap" in block
    assert "overflow-wrap:anywhere" in block
    # Scroll cap keeps long payloads inside the card.
    assert "max-height:320px" in block
    err = trace[trace.index(".tl-code.tl-code-err{"):]
    err = err[: err.index("}")]
    assert "background:#fdf0ea" in err
    assert "color:#944f3d" in err
    # No texture/image/gradient anywhere in the trace stylesheet.
    for banned in ("url(", "linear-gradient", "radial-gradient"):
        assert banned not in trace
