from __future__ import annotations

import base64
import json
import os
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
PAGES = ("chat", "memories", "profile", "provider", "dashboard", "trace", "settings")


def test_spa_static_entry_aliases_and_asset_mime() -> None:
    for path in ("/", "/index.html", "/ui/%70rofile/index.html", *(f"/ui/{page}{suffix}" for page in PAGES for suffix in ("", "/", "/index.html"))):
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
                    Path('/tmp/thyca-spa-last-result.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
                    Path('/tmp/thyca-spa-last-dom.html').write_text(dom, encoding='utf-8')
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
            Path('/tmp/thyca-spa-last-result.json').write_text(diagnostic, encoding='utf-8')
            Path('/tmp/thyca-spa-last-dom.html').write_text(dom, encoding='utf-8')
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
    if (window.__spaMode === 'profile-screenshot') {
      const menu = document.querySelector('.menu-button'); menu.click();
      await wait(() => document.querySelector('#chat-sidebar').classList.contains('mobile-open'));
      const mobileViews = [];
      const mainGrid = document.querySelector('.main-grid');
      for (const page of ['chat', 'memories', 'profile', 'provider', 'dashboard', 'trace', 'settings']) {
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
    for (const page of ['memories', 'profile', 'provider', 'dashboard', 'trace', 'settings', 'chat']) {
      if (page === 'trace') await fetch('/api/test/fail-trace-css');
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
      if (page === 'trace' && document.querySelector('[data-page-workspace="trace"] .spa-error')) {
        document.querySelector('[data-page-workspace="trace"] .spa-error button').click();
        await wait(() => ThycaRouter.currentPage() === 'trace' && !document.querySelector('[data-page-workspace="trace"] .spa-error') && !document.querySelector('[data-page-workspace="trace"]').hidden && !!document.querySelector('[data-page-workspace="trace"] #trace-sessions'), 'Trace stylesheet retry mounted UI');
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
      if (['memories', 'dashboard', 'trace'].includes(page)) {
        if (document.querySelectorAll('#team-bot-list .bot-card[data-selected="true"]').length) throw new Error(`${page} inherited a Chat selection: page=${ThycaRouter.currentPage()} highlight=${JSON.stringify(ThycaRouter.highlightedBot())} selected=${JSON.stringify([...document.querySelectorAll('#team-bot-list .bot-card[data-selected="true"]')].map(card => card.dataset.botId))}`);
        document.querySelector('#team-bot-list [data-action="select"][data-bot-id="coder"]').click();
        await wait(() => ThycaRouter.currentRoute().search === '?bot=coder');
        document.querySelector('#team-bot-list [data-action="select"][data-bot-id="coder"]').click();
        await wait(() => ThycaRouter.currentRoute().search === '');
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
    if (document.querySelectorAll('[data-page-workspace]').length !== 7) throw new Error('workspaces were not retained');
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
      for (const page of ['memories', 'profile', 'provider', 'dashboard', 'trace', 'settings', 'chat']) await ThycaRouter.navigate(page);
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
        static_requests: list[tuple[str, int]] = []
        stream_release = threading.Event()
        provider_css_started = threading.Event()
        provider_css_release = threading.Event()
        provider_css_hold = threading.Event()
        fail_trace_css = False
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
                self._json({"providerCssStarted": self.provider_css_started.is_set(), "settingsStarted": self.settings_started.is_set(), "settingsReleased": self.settings_release.is_set(), "initialDmStarted": self.initial_dm_started.is_set(), "initialDmHits": self.initial_dm_hits, "settingsPosts": len(self.settings_posts), "canonicalPosts": len(self.canonical_posts), "apiCounts": counts})
                return
            if path == "/api/test/delay-provider-css":
                self.provider_css_hold.set(); self.provider_css_started.clear(); self.provider_css_release.clear()
                self._json({"ok": True}); return
            if path == "/api/test/release-provider-css":
                self.provider_css_release.set(); self.provider_css_hold.clear(); self._json({"ok": True}); return
            if path == "/api/test/fail-trace-css":
                type(self).fail_trace_css = True; self._json({"ok": True}); return
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
            if path == "/ui/trace/tracecss/trace.css" and self.fail_trace_css:
                type(self).fail_trace_css = False; self.send_error(404, "test CSS failure"); return
            if path.startswith("/api/"):
                self.api_requests.append(path)
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
                elif path == "/api/config/status": payload = {"configured": False}
                elif path == "/api/memory/stats": payload = {"files": [{"name": name, "content": content} for name, content in self.canonical.items()], "canonical": [], "total": len(self.canonical)}
                elif path == "/api/traces/stats": payload = {"totals": {}, "by_bot": []}
                else: payload = {}
                self._json(payload)
                return
            target = safe_file(WEBUI, path)
            if target is None:
                self.static_requests.append((path, 404)); self.send_error(404); return
            self.static_requests.append((path, 200))
            body = target.read_bytes()
            if path == "/" or unquote(path) in {f"/ui/{page}{suffix}" for page in PAGES for suffix in ("", "/", "/index.html")}:
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
                self._json({"providerCssStarted": Handler.provider_css_started.is_set(), "settingsStarted": Handler.settings_started.is_set(), "settingsReleased": Handler.settings_release.is_set(), "initialDmStarted": Handler.initial_dm_started.is_set(), "initialDmHits": Handler.initial_dm_hits, "settingsPosts": len(Handler.settings_posts), "canonicalPosts": len(Handler.canonical_posts), "apiCounts": counts}); return
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
    result = _cdp_browser_result(browser, root_url, 1280, Path('/tmp/thyca-spa-rework-profile-desktop.png'), tmp_path / "chrome-profile")
    Path('/tmp/thyca-spa-last-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    if Path('/tmp/thyca-spa-rework-profile-desktop.html').exists():
        Path('/tmp/thyca-spa-last-dom.html').write_text(Path('/tmp/thyca-spa-rework-profile-desktop.html').read_text(encoding='utf-8'), encoding='utf-8')
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
    assert result["audit"]["workspaces"] == 7 and result["audit"]["intervalsStable"] is True
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
    browser_results = {"desktop": result, "mobile": [], "legacy": [], "delayedChat": {}}
    legacy_cases = (
        ("/ui/%70rofile/index.html?spaMode=legacy-check", {"page": "profile", "search": "", "hash": ""}),
        ("/ui/provider/index.html?bot=coder&spaMode=legacy-check", {"page": "profile", "search": "?bot=coder", "hash": ""}),
        ("/ui/chat/index.html?bot=thyca&spaMode=legacy-check", {"page": "chat", "search": "?bot=thyca", "hash": ""}),
        ("/ui/dashboard/index.html?s=s9&t=2&spaMode=legacy-check#s9/2", {"page": "trace", "search": "?s=s9&t=2", "hash": "#s9/2"}),
    )
    for index, (legacy, expected) in enumerate(legacy_cases):
        legacy_result = _cdp_browser_result(browser, f"http://127.0.0.1:{server.server_port}{legacy}", 1280, None, tmp_path / f'legacy-{index}')
        assert legacy_result["legacy"] is True and legacy_result["route"] == expected
        assert legacy_result["state"] == expected and legacy_result["href"].endswith("/")
        assert legacy_result["frames"] == 0
        browser_results["legacy"].append(legacy_result)
    for width in (320, 375):
        mobile_result = _cdp_browser_result(
            browser, f"http://127.0.0.1:{server.server_port}/?spaMode=profile-screenshot", width,
            Path(f"/tmp/thyca-spa-rework-profile-{width}.png"), tmp_path / f"chrome-{width}", mobile=True,
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
    with urlopen(f"http://127.0.0.1:{server.server_port}/api/test/delay-initial-dm", timeout=3):
        pass
    delayed_result = _cdp_browser_result(browser, f"http://127.0.0.1:{server.server_port}/?spaMode=delayed-chat", 1280, None, tmp_path / 'delayed-chat')
    assert delayed_result.get("delayedDmRecovered") is True, delayed_result
    assert delayed_result["session"] == "dm-thyca" and delayed_result["bot"] == "thyca" and delayed_result["dmHits"] >= 2
    assert delayed_result["scriptErrors"] == [] and delayed_result["unhandledRejections"] == []
    browser_results["delayedChat"] = delayed_result
    Path("/tmp/thyca-spa-rework-browser.json").write_text(json.dumps(browser_results, indent=2) + "\\n", encoding="utf-8")
