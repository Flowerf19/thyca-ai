// Shared shell wiring for every Thyca page: mobile menu, splitter, dialogs,
// motion toggle, blink/idle timers, roster sync. Load AFTER sidebar.js and
// studio.js (which bind DOM at load), BEFORE the page's own script.
//
// The shared sidebar dispatches to the active page controller. The defaults
// below are only compatibility fallbacks for harnesses without the router.
'use strict';

/* The persistent sidebar dispatches to the active screen controller. */
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
function closePageMenu() {
  const btn = $('#page-menu-toggle');
  const wrap = btn?.closest?.('.header-right') || $('.header-right');
  wrap?.classList?.remove('page-menu-open');
  btn?.setAttribute('aria-expanded', 'false');
}
function syncSidebarChrome() {
  const sidebar = $('#chat-sidebar'), shell = $('.notebook-shell');
  const locked = setupLocked();
  const pageMenuBtn = $('#page-menu-toggle');
  if (pageMenuBtn) {
    pageMenuBtn.disabled = locked;
    if (locked) closePageMenu();
  }
  if (!sidebar) return;
  const narrow = matchMedia('(max-width:850px)').matches;
  const collapsed = !narrow && shell?.dataset.sidebarCollapsed === 'true';
  const open = !locked && (narrow ? sidebar.classList.contains('mobile-open') : !collapsed);
  /* Desktop collapsed renders the avatar rail (74px) which stays interactive;
     only narrow closed drawer and the setup lock keep the sidebar inert. */
  sidebar.inert = locked || (narrow && !open);
  const railList = $('#rail-avatar-list');
  if (railList) railList.inert = locked;
  $('.menu-button')?.setAttribute('aria-expanded', String(narrow && open));
}
function openMobile() {
  if (setupLocked()) return;
  $('#chat-sidebar').inert = false;
  $('#chat-sidebar').classList.add('mobile-open');
  $('#mobile-overlay').hidden = false;
  $('.menu-button')?.setAttribute('aria-expanded', 'true');
  const c = $('#team-shared-create');
  const onChat = !document.body?.dataset?.activePage || document.body.dataset.activePage === 'chat';
  if (c && onChat) c.focus();
  else ($$('#chat-sidebar .bot-select')[0] || $('#open-settings'))?.focus?.();
}
function closeMobile() {
  const wasOpen = $('#chat-sidebar').classList.contains('mobile-open');
  $('#chat-sidebar').classList.remove('mobile-open');
  $('#mobile-overlay').hidden = true;
  $('.menu-button')?.setAttribute('aria-expanded', 'false');
  syncSidebarChrome();
  if (wasOpen && matchMedia('(max-width:850px)').matches) $('.menu-button')?.focus();
}
function rebuildExpressionBots() {
  const sel = $('#expression-bot');
  if (sel) sel.replaceChildren(...bots.map(b => Object.assign(document.createElement('option'), { value: b.id, textContent: b.name })));
}

function toggleMotion(value) {
  motionPaused = value;
  document.body.classList.toggle('motion-paused', value);
  // The footer motion button is gone (its job moved to the reduce-motion
  // preference in the Settings dialog); guard for markup that still has it.
  const btn = $('#motion-toggle');
  if (!btn) return;
  btn.setAttribute('aria-pressed', String(value));
  btn.setAttribute('aria-label', value ? 'Tiếp tục chuyển động' : 'Tạm dừng chuyển động');
  btn.title = value ? 'Tiếp tục chuyển động' : 'Tạm dừng chuyển động';
  btn.innerHTML = icon(value ? 'play' : 'pause');
}

/* Stream/lifecycle events → shared bot-card states. The chat NDJSON reader
   calls handleStreamEvent (a different, turn-scoped mapping); this surface is
   for any page that wants to drive states externally. */
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
  /* Dialogs — shared close button + backdrop click. */
  $$('.dialog-close').forEach(btn => btn.onclick = () => btn.closest('dialog').close());
  $$('dialog').forEach(d => d.addEventListener('click', e => { if (e.target === d) { const r = d.getBoundingClientRect(); if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) d.close(); } }));

  /* Sidebar toggle: .menu-button (narrow only). Wordmark is static. */
  const sidebarBand = () => matchMedia('(max-width:850px)').matches ? 'narrow' : matchMedia('(max-width:1150px)').matches ? 'medium' : 'wide';
  let currentBand = sidebarBand();
  const shellEl = $('.notebook-shell');
  if (shellEl && currentBand === 'medium') shellEl.dataset.sidebarCollapsed = 'true';
  else if (shellEl && currentBand === 'wide' && !shellEl.dataset.sidebarCollapsed) shellEl.dataset.sidebarCollapsed = 'false';

  const menuBtn = $('.menu-button');
  if (menuBtn) menuBtn.onclick = () => { if (matchMedia('(max-width:850px)').matches) { $('#chat-sidebar').classList.contains('mobile-open') ? closeMobile() : openMobile(); } else $('#team-shared-create')?.focus(); };
  const pageMenuBtn = $('#page-menu-toggle');
  if (pageMenuBtn) {
    pageMenuBtn.onclick = e => {
      e?.stopPropagation?.();
      if (setupLocked()) return;
      const wrap = pageMenuBtn.closest?.('.header-right') || $('.header-right');
      if (!wrap) return;
      const open = wrap.classList.toggle('page-menu-open');
      pageMenuBtn.setAttribute('aria-expanded', String(open));
    };
    document.addEventListener('click', e => {
      const wrap = pageMenuBtn.closest?.('.header-right') || $('.header-right');
      if (!wrap?.classList?.contains('page-menu-open')) return;
      if (e?.target && wrap.contains?.(e.target)) {
        if (e.target.closest?.('[data-page-link]')) closePageMenu();
        return;
      }
      closePageMenu();
    });
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
  document.addEventListener('keydown', e => { if (e.key === 'Escape') { closeMobile(); closePageMenu(); } });
  syncSidebarChrome();

  /* Motion toggle + ambient timers. */
  /* The footer motion button was removed in favor of the reduce-motion
     preference; keep the wiring if markup still carries it. */
  if ($('#motion-toggle')) $('#motion-toggle').onclick = () => toggleMotion(!motionPaused);
  if (reduced.matches) toggleMotion(true);

  const nextBlink = new WeakMap();
  setInterval(() => {
    if (motionPaused || document.hidden || reduced.matches) return; const now = Date.now();
    $$('.seal-avatar:not(.expression-preview)').forEach(a => {
      if (['offline', 'done', 'error'].includes(a.dataset.state) || a.dataset.expression === 'mention' || ['hum', 'stretch', 'wink', 'sleepy'].includes(a.dataset.idle)) return;
      if (!nextBlink.has(a)) nextBlink.set(a, now + 1500 + Math.random() * 5000); if (now < nextBlink.get(a)) return;
      nextBlink.set(a, now + 2600 + Math.random() * 6000); a.classList.add('blink'); setTimeout(() => a.classList.remove('blink'), 220);
    });
  }, 300);

  setInterval(idleTick, 400);
  document.addEventListener('visibilitychange', () => { bots.forEach(b => { clearIdle(b, true); b.idle.nextAt = Date.now() + between(4000, 9000); }); });

  /* Sidebar has two fixed modes only: full 125px and rail 74px.
     The .splitter button at the sidebar edge is the collapse toggle
     (click only, no drag). The vertical drag/scroll bars are separate
     and stay hidden while scrolling remains available. */
  const FULL_WIDTH = 125;
  const FULL_WIDTH_DESKTOP = 175;
  const RAIL_WIDTH = 74;
  const separator = $('.splitter');
  const isNarrow = () => matchMedia('(max-width:850px)').matches;
  function setSidebarMode(collapsed) {
    if (!shellEl) return;
    shellEl.dataset.sidebarCollapsed = collapsed ? 'true' : 'false';
    const fullWidth = isNarrow() ? FULL_WIDTH : FULL_WIDTH_DESKTOP;
    document.documentElement.style.setProperty('--sidebar-width', (collapsed ? RAIL_WIDTH : fullWidth) + 'px');
    separator.setAttribute('aria-expanded', String(!collapsed));
    separator.setAttribute('aria-label', collapsed ? 'Mở rộng thanh bot' : 'Thu gọn thanh bot');
    separator.textContent = collapsed ? '›' : '‹';
    syncSidebarChrome();
  }
  function syncSidebarModeFromState() {
    if (!shellEl) return;
    setSidebarMode(shellEl.dataset.sidebarCollapsed === 'true');
  }
  separator.onclick = () => {
    if (setupLocked()) return;
    if (isNarrow() && !$('#chat-sidebar')?.classList.contains('mobile-open')) openMobile();
    setSidebarMode(shellEl?.dataset.sidebarCollapsed !== 'true');
  };
  syncSidebarModeFromState();
  addEventListener('resize', () => {
    const nextBand = sidebarBand();
    if (nextBand !== currentBand) {
      if (shellEl) {
        if (nextBand === 'medium') shellEl.dataset.sidebarCollapsed = 'true';
        else if (nextBand === 'wide') shellEl.dataset.sidebarCollapsed = 'false';
        /* narrow keeps the current fixed mode */
      }
      currentBand = nextBand;
    }
    if (innerWidth > 950) closePageMenu();
    if (!isNarrow()) closeMobile();
    syncSidebarModeFromState();
  });

  /* Bots created/deleted outside this page (other client, CLI, a turn in
     another tab) don't reach any stream — refocus + a slow poll catch them. */
  document.addEventListener('visibilitychange', () => { if (!document.hidden) scheduleRosterSync(); });
  addEventListener('focus', () => scheduleRosterSync());
  setInterval(() => scheduleRosterSync(), 45000);

  /* Public adapter shared by all pages (chat adds sendTurn/stop itself). */
  window.ThycaShell = {
    setState, mention: mentionBot, notifyText, handleEvent, setGroupActivity,
    playIdle: (id, key = null) => beginIdle(id, key, true), setIdleOptions, getIdleOptions: () => ({ ...idleOptions }),
    setBotColor(id, color) {
      if (!/^#[a-f0-9]{6}$/i.test(color) || !getBot(id)) return false;
      const b = getBot(id); b.color = color; b.palette = derivePalette(color); localStorage.setItem('thyca.tint.' + id, color);
      paint($('#bot-' + id), b); $$('[data-bot-avatar="' + id + '"]').forEach(el => paint(el, b));
      if (typeof renderHeaderBots === 'function') renderHeaderBots();
      if (typeof updateComposer === 'function') updateComposer();
      if ($('#expressions-dialog').open) buildExpressions();
      return true;
    },
    /* Browser-only shape override, mirroring setBotColor. Patches every
       existing avatar instance in place: updates data-drop-shape and re-renders
       the medallion character markup inside .base while preserving className and state. */
    setBotShape(id, shape) {
      if (typeof DROP_NAMES === 'undefined' || !DROP_NAMES.includes(shape) || !getBot(id)) return false;
      const b = getBot(id); b.shape = shape; localStorage.setItem('thyca.shape.' + id, shape);
      $$('[data-bot-avatar="' + id + '"]').forEach(el => {
        el.setAttribute('data-drop-shape', shape);
        const uid = 'drop' + (++avatarCounter);
        el.innerHTML = `<span class="seal-motion">${dropMarkup(b, uid)}</span>`;
      });
      if (typeof renderHeaderBots === 'function') renderHeaderBots();
      return true;
    },
    getState() { return bots.map(b => ({ id: b.id, state: b.state, expression: expressionFor(b), color: b.color, open: b.open, elapsed: b.elapsed, idle: { activity: b.idle.active, comment: b.idle.comment } })); },
    getGroupActivity() { return activeGroup ? { groupId: activeGroup.id, speaker: activeGroup.speaker, startedAt: activeGroup.start } : null; },
  };
}

initShellApp();
