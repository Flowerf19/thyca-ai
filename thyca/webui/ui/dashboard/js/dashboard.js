// Retained dashboard controller; declarations are isolated in this mount closure.
ThycaRouter.register('dashboard', { title: 'Sử dụng', stylesheet: '/ui/dashboard/dashboardcss/dashboard.css', mount(ctx) {
  let routeKey = '';
  const root = ctx.root;
  const $ = (selector, base = root) => selector === '.workspace' ? root : base.querySelector(selector);
  const $$ = (selector, base = root) => [...base.querySelectorAll(selector)];
  let selectedBot = '';
// Dashboard "Sử dụng & chi phí" — trace stats + recent turns, real backend only.
//
// API: GET /api/traces/stats?from&to                      -> totals + by_model/by_bot/by_day/by_hour/by_status + models
//      GET /api/traces?from&to&limit&offset&model&status   -> {traces, total} paged, newest first
//      GET /api/bots, GET /api/sessions                   -> share/data.js via bootShell()
// Deep-link convention (one form everywhere): a turn row opens
//   ../trace/index.html#<session_id>/<turn_index>
// and a sidebar bot card scopes the whole page in place — stats + the
// recent-turns list are re-fetched with ?bot_id=<id> (server filters by
// the turn's bot, its DM owner, or a session participant). Clicking the
// scoped bot again, or "Bỏ lọc", returns to the team view.


const FIRST_PAGE = 15;       // "Lượt gần nhất" initial fetch — roughly one screen
const MORE_PAGE = 50;        // "Tải thêm" page size
const MAX_DAY_COLUMNS = 62;  // past this, days get unreadable -> render the sparse by_day buckets instead

let rangeFrom = '', rangeTo = '';   // 'YYYY-MM-DD'; the server compares on the UTC day of started_at
let botScope = '';                  // '' = whole team, otherwise a bot id
let dayMetric = 'requests';         // by_day bar metric: 'requests' | 'cost_usd'
let statsData = null;
const traceState = { rows: [], total: 0, loading: false, error: '' };
let statsSeq = 0, traceSeq = 0;     // stale-response guards: latest request wins

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
  if (!den) return '0%';
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
function traceBotCell(t) {
  const ids = [...new Set([t.bot_id, t.session_dm, ...(t.session_bots || [])].filter(Boolean))];
  if (!ids.length) return '<span class="bot-id">—</span>';
  const b = getBot(ids[0]);
  const label = (b ? b.name : ids[0]) + (ids.length > 1 ? ` +${ids.length - 1}` : '');
  const ava = b ? `<span class="mini-avatar">${avatar(b, b.state, 'expression-preview')}</span>` : '';
  return `<span class="trace-bot${b ? '' : ' bot-id'}" title="${escapeHTML(ids.join(', '))}">${ava}<span>${escapeHTML(label)}</span></span>`;
}
const traceHref = t => '../trace/index.html#' + encodeURIComponent(t.session_id) + '/' + t.turn_index;

/* ---------- static page markup (into .workspace from emitShell, after the
   shared ornament svgs it already placed) ---------- */
const PAGE_HTML = `
<header class="page-nav">
  <div class="page-title">
    <div class="eyebrow">Sử dụng</div>
    <h1>Chi phí &amp; nhịp làm việc</h1>
    <p class="page-sub" id="page-sub">Lượt gọi, token và chi phí của đội bot — đọc trực tiếp từ trace.</p>
    <p class="range-note" id="range-note"></p>
  </div>
  <div class="page-actions">
    <label class="range-field"><span>Từ</span><input type="date" id="range-from"></label>
    <label class="range-field"><span>Đến</span><input type="date" id="range-to"></label>
    <button class="quiet-button" id="range-apply" type="button">Áp dụng</button>
    <button class="quiet-button" id="scope-clear" type="button" hidden></button>
    <span class="range-presets" role="group" aria-label="Chọn nhanh kỳ">
      <button class="chip" type="button" data-preset="1">Hôm nay</button>
      <button class="chip" type="button" data-preset="7" aria-pressed="true">7 ngày</button>
      <button class="chip" type="button" data-preset="30">30 ngày</button>
    </span>
  </div>
</header>
<div class="page-scroll"><div class="page-inner section-stack">
  <p class="page-status" id="page-status" role="status" hidden></p>
    <div class="stat-grid" id="stat-grid" aria-live="polite"></div>
    <article class="paper-card">
      <div class="eyebrow">Theo ngày</div>
      <div class="card-head"><h2 class="card-title">Nhịp từng ngày</h2>
        <div class="seg-toggle" role="group" aria-label="Chỉ số cột">
          <button class="chip" type="button" data-day-metric="requests" aria-pressed="true">Lượt</button>
          <button class="chip" type="button" data-day-metric="cost_usd" aria-pressed="false">Chi phí</button>
        </div>
      </div>
      <div id="chart-day"></div>
      <p class="card-note" id="chart-day-note"></p>
    </article>
    <article class="paper-card" id="card-bot">
      <div class="eyebrow">Theo bot</div>
      <h2 class="card-title">Ai đang chạy nhiều</h2>
      <ul class="bot-rows" id="rows-bot"></ul>
    </article>
    <article class="paper-card">
      <div class="eyebrow">Theo model</div>
      <h2 class="card-title">Model trong kỳ</h2>
      <ul class="model-rows" id="rows-model"></ul>
    </article>
    <article class="paper-card">
      <div class="eyebrow">Theo giờ</div>
      <h2 class="card-title">Giờ hay dùng</h2>
      <div id="chart-hour"></div>
      <p class="card-note">Giờ UTC theo mốc <code>started_at</code> của phiên — giờ địa phương có thể lệch vài cột.</p>
    </article>
    <article class="paper-card">
      <div class="eyebrow">Lượt gần nhất</div>
      <div class="list-head"><h2 class="card-title">Lượt mới nhất trước</h2>
        <div class="list-filters">
          <label class="mini-field"><span>Model</span><select id="filter-model"><option value="">Tất cả model</option></select></label>
          <label class="mini-field"><span>Trạng thái</span><select id="filter-status"></select></label>
          <button class="icon-button" id="trace-refresh" type="button" title="Tải lại" aria-label="Tải lại">${icon('retry')}</button>
        </div>
      </div>
      <p class="page-status s-error" id="trace-status" hidden></p>
      <div class="table-scroll" id="trace-wrap"><table class="trace-table">
        <thead><tr><th>Thời gian</th><th>Lượt</th><th>Bot</th><th>Model</th><th>Trạng thái</th><th class="num">Request</th><th class="num">Tool</th><th class="num">Token</th><th class="num">Chi phí</th><th class="num">Độ trễ</th></tr></thead>
        <tbody id="trace-rows"></tbody>
      </table></div>
      <div class="empty-note" id="trace-empty" hidden><strong>Không có lượt nào trong kỳ.</strong><span id="trace-empty-hint">Đổi khoảng ngày hoặc nới bộ lọc để xem thêm.</span></div>
      <div class="table-foot"><span id="trace-count"></span><button class="quiet-button" id="trace-more" type="button" hidden>Tải thêm</button></div>
    </article>
</div></div>`;

/* ---------- range controls ---------- */
function updateRangeNote() {
  const f = $('#range-from').value, t = $('#range-to').value;
  const nice = v => v ? new Date(v + 'T00:00:00').toLocaleDateString('vi-VN', { day: 'numeric', month: 'numeric', year: 'numeric' }) : '…';
  $('#range-note').textContent = 'Kỳ ' + nice(f) + ' – ' + nice(t);
}
function applyRange() {
  const f = $('#range-from').value, t = $('#range-to').value;
  if (f && t && f > t) { toast('Ngày bắt đầu phải trước hoặc bằng ngày kết thúc.'); return; }
  rangeFrom = f; rangeTo = t;
  $$('.range-presets .chip').forEach(c => c.setAttribute('aria-pressed', 'false'));
  updateRangeNote();
  loadStats();            // API: GET /api/traces/stats?from&to
  loadTraces(true);       // API: GET /api/traces?from&to&limit&offset&model&status
}
function setPreset(days, btn) {
  const to = new Date(), from = new Date();
  from.setDate(from.getDate() - (days - 1));
  $('#range-from').value = isoDay(from);
  $('#range-to').value = isoDay(to);
  $$('.range-presets .chip').forEach(c => c.setAttribute('aria-pressed', String(c === btn)));
  applyRange();
}

/* ---------- status line ---------- */
function setStatus(text, cls) {
  const el = $('#page-status');
  if (!text) { el.hidden = true; return; }
  el.textContent = text;
  el.className = 'page-status' + (cls ? ' ' + cls : '');
  el.hidden = false;
}

/* ---------- data ---------- */
function rangeParams() {
  const q = new URLSearchParams();
  if (rangeFrom) q.set('from', rangeFrom);
  if (rangeTo) q.set('to', rangeTo);
  if (botScope) q.set('bot_id', botScope);
  return q;
}

/* Sidebar routing stays on this page: a bot card scopes stats + the
   recent-turns list to that bot (server-side bot_id filter). Clicking the
   scoped bot again, or "Bỏ lọc", clears it; a group/session scopes to the
   owning bot. */
function updateScopeChip() {
  const btn = $('#scope-clear');
  const b = botScope && getBot(botScope);
  btn.hidden = !botScope;
  btn.textContent = botScope ? 'Bỏ lọc: ' + (b ? b.name : botScope) + ' ✕' : '';
  $('#page-sub').textContent = botScope
    ? `Lượt gọi, token và chi phí của ${b ? b.name : botScope} — đọc trực tiếp từ trace.`
    : 'Lượt gọi, token và chi phí của đội bot — đọc trực tiếp từ trace.';
}
function selectBot(id) {
  botScope = !id || botScope === id ? '' : id;
  ctx.highlightBot(botScope);
  ctx.renderTeam();
  updateScopeChip();
  ctx.updateRoute({ search: botScope ? '?bot=' + encodeURIComponent(botScope) : '' });
  routeKey = ctx.route.search + ctx.route.hash;
  loadStats();            // API: GET /api/traces/stats?from&to&bot_id
  loadTraces(true);       // API: GET /api/traces?from&to&bot_id&limit&offset
}
function loadSession(id, title, botId) { selectBot(botId || ''); }
async function loadStats() {
  const seq = ++statsSeq;
  if (!statsData) setStatus('Đang tải số liệu…');
  try {
    // API: GET /api/traces/stats?from&to — quick filters stay table-only on purpose
    const data = await getJson('/api/traces/stats?' + rangeParams());
    if (seq !== statsSeq) return;
    statsData = data;
    setStatus('');
    renderStats(data);
  } catch (err) {
    if (seq !== statsSeq) return;
    setStatus('Không tải được số liệu: ' + err.message + ' — chỉnh kỳ rồi bấm Áp dụng để thử lại.', 's-error');
  }
}
async function loadTraces(reset) {
  if (reset) traceState.rows = [];
  const seq = ++traceSeq;
  traceState.loading = true;
  traceState.error = '';
  renderTraceFoot();
  const q = rangeParams();
  q.set('limit', reset ? FIRST_PAGE : MORE_PAGE);
  q.set('offset', traceState.rows.length);
  const model = $('#filter-model').value, status = $('#filter-status').value;
  if (model) q.set('model', model);
  if (status) q.set('status', status);
  try {
    // API: GET /api/traces?from&to&limit&offset&model&status
    const data = await getJson('/api/traces?' + q);
    if (seq !== traceSeq) return;
    traceState.rows = traceState.rows.concat(data.traces || []);
    traceState.total = typeof data.total === 'number' ? data.total : traceState.rows.length;
    renderTraceTable();
  } catch (err) {
    if (seq !== traceSeq) return;
    traceState.error = err.message;
    renderTraceTable();
  } finally {
    traceState.loading = false;
    if (seq === traceSeq) renderTraceFoot();
  }
}

/* ---------- render: hero stats ---------- */
function heroCard(label, value, hint) {
  return `<section class="panel stat"><p class="stat-label">${label}</p><p class="stat-value">${value}</p>${hint ? `<p class="stat-hint">${hint}</p>` : ''}</section>`;
}
function renderStats(s) {
  const t = s.totals || {};
  // prompt_tokens includes the cached portion — show the real uncached input.
  const inTok = typeof t.prompt_tokens === 'number' ? Math.max(0, t.prompt_tokens - (t.cached_tokens || 0)) : t.prompt_tokens;
  $('#stat-grid').innerHTML = [
    heroCard('Lượt', fmtInt(t.requests), 'request tới model'),
    heroCard('Gọi tool', fmtInt(t.tool_calls), 'lần dùng công cụ'),
    heroCard('Token', fmtInt(t.total_tokens), `in ${fmtCompact(inTok)} · cache ${fmtCompact(t.cached_tokens)} · out ${fmtCompact(t.completion_tokens)}`),
    heroCard('Chi phí', fmtCost(t.cost_usd),
      t.cost_usd == null ? 'chưa có lượt nào ghi nhận giá'
        : (t.requests ? 'TB ' + fmtCost(t.cost_usd / t.requests) + ' / request' : '')),
    heroCard('Độ trễ p50', fmtDur(t.latency_ms_p50), 'p90 ' + fmtDur(t.latency_ms_p90)),
  ].join('');
  renderDayChart();
  renderBotRows();
  renderModelRows();
  renderHourChart();
  fillModelFilter(s.models || []);
  fillStatusFilter(s.by_status || []);
}

/* ---------- render: by_day ---------- */
function dayColumns(byDay) {
  const map = new Map(byDay.map(d => [d.day, d]));
  if (rangeFrom && rangeTo) {
    const from = Date.parse(rangeFrom), to = Date.parse(rangeTo);
    const span = Math.round((to - from) / 864e5);
    if (span >= 0 && span < MAX_DAY_COLUMNS) {
      const days = [];
      for (let t = from; t <= to; t += 864e5) {
        const key = new Date(t).toISOString().slice(0, 10);
        days.push(map.get(key) || { day: key, requests: 0, cost_usd: null });
      }
      return days;
    }
  }
  return byDay;   // free-form span too wide -> show the sparse buckets as-is
}
function renderDayChart() {
  const cols = dayColumns(statsData?.by_day || []);
  const box = $('#chart-day');
  if (!cols.length) {
    box.innerHTML = '<div class="empty-note"><strong>Chưa có lượt nào trong kỳ.</strong>Các cột sẽ xuất hiện khi có phiên.</div>';
    $('#chart-day-note').textContent = '';
    return;
  }
  const val = d => dayMetric === 'cost_usd' ? (d.cost_usd || 0) : (d.requests || 0);
  const max = Math.max(0, ...cols.map(val));
  const step = Math.max(1, Math.ceil(cols.length / 15));   // keep day labels readable on long ranges
  box.innerHTML = `<div class="day-chart" role="img" aria-label="${dayMetric === 'cost_usd' ? 'Chi phí' : 'Lượt'} theo ngày">` + cols.map((d, i) => {
    const v = val(d);
    const pct = max ? (v / max) * 100 : 0;
    const tip = `${fmtDayFull(d.day)} · ${fmtInt(d.requests)} request · ${fmtCost(d.cost_usd)}`;
    return `<div class="dc-col" title="${escapeHTML(tip)}"><span class="dc-track"><i class="dc-bar${v ? '' : ' is-zero'}" style="height:${pct}%"></i></span><span class="dc-day">${i % step === 0 || i === cols.length - 1 ? fmtDay(d.day) : ''}</span></div>`;
  }).join('') + '</div>';
  $('#chart-day-note').textContent = (dayMetric === 'cost_usd'
    ? 'Cột cao theo chi phí mỗi ngày (USD). ' : 'Cột cao theo số request mỗi ngày. ')
    + 'Ngày theo mốc UTC của phiên; ngày trống không có lượt nào.';
}

/* ---------- render: by_bot (team breakdown — meaningless under a bot
   scope, which would show a single row; hidden until unscoped) ---------- */
function renderBotRows() {
  $('#card-bot').hidden = !!botScope;
  const rows = (statsData?.by_bot || []).slice().sort((a, b) => (b.requests || 0) - (a.requests || 0));
  const totalReq = statsData?.totals?.requests || 0;
  const maxReq = Math.max(1, ...rows.map(r => r.requests || 0));
  $('#rows-bot').innerHTML = rows.length ? rows.map(r => {
    const b = r.bot_id && getBot(r.bot_id);
    const pct = Math.max(1.5, ((r.requests || 0) / maxReq) * 100);
    const meta = `${fmtInt(r.requests)} request · ${fmtInt(r.tool_calls)} tool · ${fmtInt(r.total_tokens)} token · ${fmtCost(r.cost_usd)} · cuối ${relTime(r.last_started_at)}`;
    return `<li class="bot-row">${avatarCell(r.bot_id)}
      <span class="bot-row-main"><span class="bot-row-name">${escapeHTML(botDisplayName(r.bot_id))}</span>
      <span class="bot-row-meta">${meta}</span>
      <span class="share-bar" role="img" aria-label="${escapeHTML(share(r.requests, totalReq))} tổng request"><i style="width:${pct}%;background:${b ? b.color : '#a08f68'}"></i></span></span>
      <span class="bot-row-share">${share(r.requests, totalReq)}<small>request</small></span></li>`;
  }).join('') : '<li><div class="empty-note"><strong>Chưa có lượt nào gắn với bot.</strong>Lượt cũ không ghi bot sẽ gộp vào “unknown”.</div></li>';
}

/* ---------- render: by_model ---------- */
function renderModelRows() {
  const rows = (statsData?.by_model || []).slice().sort((a, b) => (b.total_tokens || 0) - (a.total_tokens || 0));
  $('#rows-model').innerHTML = rows.length ? rows.map(r => `
    <li class="model-row">
      <span class="model-name" title="${escapeHTML(r.model)}">${escapeHTML(r.model)}</span>
      <span class="model-meta">${fmtInt(r.requests)} request · ${fmtInt(r.total_tokens)} token · p50 ${fmtDur(r.latency_ms_p50)} · dùng cuối ${relTime(r.last_started_at)}</span>
      <span class="model-cost">${fmtCost(r.cost_usd)}</span>
    </li>`).join('') : '<li><div class="empty-note"><strong>Chưa có lượt nào trong kỳ.</strong></div></li>';
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
  const sel = $('#filter-model'), cur = sel.value;
  sel.replaceChildren(
    Object.assign(document.createElement('option'), { value: '', textContent: 'Tất cả model' }),
    ...models.map(m => Object.assign(document.createElement('option'), { value: m, textContent: m })),
  );
  if (models.includes(cur)) sel.value = cur;
}
function fillStatusFilter(byStatus) {
  const sel = $('#filter-status'), cur = sel.value;
  const extras = byStatus.map(x => x.status).filter(s => s && !(s in STATUS_META));
  sel.replaceChildren(
    Object.assign(document.createElement('option'), { value: '', textContent: 'Tất cả trạng thái' }),
    ...Object.entries(STATUS_META).map(([v, [label]]) => Object.assign(document.createElement('option'), { value: v, textContent: label })),
    ...extras.map(v => Object.assign(document.createElement('option'), { value: v, textContent: v })),
  );
  if ([...sel.options].some(o => o.value === cur)) sel.value = cur;
}

/* ---------- render: recent turns ---------- */
function traceRow(t) {
  return `<tr class="trace-row" tabindex="0" data-status="${escapeHTML(t.status || '')}" data-href="${traceHref(t)}" title="Mở trace ${escapeHTML(t.session_id)} · lượt ${t.turn_index}">
    <td class="t-time">${fmtDateTime(t.started_at)}</td>
    <td class="t-title">${escapeHTML(t.title || '—')}</td>
    <td>${traceBotCell(t)}</td>
    <td class="t-model">${escapeHTML(t.model || '—')}</td>
    <td>${statusChip(t.status)}</td>
    <td class="num">${fmtInt(t.requests)}</td>
    <td class="num">${fmtInt(t.tool_calls)}</td>
    <td class="num">${fmtInt(t.total_tokens)}</td>
    <td class="num">${fmtCost(t.cost_usd)}</td>
    <td class="num">${fmtDur(t.latency_ms)}</td>
  </tr>`;
}
function renderTraceTable() {
  const err = $('#trace-status');
  if (traceState.error) {
    err.hidden = false;
    err.textContent = 'Không tải được danh sách lượt: ' + traceState.error;
  } else err.hidden = true;
  const rows = traceState.rows;
  $('#trace-wrap').hidden = !rows.length;
  const filtered = !!($('#filter-model').value || $('#filter-status').value);
  $('#trace-empty-hint').textContent = filtered
    ? 'Bộ lọc model/trạng thái đang bật — thử nới lọc hoặc đổi kỳ.'
    : 'Đổi khoảng ngày để xem thêm.';
  $('#trace-empty').hidden = !!rows.length || !!traceState.error || traceState.loading;
  $('#trace-rows').innerHTML = rows.map(traceRow).join('');
  renderTraceFoot();
}
function renderTraceFoot() {
  const more = $('#trace-more');
  $('#trace-count').textContent = traceState.loading && !traceState.rows.length
    ? 'Đang tải…'
    : `Hiển thị ${fmtInt(traceState.rows.length)} trong ${fmtInt(traceState.total)} lượt`;
  more.hidden = traceState.loading || traceState.rows.length >= traceState.total;
  more.disabled = traceState.loading;
  more.textContent = traceState.loading ? 'Đang tải…' : 'Tải thêm';
}
function openTraceRow(e) {
  const tr = e.target.closest('tr[data-href]');
  if (tr) {
    const match = tr.dataset.href.match(/#([^/]+)\/(\d+)$/);
    if (match) ctx.navigate('trace', { hash: '#' + match[1] + '/' + match[2] });
  }
}

/* ---------- boot ---------- */
function buildPage() {
  $('.workspace').insertAdjacentHTML('beforeend', PAGE_HTML);
  // Default range: the last 7 days (local dates; the server buckets on UTC days).
  const today = new Date(), from = new Date();
  from.setDate(from.getDate() - 6);
  rangeFrom = isoDay(from); rangeTo = isoDay(today);
  $('#range-from').value = rangeFrom;
  $('#range-to').value = rangeTo;
  updateRangeNote();

  $('#range-apply').onclick = applyRange;
  $('#scope-clear').onclick = () => selectBot('');
  $$('.range-presets .chip').forEach(c => c.onclick = () => setPreset(Number(c.dataset.preset), c));
  $$('.seg-toggle .chip').forEach(c => c.onclick = () => {
    dayMetric = c.dataset.dayMetric;
    $$('.seg-toggle .chip').forEach(x => x.setAttribute('aria-pressed', String(x === c)));
    renderDayChart();
  });
  $('#filter-model').onchange = $('#filter-status').onchange = () => loadTraces(true);
  $('#trace-refresh').onclick = () => loadTraces(true);
  $('#trace-more').onclick = () => loadTraces(false);
  $('#trace-rows').addEventListener('click', openTraceRow);
  $('#trace-rows').addEventListener('keydown', e => { if (e.key === 'Enter') openTraceRow(e); });
}

async function boot() {
  buildPage();
  try { await ctx.shellReady; } catch (err) { toast('Không tải được đội bot: ' + (err.message || '')); }
  const wanted = new URLSearchParams(ctx.route.search).get('bot');
  if (wanted && getBot(wanted)) { botScope = wanted; ctx.highlightBot(wanted); updateScopeChip(); }
  routeKey = ctx.route.search + ctx.route.hash;
  loadStats();            // API: GET /api/traces/stats?from&to
  loadTraces(true);       // API: GET /api/traces?from&to&limit&offset&model&status
  if (statsData) renderBotRows();
  if (traceState.rows.length) renderTraceTable();
}
  const ready = boot();
  return { ready, selectBot, loadSession, onRoute(route) {
    const key = route.search + route.hash;
    if (key === routeKey) return;
    routeKey = key;
    const wanted = new URLSearchParams(route.search).get('bot') || '';
    if (wanted !== botScope) {
      botScope = wanted && getBot(wanted) ? wanted : '';
      ctx.highlightBot(botScope); ctx.renderTeam(); updateScopeChip(); loadStats(); loadTraces(true);
    }
  } };
} });
