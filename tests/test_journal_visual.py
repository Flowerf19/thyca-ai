"""Static CSS contracts for the rewritten WebUI (``thyca/webui/ui/``).

The old pager styles lived in ``shared/css/kit.css`` + per-page files that no
longer exist; the merged dashboard uses one plain ``.quiet-button`` pager
and scopes Trace detail code surfaces under ``.dashboard-shell``. These
assertions pin stylesheet ownership. Absence of the legacy pager/screen
styles is pinned once in tests/test_dropped_markers.py.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEBUI = ROOT / "thyca" / "webui"
UI = WEBUI / "ui"


def _read(rel: str) -> str:
    return (UI / rel).read_text(encoding="utf-8")


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
    for banned in ("url(", "linear-gradient", "radial-gradient"):
        assert banned not in block and banned not in err
