"""Static CSS contracts for the rewritten WebUI (``thyca/webui/ui/``).

The old pager styles lived in ``shared/css/kit.css`` + per-page files that no
longer exist; the merged dashboard uses one plain ``.quiet-button`` pager
and scopes Trace detail code surfaces under ``.dashboard-shell``. These
assertions pin stylesheet ownership — the legacy Trace CSS asset remains
available but is not the active detail stylesheet.
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
    """Legacy pagers and screen-button styles remain absent; the merged
    server-driven pager belongs to the Dashboard workspace."""
    for path in _css_files():
        css = path.read_text(encoding="utf-8")
        assert ".journal-pager" not in css, path.name
        assert ".session-pager" not in css, path.name
        assert ".screen-button" not in css, path.name


def test_load_more_is_owned_by_dashboard() -> None:
    src = _read("dashboard/js/dashboard.js")
    assert 'class="quiet-button" id="trace-more"' in src
    assert "Tải thêm" in src
    renderer = _read("trace/js/trace.js")
    assert 'id="trace-more"' not in renderer and "ThycaRouter.register" not in renderer
    shell_css = _read("share/app-shell.css")
    assert ".quiet-button" in shell_css
    # The button base style stays global — no page-scoped button resets.
    assert ".quiet-button:hover" in shell_css


def test_trace_code_uses_opaque_scoped_surface() -> None:
    """Input/Output code blocks (.tl-code, shared by tool steps and orphan
    results) sit on an opaque flat surface with a border — never the paper
    token — and the error variant keeps its own tint."""
    trace = _read("dashboard/dashboardcss/dashboard.css")
    assert ".dashboard-shell .tl-code{" in trace
    block = trace[trace.index(".dashboard-shell .tl-code{"):]
    block = block[: block.index("}")]
    assert "background:#fff8ec" in block
    assert "border:1px solid" in block
    assert "white-space:pre-wrap" in block
    assert "overflow-wrap:anywhere" in block
    # Scroll cap keeps long payloads inside the card.
    assert "max-height:320px" in block
    err = trace[trace.index(".dashboard-shell .tl-code.tl-code-err{"):]
    err = err[: err.index("}")]
    assert "background:#fdf0ea" in err
    assert "color:#944f3d" in err
    legacy = _read("trace/tracecss/trace.css")
    assert ".tl-code{" in legacy  # preserved legacy asset; Dashboard rules are separately scoped
    for banned in ("url(", "linear-gradient", "radial-gradient"):
        assert banned not in block and banned not in err
