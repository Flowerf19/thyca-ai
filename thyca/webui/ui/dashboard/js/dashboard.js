ThycaRouter.register('dashboard', { title: 'Sử dụng', stylesheet: '/ui/dashboard/dashboardcss/dashboard.css', mount(ctx) {
  let routeKey = '';
  const root = ctx.root;
  const $ = (selector, base = root) => selector === '.workspace' ? root : base.querySelector(selector);
  const $$ = (selector, base = root) => [...base.querySelectorAll(selector)];

const FIRST_PAGE = 15;
const MORE_PAGE = 50;
const MAX_DAY_COLUMNS = 62;
const MAX_MODEL_SERIES = 4;
const MAX_BOT_SERIES = 6;
const MODEL_COLORS = ['var(--series-0)', 'var(--series-1)', 'var(--series-2)', 'var(--series-3)'];
const OTHER_COLOR = 'var(--series-other)';
const UNKNOWN_BOT_COLOR = '#a08f68';
const USAGE_MODES = [
  { value: 'tokens:parts', label: 'Token · Input / Cache / Output', teamOnly: false },
  { value: 'tokens:model', label: 'Token · Theo model', teamOnly: false },
  { value: 'tokens:bot', label: 'Token · Theo bot', teamOnly: true },
  { value: 'requests:model', label: 'Lần gọi · Theo model', teamOnly: false },
  { value: 'requests:bot', label: 'Lần gọi · Theo bot', teamOnly: true },
];

let rangeFrom = '', rangeTo = '';
let rangeAll = false;
let botScope = '';
let dayMetric = 'tokens';
let tokenGroup = 'parts';
let requestGroup = 'model';
let costGroup = 'model';
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
  const showTitle = !!title && title !== name && title !== id;
  const conv = turn.kind === 'dm' ? dmKey(turn.bot_id) : groupKey(turn.group_id);
  const status = botScope && turn.status === 'failed' ? 'Lượt có lỗi' : null;
  const callsBit = `${fmtInt(turn.requests)} lần gọi` + (turn.tool_calls > 0 ? ` · ${fmtInt(turn.tool_calls)} tool` : '');
  return `<li><button type="button" class="tl-turn" data-conv="${escapeHTML(conv)}" data-turn="${turn.turn_index}"
      title="Mở lượt #${turn.turn_index} của “${escapeHTML(title)}”">
    <span class="tl-turn-idx">#${fmtInt(turn.turn_index)}</span>${face}<span class="tl-turn-main">
      <span class="tl-turn-line"><b>${escapeHTML(name)}</b>${showTitle ? `<span class="tl-turn-title">${escapeHTML(title)}</span>` : ''}<span class="tl-turn-kind">${kind}</span><span class="tl-turn-time">${escapeHTML(fmtDateTime(turn.started_at))}</span></span>
      <span class="tl-turn-sub"><span class="tl-turn-model">${escapeHTML(turn.model || '—')}</span><span>${fmtInt(turn.total_tokens)} token</span><span>${fmtCost(turn.cost_usd)}</span><span>${fmtDur(turn.latency_ms)}</span><span>${callsBit}</span></span>
    </span>${status ? `<span class="tl-chip s-error">${status}</span>` : statusChip(turn.status)}</button></li>`;
}
function updateRangeNote() {
  const nice = v => v ? new Date(v + 'T00:00:00').toLocaleDateString('vi-VN', { day: 'numeric', month: 'numeric', year: 'numeric' }) : '…';
  $('#range-note').textContent = rangeAll ? 'Tất cả' : 'Kỳ ' + nice(rangeFrom) + ' – ' + nice(rangeTo);
}
function updatePresetButtons() {
  const today = new Date();
  const preset = days => { const start = new Date(today); start.setDate(start.getDate() - (days - 1)); return [isoDay(start), isoDay(today)]; };
  $$('.range-presets .chip').forEach(button => {
    const days = Number(button.dataset.preset);
    const [pFrom, pTo] = days ? preset(days) : ['', ''];
    const active = !rangeAll && days > 0 && pFrom === rangeFrom && pTo === rangeTo;
    button.setAttribute('aria-pressed', String(!!active));
  });
}
function setPreset(days) {
  const n = Number(days);
  if (![1, 7, 30].includes(n)) return;
  const to = new Date(), from = new Date();
  from.setDate(from.getDate() - (n - 1));
  rangeFrom = isoDay(from); rangeTo = isoDay(to); rangeAll = false;
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
}
function updateViewShell() {
  const labels = { usage: 'Sử dụng', cost: 'Chi phí', trace: 'Trace' };
  $('#usage-scope-eyebrow').textContent = botScope ? botDisplayName(botScope) : 'Tổng quan';
  $('#usage-view-title').textContent = labels[activeView] || labels.usage;
  $('#detail-back-label').textContent = labels[activeView] || labels.usage;
  updateChartControls();
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

function effectiveUsageGroup() {
  const raw = dayMetric === 'tokens' ? tokenGroup : requestGroup;
  if (dayMetric === 'requests' && raw === 'parts') return 'model';
  if (botScope && raw === 'bot') return dayMetric === 'tokens' ? 'parts' : 'model';
  return raw;
}
function effectiveCostGroup() {
  return botScope ? 'model' : costGroup;
}
function isSingleDayRange() {
  return !rangeAll && !!rangeFrom && !!rangeTo && rangeFrom === rangeTo;
}
function usageChartModeValue() {
  return `${dayMetric}:${effectiveUsageGroup()}`;
}
function updateChartControls() {
  const modeSel = $('#usage-chart-mode');
  if (modeSel) {
    const allowed = USAGE_MODES.filter(item => !botScope || !item.teamOnly);
    modeSel.replaceChildren(
      ...allowed.map(item => Object.assign(document.createElement('option'), { value: item.value, textContent: item.label })),
    );
    modeSel.value = usageChartModeValue();
  }
  const costSel = $('#cost-chart-mode');
  if (costSel) {
    costSel.hidden = !!botScope;
    costSel.value = effectiveCostGroup();
  }
}
function setUsageChartMode(value) {
  const mode = USAGE_MODES.find(item => item.value === value && (!botScope || !item.teamOnly));
  if (!mode) return;
  const [metric, group] = mode.value.split(':');
  dayMetric = metric;
  if (metric === 'tokens') tokenGroup = group;
  else requestGroup = group;
  updateChartControls();
  updateUsageChartHeader();
  renderDayChart();
}
function setDayMetric(metric) {
  if (!['tokens', 'requests'].includes(metric)) return;
  dayMetric = metric;
  updateChartControls();
  updateUsageChartHeader();
  renderDayChart();
}
function setUsageGroup(group) {
  if (!['parts', 'model', 'bot'].includes(group)) return;
  if (dayMetric === 'requests' && group === 'parts') return;
  if (botScope && group === 'bot') return;
  if (dayMetric === 'tokens') tokenGroup = group;
  else requestGroup = group;
  updateChartControls();
  renderDayChart();
}
function setCostGroup(group) {
  if (!['model', 'bot'].includes(group)) return;
  if (botScope && group === 'bot') return;
  costGroup = group;
  updateChartControls();
  renderCostChart();
}
function chartUnit(data = statsData) {
  if (!isSingleDayRange()) return 'day';
  if (!data) return 'hour';
  const hasHour = (data.by_hour || []).length > 0;
  const hasDay = (data.by_day || []).length > 0;
  return hasHour || !hasDay ? 'hour' : 'day';
}
function updateUsageChartHeader(totals = statsData?.totals || null, data = statsData) {
  const t = totals;
  const titleEl = $('#usage-chart-title'), totalEl = $('#usage-chart-total'), subEl = $('#usage-chart-sub');
  const isTokens = dayMetric === 'tokens';
  const unitLabel = chartUnit(data) === 'hour' ? 'theo giờ' : 'theo ngày';
  if (titleEl) titleEl.textContent = (isTokens ? 'Token ' : 'Lần gọi ') + unitLabel;
  if (!t) {
    if (totalEl) totalEl.textContent = '—';
    if (subEl) { subEl.textContent = ''; subEl.hidden = true; }
    return;
  }
  if (totalEl) totalEl.textContent = isTokens ? fmtInt(t.total_tokens) : fmtInt(t.requests);
  if (subEl) {
    const sub = !isTokens && typeof t.turns === 'number' ? `${fmtInt(t.turns)} lượt` : '';
    subEl.textContent = sub;
    subEl.hidden = !sub;
  }
}
function updateCostChartHeader(totals = statsData?.totals || null, data = statsData) {
  const titleEl = $('#cost-chart-title'), totalEl = $('#cost-chart-total'), cov = $('#cost-coverage');
  if (titleEl) titleEl.textContent = chartUnit(data) === 'hour' ? 'Chi phí theo giờ' : 'Chi phí theo ngày';
  if (!totals) {
    if (totalEl) totalEl.textContent = '—';
    if (cov) { cov.textContent = ''; cov.hidden = true; }
    return;
  }
  if (totalEl) totalEl.textContent = fmtCost(totals.cost_usd);
  if (!cov) return;
  if (totals.cost_usd == null) {
    cov.textContent = 'Chưa có giá';
    cov.hidden = false;
  } else if (typeof totals.priced_turns === 'number' && typeof totals.turns === 'number' && totals.priced_turns < totals.turns) {
    cov.textContent = `${fmtInt(totals.priced_turns)}/${fmtInt(totals.turns)} lượt có giá`;
    cov.hidden = false;
  } else {
    cov.textContent = '';
    cov.hidden = true;
  }
}
function renderTokenPartsLegend(box, totals) {
  if (!box) return;
  const t = totals || {};
  const inTok = typeof t.prompt_tokens === 'number'
    ? Math.max(0, t.prompt_tokens - (typeof t.cached_tokens === 'number' ? t.cached_tokens : 0))
    : t.prompt_tokens;
  const sum = t.total_tokens;
  const partCard = (key, cls, label, id, val, tip, note) => {
    const pct = share(val, sum);
    const pctHtml = pct !== '—' ? `<span class="legend-pct">${pct}</span>` : '';
    return `<span class="legend-item" data-series-key="${key}" title="${tip}"><i class="legend-swatch ${cls}"></i><span class="legend-body"><span class="legend-top"><span class="legend-label">${label}</span><span class="legend-value"><strong id="${id}">${fmtInt(val)}</strong>${pctHtml}</span></span><span class="legend-note">${note}</span></span></span>`;
  };
  box.innerHTML =
    partCard('input', 'legend-input', 'Input', 'usage-input-total', inTok, 'Không bao gồm Cache read.', 'Prompt mới, chưa gồm Cache')
    + partCard('cache', 'legend-cache', 'Cache', 'usage-cache-total', t.cached_tokens, 'Tính riêng, không nằm trong Input.', 'Đọc lại từ bộ nhớ đệm')
    + partCard('output', 'legend-output', 'Output', 'usage-output-total', t.completion_tokens, 'Token hoàn tất từ model.', 'Token model sinh ra');
}
function renderStats(s, { loading = false } = {}) {
  updateChartControls();
  if (loading) {
    updateUsageChartHeader(null, null);
    updateCostChartHeader(null, null);
    if (dayMetric === 'tokens' && effectiveUsageGroup() === 'parts') renderTokenPartsLegend($('#usage-chart-legend'), null);
    else $('#usage-chart-legend').innerHTML = '';
    $('#cost-chart-legend').innerHTML = '';
    for (const id of ['usage-chart-summary', 'cost-chart-summary']) {
      const el = $(`#${id}`);
      if (el) { el.textContent = ''; el.hidden = true; }
    }
    const traceSum = $('#trace-head-summary');
    if (traceSum) { traceSum.textContent = ''; traceSum.removeAttribute?.('title'); }
    for (const id of ['chart-day', 'chart-cost']) $(`#${id}`).innerHTML = '';
    fillModelFilter(s.models || []);
    fillStatusFilter(s.by_status || []);
    updateScopeChip();
    return;
  }
  const t = s.totals || {};
  updateUsageChartHeader(t, s);
  updateCostChartHeader(t, s);
  const traceSum = $('#trace-head-summary');
  if (traceSum) {
    const bits = [];
    if (typeof t.turns === 'number') bits.push(`${fmtInt(t.turns)} lượt`);
    else if (typeof t.requests === 'number') bits.push(`${fmtInt(t.requests)} lần gọi`);
    if (typeof t.latency_ms_p50 === 'number') bits.push(`~${fmtDur(t.latency_ms_p50)}`);
    traceSum.textContent = bits.join(' · ');
    if (typeof t.latency_ms_p50 === 'number') {
      const tip = `Trung vị (p50): ${fmtDur(t.latency_ms_p50)}` + (typeof t.latency_ms_p90 === 'number' ? ` · p90: ${fmtDur(t.latency_ms_p90)}` : '');
      traceSum.title = tip;
    } else {
      traceSum.removeAttribute?.('title');
    }
  }
  renderDayChart(s); renderCostChart(s);
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
        days.push(map.get(key) || { day: key, requests: 0, prompt_tokens: 0, cached_tokens: 0, completion_tokens: 0, cost_usd: 0 });
      }
      return days;
    }
  }
  return byDay;
}
function chartBuckets(data = statsData) {
  if (chartUnit(data) === 'hour') {
    const byHour = data?.by_hour || [];
    const map = new Map(byHour.map(h => [h.hour, h]));
    const cols = [];
    for (let h = 0; h < 24; h++) {
      const key = String(h).padStart(2, '0');
      cols.push(map.get(key) || {
        hour: key, requests: 0, prompt_tokens: 0, cached_tokens: 0,
        completion_tokens: 0, total_tokens: 0, cost_usd: 0, by_model: [], by_bot: [],
      });
    }
    return { unit: 'hour', cols, hasAny: byHour.length > 0 };
  }
  const rawDays = data?.by_day || [];
  const cols = dayColumns(rawDays);
  return { unit: 'day', cols, hasAny: rawDays.length > 0 };
}
function dayTokenParts(day) {
  if (![day.prompt_tokens, day.cached_tokens, day.completion_tokens].every(value => typeof value === 'number' && Number.isFinite(value))) return null;
  return { input: Math.max(day.prompt_tokens - day.cached_tokens, 0), cache: Math.max(day.cached_tokens, 0), output: Math.max(day.completion_tokens, 0) };
}
function colTotalTokens(col) {
  if (typeof col.total_tokens === 'number' && Number.isFinite(col.total_tokens)) return col.total_tokens;
  const parts = dayTokenParts(col);
  return parts ? parts.input + parts.cache + parts.output : null;
}
function sumMetric(rows, field) {
  if (field === 'cost_usd') {
    const priced = rows.filter(r => typeof r.cost_usd === 'number' && Number.isFinite(r.cost_usd));
    return priced.length ? priced.reduce((acc, r) => acc + r.cost_usd, 0) : null;
  }
  return rows.reduce((acc, r) => acc + (typeof r[field] === 'number' && Number.isFinite(r[field]) ? r[field] : 0), 0);
}
function buildSeries(group, metricField, data = statsData) {
  if (group === 'model') {
    const raw = data?.by_model || [];
    const named = raw.filter(row => row.model && row.model !== 'unknown').slice().sort((a, b) =>
      ((b[metricField] ?? -1) - (a[metricField] ?? -1))
      || ((b.total_tokens ?? 0) - (a.total_tokens ?? 0))
      || String(a.model).localeCompare(String(b.model)));
    const unknownRow = raw.find(row => row.model === 'unknown') || null;
    if (!named.length) {
      if (!unknownRow) return [];
      return [{
        key: '__unidentified__',
        label: 'Không rõ model',
        color: OTHER_COLOR,
        value: unknownRow[metricField],
        keys: new Set(['unknown']),
        row: unknownRow,
        clickable: false,
      }];
    }
    const top = named.slice(0, MAX_MODEL_SERIES);
    const tail = named.slice(MAX_MODEL_SERIES);
    const series = top.map((row, index) => ({
      key: row.model,
      label: row.model,
      color: MODEL_COLORS[index % MODEL_COLORS.length],
      value: row[metricField],
      row,
      clickable: false,
    }));
    const otherRows = [...tail, ...(unknownRow ? [unknownRow] : [])];
    if (otherRows.length) {
      const otherVal = sumMetric(otherRows, metricField);
      if (tail.length || (typeof otherVal === 'number' && otherVal > 0)) {
        series.push({
          key: '__other__',
          label: 'Khác',
          color: OTHER_COLOR,
          value: otherVal,
          keys: new Set(otherRows.map(r => r.model)),
          rows: otherRows,
          clickable: false,
        });
      }
    }
    return series;
  }
  const raw = (data?.by_bot || []).slice().sort((a, b) =>
    ((b[metricField] ?? -1) - (a[metricField] ?? -1))
    || ((b.total_tokens ?? 0) - (a.total_tokens ?? 0))
    || String(a.bot_id || '').localeCompare(String(b.bot_id || '')));
  const top = raw.slice(0, MAX_BOT_SERIES);
  const tail = raw.slice(MAX_BOT_SERIES);
  const series = top.map(row => {
    const bot = row.bot_id && row.bot_id !== 'unknown' && getBot(row.bot_id);
    return {
      key: row.bot_id || 'unknown',
      label: botDisplayName(row.bot_id),
      color: bot ? bot.color : UNKNOWN_BOT_COLOR,
      value: row[metricField],
      row,
      bot: bot || null,
      clickable: !!bot,
    };
  });
  if (tail.length) {
    series.push({
      key: '__other__',
      label: 'Khác',
      color: OTHER_COLOR,
      value: sumMetric(tail, metricField),
      keys: new Set(tail.map(r => r.bot_id || 'unknown')),
      rows: tail,
      clickable: false,
    });
  }
  return series;
}
function bucketSeriesValues(col, group, metricField, series) {
  const entries = group === 'model' ? col.by_model : col.by_bot;
  if (!Array.isArray(entries) || !entries.length || !series.length) return null;
  const idField = group === 'model' ? 'model' : 'bot_id';
  const knownKeys = new Set(series.filter(item => !item.keys).map(item => item.key));
  const map = new Map();
  let otherSum = 0;
  for (const entry of entries) {
    const key = entry[idField] || 'unknown';
    const raw = entry[metricField];
    const value = typeof raw === 'number' && Number.isFinite(raw) && raw > 0 ? raw : 0;
    if (knownKeys.has(key)) map.set(key, (map.get(key) || 0) + value);
    else otherSum += value;
  }
  return series.map(item => ({
    series: item,
    value: item.keys ? otherSum : (map.get(item.key) || 0),
  }));
}
function seriesLegendTip(item, group, metricField) {
  const bits = [];
  if (group === 'bot' && item.clickable) bits.push(`Xem sử dụng của ${item.label}`);
  else bits.push(item.label);
  const row = item.row;
  if (row) {
    if (metricField === 'total_tokens' && typeof row.total_tokens === 'number') bits.push(`${fmtInt(row.total_tokens)} token`);
    if (typeof row.turns === 'number') bits.push(`${fmtInt(row.turns)} lượt`);
    if (typeof row.requests === 'number') bits.push(`${fmtInt(row.requests)} lần gọi`);
    if (metricField === 'cost_usd') bits.push(fmtCost(row.cost_usd));
    if (typeof row.latency_ms_p50 === 'number') bits.push(`~${fmtDur(row.latency_ms_p50)}`);
  } else if (item.rows) {
    bits.push(`${item.rows.length} mục`);
  }
  return bits.join(' · ');
}
function seriesLegendNote(item, group, metricField) {
  const row = item.row;
  if (!row) return item.rows ? `${item.rows.length} mục khác` : '';
  const bits = [];
  if (group === 'model') {
    if (metricField === 'total_tokens') {
      if (typeof row.requests === 'number') bits.push(`${fmtInt(row.requests)} lần gọi`);
      if (typeof row.latency_ms_p50 === 'number') bits.push(`~${fmtDur(row.latency_ms_p50)}`);
    } else if (metricField === 'requests') {
      if (typeof row.total_tokens === 'number') bits.push(`${fmtCompact(row.total_tokens)} token`);
      if (typeof row.latency_ms_p50 === 'number') bits.push(`~${fmtDur(row.latency_ms_p50)}`);
    } else {
      if (typeof row.requests === 'number') bits.push(`${fmtInt(row.requests)} lần gọi`);
      if (typeof row.total_tokens === 'number') bits.push(`${fmtCompact(row.total_tokens)} token`);
    }
  } else {
    if (typeof row.turns === 'number') bits.push(`${fmtInt(row.turns)} lượt`);
    if (metricField === 'requests') {
      if (typeof row.total_tokens === 'number') bits.push(`${fmtCompact(row.total_tokens)} token`);
    } else if (metricField === 'cost_usd') {
      if (typeof row.total_tokens === 'number') bits.push(`${fmtCompact(row.total_tokens)} token`);
      else if (typeof row.requests === 'number') bits.push(`${fmtInt(row.requests)} lần gọi`);
    } else if (typeof row.requests === 'number') {
      bits.push(`${fmtInt(row.requests)} lần gọi`);
    }
  }
  return bits.join(' · ');
}
function renderSeriesLegend(box, { group, metricField, series, total, formatValue, emptyNote = '', data = statsData }) {
  if (!box) return;
  if (!data) { box.innerHTML = ''; return; }
  if (!series.length) {
    box.innerHTML = emptyNote ? `<span class="legend-empty">${escapeHTML(emptyNote)}</span>` : '';
    return;
  }
  box.innerHTML = series.map(item => {
    const val = formatValue(item.value);
    const pct = share(item.value, total);
    const pctHtml = pct !== '—' ? `<span class="legend-pct">${pct}</span>` : '';
    const note = seriesLegendNote(item, group, metricField);
    const tip = seriesLegendTip(item, group, metricField);
    const inner = `<i class="legend-swatch" style="background:${item.color}"></i><span class="legend-body"><span class="legend-top"><span class="legend-label">${escapeHTML(item.label)}</span><span class="legend-value"><strong>${val}</strong>${pctHtml}</span></span>${note ? `<span class="legend-note">${escapeHTML(note)}</span>` : ''}</span>`;
    if (item.clickable && item.bot) {
      return `<button type="button" class="legend-item legend-bot-btn" data-action="select-bot" data-bot-id="${escapeHTML(item.bot.id)}" data-series-key="${escapeHTML(item.key)}" title="${escapeHTML(tip)}" aria-label="Xem sử dụng của ${escapeHTML(item.bot.name)}">${inner}</button>`;
    }
    return `<span class="legend-item" data-series-key="${escapeHTML(item.key)}" title="${escapeHTML(tip)}">${inner}</span>`;
  }).join('');
}
function fmtAxisValue(value, kind) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '';
  if (kind === 'cost') {
    if (value === 0) return '$0';
    return '$' + value.toFixed(value >= 10 ? 0 : value >= 1 ? 2 : value >= 0.01 ? 3 : 4);
  }
  if (value >= 1e6) return (value / 1e6).toLocaleString('vi-VN', { maximumFractionDigits: 1 }) + 'm';
  if (value >= 1e3) return (value / 1e3).toLocaleString('vi-VN', { maximumFractionDigits: value >= 1e4 ? 0 : 1 }) + 'k';
  return fmtInt(Math.round(value));
}
function chartFrameHtml({ chartHtml, max, kind }) {
  const yAxis = max > 0
    ? `<div class="chart-yaxis" aria-hidden="true"><span>${fmtAxisValue(max, kind)}</span><span>${fmtAxisValue(max / 2, kind)}</span><span>${kind === 'cost' ? '$0' : '0'}</span></div>`
    : `<div class="chart-yaxis" aria-hidden="true"><span></span><span></span><span>${kind === 'cost' ? '$0' : '0'}</span></div>`;
  return `<div class="chart-frame">${yAxis}<div class="chart-plot"><div class="chart-grid" aria-hidden="true"><i></i><i></i><i></i></div>${chartHtml}</div></div>`;
}
function updateChartSummary(el, { cols, values, unit, kind, data, group = '', isTokens = true }) {
  if (!el) return;
  const bits = [];
  let maxVal = 0, peakIdx = -1, activeCount = 0, activeSum = 0;
  values.forEach((val, idx) => {
    if (typeof val === 'number' && Number.isFinite(val) && val > 0) {
      activeCount++;
      activeSum += val;
      if (val > maxVal) { maxVal = val; peakIdx = idx; }
    }
  });
  if (peakIdx >= 0) {
    const col = cols[peakIdx];
    const peakLabel = unit === 'hour' ? `${col.hour}:00–${col.hour}:59` : fmtDay(col.day);
    const peakText = kind === 'cost'
      ? fmtCost(maxVal)
      : isTokens ? `${fmtAxisValue(maxVal, 'num')} token` : `${fmtInt(maxVal)} lần gọi`;
    bits.push(`Cao nhất: ${peakLabel} (${peakText})`);
  }
  const t = data?.totals || {};
  if (kind === 'cost') {
    if (typeof t.cost_usd === 'number' && typeof t.requests === 'number' && t.requests > 0) {
      bits.push(`TB ${fmtCost(t.cost_usd / t.requests)} / lần gọi`);
    }
  } else if (group === 'parts' && typeof t.prompt_tokens === 'number' && t.prompt_tokens > 0 && typeof t.cached_tokens === 'number') {
    bits.push(`Tỷ lệ cache: ${share(t.cached_tokens, t.prompt_tokens)}`);
  } else if (activeCount > 1) {
    const avg = activeSum / activeCount;
    const avgText = isTokens ? `${fmtAxisValue(avg, 'num')} token` : `${fmtInt(Math.round(avg))} lần gọi`;
    bits.push(`TB ${unit === 'hour' ? 'giờ' : 'ngày'} hoạt động: ${avgText}`);
  }
  el.textContent = bits.join(' · ');
  el.hidden = !bits.length;
}
function bindLegendHighlight(legendEl, chartEl) {
  if (!legendEl || !chartEl || typeof legendEl.addEventListener !== 'function') return;
  const apply = key => {
    const host = chartEl;
    if (!key) {
      host.removeAttribute?.('data-hl-series');
      host.querySelectorAll?.('.is-hl').forEach(el => el.classList.remove('is-hl'));
      return;
    }
    host.setAttribute?.('data-hl-series', key);
    host.querySelectorAll?.('[data-series-key]').forEach(el =>
      el.classList.toggle('is-hl', el.dataset.seriesKey === key));
  };
  legendEl.addEventListener('pointerover', event => {
    const item = event.target?.closest?.('[data-series-key]');
    if (item) apply(item.dataset.seriesKey);
  });
  legendEl.addEventListener('pointerleave', () => apply(''));
}
function bucketLabel(col, unit) {
  return unit === 'hour' ? `${col.hour}:00–${col.hour}:59` : fmtDayFull(col.day);
}
function axisLabel(col, unit, index, totalLen, step) {
  if (unit === 'hour') return (Number(col.hour) % 6 === 0 || col.hour === '23') ? col.hour + 'h' : '';
  return (index % step === 0 || index === totalLen - 1) ? fmtDay(col.day) : '';
}
function stackedBarsHtml(segments, total, max, fallbackClass) {
  if (total === null) return '<i class="dc-unavailable">—</i>';
  if (!total) return `<i class="dc-bar ${fallbackClass} is-zero" style="height:0%"></i>`;
  const pct = value => max ? (value / max) * 100 : 0;
  const active = segments ? segments.filter(seg => seg.value > 0) : [];
  if (!active.length) return `<i class="dc-bar ${fallbackClass}" style="height:${pct(total)}%"></i>`;
  return active.slice().reverse().map(seg =>
    `<i class="dc-bar dc-seg-series" data-series-key="${escapeHTML(seg.series.key)}" style="height:${pct(seg.value)}%;background:${seg.series.color}"></i>`
  ).join('');
}
function renderDayChart(data = statsData) {
  const box = $('#chart-day');
  const legendBox = $('#usage-chart-legend');
  const summaryEl = $('#usage-chart-summary');
  if (!data) {
    box.innerHTML = '';
    if (summaryEl) { summaryEl.textContent = ''; summaryEl.hidden = true; }
    return;
  }
  const group = effectiveUsageGroup();
  const isTokens = dayMetric === 'tokens';
  const metricField = isTokens ? 'total_tokens' : 'requests';
  const series = group === 'parts' ? [] : buildSeries(group, metricField, data);
  if (group === 'parts') {
    renderTokenPartsLegend(legendBox, data.totals || {});
  } else {
    renderSeriesLegend(legendBox, {
      group,
      metricField,
      series,
      total: data.totals?.[metricField],
      formatValue: isTokens ? fmtCompact : fmtInt,
      emptyNote: group === 'model' && (data.by_model || []).length ? 'Không có model xác định trong kỳ.' : '',
      data,
    });
  }
  const { unit, cols, hasAny } = chartBuckets(data);
  if (!hasAny) {
    box.innerHTML = '<div class="empty-note"><strong>Chưa có lượt nào trong kỳ.</strong>Các cột sẽ xuất hiện khi có phiên.</div>';
    if (summaryEl) { summaryEl.textContent = ''; summaryEl.hidden = true; }
    return;
  }
  const partsList = group === 'parts' ? cols.map(dayTokenParts) : null;
  const totals = cols.map((col, index) => {
    if (group === 'parts') return partsList[index] ? partsList[index].input + partsList[index].cache + partsList[index].output : null;
    if (isTokens) return colTotalTokens(col);
    return typeof col.requests === 'number' && Number.isFinite(col.requests) ? col.requests : null;
  });
  const max = Math.max(0, ...totals.filter(value => value !== null));
  const step = Math.max(1, Math.ceil(cols.length / 15));
  const denseCls = unit === 'hour' ? ' is-hourly' : cols.length > 14 ? ' is-compact' : '';
  const ariaLabel = `${isTokens ? 'Token' : 'Lần gọi AI'} ${unit === 'hour' ? 'theo giờ' : 'theo ngày'}`;
  const chartHtml = `<div class="day-chart${denseCls}" role="img" aria-label="${ariaLabel}">` + cols.map((col, index) => {
    const total = totals[index], pct = value => max && total !== null ? (value / max) * 100 : 0;
    const head = bucketLabel(col, unit);
    let bars, tip;
    if (group === 'parts') {
      const parts = partsList[index];
      tip = `${head} · Input ${fmtInt(col.prompt_tokens)} (Cache ${fmtInt(col.cached_tokens)}) · Output ${fmtInt(col.completion_tokens)}`;
      bars = total === null ? '<i class="dc-unavailable">—</i>'
        : total === 0 ? '<i class="dc-bar dc-bar-tokens is-zero" style="height:0%"></i>'
        : `<i class="dc-bar dc-seg-output" data-series-key="output" style="height:${pct(parts.output)}%"></i><i class="dc-bar dc-seg-cache" data-series-key="cache" style="height:${pct(parts.cache)}%"></i><i class="dc-bar dc-seg-input" data-series-key="input" style="height:${pct(parts.input)}%"></i>`;
    } else {
      const segments = bucketSeriesValues(col, group, metricField, series);
      const breakdown = segments ? segments.filter(seg => seg.value > 0).map(seg => `${seg.series.label} ${fmtInt(seg.value)}`) : [];
      const unitWord = isTokens ? 'token' : 'lần gọi';
      tip = `${head} · ${fmtInt(total)} ${unitWord}${breakdown.length ? ' · ' + breakdown.join(' · ') : ''}`;
      bars = stackedBarsHtml(segments, total, max, isTokens ? 'dc-bar-tokens' : 'dc-bar-requests');
    }
    return `<div class="dc-col${total === null ? ' is-unavailable' : ''}" title="${escapeHTML(tip)}"><span class="dc-track">${bars}</span><span class="dc-day">${axisLabel(col, unit, index, cols.length, step)}</span></div>`;
  }).join('') + '</div>';
  box.innerHTML = chartFrameHtml({ chartHtml, max, kind: 'num' });
  updateChartSummary(summaryEl, { cols, values: totals, unit, kind: 'num', data, group, isTokens });
}
function renderCostChart(data = statsData) {
  const box = $('#chart-cost');
  const legendBox = $('#cost-chart-legend');
  const summaryEl = $('#cost-chart-summary');
  if (!data) {
    box.innerHTML = '';
    if (legendBox) legendBox.innerHTML = '';
    if (summaryEl) { summaryEl.textContent = ''; summaryEl.hidden = true; }
    return;
  }
  const group = effectiveCostGroup();
  const series = buildSeries(group, 'cost_usd', data);
  renderSeriesLegend(legendBox, {
    group,
    metricField: 'cost_usd',
    series,
    total: data.totals?.cost_usd,
    formatValue: fmtCost,
    emptyNote: group === 'model' && (data.by_model || []).length ? 'Không có model xác định trong kỳ.' : '',
    data,
  });
  const { unit, cols, hasAny } = chartBuckets(data);
  if (!hasAny) {
    box.innerHTML = '<div class="empty-note"><strong>Chưa có lượt nào trong kỳ.</strong></div>';
    if (summaryEl) { summaryEl.textContent = ''; summaryEl.hidden = true; }
    return;
  }
  const values = cols.map(col => typeof col.cost_usd === 'number' && Number.isFinite(col.cost_usd) ? col.cost_usd : null);
  const max = Math.max(0, ...values.filter(value => value !== null));
  const step = Math.max(1, Math.ceil(cols.length / 15));
  const denseCls = unit === 'hour' ? ' is-hourly' : cols.length > 14 ? ' is-compact' : '';
  const ariaLabel = unit === 'hour' ? 'Chi phí theo giờ' : 'Chi phí theo ngày';
  const chartHtml = `<div class="day-chart cost-day-chart${denseCls}" role="img" aria-label="${ariaLabel}">` + cols.map((col, index) => {
    const value = values[index];
    const segments = value ? bucketSeriesValues(col, group, 'cost_usd', series) : null;
    const breakdown = segments ? segments.filter(seg => seg.value > 0).map(seg => `${seg.series.label} ${fmtCost(seg.value)}`) : [];
    const partial = typeof col.priced_turns === 'number' && typeof col.turns === 'number' && col.priced_turns < col.turns
      ? ` (${fmtInt(col.priced_turns)}/${fmtInt(col.turns)} lượt có giá)`
      : '';
    const inactive = value === 0 && !col.requests && !col.turns;
    const costLabel = value === null
      ? `${col.requests ? fmtInt(col.requests) + ' lần gọi · ' : ''}chưa có giá`
      : inactive ? 'không có hoạt động' : fmtCost(value) + partial;
    const tip = `${bucketLabel(col, unit)} · ${costLabel}${breakdown.length ? ' · ' + breakdown.join(' · ') : ''}`;
    const bars = stackedBarsHtml(segments, value, max, 'dc-bar-cost');
    return `<div class="dc-col${value === null ? ' is-unavailable' : ''}" title="${escapeHTML(tip)}"><span class="dc-track">${bars}</span><span class="dc-day">${axisLabel(col, unit, index, cols.length, step)}</span></div>`;
  }).join('') + '</div>';
  box.innerHTML = chartFrameHtml({ chartHtml, max, kind: 'cost' });
  updateChartSummary(summaryEl, { cols, values, unit, kind: 'cost', data, group });
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
function buildPage() {
  const dashTpl = document.querySelector('template#tpl-dashboard');
  $('.workspace').insertAdjacentHTML('beforeend', dashTpl ? dashTpl.innerHTML : '');
  $('#usage-scope-card').addEventListener('click', event => {
    if (event.target?.closest?.('#scope-clear, .bot-scope-clear')) selectBot('');
  });
  $$('.range-presets .chip').forEach(button => button.onclick = () => setPreset(button.dataset.preset));
  $('#usage-view-tabs').onclick = event => {
    const button = event.target.closest('[data-usage-view]');
    if (button) selectUsageView(button.dataset.usageView);
  };
  $('#usage-chart-mode').onchange = event => setUsageChartMode(event.target.value);
  $('#cost-chart-mode').onchange = event => setCostGroup(event.target.value);
  bindLegendHighlight($('#usage-chart-legend'), $('#chart-day'));
  bindLegendHighlight($('#cost-chart-legend'), $('#chart-cost'));
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
    if (viewMode === 'overview') ensureOverviewData(false);
  },
  onLeave() {
    clearTimeout(qTimer); qTimer = null;
  },
  onRoute(route) {
    const key = route.search + route.hash;
    if (key === routeKey) return;
    applyRoute(route);
  } };
} });
