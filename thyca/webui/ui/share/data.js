// Shared backend data layer for every Thyca page.
// API: GET /api/bots, GET /api/sessions, GET /api/config,
//      GET /api/bots/<id>/dm, GET /api/bots/<id>/rooms/<session>/operations.
'use strict';
const bots = [];      // live roster from loadRoster()
const groups = [];    // group sessions from refreshSessions()
let feedSeq = 0, avatarCounter = 0, run = null;
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
    let message = `HTTP ${res.status}`;
    try { message = (await res.json()).error || message; } catch {}
    const err = new Error(message);
    err.status = res.status;
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
    feed: [],
    idle: { since: Date.now(), nextAt: Date.now() + 3400 + Math.random() * 6200 + index * 900, nextGaze: Date.now() + 1200 + index * 850, active: null, endsAt: 0, comment: '', commentUntil: 0, last: null, lastGaze: -1, recentComments: [] },
  };
}

async function loadRoster() {
  const payload = await getJson('/api/bots');                  // API: GET /api/bots
  bots.splice(0, bots.length, ...(payload.bots || []).map(normalizeBot));
  bots.forEach((b, i) => { b.open = i === 0; });
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
