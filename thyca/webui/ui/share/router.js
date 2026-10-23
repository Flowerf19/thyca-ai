/* Native root-only SPA router. Page factories register once; retained workspaces
   and controllers mount lazily, while route data lives only in history.state. */
'use strict';
(function () {
  const pages = new Map();
  const host = document.querySelector('#thyca-page-host');
  const shellReady = bootShell();
  let current = null, route = null, generation = 0, styleRetrySeq = 0;
  const validPage = id => ['chat', 'memories', 'profile', 'provider', 'dashboard', 'trace', 'settings'].includes(id);

  function routeParts(page, search = '', hash = '') {
    const allowed = { chat: ['bot', 's'], memories: ['bot'], profile: ['bot'], provider: [], dashboard: ['bot'], trace: ['bot', 's', 't'], settings: [] }[page] || [];
    const input = new URLSearchParams(String(search).replace(/^\?/, ''));
    const output = new URLSearchParams();
    for (const key of allowed) { const value = input.get(key); if (value && value.length <= 200) output.set(key, value); }
    const rawHash = String(hash).replace(/^#/, '');
    let safeHash = '';
    if (page === 'profile' && /^[A-Za-z0-9_.-]{1,80}$/.test(rawHash)) safeHash = '#' + rawHash;
    if (page === 'trace' && /^[A-Za-z0-9_-]{1,120}\/\d{1,8}$/.test(rawHash)) safeHash = '#' + rawHash;
    return { page, search: output.size ? '?' + output.toString() : '', hash: safeHash };
  }
  function normalize(value) {
    return value && validPage(value.page) && typeof value.search === 'string' && typeof value.hash === 'string'
      ? routeParts(value.page, value.search, value.hash) : null;
  }
  function legacyRoute(path, search, hash) {
    const map = { '/ui/chat': 'chat', '/ui/memories': 'memories', '/ui/profile': 'profile', '/ui/provider': 'provider', '/ui/dashboard': 'dashboard', '/ui/trace': 'trace', '/ui/settings': 'settings' };
    let decodedPath;
    try { decodedPath = decodeURIComponent(path); } catch { decodedPath = ''; }
    const key = decodedPath.replace(/\/index\.html$/, '').replace(/\/$/, '') || '/';
    let page = map[key] || 'chat';
    const params = new URLSearchParams(search);
    if (page === 'provider' && params.has('bot')) page = 'profile';
    if (page === 'dashboard' && (params.has('s') || params.has('t') || hash)) page = 'trace';
    if (page === 'chat' && hash) page = 'trace';
    return routeParts(page, search, hash);
  }
  function currentRoute() { return route ? { ...route } : { page: 'chat', search: '', hash: '' }; }
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
      navigate(pageId, parts = {}) { return navigate(pageId, parts); },
      updateRoute(parts = {}) {
        const next = routeParts(page,
          Object.hasOwn(parts, 'search') ? parts.search : ctx.route.search,
          Object.hasOwn(parts, 'hash') ? parts.hash : ctx.route.hash);
        if (ctx.route.search === next.search && ctx.route.hash === next.hash) return;
        ctx.route = next;
        if (current?.page === page) {
          route = next;
          history.replaceState({ thycaSpa: route }, '', '/');
        }
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
  async function navigate(page, parts = {}, { replace = false, force = false } = {}) {
    if (!validPage(page)) return;
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
    if (!force && previous === entry && sameRoute) return;
    const pageChanged = !previous || previous.page !== page;
    route = next;
    if (!sameRoute || replace) history[replace ? 'replaceState' : 'pushState']({ thycaSpa: next }, '', '/');
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
        entry.view.innerHTML = WORKSPACE_ART;
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
      if (active) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
    }
    document.title = `Thyca — ${entry.title}`;
    if (pageChanged) {
      const heading = entry.view.querySelector('h1, [data-page-heading]') || entry.view;
      if (!heading.hasAttribute('tabindex')) heading.tabIndex = -1;
      heading.focus({ preventScroll: true });
    }
  }
  function navigateFromState(state) {
    const next = normalize(state?.thycaSpa) || legacyRoute(location.pathname, location.search, location.hash);
    history.replaceState({ thycaSpa: next }, '', '/'); navigate(next.page, next, { replace: true });
  }
  function dispatch(action, id, ...args) {
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
  };
  addEventListener('popstate', e => navigateFromState(e.state));
  document.addEventListener('click', e => {
    const link = e.target.closest('a[href]');
    if (!link || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || link.target) return;
    const dest = new URL(link.href, location.href);
    if (dest.origin !== location.origin || !/^\/ui\/(chat|memories|profile|provider|dashboard|trace|settings)(?:\/index\.html|\/)?$/.test(dest.pathname)) return;
    e.preventDefault();
    const next = legacyRoute(dest.pathname, dest.search, dest.hash);
    navigate(next.page, dest.search || dest.hash ? next : {});
  });
  const initial = normalize(history.state?.thycaSpa) || legacyRoute(location.pathname, location.search, location.hash);
  route = initial; history.replaceState({ thycaSpa: initial }, '', '/');
  document.addEventListener('DOMContentLoaded', () => navigate(initial.page, initial, { replace: true }), { once: true });
})();
