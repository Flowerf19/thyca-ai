from __future__ import annotations

import base64
import json
import os
import re
import secrets
import shutil
import socket
import struct
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit
from urllib.request import Request, urlopen

import pytest

from thyca.serve.static import content_type, safe_file


ROOT = Path(__file__).resolve().parents[1]
WEBUI = ROOT / "thyca" / "webui"
PAGES = ("chat", "memories", "profile", "provider", "dashboard", "settings")
LEGACY_ENTRIES = PAGES + ("trace",)


def test_spa_static_entry_aliases_and_asset_mime() -> None:
    for path in ("/", "/index.html", "/ui/%70rofile/index.html", *(f"/ui/{page}{suffix}" for page in LEGACY_ENTRIES for suffix in ("", "/", "/index.html"))):
        assert safe_file(WEBUI, path) == (WEBUI / "ui/chat/index.html").resolve()
    assert content_type(safe_file(WEBUI, "/ui/share/router.js")) == "text/javascript; charset=utf-8"
    assert content_type(safe_file(WEBUI, "/ui/share/router.css")) == "text/css; charset=utf-8"
    for path in ("/ui/not-a-page/", "/ui/chat/%00", "/ui/chat/%2e%2e/secret", "/%2e%2e/secret"):
        assert safe_file(WEBUI, path) is None


def test_spa_static_rejects_symlink_escape(tmp_path: Path) -> None:
    webui = tmp_path / "webui"
    outside = tmp_path / "outside"
    (webui / "ui").mkdir(parents=True)
    outside.mkdir()
    (outside / "index.html").write_text("outside")
    (webui / "ui/chat").symlink_to(outside, target_is_directory=True)
    assert safe_file(webui, "/") is None


def test_root_and_legacy_aliases_serve_entry_over_http() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            target = safe_file(WEBUI, urlsplit(self.path).path)
            if target is None:
                self.send_error(404); return
            body = target.read_bytes()
            self.send_response(200); self.send_header("Content-Type", content_type(target)); self.end_headers(); self.wfile.write(body)

        def log_message(self, *_args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        bodies = []
        for path in ("/", "/index.html", "/ui/profile/index.html"):
            with urlopen(f"http://127.0.0.1:{server.server_port}{path}", timeout=3) as response:
                assert response.status == 200
                assert response.headers.get_content_type() == "text/html"
                bodies.append(response.read())
        assert bodies[0] == bodies[1] == bodies[2]
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=3)


def _read_exact(sock: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = sock.recv(size - len(chunks))
        if not chunk:
            raise ConnectionError("Chrome DevTools websocket closed")
        chunks.extend(chunk)
    return bytes(chunks)


def _cdp_frame(sock: socket.socket, payload: bytes) -> None:
    mask = secrets.token_bytes(4)
    size = len(payload)
    header = bytes([0x81, 0x80 | (size if size < 126 else 126 if size < 65536 else 127)])
    if size >= 126:
        header += struct.pack("!H" if size < 65536 else "!Q", size)
    sock.sendall(header + mask + bytes(value ^ mask[i % 4] for i, value in enumerate(payload)))


def _cdp_recv(sock: socket.socket) -> dict:
    while True:
        first, second = _read_exact(sock, 2)
        size = second & 0x7F
        if size == 126: size = struct.unpack("!H", _read_exact(sock, 2))[0]
        elif size == 127: size = struct.unpack("!Q", _read_exact(sock, 8))[0]
        mask = _read_exact(sock, 4) if second & 0x80 else b""
        payload = _read_exact(sock, size)
        if mask: payload = bytes(value ^ mask[i % 4] for i, value in enumerate(payload))
        opcode = first & 0x0F
        if opcode == 8: raise ConnectionError("Chrome DevTools websocket closed")
        if opcode == 1: return json.loads(payload)


def _cdp_browser_result(chrome: str, url: str, width: int, screenshot: Path | None, user_data: Path, *, mobile: bool = False) -> dict:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0)); port = probe.getsockname()[1]
    process = subprocess.Popen([
        chrome, "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
        "--no-first-run", "--no-default-browser-check", "--no-proxy-server",
        f"--remote-debugging-port={port}", f"--user-data-dir={user_data}", "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=dict(os.environ, HOME="/tmp/sandbox-home"))
    sock = None
    try:
        end = time.monotonic() + 10
        while True:
            try:
                with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=0.5):
                    break
            except OSError:
                if time.monotonic() >= end: raise
                time.sleep(0.05)
        with urlopen(Request(f"http://127.0.0.1:{port}/json/new?{quote('about:blank', safe=':')}", method="PUT"), timeout=3) as response:
            target = json.loads(response.read())
        ws_url = urlsplit(target["webSocketDebuggerUrl"])
        sock = socket.create_connection((ws_url.hostname, ws_url.port), timeout=10)
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        sock.sendall((f"GET {ws_url.path} HTTP/1.1\r\nHost: {ws_url.netloc}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        header = bytearray()
        while not header.endswith(b"\r\n\r\n"): header.extend(sock.recv(1))
        if b" 101 " not in header: raise ConnectionError(header.decode(errors="replace"))
        next_id = 0
        def call(method: str, params: dict | None = None) -> dict:
            nonlocal next_id
            next_id += 1; command_id = next_id
            _cdp_frame(sock, json.dumps({"id": command_id, "method": method, "params": params or {}}).encode())
            while True:
                response = _cdp_recv(sock)
                if response.get("id") == command_id:
                    if "error" in response: raise RuntimeError(response["error"])
                    return response.get("result", {})
        call("Page.enable")
        call("Runtime.enable")
        call("Emulation.setDeviceMetricsOverride", {"width": width, "height": 900, "deviceScaleFactor": 1, "mobile": mobile})
        call("Page.navigate", {"url": url})
        end = time.monotonic() + 30
        while time.monotonic() < end:
            result = call("Runtime.evaluate", {"expression": "document.body?.dataset.spaTestResult || ''", "returnByValue": True})
            encoded = result.get("result", {}).get("value")
            if encoded:
                data = json.loads(encoded)
                dom = call("Runtime.evaluate", {"expression": "document.documentElement.outerHTML", "returnByValue": True}).get("result", {}).get("value", "")
                if screenshot is None:
                    Path('/tmp/thyca-usage-trace-spa-last-result.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
                    Path('/tmp/thyca-usage-trace-spa-last-dom.html').write_text(dom, encoding='utf-8')
                else:
                    screenshot.with_suffix('.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
                    screenshot.with_suffix('.html').write_text(dom, encoding='utf-8')
                if screenshot is not None:
                    png = call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
                    screenshot.write_bytes(base64.b64decode(png["data"]))
                return data
            time.sleep(0.05)
        diagnostic = call("Runtime.evaluate", {"expression": "JSON.stringify({stage:window.__spaTestStage||'',route:window.ThycaRouter?.currentRoute(),visible:[...document.querySelectorAll('[data-page-workspace]')].filter(view=>!view.hidden).map(view=>view.dataset.pageWorkspace),errors:window.__scriptErrors||[],rejections:window.__promiseErrors||[]})", "returnByValue": True}).get("result", {}).get("value", "{}")
        dom = call("Runtime.evaluate", {"expression": "document.documentElement.outerHTML", "returnByValue": True}).get("result", {}).get("value", "")
        if screenshot is None:
            Path('/tmp/thyca-usage-trace-spa-last-result.json').write_text(diagnostic, encoding='utf-8')
            Path('/tmp/thyca-usage-trace-spa-last-dom.html').write_text(dom, encoding='utf-8')
        else:
            screenshot.with_suffix('.json').write_text(diagnostic, encoding='utf-8')
            screenshot.with_suffix('.html').write_text(dom, encoding='utf-8')
        raise TimeoutError(f"SPA browser did not finish: {url}; diagnostic={diagnostic}")
    finally:
        if sock: sock.close()
        process.terminate()
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)


@pytest.mark.skipif(not shutil.which("google-chrome"), reason="Google Chrome is required for the real-browser SPA lifecycle test")
def test_root_spa_retains_all_workspaces_and_restores_history(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    result_script = r"""
(async () => {
  const wait = async (predicate, label = predicate.toString()) => {
    window.__spaTestStage = label;
    const end = Date.now() + 12000;
    let lastSnapshot = false;
    while (Date.now() < end) { lastSnapshot = predicate(); if (lastSnapshot) return; await new Promise(r => setTimeout(r, 25)); }
    const visibleViews = [...document.querySelectorAll('[data-page-workspace]')].filter(view => !view.hidden).map(view => view.dataset.pageWorkspace);
    throw new Error('timed out waiting for SPA state; stage=' + label + '; route=' + JSON.stringify(window.ThycaRouter?.currentRoute()) + '; visible=' + JSON.stringify(visibleViews) + '; lastPredicate=' + JSON.stringify(lastSnapshot) + '; router=' + typeof window.ThycaRouter + '; errors=' + JSON.stringify(window.__scriptErrors || []) + '; scripts=' + JSON.stringify([...document.scripts].map(s => s.src)));
  };
  const waitForApiState = async (predicate, label = predicate.toString()) => {
    window.__spaTestStage = label;
    const end = Date.now() + 12000;
    let lastSnapshot = null;
    while (Date.now() < end) { lastSnapshot = await fetch(window.__spaControlBase + '/state').then(r => r.json()); if (predicate(lastSnapshot)) return lastSnapshot; await new Promise(r => setTimeout(r, 25)); }
    throw new Error('timed out waiting for mock API state; stage=' + label + '; route=' + JSON.stringify(window.ThycaRouter?.currentRoute()) + '; lastSnapshot=' + JSON.stringify(lastSnapshot));
  };
  const finish = value => { value.scriptErrors = window.__scriptErrors || []; value.unhandledRejections = window.__promiseErrors || []; document.body.dataset.spaTestResult = JSON.stringify(value); };
  const activeStyles = () => [...document.querySelectorAll('link[data-page-style]')].filter(link => !link.disabled && matchMedia(link.media || 'all').matches).map(link => link.dataset.pageStyle);
  const chatStyleSnapshot = () => { const e = document.querySelector('.chat-title-head .eyebrow'); const s = getComputedStyle(e); return [s.fontSize, s.marginBottom, s.letterSpacing, getComputedStyle(document.querySelector('#run-demo')).backgroundColor]; };
  const profileGeometry = () => {
    const workspace = document.querySelector('.profile-shell [data-page-workspace="profile"]');
    const scroll = workspace.querySelector('.page-scroll');
    const edge = scroll.getBoundingClientRect().left + scroll.clientLeft;
    const right = edge + scroll.clientWidth;
    const cards = [...document.querySelectorAll('#profile-root > .paper-card')];
    const rects = cards.map(card => card.getBoundingClientRect());
    return { insets: rects.length ? [rects[0].left - edge, right - rects[0].right] : [],
      gap: rects.length > 1 ? rects[1].top - rects[0].bottom : null,
      saveSpacing: parseFloat(getComputedStyle(document.querySelector('#bot-model-card .form-footer')).marginTop),
      scroll: { rect: (() => { const r = scroll.getBoundingClientRect(); return [r.left, r.right, r.width]; })(), clientWidth: scroll.clientWidth, scrollWidth: scroll.scrollWidth, clientLeft: scroll.clientLeft },
      cardRects: rects.map(rect => [rect.left, rect.right, rect.width]),
      overflowElements: [document.documentElement, document.body, scroll, scroll.querySelector('.page-inner'), document.querySelector('#profile-root'), ...cards].filter(el => el.scrollWidth > el.clientWidth + 1).map(el => ({ tag: el.tagName, id: el.id, classes: el.className, scrollWidth: el.scrollWidth, clientWidth: el.clientWidth })),
      overflow: [document.documentElement, document.body, scroll, scroll.querySelector('.page-inner'), document.querySelector('#profile-root'), ...cards].some(el => el.scrollWidth > el.clientWidth + 1) };
  };
  try {
    await wait(() => window.ThycaRouter);
    if (window.__spaMode !== 'delayed-chat') await wait(() => ThycaRouter.currentPage() && document.querySelector('[data-page-workspace]:not([hidden])'));
    const marker = sessionStorage.getItem('thyca-spa-test-reload');
    if (window.__spaMode === 'delayed-chat') {
      await wait(() => ThycaRouter.currentPage() === 'chat');
      await waitForApiState(state => state.initialDmStarted);
      await ThycaRouter.navigate('settings');
      await fetch(window.__spaControlBase + '/release-initial-dm');
      await wait(() => ThycaRouter.currentPage() === 'settings' && !document.querySelector('[data-page-workspace="settings"]').hidden);
      await ThycaRouter.navigate('chat');
      await wait(() => ThycaRouter.currentPage() === 'chat' && !document.querySelector('[data-page-workspace="chat"]').hidden && currentSession === 'dm-thyca');
      if (selectedBot !== 'thyca' || document.querySelector('#send-message').disabled) throw new Error('Chat did not recover its initial conversation after delayed navigation');
      finish({ delayedDmRecovered: true, session: currentSession, bot: selectedBot, dmHits: (await waitForApiState(state => state.initialDmHits >= 2)).initialDmHits, route: ThycaRouter.currentRoute() });
      return;
    }
    if (window.__spaMode === 'legacy-check') {
      const page = ThycaRouter.currentPage();
      await wait(() => { const view = document.querySelector(`[data-page-workspace="${page}"]`); return !!view && !view.hidden; });
      finish({ legacy: true, route: ThycaRouter.currentRoute(), state: history.state?.thycaSpa, href: location.href, frames: document.querySelectorAll('iframe').length });
      return;
    }
    if (window.__spaMode === 'fresh-detail') {
      await wait(() => ThycaRouter.currentPage() === 'chat');
      await fetch(window.__spaControlBase + '/reset-api-counts');
      history.pushState({ external: 'previous entry' }, '', '/');
      await ThycaRouter.navigate('trace', { search: '?s=synthetic-session&t=0' });
      const view = document.querySelector('[data-page-workspace="dashboard"]');
      await wait(() => ThycaRouter.currentPage() === 'dashboard' && ThycaRouter.currentRoute().hash === '#synthetic-session/0' && !view.querySelector('#turn-detail-view').hidden, 'legacy query opens Dashboard detail');
      if (history.state?.thycaReturn || history.state?.thycaSpa?.page !== 'dashboard') throw new Error('fresh detail acquired an unvalidated return origin');
      const state = await fetch(window.__spaControlBase + '/state').then(response => response.json());
      if ((state.apiCounts['/api/traces/synthetic-session/0'] || 0) !== 1 || state.apiCounts['/api/traces'] || state.apiCounts['/api/traces/stats']) throw new Error('direct detail triggered overview API reads: ' + JSON.stringify(state.apiCounts));
      const detailCalls = state.apiCounts;
      view.querySelector('#detail-back').click();
      await wait(() => ThycaRouter.currentRoute().hash === '' && !view.querySelector('#usage-overview').hidden, 'fresh all-time detail returns to overview');
      await waitForApiState(snapshot => (snapshot.apiCounts['/api/traces/stats'] || 0) === 1 && (snapshot.apiCounts['/api/traces'] || 0) === 1, 'fresh all-time detail back loads overview');
      const afterFreshBack = await fetch(window.__spaControlBase + '/state').then(response => response.json());
      const freshDefaultRoute = ThycaRouter.currentRoute();
      if ((detailCalls['/api/traces/stats'] || 0) || (detailCalls['/api/traces'] || 0) || afterFreshBack.apiCounts['/api/traces/stats'] !== 1 || afterFreshBack.apiCounts['/api/traces'] !== 1) throw new Error('fresh all-time detail did not make exactly one overview request pair: ' + JSON.stringify(afterFreshBack.apiCounts));
      if (!view.querySelector('#trace-sessions button[data-session="synthetic-session"][data-turn="0"]') || !document.querySelector('#stat-grid .stat-value')?.textContent.trim()) throw new Error('fresh all-time back did not render loaded overview data');
      history.back();
      await wait(() => ThycaRouter.currentPage() === 'chat' && !document.querySelector('#chat-workspace').hidden, 'back after fresh detail reaches external previous entry');
      const externalReturnRoute = ThycaRouter.currentRoute();
      await ThycaRouter.navigate('dashboard', { search: '', hash: '' });
      const overview = document.querySelector('[data-page-workspace="dashboard"]');
      await wait(() => !!overview.querySelector('#trace-sessions button[data-session="synthetic-session"][data-turn="0"]'));
      overview.querySelector('[data-usage-view="trace"]').click();
      await wait(() => new URLSearchParams(ThycaRouter.currentRoute().search).get('view') === 'trace');
      const overviewRoute = ThycaRouter.currentRoute();
      const row = overview.querySelector('#trace-sessions button[data-session="synthetic-session"][data-turn="0"]');
      row.click();
      await wait(() => ThycaRouter.currentRoute().hash === '#synthetic-session/0' && !view.querySelector('#turn-detail-view').hidden, 'local all-time detail opened');
      await ThycaRouter.navigate('trace', { search: overviewRoute.search, hash: '#second-session/1' });
      await wait(() => ThycaRouter.currentRoute().hash === '#second-session/1' && !view.querySelector('#turn-detail-view').hidden, 'detail-to-detail navigation');
      if (history.state?.thycaReturn) throw new Error('detail-to-detail push inherited an overview return marker');
      const beforeFallbackBack = history.length;
      view.querySelector('#detail-back').click();
      await wait(() => ThycaRouter.currentPage() === 'dashboard' && ThycaRouter.currentRoute().hash === '' && !view.querySelector('#usage-overview').hidden, 'detail-to-detail explicit back falls back to overview');
      if (history.length !== beforeFallbackBack || ThycaRouter.currentPage() !== 'dashboard') throw new Error('detail-to-detail fallback created history or exited the workspace');
      finish({ freshDetail: true, freshDefaultRoute, route: ThycaRouter.currentRoute(), externalReturnRoute,
        hashCleared: true, externalEntryPreserved: externalReturnRoute.page === 'chat' && externalReturnRoute.search === '',
        overviewRequests: afterFreshBack.apiCounts, detailToDetailFallback: true, scriptErrors: window.__scriptErrors || [],
        unhandledRejections: window.__promiseErrors || [], frames: document.querySelectorAll('iframe').length });
      return;
    }
    if (window.__spaMode === 'invalid-dashboard-dates') {
      await wait(() => ThycaRouter.setupState() === 'ready' && ThycaRouter.currentPage() === 'chat');
      await ThycaRouter.navigate('trace', { search: '?range=all&bot=thyca&from=2026-02-30&to=2026-03-01&junk=ignored' });
      const invalidDay = ThycaRouter.currentRoute();
      await ThycaRouter.navigate('trace', { search: '?range=all&bot=thyca&from=2026-09-30&to=2026-09-01&junk=ignored' });
      const inverted = ThycaRouter.currentRoute();
      await ThycaRouter.navigate('trace', { search: '?range=all&from=2024-02-29&to=2024-03-01' });
      const leapDay = ThycaRouter.currentRoute();
      await ThycaRouter.navigate('dashboard', { search: '?view=unknown&junk=ignored' });
      const invalidView = ThycaRouter.currentRoute();
      await ThycaRouter.navigate('dashboard', { search: '?view=cost' });
      const validView = ThycaRouter.currentRoute();
      if (new URLSearchParams(invalidDay.search).has('from') || new URLSearchParams(invalidDay.search).has('junk') || new URLSearchParams(invalidDay.search).get('to') !== '2026-03-01') throw new Error('calendar-invalid date or unknown metadata was not sanitized: ' + JSON.stringify(invalidDay));
      if (inverted.search !== '?bot=thyca&view=trace') throw new Error('inverted date range did not fall back to the default range: ' + JSON.stringify(inverted));
      if (leapDay.search !== '?from=2024-02-29&to=2024-03-01&view=trace') throw new Error('valid leap-day range was rejected: ' + JSON.stringify(leapDay));
      finish({ invalidDay, inverted, leapDay, invalidView, validView, scriptErrors: window.__scriptErrors || [], unhandledRejections: window.__promiseErrors || [] });
      return;
    }
    if (window.__spaMode === 'fresh-default-detail') {
      await wait(() => ThycaRouter.setupState() === 'ready' && ThycaRouter.currentPage() === 'chat');
      await fetch(window.__spaControlBase + '/reset-api-counts');
      await ThycaRouter.navigate('dashboard', { hash: '#synthetic-session/0' });
      const view = document.querySelector('[data-page-workspace="dashboard"]');
      await wait(() => ThycaRouter.currentRoute().hash === '#synthetic-session/0' && !view.querySelector('#turn-detail-view').hidden, 'default-range detail opened');
      const initial = await fetch(window.__spaControlBase + '/state').then(response => response.json());
      if (initial.apiCounts['/api/traces'] || initial.apiCounts['/api/traces/stats']) throw new Error('default-range direct detail fetched overview data first');
      view.querySelector('#detail-back').click();
      await waitForApiState(snapshot => (snapshot.apiCounts['/api/traces/stats'] || 0) === 1 && (snapshot.apiCounts['/api/traces'] || 0) === 1, 'default-range detail back loads overview');
      const state = await fetch(window.__spaControlBase + '/state').then(response => response.json());
      const listUrl = state.apiUrls.find(url => url.startsWith('/api/traces?')) || '';
      if (!listUrl.includes('from=') || !listUrl.includes('to=') || listUrl.includes('range=all')) throw new Error('default-range return did not use the seven-day range: ' + JSON.stringify(state.apiUrls));
      await wait(() => !view.querySelector('#usage-overview').hidden && !!view.querySelector('#trace-sessions button[data-session="synthetic-session"]'));
      finish({ freshDefaultDetail: true, route: ThycaRouter.currentRoute(), apiCounts: state.apiCounts,
        apiUrls: state.apiUrls, scriptErrors: window.__scriptErrors || [], unhandledRejections: window.__promiseErrors || [] });
      return;
    }
    if (window.__spaMode === 'usage-overview-screenshot' || window.__spaMode === 'usage-detail-screenshot') {
      await wait(() => ThycaRouter.setupState() === 'ready' && ThycaRouter.currentPage() === 'chat');
      await ThycaRouter.navigate('dashboard');
      const view = document.querySelector('[data-page-workspace="dashboard"]');
      await wait(() => !!view.querySelector('#trace-sessions button[data-session="synthetic-session"][data-turn="0"]') && !!view.querySelector('#stat-grid .stat-value')?.textContent.trim());
      const overview = window.__spaMode === 'usage-overview-screenshot';
      if (!overview) {
        view.querySelector('[data-usage-view="trace"]').click();
        await wait(() => new URLSearchParams(ThycaRouter.currentRoute().search).get('view') === 'trace');
        view.querySelector('#trace-sessions button[data-session="synthetic-session"][data-turn="0"]').click();
        await wait(() => ThycaRouter.currentRoute().hash === '#synthetic-session/0' && !view.querySelector('#turn-detail-view').hidden);
      }
      const nav = document.querySelector('.page-links');
      const activeLink = nav.querySelector('[aria-current="page"]');
      const navRect = nav.getBoundingClientRect(), linkRect = activeLink.getBoundingClientRect();
      const scroller = view.querySelector('#usage-scroll'), inner = scroller.querySelector('.page-inner');
      const content = [document.documentElement, document.body, scroller, inner,
        view.querySelector(overview ? '#usage-overview' : '#turn-detail-view'),
        ...view.querySelectorAll(overview ? '.tl-session' : '#detail-body, .tl-detail-head, .tl-timeline')];
      const overflows = content.filter(element => element && element.scrollWidth > element.clientWidth + 1)
        .map(element => ({ id: element.id, className: element.className, scrollWidth: element.scrollWidth, clientWidth: element.clientWidth }));
      finish({ usageScreenshot: overview ? 'overview' : 'detail', viewport: innerWidth,
        documentWidth: document.documentElement.scrollWidth, bodyWidth: document.body.scrollWidth,
        contentWidth: inner.clientWidth, contentScrollWidth: inner.scrollWidth, overflows,
        nav: [navRect.left, navRect.right], activeLink: [linkRect.left, linkRect.right],
        route: ThycaRouter.currentRoute(), returnTo: history.state?.thycaReturn,
        overviewVisible: !view.querySelector('#usage-overview').hidden, headerControlsHidden: view.querySelector('#usage-header-controls').hidden,
        detailVisible: !view.querySelector('#turn-detail-view').hidden,
        statValues: [...view.querySelectorAll('#stat-grid .stat-value')].map(el => el.textContent.trim()),
        turnText: view.querySelector('#trace-sessions').textContent, detailText: view.querySelector('#detail-body').textContent,
        scriptErrors: window.__scriptErrors || [], unhandledRejections: window.__promiseErrors || [], frames: document.querySelectorAll('iframe').length });
      return;
    }
    if (window.__spaMode === 'profile-screenshot') {
      const menu = document.querySelector('.menu-button'); menu.click();
      await wait(() => document.querySelector('#chat-sidebar').classList.contains('mobile-open'));
      const mobileViews = [];
      const mainGrid = document.querySelector('.main-grid');
      for (const page of ['chat', 'memories', 'profile', 'provider', 'dashboard', 'settings']) {
        await ThycaRouter.navigate(page, page === 'profile' ? { search: '?bot=coder' } : {});
        await wait(() => ThycaRouter.currentPage() === page && !!document.querySelector(`[data-page-workspace="${page}"]:not([hidden])`));
        const view = document.querySelector(`[data-page-workspace="${page}"]`);
        const viewRect = view.getBoundingClientRect(), gridRect = mainGrid.getBoundingClientRect();
        const gridStyle = getComputedStyle(mainGrid);
        const gridLeft = gridRect.left + mainGrid.clientLeft + parseFloat(gridStyle.paddingLeft);
        const gridRight = gridRect.left + mainGrid.clientLeft + mainGrid.clientWidth - parseFloat(gridStyle.paddingRight);
        const layout = { page, left: viewRect.left, right: viewRect.right, width: viewRect.width,
          gridLeft, gridRight, gridWidth: gridRight - gridLeft };
        mobileViews.push(layout);
        if (Math.abs(viewRect.left - gridLeft) > 1 || Math.abs(viewRect.right - gridRight) > 1 || Math.abs(viewRect.width - (gridRight - gridLeft)) > 1) throw new Error('mobile workspace does not fill the main-grid content edges: ' + JSON.stringify(layout));
      }
      await ThycaRouter.navigate('profile', { search: '?bot=coder' });
      await wait(() => ThycaRouter.currentPage() === 'profile' && !document.querySelector('[data-page-workspace="profile"]').hidden);
      const geometry = profileGeometry();
      finish({ screenshot: true, href: location.href, route: ThycaRouter.currentRoute(), mobileViews,
        viewport: innerWidth, documentWidth: document.documentElement.scrollWidth,
        bodyWidth: document.body.scrollWidth, frames: document.querySelectorAll('iframe').length,
        ...geometry, mobileMenuClosed: !document.querySelector('#chat-sidebar').classList.contains('mobile-open') && document.querySelector('#mobile-overlay').hidden });
      return;
    }
    if (marker) {
      const expected = JSON.parse(marker);
      await wait(() => { const view = document.querySelector(`[data-page-workspace="${expected.page}"]`); return ThycaRouter.currentPage() === expected.page && !!view && !view.hidden; });
      const route = ThycaRouter.currentRoute();
      const views = [...document.querySelectorAll('[data-page-workspace]')];
      const state = history.state && history.state.thycaSpa;
      const audit = JSON.parse(sessionStorage.getItem('thyca-spa-audit') || '{}');
      finish({ reloaded: true, route, state, href: location.href, views: views.length,
        visible: views.filter(view => !view.hidden).map(view => view.dataset.pageWorkspace),
        frames: document.querySelectorAll('iframe').length,
        navigations: performance.getEntriesByType('navigation').length, audit });
      sessionStorage.removeItem('thyca-spa-test-reload'); sessionStorage.removeItem('thyca-spa-audit');
      return;
    }
    await wait(() => document.querySelector('#chat-title').textContent === 'Thyca');
    const routerClickWasPrevented = href => {
      const link = document.createElement('a'); link.href = href; link.textContent = 'router click test'; document.body.append(link);
      let prevented = null;
      const observe = event => { if (event.target === link) { prevented = event.defaultPrevented; event.preventDefault(); } };
      document.addEventListener('click', observe);
      link.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, button: 0, view: window }));
      link.remove(); return prevented;
    };
    if (!routerClickWasPrevented('/ui/chat/index.html')) throw new Error('legacy entry click was not intercepted');
    if (routerClickWasPrevented('/ui/chat/js/chat.js') || routerClickWasPrevented('/ui/chat/not-a-page') || routerClickWasPrevented('/ui/not-a-page')) throw new Error('asset or unknown path click was intercepted');
    const initialChatStyles = activeStyles();
    const initialChatSnapshot = chatStyleSnapshot();
    const markdownStyle = document.querySelector('link[href*="markdown.css"]');
    if (!markdownStyle || markdownStyle.disabled || !markdownStyle.sheet) throw new Error('shared markdown CSS was not active on Chat');
    await wait(() => [...document.querySelector('#composer-model').options].some(option => option.value === 'model-b'));
    const composerModel = document.querySelector('#composer-model'); composerModel.value = 'model-b'; composerModel.dispatchEvent(new Event('change'));
    document.querySelector('#thinking-effort').value = 'high';
    const composer = document.querySelector('#message'); composer.value = 'keep this streamed turn';
    document.querySelector('#composer').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    await wait(() => document.querySelector('.running-response .stream-text')?.textContent.includes('FAKE STREAM TOKEN'));
    const intervalCount = window.__intervalCalls;
    const runningNode = document.querySelector('.running-response');
    document.querySelector('#message').value = 'unsent draft retained';
    for (const page of ['memories', 'profile', 'provider', 'dashboard', 'settings', 'chat']) {
      if (page === 'dashboard') await fetch('/api/test/fail-dashboard-css');
      if (page === 'provider') {
        await fetch('/api/test/delay-provider-css');
        const delayed = ThycaRouter.navigate('provider');
        await waitForApiState(state => state.providerCssStarted);
        await ThycaRouter.navigate('settings');
        await fetch('/api/test/release-provider-css');
        await delayed;
        if (ThycaRouter.currentPage() !== 'settings' || !document.querySelector('[data-page-workspace="settings"]:not([hidden])') || !activeStyles().includes('app-shell') || activeStyles().includes('provider')) throw new Error('stale stylesheet navigation activated Provider');
        await ThycaRouter.navigate('provider');
      } else {
        await ThycaRouter.navigate(page, page === 'profile' ? { search: '?bot=ghost' } : {});
      }
      if (ThycaRouter.currentPage() !== page) throw new Error(`failed to activate ${page}`);
      if (location.pathname !== '/' || location.search || location.hash) throw new Error(`visible URL changed on ${page}: ${location.href}`);
      const active = [...document.querySelectorAll('[data-page-workspace]:not([hidden])')];
      if (active.length !== 1 || active[0].dataset.pageWorkspace !== page) throw new Error(`bad active workspace for ${page}: ${JSON.stringify(active.map(v => [v.dataset.pageWorkspace, v.hidden]))}`);
      if (page === 'dashboard' && document.querySelector('[data-page-workspace="dashboard"] .spa-error')) {
        document.querySelector('[data-page-workspace="dashboard"] .spa-error button').click();
        await wait(() => ThycaRouter.currentPage() === 'dashboard' && !document.querySelector('[data-page-workspace="dashboard"] .spa-error') && !document.querySelector('[data-page-workspace="dashboard"]').hidden && !!document.querySelector('[data-page-workspace="dashboard"] #trace-sessions'), 'Dashboard stylesheet retry mounted UI');
      }
      if (page === 'dashboard') {
        const view = document.querySelector('[data-page-workspace="dashboard"]');
        const overviewScope = document.querySelector('#chat-sidebar #usage-overview-scope');
        if (!overviewScope || overviewScope.hidden || document.querySelectorAll('#chat-sidebar #usage-overview-scope').length !== 1) throw new Error('Dashboard Overview scope button is missing, hidden, or duplicated');
        await wait(() => !!view.querySelector('#trace-sessions button[data-session="synthetic-session"][data-turn="0"]'), 'Usage trace list loaded');
        const beforeTabs = await fetch(window.__spaControlBase + '/state').then(response => response.json());
        for (const tab of ['cost', 'usage', 'trace']) {
          view.querySelector(`[data-usage-view="${tab}"]`).click();
          await wait(() => new URLSearchParams(ThycaRouter.currentRoute().search).get('view') === tab && !view.querySelector(`#usage-panel-${tab}`).hidden, `Usage ${tab} tab`);
        }
        const afterTabs = await fetch(window.__spaControlBase + '/state').then(response => response.json());
        if (JSON.stringify(beforeTabs.apiCounts) !== JSON.stringify(afterTabs.apiCounts)) throw new Error('content tabs refetched overview or trace data');
        view.querySelector('#trace-sessions button[data-session="synthetic-session"][data-turn="0"]').click();
        await wait(() => ThycaRouter.currentRoute().hash === '#synthetic-session/0' && !view.querySelector('#turn-detail-view').hidden, 'Usage detail opened');
        if (history.state?.thycaReturn?.page !== 'dashboard' || history.state.thycaReturn.hash !== '' || history.state.thycaSpa.hash !== '#synthetic-session/0') throw new Error('local detail return metadata is invalid: ' + JSON.stringify(history.state));
        if (JSON.stringify(history.state).includes('Synthetic detail body')) throw new Error('trace content leaked into history state');
        history.back();
        await wait(() => ThycaRouter.currentRoute().hash === '' && !view.querySelector('#usage-overview').hidden, 'native Back returns to Usage');
        history.forward();
        await wait(() => ThycaRouter.currentRoute().hash === '#synthetic-session/0' && !view.querySelector('#turn-detail-view').hidden, 'native Forward restores detail');
        view.querySelector('#detail-back').click();
        await wait(() => ThycaRouter.currentRoute().hash === '' && !view.querySelector('#usage-overview').hidden, 'explicit back returns to same Usage entry');
        const dashboardRoute = ThycaRouter.currentRoute();
        await ThycaRouter.navigate('chat');
        await wait(() => ThycaRouter.currentPage() === 'chat' && !document.querySelector('#chat-workspace').hidden);
        if (!overviewScope.hidden) throw new Error('Dashboard Overview scope remained visible after leaving the page');
        const chatRoute = ThycaRouter.currentRoute(), draft = document.querySelector('#message').value;
        const liveNode = document.querySelector('.running-response');
        await ThycaRouter.navigate('dashboard', dashboardRoute);
        await wait(() => ThycaRouter.currentPage() === 'dashboard' && !view.querySelector('#usage-overview').hidden && !overviewScope.hidden);
        const q = view.querySelector('#filter-q');
        q.value = 'Synthetic quick'; q.dispatchEvent(new Event('input', { bubbles: true }));
        await wait(() => new URLSearchParams(ThycaRouter.currentRoute().search).get('q') === 'Synthetic quick');
        view.querySelector('#trace-sessions button[data-session="synthetic-session"][data-turn="0"]').click();
        await wait(() => ThycaRouter.currentRoute().hash === '#synthetic-session/0' && !view.querySelector('#turn-detail-view').hidden);
        const detailSearch = ThycaRouter.currentRoute().search;
        await new Promise(resolve => setTimeout(resolve, 500));
        if (ThycaRouter.currentRoute().hash !== '#synthetic-session/0' || ThycaRouter.currentRoute().search !== detailSearch || view.querySelector('#turn-detail-view').hidden) throw new Error('query debounce stomped an opened detail route');
        document.querySelector('[data-page-link="chat"]').click();
        await wait(() => ThycaRouter.currentPage() === 'chat' && !document.querySelector('#chat-workspace').hidden);
        await new Promise(resolve => setTimeout(resolve, 500));
        if (JSON.stringify(ThycaRouter.currentRoute()) !== JSON.stringify(chatRoute) || document.querySelector('#message').value !== draft || document.querySelector('.running-response') !== liveNode) throw new Error('delayed Usage search altered Chat route, draft, or stream');
        await ThycaRouter.navigate('dashboard');
        await wait(() => ThycaRouter.currentPage() === 'dashboard' && ThycaRouter.currentRoute().hash === '#synthetic-session/0' && !view.querySelector('#turn-detail-view').hidden);
        view.querySelector('#detail-back').click();
        await wait(() => !view.querySelector('#usage-overview').hidden && ThycaRouter.currentRoute().hash === '');
        q.value = 'cancelled quick'; q.dispatchEvent(new Event('input', { bubbles: true }));
        await wait(() => new URLSearchParams(ThycaRouter.currentRoute().search).get('q') === 'cancelled quick');
        document.querySelector('[data-page-link="chat"]').click();
        await wait(() => ThycaRouter.currentPage() === 'chat' && !document.querySelector('#chat-workspace').hidden);
        const fastChatRoute = ThycaRouter.currentRoute();
        await new Promise(resolve => setTimeout(resolve, 500));
        if (JSON.stringify(ThycaRouter.currentRoute()) !== JSON.stringify(fastChatRoute) || ThycaRouter.currentRoute().page !== 'chat' || document.querySelector('#message').value !== draft || document.querySelector('.running-response') !== liveNode) throw new Error('cancelled Usage search changed Chat after navigation');
        await ThycaRouter.navigate('dashboard');
        await wait(() => ThycaRouter.currentPage() === 'dashboard' && ThycaRouter.currentRoute().hash === '' && new URLSearchParams(ThycaRouter.currentRoute().search).get('q') === 'cancelled quick' && !view.querySelector('#usage-overview').hidden);
      }
      const styles = activeStyles();
      if (page === 'chat' ? (!styles.includes('chat') || styles.includes('app-shell')) : (!styles.includes('app-shell') || !styles.includes(page) || styles.includes('chat'))) throw new Error(`wrong active CSS for ${page}: ${styles}`);
      const workspace = active[0];
      if (workspace.querySelectorAll('.ornament').length !== 1 || workspace.querySelectorAll('.staff').length !== 1) throw new Error(`paper ornament missing or duplicated for ${page}`);
      if (page === 'profile') {
        if (document.querySelectorAll('#team-bot-list .bot-card[data-selected="true"]').length) throw new Error('unknown Profile bot kept a Chat selection');
        document.querySelector('#profile-shared').click();
        await wait(() => ThycaRouter.currentPage() === 'profile' && ThycaRouter.currentRoute().search === '');
        if (document.querySelectorAll('#team-bot-list .bot-card[data-selected="true"]').length) throw new Error('shared Profile scope kept a selected bot');
        document.querySelector('#team-bot-list [data-action="select"][data-bot-id="coder"]').click();
        await wait(() => ThycaRouter.currentPage() === 'profile' && ThycaRouter.currentRoute().search === '?bot=coder');
        if (document.querySelector('[data-bot-id="coder"] .bot-select').getAttribute('aria-label') !== 'Hồ sơ của Coder') throw new Error('Profile sidebar label was lost');
        updateCard(getBot('coder'));
        if (document.querySelector('[data-bot-id="coder"] .bot-select').getAttribute('aria-label') !== 'Hồ sơ của Coder') throw new Error('roster repaint overwrote Profile sidebar label');
      }
      if (['memories', 'dashboard'].includes(page)) {
        if (document.querySelectorAll('#team-bot-list .bot-card[data-selected="true"]').length) throw new Error(`${page} inherited a Chat selection: page=${ThycaRouter.currentPage()} highlight=${JSON.stringify(ThycaRouter.highlightedBot())} selected=${JSON.stringify([...document.querySelectorAll('#team-bot-list .bot-card[data-selected="true"]')].map(card => card.dataset.botId))}`);
        document.querySelector('#team-bot-list [data-action="select"][data-bot-id="coder"]').click();
        await wait(() => new URLSearchParams(ThycaRouter.currentRoute().search).get('bot') === 'coder');
        if (page === 'dashboard') document.querySelector('#chat-sidebar #usage-overview-scope').click();
        else document.querySelector('#team-bot-list [data-action="select"][data-bot-id="coder"]').click();
        await wait(() => !new URLSearchParams(ThycaRouter.currentRoute().search).has('bot'));
        if (document.querySelectorAll('#team-bot-list .bot-card[data-selected="true"]').length) throw new Error(`${page} filter clear kept a selected bot`);
      }
      if (page === 'chat' && document.querySelectorAll('#team-bot-list .bot-card[data-selected="true"]').length !== 1) throw new Error('return to Chat did not restore Thyca selection');
    }
    if (document.querySelector('#message').value !== 'unsent draft retained') throw new Error('Chat draft was not retained');
    if (composerModel.value !== 'model-b' || document.querySelector('#thinking-effort').value !== 'high') throw new Error('Chat composer overrides were not retained');
    if (window.__intervalCalls !== intervalCount) throw new Error('screen changes duplicated an interval');
    const finalChatStyles = activeStyles(), finalChatSnapshot = chatStyleSnapshot();
    if (JSON.stringify(finalChatStyles) !== JSON.stringify(initialChatStyles) || JSON.stringify(finalChatSnapshot) !== JSON.stringify(initialChatSnapshot)) throw new Error('Chat CSS changed after visiting other pages: ' + JSON.stringify({ initialStyles: initialChatStyles, finalStyles: finalChatStyles, initialSnapshot: initialChatSnapshot, finalSnapshot: finalChatSnapshot, links: [...document.querySelectorAll('link[data-page-style]')].map(link => ({ style: link.dataset.pageStyle, media: link.media, disabled: link.disabled, hasSheet: !!link.sheet, state: link.dataset.loadState, href: link.href })) }));
    if (markdownStyle.disabled || !markdownStyle.sheet) throw new Error('shared markdown CSS was disabled during navigation');
    if (document.querySelector('.running-response') !== runningNode || !runningNode.querySelector('.stream-text')?.textContent.includes('FAKE STREAM TOKEN')) throw new Error('Chat stream/token was not retained');
    await fetch('/api/test/stream/continue');
    await wait(() => !document.querySelector('.running-response'));
    if (document.querySelectorAll('[data-page-workspace]').length !== 6) throw new Error('workspaces were not retained');
    if (document.querySelectorAll('iframe').length) throw new Error('iframe found');
    if (performance.getEntriesByType('navigation').length !== 1) throw new Error('screen navigation reloaded the document');
    await ThycaRouter.navigate('profile', { search: '?bot=coder' });
    await wait(() => ThycaRouter.currentPage() === 'profile' && !document.querySelector('[data-page-workspace="profile"]').hidden);
    const profileRoot = document.querySelector('#profile-root');
    const geometry = profileGeometry();
    if (JSON.stringify(geometry.insets.map(Math.round)) !== JSON.stringify([38, 38]) || geometry.gap !== 22 || geometry.saveSpacing !== 20 || geometry.overflow) throw new Error('desktop Profile geometry mismatch: ' + JSON.stringify(geometry));
    await wait(() => document.querySelector('#bot-model')?.value === 'model-a' && document.querySelector('#bot-thinking')?.value === 'medium');
    document.querySelector('#bot-model').value = 'model-b'; document.querySelector('#bot-model').dispatchEvent(new Event('change'));
    document.querySelector('#bot-thinking').value = 'high';
    const savedProfile = { page: 'profile', search: '?bot=coder', hash: '' };
    document.querySelector('[data-page-link="settings"]').click();
    await wait(() => ThycaRouter.currentPage() === 'settings' && !document.querySelector('[data-page-workspace="settings"]').hidden);
    if (document.activeElement !== document.querySelector('[data-page-workspace="settings"] h1')) throw new Error('navigation did not focus the new page heading');
    document.querySelector('[data-page-link="profile"]').click();
    await wait(() => ThycaRouter.currentPage() === 'profile' && !document.querySelector('[data-page-workspace="profile"]').hidden);
    if (document.querySelector('#profile-root') !== profileRoot || JSON.stringify(ThycaRouter.currentRoute()) !== JSON.stringify(savedProfile)) throw new Error('Profile destination route/workspace was not retained');
    if (document.querySelector('#bot-model').value !== 'model-b' || document.querySelector('#bot-thinking').value !== 'high') throw new Error('Profile unsaved model draft did not survive Settings navigation');
    const historyLength = history.length;
    document.querySelector('[data-page-link="profile"]').click(); await new Promise(resolve => setTimeout(resolve, 100));
    if (history.length !== historyLength) throw new Error('same-page header navigation added history');
    history.back();
    await wait(() => ThycaRouter.currentPage() === 'settings' && !document.querySelector('[data-page-workspace="settings"]').hidden);
    history.forward();
    await wait(() => ThycaRouter.currentPage() === 'profile' && ThycaRouter.currentRoute().search === '?bot=coder' && !document.querySelector('[data-page-workspace="profile"]').hidden);
    await fetch('/api/test/delay-settings');
    document.querySelector('#save-bot-settings').click();
    await wait(() => document.querySelector('#save-bot-settings').disabled, 'Profile Save disabled while settings request is pending');
    if (!document.querySelector('#save-bot-settings').disabled) {
      const pendingState = await fetch(window.__spaControlBase + '/state').then(response => response.json());
      throw new Error('Profile Save remained enabled during pending POST: ' + JSON.stringify({ disabled: document.querySelector('#save-bot-settings').disabled, hint: document.querySelector('#bot-settings-hint').textContent, settingsStarted: pendingState.settingsStarted, settingsPosts: pendingState.settingsPosts, settingsDelay: pendingState.settingsDelay }));
    }
    document.querySelector('#bot-settings-form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    await new Promise(resolve => setTimeout(resolve, 100));
    if ((await waitForApiState(state => state.settingsPosts >= 1)).settingsPosts !== 1) throw new Error('duplicate Profile settings POST');
    document.querySelector('[data-page-link="settings"]').click();
    await wait(() => ThycaRouter.currentPage() === 'settings' && !document.querySelector('[data-page-workspace="settings"]').hidden);
    document.querySelector('[data-page-link="profile"]').click();
    await wait(() => ThycaRouter.currentPage() === 'profile' && !document.querySelector('[data-page-workspace="profile"]').hidden);
    if (document.querySelector('#bot-model').value !== 'model-b' || document.querySelector('#bot-thinking').value !== 'high') throw new Error('pending settings draft was lost across navigation');
    await fetch(window.__spaControlBase + '/release-settings');
    await wait(() => document.querySelector('#bot-settings-hint').textContent.includes('Đã lưu.'));
    if ((await waitForApiState(state => state.settingsPosts >= 1)).settingsPosts !== 1) throw new Error('duplicate Profile settings POST');
    if (!document.querySelector('#profile-root .chip')?.textContent.includes('model: model-b · suy nghĩ high')) throw new Error('saved Profile model chip did not update');
    const coderSaved = { model: document.querySelector('#bot-model').value, thinking: document.querySelector('#bot-thinking').value };
    document.querySelector('#team-bot-list [data-action="select"][data-bot-id="thyca"]').click();
    await wait(() => ThycaRouter.currentRoute().search === '?bot=thyca' && document.querySelector('#bot-model')?.value === 'model-a' && document.querySelector('#bot-thinking')?.value === 'low');
    if (coderSaved.model !== 'model-b' || coderSaved.thinking !== 'high') throw new Error('Coder settings were not kept distinct from Thyca');
    document.querySelector('#bot-model').value = 'model-b'; document.querySelector('#bot-model').dispatchEvent(new Event('change'));
    document.querySelector('#bot-thinking').value = 'max';
    await ThycaRouter.navigate('settings'); await ThycaRouter.navigate('profile');
    if (ThycaRouter.currentRoute().search !== '?bot=thyca' || document.querySelector('#bot-model').value !== 'model-b' || document.querySelector('#bot-thinking').value !== 'max') throw new Error('Thyca draft did not retain its own route/form state');
    document.querySelector('#profile-shared').click();
    await wait(() => ThycaRouter.currentRoute().search === '' && document.querySelector('[data-edit="USER.md"]'));
    if (document.querySelectorAll('#team-bot-list .bot-card[data-selected="true"]').length) throw new Error('shared Profile failed to clear bot selection');
    const editButton = document.querySelector('[data-edit="USER.md"]');
    editButton.click();
    const editDialog = document.querySelector('#edit-dialog');
    await wait(() => editDialog.open && document.activeElement === document.querySelector('#edit-content'));
    document.querySelector('[data-page-link="settings"]').click();
    await wait(() => ThycaRouter.currentPage() === 'settings');
    if (editDialog.open) throw new Error('page navigation left a Profile dialog open');
    document.querySelector('[data-page-link="profile"]').click();
    await wait(() => ThycaRouter.currentPage() === 'profile' && !document.querySelector('[data-page-workspace="profile"]').hidden);
    document.querySelector('[data-edit="USER.md"]').click();
    await wait(() => editDialog.open);
    editDialog.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: 0, clientY: 0 }));
    await wait(() => !editDialog.open && document.activeElement === document.querySelector('[data-edit="USER.md"]'));
    document.querySelector('[data-edit="USER.md"]').click();
    await wait(() => editDialog.open);
    editDialog.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    await wait(() => !editDialog.open);
    document.querySelector('[data-edit="USER.md"]').click();
    await wait(() => editDialog.open);
    const canonicalBeforeConfirm = (await fetch(window.__spaControlBase + '/state').then(response => response.json())).canonicalPosts;
    window.confirm = () => false;
    document.querySelector('#edit-content').value = '';
    document.querySelector('#edit-form').requestSubmit();
    await new Promise(resolve => setTimeout(resolve, 100));
    if (!editDialog.open || (await fetch(window.__spaControlBase + '/state').then(response => response.json())).canonicalPosts !== canonicalBeforeConfirm) throw new Error('blank canonical confirmation did not guard the save');
    window.confirm = () => true;
    document.querySelector('#edit-content').value = 'Edited from SPA test';
    await fetch('/api/test/fail-canonical-once');
    document.querySelector('#edit-form').requestSubmit();
    await wait(() => document.querySelector('#edit-status').textContent.includes('injected canonical failure'));
    if (!editDialog.open) throw new Error('canonical save error closed the editing dialog');
    document.querySelector('#edit-form').requestSubmit();
    await wait(() => !editDialog.open && document.querySelector('#profile-status').textContent.includes('Đã lưu USER.md'));
    await wait(() => document.getElementById('file-USER.md')?.textContent.includes('Edited from SPA test'), 'canonical USER.md content refreshed');
    if (document.activeElement !== document.querySelector('[data-edit="USER.md"]')) throw new Error('canonical dialog did not restore focus');
    await ThycaRouter.navigate('profile', { search: '?bot=coder' });
    await wait(() => ThycaRouter.currentRoute().search === '?bot=coder' && document.querySelector('#bot-model')?.value === 'model-b');
    const stableApiBefore = (await fetch(window.__spaControlBase + '/state').then(response => response.json())).apiCounts;
    for (let cycle = 0; cycle < 2; cycle++) {
      for (const page of ['memories', 'profile', 'provider', 'dashboard', 'settings', 'chat']) await ThycaRouter.navigate(page);
      await ThycaRouter.navigate('profile');
    }
    const stableApiAfter = (await fetch(window.__spaControlBase + '/state').then(response => response.json())).apiCounts;
    if (JSON.stringify(stableApiAfter) !== JSON.stringify(stableApiBefore)) throw new Error('repeated workspace visits duplicated API reads/writes');
    sessionStorage.setItem('thyca-spa-audit', JSON.stringify({ initialChatStyles, initialChatSnapshot, finalChatStyles,
      finalChatSnapshot, desktopProfileGeometry: geometry, workspaces: document.querySelectorAll('[data-page-workspace]').length,
      intervalsStable: window.__intervalCalls === intervalCount, scriptErrors: window.__scriptErrors, unhandledRejections: window.__promiseErrors }));
    sessionStorage.setItem('thyca-spa-test-reload', JSON.stringify(savedProfile));
    location.reload();
  } catch (error) { finish({ error: String(error), stack: error.stack }); }
})();
"""

    class Handler(BaseHTTPRequestHandler):
        api_requests: list[str] = []
        api_urls: list[str] = []
        static_requests: list[tuple[str, int]] = []
        stream_release = threading.Event()
        provider_css_started = threading.Event()
        provider_css_release = threading.Event()
        provider_css_hold = threading.Event()
        fail_dashboard_css = False
        fail_canonical_once = False
        settings_started = threading.Event()
        settings_release = threading.Event()
        settings_delay = False
        initial_dm_delay = False
        initial_dm_started = threading.Event()
        initial_dm_release = threading.Event()
        initial_dm_hits = 0
        settings_posts: list[tuple[str, dict]] = []
        canonical_posts: list[dict] = []
        settings = {
            "thyca": {"bot_id": "thyca", "model": {"id": "model-a", "thinking": "low"}, "effective": {"model": "model-a", "provider": "mock", "thinking": "low"}},
            "coder": {"bot_id": "coder", "model": {"id": "model-a", "thinking": "medium"}, "effective": {"model": "model-a", "provider": "mock", "thinking": "medium"}},
        }
        canonical = {"USER.md": "Original user profile", "SOUL.md": "Original voice", "IDENTITY.md": "Original identity"}
        turn_posts = 0
        turn_cancels = 0

        def log_message(self, *_args) -> None:
            pass

        def _json(self, payload: dict, status: int = 200) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path == "/api/test/stream/continue":
                self.stream_release.set()
                self.send_response(204); self.end_headers(); return
            if path == "/api/test/state":
                counts = {}
                for request_path in self.api_requests: counts[request_path] = counts.get(request_path, 0) + 1
                self._json({"providerCssStarted": self.provider_css_started.is_set(), "settingsStarted": self.settings_started.is_set(), "settingsReleased": self.settings_release.is_set(), "initialDmStarted": self.initial_dm_started.is_set(), "initialDmHits": self.initial_dm_hits, "settingsPosts": len(self.settings_posts), "canonicalPosts": len(self.canonical_posts), "apiCounts": counts, "apiUrls": list(self.api_urls)})
                return
            if path == "/api/test/delay-provider-css":
                self.provider_css_hold.set(); self.provider_css_started.clear(); self.provider_css_release.clear()
                self._json({"ok": True}); return
            if path == "/api/test/release-provider-css":
                self.provider_css_release.set(); self.provider_css_hold.clear(); self._json({"ok": True}); return
            if path == "/api/test/fail-dashboard-css":
                type(self).fail_dashboard_css = True; self._json({"ok": True}); return
            if path == "/api/test/fail-canonical-once":
                type(self).fail_canonical_once = True; self._json({"ok": True}); return
            if path == "/api/test/delay-settings":
                type(self).settings_delay = True; self.settings_started.clear(); self.settings_release.clear(); self._json({"ok": True}); return
            if path == "/api/test/release-settings":
                type(self).settings_release.set(); type(self).settings_delay = False; self._json({"ok": True}); return
            if path == "/api/test/delay-initial-dm":
                type(self).initial_dm_hits = 0; type(self).initial_dm_delay = True
                self.initial_dm_started.clear(); self.initial_dm_release.clear(); self._json({"ok": True}); return
            if path == "/api/test/release-initial-dm":
                type(self).initial_dm_release.set(); type(self).initial_dm_delay = False; self._json({"ok": True}); return
            if path.startswith("/ui/provider/providercss/provider.css") and self.provider_css_hold.is_set():
                self.provider_css_started.set(); self.provider_css_release.wait()
            if path == "/ui/dashboard/dashboardcss/dashboard.css" and self.fail_dashboard_css:
                type(self).fail_dashboard_css = False; self.send_error(404, "test CSS failure"); return
            if path.startswith("/api/"):
                self.api_requests.append(path); self.api_urls.append(self.path)
                if path == "/api/bots":
                    payload = {"bots": [{"id": "thyca", "name": "Thyca", "tint": "botanical", "role": "assistant"}, {"id": "coder", "name": "Coder", "tint": "botanical", "role": "coding"}]}
                elif path == "/api/sessions": payload = {"sessions": []}
                elif path == "/api/bots/thyca/dm":
                    type(self).initial_dm_hits += 1
                    if self.initial_dm_delay and self.initial_dm_hits == 1:
                        self.initial_dm_started.set(); self.initial_dm_release.wait()
                    payload = {"id": "dm-thyca", "dm": "thyca", "title": "Thyca", "messages": []}
                elif path in ("/api/bots/thyca/settings", "/api/bots/coder/settings"):
                    bot_id = path.split("/")[3]; payload = self.settings[bot_id]
                elif path == "/api/config": payload = {"values": {"models": {"model-a": {"provider": "mock", "reasoningEfforts": ["low", "medium"]}, "model-b": {"provider": "mock", "reasoningEfforts": ["high", "max"]}}, "defaultModel": "model-a", "defaultProvider": "mock", "providers": {"mock": {}}}, "schema": {"sections": [{"fields": [{"key": "provider.reasoningEffort", "choices": ["low", "medium", "high", "max"]}]}]}, "defaults": {}}
                elif path == "/api/config/status": payload = {"ready": True}
                elif path == "/api/memory/stats": payload = {"files": [{"name": name, "content": content} for name, content in self.canonical.items()], "canonical": [], "total": len(self.canonical)}
                elif path == "/api/traces/stats": payload = {"totals": {"requests": 2, "tool_calls": 1, "prompt_tokens": 10, "cached_tokens": 2, "completion_tokens": 3, "total_tokens": 13, "cost_usd": 0.125, "latency_ms_p50": 120, "latency_ms_p90": 120}, "by_day": [{"day": "2026-09-20", "requests": 2, "cost_usd": 0.125}], "by_bot": [{"bot_id": "thyca", "requests": 2, "tool_calls": 1, "total_tokens": 13, "cost_usd": 0.125}], "by_model": [{"model": "fixture-model", "requests": 2, "total_tokens": 13, "cost_usd": 0.125, "latency_ms_p50": 120}], "by_hour": [], "models": ["fixture-model"], "by_status": [{"status": "completed", "requests": 2}]}
                elif path == "/api/traces": payload = {"traces": [{"session_id": "synthetic-session", "turn_index": 0, "title": "Synthetic Usage turn", "started_at": "2026-09-20T10:00:00Z", "status": "completed", "session_kind": "dm", "session_dm": "thyca", "bot_id": "thyca", "model": "fixture-model", "requests": 2, "tool_calls": 1, "total_tokens": 13, "cost_usd": 0.125, "latency_ms": 120}], "total": 1}
                elif path == "/api/traces/synthetic-session/0": payload = {"session_id": "synthetic-session", "turn_index": 0, "title": "Synthetic Usage turn", "bot_id": "thyca", "started_at": "2026-09-20T10:00:00Z", "ended_at": "2026-09-20T10:00:01Z", "status": "completed", "model": "fixture-model", "requests": 2, "rounds": 2, "tool_calls": 1, "prompt_tokens": 10, "cached_tokens": 2, "completion_tokens": 3, "total_tokens": 13, "cost_usd": 0.125, "latency_ms": 120, "messages": [{"role": "user", "content": "Synthetic detail body"}, {"role": "assistant", "content": "Synthetic detail reply", "meta": {"bot_id": "thyca", "model": "fixture-model"}}]}
                else: payload = {}
                self._json(payload)
                return
            target = safe_file(WEBUI, path)
            if target is None:
                self.static_requests.append((path, 404)); self.send_error(404); return
            self.static_requests.append((path, 200))
            body = target.read_bytes()
            if path == "/" or unquote(path) in {f"/ui/{page}{suffix}" for page in LEGACY_ENTRIES for suffix in ("", "/", "/index.html")}:
                head_script = (f"<script>window.__spaControlBase={json.dumps(control_base)};window.__spaMode=new URLSearchParams(location.search).get('spaMode');window.__scriptErrors=[];window.__promiseErrors=[];window.__intervalCalls=0;const __setInterval=window.setInterval.bind(window);window.setInterval=(...a)=>{{window.__intervalCalls++;return __setInterval(...a)}};window.addEventListener('error',e=>window.__scriptErrors.push(String(e.message)+' '+String(e.filename)));window.addEventListener('unhandledrejection',e=>window.__promiseErrors.push(String(e.reason)))</script></head>").encode()
                body = body.replace(b"</head>", head_script)
                body = body.replace(b"</body>", b"<script>" + result_script.encode() + b"</script></body>")
            self.send_response(200); self.send_header("Content-Type", content_type(target)); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

        def do_POST(self) -> None:
            path = urlsplit(self.path).path
            self.api_requests.append(path)
            if path == "/api/sessions/dm-thyca/turn/stream":
                type(self).turn_posts += 1
                self.send_response(200); self.send_header("Content-Type", "application/x-ndjson"); self.send_header("Transfer-Encoding", "chunked"); self.end_headers()
                def chunk(event: dict) -> None:
                    data = (json.dumps(event) + "\n").encode()
                    self.wfile.write(f"{len(data):X}\r\n".encode() + data + b"\r\n"); self.wfile.flush()
                chunk({"type": "turn.accepted", "bot_id": "thyca"})
                chunk({"type": "llm.started", "bot_id": "thyca"})
                chunk({"type": "llm.content", "bot_id": "thyca", "delta": "FAKE STREAM TOKEN"})
                self.stream_release.wait()
                chunk({"type": "llm.finished", "bot_id": "thyca"})
                chunk({"type": "turn.completed", "bot_id": "thyca", "detail": {"id": "dm-thyca", "dm": "thyca", "title": "Thyca", "messages": [{"role": "user", "content": "keep this streamed turn"}, {"role": "assistant", "content": "FAKE STREAM TOKEN", "meta": {"bot_id": "thyca"}}]}})
                self.wfile.write(b"0\r\n\r\n"); self.wfile.flush(); return
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            if path in ("/api/bots/thyca/settings", "/api/bots/coder/settings"):
                bot_id = path.split("/")[3]
                self.settings_posts.append((bot_id, payload))
                if bot_id == "coder" and self.settings_delay:
                    self.settings_started.set(); self.settings_release.wait()
                model = payload.get("model", {})
                response = {"bot_id": bot_id, "model": model, "effective": {"model": model.get("id"), "provider": "mock", "thinking": model.get("thinking")}}
                self.settings[bot_id] = response
                self._json(response); return
            if path == "/api/memory/canonical":
                self.canonical_posts.append(payload)
                if self.fail_canonical_once:
                    type(self).fail_canonical_once = False
                    self._json({"error": "injected canonical failure"}, status=500); return
                self.canonical[payload["name"]] = payload["content"]
                self._json({"ok": True}); return
            if path.endswith("/turn/cancel"):
                type(self).turn_cancels += 1
            self._json({})

    class ControlHandler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            pass

        def _json(self, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(200); self.send_header("Access-Control-Allow-Origin", "*"); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path == "/release-settings":
                Handler.settings_release.set(); Handler.settings_delay = False
                self._json({"ok": True}); return
            if path == "/release-initial-dm":
                Handler.initial_dm_release.set(); Handler.initial_dm_delay = False
                self._json({"ok": True}); return
            if path == "/state":
                counts = {}
                for request_path in Handler.api_requests: counts[request_path] = counts.get(request_path, 0) + 1
                self._json({"providerCssStarted": Handler.provider_css_started.is_set(), "settingsStarted": Handler.settings_started.is_set(), "settingsReleased": Handler.settings_release.is_set(), "initialDmStarted": Handler.initial_dm_started.is_set(), "initialDmHits": Handler.initial_dm_hits, "settingsPosts": len(Handler.settings_posts), "canonicalPosts": len(Handler.canonical_posts), "apiCounts": counts, "apiUrls": list(Handler.api_urls)}); return
            if path == "/reset-api-counts":
                Handler.api_requests.clear(); Handler.api_urls.clear(); self._json({"ok": True}); return
            self.send_error(404)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    control_server = ThreadingHTTPServer(("127.0.0.1", 0), ControlHandler)
    control_thread = threading.Thread(target=control_server.serve_forever, daemon=True); control_thread.start()
    control_base = f"http://127.0.0.1:{control_server.server_port}"
    def stop_mock_server() -> None:
        Handler.settings_release.set(); Handler.provider_css_release.set(); Handler.stream_release.set(); Handler.initial_dm_release.set()
        server.shutdown(); server.server_close(); thread.join(timeout=3)
        control_server.shutdown(); control_server.server_close(); control_thread.join(timeout=3)
    request.addfinalizer(stop_mock_server)
    browser = shutil.which("google-chrome")
    root_url = f"http://127.0.0.1:{server.server_port}/"
    result = _cdp_browser_result(browser, root_url, 1280, Path('/tmp/thyca-usage-trace-spa-desktop.png'), tmp_path / "chrome-profile")
    Path('/tmp/thyca-usage-trace-spa-last-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    if Path('/tmp/thyca-usage-trace-spa-desktop.html').exists():
        Path('/tmp/thyca-usage-trace-spa-last-dom.html').write_text(Path('/tmp/thyca-usage-trace-spa-desktop.html').read_text(encoding='utf-8'), encoding='utf-8')
    assert "error" not in result, {**result, "static_requests": Handler.static_requests}
    assert result["reloaded"] is True
    assert result["route"] == {"page": "profile", "search": "?bot=coder", "hash": ""}
    assert result["state"] == result["route"]
    assert result["audit"]["initialChatStyles"] == result["audit"]["finalChatStyles"]
    assert result["audit"]["initialChatSnapshot"] == result["audit"]["finalChatSnapshot"]
    assert result["audit"]["desktopProfileGeometry"]["insets"] == [38, 38]
    assert result["audit"]["desktopProfileGeometry"]["gap"] == 22
    assert result["audit"]["desktopProfileGeometry"]["saveSpacing"] == 20
    assert result["audit"]["desktopProfileGeometry"]["overflow"] is False
    assert result["audit"]["workspaces"] == 6 and result["audit"]["intervalsStable"] is True
    assert result["audit"]["scriptErrors"] == [] and result["audit"]["unhandledRejections"] == []
    assert result["href"].endswith("/")
    assert result["views"] == 2 and result["visible"] == ["profile"]
    assert result["frames"] == 0 and result["navigations"] == 1
    assert Handler.turn_posts == 1 and Handler.turn_cancels == 0
    assert [bot for bot, _ in Handler.settings_posts] == ["coder"]
    assert Handler.settings_posts[0][1] == {"model": {"id": "model-b", "thinking": "high"}}
    assert [post["name"] for post in Handler.canonical_posts] == ["USER.md", "USER.md"]
    assert Handler.canonical["USER.md"] == "Edited from SPA test"
    assert not any("provider" in path and "api-key" in path for path in Handler.api_requests)
    browser_results = {"desktop": result, "mobile": [], "legacy": [], "delayedChat": {}, "freshDetail": {}}
    legacy_cases = (
        ("/ui/%70rofile/index.html?spaMode=legacy-check", {"page": "profile", "search": "", "hash": ""}),
        ("/ui/provider/index.html?bot=coder&spaMode=legacy-check", {"page": "profile", "search": "?bot=coder", "hash": ""}),
        ("/ui/chat/index.html?bot=thyca&spaMode=legacy-check", {"page": "chat", "search": "?bot=thyca", "hash": ""}),
        ("/ui/dashboard/index.html?s=s9&t=2&spaMode=legacy-check#s9/2", {"page": "dashboard", "search": "?view=trace", "hash": "#s9/2"}),
        ("/ui/trace/index.html?s=s9&t=2&spaMode=legacy-check#s9/2", {"page": "dashboard", "search": "?range=all&view=trace", "hash": "#s9/2"}),
    )
    for index, (legacy, expected) in enumerate(legacy_cases):
        legacy_result = _cdp_browser_result(browser, f"http://127.0.0.1:{server.server_port}{legacy}", 1280, None, tmp_path / f'legacy-{index}')
        assert legacy_result["legacy"] is True and legacy_result["route"] == expected
        assert legacy_result["state"] == expected and legacy_result["href"].endswith("/")
        assert legacy_result["frames"] == 0
        browser_results["legacy"].append(legacy_result)
    invalid_dates = _cdp_browser_result(browser, f"http://127.0.0.1:{server.server_port}/?spaMode=invalid-dashboard-dates", 1280, None, tmp_path / 'invalid-dates')
    assert invalid_dates["invalidDay"]["search"] == "?bot=thyca&to=2026-03-01&view=trace", invalid_dates
    assert invalid_dates["inverted"]["search"] == "?bot=thyca&view=trace", invalid_dates
    assert invalid_dates["leapDay"]["search"] == "?from=2024-02-29&to=2024-03-01&view=trace", invalid_dates
    assert invalid_dates["invalidView"]["search"] == ""
    assert invalid_dates["validView"]["search"] == "?view=cost"
    assert invalid_dates["scriptErrors"] == [] and invalid_dates["unhandledRejections"] == []
    browser_results["invalidDates"] = invalid_dates
    fresh_default = _cdp_browser_result(browser, f"http://127.0.0.1:{server.server_port}/?spaMode=fresh-default-detail", 1280, None, tmp_path / 'fresh-default')
    assert fresh_default.get("freshDefaultDetail") is True, fresh_default
    assert fresh_default["route"]["page"] == "dashboard" and fresh_default["route"]["hash"] == ""
    assert fresh_default["apiCounts"].get("/api/traces/synthetic-session/0") == 1
    assert fresh_default["apiCounts"].get("/api/traces") == 1 and fresh_default["apiCounts"].get("/api/traces/stats") == 1
    assert "from=" in fresh_default["apiUrls"][-1] and "to=" in fresh_default["apiUrls"][-1]
    browser_results["freshDefaultDetail"] = fresh_default
    fresh_detail_result = _cdp_browser_result(browser, f"http://127.0.0.1:{server.server_port}/?spaMode=fresh-detail", 1280, None, tmp_path / 'fresh-detail')
    assert fresh_detail_result.get("freshDetail") is True, fresh_detail_result
    assert fresh_detail_result["freshDefaultRoute"] == {"page": "dashboard", "search": "?range=all&view=trace", "hash": ""}
    assert fresh_detail_result["route"]["page"] == "dashboard" and re.fullmatch(r"\?from=\d{4}-\d{2}-\d{2}&to=\d{4}-\d{2}-\d{2}&view=trace", fresh_detail_result["route"]["search"])
    assert fresh_detail_result["route"]["hash"] == ""
    assert fresh_detail_result["externalReturnRoute"] == {"page": "chat", "search": "", "hash": ""}
    assert fresh_detail_result["externalEntryPreserved"] is True and fresh_detail_result["detailToDetailFallback"] is True
    assert fresh_detail_result["overviewRequests"].get("/api/traces/synthetic-session/0") == 1
    assert fresh_detail_result["overviewRequests"].get("/api/traces", 0) == 1 and fresh_detail_result["overviewRequests"].get("/api/traces/stats", 0) == 1
    browser_results["freshDetail"] = fresh_detail_result
    for width in (320, 375):
        mobile_result = _cdp_browser_result(
            browser, f"http://127.0.0.1:{server.server_port}/?spaMode=profile-screenshot", width,
            Path(f"/tmp/thyca-usage-trace-spa-{width}.png"), tmp_path / f"chrome-{width}", mobile=True,
        )
        assert mobile_result.get("screenshot") is True, mobile_result
        assert mobile_result["href"].endswith("/") and mobile_result["frames"] == 0
        assert mobile_result["documentWidth"] <= mobile_result["viewport"], mobile_result
        assert mobile_result["bodyWidth"] <= mobile_result["viewport"], mobile_result
        assert mobile_result["insets"] == [14, 14], mobile_result
        assert mobile_result["gap"] == 22 and mobile_result["saveSpacing"] == 20, mobile_result
        assert mobile_result["overflow"] is False and mobile_result["mobileMenuClosed"] is True, mobile_result
        assert [item["page"] for item in mobile_result["mobileViews"]] == list(PAGES), mobile_result
        assert all(abs(item["left"] - item["gridLeft"]) <= 1 and abs(item["right"] - item["gridRight"]) <= 1 and abs(item["width"] - item["gridWidth"]) <= 1 for item in mobile_result["mobileViews"]), mobile_result
        browser_results["mobile"].append(mobile_result)
    for width in (1280, 320, 375):
        for mode in ("overview", "detail"):
            usage_result = _cdp_browser_result(
                browser, f"http://127.0.0.1:{server.server_port}/?spaMode=usage-{mode}-screenshot", width,
                Path(f"/tmp/thyca-usage-trace-{mode}-spa-{width}.png"), tmp_path / f"usage-{mode}-{width}", mobile=width < 850,
            )
            assert usage_result.get("usageScreenshot") == mode, usage_result
            assert usage_result["detailVisible"] is (mode == "detail")
            assert usage_result["overviewVisible"] is (mode == "overview")
            assert usage_result["headerControlsHidden"] is (mode == "detail")
            assert usage_result["frames"] == 0 and usage_result["scriptErrors"] == [] and usage_result["unhandledRejections"] == []
            assert usage_result["route"]["page"] == "dashboard"
            assert usage_result["route"]["hash"] == ("#synthetic-session/0" if mode == "detail" else "")
            assert usage_result["viewport"] == width and usage_result["documentWidth"] <= width and usage_result["bodyWidth"] <= width
            assert usage_result["overflows"] == [], usage_result
            assert usage_result["nav"][0] <= usage_result["activeLink"][0] and usage_result["activeLink"][1] <= usage_result["nav"][1]
            if mode == "detail":
                assert usage_result["returnTo"]["page"] == "dashboard" and usage_result["returnTo"]["hash"] == ""
            else:
                assert usage_result["statValues"] and "Synthetic Usage turn" in usage_result["turnText"]
            browser_results.setdefault("usageScreenshots", {}).setdefault(str(width), {})[mode] = usage_result
    with urlopen(f"http://127.0.0.1:{server.server_port}/api/test/delay-initial-dm", timeout=3):
        pass
    delayed_result = _cdp_browser_result(browser, f"http://127.0.0.1:{server.server_port}/?spaMode=delayed-chat", 1280, None, tmp_path / 'delayed-chat')
    assert delayed_result.get("delayedDmRecovered") is True, delayed_result
    assert delayed_result["session"] == "dm-thyca" and delayed_result["bot"] == "thyca" and delayed_result["dmHits"] >= 2
    assert delayed_result["scriptErrors"] == [] and delayed_result["unhandledRejections"] == []
    browser_results["delayedChat"] = delayed_result
    Path("/tmp/thyca-usage-trace-spa-browser.json").write_text(json.dumps(browser_results, indent=2) + "\\n", encoding="utf-8")


@pytest.mark.skipif(not shutil.which("google-chrome"), reason="Google Chrome is required for the setup-gate browser test")
def test_first_run_setup_gate_in_real_spa(tmp_path: Path) -> None:
    class Handler(BaseHTTPRequestHandler):
        gate_case = "required"
        ready = False
        config_error = False
        config = {"values": {"providers": {"legacy": {"baseUrl": "https://legacy.invalid/v1", "api": "openai_chat", "apiKeyEnv": "THYCA_SETUP_GATE_MISSING"}}, "models": {}, "defaultProvider": "legacy", "defaultModel": "legacy/unregistered"}, "schema": {"sections": [{"fields": [{"key": "provider.reasoningEffort", "choices": ["low", "high", "max"], "default": "high"}]}]}}
        status_hits = 0
        status_started = threading.Event()
        status_release = threading.Event()
        api_requests: list[str] = []
        dm_hits = 0
        verification_meta: dict = {}
        save_count = 0

        def log_message(self, *_args) -> None:
            pass

        def _json(self, payload: dict, status: int = 200) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            query = dict(__import__("urllib.parse", fromlist=["parse_qsl"]).parse_qsl(urlsplit(self.path).query))
            if path.startswith("/api/test/"):
                if path == "/api/test/state":
                    self._json({"statusHits": Handler.status_hits, "statusStarted": Handler.status_started.is_set(), "dmHits": Handler.dm_hits, "verification": Handler.verification_meta, "saveCount": Handler.save_count, "apiRequests": Handler.api_requests}); return
                if path == "/api/test/release": Handler.status_release.set(); self._json({"ok": True}); return
                if path == "/api/test/ready": Handler.gate_case = "ready"; Handler.ready = True; self._json({"ok": True}); return
                self.send_error(404); return
            if path.startswith("/api/"):
                Handler.api_requests.append(path)
                if path == "/api/config/status":
                    Handler.status_hits += 1
                    if Handler.gate_case == "stale" and Handler.status_hits == 1:
                        Handler.status_started.set(); Handler.status_release.wait(timeout=8); payload = {"ready": True}
                    elif Handler.gate_case == "stale" and Handler.status_hits == 2:
                        payload = {"ready": False}
                    elif Handler.gate_case == "error": payload = {"ready": "invalid"}
                    else: payload = {"ready": Handler.ready}
                    self._json(payload); return
                if path == "/api/config":
                    if Handler.config_error: self._json({"error": "synthetic config error"}, 503); return
                    config = json.loads(json.dumps(Handler.config))
                    providers = config["values"].get("providers", {})
                    meta = {pid: bool(value.get("apiKey")) for pid, value in providers.items()}
                    for value in providers.values(): value["apiKey"] = ""
                    config["meta"] = {"providers": meta, "hasApiKey": meta.get(config["values"].get("defaultProvider", ""), False)}
                    self._json(config); return
                if path == "/api/bots": self._json({"bots": [{"id": "thyca", "name": "Thyca", "tint": "botanical", "role": "assistant"}, {"id": "coder", "name": "Coder", "tint": "botanical", "role": "coding"}]}); return
                if path == "/api/sessions": self._json({"sessions": []}); return
                if path == "/api/bots/thyca/dm": Handler.dm_hits += 1; self._json({"id": "dm-thyca", "dm": "thyca", "title": "Thyca", "messages": []}); return
                if path == "/api/bots/coder/settings": self._json({"bot_id": "coder", "model": {"id": "default", "thinking": None}, "effective": {"model": "legacy/unregistered", "provider": "legacy", "thinking": None}}); return
                if path == "/api/memory/stats": self._json({"files": [], "canonical": [], "total": 0}); return
                if path == "/api/traces/stats": self._json({"totals": {}, "by_bot": []}); return
                self._json({}); return
            if query.get("gateCase"):
                Handler.gate_case = query["gateCase"]
                Handler.ready = Handler.gate_case in ("ready", "ready-mobile")
                Handler.config_error = Handler.gate_case == "config-error"
                Handler.status_hits = Handler.dm_hits = Handler.save_count = 0
                Handler.status_started.clear(); Handler.status_release.clear(); Handler.api_requests.clear(); Handler.verification_meta = {}
                Handler.config = {"values": {"providers": {"legacy": {"baseUrl": "https://legacy.invalid/v1", "api": "openai_chat", "apiKeyEnv": "THYCA_SETUP_GATE_MISSING"}}, "models": {}, "defaultProvider": "legacy", "defaultModel": "legacy/unregistered"}, "schema": {"sections": [{"fields": [{"key": "provider.reasoningEffort", "choices": ["low", "high", "max"], "default": "high"}]}]}}
            target = safe_file(WEBUI, path)
            if target is None: self.send_error(404); return
            body = target.read_bytes()
            if path == "/" or path.startswith("/ui/") and path.endswith(("", "/", "/index.html")):
                head = f'<script>window.__spaGateCase=new URLSearchParams(location.search).get("gateCase");window.__controlBase={json.dumps(Handler.control_base)};window.__scriptErrors=[];window.__promiseErrors=[];window.addEventListener("error",e=>window.__scriptErrors.push(String(e.message)));window.addEventListener("unhandledrejection",e=>window.__promiseErrors.push(String(e.reason)))</script></head>'.encode()
                script = SETUP_GATE_RESULT_SCRIPT.encode()
                body = body.replace(b"</head>", head).replace(b"</body>", b"<script>" + script + b"</script></body>")
            self.send_response(200); self.send_header("Content-Type", content_type(target)); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

        def do_POST(self) -> None:
            path = urlsplit(self.path).path
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            Handler.api_requests.append("POST " + path)
            if path == "/api/onboarding/verify":
                Handler.verification_meta = {"apiKeyProvided": bool(payload.get("apiKey")), "providerIdProvided": "providerId" in payload}
                self._json({"models": ["fixture-model"], "apiKeyOk": True}); return
            if path == "/api/config":
                Handler.config = {"values": payload, "schema": Handler.config.get("schema", {})}
                Handler.ready = bool(payload.get("defaultModel") and payload.get("providers", {}).get("local", {}).get("apiKey"))
                Handler.save_count += 1
                self._json({"ok": True, "ready": Handler.ready}); return
            self._json({"ok": True})

    class ControlHandler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None: pass
        def _json(self, payload: dict) -> None:
            body = json.dumps(payload).encode(); self.send_response(200); self.send_header("Access-Control-Allow-Origin", "*"); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path == "/release": Handler.status_release.set(); self._json({"ok": True}); return
            if path == "/ready": Handler.gate_case = "ready"; Handler.ready = True; self._json({"ok": True}); return
            if path == "/config-ok": Handler.config_error = False; self._json({"ok": True}); return
            if path == "/state": self._json({"statusHits": Handler.status_hits, "statusStarted": Handler.status_started.is_set(), "dmHits": Handler.dm_hits, "verification": Handler.verification_meta, "saveCount": Handler.save_count, "apiRequests": Handler.api_requests}); return
            self.send_error(404)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    control = ThreadingHTTPServer(("127.0.0.1", 0), ControlHandler)
    control_thread = threading.Thread(target=control.serve_forever, daemon=True); control_thread.start()
    request_state = f"http://127.0.0.1:{control.server_port}"
    Handler.control_base = request_state
    try:
        chrome = shutil.which("google-chrome")
        required = _cdp_browser_result(chrome, f"http://127.0.0.1:{server.server_port}/ui/profile?bot=coder&gateCase=required", 1280, Path("/tmp/thyca-setup-gate-ready.png"), tmp_path / "required")
        assert required.get("passed") is True, required
        assert required["finalRoute"] == {"page": "profile", "search": "?bot=coder", "hash": ""}
        assert required["beforeSave"]["route"] == {"page": "provider", "search": "", "hash": ""}
        assert required["beforeSave"]["setupState"] == "required" and required["beforeSave"]["chatHidden"] is True and required["beforeSave"]["chatInert"] is True
        assert required["beforeSave"]["sidebarInert"] is True and required["beforeSave"]["menuDisabled"] is True
        assert required["beforeSave"]["noticeText"] == "Thiết lập model để bắt đầu"
        assert required["beforeSave"]["statePage"] == "provider" and required["beforeSave"]["dmHits"] == 0
        assert required["verification"] == {"apiKeyProvided": True, "providerIdProvided": False}
        assert required["afterEscape"] is True and required["afterResize"] is True
        assert required["afterOpen"] == {"inert": True, "open": False, "overlayHidden": True, "menuDisabled": True, "chatInert": True, "groupDialogOpen": False}
        assert required["saveCount"] == 1 and required["dmHits"] == 0 and "POST /api/sessions" not in required["apiRequests"]
        ready = _cdp_browser_result(chrome, f"http://127.0.0.1:{server.server_port}/ui/profile?bot=coder&gateCase=ready", 1280, None, tmp_path / "ready")
        assert ready.get("passed") is True and ready["setupState"] == "ready" and ready["notice"] is False and ready["ordinaryEventIgnored"] is True
        assert ready["route"] == {"page": "profile", "search": "?bot=coder", "hash": ""}
        assert ready["desktopSidebarUnlocked"] is True
        ready_mobile = _cdp_browser_result(chrome, f"http://127.0.0.1:{server.server_port}/ui/profile?bot=coder&gateCase=ready-mobile", 375, None, tmp_path / "ready-mobile")
        assert ready_mobile.get("passed") is True and ready_mobile["mobileSidebarInitiallyInert"] and ready_mobile["mobileOpenAllowed"] and ready_mobile["mobileCloseRestoresInert"]
        error = _cdp_browser_result(chrome, f"http://127.0.0.1:{server.server_port}/ui/profile?bot=coder&gateCase=error", 1280, None, tmp_path / "error")
        assert error.get("passed") is True and error["errorState"] == "error" and error["retryRestored"] is True
        config_error = _cdp_browser_result(chrome, f"http://127.0.0.1:{server.server_port}/ui/profile?bot=coder&gateCase=config-error", 1280, None, tmp_path / "config-error")
        assert config_error.get("passed") is True and config_error["errorState"] == "error" and config_error["retryRequired"] is True
        stale = _cdp_browser_result(chrome, f"http://127.0.0.1:{server.server_port}/ui/profile?bot=coder&gateCase=stale", 1280, None, tmp_path / "stale")
        assert stale.get("passed") is True and stale["checkingVisible"] is True and stale["staleStayedLocked"] is True and stale["retryRestored"] is True
        evidence = {"required": required, "readyLegacy": ready, "readyMobile": ready_mobile, "errorRetry": error, "configErrorRetry": config_error, "staleRetry": stale}
        for stage in ("ready", "ready-mobile", "error", "config-error", "stale"):
            html_artifact = Path(f"/tmp/thyca-setup-gate-{stage}.html")
            if html_artifact.exists():
                html_artifact.write_text(html_artifact.read_text(encoding="utf-8").replace("synthetic-only-key", "[redacted]"), encoding="utf-8")
        Path("/tmp/thyca-setup-gate-browser.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finally:
        Handler.status_release.set()
        server.shutdown(); server.server_close(); thread.join(timeout=3)
        control.shutdown(); control.server_close(); control_thread.join(timeout=3)


SETUP_GATE_RESULT_SCRIPT = r"""
(async () => {
  const wait = async predicate => { const end=Date.now()+12000; while(Date.now()<end){if(await predicate())return;await new Promise(r=>setTimeout(r,25));} throw new Error('setup gate timeout: '+JSON.stringify({state:ThycaRouter?.setupState(),route:ThycaRouter?.currentRoute(),errors:window.__scriptErrors})); };
  const gateCase = window.__spaGateCase;
  try {
    if (gateCase === 'required') {
      await wait(()=>ThycaRouter?.setupState()==='required' && ThycaRouter.currentPage()==='provider');
      const before={setupState:ThycaRouter.setupState(),route:ThycaRouter.currentRoute(),chatHidden:document.querySelector('#chat-workspace').hidden,chatInert:document.querySelector('#chat-workspace').inert,sidebarInert:document.querySelector('#chat-sidebar').inert,menuDisabled:document.querySelector('.menu-button').disabled,statePage:history.state?.thycaSpa?.page,dmHits:(await fetch('/api/test/state').then(r=>r.json())).dmHits,noticeText:document.querySelector('[data-setup-notice]').textContent};
      document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true})); const afterEscape=document.querySelector('#chat-sidebar').inert;
      window.dispatchEvent(new Event('resize')); const afterResize=document.querySelector('#chat-sidebar').inert;
      openMobile(); document.querySelector('.menu-button').click();
      const afterOpen={inert:document.querySelector('#chat-sidebar').inert,open:document.querySelector('#chat-sidebar').classList.contains('mobile-open'),overlayHidden:document.querySelector('#mobile-overlay').hidden,menuDisabled:document.querySelector('.menu-button').disabled,chatInert:document.querySelector('#chat-workspace').inert,groupDialogOpen:document.querySelector('#group-dialog').open};
      await ThycaRouter.navigate('settings'); ThycaRouter.dispatch('selectBot','coder');
      document.querySelector('[data-page-link="settings"]')?.click();
      history.pushState({thycaSpa:{page:'settings',search:'',hash:''}},'', '/?bypass=1'); dispatchEvent(new PopStateEvent('popstate',{state:history.state}));
      await wait(()=>ThycaRouter.currentPage()==='provider' && history.state?.thycaSpa?.page==='provider');
      const apiState=document.querySelector('#chat-workspace').hidden;
      document.querySelector('#new-connection').click();
      document.querySelector('#pv-base-url').value='https://mock.invalid/v1'; document.querySelector('#pv-base-url').dispatchEvent(new Event('input',{bubbles:true}));
      document.querySelector('#pv-key').value='synthetic-only-key'; document.querySelector('#pv-key').dispatchEvent(new Event('input',{bubbles:true}));
      document.querySelector('#v-submit').click();
      await wait(()=>document.querySelector('#m-picker').querySelector('[data-model="fixture-model"]'));
      document.querySelector('#m-picker').value='model:fixture-model'; document.querySelector('#m-picker').dispatchEvent(new Event('change',{bubbles:true}));
      document.querySelector('#pv-id').value='local'; document.querySelector('#pv-id').dispatchEvent(new Event('input',{bubbles:true}));
      document.querySelector('#save-use').click();
      await wait(()=>ThycaRouter.setupState()==='ready' && ThycaRouter.currentRoute().page==='profile');
      await wait(()=>document.querySelector('[data-page-workspace="profile"]:not([hidden])'));
      const afterState=await fetch('/api/test/state').then(r=>r.json());
      const result={passed:true,beforeSave:before,afterEscape,afterResize,afterOpen,finalRoute:ThycaRouter.currentRoute(),statePage:history.state?.thycaSpa?.page,verification:afterState.verification,saveCount:afterState.saveCount,dmHits:afterState.dmHits,apiRequests:afterState.apiRequests,providerStatePage:apiState};
      result.scriptErrors=window.__scriptErrors; result.unhandledRejections=window.__promiseErrors; document.body.dataset.spaTestResult=JSON.stringify(result); return;
    }
    if (gateCase === 'ready' || gateCase === 'ready-mobile') {
      await wait(()=>ThycaRouter?.setupState()==='ready' && ThycaRouter.currentPage()==='profile');
      const before=await fetch('/api/test/state').then(r=>r.json());
      const sidebar=document.querySelector('#chat-sidebar');
      let desktopSidebarUnlocked=false,mobileSidebarInitiallyInert=false,mobileOpenAllowed=false,mobileCloseRestoresInert=false;
      if (gateCase === 'ready') desktopSidebarUnlocked=!sidebar.inert&&!sidebar.classList.contains('mobile-open');
      else { mobileSidebarInitiallyInert=sidebar.inert&&!sidebar.classList.contains('mobile-open'); openMobile(); mobileOpenAllowed=!sidebar.inert&&sidebar.classList.contains('mobile-open'); closeMobile(); mobileCloseRestoresInert=sidebar.inert&&!sidebar.classList.contains('mobile-open')&&document.querySelector('#mobile-overlay').hidden; }
      dispatchEvent(new Event('thyca:config-saved')); await new Promise(r=>setTimeout(r,100));
      const after=await fetch('/api/test/state').then(r=>r.json());
      const result={passed:true,setupState:ThycaRouter.setupState(),route:ThycaRouter.currentRoute(),notice:!!document.querySelector('[data-setup-notice]'),dmHits:after.dmHits,ordinaryEventIgnored:before.statusHits===after.statusHits,desktopSidebarUnlocked,mobileSidebarInitiallyInert,mobileOpenAllowed,mobileCloseRestoresInert};
      result.scriptErrors=window.__scriptErrors; result.unhandledRejections=window.__promiseErrors; document.body.dataset.spaTestResult=JSON.stringify(result); return;
    }
    if (gateCase === 'error') {
      await wait(()=>ThycaRouter?.setupState()==='error' && ThycaRouter.currentPage()==='provider');
      const errorState=ThycaRouter.setupState(); await fetch(window.__controlBase+'/ready');
      document.querySelector('[data-setup-notice] button').click();
      await wait(()=>ThycaRouter.setupState()==='ready' && ThycaRouter.currentRoute().page==='profile');
      const result={passed:true,errorState,retryRestored:true,route:ThycaRouter.currentRoute()};
      result.scriptErrors=window.__scriptErrors; result.unhandledRejections=window.__promiseErrors; document.body.dataset.spaTestResult=JSON.stringify(result); return;
    }
    if (gateCase === 'config-error') {
      await wait(()=>ThycaRouter?.setupState()==='error' && ThycaRouter.currentPage()==='provider');
      const errorState=ThycaRouter.setupState(); await fetch(window.__controlBase+'/config-ok');
      document.querySelector('[data-setup-notice] button').click();
      await wait(()=>ThycaRouter.setupState()==='required');
      await wait(()=>!document.querySelector('#editor-controls').disabled);
      const result={passed:true,errorState,retryRequired:true,notice:document.querySelector('[data-setup-notice]').textContent};
      result.scriptErrors=window.__scriptErrors; result.unhandledRejections=window.__promiseErrors; document.body.dataset.spaTestResult=JSON.stringify(result); return;
    }
    if (gateCase === 'stale') {
      await wait(async()=> (await fetch(window.__controlBase+'/state').then(r=>r.json())).statusStarted);
      const checkingNotice=document.querySelector('#thyca-page-host>[data-setup-notice]');
      const checkingVisible=!!checkingNotice&&checkingNotice.getAttribute('role')==='status'&&getComputedStyle(checkingNotice).gridColumnStart==='3';
      dispatchEvent(new Event('thyca:config-saved'));
      await wait(()=>ThycaRouter.setupState()==='required');
      await fetch(window.__controlBase+'/release');
      await new Promise(r=>setTimeout(r,150));
      const staleStayedLocked=ThycaRouter.setupState()==='required' && ThycaRouter.currentPage()==='provider';
      await fetch(window.__controlBase+'/ready'); dispatchEvent(new Event('thyca:config-saved'));
      await wait(()=>ThycaRouter.setupState()==='ready' && ThycaRouter.currentRoute().page==='profile');
      const result={passed:true,checkingVisible,staleStayedLocked,retryRestored:true,route:ThycaRouter.currentRoute()};
      result.scriptErrors=window.__scriptErrors; result.unhandledRejections=window.__promiseErrors; document.body.dataset.spaTestResult=JSON.stringify(result); return;
    }
  } catch(error) { document.body.dataset.spaTestResult=JSON.stringify({error:String(error),stage:window.__spaTestStage,route:ThycaRouter?.currentRoute(),setupState:ThycaRouter?.setupState(),scriptErrors:window.__scriptErrors,unhandledRejections:window.__promiseErrors}); }
})();
"""
