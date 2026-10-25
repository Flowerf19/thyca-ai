"""Focused Usage dashboard scope/view behavior tests."""
from __future__ import annotations

import shutil

import pytest

from test_dashboard_journal import _eval_dash


@pytest.fixture(scope="module")
def node() -> str:
    binary = shutil.which("node")
    if not binary:
        pytest.skip("node not installed")
    return binary


def test_bot_selection_is_stable_and_preserves_content_view(node: str) -> None:
    out = _eval_dash(
        node,
        "__controller.selectBot('coder'); const first = { ...ctx.route };"
        "ctx.route.hash = '#s/0'; activeView = 'trace'; __controller.selectBot('coder'); const sameId = { ...ctx.route };"
        "__controller.loadSession('group', 'Group', 'coder');"
        "console.log(JSON.stringify({ first, sameId, route: ctx.route, botScope, activeView }))",
        "location.search = '?range=all&view=cost'; location.hash = '';"
    )
    assert out["first"]["search"] == "?bot=coder&range=all&view=cost"
    assert out["sameId"]["hash"] == ""
    assert out["route"]["page"] == "dashboard"
    assert out["route"]["search"] == "?bot=coder&range=all&view=trace"
    assert out["botScope"] == "coder" and out["activeView"] == "trace"


def test_content_view_is_excluded_from_authoritative_request_keys(node: str) -> None:
    out = _eval_dash(
        node,
        "readRoute({ page: 'dashboard', search: '?bot=coder&from=2026-09-01&to=2026-09-30&view=usage', hash: '' });"
        "const usage = [statsRequestKey(), listRequestKey()];"
        "readRoute({ page: 'dashboard', search: '?bot=coder&from=2026-09-01&to=2026-09-30&view=cost', hash: '' });"
        "const cost = [statsRequestKey(), listRequestKey()];"
        "console.log(JSON.stringify({ usage, cost, activeView }))",
    )
    assert out["usage"] == out["cost"]
    assert out["activeView"] == "cost"


def test_cost_bot_rows_are_hidden_when_scoped_and_unknown_is_informational(node: str) -> None:
    out = _eval_dash(
        node,
        "statsData = { totals: {}, by_bot: [{ bot_id: 'coder', total_tokens: 2, cost_usd: 0.1 },"
        "{ bot_id: 'unknown', total_tokens: 1, cost_usd: 0.2 }] };"
        "botScope = 'coder'; renderCostRows(); renderBotRows();"
        "console.log(JSON.stringify({ hidden: document.querySelector('#card-bot-cost').hidden,"
        " cost: document.querySelector('#rows-bot-cost').innerHTML, usage: document.querySelector('#rows-bot').innerHTML }))",
    )
    assert out["hidden"] is True
    assert 'data-action="select-bot" data-bot-id="unknown"' not in out["cost"]
    assert 'data-action="select-bot" data-bot-id="unknown"' not in out["usage"]


def test_explicit_bot_contribution_and_full_turn_context_are_distinguished(node: str) -> None:
    out = _eval_dash(
        node,
        "botScope = 'coder'; const contribution = botContributionHtml({ bot_breakdown: [{ bot_id: 'coder',"
        " requests: 1, tool_calls: 1, prompt_tokens: 10, cached_tokens: 2, completion_tokens: 3,"
        " total_tokens: 13, cost_usd: 0.1, latency_ms: null }] });"
        "detailData = { session_id: 's', turn_index: 0, bot_breakdown: [{ bot_id: 'coder' }], messages: [] };"
        "detailLoading = false; renderDetail();"
        "console.log(JSON.stringify({ contribution, detail: document.querySelector('#detail-body').innerHTML }))",
        "globalThis.__roster.coder = { id: 'coder', name: 'Coder', color: '#123', state: 'idle' };"
        "window.ThycaTraceDetail = { render: () => '<p>full canonical turn</p>' };",
    )
    assert "Đóng góp của Coder" in out["contribution"]
    assert "toàn bộ lượt" in out["detail"] and "Đóng góp của Coder" in out["detail"]


def test_dashboard_structure_and_scope_lifecycle_guards() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = (root / "thyca/webui/ui/dashboard/js/dashboard.js").read_text(encoding="utf-8")
    css = (root / "thyca/webui/ui/dashboard/dashboardcss/dashboard.css").read_text(encoding="utf-8")
    assert "let button = sidebar.querySelector('#usage-overview-scope')" in source
    assert "if (overviewButton) overviewButton.hidden = false" in source
    assert "if (overviewButton) overviewButton.hidden = true" in source
    assert "ctx.navigate('dashboard', { search: pageQuery(view), hash: '' })" in source
    assert "sidebarLabel(id, name, status) { return `Xem sử dụng của ${name}`; }" in source
    assert ".dashboard-shell .workspace{display:flex;flex-direction:column}" in css
    assert "repeat(7,minmax(0,1fr))" not in css
