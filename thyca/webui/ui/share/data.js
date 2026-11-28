'use strict';
const bots = [];
const groups = [];
let liveSeq = 0, avatarCounter = 0, run = null;
let selectedBot = '', motionPaused = false, toastTimer = null, rosterSyncTimer = null;
const reduced = matchMedia('(prefers-reduced-motion:reduce)');

function getBot(id) { return bots.find(b => b.id === id); }

function dmKey(botId) { return 'dm:' + botId; }
function groupKey(groupId) { return 'group:' + groupId; }
function convParts(key) {
  const text = String(key || '');
  if (text.startsWith('dm:') && text.length > 3) return { kind: 'dm', bot_id: text.slice(3) };
  if (text.startsWith('group:') && text.length > 6) return { kind: 'group', group_id: text.slice(6) };
  return null;
}
function convDetailUrl(key) {
  const parts = convParts(key);
  if (!parts) return null;
  return parts.kind === 'dm'
    ? `/api/bots/${encodeURIComponent(parts.bot_id)}/dm`
    : `/api/groups/${encodeURIComponent(parts.group_id)}`;
}
function convDoorUrl(key, suffix) {
  const parts = convParts(key);
  if (!parts) return null;
  const tail = suffix ? '/' + suffix : '';
  return parts.kind === 'dm'
    ? `/api/bots/${encodeURIComponent(parts.bot_id)}/dm${tail}`
    : `/api/groups/${encodeURIComponent(parts.group_id)}${tail}`;
}

const FAVORITE_KEY = 'thyca.favorites';
const GROUP_FAVORITE_KEY = 'thyca.groupFavorites';
function favoriteIdsFor(key) {
  try {
    const raw = JSON.parse(localStorage.getItem(key) || '[]');
    return new Set(Array.isArray(raw) ? raw.filter(x => typeof x === 'string') : []);
  } catch { return new Set(); }
}
function setFavoriteFor(key, id, favorite) {
  const ids = favoriteIdsFor(key);
  if (favorite) ids.add(id); else ids.delete(id);
  try { localStorage.setItem(key, JSON.stringify([...ids])); } catch {  }
}
function favoriteIds() { return favoriteIdsFor(FAVORITE_KEY); }
function botFavorite(cardId, system) {
  return system === true || favoriteIds().has(cardId);
}
function setBotFavorite(id, favorite) { setFavoriteFor(FAVORITE_KEY, id, favorite); }
function groupFavoriteIds() { return favoriteIdsFor(GROUP_FAVORITE_KEY); }
function setGroupFavorite(id, favorite) { setFavoriteFor(GROUP_FAVORITE_KEY, id, favorite); }

function groupTitleOf(title) {
  return typeof title === 'string' && title.trim() ? title.trim() : 'Hội thoại chung';
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

const TINT_HEX = {
  accent: '#a65a42',
  'accent-deep': '#8f4a35',
  botanical: '#6f7f57',
  'success-ink': '#2f6b52',
  ink: '#4a4034',
};
const TINT_ORDER = ['accent', 'botanical', 'success-ink', 'ink', 'accent-deep'];

const STATE_MAP = { idle: 'idle', thinking: 'thinking', running: 'working', waiting: 'waiting', done: 'done', error: 'error', offline: 'offline' };

function isDarkThemeActive() { return typeof document !== 'undefined' && document.documentElement?.dataset?.theme === 'dark'; }
function botStateFrom(card) {
  if (card.status === 'paused') return 'offline';
  const st = String(card.agent_status?.state || 'idle');
  const kind = String(card.agent_status?.waiting_on?.kind || card.agent_status?.phase || '').toLowerCase();
  if (st === 'waiting' && (kind === 'user' || kind === 'private' || kind === 'private_followup')) return 'confused';
  if (card.agent_status?.room_id && st === 'running') return 'group';
  return STATE_MAP[st] || 'idle';
}
function botActionSummary(card) {
  const ca = card.agent_status?.current_action;
  if (typeof ca === 'string') return ca;
  if (ca && typeof ca === 'object') return String(ca.name || ca.title || '');
  return '';
}
function botElapsed(card) {
  const at = Date.parse(card.agent_status?.since || '');
  return Number.isFinite(at) ? Math.max(0, Math.floor((Date.now() - at) / 1000)) : 0;
}

function botHasRun(id) {
  return typeof runs !== 'undefined' && [...runs.values()].some(t => t.botId === id);
}

function normalizeBot(card, index) {
  const hex = localStorage.getItem('thyca.tint.' + card.id)
    || TINT_HEX[card.tint]
    || TINT_HEX[TINT_ORDER[index % TINT_ORDER.length]];
  const shapeRaw = localStorage.getItem('thyca.shape.' + card.id);
  const shape = (typeof CREW_IDS !== 'undefined' && CREW_IDS.includes(shapeRaw)) ? shapeRaw : null;
  const themeRaw = localStorage.getItem('thyca.theme.' + card.id);
  const theme = (typeof THEME_IDS !== 'undefined' && THEME_IDS.includes(themeRaw)) ? themeRaw : null;
  const accRaw = localStorage.getItem('thyca.acc.' + card.id);
  const accessory = (typeof ACCESSORY_IDS !== 'undefined' && ACCESSORY_IDS.includes(accRaw)) ? accRaw : null;
  return {
    id: card.id,
    name: card.name || card.id,
    role: card.role || '',
    description: card.description || '',
    color: hex,
    tint: card.tint || 'accent',
    shape,
    theme,
    accessory,
    palette: derivePalette(hex, isDarkThemeActive()),
    state: botStateFrom(card),
    roomId: card.agent_status?.room_id || null,
    waitingOn: card.agent_status?.waiting_on || null,
    elapsed: botElapsed(card),
    summary: botActionSummary(card),
    capabilities: (card.skills || []).map(s => [s, '']),
    persona: String(card.persona || ''),
    memory: card.memory || null,
    tools: (card.tools || []).map(t => typeof t === 'string' ? { name: t, description: '' } : { name: t.name || '', description: t.description || '' }),
    conversations: [],
    favorite: botFavorite(card.id, card.system),
    open: false,
    phase: -1.2 - index * 1.7,
    system: card.system === true,
    model: card.model || null,
    bash: card.bash || '',
    schedule: card.schedule || null,
    mentioned: false, revision: 0, orbitAngle: 0, orbitAt: Date.now(),
    liveCalls: [],
    taskWindow: null,
    taskCache: null,
    taskGen: 0,
    idle: { since: Date.now(), nextAt: Date.now() + 3400 + Math.random() * 6200 + index * 900, nextGaze: Date.now() + 1200 + index * 850, active: null, endsAt: 0, comment: '', commentUntil: 0, last: null, lastGaze: -1, recentComments: [] },
  };
}
function refreshAllBotPalettes() {
  const dark = isDarkThemeActive();
  bots.forEach(b => {
    b.palette = derivePalette(b.color, dark);
    if (typeof document !== 'undefined' && document.querySelectorAll && typeof paletteStyle === 'function') {
      const styleStr = paletteStyle(b);
      document.querySelectorAll(`[data-bot-id="${b.id}"], .message-assistant[data-bot="${b.id}"]`).forEach(el => {
        el.setAttribute('style', styleStr);
      });
    }
  });
}

function currentChatWindow() {
  try {
    if (typeof currentConversation === 'undefined' || !currentConversation) return null;
    const router = typeof window !== 'undefined' ? window.ThycaRouter : null;
    if (router && typeof router.currentPage === 'function' && router.currentPage() !== 'chat') return null;
    return currentConversation;
  } catch { return null; }
}

function liveCallStarted(b, opts) {
  const { kind, name, call_id, conversation, turn, invocation_id } = opts || {};
  if (invocation_id && turn) {
    const seen = turn.seenInvocations || (turn.seenInvocations = new Set());
    const scope = `${b.id}\0${conversation}\0${invocation_id}`;
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
    conversation: conversation || null,
    seq: liveSeq,
  };
  b.liveCalls.push(entry);
  return entry;
}
function liveCallFinished(b, call_id, opts) {
  const { kind, name, conversation, turn, invocation_id } = opts || {};
  const id = String(call_id ?? '');
  if (!id) return null;
  const list = b.liveCalls || [];
  const candidates = list.filter(f =>
    (conversation == null || f.conversation === conversation)
    && (turn == null || f.turn === turn)
    && f.call_id === id
    && f.invocation_id === (invocation_id || null)
    && (kind == null || f.kind === kind)
    && (name == null || f.name === name));
  if (candidates.length !== 1) return null;
  return list.splice(list.indexOf(candidates[0]), 1)[0];
}
function clearLiveCalls(b, conversation) {
  if (!b || !b.liveCalls) return;
  if (conversation == null) b.liveCalls.splice(0, b.liveCalls.length);
  else {
    for (let i = b.liveCalls.length - 1; i >= 0; i--) {
      if (b.liveCalls[i].conversation === conversation) b.liveCalls.splice(i, 1);
    }
  }
}
function liveCallsForWindow(b, window) {
  if (!window) return [];
  return (b.liveCalls || []).filter(f => f.conversation === window);
}
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

async function refreshBotTasks(b, window) {
  const target = window === undefined ? currentChatWindow() : window;
  const flight = b._taskFlight;
  if (flight && flight.window === target) {
    flight.dirty = true;
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
      const parts = convParts(target);
      const url = parts && parts.kind === 'dm'
        ? `/api/bots/${encodeURIComponent(b.id)}/dm/tasks`
        : parts ? `/api/bots/${encodeURIComponent(b.id)}/groups/${encodeURIComponent(parts.group_id)}/tasks` : null;
      if (!url) throw new Error('unknown conversation');
      const payload = await getJson(url);
      if (gen !== b.taskGen || b.taskWindow !== target) return null;
      b.taskCache = { status: 'ok', payload };
    } catch (err) {
      if (gen !== b.taskGen || b.taskWindow !== target) return null;
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
    if (now === target) return refreshBotTasks(b, target);
  }
  return out;
}
function noteTaskStale(b) { if (b) b.taskGen++; }

async function loadRoster() {
  const payload = await getJson('/api/bots');
  bots.splice(0, bots.length, ...(payload.bots || []).map(normalizeBot));
  bots.forEach(b => { b.open = false; });
  if (!getBot(selectedBot) && bots[0]) selectedBot = bots[0].id;
}

function conversationRow(conversation) {
  const when = conversation.updated_at ? new Date(conversation.updated_at) : null;
  const day = when && when.toDateString() === new Date().toDateString() ? 'Hôm nay'
    : when && when.toDateString() === new Date(Date.now() - 864e5).toDateString() ? 'Hôm qua'
    : when ? when.toLocaleDateString('vi-VN', { day: 'numeric', month: 'numeric' }) : '';
  const turns = conversation.turns ? ` · ${conversation.turns} lượt` : '';
  const key = conversation.kind === 'dm' ? dmKey(conversation.bot_id) : groupKey(conversation.group_id);
  const label = conversation.kind === 'dm' ? ((getBot(conversation.bot_id) || {}).name || conversation.bot_id || 'Thyca') : groupTitleOf(conversation.title);
  return [key, label, day + turns];
}
async function refreshConversations() {
  const payload = await getJson('/api/conversations');
  const list = payload.conversations || [];
  const tsOf = s => { const t = Date.parse(s.updated_at || ''); return Number.isFinite(t) ? t : 0; };
  groups.splice(0, groups.length, ...list.filter(s => s.kind === 'group').map(s => {
    const live = (s.bots || []).filter(id => getBot(id));
    return { id: s.group_id, title: groupTitleOf(s.title), bots: live, subtitle: `${live.length} người bạn${s.turns ? ' · ' + s.turns + ' lượt' : ''}`, updatedAtMs: tsOf(s), favorite: groupFavoriteIds().has(s.group_id) };
  }));
  bots.forEach(b => {
    const mine = list.filter(s => (s.kind === 'dm' && s.bot_id === b.id)
      || (s.kind === 'group' && (s.bots || []).includes(b.id)));
    b.conversations = mine.map(conversationRow);
    b.activityAt = list.filter(s => s.kind === 'dm' && s.bot_id === b.id)
      .reduce((max, s) => Math.max(max, tsOf(s)), 0);
  });
}

async function syncRoster() {
  const payload = await getJson('/api/bots');
  const incoming = payload.bots || [], ids = new Set(incoming.map(c => c.id));
  let changed = false;
  incoming.forEach((raw, i) => {
    const cur = getBot(raw.id);
    if (!cur) { bots.push(normalizeBot(raw, i)); changed = true; return; }
    const next = normalizeBot(raw, i);
    ['name', 'role', 'description', 'color', 'tint', 'shape', 'theme', 'accessory', 'palette', 'capabilities', 'persona', 'tools', 'memory', 'system', 'model', 'bash', 'schedule', 'roomId', 'waitingOn'].forEach(k => { cur[k] = next[k]; });
    if (!botHasRun(cur.id)) { cur.state = next.state; cur.summary = next.summary; cur.elapsed = next.elapsed; }
  });
  for (let i = bots.length - 1; i >= 0; i--) if (!ids.has(bots[i].id)) { bots.splice(i, 1); changed = true; }
  if (!changed) { bots.forEach(b => { if (typeof updateCard === 'function') updateCard(b); }); return; }
  if (typeof renderTeam === 'function') renderTeam();
  if (typeof rebuildExpressionBots === 'function') rebuildExpressionBots();
  await refreshConversations();
  if (typeof renderGroups === 'function') renderGroups();
  if (!getBot(selectedBot) && bots.length) selectedBot = bots[0].id;
}
function scheduleRosterSync() {
  clearTimeout(rosterSyncTimer);
  rosterSyncTimer = setTimeout(() => syncRoster().catch(() => {}), 350);
}

async function botContext(botId) {
  await getJson(`/api/bots/${encodeURIComponent(botId)}/dm`);
  const ops = await getJson(`/api/bots/${encodeURIComponent(botId)}/dm/operations`);
  return ops && typeof ops === 'object' ? ops : null;
}
