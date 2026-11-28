'use strict';

function selectBot(id) {
  if (window.ThycaRouter) return ThycaRouter.dispatch('selectBot', id);
  return false;
}
function loadConversation(id, botId) {
  if (window.ThycaRouter) return ThycaRouter.dispatch('loadConversation', id, botId);
  return false;
}

function setupLocked() {
  const router = window.ThycaRouter;
  return typeof router?.setupState === 'function' && router.setupState() !== 'ready';
}
function syncSidebarChrome() {
  const sidebar = $('#chat-sidebar'), navSidebar = $('#nav-sidebar'), shell = $('.notebook-shell');
  const locked = setupLocked();
  const narrow = matchMedia('(max-width:850px)').matches;
  const navCollapsed = !narrow && shell?.dataset.navCollapsed === 'true';
  const collapsed = !narrow && shell?.dataset.sidebarCollapsed === 'true';
  const navOpen = !locked && (narrow ? Boolean(navSidebar?.classList.contains('mobile-open')) : true);
  const open = !locked && (narrow ? Boolean(sidebar?.classList.contains('mobile-open')) : !collapsed);
  if (shell) shell.dataset.mobileDrawerOpen = String(narrow && (open || navOpen));
  const navBtn = $('#nav-toggle');
  if (navBtn) {
    navBtn.disabled = locked;
    const navLabel = narrow
      ? (navOpen ? 'Đóng điều hướng' : 'Mở điều hướng')
      : (navCollapsed ? 'Mở rộng điều hướng' : 'Thu gọn điều hướng');
    navBtn.setAttribute('aria-expanded', String(narrow ? navOpen : (!locked && !navCollapsed)));
    navBtn.setAttribute('aria-label', navLabel);
    navBtn.title = navLabel;
    navBtn.dataset.collapsed = String(navCollapsed);
  }
  if (navSidebar) navSidebar.inert = locked || (narrow && !navOpen);
  const menuBtn = $('.menu-button');
  if (menuBtn) {
    menuBtn.disabled = locked;
    const label = narrow
      ? (open ? 'Đóng thanh bot' : 'Mở thanh bot')
      : (collapsed ? 'Mở rộng thanh bot' : 'Thu gọn thanh bot');
    menuBtn.setAttribute('aria-expanded', String(open));
    menuBtn.setAttribute('aria-label', label);
    menuBtn.title = label;
    menuBtn.dataset.collapsed = String(collapsed);
  }
  if (!sidebar) return;
  sidebar.inert = locked || !open;
  const railVisible = !locked && (narrow ? (!open && !navOpen) : collapsed);
  const railWrap = $('#header-rail-wrap');
  if (railWrap) railWrap.setAttribute('aria-hidden', String(!railVisible));
  const railList = $('#rail-avatar-list');
  if (railList) railList.inert = !railVisible;
  $('.menu-button')?.setAttribute('aria-expanded', String(open));
}
function openNavMobile() {
  if (setupLocked()) return;
  const navSidebar = $('#nav-sidebar');
  if (!navSidebar) return;
  const sidebar = $('#chat-sidebar');
  if (sidebar?.classList.contains('mobile-open')) sidebar.classList.remove('mobile-open');
  navSidebar.inert = false;
  navSidebar.classList.add('mobile-open');
  $('#mobile-overlay').hidden = false;
  syncSidebarChrome();
  const cur = $('#nav-sidebar .page-links a.is-current') || $('#nav-sidebar .page-links a');
  cur?.focus?.();
}
function closeNavMobile(restoreFocus = true) {
  const navSidebar = $('#nav-sidebar');
  const wasOpen = Boolean(navSidebar?.classList.contains('mobile-open'));
  navSidebar?.classList.remove('mobile-open');
  if (!$('#chat-sidebar')?.classList.contains('mobile-open')) $('#mobile-overlay').hidden = true;
  syncSidebarChrome();
  if (restoreFocus && wasOpen && matchMedia('(max-width:850px)').matches) $('#nav-toggle')?.focus();
}
function openMobile() {
  if (setupLocked()) return;
  const navSidebar = $('#nav-sidebar');
  if (navSidebar?.classList.contains('mobile-open')) navSidebar.classList.remove('mobile-open');
  $('#chat-sidebar').inert = false;
  $('#chat-sidebar').classList.add('mobile-open');
  $('#mobile-overlay').hidden = false;
  $('.menu-button')?.setAttribute('aria-expanded', 'true');
  syncSidebarChrome();
  const c = $('#team-shared-create');
  const onChat = !document.body?.dataset?.activePage || document.body.dataset.activePage === 'chat';
  if (c && onChat) c.focus();
  else $$('#chat-sidebar .bot-select')[0]?.focus?.();
}
function closeMobile() {
  const wasSide = $('#chat-sidebar')?.classList.contains('mobile-open');
  const wasNav = $('#nav-sidebar')?.classList.contains('mobile-open');
  $('#chat-sidebar')?.classList.remove('mobile-open');
  $('#nav-sidebar')?.classList.remove('mobile-open');
  $('#mobile-overlay').hidden = true;
  $('.menu-button')?.setAttribute('aria-expanded', 'false');
  syncSidebarChrome();
  if (matchMedia('(max-width:850px)').matches) {
    if (wasSide) $('.menu-button')?.focus();
    else if (wasNav) $('#nav-toggle')?.focus();
  }
}
function rebuildExpressionBots() {
  const sel = $('#expression-bot');
  if (sel) sel.replaceChildren(...bots.map(b => Object.assign(document.createElement('option'), { value: b.id, textContent: b.name })));
}

function toggleMotion(value) {
  motionPaused = value;
  document.body.classList.toggle('motion-paused', value);
  const btn = $('#motion-toggle');
  if (!btn) return;
  btn.setAttribute('aria-pressed', String(value));
  btn.setAttribute('aria-label', value ? 'Tiếp tục chuyển động' : 'Tạm dừng chuyển động');
  btn.title = value ? 'Tiếp tục chuyển động' : 'Tạm dừng chuyển động';
  btn.innerHTML = icon(value ? 'play' : 'pause');
}

function handleEvent(id, type, payload = {}) {
  const b = getBot(id); if (!b) return false;
  if (b.state === 'offline' && type !== 'connection.restored') return false;
  if (b.state === 'error' && ['llm.started', 'llm.thinking', 'llm.content', 'tool.started', 'turn.completed'].includes(type)) return false;
  if (type === 'bot.mentioned') return mentionBot(id);
  if (type === 'group.speaker') return setGroupActivity(payload.groupId, id);
  const map = { 'turn.accepted': 'waiting', 'input.waiting': 'waiting', 'llm.started': 'thinking', 'llm.thinking': 'thinking', 'tool.started': 'working', 'skill.started': 'working', 'llm.content': 'writing', 'turn.completed': 'done', 'turn.failed': 'error', 'turn.cancelled': 'idle', 'connection.lost': 'offline', 'connection.restored': 'idle', 'input.required': 'confused' };
  const state = map[type]; if (!state) return false;
  if (type === 'llm.content') { if (b.state !== 'writing' && !setState(id, 'writing')) return false; return notifyText(id, payload.delta || payload.text || ''); }
  return setState(id, state, payload.summary, payload.elapsed);
}

function initShellApp() {
  $$('.dialog-close').forEach(btn => btn.onclick = () => btn.closest('dialog').close());
  $$('dialog').forEach(d => d.addEventListener('click', e => { if (e.target === d) { const r = d.getBoundingClientRect(); if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) d.close(); } }));

  const sidebarBand = () => matchMedia('(max-width:850px)').matches
    ? 'narrow'
    : matchMedia('(max-width:1024px)').matches
      ? 'medium'
      : matchMedia('(max-width:1240px)').matches
        ? 'compact'
        : 'wide';
  let currentBand = sidebarBand();
  const shellEl = $('.notebook-shell');
  if (shellEl) {
    if (currentBand === 'medium') {
      shellEl.dataset.navCollapsed = 'true';
      shellEl.dataset.sidebarCollapsed = 'true';
    } else if (currentBand === 'compact') {
      shellEl.dataset.navCollapsed = 'true';
      if (!shellEl.dataset.sidebarCollapsed) shellEl.dataset.sidebarCollapsed = 'false';
    } else if (currentBand === 'wide') {
      if (!shellEl.dataset.navCollapsed) shellEl.dataset.navCollapsed = 'false';
      if (!shellEl.dataset.sidebarCollapsed) shellEl.dataset.sidebarCollapsed = 'false';
    }
  }

  const navBtn = $('#nav-toggle');
  if (navBtn) {
    navBtn.onclick = () => {
      if (setupLocked()) return;
      if (isNarrow()) {
        $('#nav-sidebar')?.classList.contains('mobile-open') ? closeNavMobile() : openNavMobile();
      } else {
        setNavMode(shellEl?.dataset.navCollapsed !== 'true');
      }
    };
  }
  const menuBtn = $('.menu-button');
  if (menuBtn) {
    menuBtn.onclick = () => {
      if (setupLocked()) return;
      if (isNarrow()) {
        $('#chat-sidebar').classList.contains('mobile-open') ? closeMobile() : openMobile();
      } else {
        setSidebarMode(shellEl?.dataset.sidebarCollapsed !== 'true');
      }
    };
  }
  const teamNavTitle = $('#team-nav-title');
  if (teamNavTitle) {
    const clearTeamFilter = () => {
      if (setupLocked()) return;
      const page = window.ThycaRouter?.currentPage?.() || '';
      if (!['dashboard', 'memories', 'profile'].includes(page)) return;
      if (matchMedia('(max-width:850px)').matches && $('#chat-sidebar')?.classList.contains('mobile-open')) closeMobile();
      window.ThycaRouter.dispatch('selectBot', '');
    };
    teamNavTitle.onclick = clearTeamFilter;
    teamNavTitle.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); clearTeamFilter(); }
    });
  }
  $('#mobile-overlay').onclick = () => closeMobile();
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeMobile(); });
  syncSidebarChrome();

  const themeBtn = $('#theme-toggle');
  if (themeBtn) {
    themeBtn.onclick = () => {
      const dark = document.documentElement?.dataset?.theme === 'dark';
      if (typeof setPrefs === 'function') setPrefs({ theme: dark ? 'light' : 'dark' });
    };
  }
  window.ThycaThemeSync = () => {
    if (typeof refreshAllBotPalettes === 'function') refreshAllBotPalettes();
    if (typeof renderTeam === 'function') renderTeam();
    if ($('#expressions-dialog')?.open && typeof buildExpressions === 'function') buildExpressions();
    if (typeof registerLiveRigs === 'function') registerLiveRigs();
    if (typeof wakeEyeLoop === 'function') wakeEyeLoop();
  };

  if ($('#motion-toggle')) $('#motion-toggle').onclick = () => toggleMotion(!motionPaused);
  if (reduced.matches) toggleMotion(true);

  const nextBlink = new WeakMap();
  setInterval(() => {
    if (motionPaused || document.hidden || reduced.matches || window.ThycaIsShy) return; const now = Date.now();
    $$('.crew-avatar:not(.expression-preview) .char-box').forEach(box => {
      const a = box.closest('.crew-avatar');
      if (!a || ['offline', 'done', 'error'].includes(a.dataset.state) || a.dataset.expression === 'mention' || ['hum', 'stretch', 'wink', 'sleepy'].includes(a.dataset.idle)) return;
      if (!nextBlink.has(box)) nextBlink.set(box, now + 1500 + Math.random() * 5000); if (now < nextBlink.get(box)) return;
      nextBlink.set(box, now + 2600 + Math.random() * 6000); box.classList.add('blink'); setTimeout(() => box.classList.remove('blink'), 175);
    });
  }, 300);

  if (typeof document !== 'undefined' && document.addEventListener) {
    document.addEventListener('pointermove', ev => {
      window.ThycaPointer = { clientX: ev.clientX, clientY: ev.clientY };
      if (typeof wakeEyeLoop === 'function') wakeEyeLoop();
    });
    $('#team-bot-list')?.addEventListener('pointerover', ev => {
      const el = ev.target?.closest?.('.bot-card');
      const next = el ? (el.dataset.botId || el.dataset.group || null) : null;
      if (window.ThycaHoverCardId !== next) {
        window.ThycaHoverCardId = next;
        if (typeof wakeEyeLoop === 'function') wakeEyeLoop();
      }
    });
    $('#team-bot-list')?.addEventListener('pointerleave', () => {
      if (window.ThycaHoverCardId !== null) {
        window.ThycaHoverCardId = null;
        if (typeof wakeEyeLoop === 'function') wakeEyeLoop();
      }
    });
    $('#team-bot-list')?.addEventListener('scroll', () => { if (typeof markCtmDirty === 'function') markCtmDirty(); }, { passive: true });
    window.addEventListener?.('resize', () => { if (typeof markCtmDirty === 'function') markCtmDirty(); });
  }
  if (typeof registerLiveRigs === 'function') registerLiveRigs();
  if (typeof wakeEyeLoop === 'function') wakeEyeLoop();

  setInterval(idleTick, 400);
  document.addEventListener('visibilitychange', () => { bots.forEach(b => { clearIdle(b, true); b.idle.nextAt = Date.now() + between(4000, 9000); }); });

  const NAV_FULL_VAR = 'var(--nav-full)';
  const NAV_RAIL_VAR = 'var(--nav-rail)';
  const FULL_WIDTH_VAR = 'var(--sidebar-full)';
  const COLLAPSED_WIDTH = '0px';
  const isNarrow = () => matchMedia('(max-width:850px)').matches;
  function setNavMode(collapsed) {
    if (!shellEl) return;
    if (isNarrow()) {
      shellEl.dataset.navCollapsed = 'false';
      document.documentElement.style.removeProperty('--nav-width');
      syncSidebarChrome();
      return;
    }
    shellEl.dataset.navCollapsed = collapsed ? 'true' : 'false';
    document.documentElement.style.setProperty('--nav-width', collapsed ? NAV_RAIL_VAR : NAV_FULL_VAR);
    syncSidebarChrome();
  }
  function setSidebarMode(collapsed) {
    if (!shellEl) return;
    if (isNarrow()) {
      shellEl.dataset.sidebarCollapsed = 'false';
      document.documentElement.style.removeProperty('--sidebar-width');
      syncSidebarChrome();
      return;
    }
    shellEl.dataset.sidebarCollapsed = collapsed ? 'true' : 'false';
    document.documentElement.style.setProperty('--sidebar-width', collapsed ? COLLAPSED_WIDTH : FULL_WIDTH_VAR);
    syncSidebarChrome();
  }
  function syncSidebarModeFromState() {
    if (!shellEl) return;
    setNavMode(shellEl.dataset.navCollapsed === 'true');
    setSidebarMode(shellEl.dataset.sidebarCollapsed === 'true');
  }
  syncSidebarModeFromState();
  addEventListener('resize', () => {
    const nextBand = sidebarBand();
    if (nextBand !== currentBand) {
      if (shellEl) {
        if (nextBand === 'medium') {
          shellEl.dataset.navCollapsed = 'true';
          shellEl.dataset.sidebarCollapsed = 'true';
        } else if (nextBand === 'compact') {
          shellEl.dataset.navCollapsed = 'true';
          shellEl.dataset.sidebarCollapsed = 'false';
        } else {
          shellEl.dataset.navCollapsed = 'false';
          shellEl.dataset.sidebarCollapsed = 'false';
        }
      }
      currentBand = nextBand;
    }
    if (!isNarrow()) closeMobile();
    syncSidebarModeFromState();
  });

  document.addEventListener('visibilitychange', () => { if (!document.hidden) scheduleRosterSync(); });
  addEventListener('focus', () => scheduleRosterSync());
  setInterval(() => scheduleRosterSync(), 45000);

  window.ThycaShell = {
    setState, mention: mentionBot, notifyText, handleEvent, setGroupActivity, setPeerExchange,
    playIdle: (id, key = null) => beginIdle(id, key, true), setIdleOptions, getIdleOptions: () => ({ ...idleOptions }),
    setTheme(mode) {
      const next = mode === 'dark' ? 'dark' : 'light';
      if (typeof setPrefs === 'function') setPrefs({ theme: next });
      return next;
    },
    getTheme() { return document.documentElement?.dataset?.theme === 'dark' ? 'dark' : 'light'; },
    setBotColor(id, color) {
      if (!/^#[a-f0-9]{6}$/i.test(color) || !getBot(id)) return false;
      const b = getBot(id);
      const dark = typeof isDarkMode === 'function' ? isDarkMode() : false;
      b.color = color;
      b.palette = derivePalette(color, dark, b.tint);
      localStorage.setItem('thyca.tint.' + id, color);
      paint($('#bot-' + id), b);
      $$('[data-bot-avatar="' + id + '"]').forEach(el => paint(el, b));
      if (typeof renderHeaderBots === 'function') renderHeaderBots();
      if (typeof updateComposer === 'function') updateComposer();
      if ($('#expressions-dialog')?.open) buildExpressions();
      return true;
    },
    setBotShape(id, shape) {
      if (typeof CREW_IDS === 'undefined' || !CREW_IDS.includes(shape) || !getBot(id)) return false;
      const b = getBot(id);
      b.shape = shape;
      localStorage.setItem('thyca.shape.' + id, shape);
      $$('.crew-avatar[data-bot-avatar="' + id + '"]').forEach(el => syncAvatar(el, b));
      if (typeof renderHeaderBots === 'function') renderHeaderBots();
      if (typeof syncGroupAvatars === 'function') syncGroupAvatars();
      if ($('#expressions-dialog')?.open) buildExpressions();
      return true;
    },
    setBotTheme(id, theme) {
      if (typeof THEME_IDS === 'undefined' || !THEME_IDS.includes(theme) || !getBot(id)) return false;
      const b = getBot(id);
      b.theme = theme;
      localStorage.setItem('thyca.theme.' + id, theme);
      const stage = $(`#bot-${id} .card-stage`);
      if (stage) stage.dataset.botTheme = theme;
      $$('.crew-avatar[data-bot-avatar="' + id + '"]').forEach(el => syncAvatar(el, b));
      if ($('#expressions-dialog')?.open) buildExpressions();
      return true;
    },
    setBotAccessory(id, accessory) {
      if (typeof ACCESSORY_IDS === 'undefined' || !ACCESSORY_IDS.includes(accessory) || !getBot(id)) return false;
      const b = getBot(id);
      b.accessory = accessory;
      localStorage.setItem('thyca.acc.' + id, accessory);
      $$('.crew-avatar[data-bot-avatar="' + id + '"]').forEach(el => syncAvatar(el, b));
      if (typeof syncGroupAvatars === 'function') syncGroupAvatars();
      if ($('#expressions-dialog')?.open) buildExpressions();
      return true;
    },
    getPeerExchange() { return window.ThycaPeerExchange ? { ...window.ThycaPeerExchange } : null; },
    getState() { return bots.map(b => ({ id: b.id, state: b.state, expression: expressionFor(b), color: b.color, shape: crewShapeFor(b).id, theme: themeFor(b), accessory: accessoryFor(b), open: b.open, elapsed: b.elapsed, idle: { activity: b.idle?.active || null, comment: b.idle?.comment || '' } })); },
    getGroupActivity() { return activeGroup ? { groupId: activeGroup.id, speaker: activeGroup.speaker, startedAt: activeGroup.start } : null; },
  };
}

initShellApp();
