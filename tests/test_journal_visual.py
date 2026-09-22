"""Static CSS contracts for the compact pagers and the opaque Trace code
surface (GOAL-011 TASK-034/035).

These assertions pin the scoped stylesheet contract only: which file owns
which rules, that the card-looking .screen-button appearance is reset inside
the pagers, and that the payload code blocks no longer sit on the paper
token. They are NOT visual acceptance — real Chrome screenshots (desktop
1440 / mobile 320-414, light and dusk) remain the gate for how the pager and
code surface look after integration.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEBUI = ROOT / "thyca" / "webui"


def _read(name: str) -> str:
    return (WEBUI / name).read_text(encoding="utf-8")


def test_compact_pager_is_shared_in_screens_css() -> None:
    """One shared block styles the dashboard/trace .journal-pager and the chat
    .session-pager; neither may inherit the card .screen-button look."""
    screens = _read("shared/css/kit.css")
    assert ":is(.dashboard-shell, .trace-shell) .journal-pager" in screens
    assert ".session-pager" in screens
    # The old card appearance is reset inside the pager scope only.
    for reset in (
        "background: none",
        "border: 0",
        "border-radius: 0",
        "box-shadow: none",
    ):
        assert reset in screens
    # Touch target, restrained states and a modest tabular label.
    assert "min-height: 2.75rem" in screens
    assert "text-decoration: underline" in screens
    assert ":disabled" in screens
    assert "font-variant-numeric: tabular-nums" in screens


def test_old_pager_rules_are_gone_from_cost_and_styles() -> None:
    """cost.css no longer styles .journal-pager (it used to hit Cost and Trace
    at once) and styles.css keeps only the chat pager's placement margin."""
    cost = _read("pages/dashboard/cost.css")
    styles = _read("pages/chat/chat.css")
    assert ".journal-pager" not in cost
    assert ".session-pager .screen-button" not in styles
    assert ".session-pager-label" not in styles
    # Chat spacing stays, scoped to the pager itself.
    assert ".session-pager {" in styles
    assert "margin-block: 0.75rem 0" in styles


def test_pager_scoping_does_not_touch_global_button() -> None:
    """The global .screen-button base/hover blocks stay as they were; the
    pager block itself never selects .screen-button — it hooks the pager
    classes only, so other screens' buttons keep their card look."""
    screens = _read("shared/css/kit.css")
    assert ".screen-button, .screen-select, .screen-input {" in screens
    assert (
        ".screen-button:not(:disabled):hover { border-color: var(--color-accent); }"
        in screens
    )
    block = screens[screens.index("Compact journal pagers") : screens.index("@media (max-width: 56rem)", screens.index("Compact journal pagers"))]
    for legacy in (".journal-pager .screen-button", ".session-pager .screen-button"):
        assert legacy not in block
    assert "journal-pager-step" in block and "session-pager-step" in block


def test_trace_code_uses_opaque_scoped_surface() -> None:
    """Input/Output code blocks (turn and tool steps share .trace-code) sit on
    an opaque flat surface scoped to #trace, never the paper token."""
    trace = _read("pages/dashboard/trace.css")
    assert "--trace-code-bg:" in trace
    assert "--trace-code-ink:" in trace
    assert "background: var(--trace-code-bg)" in trace
    assert "color: var(--trace-code-ink)" in trace
    assert "var(--color-paper-input)" not in trace
    # Dusk gets its own surface; no texture/image/gradient anywhere.
    assert 'html[data-theme="dusk"] #trace' in trace
    for banned in ("url(", "linear-gradient", "radial-gradient"):
        assert banned not in trace
