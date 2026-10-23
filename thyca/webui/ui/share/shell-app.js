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
function loadSession(id, title, botId) {
  if (window.ThycaRouter) return ThycaRouter.dispatch('loadSession', id, title, botId);
  return false;
}

function openMobile() { $('#chat-sidebar').inert = false; $('#chat-sidebar').classList.add('mobile-open'); $('#mobile-overlay').hidden = false; $('.menu-button')?.setAttribute('aria-expanded', 'true'); const c = $('#team-shared-create'); if (c) c.focus(); }
function closeMobile() { const wasOpen = $('#chat-sidebar').classList.contains('mobile-open'); $('#chat-sidebar').classList.remove('mobile-open'); $('#chat-sidebar').inert = matchMedia('(max-width:850px)').matches; $('#mobile-overlay').hidden = true; $('.menu-button')?.setAttribute('aria-expanded', 'false'); if (wasOpen) $('.menu-button')?.focus(); }

function rebuildExpressionBots() {
  const sel = $('#expression-bot');
  if (sel) sel.replaceChildren(...bots.map(b => Object.assign(document.createElement('option'), { value: b.id, textContent: b.name })));
}

function toggleMotion(value) {
  motionPaused = value;
  document.body.classList.toggle('motion-paused', value);
  const btn = $('#motion-toggle');
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

  /* Mobile sidebar (menu button exists only on pages that ship one — the
     shared header no longer emits it). */
  const menuBtn = $('.menu-button');
  if (menuBtn) menuBtn.onclick = () => { if (matchMedia('(max-width:850px)').matches) { $('#chat-sidebar').classList.contains('mobile-open') ? closeMobile() : openMobile(); } else $('#team-shared-create')?.focus(); };
  $('#mobile-overlay').onclick = closeMobile;
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeMobile(); });
  $('#chat-sidebar').inert = matchMedia('(max-width:850px)').matches;

  /* Motion toggle + ambient timers. */
  $('#motion-toggle').onclick = () => toggleMotion(!motionPaused);
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

  /* Sidebar width splitter (drag + arrow keys). */
  const separator = $('.splitter'); let dragging = false;
  function resizeSide(value) { const max = Math.min(470, innerWidth - 540); const w = Math.round(Math.max(310, Math.min(max, value))); document.documentElement.style.setProperty('--sidebar-width', w + 'px'); separator.setAttribute('aria-valuenow', String(w)); return w; }
  separator.onpointerdown = e => { dragging = true; separator.setPointerCapture(e.pointerId); document.body.classList.add('resizing'); };
  separator.onpointermove = e => { if (dragging) resizeSide(e.clientX - $('.main-grid').getBoundingClientRect().left); };
  separator.onpointerup = () => { dragging = false; document.body.classList.remove('resizing'); };
  separator.onlostpointercapture = () => { dragging = false; document.body.classList.remove('resizing'); };
  separator.onkeydown = e => { if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { e.preventDefault(); resizeSide($('#chat-sidebar').getBoundingClientRect().width + (e.key === 'ArrowLeft' ? -16 : 16)); } };
  addEventListener('resize', () => { if (innerWidth > 850) { closeMobile(); document.documentElement.style.removeProperty('--sidebar-width'); } separator.setAttribute('aria-valuenow', Math.round($('#chat-sidebar').getBoundingClientRect().width)); });

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
      if (typeof updateComposer === 'function') updateComposer();
      if ($('#expressions-dialog').open) buildExpressions();
      return true;
    },
    getState() { return bots.map(b => ({ id: b.id, state: b.state, expression: expressionFor(b), color: b.color, open: b.open, elapsed: b.elapsed, idle: { activity: b.idle.active, comment: b.idle.comment } })); },
    getGroupActivity() { return activeGroup ? { groupId: activeGroup.id, speaker: activeGroup.speaker, startedAt: activeGroup.start } : null; },
  };
}

initShellApp();
