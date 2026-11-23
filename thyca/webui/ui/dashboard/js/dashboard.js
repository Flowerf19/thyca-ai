ThycaRouter.register('dashboard', { title: 'Sử dụng', stylesheet: '/ui/dashboard/dashboardcss/dashboard.css', mount(ctx) {
  let routeKey = '';
  const root = ctx.root;
  const $ = (selector, base = root) => selector === '.workspace' ? root : base.querySelector(selector);
  const $$ = (selector, base = root) => [...base.querySelectorAll(selector)];

const FIRST_PAGE = 15;
const MORE_PAGE = 50;
const MAX_DAY_COLUMNS = 62;
const MAX_SESSIONS = 200;

let rangeFrom = '', rangeTo = '';
let rangeAll = false;
let customRangeOpen = false;
let botScope = '';
let dayMetric = 'tokens';
let statsData = null, statsError = '', statsLoading = false;
let statsKey = null, listKey = null, detailKey = '';
let statsLoaded = false, listLoaded = false;
let detailData = null, detailError = '', detailLoading = false;
let viewMode = 'overview', activeView = 'usage', overviewScroll = 0, overviewFocus = null;
const listFilters = { q: '', model: '', status: '' };
const traceState = { rows: [], total: 0, loading: false, error: '' };
let statsSeq = 0, traceSeq = 0, detailSeq = 0;
let qTimer = null;

const fmtInt = n => (typeof n === 'number' && Number.isFinite(n)) ? n.toLocaleString('vi-VN') : '—';
const fmtCompact = n => (typeof n !== 'number' || !Number.isFinite(n)) ? '—'
  : n >= 1e6 ? (n / 1e6).toLocaleString('vi-VN', { maximumFractionDigits: 1 }) + 'm'
  : fmtInt(n);
function fmtCost(usd) {
  if (typeof usd !== 'number' || !Number.isFinite(usd)) return '—';
  return '$' + usd.toFixed(usd >= 1 ? 2 : usd >= 0.01 ? 4 : 6);
}
function fmtDur(ms) {
  if (typeof ms !== 'number' || !Number.isFinite(ms)) return '—';
  if (ms < 1000) return fmtInt(ms) + ' ms';
  const s = ms / 1000;
  if (s < 60) return s.toLocaleString('vi-VN', { maximumFractionDigits: 1 }) + ' giây';
  const m = Math.floor(s / 60);
  return m + ' ph ' + Math.round(s - m * 60) + ' giây';
}
function fmtDateTime(iso) {
  const d = new Date(iso);
  if (!Number.isFinite(d.getTime())) return '—';
  return d.getDate() + ' thg ' + (d.getMonth() + 1) + ', '
    + String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
}
const fmtDay = key => key ? key.slice(8, 10) + '/' + key.slice(5, 7) : '—';
function fmtDayFull(key) {
  const d = new Date(key + 'T00:00:00Z');
  return Number.isFinite(d.getTime())
    ? d.toLocaleDateString('vi-VN', { weekday: 'short', day: 'numeric', month: 'numeric', timeZone: 'UTC' })
    : key;
}
function relTime(iso) {
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return '—';
  const m = Math.floor((Date.now() - t) / 60000);
  if (m < 1) return 'vừa xong';
  if (m < 60) return m + ' phút trước';
  const h = Math.floor(m / 60);
  if (h < 24) return h + ' giờ trước';
  const d = Math.floor(h / 24);
  if (d < 31) return d + ' ngày trước';
  return fmtDateTime(iso);
}
function share(num, den) {
  if (typeof num !== 'number' || !Number.isFinite(num) || typeof den !== 'number' || !Number.isFinite(den)) return '—';
  if (!den) return num === 0 ? '0%' : '—';
  const p = (num / den) * 100;
  return (p >= 10 ? Math.round(p) : Math.round(p * 10) / 10).toLocaleString('vi-VN') + '%';
}
const isoDay = d => d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');

const STATUS_META = {
  completed: ['Hoàn tất', 's-ok'],
  failed: ['Thất bại', 's-error'],
  context_limit: ['Hết ngữ cảnh', 's-warn'],
  loop_limit: ['Quá vòng lặp', 's-warn'],
};
function statusChip(status) {
  const [label, cls] = STATUS_META[status] || [status || 'không rõ', ''];
  return `<span class="st-chip ${cls}">${escapeHTML(label)}</span>`;
}

function avatarCell(botId) {
  const b = botId && getBot(botId);
  if (b) return `<span class="row-avatar">${avatar(b, b.state, 'expression-preview')}</span>`;
  const mark = botId && botId !== 'unknown' ? escapeHTML(botId.slice(0, 2).toUpperCase()) : '?';
  return `<span class="row-avatar id-chip" title="${escapeHTML(botId || '')}">${mark}</span>`;
}
function botDisplayName(botId) {
  const b = botId && getBot(botId);
  if (b) return b.name;
  return botId === 'unknown' ? 'Chưa gắn bot' : (botId || '—');
}
function turnBotIds(turn) {
  const ids = [];
  const push = id => { if (id && !ids.includes(id)) ids.push(id); };
  const breakdown = Array.isArray(turn.bot_breakdown) ? turn.bot_breakdown : [];
  if (breakdown.length) breakdown.forEach(entry => push(entry.bot_id));
  else push(turn.bot_id);
  return ids;
}
function turnRowHtml(turn) {
  const ids = botScope ? [botScope] : turnBotIds(turn);
  const id = ids[0] || '';
  const bot = id && getBot(id);
  const face = bot ? `<span class="mini-avatar">${avatar(bot, bot.state, 'expression-preview')}</span>`
    : `<span class="tl-botid">${escapeHTML(id || '—')}</span>`;
  const name = ids.length ? botDisplayName(id) + (ids.length > 1 ? ` +${ids.length - 1}` : '') : '—';
  const kind = turn.kind === 'dm' ? 'Chat riêng' : turn.kind === 'group' ? 'Chat nhóm' : 'Phiên';
  const rawTitle = turn.title || turn.group_id || turn.bot_id || '—';
  const title = turn.kind === 'group' && rawTitle === turn.group_id ? 'Hội thoại chung' : rawTitle;
  const conv = turn.kind === 'dm' ? dmKey(turn.bot_id) : groupKey(turn.group_id);
  const status = botScope && turn.status === 'failed' ? 'Lượt có lỗi' : null;
  return `<li><button type="button" class="tl-turn" data-conv="${escapeHTML(conv)}" data-turn="${turn.turn_index}"
      title="Mở lượt #${turn.turn_index} của “${escapeHTML(title)}”">
    <span class="tl-turn-idx">#${fmtInt(turn.turn_index)}</span>${face}<span class="tl-turn-main">
      <span class="tl-turn-line"><b>${escapeHTML(name)}</b><span class="tl-turn-title">${escapeHTML(title)}</span></span>
      <span class="tl-turn-sub"><span>${kind}</span><span>${escapeHTML(fmtDateTime(turn.started_at))}</span><span>${escapeHTML(turn.model || '—')}</span><span>${fmtInt(turn.requests)} gọi model</span><span>${fmtInt(turn.tool_calls)} gọi tool</span><span>${fmtInt(turn.total_tokens)} token</span><span>${fmtCost(turn.cost_usd)}</span><span>${fmtDur(turn.latency_ms)}</span></span>
    </span>${status ? `<span class="tl-chip s-error">${status}</span>` : statusChip(turn.status)}</button></li>`;
}
function updateRangeNote() {
  const f = $('#range-from').value, t = $('#range-to').value;
  const nice = v => v ? new Date(v + 'T00:00:00').toLocaleDateString('vi-VN', { day: 'numeric', month: 'numeric', year: 'numeric' }) : '…';
  $('#range-note').textContent = rangeAll ? 'Tất cả' : 'Kỳ ' + nice(f) + ' – ' + nice(t);
}
function syncRangeCustomUI() {
  const bar = $('#range-custom-bar'), toggle = $('#range-custom-toggle');
  if (bar) bar.hidden = !customRangeOpen;
  if (toggle) {
    toggle.setAttribute('aria-expanded', String(customRangeOpen));
    toggle.setAttribute('aria-pressed', String(customRangeOpen));
  }
}
function updatePresetButtons() {
  const from = $('#range-from').value, to = $('#range-to').value;
  const today = new Date();
  const preset = days => { const start = new Date(today); start.setDate(start.getDate() - (days - 1)); return [isoDay(start), isoDay(today)]; };
  const matchesPreset = rangeAll || [1, 7, 30].some(days => {
    const [pFrom, pTo] = preset(days);
    return pFrom === from && pTo === to;
  });
  if (!matchesPreset && (from || to)) customRangeOpen = true;
  $$('.range-presets .chip').forEach(button => {
    const days = button.dataset.preset;
    const active = days === 'all' ? rangeAll : !rangeAll && days && preset(Number(days))[0] === from && preset(Number(days))[1] === to;
    button.setAttribute('aria-pressed', String(!!active));
  });
  syncRangeCustomUI();
}
function applyRange() {
  const f = $('#range-from').value, t = $('#range-to').value;
  if (f && t && f > t) { toast('Ngày bắt đầu phải trước hoặc bằng ngày kết thúc.'); return; }
  rangeFrom = f; rangeTo = t; rangeAll = !f && !t;
  updateRangeNote(); updatePresetButtons();
  applyControlRoute();
}
function setPreset(days) {
  customRangeOpen = false;
  if (String(days) === 'all') {
    rangeFrom = ''; rangeTo = ''; rangeAll = true;
  } else {
    const to = new Date(), from = new Date();
    from.setDate(from.getDate() - (Number(days) - 1));
    rangeFrom = isoDay(from); rangeTo = isoDay(to); rangeAll = false;
  }
  $('#range-from').value = rangeFrom; $('#range-to').value = rangeTo;
  updateRangeNote(); updatePresetButtons();
  applyControlRoute();
}

function setStatus(text, cls) {
  const el = $('#page-status');
  $('#stats-status-text').textContent = text || '';
  $('#stats-retry').hidden = !statsError;
  el.className = 'page-status' + (cls ? ' ' + cls : '');
  el.hidden = !text;
}

function rangeParams() {
  const q = new URLSearchParams();
  if (rangeFrom) q.set('from', rangeFrom);
  if (rangeTo) q.set('to', rangeTo);
  if (botScope) q.set('bot_id', botScope);
  return q;
}
function pageQuery(view = activeView) {
  const q = new URLSearchParams();
  if (botScope) q.set('bot', botScope);
  if (rangeFrom) q.set('from', rangeFrom);
  if (rangeTo) q.set('to', rangeTo);
  if (rangeAll && !rangeFrom && !rangeTo) q.set('range', 'all');
  if (listFilters.q) q.set('q', listFilters.q);
  if (listFilters.model) q.set('model', listFilters.model);
  if (listFilters.status) q.set('status', listFilters.status);
  q.set('view', view);
  return '?' + q.toString();
}
function statsRequestKey() { return rangeParams().toString(); }
function listRequestKey() {
  const q = rangeParams();
  if (listFilters.q) q.set('q', listFilters.q);
  if (listFilters.model) q.set('model', listFilters.model);
  if (listFilters.status) q.set('status', listFilters.status);
  return q.toString();
}
function parseDetailRoute(route) {
  const raw = String(route.hash || '').replace(/^#/, '');
  const parts = raw.split('/');
  if (parts.length !== 3 || !/^\d{1,8}$/.test(parts[2])) return null;
  const turn = Number(parts[2]);
  if (!Number.isSafeInteger(turn)) return null;
  let id;
  try { id = decodeURIComponent(parts[1]); } catch { return null; }
  if (parts[0] === 'dm' && /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(id)) return { kind: 'dm', bot_id: id, turn };
  if (parts[0] === 'groups' && /^group-[0-9a-f]{32}$/.test(id)) return { kind: 'group', group_id: id, turn };
  return null;
}
function readRoute(route, initial = false) {
  const params = new URLSearchParams(route.search);
  botScope = params.get('bot') || '';
  activeView = ['usage', 'cost', 'trace'].includes(params.get('view')) ? params.get('view') : 'usage';
  rangeFrom = params.get('from') || '';
  rangeTo = params.get('to') || '';
  rangeAll = !rangeFrom && !rangeTo && params.get('range') === 'all';
  listFilters.q = params.get('q') || '';
  listFilters.model = params.get('model') || '';
  listFilters.status = params.get('status') || '';
  if (parseDetailRoute(route)) activeView = 'trace';
  if (!rangeFrom && !rangeTo && !rangeAll) {
    const today = new Date(), from = new Date(); from.setDate(from.getDate() - 6);
    rangeFrom = isoDay(from); rangeTo = isoDay(today);
  }
  $('#range-from').value = rangeFrom; $('#range-to').value = rangeTo;
  $('#filter-q').value = listFilters.q;
  updateRangeNote(); updatePresetButtons(); updateScopeChip(); updateViewShell();
  fillModelFilter(statsData?.models || []);
  fillStatusFilter(statsData?.by_status || []);
}
function writeCurrentRoute(hash = ctx.route.hash) {
  ctx.updateRoute({ search: pageQuery(), hash });
  routeKey = ctx.route.search + ctx.route.hash;
}
function applyControlRoute() {
  writeCurrentRoute('');
  routeKey = ctx.route.search + ctx.route.hash;
  updateScopeChip();
  ensureOverviewData(true);
}

function updateScopeChip() {
  const card = $('#usage-scope-card');
  const filterRow = $('#usage-filter-row');
  if (card) {
    if (!botScope) {
      card.hidden = true;
      card.innerHTML = '';
      if (typeof card.removeAttribute === 'function') card.removeAttribute('style');
      else if (card.style) card.style.cssText = '';
      if (filterRow && typeof filterRow.removeAttribute === 'function') filterRow.removeAttribute('style');
      else if (filterRow && filterRow.style) filterRow.style.cssText = '';
    } else {
      const b = getBot(botScope);
      card.hidden = false;
      const css = b && b.palette && typeof paletteStyle === 'function' ? paletteStyle(b) : '';
      if (css) {
        if (card.style) card.style.cssText = css;
        if (filterRow && filterRow.style) filterRow.style.cssText = css;
      } else if (typeof card.removeAttribute === 'function') {
        card.removeAttribute('style');
        if (filterRow && typeof filterRow.removeAttribute === 'function') filterRow.removeAttribute('style');
      } else if (card.style) {
        card.style.cssText = '';
        if (filterRow && filterRow.style) filterRow.style.cssText = '';
      }
      const name = b ? b.name : botScope;
      const face = b
        ? avatar(b, b.state, 'expression-preview')
        : `<span class="id-chip" aria-hidden="true">${escapeHTML(String(botScope).slice(0, 2).toUpperCase() || '?')}</span>`;
      const stateLabel = b && typeof states !== 'undefined' && states[b.state] ? states[b.state][1] : '';
      const t = statsData?.totals;
      const meta = t
        ? `${fmtInt(t.conversations ?? 0)} phiên · ${fmtInt(t.turns ?? 0)} lượt${stateLabel ? ' · ' + escapeHTML(stateLabel) : ''}`
        : `Đang lọc theo bot${stateLabel ? ' · ' + escapeHTML(stateLabel) : ''}`;
      card.innerHTML = `<div class="bot-scope">${face}<div class="bot-scope-main"><h3 class="bot-scope-name">${escapeHTML(name)}${b?.role ? ` <span class="bot-scope-role">${escapeHTML(b.role)}</span>` : ''}</h3><p class="bot-scope-meta">${meta}</p></div><button class="quiet-button bot-scope-clear" id="scope-clear" type="button">Bỏ lọc</button></div>`;

    }
  }
  ctx.sidebar?.querySelector('#usage-overview-scope')?.setAttribute('aria-pressed', String(!botScope));
}
function updateViewShell() {
  const labels = { usage: 'Sử dụng token', cost: 'Chi phí', trace: 'Trace' };
  const bot = botScope && getBot(botScope);
  $('#usage-scope-eyebrow').textContent = botScope ? botDisplayName(botScope) : 'Tổng quan';
  $('#usage-view-title').textContent = labels[activeView] || labels.usage;
  $('#detail-back-label').textContent = labels[activeView] || labels.usage;
  $('#card-bot').hidden = !!botScope;
  $('#card-bot-cost').hidden = !!botScope;
  $('#usage-scope-note').textContent = botScope
    ? `Chỉ tính đóng góp được gắn rõ với ${bot ? bot.name : botScope}; một lượt vẫn có thể chứa hoạt động của bot khác. Thống kê dựa trên tối đa ${MAX_SESSIONS} phiên gần nhất. Lượt chưa gắn bot chỉ có trong Tổng quan.`
    : `Tổng quan gồm cả lượt chưa gắn bot; thống kê dựa trên tối đa ${MAX_SESSIONS} phiên gần nhất.`;
  for (const view of ['usage', 'cost', 'trace']) {
    const button = $(`#usage-view-tabs [data-usage-view="${view}"]`);
    button?.setAttribute('aria-pressed', String(view === activeView));
    const panel = $(`#usage-panel-${view}`);
    if (panel) panel.hidden = view !== activeView;
  }
}
function selectUsageView(view) {
  if (!['usage', 'cost', 'trace'].includes(view) || view === activeView) return;
  void ctx.navigate('dashboard', { search: pageQuery(view), hash: '' });
}
function selectBot(id) {
  botScope = id || '';
  ctx.highlightBot(botScope); ctx.renderTeam();
  if (ctx.sidebar?.classList.contains('mobile-open') && typeof closeMobile === 'function') closeMobile();
  writeCurrentRoute('');
  applyRoute(ctx.route, { forceScope: true });
}
function loadConversation(id, botId) { selectBot(botId || ''); }
async function loadStats(force = false) {
  const key = statsRequestKey();
  if (!force && key === statsKey && (statsLoaded || statsLoading || statsError)) return;
  const changed = key !== statsKey;
  statsKey = key;
  if (changed) { statsLoaded = false; statsData = null; renderStats({}, { loading: true }); }
  const seq = ++statsSeq;
  statsLoading = true; statsError = '';
  setStatus('Đang tải số liệu…');
  try {
    const data = await getJson('/api/traces/stats?' + rangeParams());
    if (seq !== statsSeq || key !== statsKey) return;
    statsData = data; statsLoaded = true; renderStats(data); setStatus('');
  } catch (err) {
    if (seq !== statsSeq || key !== statsKey) return;
    statsError = err.message || 'lỗi không rõ';
    setStatus('Không tải được số liệu: ' + statsError, 's-error');
  } finally {
    if (seq === statsSeq && key === statsKey) { statsLoading = false; }
  }
}
async function loadTraces(reset = true, force = false, offsetOverride = null) {
  const key = listRequestKey();
  if (!force && key === listKey && reset && (listLoaded || traceState.loading || traceState.error)) return;
  if (!force && key === listKey && !reset && traceState.loading) return;
  if (key !== listKey) reset = true;
  if (reset) {
    listLoaded = false; traceState.rows = []; traceState.total = 0;
    traceState.error = ''; traceState.failedRequest = null;
  }
  listKey = key;
  const seq = ++traceSeq;
  traceState.loading = true; traceState.error = '';
  renderTraceTable();
  const q = rangeParams();
  if (listFilters.q) q.set('q', listFilters.q);
  if (listFilters.model) q.set('model', listFilters.model);
  if (listFilters.status) q.set('status', listFilters.status);
  q.set('limit', reset ? FIRST_PAGE : MORE_PAGE);
  const offset = reset ? 0 : (offsetOverride ?? traceState.rows.length);
  q.set('offset', offset);
  try {
    const data = await getJson('/api/traces?' + q);
    if (seq !== traceSeq || key !== listKey) return;
    traceState.rows = traceState.rows.concat(Array.isArray(data.traces) ? data.traces : []);
    traceState.total = typeof data.total === 'number' ? data.total : traceState.rows.length;
    traceState.failedRequest = null; listLoaded = true;
    renderTraceTable();
  } catch (err) {
    if (seq !== traceSeq || key !== listKey) return;
    traceState.error = err.message || 'lỗi không rõ';
    traceState.failedRequest = { reset, offset };
    renderTraceTable();
  } finally {
    if (seq === traceSeq && key === listKey) {
      traceState.loading = false; renderTraceTable();
    }
  }
}
function ensureOverviewData(force = false) {
  void loadStats(force);
  void loadTraces(true, force);
}
function setView(mode, restore = false) {
  viewMode = mode;
  $('#usage-header-controls').hidden = mode !== 'overview';
  $('#usage-overview').hidden = mode !== 'overview';
  $('#turn-detail-view').hidden = mode !== 'detail';
  const scroller = $('#usage-scroll');
  if (mode === 'detail') scroller.scrollTop = 0;
  else if (restore) {
    scroller.scrollTop = overviewScroll;
    const focus = overviewFocus && [...$('#trace-conversations').querySelectorAll('button[data-conv][data-turn]')]
      .find(button => button.dataset.conv === overviewFocus.sid && button.dataset.turn === String(overviewFocus.turn));
    const target = focus || $('#usage-view-title');
    if (target && !target.hasAttribute('tabindex')) target.tabIndex = -1;
    target?.focus?.({ preventScroll: true });
  }
}
function botContributionHtml(detail) {
  if (!botScope) return '';
  const contribution = Array.isArray(detail.bot_breakdown)
    ? detail.bot_breakdown.find(entry => entry.bot_id === botScope) : null;
  const name = botDisplayName(botScope);
  if (!contribution) return `<section class="panel own-contribution"><h2 class="panel-title">Đóng góp của ${escapeHTML(name)}</h2><p class="card-copy">Không có phần đóng góp được ghi nhận cho bot này.</p></section>`;
  const metrics = [
    ['Gọi model', fmtInt(contribution.requests)], ['Gọi tool', fmtInt(contribution.tool_calls)],
    ['Input (gồm Cache)', fmtInt(contribution.prompt_tokens)], ['Cache read', fmtInt(contribution.cached_tokens)],
    ['Output', fmtInt(contribution.completion_tokens)], ['Tổng token', fmtInt(contribution.total_tokens)],
    ['Chi phí đã ghi nhận', fmtCost(contribution.cost_usd)], ['Độ trễ', fmtDur(contribution.latency_ms)],
  ];
  return `<section class="panel own-contribution"><h2 class="panel-title">Đóng góp của ${escapeHTML(name)}</h2><ul class="meta-list">${metrics.map(([label, value]) => `<li><span>${label}</span><strong>${value}</strong></li>`).join('')}</ul></section>`;
}
function renderDetail() {
  const status = $('#detail-status'), message = $('#detail-status-text'), retry = $('#detail-retry'), body = $('#detail-body');
  if (detailError) {
    status.hidden = false; message.textContent = 'Không đọc được lượt ' + detailKey + ': ' + detailError;
    retry.hidden = false;
    body.innerHTML = '<div class="empty-note"><strong>Không mở được trace này.</strong>Lượt có thể đã bị xoá, hoặc phiên không còn đọc được. Quay lại để chọn lượt khác.</div>';
    return;
  }
  status.hidden = true; retry.hidden = true; message.textContent = '';
  body.innerHTML = detailLoading ? '<p class="status-line">Đang đọc lượt…</p>'
    : detailData ? (botScope ? `<p class="detail-context-note">Ngữ cảnh dưới đây là toàn bộ lượt và có thể chứa hoạt động của bot khác.</p>${botContributionHtml(detailData)}<div class="full-turn-context">${window.ThycaTraceDetail.render(detailData)}</div>` : window.ThycaTraceDetail.render(detailData)) : '';
}
async function loadDetail(ref, turn, force = false) {
  const sid = ref.kind === 'dm' ? dmKey(ref.bot_id) : groupKey(ref.group_id);
  const key = sid + '/' + turn;
  if (!force && detailKey === key && (detailLoading || detailData || detailError)) return;
  detailKey = key; detailData = null; detailError = ''; detailLoading = true;
  const seq = ++detailSeq;
  renderDetail();
  try {
    const url = ref.kind === 'dm'
      ? `/api/traces/dm/${encodeURIComponent(ref.bot_id)}/${turn}`
      : `/api/traces/groups/${encodeURIComponent(ref.group_id)}/${turn}`;
    const data = await getJson(url);
    if (seq !== detailSeq || key !== detailKey) return;
    detailData = data;
  } catch (err) {
    if (seq !== detailSeq || key !== detailKey) return;
    detailError = err.message || 'không đọc được';
  } finally {
    if (seq === detailSeq && key === detailKey) { detailLoading = false; renderDetail(); }
  }
}
function returnFromDetail() {
  detailSeq++; detailLoading = false;
  ctx.returnTo({ search: ctx.route.search, hash: '' });
}
function applyRoute(route, { force = false } = {}) {
  const oldStatsKey = statsRequestKey(), oldListKey = listRequestKey();
  const wasDetail = viewMode === 'detail';
  const priorBot = botScope;
  readRoute(route);
  const selected = parseDetailRoute(route);
  if (selected && wasDetail && priorBot !== botScope) {
    ctx.updateRoute({ search: pageQuery(), hash: '' });
    route = ctx.route; routeKey = route.search + route.hash;
  }
  routeKey = route.search + route.hash;
  ctx.highlightBot(botScope); ctx.renderTeam();
  if (selected && !(wasDetail && priorBot !== botScope)) {
    if (!wasDetail) {
      overviewScroll = $('#usage-scroll').scrollTop;
      const active = document.activeElement?.closest?.('button[data-conv][data-turn]');
      overviewFocus = active ? { sid: active.dataset.conv, turn: active.dataset.turn } : overviewFocus;
    }
    setView('detail');
    void loadDetail(selected, selected.turn, force);
    return;
  }
  if (wasDetail) {
    detailSeq++; detailLoading = false; detailData = null; detailError = ''; detailKey = '';
  }
  setView('overview', wasDetail);
  updateScopeChip();
  const statsChanged = oldStatsKey !== statsRequestKey();
  const listChanged = oldListKey !== listRequestKey();
  if (force || statsChanged || statsKey !== statsRequestKey()) void loadStats(force);
  if (force || listChanged || listKey !== listRequestKey()) void loadTraces(true, force);
}

function heroCard(label, value, hint) {
  return `<section class="stat"><p class="stat-label">${label}</p><p class="stat-value">${value}</p>${hint ? `<p class="stat-hint">${hint}</p>` : ''}</section>`;
}
function renderStats(s, { loading = false } = {}) {
  if (loading) {
    $('#stat-grid').innerHTML = [
      heroCard('Token', '—', 'Input — · Cache — · Output —'),
      heroCard('Chi phí', '—', ''),
      heroCard('Gọi model', '—', '— tool · — lượt'),
      heroCard('Độ trễ p50', '—', 'p90 —'),
    ].join('');
    for (const id of ['chart-day', 'chart-cost', 'chart-hour', 'rows-bot', 'rows-model', 'rows-bot-cost', 'rows-model-cost']) $(`#${id}`).innerHTML = '';
    $('#chart-day-note').textContent = ''; $('#chart-cost-note').textContent = ''; $('#cost-coverage').textContent = '';
    $('#usage-input-total').textContent = '—'; $('#usage-cache-total').textContent = '—'; $('#usage-output-total').textContent = '—';
    $('#card-bot').hidden = !!botScope;
    $('#card-bot-cost').hidden = !!botScope;
    fillModelFilter(s.models || []);
    fillStatusFilter(s.by_status || []);
    updateScopeChip();
    return;
  }
  const t = s.totals || {};
  const inTok = typeof t.prompt_tokens === 'number' ? Math.max(0, t.prompt_tokens - (typeof t.cached_tokens === 'number' ? t.cached_tokens : 0)) : t.prompt_tokens;
  $('#stat-grid').innerHTML = [
    heroCard('Token', fmtInt(t.total_tokens), `Input ${fmtCompact(inTok)} · Cache ${fmtCompact(t.cached_tokens)} · Output ${fmtCompact(t.completion_tokens)}`),
    heroCard('Chi phí', fmtCost(t.cost_usd),
      t.cost_usd == null ? 'chưa có giá'
        : (typeof t.requests === 'number' && t.requests ? 'TB ' + fmtCost(t.cost_usd / t.requests) + ' / req' : '')),
    heroCard('Gọi model', fmtInt(t.requests), `${fmtInt(t.tool_calls)} tool · ${fmtInt(t.turns)} lượt`),
    heroCard('Độ trễ p50', fmtDur(t.latency_ms_p50), 'p90 ' + fmtDur(t.latency_ms_p90)),
  ].join('');
  $('#usage-input-total').textContent = fmtInt(inTok);
  $('#usage-cache-total').textContent = fmtInt(t.cached_tokens);
  $('#usage-output-total').textContent = fmtInt(t.completion_tokens);
  $('#cost-coverage').textContent = typeof t.priced_turns === 'number' && typeof t.turns === 'number'
    ? `${fmtInt(t.priced_turns)} / ${fmtInt(t.turns)} lượt có giá · phần thiếu không tính là $0.`
    : 'Chưa có dữ liệu tỷ lệ định giá.';
  renderDayChart(); renderCostChart(); renderBotRows(); renderModelRows(); renderCostRows(); renderHourChart();
  fillModelFilter(s.models || []);
  fillStatusFilter(s.by_status || []);
  updateScopeChip();
}

function dayColumns(byDay) {
  const map = new Map(byDay.map(d => [d.day, d]));
  if (rangeFrom && rangeTo) {
    const from = Date.parse(rangeFrom), to = Date.parse(rangeTo);
    const span = Math.round((to - from) / 864e5);
    if (span >= 0 && span < MAX_DAY_COLUMNS) {
      const days = [];
      for (let t = from; t <= to; t += 864e5) {
        const key = new Date(t).toISOString().slice(0, 10);
        days.push(map.get(key) || { day: key, requests: 0, prompt_tokens: 0, cached_tokens: 0, completion_tokens: 0, cost_usd: null });
      }
      return days;
    }
  }
  return byDay;
}
function dayTokenParts(day) {
  if (![day.prompt_tokens, day.cached_tokens, day.completion_tokens].every(value => typeof value === 'number' && Number.isFinite(value))) return null;
  return { input: Math.max(day.prompt_tokens - day.cached_tokens, 0), cache: Math.max(day.cached_tokens, 0), output: Math.max(day.completion_tokens, 0) };
}
function renderDayChart() {
  const box = $('#chart-day');
  if (!statsData) { box.innerHTML = ''; $('#chart-day-note').textContent = ''; return; }
  const cols = dayColumns(statsData.by_day || []);
  if (!cols.length) { box.innerHTML = '<div class="empty-note"><strong>Chưa có lượt nào trong kỳ.</strong>Các cột sẽ xuất hiện khi có phiên.</div>'; $('#chart-day-note').textContent = ''; return; }
  const vals = cols.map(day => dayMetric === 'tokens' ? dayTokenParts(day) : null);
  const totals = cols.map((day, index) => dayMetric === 'tokens'
    ? (vals[index] ? vals[index].input + vals[index].cache + vals[index].output : null)
    : (typeof day.requests === 'number' && Number.isFinite(day.requests) ? day.requests : null));
  const max = Math.max(0, ...totals.filter(value => value !== null));
  const step = Math.max(1, Math.ceil(cols.length / 15));
  box.innerHTML = `<div class="day-chart" role="img" aria-label="${dayMetric === 'tokens' ? 'Thành phần token' : 'Lượt gọi model'} theo ngày">` + cols.map((day, index) => {
    const total = totals[index], pct = value => max && total !== null ? (value / max) * 100 : 0;
    const parts = vals[index];
    const tip = dayMetric === 'tokens'
      ? `${fmtDayFull(day.day)} · Input ${fmtInt(day.prompt_tokens)} (Cache ${fmtInt(day.cached_tokens)}) · Output ${fmtInt(day.completion_tokens)}`
      : `${fmtDayFull(day.day)} · ${fmtInt(day.requests)} lượt gọi model`;
    const bars = total === null ? '' : dayMetric === 'tokens'
      ? `<i class="dc-bar dc-seg-output" style="height:${pct(parts.output)}%"></i><i class="dc-bar dc-seg-cache" style="height:${pct(parts.cache)}%"></i><i class="dc-bar dc-seg-input" style="height:${pct(parts.input)}%"></i>`
      : `<i class="dc-bar dc-bar-requests${total ? '' : ' is-zero'}" style="height:${pct(total)}%"></i>`;
    return `<div class="dc-col${total === null ? ' is-unavailable' : ''}" title="${escapeHTML(tip)}"><span class="dc-track">${bars || '<i class="dc-unavailable">—</i>'}</span><span class="dc-day">${index % step === 0 || index === cols.length - 1 ? fmtDay(day.day) : ''}</span></div>`;
  }).join('') + '</div>';
  $('#chart-day-note').textContent = dayMetric === 'tokens'
    ? 'Ngày theo UTC; cột tách Input chưa cache, Cache và Output.'
    : 'Ngày theo UTC; số lượt gọi model mỗi ngày.';
}
function renderCostChart() {
  const box = $('#chart-cost');
  if (!statsData) { box.innerHTML = ''; $('#chart-cost-note').textContent = ''; return; }
  const cols = dayColumns(statsData.by_day || []);
  if (!cols.length) { box.innerHTML = '<div class="empty-note"><strong>Chưa có lượt nào trong kỳ.</strong></div>'; $('#chart-cost-note').textContent = ''; return; }
  const values = cols.map(day => typeof day.cost_usd === 'number' && Number.isFinite(day.cost_usd) ? day.cost_usd : null);
  const max = Math.max(0, ...values.filter(value => value !== null));
  const step = Math.max(1, Math.ceil(cols.length / 15));
  box.innerHTML = '<div class="day-chart cost-day-chart" role="img" aria-label="Chi phí đã ghi nhận theo ngày">' + cols.map((day, index) => {
    const value = values[index], pct = value !== null && max ? value / max * 100 : 0;
    const tip = `${fmtDayFull(day.day)} · chi phí đã ghi nhận ${fmtCost(day.cost_usd)}`;
    return `<div class="dc-col${value === null ? ' is-unavailable' : ''}" title="${escapeHTML(tip)}"><span class="dc-track">${value === null ? '<i class="dc-unavailable">—</i>' : `<i class="dc-bar dc-bar-cost${value ? '' : ' is-zero'}" style="height:${pct}%"></i>`}</span><span class="dc-day">${index % step === 0 || index === cols.length - 1 ? fmtDay(day.day) : ''}</span></div>`;
  }).join('') + '</div>';
  $('#chart-cost-note').textContent = 'Ngày theo UTC; “—” là chưa có giá, không phải $0.';
}

function meterWidth(value, max) {
  return typeof value === 'number' && Number.isFinite(value) && typeof max === 'number' && max > 0
    ? Math.max(0, Math.min(100, value / max * 100)) : 0;
}
function rowMeta(row) {
  return `${fmtInt(row.conversations)} phiên · ${fmtInt(row.turns)} lượt · ${fmtInt(row.requests)} request · ${fmtInt(row.tool_calls)} tool · ${fmtInt(row.total_tokens)} token · ${fmtCost(row.cost_usd)}`;
}
function renderBotRows() {
  $('#card-bot').hidden = !!botScope;
  const rows = (statsData?.by_bot || []).slice().sort((a, b) => (b.total_tokens || 0) - (a.total_tokens || 0));
  const total = statsData?.totals?.total_tokens;
  const max = Math.max(0, ...rows.map(row => typeof row.total_tokens === 'number' ? row.total_tokens : 0));
  $('#rows-bot').innerHTML = !statsData ? '' : rows.length ? rows.map(row => {
    const bot = row.bot_id && row.bot_id !== 'unknown' && getBot(row.bot_id), value = row.total_tokens;
    const pct = meterWidth(value, max), percent = share(value, total);
    const meta = rowMeta(row) + ` · cuối ${relTime(row.last_started_at)}`;
    const contents = `${avatarCell(row.bot_id)}<span class="bot-row-main"><span class="bot-row-name">${escapeHTML(botDisplayName(row.bot_id))}</span><span class="bot-row-meta">${meta}</span><span class="share-bar" role="img" aria-label="${escapeHTML(percent)} tổng token"><i style="width:${pct}%;background:${bot ? bot.color : '#a08f68'}"></i></span></span><span class="bot-row-share">${fmtInt(value)}<small>token · ${percent}</small></span>`;
    return `<li>${bot ? `<button type="button" class="bot-row bot-row-scope" data-action="select-bot" data-bot-id="${escapeHTML(row.bot_id)}" aria-label="Xem sử dụng của ${escapeHTML(bot.name)}">${contents}</button>` : `<div class="bot-row">${contents}</div>`}</li>`;
  }).join('') : '<li><div class="empty-note"><strong>Chưa có dữ liệu bot trong kỳ.</strong></div></li>';
}
function renderModelRows() {
  const rows = (statsData?.by_model || []).filter(row => row.model !== 'unknown').slice().sort((a, b) => (b.total_tokens || 0) - (a.total_tokens || 0));
  const total = statsData?.totals?.total_tokens, max = Math.max(0, ...rows.map(row => typeof row.total_tokens === 'number' ? row.total_tokens : 0));
  $('#rows-model').innerHTML = !statsData ? '' : rows.length ? rows.map(row => `
    <li class="model-row"><span class="model-name" title="${escapeHTML(row.model || '—')}">${escapeHTML(row.model || '—')}</span>
      <span class="model-cost">${fmtInt(row.total_tokens)} token</span>
      <span class="model-meta">${escapeHTML(rowMeta(row))} · p50 ${fmtDur(row.latency_ms_p50)} · dùng cuối ${relTime(row.last_started_at)}</span>
      <span class="meter row-meter" role="img" aria-label="${escapeHTML(share(row.total_tokens, total))} tổng token"><i style="width:${meterWidth(row.total_tokens, max)}%"></i></span>
    </li>`).join('') : ((statsData?.by_model || []).length ? '<li><div class="empty-note"><strong>Không có model xác định trong kỳ.</strong></div></li>' : '<li><div class="empty-note"><strong>Chưa có lượt nào trong kỳ.</strong></div></li>');
}
function costMeta(row) {
  return `${fmtInt(row.priced_turns)} lượt có ghi nhận giá · ${fmtInt(row.turns)} lượt · ${fmtInt(row.conversations)} phiên · ${fmtInt(row.requests)} request · ${fmtInt(row.total_tokens)} token`;
}
function renderCostRows() {
  $('#card-bot-cost').hidden = !!botScope;
  const bots = (statsData?.by_bot || []).slice().sort((a, b) => (b.cost_usd ?? -1) - (a.cost_usd ?? -1));
  const models = (statsData?.by_model || []).filter(row => row.model !== 'unknown').slice().sort((a, b) => (b.cost_usd ?? -1) - (a.cost_usd ?? -1));
  const total = statsData?.totals?.cost_usd;
  const botMax = Math.max(0, ...bots.map(row => typeof row.cost_usd === 'number' ? row.cost_usd : 0));
  const modelMax = Math.max(0, ...models.map(row => typeof row.cost_usd === 'number' ? row.cost_usd : 0));
  $('#rows-bot-cost').innerHTML = !statsData ? '' : bots.length ? bots.map(row => {
    const bot = row.bot_id && row.bot_id !== 'unknown' && getBot(row.bot_id), value = row.cost_usd, percent = share(value, total);
    const contents = `${avatarCell(row.bot_id)}<span class="bot-row-main"><span class="bot-row-name">${escapeHTML(botDisplayName(row.bot_id))}</span><span class="bot-row-meta">${costMeta(row)}</span><span class="share-bar" role="img" aria-label="${escapeHTML(percent)} chi phí đã ghi nhận"><i style="width:${meterWidth(value, botMax)}%;background:${bot ? bot.color : '#a08f68'}"></i></span></span><span class="bot-row-share">${fmtCost(value)}<small>${percent}</small></span>`;
    return `<li>${bot ? `<button type="button" class="bot-row bot-row-scope" data-action="select-bot" data-bot-id="${escapeHTML(row.bot_id)}" aria-label="Xem sử dụng của ${escapeHTML(bot.name)}">${contents}</button>` : `<div class="bot-row">${contents}</div>`}</li>`;
  }).join('') : '<li><div class="empty-note"><strong>Chưa có dữ liệu chi phí theo bot.</strong></div></li>';
  $('#rows-model-cost').innerHTML = !statsData ? '' : models.length ? models.map(row => `
    <li class="model-row"><span class="model-name" title="${escapeHTML(row.model || '—')}">${escapeHTML(row.model || '—')}</span>
      <span class="model-cost">${fmtCost(row.cost_usd)}</span>
      <span class="model-meta">${escapeHTML(costMeta(row))} · p50 ${fmtDur(row.latency_ms_p50)}</span>
      <span class="meter row-meter" role="img" aria-label="${escapeHTML(share(row.cost_usd, total))} chi phí đã ghi nhận"><i class="cost-meter-fill" style="width:${meterWidth(row.cost_usd, modelMax)}%"></i></span>
    </li>`).join('') : ((statsData?.by_model || []).length ? '<li><div class="empty-note"><strong>Không có model xác định trong kỳ.</strong></div></li>' : '<li><div class="empty-note"><strong>Chưa có dữ liệu chi phí theo model.</strong></div></li>');
}

function renderHourChart() {
  const map = new Map((statsData?.by_hour || []).map(h => [h.hour, h]));
  const cols = [];
  for (let h = 0; h < 24; h++) {
    const key = String(h).padStart(2, '0');
    const v = map.get(key);
    cols.push({ hour: key, requests: v ? v.requests : 0, cost_usd: v ? v.cost_usd : null });
  }
  const max = Math.max(0, ...cols.map(c => c.requests));
  const box = $('#chart-hour');
  if (!max) { box.innerHTML = '<div class="empty-note"><strong>Chưa có lượt nào trong kỳ.</strong></div>'; return; }
  box.innerHTML = '<div class="hour-chart" role="img" aria-label="Lượt theo giờ UTC">' + cols.map(c => {
    const pct = max ? (c.requests / max) * 100 : 0;
    const tip = `${c.hour}:00–${c.hour}:59 · ${fmtInt(c.requests)} request · ${fmtCost(c.cost_usd)}`;
    const label = (Number(c.hour) % 6 === 0 || c.hour === '23') ? c.hour + 'h' : '';
    return `<div class="hc-col" title="${escapeHTML(tip)}"><span class="hc-track"><i class="hc-bar${c.requests ? '' : ' is-zero'}" style="height:${pct}%"></i></span><span class="hc-hour">${label}</span></div>`;
  }).join('') + '</div>';
}

function fillModelFilter(models) {
  const sel = $('#filter-model'), cur = listFilters.model;
  const values = [...new Set(models)];
  if (cur && !values.includes(cur)) values.push(cur);
  sel.replaceChildren(
    Object.assign(document.createElement('option'), { value: '', textContent: 'Tất cả model' }),
    ...values.map(m => Object.assign(document.createElement('option'), { value: m, textContent: m })),
  );
  sel.value = cur;
}
function fillStatusFilter(byStatus) {
  const sel = $('#filter-status'), cur = listFilters.status;
  const extras = byStatus.map(x => x.status).filter(s => s && !(s in STATUS_META));
  if (cur && !(cur in STATUS_META) && !extras.includes(cur)) extras.push(cur);
  sel.replaceChildren(
    Object.assign(document.createElement('option'), { value: '', textContent: 'Tất cả trạng thái' }),
    ...Object.entries(STATUS_META).map(([v, [label]]) => Object.assign(document.createElement('option'), { value: v, textContent: label })),
    ...extras.map(v => Object.assign(document.createElement('option'), { value: v, textContent: v })),
  );
  sel.value = cur;
}

function renderTraceTable() {
  const err = $('#trace-status');
  $('#trace-status-text').textContent = traceState.error ? 'Không tải được danh sách lượt: ' + traceState.error : '';
  err.hidden = !traceState.error;
  $('#trace-retry').hidden = !traceState.error;
  const rows = traceState.rows;
  $('#trace-wrap').hidden = !rows.length;
  const filtered = !!(listFilters.q || listFilters.model || listFilters.status);
  $('#trace-empty-hint').textContent = filtered
    ? 'Bộ lọc lượt đang bật — thử nới lọc hoặc đổi kỳ.'
    : 'Đổi khoảng ngày để xem thêm.';
  $('#trace-empty').hidden = !!rows.length || !!traceState.error || traceState.loading;
  $('#trace-conversations').innerHTML = rows.map(turnRowHtml).join('');
  renderTraceFoot();
}
function renderTraceFoot() {
  const more = $('#trace-more');
  $('#trace-count').textContent = traceState.loading && !traceState.rows.length
    ? 'Đang tải…' : `Hiển thị ${fmtInt(traceState.rows.length)} trong ${fmtInt(traceState.total)} lượt`;
  more.hidden = traceState.loading || !!traceState.error || traceState.rows.length >= traceState.total;
  more.disabled = traceState.loading;
  more.textContent = traceState.loading ? 'Đang tải…' : 'Tải thêm';
}
function openTraceRow(e) {
  const button = e.target.closest('button[data-conv][data-turn]');
  if (!button) return;
  clearTimeout(qTimer); qTimer = null;
  overviewScroll = $('#usage-scroll').scrollTop;
  overviewFocus = { sid: button.dataset.conv, turn: button.dataset.turn };
  const parts = convParts(button.dataset.conv);
  const hash = parts && parts.kind === 'dm'
    ? '#dm/' + encodeURIComponent(parts.bot_id) + '/' + button.dataset.turn
    : '#groups/' + encodeURIComponent(parts.group_id) + '/' + button.dataset.turn;
  ctx.navigate('dashboard', { search: ctx.route.search, hash });
}

function syncListFilters() {
  listFilters.q = $('#filter-q').value.trim();
  listFilters.model = $('#filter-model').value;
  listFilters.status = $('#filter-status').value;
}
function applyListFilters() {
  if (!ctx.isActive() || viewMode !== 'overview') return;
  syncListFilters(); writeCurrentRoute();
  ensureOverviewData(false);
}
function queryInput() {
  listFilters.q = $('#filter-q').value.trim();
  if (ctx.isActive() && viewMode === 'overview') writeCurrentRoute();
  clearTimeout(qTimer);
  qTimer = setTimeout(() => {
    qTimer = null;
    if (ctx.isActive() && viewMode === 'overview') ensureOverviewData(false);
  }, 350);
}
function ensureOverviewScopeButton() {
  const sidebar = ctx.sidebar;
  const list = sidebar?.querySelector('#team-bot-list');
  if (!sidebar || !list) return;
  let button = sidebar.querySelector('#usage-overview-scope');
  if (!button) {
    button = document.createElement('button');
    button.id = 'usage-overview-scope';
    button.type = 'button';
    button.className = 'quiet-button bot-select team-bot-item usage-overview-scope';
    button.setAttribute('aria-label', 'Tổng quan mọi bot');
    button.textContent = 'Tổng quan';
    button.hidden = true;
    const heading = sidebar.querySelector('.sessions-heading');
    if (heading) {
      const createBtn = heading.querySelector('#team-shared-create');
      if (createBtn) heading.insertBefore(button, createBtn);
      else heading.appendChild(button);
    } else {
      list.parentNode.insertBefore(button, list);
    }
    button.onclick = () => {
      if (sidebar.classList.contains('mobile-open') && typeof closeMobile === 'function') closeMobile();
      selectBot('');
    };
  }
  button.setAttribute('aria-pressed', String(!botScope));
}
function buildPage() {
  const dashTpl = document.querySelector('template#tpl-dashboard');
  $('.workspace').insertAdjacentHTML('beforeend', dashTpl ? dashTpl.innerHTML : '');
  ensureOverviewScopeButton();
  $('#range-apply').onclick = applyRange;
  $('#range-custom-toggle').onclick = () => { customRangeOpen = !customRangeOpen; syncRangeCustomUI(); };
  $('#usage-scope-card').addEventListener('click', event => {
    if (event.target?.closest?.('#scope-clear, .bot-scope-clear')) selectBot('');
  });
  $$('.range-presets .chip').forEach(button => button.onclick = () => setPreset(button.dataset.preset));
  $('#usage-view-tabs').onclick = event => {
    const button = event.target.closest('[data-usage-view]');
    if (button) selectUsageView(button.dataset.usageView);
  };
  $$('.seg-toggle .chip').forEach(button => button.onclick = () => {
    dayMetric = button.dataset.dayMetric;
    $$('.seg-toggle .chip').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
    renderDayChart();
  });
  $('#filter-model').onchange = $('#filter-status').onchange = applyListFilters;
  $('#filter-q').addEventListener('input', queryInput);
  $('#trace-refresh').onclick = () => ensureOverviewData(true);
  $('#stats-retry').onclick = () => { void loadStats(true); };
  $('#trace-retry').onclick = () => {
    const failed = traceState.failedRequest;
    void loadTraces(failed?.reset ?? true, true, failed?.offset ?? null);
  };
  $('#trace-more').onclick = () => { void loadTraces(false); };
  root.addEventListener('click', event => {
    const button = event.target.closest('button[data-action="select-bot"][data-bot-id]');
    if (button && root.contains(button)) selectBot(button.dataset.botId);
  });
  $('#trace-conversations').addEventListener('click', openTraceRow);
  $('#trace-conversations').addEventListener('keydown', event => { if (event.key === 'Enter') openTraceRow(event); });
  $('#detail-back').onclick = returnFromDetail;
  $('#detail-retry').onclick = () => {
    const selected = parseDetailRoute(ctx.route);
    if (selected) void loadDetail(selected, selected.turn, true);
  };
  $('#detail-refresh').onclick = () => {
    const selected = parseDetailRoute(ctx.route);
    if (selected) void loadDetail(selected, selected.turn, true);
  };
}
async function boot() {
  buildPage();
  readRoute(ctx.route, true);
  routeKey = ctx.route.search + ctx.route.hash;
  try { await ctx.shellReady; } catch (err) { toast('Không tải được đội bot: ' + (err.message || '')); }
  ctx.highlightBot(botScope); ctx.renderTeam(); updateScopeChip();
  const selected = parseDetailRoute(ctx.route);
  if (selected) { setView('detail'); void loadDetail(selected, selected.turn); }
  else { setView('overview'); ensureOverviewData(false); }
}
const ready = boot();
return { ready, selectBot, loadConversation,
  sidebarLabel(id, name, status) { return `Xem sử dụng của ${name}`; },
  onEnter() {
    const overviewButton = ctx.sidebar?.querySelector('#usage-overview-scope');
    if (overviewButton) overviewButton.hidden = false;
    if (viewMode === 'overview') ensureOverviewData(false);
  },
  onLeave() {
    clearTimeout(qTimer); qTimer = null;
    const overviewButton = ctx.sidebar?.querySelector('#usage-overview-scope');
    if (overviewButton) overviewButton.hidden = true;
  },
  onRoute(route) {
    const key = route.search + route.hash;
    if (key === routeKey) return;
    applyRoute(route);
  } };
} });
