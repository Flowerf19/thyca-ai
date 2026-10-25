"""Read-only source-backed Usage/Trace verification with canonical sessions."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

from test_webui_setup_gate_source_e2e import (
    _browser_e2e,
    _free_port,
    _start_source_cli,
    _stop_source_cli,
    _wait_server,
)

ROOT = Path(__file__).resolve().parents[1]


BROWSER_SCRIPT = r"""
(() => {
  const mode = new URLSearchParams(location.search).get('sourceMode');
  window.__sourceApiCalls = [];
  window.__sourceResponses = [];
  window.__sourceErrors = [];
  window.addEventListener('error', event => window.__sourceErrors.push(String(event.message) + ' ' + String(event.filename) + ':' + event.lineno + ':' + event.colno + ' ' + String(event.error?.stack || '')));
  window.addEventListener('unhandledrejection', event => window.__sourceErrors.push(String(event.reason)));
  const originalFetch = window.fetch.bind(window);
  window.fetch = (input, init = {}) => {
    const raw = typeof input === 'string' ? input : input.url;
    const url = new URL(raw, location.href);
    const method = String(init.method || (typeof input === 'object' && input.method) || 'GET').toUpperCase();
    if (url.origin === location.origin && url.pathname.startsWith('/api/')) window.__sourceApiCalls.push({ method, url: url.pathname + url.search });
    return originalFetch(input, init).then(response => {
      if (url.origin === location.origin && url.pathname.startsWith('/api/')) {
        response.clone().json().then(payload => window.__sourceResponses.push({ method, path: url.pathname, search: url.search, payload })).catch(() => {});
      }
      return response;
    });
  };
  const wait = async (condition, label) => {
    const until = Date.now() + 15000;
    while (Date.now() < until) {
      if (condition()) return;
      await new Promise(resolve => setTimeout(resolve, 25));
    }
    throw new Error('timeout: ' + label);
  };
  const finish = result => {
    window.__sourceResult = result;
    if (document.body) document.body.dataset.sourceE2eResult = JSON.stringify(result);
    else setTimeout(() => finish(result), 0);
  };
  const show = (el, selector) => {
    const found = el.querySelector(selector);
    return !!found && !found.hidden;
  };
  const overviewResponses = () => ({
    stats: window.__sourceResponses.find(entry => entry.path === '/api/traces/stats')?.payload || null,
    list: window.__sourceResponses.find(entry => entry.path === '/api/traces' && entry.search.includes('offset=0'))?.payload || null,
  });
  const captureLayout = (view, overview) => {
    const scroller = view.querySelector('#usage-scroll');
    const inner = scroller.querySelector('.page-inner');
    const activePanel = view.querySelector('.usage-view-panel:not([hidden])');
    const content = [document.documentElement, document.body, scroller, inner,
      view.querySelector(overview ? '#usage-overview' : '#turn-detail-view'), activePanel,
      ...view.querySelectorAll(overview ? '.tl-turn' : '#detail-body, .tl-detail-head, .tl-timeline')];
    const overviewSection = view.querySelector('#usage-overview');
    const workspaceDisplay = getComputedStyle(view).display;
    const rangeActions = view.querySelector('#usage-header-controls .page-actions');
    const rangeActionsRect = rangeActions.getBoundingClientRect();
    const rangeChildren = [...rangeActions.children].filter(el => !el.hidden && getComputedStyle(el).display !== 'none');
    return {
      viewport: innerWidth,
      documentWidth: document.documentElement.scrollWidth,
      bodyWidth: document.body.scrollWidth,
      contentWidth: inner.clientWidth,
      contentScrollWidth: inner.scrollWidth,
      overflows: content.filter(el => el && el.scrollWidth > el.clientWidth + 1)
        .map(el => ({ id: el.id, className: el.className, clientWidth: el.clientWidth, scrollWidth: el.scrollWidth })),
      childOverflows: [...view.querySelectorAll('*')].filter(el => el.clientWidth > 0 && el.scrollWidth > el.clientWidth + 1)
        .map(el => ({ tag: el.tagName, id: el.id, className: String(el.className || ''), clientWidth: el.clientWidth, scrollWidth: el.scrollWidth })).slice(0, 15),
      nav: [document.querySelector('.page-links').getBoundingClientRect().left, document.querySelector('.page-links').getBoundingClientRect().right],
      activeLink: [document.querySelector('.page-links [aria-current="page"]').getBoundingClientRect().left, document.querySelector('.page-links [aria-current="page"]').getBoundingClientRect().right],
      overviewVisible: show(view, '#usage-overview'),
      overviewRowGap: getComputedStyle(overviewSection).rowGap,
      workspaceDisplay,
      activePanel: activePanel?.id || null,
      botCostHidden: view.querySelector('#card-bot-cost').hidden,
      headerControlsHidden: view.querySelector('#usage-header-controls').hidden,
      rangeControlsVisible: ['#range-from', '#range-to'].every(selector => {
        const rect = view.querySelector(selector).getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
      }),
      rangeActionsFit: rangeActionsRect.left >= view.querySelector('.page-nav').getBoundingClientRect().left - 1 &&
        rangeActionsRect.right <= view.querySelector('.page-nav').getBoundingClientRect().right + 1 &&
        rangeActions.scrollWidth <= rangeActions.clientWidth + 1 && rangeChildren.every(el => {
          const rect = el.getBoundingClientRect();
          return rect.left >= rangeActionsRect.left - 1 && rect.right <= rangeActionsRect.right + 1;
        }),
      rangeActionLayout: {
        nav: [view.querySelector('.page-nav').getBoundingClientRect().left, view.querySelector('.page-nav').getBoundingClientRect().right],
        actions: [rangeActionsRect.left, rangeActionsRect.right, rangeActions.clientWidth, rangeActions.scrollWidth],
        wrapper: [view.querySelector('#usage-header-controls').getBoundingClientRect().left, view.querySelector('#usage-header-controls').getBoundingClientRect().right],
        note: [view.querySelector('#range-note').getBoundingClientRect().left, view.querySelector('#range-note').getBoundingClientRect().right],
        title: [view.querySelector('.page-title').getBoundingClientRect().left, view.querySelector('.page-title').getBoundingClientRect().right],
        presets: [view.querySelector('.range-presets').getBoundingClientRect().left, view.querySelector('.range-presets').getBoundingClientRect().right],
        flexDirection: getComputedStyle(view.querySelector('#usage-header-controls')).flexDirection,
        navFlex: getComputedStyle(view.querySelector('.page-nav')).flexDirection,
        navAlign: getComputedStyle(view.querySelector('.page-nav')).alignItems,
      },
      detailVisible: show(view, '#turn-detail-view'),
      statValues: [...view.querySelectorAll('#stat-grid .stat-value')].map(el => el.textContent.trim()),
      visibleTurnText: view.querySelector('#trace-sessions').textContent,
      view: new URLSearchParams(ThycaRouter.currentRoute().search).get('view') || 'usage',
      detailText: view.querySelector('#detail-body').textContent,
      route: ThycaRouter.currentRoute(),
      returnTo: history.state?.thycaReturn || null,
      iframeCount: document.querySelectorAll('iframe').length,
      errors: [...window.__sourceErrors],
    };
  };
  const run = async () => {
    await wait(() => {
      const candidate = document.querySelector('[data-page-workspace="dashboard"]');
      return window.ThycaRouter?.setupState() === 'ready' && window.ThycaRouter.currentPage() === 'dashboard' &&
        !!candidate?.querySelector('#turn-detail-view');
    }, 'ready Usage workspace');
    const view = document.querySelector('[data-page-workspace="dashboard"]');
    const detail = view.querySelector('#turn-detail-view');
    await wait(() => !detail.hidden && view.querySelector('#detail-body').textContent.includes('GROUP_CANONICAL_REPLY'), 'source-backed canonical detail');
    const detailPath = path => /^\/api\/traces\/[^/]+\/\d+$/.test(path);
    const detailPayload = window.__sourceResponses.find(entry => detailPath(entry.path))?.payload || null;
    const detailCalls = window.__sourceApiCalls.filter(entry => detailPath(new URL(entry.url, location.origin).pathname));
    if (!detailPayload || detailCalls.length !== 1 || detailCalls[0].method !== 'GET') throw new Error('fresh canonical detail must issue exactly one read-only detail GET: ' + JSON.stringify(detailCalls));
    const detailText = view.querySelector('#detail-body').textContent;
    for (const text of ['GROUP_CANONICAL_REPLY', 'GROUP_TOOL_RESULT', 'GROUP_REASONING']) {
      if (detailText.split(text).length !== 2) throw new Error('canonical detail omitted or duplicated ' + text);
    }
    if (detailText.includes('DM_PRIVATE_SENTINEL')) throw new Error('detail crossed canonical session/window boundaries');
    if (window.__sourceApiCalls.some(entry => entry.method !== 'GET')) throw new Error('read-only Usage E2E made a non-GET API call');
    if (mode === 'source-detail') {
      await new Promise(resolve => setTimeout(resolve, 100));
      if (window.__sourceErrors.length) throw new Error('browser console/runtime errors: ' + JSON.stringify(window.__sourceErrors));
      finish({ mode, detail: captureLayout(view, false), detailApiCalls: detailCalls,
        detailPayload: { session_id: detailPayload.session_id, turn_index: detailPayload.turn_index,
          requests: detailPayload.requests, tool_calls: detailPayload.tool_calls, total_tokens: detailPayload.total_tokens,
          cost_usd: detailPayload.cost_usd }, apiCalls: window.__sourceApiCalls,
        errors: window.__sourceErrors, iframeCount: document.querySelectorAll('iframe').length });
      return;
    }
    view.querySelector('#detail-back').click();
    await wait(() => detail.hidden && !view.querySelector('#usage-overview').hidden &&
      window.__sourceResponses.some(entry => entry.path === '/api/traces/stats') &&
      window.__sourceResponses.some(entry => entry.path === '/api/traces' && entry.search.includes('offset=0')),
      'all-time Usage overview data after detail back');
    const payloads = overviewResponses();
    if (!payloads.stats || !payloads.list) throw new Error('Usage API payloads missing after detail back');
    const turns = payloads.list.traces || [];
    const groupId = detailPayload.session_id;
    const groupTurn = turns.find(turn => turn.session_id === groupId && turn.turn_index === 0);
    if (!groupTurn) throw new Error('canonical group turn missing from all-time overview API response');
    const statValues = [...view.querySelectorAll('#stat-grid .stat-value')].map(el => el.textContent.trim());
    const total = payloads.stats.totals || {};
    const costText = value => '$' + Number(value || 0).toFixed(value >= 1 ? 2 : value >= 0.01 ? 4 : 6);
    if (statValues[0] !== Number(total.requests || 0).toLocaleString('vi-VN') ||
        statValues[2] !== Number(total.total_tokens || 0).toLocaleString('vi-VN') ||
        statValues[3] !== costText(total.cost_usd)) throw new Error('overview stat cards disagree with the exact stats API response: ' + JSON.stringify({ statValues, total }));
    const groupButton = [...view.querySelectorAll('#trace-sessions button[data-session]')]
      .find(button => button.dataset.session === groupId && button.dataset.turn === '0');
    if (!groupButton || !groupButton.textContent.includes(groupTurn.title) ||
        !groupButton.textContent.includes(groupTurn.model) || !groupButton.textContent.includes(costText(groupTurn.cost_usd))) {
      throw new Error('canonical turn row disagrees with the exact list API response');
    }
    const statsCount = window.__sourceResponses.filter(entry => entry.path === '/api/traces/stats').length;
    const q = view.querySelector('#filter-q');
    q.value = groupTurn.title; q.dispatchEvent(new Event('input', { bubbles: true }));
    await wait(() => window.__sourceResponses.some(entry => entry.path === '/api/traces' && entry.search.includes('q=')), 'Usage search list request');
    const filtered = window.__sourceResponses.filter(entry => entry.path === '/api/traces' && entry.search.includes('q=')).at(-1);
    if (window.__sourceResponses.filter(entry => entry.path === '/api/traces/stats').length !== statsCount) throw new Error('table-only search changed statistical API reads');
    if (!filtered?.payload?.traces?.some(turn => turn.session_id === groupId && turn.turn_index === 0)) throw new Error('search API results lost the canonical group turn');
    if (JSON.stringify(payloads.stats.totals) !== JSON.stringify(total)) throw new Error('Usage filter changed API statistical semantics');
    const filteredButton = [...view.querySelectorAll('#trace-sessions button[data-session]')]
      .find(button => button.dataset.session === groupId && button.dataset.turn === '0');
    if (!filteredButton) throw new Error('filtered group turn row missing');
    const scroller = view.querySelector('#usage-scroll');
    scroller.scrollTop = Math.min(60, scroller.scrollHeight);
    filteredButton.focus({ preventScroll: true });
    const scrollBefore = scroller.scrollTop;
    const overviewScreenshot = captureLayout(view, true);
    const beforeTabCounts = { stats: window.__sourceApiCalls.filter(entry => entry.url.startsWith('/api/traces/stats')).length,
      lists: window.__sourceApiCalls.filter(entry => new URL(entry.url, location.origin).pathname === '/api/traces').length };
    view.querySelector('[data-usage-view="cost"]').click();
    await wait(() => !view.querySelector('#usage-panel-cost').hidden, 'Cost content view');
    if (window.__sourceApiCalls.filter(entry => entry.url.startsWith('/api/traces/stats')).length !== beforeTabCounts.stats) throw new Error('view change refetched stats');
    view.querySelector('[data-usage-view="trace"]').click();
    await wait(() => !view.querySelector('#usage-panel-trace').hidden, 'Trace content view');
    filteredButton.click();
    await wait(() => !detail.hidden && ThycaRouter.currentRoute().hash === '#' + groupId + '/0' &&
      view.querySelector('#detail-body').textContent.includes('GROUP_CANONICAL_REPLY'), 'filtered canonical detail reopened');
    const reopened = window.__sourceApiCalls.filter(entry => entry.url.includes('/api/traces/' + groupId + '/0'));
    if (reopened.length !== 2) throw new Error('reopened detail did not fetch exactly once per opening');
    view.querySelector('#detail-back').click();
    await wait(() => detail.hidden && !view.querySelector('#usage-overview').hidden && ThycaRouter.currentRoute().hash === '' && document.activeElement === filteredButton,
      'detail return restores overview route and focused row');
    const focusRestored = document.activeElement === filteredButton;
    const scrollAfter = scroller.scrollTop;
    if (scroller.scrollTop !== scrollBefore) throw new Error('detail return did not restore overview scroll position: ' + JSON.stringify({ scrollBefore, scrollAfter: scroller.scrollTop, scrollHeight: scroller.scrollHeight, clientHeight: scroller.clientHeight, route: ThycaRouter.currentRoute() }));
    if (innerWidth <= 850 && !document.querySelector('#chat-sidebar').classList.contains('mobile-open')) {
      document.querySelector('.menu-button').click();
      await wait(() => document.querySelector('#chat-sidebar').classList.contains('mobile-open'), 'open mobile bot sidebar');
    }
    const sidebarBot = document.querySelector('#chat-sidebar #team-bot-list [data-action="select"][data-bot-id="coder"]');
    if (!sidebarBot || sidebarBot.getAttribute('aria-label') !== 'Xem sử dụng của Coder') throw new Error('Usage bot action label is not explicit');
    sidebarBot.click();
    if (innerWidth <= 850) await wait(() => !document.querySelector('#chat-sidebar').classList.contains('mobile-open'), 'bot selection closes mobile drawer');
    await wait(() => new URLSearchParams(ThycaRouter.currentRoute().search).get('bot') === 'coder' &&
      window.__sourceResponses.some(entry => entry.path === '/api/traces/stats' && entry.search.includes('bot_id=coder')) &&
      window.__sourceResponses.some(entry => entry.path === '/api/traces' && entry.search.includes('bot_id=coder') && entry.search.includes('offset=0')),
      'bot-scoped stats and trace');
    if (new URLSearchParams(ThycaRouter.currentRoute().search).get('view') !== 'trace') throw new Error('bot selection changed the current content view');
    const scopedStats = window.__sourceResponses.filter(entry => entry.path === '/api/traces/stats' && entry.search.includes('bot_id=coder')).at(-1)?.payload;
    if (!scopedStats || scopedStats.totals?.requests !== total.requests || scopedStats.totals?.total_tokens !== total.total_tokens) throw new Error('coder private/shared attribution does not match authoritative source totals: ' + JSON.stringify(scopedStats?.totals));
    view.querySelector('[data-usage-view="cost"]').click();
    await wait(() => !view.querySelector('#usage-panel-cost').hidden && view.querySelector('#card-bot-cost').hidden, 'scoped Cost hides the team bot breakdown');
    const scopedCostHidden = view.querySelector('#card-bot-cost').hidden;
    view.querySelector('[data-usage-view="trace"]').click();
    await wait(() => !view.querySelector('#usage-panel-trace').hidden, 'scoped Trace content');
    const scopedGroup = [...view.querySelectorAll('#trace-sessions button[data-session]')].find(button => button.dataset.session === groupId && button.dataset.turn === '0');
    if (!scopedGroup) throw new Error('scoped canonical shared turn missing');
    scopedGroup.click();
    await wait(() => !detail.hidden && view.querySelector('#detail-body .own-contribution') && view.querySelector('#detail-body').textContent.includes('GROUP_CANONICAL_REPLY'), 'bot contribution plus full canonical detail');
    const scopedDetailText = view.querySelector('#detail-body').textContent;
    if (!scopedDetailText.includes('Đóng góp của Coder') || !scopedDetailText.includes('toàn bộ lượt')) throw new Error('bot contribution and complete turn context are not distinguished');
    view.querySelector('#detail-back').click();
    await wait(() => detail.hidden && ThycaRouter.currentRoute().hash === '' && new URLSearchParams(ThycaRouter.currentRoute().search).get('bot') === 'coder', 'return to scoped Trace');
    document.querySelector('#chat-sidebar #usage-overview-scope').click();
    await wait(() => !new URLSearchParams(ThycaRouter.currentRoute().search).has('bot') && new URLSearchParams(ThycaRouter.currentRoute().search).get('view') === 'trace', 'dedicated Overview scope clears bot');
    if (window.__sourceApiCalls.some(entry => entry.method !== 'GET')) throw new Error('read-only Usage E2E made a non-GET API call');
    if (document.querySelectorAll('iframe').length) throw new Error('SPA introduced an iframe');
    await new Promise(resolve => setTimeout(resolve, 100));
    if (window.__sourceErrors.length) throw new Error('browser console/runtime errors: ' + JSON.stringify(window.__sourceErrors));
    finish({ mode, canonicalId: groupId, detailPayload: { session_id: detailPayload.session_id, turn_index: detailPayload.turn_index,
        requests: detailPayload.requests, tool_calls: detailPayload.tool_calls, total_tokens: detailPayload.total_tokens,
        cost_usd: detailPayload.cost_usd }, statsTotals: total, listTotal: payloads.list.total,
      groupTurn, statValues, searchUrl: filtered.search, searchTotal: filtered.payload.total,
      overviewApiCounts: { stats: statsCount, lists: window.__sourceResponses.filter(entry => entry.path === '/api/traces').length,
        detail: reopened.length }, scopedBot: 'coder', scopedSessions: scopedStats.totals.sessions,
      scopedTraceDetail: true, scopedCostHidden,
      mobileMenuClosed: innerWidth > 850 || !document.querySelector('#chat-sidebar').classList.contains('mobile-open'),
      overview: overviewScreenshot, returnedOverview: captureLayout(view, true),
      focusRestored, scrollBefore, scrollAfter,
      apiCalls: window.__sourceApiCalls, errors: window.__sourceErrors, iframeCount: document.querySelectorAll('iframe').length });
  };
  run().catch(error => finish({ error: String(error), stack: error.stack, apiCalls: window.__sourceApiCalls, errors: window.__sourceErrors }));
})();
"""


def _seed_source_fixture(root: Path) -> tuple[str, dict[str, bytes]]:
    from thyca.bots.card import BotCard
    from thyca.bots.timeline import WorkspaceTimeline
    from thyca.config import Config, ModelCfg, ProviderEntry, save
    from thyca.core.protocol import Message, ToolCall
    from thyca.sessions.manager import SessionManager
    from thyca.app.chat_app import ChatApp

    provider = ProviderEntry(baseUrl="http://127.0.0.1:1/v1", apiKey="synthetic-source-fixture-key")
    config = Config(providers={"fixture": provider}, defaultProvider="fixture",
                    models={"fixture-model": ModelCfg(provider="fixture")}, defaultModel="fixture-model")
    save(config, root / "config.json")
    app = ChatApp(root, config, connect=lambda *_args, **_kwargs: None)
    try:
        app._bots.create(BotCard(bot_id="coder", name="Coder", role="Coder", description="Fixture bot.", persona=""))
        dm = app.create(bots=["coder"])
        group = app.create(bots=["thyca", "coder"])
        dm_window = app._webui.window_for(app._sessions.store.load(dm["id"]))
        group_window = app._webui.window_for(app._sessions.store.load(group["id"]))
        timeline = WorkspaceTimeline(root, "coder")
        span = "source_usage_trace_fixture_span"

        def source_rows(prefix: str, private: bool) -> list[Message]:
            if private:
                assistant = "DM_PRIVATE_SENTINEL"
                tool = "DM_PRIVATE_TOOL_SENTINEL"
            else:
                assistant = "GROUP_CANONICAL_REPLY"
                tool = "GROUP_TOOL_RESULT"
            meta = {"kind": "llm", "bot_id": "coder", "span_id": span,
                    "model": "fixture-model", "requests": 2, "tool_call_count": 1,
                    "rounds": 2, "usage": {"prompt_tokens": 10, "cached_tokens": 2,
                    "completion_tokens": 3, "total_tokens": 13}, "cost_usd": 0.125,
                    "latency_ms": 120, "reasoning": "GROUP_REASONING" if not private else "PRIVATE_REASONING"}
            return [
                Message(role="user", content=f"{prefix} public aggregate question", ts="2026-09-20T10:00:00Z"),
                Message(role="assistant", content=assistant,
                        tool_calls=[ToolCall(id=f"{prefix}-call", name="fixture_tool", arguments={"query": prefix})],
                        reasoning=meta["reasoning"], ts="2026-09-20T10:00:01Z", meta=meta),
                Message(role="tool", content=tool, tool_call_id=f"{prefix}-call",
                        ts="2026-09-20T10:00:02Z", meta={"kind": "tool_result", "bot_id": "coder", "span_id": span}),
            ]

        for window, prefix, private in ((dm_window, "dm", True), (group_window, "group", False)):
            for idx, message in enumerate(source_rows(prefix, private)):
                timeline.append_message(window, message, event_id=f"{prefix}-event-{idx}")
        timeline.replace_view(group_window, source_rows("group", False), event_id="group-view-reset")
        app._runtime.workspaces.store.bind_window("coder", dm_window)
        app._runtime.workspaces.store.bind_window("coder", group_window)

        manager = SessionManager(root / "sessions")
        for room, title, text in ((dm, "DM source fixture", "DM session public question"),
                                  (group, "Group source fixture", "Group session public question")):
            manager.load(room["id"])
            manager.set_title(title, source="fixture")
            manager.append(Message(role="user", content=text, ts="2026-09-20T09:59:00Z"))
            manager.append(Message(role="assistant", content="Fixture acknowledgement", ts="2026-09-20T09:59:01Z",
                                   meta={"kind": "message", "bot_id": "coder", "span_id": span,
                                         "model": "fixture-model", "requests": 2, "tool_call_count": 1,
                                         "rounds": 2, "usage": {"prompt_tokens": 10, "cached_tokens": 2,
                                         "completion_tokens": 3, "total_tokens": 13}, "cost_usd": 0.125,
                                         "latency_ms": 120}))
        files = [root / "config.json", root / "auth.json", *sorted((root / "sessions").glob("*.jsonl")),
                 *sorted((root / "bots").glob("**/timeline.jsonl"))]
        baseline = {str(path.relative_to(root)): path.read_bytes() for path in files if path.exists()}
        group_id = group["id"]
    finally:
        app.shutdown()
    return group_id, baseline


def _source_cli(chrome: str, tmp_path: Path, mode: str, *, width: int = 1280, height: int = 900, mobile: bool = False) -> dict:
    root = tmp_path / "source-runtime"
    root.mkdir(parents=True)
    group_id, baseline = _seed_source_fixture(root)
    port = _free_port()
    source_env = dict(os.environ, HOME="/tmp/sandbox-home", PYTHONPATH=str(ROOT))
    source_env.pop("THYCA_TOKEN", None)
    server_log = tmp_path / "usage-trace-source.log"
    process, log_handle = _start_source_cli(root, port, source_env, server_log)
    user_data = tmp_path / "chrome-profile"
    stop_proof: list[dict] = []
    token = str(int(time.time() * 1000))
    script = BROWSER_SCRIPT.replace("const mode = new URLSearchParams(location.search).get('sourceMode');", f"const mode = new URLSearchParams(location.search).get('sourceMode') || {json.dumps(mode)};")
    script = script.replace("__FIXTURE_GROUP_ID__", json.dumps(group_id))
    url = f"http://127.0.0.1:{port}/ui/trace/index.html?sourceMode={mode}&fixture={token}#{quote(group_id, safe='-_.')}/0"
    try:
        _wait_server(process, port)
        screenshot = tmp_path / f"usage-trace-source-{mode}-{width}x{height}.png"
        result = _browser_e2e(chrome, url, user_data, script, screenshot, width=width, height=height, mobile=mobile)
        after = {name: (root / name).read_bytes() for name in baseline}
        stable_screenshot = Path(f"/tmp/thyca-usage-bots-source-{mode}-{width}.png")
        shutil.copyfile(screenshot, stable_screenshot)
        result.update({"sourceBacked": True, "fixtureSessionId": group_id, "screenshot": str(stable_screenshot),
                       "serverLog": str(Path(f"/tmp/thyca-usage-bots-source-{mode}-{width}.log")), "readOnlyFilesUnchanged": after == baseline,
                       "readOnlyFileCount": len(baseline)})
        if not result.get("readOnlyFilesUnchanged"):
            result["changedFixtureFiles"] = [name for name in baseline if after.get(name) != baseline[name]]
    finally:
        _stop_source_cli(process, log_handle, port, stop_proof)
    result["runtimeStop"] = stop_proof
    shutil.copyfile(server_log, result["serverLog"])
    return result


def _chromium_binary() -> str | None:
    return (os.environ.get("THYCA_CHROME") or shutil.which("google-chrome")
            or shutil.which("chromium") or shutil.which("chromium-browser"))


def test_source_usage_detail_and_overview_are_canonical_read_only(tmp_path: Path) -> None:
    chrome = _chromium_binary()
    if not chrome:
        import pytest
        pytest.skip("Chromium is not installed; source-backed Usage E2E is required in the review environment")
    result = _source_cli(chrome, tmp_path, "source-flow")
    mobile = _source_cli(chrome, tmp_path / "mobile", "source-flow-mobile", width=375, mobile=True)
    Path("/tmp/thyca-usage-bots-mobile-scope.json").write_text(json.dumps(mobile, ensure_ascii=False, indent=2), encoding="utf-8")
    artifact = Path("/tmp/thyca-usage-bots-source-e2e.json")
    artifact.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    assert "error" not in result, result
    assert result.get("sourceBacked") and result.get("readOnlyFilesUnchanged") is True
    assert result.get("iframeCount") == 0 and result.get("errors") == []
    assert mobile.get("sourceBacked") and mobile.get("readOnlyFilesUnchanged") is True
    assert mobile.get("mobileMenuClosed") is True and mobile.get("errors") == []
    assert mobile.get("runtimeStop") and all(item.get("stopped") and item.get("exitCode") == 0 for item in mobile["runtimeStop"])
    assert result.get("runtimeStop") and all(item.get("stopped") and item.get("exitCode") == 0 for item in result["runtimeStop"])
    assert result.get("focusRestored") is True and result.get("scrollBefore") == result.get("scrollAfter")
    assert result["statsTotals"].get("requests") == 4
    assert result["groupTurn"].get("requests") == 2 and result["groupTurn"].get("total_tokens") == 13
    assert "q=" in result["searchUrl"] and result["searchTotal"] < result["listTotal"]
    assert result["overview"].get("overflows") == []
    assert result["overview"].get("overviewRowGap") == "22px"
    assert result["overview"].get("activePanel") == "usage-panel-trace"
    assert result["scopedBot"] == "coder" and result["scopedSessions"] == 2
    assert result["scopedTraceDetail"] is True and result["scopedCostHidden"] is True
    assert all(call["method"] == "GET" for call in result["apiCalls"])
    assert artifact.exists()


def test_source_usage_overview_and_detail_responsive_screenshots(tmp_path: Path) -> None:
    chrome = _chromium_binary()
    if not chrome:
        import pytest
        pytest.skip("Chromium is not installed; source-backed Usage E2E is required in the review environment")
    artifacts = []
    for mode, width, height, mobile in (("source-overview", 1280, 900, False),
                                        ("source-detail", 1280, 900, False),
                                        ("source-overview", 320, 900, True),
                                        ("source-detail", 320, 900, True),
                                        ("source-overview", 375, 900, True),
                                        ("source-detail", 375, 900, True)):
        result = _source_cli(chrome, tmp_path / f"{mode}-{width}", mode,
                             width=width, height=height, mobile=mobile)
        assert "error" not in result, result
        assert result.get("sourceBacked") and result.get("readOnlyFilesUnchanged") is True
        assert result.get("iframeCount") == 0
        assert result.get("runtimeStop") and all(item.get("stopped") and item.get("exitCode") == 0 for item in result["runtimeStop"])
        layout = result["detail"] if mode == "source-detail" else result.get("overview", result.get("returnedOverview"))
        assert layout["documentWidth"] <= width and layout["bodyWidth"] <= width, layout
        assert layout["overflows"] == [], layout
        if mode == "source-overview":
            assert layout["rangeControlsVisible"] and layout["rangeActionsFit"], layout
        assert layout["detailVisible"] is (mode == "source-detail"), layout
        assert layout["workspaceDisplay"] == "flex", layout
        assert layout["overviewVisible"] is (mode == "source-overview"), layout
        assert layout["headerControlsHidden"] is (mode == "source-detail"), layout
        artifacts.append({"mode": mode, "viewport": width, "screenshot": result["screenshot"],
                          "layout": layout, "fixtureSessionId": result["fixtureSessionId"],
                          "runtimeStop": result["runtimeStop"]})
    evidence = Path("/tmp/thyca-usage-bots-source-responsive.json")
    evidence.write_text(json.dumps(artifacts, ensure_ascii=False, indent=2), encoding="utf-8")
    assert evidence.exists()
