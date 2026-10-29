// Shared backend data layer for every Thyca page.
// API: GET /api/bots, GET /api/sessions, GET /api/config,
//      GET /api/bots/<id>/dm, GET /api/bots/<id>/rooms/<session>/operations,
//      GET /api/bots/<id>/rooms/<session>/tasks (live tool/skill calls).
'use strict';
const bots = [];      // live roster from loadRoster()
const groups = [];    // group sessions from refreshSessions()
let liveSeq = 0, avatarCounter = 0, run = null;
let selectedBot = '', motionPaused = false, toastTimer = null, rosterSyncTimer = null;
const reduced = matchMedia('(prefers-reduced-motion:reduce)');

function getBot(id) { return bots.find(b => b.id === id); }

/* Favorite (star) markers are a browser-only UI preference, persisted
   in localStorage like the tint override ('thyca.tint.<id>'). The one
   exception is the system bot: Thyca is always the main bot, so its
   star is permanently on and cannot be removed. */
const FAVORITE_KEY = 'thyca.favorites';
function favoriteIds() {
  try {
    const raw = JSON.parse(localStorage.getItem(FAVORITE_KEY) || '[]');
    return new Set(Array.isArray(raw) ? raw.filter(x => typeof x === 'string') : []);
  } catch { return new Set(); }
}
function botFavorite(cardId, system) {
  return system === true || favoriteIds().has(cardId);
}
function setBotFavorite(id, favorite) {
  const ids = favoriteIds();
  if (favorite) ids.add(id); else ids.delete(id);
  try { localStorage.setItem(FAVORITE_KEY, JSON.stringify([...ids])); } catch { /* marker still applies when storage is unavailable */ }
}

async function getJson(url, options) {
  const res = await fetch(url, options);
  if (!res.ok) {
    let message = `HTTP ${res.status}`, data = null;
    try { data = await res.json(); message = data.error || message; } catch {}
    const err = new Error(message);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return res.json();
}
const postJson = (url, payload) => getJson(url, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
});

function toast(text) {
  const el = $('#toast'); if (!el) return;
  el.textContent = text; el.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { el.hidden = true; }, 3300);
}
function announce(text) { const el = $('#announcer'); if (el) el.textContent = text; }

/* Card.tint is a theme token, not a hex — map to the same hues the seal
   engine expects. A saved custom hex overrides it first. */
const TINT_HEX = {
  accent: '#a65a42',
  'accent-deep': '#8f4a35',
  botanical: '#6f7f57',
  'success-ink': '#2f6b52',
  ink: '#4a4034',
};
const TINT_ORDER = ['accent', 'botanical', 'success-ink', 'ink', 'accent-deep'];

const STATE_MAP = { idle: 'idle', thinking: 'thinking', running: 'working', waiting: 'waiting', done: 'done', error: 'error', offline: 'offline' };

function botStateFrom(card) {
  if (card.status === 'paused') return 'offline';
  return STATE_MAP[String(card.agent_status?.state || 'idle')] || 'idle';
}
function botElapsed(card) {
  const at = Date.parse(card.agent_status?.since || '');
  return Number.isFinite(at) ? Math.max(0, Math.floor((Date.now() - at) / 1000)) : 0;
}

/* A bot mid-turn in this client owns its state; roster polls only patch
   cards while no local run drives the same bot id. */
function botHasRun(id) {
  return typeof runs !== 'undefined' && [...runs.values()].some(t => t.botId === id);
}

/* Public bot card -> the runtime shape sidebar, avatars and pages share. */
function normalizeBot(card, index) {
  const hex = localStorage.getItem('thyca.tint.' + card.id)
    || TINT_HEX[card.tint]
    || TINT_HEX[TINT_ORDER[index % TINT_ORDER.length]];
  /* Browser-only shape override, mirroring the tint override above.
     DROP_NAMES is owned by avatars.js; call-time reference is safe
     because normalizeBot only runs after all scripts have loaded. */
  const shapeRaw = localStorage.getItem('thyca.shape.' + card.id);
  const shape = (typeof DROP_NAMES !== 'undefined' && DROP_NAMES.includes(shapeRaw)) ? shapeRaw : null;
  return {
    id: card.id,
    name: card.name || card.id,
    role: card.role || '',
    description: card.description || '',
    color: hex,
    tint: card.tint || 'accent',
    shape,
    palette: derivePalette(hex),
    state: botStateFrom(card),
    elapsed: botElapsed(card),
    summary: String(card.agent_status?.current_action || ''),
    capabilities: (card.skills || []).map(s => [s, '']),
    persona: String(card.persona || ''),
    memory: card.memory || null,
    tools: (card.tools || []).map(t => typeof t === 'string' ? { name: t, description: '' } : { name: t.name || '', description: t.description || '' }),
    sessions: [],
    favorite: botFavorite(card.id, card.system),
    open: false,
    phase: -1.2 - index * 1.7,
    system: card.system === true,
    model: card.model || null,
    bash: card.bash || '',
    schedule: card.schedule || null,
    mentioned: false, revision: 0, orbitAngle: 0, orbitAt: Date.now(),
    /* Live tool/skill calls observed on this client's streams:
       {key, call_id, kind, name, session, seq}. The compact preview
       derives from them; removal happens only on the corresponding
       finish (first match) or when the session's turn ends. */
    liveCalls: [],
    /* Pocket task disclosure: fetched only after an explicit chevron/+N
       open, guarded by taskGen so a late response can never paint a
       stale bot/window. taskWindow is the window the cache was read for. */
    taskWindow: null,
    taskCache: null,
    taskGen: 0,
    idle: { since: Date.now(), nextAt: Date.now() + 3400 + Math.random() * 6200 + index * 900, nextGaze: Date.now() + 1200 + index * 850, active: null, endsAt: 0, comment: '', commentUntil: 0, last: null, lastGaze: -1, recentComments: [] },
  };
}

/* Exact current chat window — the only room a pocket or preview may
   read. Null on non-chat pages (sidebar.js is shared) or when no
   session is selected, so callers can never invent a window and leak
   another room's private work. currentSession lives in the chat bundle;
   the typeof guard keeps this shared file safe where it is absent. */
function currentChatWindow() {
  try {
    if (typeof currentSession === 'undefined' || !currentSession) return null;
    const router = typeof window !== 'undefined' ? window.ThycaRouter : null;
    if (router && typeof router.currentPage === 'function' && router.currentPage() !== 'chat') return null;
    return currentSession;
  } catch { return null; }
}

/* Invocation tokens are opaque and scoped to the stream's turn, bot and
   window. Legacy events can finish only a unique legacy candidate. */
function liveCallStarted(b, opts) {
  const { kind, name, call_id, session, turn, invocation_id } = opts || {};
  if (invocation_id && turn) {
    const seen = turn.seenInvocations || (turn.seenInvocations = new Set());
    const scope = `${b.id}\0${session}\0${invocation_id}`;
    if (seen.has(scope)) return null;
    seen.add(scope);
  }
  const entry = {
    key: `${String(kind || 'tool')}:${String(call_id ?? '')}:${++liveSeq}`,
    call_id: String(call_id ?? ''),
    invocation_id: invocation_id || null,
    turn: turn || null,
    kind: String(kind || 'tool') === 'skill' ? 'skill' : 'tool',
    name: String(name || 'tool'),
    session: session || null,
    seq: liveSeq,
  };
  b.liveCalls.push(entry);
  return entry;
}
function liveCallFinished(b, call_id, opts) {
  const { kind, name, session, turn, invocation_id } = opts || {};
  const id = String(call_id ?? '');
  if (!id) return null;
  const list = b.liveCalls || [];
  const candidates = list.filter(f =>
    (session == null || f.session === session)
    && (turn == null || f.turn === turn)
    && f.call_id === id
    && f.invocation_id === (invocation_id || null)
    && (kind == null || f.kind === kind)
    && (name == null || f.name === name));
  if (candidates.length !== 1) return null;
  return list.splice(list.indexOf(candidates[0]), 1)[0];
}
function clearLiveCalls(b, session) {
  if (!b || !b.liveCalls) return;
  if (session == null) b.liveCalls.splice(0, b.liveCalls.length);
  else {
    for (let i = b.liveCalls.length - 1; i >= 0; i--) {
      if (b.liveCalls[i].session === session) b.liveCalls.splice(i, 1);
    }
  }
}
/* Live calls for exactly the current window, newest last. */
function liveCallsForWindow(b, window) {
  if (!window) return [];
  return (b.liveCalls || []).filter(f => f.session === window);
}
/* Compact preview model: concurrent calls group by actual kind+name
   (tool 'read' and skill 'read' stay distinct), newest group first.
   Shows up to two groups; `extra` counts the remaining groups and
   drives the +N button, which opens the full list separately. */
function livePreviewGroups(b, window) {
  const grouped = new Map();
  for (const f of liveCallsForWindow(b, window)) {
    const key = `${f.kind}\0${f.name}`;
    const slot = grouped.get(key);
    if (slot) { slot.active += 1; if (f.seq > slot.seq) slot.seq = f.seq; }
    else grouped.set(key, { kind: f.kind, name: f.name, active: 1, seq: f.seq });
  }
  const groups = [...grouped.values()].sort((a, c) => c.seq - a.seq);
  return { shown: groups.slice(0, 2), extra: Math.max(0, groups.length - 2), total: groups.length };
}

/* Pocket task fetch — explicit-open only. Callers (chevron/+N, stream
   events while open, window switches while open) invoke this; roster
   load, bot select and send never do. The taskGen guard drops late
   responses so a stale fetch can never paint another bot/window:
   the payload renders only when gen and window still match. Bursts
   (parallel calls, replay) coalesce: a same-window fetch already in
   flight absorbs newcomers into one bounded trailing recheck — no
   background polling, at most one extra request per burst. */
async function refreshBotTasks(b, window) {
  const target = window === undefined ? currentChatWindow() : window;
  const flight = b._taskFlight;
  if (flight && flight.window === target) {
    flight.dirty = true;   // coalesce: recheck once when the flight lands
    return flight.promise;
  }
  const gen = ++b.taskGen;
  if (!target) {
    b.taskWindow = null;
    b.taskCache = { status: 'unavailable' };
    if (typeof updateCard === 'function') { try { updateCard(b); } catch {} }
    return b.taskCache;
  }
  b.taskWindow = target;
  b.taskCache = { status: 'loading' };
  if (typeof updateCard === 'function') { try { updateCard(b); } catch {} }
  const runner = (async () => {
    try {
      const payload = await getJson(`/api/bots/${encodeURIComponent(b.id)}/rooms/${encodeURIComponent(target)}/tasks`);
      if (gen !== b.taskGen || b.taskWindow !== target) return null;  // superseded
      b.taskCache = { status: 'ok', payload };
    } catch (err) {
      if (gen !== b.taskGen || b.taskWindow !== target) return null;  // superseded
      const status = err && (err.status === 404 || err.status === 503) ? 'unavailable' : 'error';
      b.taskCache = { status, message: (err && err.message) || 'Unavailable' };
    }
    if (typeof updateCard === 'function') { try { updateCard(b); } catch {} }
    return b.taskCache;
  })();
  const slot = { window: target, dirty: false, promise: runner };
  b._taskFlight = slot;
  const out = await runner;
  if (b._taskFlight === slot) b._taskFlight = null;
  if (slot.dirty && b.open && b.taskWindow === target) {
    const now = window === undefined ? currentChatWindow() : window;
    if (now === target) return refreshBotTasks(b, target);   // one bounded recheck
  }
  return out;
}
function noteTaskStale(b) { if (b) b.taskGen++; }

async function loadRoster() {
  const payload = await getJson('/api/bots');                  // API: GET /api/bots
  bots.splice(0, bots.length, ...(payload.bots || []).map(normalizeBot));
  // Pockets stay closed until the user explicitly opens one (chevron/+N):
  // roster load, bot select and send must not disclose tasks by themselves.
  bots.forEach(b => { b.open = false; });
  if (!getBot(selectedBot) && bots[0]) selectedBot = bots[0].id;
}

/* Sessions -> compact rows per pocket + the shared group roster. Deleted
   bots are filtered out by getBot before any member row is built. */
function sessionRow(session) {
  const when = session.updated_at ? new Date(session.updated_at) : null;
  const day = when && when.toDateString() === new Date().toDateString() ? 'Hôm nay'
    : when && when.toDateString() === new Date(Date.now() - 864e5).toDateString() ? 'Hôm qua'
    : when ? when.toLocaleDateString('vi-VN', { day: 'numeric', month: 'numeric' }) : '';
  const turns = session.turns ? ` · ${session.turns} lượt` : '';
  return [session.id, session.title || 'Chưa đặt tên', day + turns];
}
async function refreshSessions() {
  const payload = await getJson('/api/sessions');              // API: GET /api/sessions
  const list = payload.sessions || [];
  groups.splice(0, groups.length, ...list.filter(s => s.kind === 'group').map(s => {
    const live = (s.bots || []).filter(id => getBot(id));
    return { id: s.id, title: s.title || 'Hội thoại chung', bots: live, subtitle: `${live.length} người bạn${s.turns ? ' · ' + s.turns + ' lượt' : ''}` };
  }));
  bots.forEach(b => {
    b.sessions = list.filter(s => s.dm === b.id || (!s.dm && (s.bots || []).includes(b.id))).map(sessionRow);
  });
}

/* Re-sync the roster without a page reload: new bots get a card, deleted
   bots drop out, card fields refresh in place. */
async function syncRoster() {
  const payload = await getJson('/api/bots');                  // API: GET /api/bots
  const incoming = payload.bots || [], ids = new Set(incoming.map(c => c.id));
  let changed = false;
  incoming.forEach((raw, i) => {
    const cur = getBot(raw.id);
    if (!cur) { bots.push(normalizeBot(raw, i)); changed = true; return; }
    const next = normalizeBot(raw, i);
    ['name', 'role', 'description', 'color', 'tint', 'shape', 'palette', 'capabilities', 'persona', 'tools', 'memory', 'system', 'model', 'bash', 'schedule'].forEach(k => { cur[k] = next[k]; });
    if (!botHasRun(cur.id)) { cur.state = next.state; cur.summary = next.summary; cur.elapsed = next.elapsed; }
  });
  for (let i = bots.length - 1; i >= 0; i--) if (!ids.has(bots[i].id)) { bots.splice(i, 1); changed = true; }
  if (!changed) { bots.forEach(b => { if (typeof updateCard === 'function') updateCard(b); }); return; }
  if (typeof renderTeam === 'function') renderTeam();
  if (typeof rebuildExpressionBots === 'function') rebuildExpressionBots();
  await refreshSessions();                                     // API: GET /api/sessions
  if (typeof renderGroups === 'function') renderGroups();
  if (!getBot(selectedBot) && bots.length) selectedBot = bots[0].id;
}
function scheduleRosterSync() {
  clearTimeout(rosterSyncTimer);
  rosterSyncTimer = setTimeout(() => syncRoster().catch(() => {}), 350);
}

/* Context meter for one bot's DM (the fixed room it uses on this page). */
async function botContext(botId) {
  const dm = await getJson(`/api/bots/${botId}/dm`);            // API: GET /api/bots/<id>/dm
  const room = dm.session_id || dm.id;
  const ops = await getJson(`/api/bots/${botId}/rooms/${room}/operations?scope=turn`);
  return ops && typeof ops === 'object' ? ops : null;
}
