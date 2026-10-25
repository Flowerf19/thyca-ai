// Retained Usage controller; declarations are isolated in this mount closure.
ThycaRouter.register('dashboard', { title: 'Sử dụng', stylesheet: '/ui/dashboard/dashboardcss/dashboard.css', mount(ctx) {
  let routeKey = '';
  const root = ctx.root;
  const $ = (selector, base = root) => selector === '.workspace' ? root : base.querySelector(selector);
  const $$ = (selector, base = root) => [...base.querySelectorAll(selector)];
// Usage, cost, and flat trace views with an in-workspace full-turn detail.
//
// API: GET /api/traces/stats?from&to                      -> totals + by_model/by_bot/by_day/by_hour/by_status + models
//      GET /api/traces?from&to&limit&offset&q&model&status -> {traces, total} paged, newest first
//      GET /api/bots                                    -> share/data.js via bootShell()
// A sidebar bot scope filters to explicit bot_breakdown attribution; direct
// bot_id is the server-verified fallback only when no breakdown exists.


const FIRST_PAGE = 15;
const MORE_PAGE = 50;
const MAX_DAY_COLUMNS = 62;
const MAX_SESSIONS = 200;

let rangeFrom = '', rangeTo = '';
let rangeAll = false;
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

/* ---------- formatters (vi-VN numbers; cost_usd is USD so it keeps the $ sign) ---------- */
const fmtInt = n => (typeof n === 'number' && Number.isFinite(n)) ? n.toLocaleString('vi-VN') : '—';
// Compact counts for hint lines: only millions get the "m" suffix
// (7.149.853 -> "7,1m"); smaller counts stay exact (242.460).
const fmtCompact = n => (typeof n !== 'number' || !Number.isFinite(n)) ? '—'
  : n >= 1e6 ? (n / 1e6).toLocaleString('vi-VN', { maximumFractionDigits: 1 }) + 'm'
  : fmtInt(n);
// Same rule as the old prototype's fmtCost: 6 decimals under $0.01, 4 under $1, else 2.
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
const fmtDay = key => key ? key.slice(8, 10) + '/' + key.slice(5, 7) : '—';   // 'YYYY-MM-DD' -> 'dd/mm'
function fmtDayFull(key) {                                                    // tooltip: 'Th 3, 21/10'
  const d = new Date(key + 'T00:00:00Z');                                     // by_day keys are UTC days
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
// Old prototype's share(): one decimal under 10%, integer above.
function share(num, den) {
  if (typeof num !== 'number' || !Number.isFinite(num) || typeof den !== 'number' || !Number.isFinite(den)) return '—';
  if (!den) return num === 0 ? '0%' : '—';
  const p = (num / den) * 100;
  return (p >= 10 ? Math.round(p) : Math.round(p * 10) / 10).toLocaleString('vi-VN') + '%';
}
const isoDay = d => d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');

/* ---------- status chips (TurnSummary.status: completed | failed | loop_limit | context_limit) ---------- */
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

/* Avatar slot: real seal when the bot is in the roster, a dashed chip with the
   id's initials otherwise (the raw id still shows as the row name — spec:
   bots missing from the roster render as plain id text). 'expression-preview'
   keeps list avatars static — they stay out of the blink/sync loops. */
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
  const kind = turn.session_kind === 'dm' ? 'Chat riêng' : turn.session_kind === 'group' ? 'Chat nhóm' : 'Phiên';
  const title = turn.title || turn.session_id || '—';
  const status = botScope && turn.status === 'failed' ? 'Lượt có lỗi' : null;
  return `<li><button type="button" class="tl-turn" data-session="${escapeHTML(turn.session_id)}" data-turn="${turn.turn_index}"
      title="Mở lượt #${turn.turn_index} của “${escapeHTML(title)}”">
    <span class="tl-turn-idx">#${fmtInt(turn.turn_index)}</span>${face}<span class="tl-turn-main">
      <span class="tl-turn-line"><b>${escapeHTML(name)}</b><span class="tl-turn-title">${escapeHTML(title)}</span></span>
      <span class="tl-turn-sub"><span>${kind}</span><span>${escapeHTML(fmtDateTime(turn.started_at))}</span><span>${escapeHTML(turn.model || '—')}</span><span>${fmtInt(turn.requests)} gọi model</span><span>${fmtInt(turn.tool_calls)} gọi tool</span><span>${fmtInt(turn.total_tokens)} token</span><span>${fmtCost(turn.cost_usd)}</span><span>${fmtDur(turn.latency_ms)}</span></span>
    </span>${status ? `<span class="tl-chip s-error">${status}</span>` : statusChip(turn.status)}</button></li>`;
}
const PAGE_HTML = `
<header class="page-nav">
  <div class="page-title"><p class="eyebrow" id="usage-scope-eyebrow">Tổng quan</p><h1 id="usage-view-title">Sử dụng token</h1><p class="page-sub" id="usage-scope-note"></p></div>
  <div class="usage-header-controls" id="usage-header-controls">
    <p class="range-note" id="range-note"></p>
    <div class="page-actions">
    <button class="quiet-button" id="scope-clear" type="button" hidden></button>
    <span class="range-presets" role="group" aria-label="Chọn nhanh kỳ">
      <button class="chip" type="button" data-preset="1">1 ngày</button>
      <button class="chip" type="button" data-preset="7" aria-pressed="true">7 ngày</button>
      <button class="chip" type="button" data-preset="30">30 ngày</button>
      <button class="chip" type="button" data-preset="all">Tất cả</button>
    </span>
    <label class="range-field"><span>Từ</span><input type="date" id="range-from"></label>
    <label class="range-field"><span>Đến</span><input type="date" id="range-to"></label>
    <button class="quiet-button" id="range-apply" type="button">Áp dụng</button>
    </div>
  </div>
</header>
<div class="page-scroll" id="usage-scroll"><div class="page-inner section-stack">
  <div id="usage-overview">
    <div class="usage-view-tabs" id="usage-view-tabs" role="group" aria-label="Nội dung Usage">
      <button class="chip" type="button" data-usage-view="usage" aria-pressed="true">Sử dụng</button>
      <button class="chip" type="button" data-usage-view="cost" aria-pressed="false">Chi phí</button>
      <button class="chip" type="button" data-usage-view="trace" aria-pressed="false">Trace</button>
    </div>
    <div class="page-status" id="page-status" role="status" hidden><span id="stats-status-text"></span><button class="quiet-button" id="stats-retry" type="button" hidden>Thử lại số liệu</button></div>
    <div class="stat-grid" id="stat-grid" aria-live="polite"></div>
    <section class="usage-view-panel" id="usage-panel-usage" aria-label="Sử dụng token">
      <article class="paper-card">
        <div class="card-head"><div><h2 class="card-title">Nhịp token từng ngày</h2><p class="card-copy">Input gồm cả Cache; cột tách Input chưa cache, Cache và Output.</p></div>
          <div class="seg-toggle" role="group" aria-label="Chỉ số cột">
            <button class="chip" type="button" data-day-metric="tokens" aria-pressed="true">Token</button>
            <button class="chip" type="button" data-day-metric="requests" aria-pressed="false">Gọi model</button>
          </div>
        </div>
        <div class="token-legend" aria-label="Chú giải biểu đồ"><span><i class="legend-input"></i>Input chưa cache</span><span><i class="legend-cache"></i>Cache</span><span><i class="legend-output"></i>Output</span></div>
        <div id="chart-day"></div><p class="card-note" id="chart-day-note"></p>
      </article>
      <section class="token-summary-grid" aria-label="Thành phần token">
        <article class="panel token-summary"><h2 class="panel-title">Input</h2><strong id="usage-input-total">—</strong><p>Đã bao gồm Cache read.</p></article>
        <article class="panel token-summary"><h2 class="panel-title">Cache read</h2><strong id="usage-cache-total">—</strong><p>Nằm trong tổng Input.</p></article>
        <article class="panel token-summary"><h2 class="panel-title">Output</h2><strong id="usage-output-total">—</strong><p>Token hoàn tất từ model.</p></article>
      </section>
      <details class="paper-card analysis-panel" id="analysis-details">
        <summary>Phân tích</summary>
        <div class="analysis-panels">
          <section id="card-bot"><h2 class="card-title">Theo bot</h2><ul class="bot-rows" id="rows-bot"></ul></section>
          <section><h2 class="card-title">Theo model</h2><ul class="model-rows" id="rows-model"></ul></section>
          <section><h2 class="card-title">Theo giờ UTC</h2><div id="chart-hour"></div><p class="card-note">Giờ theo mốc <code>started_at</code> của phiên.</p></section>
        </div>
      </details>
    </section>
    <section class="usage-view-panel" id="usage-panel-cost" aria-label="Chi phí" hidden>
      <p class="card-copy" id="cost-coverage"></p>
      <article class="paper-card">
        <div class="card-head"><div><h2 class="card-title">Chi phí đã ghi nhận theo ngày</h2><p class="card-copy">Lượt có ghi nhận giá có thể vẫn có phần chưa được định giá; phần thiếu không được tính là $0.</p></div></div>
        <div id="chart-cost"></div><p class="card-note" id="chart-cost-note"></p>
      </article>
      <div class="analysis-panels cost-analysis">
        <section class="paper-card" id="card-bot-cost"><h2 class="card-title">Theo bot</h2><ul class="bot-rows" id="rows-bot-cost"></ul></section>
        <section class="paper-card"><h2 class="card-title">Theo model</h2><ul class="model-rows" id="rows-model-cost"></ul></section>
      </div>
    </section>
    <section class="usage-view-panel" id="usage-panel-trace" aria-label="Trace" hidden>
      <article class="paper-card usage-turn-list">
        <div class="list-head"><h2 class="card-title">Lịch sử lượt</h2>
          <div class="list-filters">
            <label class="mini-field"><span>Tìm lượt</span><input id="filter-q" type="search" autocomplete="off" placeholder="Tên hoặc mã phiên"></label>
            <label class="mini-field"><span>Model</span><select id="filter-model"><option value="">Tất cả model</option></select></label>
            <label class="mini-field"><span>Trạng thái</span><select id="filter-status"></select></label>
            <button class="icon-button" id="trace-refresh" type="button" title="Tải lại lượt" aria-label="Tải lại lượt">${icon('retry')}</button>
          </div>
        </div>
        <p class="page-status s-error" id="trace-status" hidden><span id="trace-status-text"></span><button class="quiet-button" id="trace-retry" type="button">Thử lại danh sách</button></p>
        <div id="trace-wrap"><ul class="tl-flat-turns" id="trace-sessions" aria-live="polite"></ul></div>
        <div class="empty-note" id="trace-empty" hidden><strong>Không có lượt nào khớp.</strong><span id="trace-empty-hint">Đổi khoảng ngày hoặc nới bộ lọc để xem thêm.</span></div>
        <div class="table-foot"><span id="trace-count"></span><button class="quiet-button" id="trace-more" type="button" hidden>Tải thêm</button></div>
      </article>
    </section>
  </div>
  <section id="turn-detail-view" aria-label="Chi tiết lượt" hidden>
    <div class="detail-toolbar"><button class="quiet-button tl-back" id="detail-back" type="button">${icon('arrow')}<span id="detail-back-label">Trace</span></button><button class="quiet-button" id="detail-refresh" type="button">${icon('retry')}<span>Tải lại lượt</span></button></div>
    <p class="page-status s-error" id="detail-status" role="status" hidden><span id="detail-status-text"></span><button class="quiet-button" id="detail-retry" type="button">Thử lại lượt</button></p>
    <div id="detail-body"></div>
  </section>
</div></div>`;

/* ---------- range controls ---------- */
function updateRangeNote() {
  const f = $('#range-from').value, t = $('#range-to').value;
  const nice = v => v ? new Date(v + 'T00:00:00').toLocaleDateString('vi-VN', { day: 'numeric', month: 'numeric', year: 'numeric' }) : '…';
  $('#range-note').textContent = rangeAll ? 'Tất cả' : 'Kỳ ' + nice(f) + ' – ' + nice(t);
}
function updatePresetButtons() {
  const from = $('#range-from').value, to = $('#range-to').value;
  const today = new Date();
  const preset = days => { const start = new Date(today); start.setDate(start.getDate() - (days - 1)); return [isoDay(start), isoDay(today)]; };
  $$('.range-presets .chip').forEach(button => {
    const days = button.dataset.preset;
    const active = days === 'all' ? rangeAll : !rangeAll && days && preset(Number(days))[0] === from && preset(Number(days))[1] === to;
    button.setAttribute('aria-pressed', String(!!active));
  });
}
function applyRange() {
  const f = $('#range-from').value, t = $('#range-to').value;
  if (f && t && f > t) { toast('Ngày bắt đầu phải trước hoặc bằng ngày kết thúc.'); return; }
  rangeFrom = f; rangeTo = t; rangeAll = !f && !t;
  updateRangeNote(); updatePresetButtons();
  applyControlRoute();
}
function setPreset(days) {
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

/* ---------- status line ---------- */
function setStatus(text, cls) {
  const el = $('#page-status');
  $('#stats-status-text').textContent = text || '';
  $('#stats-retry').hidden = !statsError;
  el.className = 'page-status' + (cls ? ' ' + cls : '');
  el.hidden = !text;
}

/* ---------- routed filters and request keys ---------- */
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
  const slash = raw.indexOf('/');
  if (slash <= 0 || raw.indexOf('/', slash + 1) !== -1) return null;
  let sid;
  try { sid = decodeURIComponent(raw.slice(0, slash)); } catch { return null; }
  const turnText = raw.slice(slash + 1);
  if (!/^[A-Za-z0-9_-]{1,120}$/.test(sid) || !/^\d{1,8}$/.test(turnText)) return null;
  const turn = Number(turnText);
  return Number.isSafeInteger(turn) ? { sid, turn } : null;
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
  const btn = $('#scope-clear');
  const b = botScope && getBot(botScope);
  btn.hidden = !botScope;
  btn.textContent = botScope ? 'Bỏ lọc: ' + (b ? b.name : botScope) + ' ✕' : '';
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
function loadSession(id, title, botId) { selectBot(botId || ''); }
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
    const focus = overviewFocus && [...$('#trace-sessions').querySelectorAll('button[data-session][data-turn]')]
      .find(button => button.dataset.session === overviewFocus.sid && button.dataset.turn === String(overviewFocus.turn));
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
async function loadDetail(sid, turn, force = false) {
  const key = sid + '/' + turn;
  if (!force && detailKey === key && (detailLoading || detailData || detailError)) return;
  detailKey = key; detailData = null; detailError = ''; detailLoading = true;
  const seq = ++detailSeq;
  renderDetail();
  try {
    const data = await getJson(`/api/traces/${encodeURIComponent(sid)}/${turn}`);
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
      const active = document.activeElement?.closest?.('button[data-session][data-turn]');
      overviewFocus = active ? { sid: active.dataset.session, turn: active.dataset.turn } : overviewFocus;
    }
    setView('detail');
    void loadDetail(selected.sid, selected.turn, force);
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

/* ---------- render: hero stats ---------- */
function heroCard(label, value, hint) {
  return `<section class="panel stat"><p class="stat-label">${label}</p><p class="stat-value">${value}</p>${hint ? `<p class="stat-hint">${hint}</p>` : ''}</section>`;
}
function renderStats(s, { loading = false } = {}) {
  if (loading) {
    $('#stat-grid').innerHTML = [
      heroCard('Gọi model', '—', 'request tới model'),
      heroCard('Gọi tool', '—', 'lần dùng công cụ'),
      heroCard('Token', '—', 'Input — · Cache — · Output —'),
      heroCard('Chi phí đã ghi nhận', '—', ''),
      heroCard('Độ trễ p50', '—', 'p90 —'),
      heroCard('Phiên', '—', 'mỗi phiên một lần'),
      heroCard('Lượt', '—', 'lượt trong kỳ'),
    ].join('');
    for (const id of ['chart-day', 'chart-cost', 'chart-hour', 'rows-bot', 'rows-model', 'rows-bot-cost', 'rows-model-cost']) $(`#${id}`).innerHTML = '';
    $('#chart-day-note').textContent = ''; $('#chart-cost-note').textContent = ''; $('#cost-coverage').textContent = '';
    $('#usage-input-total').textContent = '—'; $('#usage-cache-total').textContent = '—'; $('#usage-output-total').textContent = '—';
    $('#card-bot').hidden = !!botScope;
    $('#card-bot-cost').hidden = !!botScope;
    fillModelFilter(s.models || []);
    fillStatusFilter(s.by_status || []);
    return;
  }
  const t = s.totals || {};
  const inTok = typeof t.prompt_tokens === 'number' ? Math.max(0, t.prompt_tokens - (typeof t.cached_tokens === 'number' ? t.cached_tokens : 0)) : t.prompt_tokens;
  $('#stat-grid').innerHTML = [
    heroCard('Gọi model', fmtInt(t.requests), 'request tới model'),
    heroCard('Gọi tool', fmtInt(t.tool_calls), 'lần dùng công cụ'),
    heroCard('Token', fmtInt(t.total_tokens), `Input ${fmtCompact(inTok)} · Cache ${fmtCompact(t.cached_tokens)} · Output ${fmtCompact(t.completion_tokens)}`),
    heroCard('Chi phí đã ghi nhận', fmtCost(t.cost_usd),
      t.cost_usd == null ? 'chưa có lượt nào ghi nhận giá'
        : (typeof t.requests === 'number' && t.requests ? 'TB ' + fmtCost(t.cost_usd / t.requests) + ' / request · phần đã ghi nhận' : '')),
    heroCard('Độ trễ p50', fmtDur(t.latency_ms_p50), 'p90 ' + fmtDur(t.latency_ms_p90)),
    heroCard('Phiên', fmtInt(t.sessions), botScope ? 'phiên có đóng góp' : 'mỗi phiên một lần'),
    heroCard('Lượt', fmtInt(t.turns), botScope ? 'lượt có đóng góp' : 'lượt trong kỳ'),
  ].join('');
  $('#usage-input-total').textContent = fmtInt(t.prompt_tokens);
  $('#usage-cache-total').textContent = fmtInt(t.cached_tokens);
  $('#usage-output-total').textContent = fmtInt(t.completion_tokens);
  $('#cost-coverage').textContent = typeof t.priced_turns === 'number' && typeof t.turns === 'number'
    ? `${fmtInt(t.priced_turns)} / ${fmtInt(t.turns)} lượt có ghi nhận giá; một lượt vẫn có thể chỉ được định giá một phần. Phần thiếu không tính là $0.`
    : 'Số lượt có ghi nhận giá chưa có trong dữ liệu.';
  renderDayChart(); renderCostChart(); renderBotRows(); renderModelRows(); renderCostRows(); renderHourChart();
  fillModelFilter(s.models || []);
  fillStatusFilter(s.by_status || []);
}

/* ---------- render: daily tokens, calls, and recorded cost ---------- */
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
    ? 'Input gồm Cache; cột tách Input chưa cache, Cache read và Output. Ngày theo UTC; dữ liệu thiếu được để trống.'
    : 'Cột biểu thị lượt gọi model theo ngày UTC; dữ liệu thiếu được để trống.';
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
  $('#chart-cost-note').textContent = 'Chỉ hiển thị chi phí được ghi nhận theo ngày UTC; “—” không phải $0.';
}

/* ---------- render: authoritative token and cost breakdowns ---------- */
function meterWidth(value, max) {
  return typeof value === 'number' && Number.isFinite(value) && typeof max === 'number' && max > 0
    ? Math.max(0, Math.min(100, value / max * 100)) : 0;
}
function rowMeta(row) {
  return `${fmtInt(row.sessions)} phiên · ${fmtInt(row.turns)} lượt · ${fmtInt(row.requests)} request · ${fmtInt(row.tool_calls)} tool · ${fmtInt(row.total_tokens)} token · ${fmtCost(row.cost_usd)}`;
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
  const rows = (statsData?.by_model || []).slice().sort((a, b) => (b.total_tokens || 0) - (a.total_tokens || 0));
  const total = statsData?.totals?.total_tokens, max = Math.max(0, ...rows.map(row => typeof row.total_tokens === 'number' ? row.total_tokens : 0));
  $('#rows-model').innerHTML = !statsData ? '' : rows.length ? rows.map(row => `
    <li class="model-row"><span class="model-name" title="${escapeHTML(row.model || '—')}">${escapeHTML(row.model || '—')}</span>
      <span class="model-cost">${fmtInt(row.total_tokens)} token</span>
      <span class="model-meta">${escapeHTML(rowMeta(row))} · p50 ${fmtDur(row.latency_ms_p50)} · dùng cuối ${relTime(row.last_started_at)}</span>
      <span class="meter row-meter" role="img" aria-label="${escapeHTML(share(row.total_tokens, total))} tổng token"><i style="width:${meterWidth(row.total_tokens, max)}%"></i></span>
    </li>`).join('') : '<li><div class="empty-note"><strong>Chưa có lượt nào trong kỳ.</strong></div></li>';
}
function costMeta(row) {
  return `${fmtInt(row.priced_turns)} lượt có ghi nhận giá · ${fmtInt(row.turns)} lượt · ${fmtInt(row.sessions)} phiên · ${fmtInt(row.requests)} request · ${fmtInt(row.total_tokens)} token`;
}
function renderCostRows() {
  $('#card-bot-cost').hidden = !!botScope;
  const bots = (statsData?.by_bot || []).slice().sort((a, b) => (b.cost_usd ?? -1) - (a.cost_usd ?? -1));
  const models = (statsData?.by_model || []).slice().sort((a, b) => (b.cost_usd ?? -1) - (a.cost_usd ?? -1));
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
    </li>`).join('') : '<li><div class="empty-note"><strong>Chưa có dữ liệu chi phí theo model.</strong></div></li>';
}

/* ---------- render: by_hour (always 24 columns, UTC hours) ---------- */
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

/* ---------- quick filters (table only) ---------- */
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

/* ---------- render: grouped turn history ---------- */
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
  $('#trace-sessions').innerHTML = rows.map(turnRowHtml).join('');
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
  const button = e.target.closest('button[data-session][data-turn]');
  if (!button) return;
  clearTimeout(qTimer); qTimer = null;
  overviewScroll = $('#usage-scroll').scrollTop;
  overviewFocus = { sid: button.dataset.session, turn: button.dataset.turn };
  ctx.navigate('dashboard', { search: ctx.route.search, hash: '#' + encodeURIComponent(button.dataset.session) + '/' + button.dataset.turn });
}

/* ---------- controls and lifecycle ---------- */
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
    list.parentNode.insertBefore(button, list);
    button.onclick = () => {
      if (sidebar.classList.contains('mobile-open') && typeof closeMobile === 'function') closeMobile();
      selectBot('');
    };
  }
  button.setAttribute('aria-pressed', String(!botScope));
}
function buildPage() {
  $('.workspace').insertAdjacentHTML('beforeend', PAGE_HTML);
  ensureOverviewScopeButton();
  $('#range-apply').onclick = applyRange;
  $('#scope-clear').onclick = () => selectBot('');
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
  $('#trace-sessions').addEventListener('click', openTraceRow);
  $('#trace-sessions').addEventListener('keydown', event => { if (event.key === 'Enter') openTraceRow(event); });
  $('#detail-back').onclick = returnFromDetail;
  $('#detail-retry').onclick = () => {
    const selected = parseDetailRoute(ctx.route);
    if (selected) void loadDetail(selected.sid, selected.turn, true);
  };
  $('#detail-refresh').onclick = () => {
    const selected = parseDetailRoute(ctx.route);
    if (selected) void loadDetail(selected.sid, selected.turn, true);
  };
}
async function boot() {
  buildPage();
  readRoute(ctx.route, true);
  routeKey = ctx.route.search + ctx.route.hash;
  try { await ctx.shellReady; } catch (err) { toast('Không tải được đội bot: ' + (err.message || '')); }
  ctx.highlightBot(botScope); ctx.renderTeam(); updateScopeChip();
  const selected = parseDetailRoute(ctx.route);
  if (selected) { setView('detail'); void loadDetail(selected.sid, selected.turn); }
  else { setView('overview'); ensureOverviewData(false); }
}
const ready = boot();
return { ready, selectBot, loadSession,
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
