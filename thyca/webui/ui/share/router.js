/* Native root-only SPA router. Page factories register once; retained workspaces
   and controllers mount lazily, while route data lives only in history.state. */
'use strict';
(function () {
  const pages = new Map();
  const host = document.querySelector('#thyca-page-host');
  const shellReady = bootShell();
  let current = null, route = null, generation = 0, styleRetrySeq = 0;
  let setupState = 'checking', setupGeneration = 0, startupRoute = null, startupReturn = null;
  const chatWorkspace = document.querySelector('#chat-workspace');
  if (chatWorkspace) { chatWorkspace.hidden = true; chatWorkspace.inert = true; chatWorkspace.setAttribute('aria-hidden', 'true'); }
  const PAGE_IDS = ['chat', 'memories', 'profile', 'provider', 'dashboard'];
  // Legacy /ui/settings* URLs land on chat with the shared Settings dialog
  // open (see pendingSettingsDialog); the dedicated settings page is gone.
  let pendingSettingsDialog = false;
  const validPage = id => PAGE_IDS.includes(id);

  function parseTurnHash(hash) {
    const raw = String(hash || '').replace(/^#/, '');
    const slash = raw.indexOf('/');
    if (slash <= 0 || raw.indexOf('/', slash + 1) !== -1) return null;
    let sid;
    try { sid = decodeURIComponent(raw.slice(0, slash)); } catch { return null; }
    const turnText = raw.slice(slash + 1);
    if (!/^[A-Za-z0-9_-]{1,120}$/.test(sid) || !/^\d{1,8}$/.test(turnText)) return null;
    const turn = Number(turnText);
    return Number.isSafeInteger(turn) ? { sid, turn } : null;
  }
  const turnHash = ({ sid, turn }) => '#' + encodeURIComponent(sid) + '/' + turn;
  function validCalendarDate(value) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
    const date = new Date(value + 'T00:00:00Z');
    return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value;
  }
  function dashboardRoute(search = '', hash = '', allIfUnbounded = false) {
    const input = new URLSearchParams(String(search).replace(/^\?/, ''));
    const output = new URLSearchParams();
    for (const key of ['bot', 'from', 'to', 'q', 'model', 'status']) {
      const value = input.get(key);
      if (!value || value.length > 200) continue;
      if ((key === 'from' || key === 'to') && !validCalendarDate(value)) continue;
      output.set(key, value);
    }
    let range = input.get('range');
    let from = output.get('from'), to = output.get('to');
    const invalidOrder = !!(from && to && from > to);
    if (invalidOrder) { output.delete('from'); output.delete('to'); from = to = null; }
    if (from || to) range = '';
    else if (range !== 'all') range = allIfUnbounded && !invalidOrder ? 'all' : '';
    if (invalidOrder) range = '';
    if (range === 'all') output.set('range', 'all');
    let detail = null;
    if (String(hash || '').replace(/^#/, '')) detail = parseTurnHash(hash);
    else {
      const sid = input.get('s'), turn = input.get('t');
      if (sid && turn && /^[A-Za-z0-9_-]{1,120}$/.test(sid) && /^\d{1,8}$/.test(turn)) detail = { sid, turn: Number(turn) };
    }
    let view = input.get('view');
    if (!['usage', 'cost', 'trace'].includes(view)) view = '';
    if (allIfUnbounded || detail) view = 'trace';
    if (view) output.set('view', view);
    return { page: 'dashboard', search: output.size ? '?' + output.toString() : '', hash: detail ? turnHash(detail) : '' };
  }
  function routeParts(page, search = '', hash = '') {
    if (page === 'trace') return dashboardRoute(search, hash, true);
    if (page === 'dashboard') return dashboardRoute(search, hash);
    const allowed = { chat: ['bot', 's'], memories: ['bot'], profile: ['bot'], provider: [] }[page] || [];
    const input = new URLSearchParams(String(search).replace(/^\?/, ''));
    const output = new URLSearchParams();
    for (const key of allowed) { const value = input.get(key); if (value && value.length <= 200) output.set(key, value); }
    const rawHash = String(hash).replace(/^#/, '');
    const safeHash = page === 'profile' && /^[A-Za-z0-9_.-]{1,80}$/.test(rawHash) ? '#' + rawHash : '';
    return { page, search: output.size ? '?' + output.toString() : '', hash: safeHash };
  }
  function normalize(value) {
    if (value && value.page === 'settings' && typeof value.search === 'string' && typeof value.hash === 'string') {
      // Pre-migration history entries: the dedicated settings page is now
      // the shared footer dialog over chat.
      pendingSettingsDialog = true;
      return { page: 'chat', search: '', hash: '' };
    }
    return value && typeof value.page === 'string' && typeof value.search === 'string' && typeof value.hash === 'string'
      && (validPage(value.page) || value.page === 'trace') ? routeParts(value.page, value.search, value.hash) : null;
  }
  function legacyRoute(path, search, hash) {
    const map = { '/ui/chat': 'chat', '/ui/memories': 'memories', '/ui/profile': 'profile', '/ui/provider': 'provider', '/ui/dashboard': 'dashboard', '/ui/trace': 'trace', '/ui/settings': 'chat' };
    let decodedPath;
    try { decodedPath = decodeURIComponent(path); } catch { decodedPath = ''; }
    const key = decodedPath.replace(/\/index\.html$/, '').replace(/\/$/, '') || '/';
    if (key === '/ui/settings') {
      pendingSettingsDialog = true;   // honored on arrival when setup allows
      return { page: 'chat', search: '', hash: '' };
    }
    let page = map[key] || 'chat';
    const params = new URLSearchParams(search);
    if (page === 'provider' && params.has('bot')) page = 'profile';
    if (page === 'trace') return dashboardRoute(search, hash, true);
    if (page === 'dashboard' && (params.has('s') || params.has('t'))) return dashboardRoute(search, hash);
    if (page === 'chat' && hash && parseTurnHash(hash)) return dashboardRoute(search, hash, true);
    return routeParts(page, search, hash);
  }
  function validReturn(value, detailRoute) {
    if (!value || value.page !== 'dashboard' || value.hash !== '' || detailRoute?.page !== 'dashboard' || !parseTurnHash(detailRoute.hash)) return null;
    const candidate = dashboardRoute(value.search, '');
    if (candidate.search !== value.search || candidate.search !== detailRoute.search) return null;
    return { page: 'dashboard', search: candidate.search, hash: '' };
  }
  function historyState(next, returnTo) {
    const state = { thycaSpa: next };
    const marker = validReturn(returnTo, next);
    if (marker) state.thycaReturn = marker;
    return state;
  }
  function currentRoute() { return route ? { ...route } : { page: 'chat', search: '', hash: '' }; }
  function setNavigationLock() {
    const locked = setupState !== 'ready';
    document.documentElement.dataset.setupState = setupState;
    const sidebar = document.querySelector('#chat-sidebar'), menu = document.querySelector('.menu-button');
    if (locked && sidebar?.classList.contains('mobile-open') && typeof closeMobile === 'function') closeMobile();
    if (sidebar) sidebar.inert = locked || (matchMedia('(max-width:850px)').matches && !sidebar.classList.contains('mobile-open'));
    if (menu) menu.disabled = locked;
    for (const link of document.querySelectorAll('[data-page-link]')) {
      if (locked && link.dataset.pageLink !== 'provider') {
        if (!link.hasAttribute('data-setup-original-tabindex')) link.dataset.setupOriginalTabindex = link.getAttribute('tabindex') ?? '';
        link.setAttribute('aria-disabled', 'true'); link.tabIndex = -1; link.classList.add('setup-disabled');
      } else {
        link.removeAttribute('aria-disabled'); link.classList.remove('setup-disabled');
        if (link.hasAttribute('data-setup-original-tabindex')) {
          const original = link.dataset.setupOriginalTabindex;
          if (original) link.setAttribute('tabindex', original); else link.removeAttribute('tabindex');
          delete link.dataset.setupOriginalTabindex;
        }
      }
    }
    const splitter = document.querySelector('.splitter');
    if (splitter) { splitter.inert = locked; splitter.setAttribute('aria-disabled', String(locked)); }
  }
  setNavigationLock();
  function renderSetupNotice() {
    const view = pages.get('provider')?.view;
    if (!view && setupState === 'checking') {
      let notice = host.querySelector('[data-setup-notice]');
      if (!notice) {
        notice = document.createElement('aside'); notice.dataset.setupNotice = 'true';
        notice.setAttribute('role', 'status'); notice.setAttribute('aria-live', 'polite'); host.prepend(notice);
      }
      notice.className = 'setup-notice setup-notice-checking'; notice.textContent = 'Kiểm tra cấu hình model…';
      return;
    }
    host.querySelectorAll(':scope > [data-setup-notice]').forEach(notice => notice.remove());
    if (!view) return;
    let notice = view.querySelector('[data-setup-notice]');
    if (setupState === 'ready') { notice?.remove(); return; }
    if (!notice) {
      notice = document.createElement('aside'); notice.dataset.setupNotice = 'true';
      notice.setAttribute('role', 'status'); notice.setAttribute('aria-live', 'polite');
      view.prepend(notice);
    }
    notice.className = `setup-notice setup-notice-${setupState}`;
    notice.replaceChildren();
    if (setupState === 'checking') {
      notice.textContent = 'Kiểm tra cấu hình model…';
      return;
    }
    const heading = document.createElement('strong');
    const copy = document.createElement('span');
    if (setupState === 'required') {
      heading.textContent = 'Thiết lập model để bắt đầu';
      notice.append(heading);
      return;
    }
    heading.textContent = 'Không kiểm tra được cấu hình';
    copy.textContent = 'Hãy thử lại. Nếu cấu hình chưa hợp lệ, bạn có thể sửa ở trang này.';
    const retry = document.createElement('button');
    retry.type = 'button'; retry.className = 'quiet-button'; retry.textContent = 'Thử lại';
    retry.addEventListener('click', () => {
      window.dispatchEvent(new Event('thyca:setup-retry'));
      void startSetupCheck();
    });
    notice.append(heading, copy, retry);
  }
  function updateSetupState(next) {
    setupState = next; setNavigationLock(); renderSetupNotice();
  }
  function setupProviderRoute() {
    const next = routeParts('provider');
    if (current?.page === 'provider') {
      if (route?.page !== 'provider' || route.search || route.hash) { route = next; current.ctx.route = next; }
      historyWrite('replaceState', { thycaSpa: next });
      renderSetupNotice();
      return Promise.resolve();
    }
    return navigate('provider', next, { replace: true, force: true });
  }
  async function startSetupCheck() {
    const ticket = ++setupGeneration;
    updateSetupState('checking');
    let statusRequest;
    try { statusRequest = getJson('/api/config/status'); }
    catch (error) { statusRequest = Promise.reject(error); }
    let status, failure = null;
    try {
      status = await statusRequest;
      if (!status || typeof status.ready !== 'boolean') throw new Error('invalid readiness response');
    } catch (error) { failure = error; }
    if (!failure && status.ready === false) {
      try {
        const config = await getJson('/api/config');
        if (!config || !config.values || typeof config.values !== 'object' || Array.isArray(config.values)) throw new Error('invalid config response');
      } catch (error) { failure = error; }
    }
    if (ticket !== setupGeneration) return;
    if (failure || !status.ready) {
      await setupProviderRoute();
      if (ticket !== setupGeneration) return;
      updateSetupState(failure ? 'error' : 'required');
      return;
    }
    updateSetupState('ready');
    await navigate(startupRoute.page, startupRoute, { replace: true, returnTo: startupReturn });
  }
  function notifyRoute(entry) {
    if (entry?.ctx) for (const fn of [...entry.ctx.listeners]) fn(currentRoute());
  }
  function showError(view, error, retry) {
    let note = view.querySelector('.spa-error');
    if (!note) { note = document.createElement('div'); note.className = 'spa-error'; note.setAttribute('role', 'alert'); view.append(note); }
    note.replaceChildren(document.createTextNode(`Không thể mở màn hình: ${error?.message || error} `));
    const button = document.createElement('button'); button.type = 'button'; button.textContent = 'Thử lại';
    button.addEventListener('click', retry, { once: true }); note.append(button);
  }
  function styleLink(href, pageStyle) {
    let link = [...document.querySelectorAll('link[data-page-style]')].find(x => new URL(x.href).pathname === href);
    const retry = link?.dataset.loadState === 'failed';
    if (retry) { link.remove(); link = null; }
    if (!link) {
      link = document.createElement('link'); link.rel = 'stylesheet';
      link.href = retry ? `${href}?spa-retry=${++styleRetrySeq}` : href;
      link.dataset.pageStyle = pageStyle; link.dataset.loadState = 'pending';
    }
    return link;
  }
  async function awaitStylesheet(link, ticket) {
    if (link.dataset.loadState === 'failed') throw new Error(`CSS không tải được: ${link.href}`);
    if (link.sheet) { link.dataset.loadState = 'loaded'; return; }
    await new Promise((resolve, reject) => {
      let settled = false;
      const finish = (error) => {
        if (settled) return; settled = true; clearTimeout(timer);
        link.removeEventListener('load', onload); link.removeEventListener('error', onerror);
        if (error) reject(error); else resolve();
      };
      const onload = () => {
        if (!link.sheet) { onerror(); return; }
        link.dataset.loadState = 'loaded'; finish();
      };
      const onerror = () => { link.dataset.loadState = 'failed'; finish(new Error(`CSS không tải được: ${link.href}`)); };
      const timer = setTimeout(() => { link.dataset.loadState = 'failed'; finish(new Error(`Quá thời gian tải CSS: ${link.href}`)); }, 10000);
      link.addEventListener('load', onload, { once: true }); link.addEventListener('error', onerror, { once: true });
      if (!link.isConnected) document.head.append(link);
      if (link.sheet) { link.dataset.loadState = 'loaded'; finish(); }
    });
    if (ticket !== generation) return;
  }
  async function enableStyles(entry, ticket) {
    const page = entry.page;
    const shared = page === 'chat' ? null : styleLink('/ui/share/app-shell.css', 'app-shell');
    const pageStyle = styleLink(entry.stylesheet, page);
    for (const link of document.querySelectorAll('link[data-page-style]')) {
      const style = link.dataset.pageStyle;
      link.media = style === page || (page !== 'chat' && style === 'app-shell') ? 'all' : 'not all';
    }
    if (shared) shared.media = 'all';
    pageStyle.media = 'all';
    document.body.dataset.activePage = page;
    for (const link of [shared, pageStyle].filter(Boolean)) {
      await awaitStylesheet(link, ticket);
      if (ticket !== generation) return;
    }
  }
  function contextFor(page, view) {
    const listeners = new Set();
    const ctx = {
      root: view,
      $: (s, base = view) => base.querySelector(s),
      $$: (s, base = view) => [...base.querySelectorAll(s)],
      route: { page, search: '', hash: '' },
      sidebar: document.querySelector('#chat-sidebar'),
      shellReady,
      navigate(pageId, parts = {}, options = {}) { return navigate(pageId, parts, options); },
      updateRoute(parts = {}) {
        const next = routeParts(page,
          Object.hasOwn(parts, 'search') ? parts.search : ctx.route.search,
          Object.hasOwn(parts, 'hash') ? parts.hash : ctx.route.hash);
        if (ctx.route.search === next.search && ctx.route.hash === next.hash) return;
        ctx.route = next;
        if (current?.page === page) {
          route = next;
          historyWrite('replaceState', historyState(route, history.state?.thycaReturn));
        }
      },
      returnTo(parts = {}) {
        const fallback = routeParts(page,
          Object.hasOwn(parts, 'search') ? parts.search : ctx.route.search,
          Object.hasOwn(parts, 'hash') ? parts.hash : '');
        const origin = validReturn(history.state?.thycaReturn, route);
        if (page === 'dashboard' && origin && origin.search === fallback.search) return history.back();
        return navigate(fallback.page, fallback, { replace: true });
      },
      highlightBot(id) { const entry = pages.get(page); if (entry) entry.highlighted = id && getBot(id) ? id : ''; if (current?.page === page) renderTeam(); },
      renderTeam() { if (current?.page === page) renderTeam(); },
      isActive() { return current?.page === page; },
      onRoute(listener) { listeners.add(listener); return () => listeners.delete(listener); },
      listeners,
    };
    return ctx;
  }
  function setWorkspace(entry, visible) {
    if (!entry?.view) return;
    entry.view.hidden = !visible; entry.view.inert = !visible;
    entry.view.setAttribute('aria-hidden', String(!visible));
  }
  function bindWorkspaceDialogs(view) {
    if (view.dataset.dialogsBound) return;
    view.dataset.dialogsBound = 'true';
    view.addEventListener('keydown', event => {
      if (event.key !== 'Escape') return;
      const dialog = [...view.querySelectorAll('dialog[open]')].at(-1);
      if (dialog) { event.preventDefault(); dialog.close(); }
    });
    view.addEventListener('click', event => {
      const close = event.target.closest('.dialog-close');
      const dialog = close?.closest('dialog') || (event.target.tagName === 'DIALOG' ? event.target : null);
      if (!dialog?.open) return;
      if (close) { dialog.close(); return; }
      const rect = dialog.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
    });
  }
  async function navigate(requestedPage, parts = {}, options = {}) {
    const { replace = false, force = false } = options;
    let page = requestedPage;
    if (page === 'trace') {
      const savedDashboard = pages.get('dashboard')?.ctx?.route;
      const search = Object.hasOwn(parts, 'search') ? parts.search : (savedDashboard?.search || '');
      const hash = Object.hasOwn(parts, 'hash') ? parts.hash : (savedDashboard?.hash || '');
      parts = dashboardRoute(search, hash, true);
      page = 'dashboard';
    }
    if (!validPage(page)) return;
    if (setupState !== 'ready' && page !== 'provider') return setupProviderRoute();
    const entry = pages.get(page);
    if (!entry) { showError(host, new Error(`Màn hình ${page} chưa đăng ký`), () => {}); return; }
    const old = route || { page: 'chat', search: '', hash: '' };
    const saved = entry.ctx?.route || (old.page === page ? old : { page, search: '', hash: '' });
    const next = routeParts(page,
      Object.hasOwn(parts, 'search') ? parts.search : saved.search,
      Object.hasOwn(parts, 'hash') ? parts.hash : saved.hash);
    const destinationRouteChanged = !entry.ctx || saved.search !== next.search || saved.hash !== next.hash;
    const previous = current;
    const sameRoute = route?.page === next.page && route.search === next.search && route.hash === next.hash;
    if (!force && previous === entry && sameRoute) { drainPendingSettingsDialog(); return; }
    const pageChanged = !previous || previous.page !== page;
    let returnMarker;
    if (Object.hasOwn(options, 'returnTo')) returnMarker = validReturn(options.returnTo, next);
    else if (!replace && old.page === 'dashboard' && !parseTurnHash(old.hash) && parseTurnHash(next.hash) && old.search === next.search)
      returnMarker = { page: 'dashboard', search: old.search, hash: '' };
    else if (replace) returnMarker = validReturn(history.state?.thycaReturn, next);
    else returnMarker = null;
    route = next;
    if (!sameRoute || replace || Object.hasOwn(options, 'returnTo')) historyWrite(replace ? 'replaceState' : 'pushState', historyState(next, returnMarker));
    if (pageChanged) {
      for (const dialog of document.querySelectorAll('dialog[open]')) dialog.close();
      if (typeof closeMobile === 'function') closeMobile();
      previous?.controller?.onLeave?.();
      if (previous) setWorkspace(previous, false);
    }
    const ticket = ++generation;
    if (force && previous === entry) setWorkspace(entry, false);
    current = entry;
    const shell = document.querySelector('.notebook-shell');
    if (shell) {
      for (const id of pages.keys()) shell.classList.remove(`${id}-shell`);
      shell.classList.add(`${page}-shell`);
      shell.setAttribute('aria-label', `Thyca — ${entry.title}`);
    }
    if (!entry.view) {
      entry.view = page === 'chat' ? document.querySelector('#chat-workspace') : document.createElement('section');
      if (!entry.view) { showError(host, new Error('Không tìm thấy vùng nội dung')); return; }
      if (page !== 'chat') {
        entry.view.className = 'workspace app-workspace'; entry.view.dataset.pageWorkspace = page;
        entry.view.setAttribute('aria-label', `Nội dung ${page}`); entry.view.tabIndex = -1; entry.view.hidden = true; entry.view.inert = true;
        host.append(entry.view);
      }
      entry.ctx = contextFor(page, entry.view);
      bindWorkspaceDialogs(entry.view);
    }
    entry.ctx.route = { ...next };
    try { await enableStyles(entry, ticket); }
    catch (err) {
      if (ticket !== generation || current !== entry) return;
      for (const item of pages.values()) setWorkspace(item, item === entry);
      showError(entry.view, err, () => { entry.view.querySelector('.spa-error')?.remove(); navigate(page, entry.ctx.route, { replace: true, force: true }); });
      return;
    }
    if (ticket !== generation || current !== entry) return;
    renderSetupNotice();
    if (!entry.controller) {
      try { entry.controller = entry.mount(entry.ctx) || {}; }
      catch (err) {
        showError(entry.view, err);
        for (const item of pages.values()) setWorkspace(item, item === entry);
        document.title = `Thyca — ${entry.title}`;
        return;
      }
      entry.ready = Promise.resolve(entry.controller.ready).catch(err => showError(entry.view, err));
    }
    if (destinationRouteChanged) notifyRoute(entry);
    await entry.ready;
    if (ticket !== generation || current !== entry) return;
    for (const item of pages.values()) setWorkspace(item, item === entry);
    if (destinationRouteChanged) entry.controller?.onRoute?.(entry.ctx.route);
    entry.controller?.onEnter?.(entry.ctx.route);
    renderTeam();
    for (const link of document.querySelectorAll('[data-page-link]')) {
      const active = link.dataset.pageLink === page;
      link.classList.toggle('is-current', active);
      if (active) {
        link.setAttribute('aria-current', 'page');
        link.scrollIntoView({ block: 'nearest', inline: 'nearest' });
      } else link.removeAttribute('aria-current');
    }
    document.title = `Thyca — ${entry.title}`;
    // Legacy settings URL / historical settings entry: chat is showing, so
    // open the shared dialog once the setup gate allows it. The marker
    // survives the gate redirect (it only clears when the dialog can open),
    // so a legacy settings URL reached while setup is required still opens
    // after onboarding completes.
    drainPendingSettingsDialog();
    if (pageChanged) {
      const heading = entry.view.querySelector('h1, [data-page-heading]') || entry.view;
      if (!heading.hasAttribute('tabindex')) heading.tabIndex = -1;
      heading.focus({ preventScroll: true });
    }
  }
  function drainPendingSettingsDialog() {
    // Honored on every arrival, including the same-route early return, so a
    // marked Settings history entry reopens the dialog however it is reached.
    if (pendingSettingsDialog && setupState === 'ready') {
      pendingSettingsDialog = false;
      if (typeof openSettingsDialog === 'function') openSettingsDialog();
    }
  }
  function historyWrite(method, state) {
    // Same-entry rewrites preserve a Settings marker; pushed entries never
    // inherit one, so only history that originated from Settings reopens it.
    if (method === 'replaceState' && history.state?.thycaSettings && state.thycaSettings !== true) {
      state = { ...state, thycaSettings: true };
    }
    history[method](state, '', '/');
  }
  function navigateFromState(state) {
    const raw = state?.thycaSpa;
    const marked = raw?.page === 'settings' || state?.thycaSettings === true;
    const next = normalize(raw) || legacyRoute(location.pathname, location.search, location.hash);
    if (marked) pendingSettingsDialog = true;   // normalize already sets it for page:'settings'
    const returnMarker = validReturn(state?.thycaReturn, next);
    const out = historyState(next, returnMarker);
    historyWrite('replaceState', marked ? { ...out, thycaSettings: true } : out);
    navigate(next.page, next, { replace: true, returnTo: returnMarker });
  }
  function dispatch(action, id, ...args) {
    if (setupState !== 'ready') return false;
    if (action === 'selectBot') return current?.controller?.selectBot?.(id);
    if (action === 'loadSession') return current?.controller?.loadSession?.(id, ...args);
  }
  window.ThycaRouter = {
    register(pageId, spec) {
      if (!validPage(pageId) || pages.has(pageId)) throw new Error(`duplicate/invalid SPA page: ${pageId}`);
      pages.set(pageId, { ...spec, page: pageId, view: null, controller: null, highlighted: '' });
    },
    navigate, dispatch, currentRoute, currentPage: () => current?.page || '',
    updateCurrent(parts) { current?.ctx?.updateRoute(parts); },
    highlightedBot: () => current?.page === 'chat' ? selectedBot : (current?.highlighted ?? ''),
    sidebarLabel(id, name, status) { return current?.controller?.sidebarLabel?.(id, name, status) || `Trò chuyện với ${name} · ${status}`; },
    get shellReady() { return shellReady; },
    setupState() { return setupState; },
  };
  addEventListener('popstate', e => navigateFromState(e.state));
  window.addEventListener('thyca:config-saved', () => {
    if (setupState !== 'ready') void startSetupCheck();
  });
  document.addEventListener('click', e => {
    const link = e.target.closest('a[href]');
    if (!link || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || link.target) return;
    const dest = new URL(link.href, location.href);
    if (dest.origin !== location.origin || !/^\/ui\/(chat|memories|profile|provider|dashboard|trace)(?:\/index\.html|\/)?$/.test(dest.pathname)) return;
    e.preventDefault();
    const next = legacyRoute(dest.pathname, dest.search, dest.hash);
    navigate(next.page, dest.search || dest.hash ? next : {});
  });
  const initial = normalize(history.state?.thycaSpa) || legacyRoute(location.pathname, location.search, location.hash);
  startupRoute = { ...initial };
  startupReturn = validReturn(history.state?.thycaReturn, startupRoute);
  // A reloaded entry that originated from Settings restores it like a revisit.
  if (history.state?.thycaSettings === true) pendingSettingsDialog = true;
  route = initial;
  // Startup normalization of a legacy /ui/settings URL or an old settings
  // state sets the pending dialog above; write the marker too, so navigating
  // away and back to this entry reopens Settings like later visits do.
  historyWrite('replaceState', pendingSettingsDialog
    ? { ...historyState(route, startupReturn), thycaSettings: true }
    : historyState(route, startupReturn));
  document.addEventListener('DOMContentLoaded', () => { void startSetupCheck(); }, { once: true });
})();
