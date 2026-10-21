// Trace page — session picker + per-turn detail, real backend only.
// Shell: emitShell('trace') ran in index.html before this file loaded;
// bootShell() below fills the shared sidebar (roster + sessions).
//
// API: GET /api/traces?from&to&limit&offset&q&model&status  -> {traces, total}
//      GET /api/traces/<session_id>/<turn_index>           -> turn + messages[]
//      GET /api/bots, GET /api/sessions                   -> share/data.js via bootShell()
//
// Deep links — both resolve to the same turn detail:
//   index.html#<session_id>/<turn_index>
//   index.html?s=<session_id>&t=<turn_index>
// In-page navigation rewrites the URL via history.replaceState (no entries pushed).
'use strict';

const TRACE_PAGE_SIZE = 50;

/* TurnSummary.status -> [label, chip class]. Unknown statuses keep their raw id. */
const STATUS_META = {
  completed: ['Thành công', 's-ok'],
  failed: ['Lỗi', 's-error'],
  context_limit: ['Hết ngữ cảnh', 's-warn'],
  loop_limit: ['Chạm giới hạn', 's-warn'],
};

/* meta.kind values that read as a quiet metadata line instead of a bubble. */
const SIDE_KIND_LABEL = { naming: 'Đổi tên phiên', compaction: 'Nén ngữ cảnh' };
const BUS_KIND_LABEL = { delegate: 'Giao việc', step: 'Nhịp làm việc', status: 'Trạng thái', help_request: 'Cần hỗ trợ', card: 'Thẻ hỏi' };
const STOP_LABEL = { context_limit: 'Dừng — hết ngữ cảnh', loop_limit: 'Dừng — chạm giới hạn vòng lặp' };

const tr = {
  filters: { from: '', to: '', q: '', model: '', status: '' },
  botScope: '',           // '' = whole team, otherwise a bot id
  rows: [],               // flat trace rows, newest-first as the API returns them
  total: 0,
  loading: false,
  error: '',
  request: 0,             // stale-response guard: latest list request wins
  models: new Set(),      // model values observed in loaded rows -> filter options
  mode: 'list',           // 'list' | 'detail'
  listScroll: 0,
  detail: null,
  detailKey: '',
  detailError: '',
  detailRequest: 0,       // stale-response guard for the detail endpoint
};

/* ---------- static markup (fills the .workspace from emitShell) ---------- */

const PAGE_HTML = `
<header class="page-nav">
  <div class="page-title">
    <div class="eyebrow">Trace</div>
    <h1>Từng bước của lượt</h1>
    <p class="page-sub" id="page-sub">Mở một lượt để xem bot gọi công cụ nào, nghĩ gì và tốn bao nhiêu token.</p>
  </div>
  <div class="page-actions">
    <button class="quiet-button" id="scope-clear" type="button" hidden></button>
    <button class="quiet-button" id="trace-reload" type="button">${icon('retry')}<span>Tải lại</span></button>
  </div>
</header>
<div class="page-scroll" id="trace-scroll"><div class="page-inner">

  <section id="trace-list-view" aria-label="Các lượt theo phiên">
    <p class="page-status" id="trace-status" role="status" hidden></p>
    <form class="paper-card tl-filters" id="trace-filters" aria-label="Bộ lọc trace">
      <label class="tl-mini"><span>Từ ngày</span><input type="date" id="f-from" aria-label="Từ ngày"></label>
      <label class="tl-mini"><span>Đến ngày</span><input type="date" id="f-to" aria-label="Đến ngày"></label>
      <label class="tl-mini"><span>Model</span><select id="f-model"><option value="">Tất cả model</option></select></label>
      <label class="tl-mini"><span>Trạng thái</span><select id="f-status">
        <option value="">Tất cả trạng thái</option>
        <option value="completed">Thành công</option>
        <option value="failed">Lỗi</option>
        <option value="context_limit">Hết ngữ cảnh</option>
        <option value="loop_limit">Chạm giới hạn</option>
      </select></label>
      <label class="tl-mini tl-q"><span>Tìm kiếm</span><input type="search" id="f-q" autocomplete="off"
        placeholder="Tên phiên, mã phiên…" aria-label="Tìm theo tên phiên hoặc mã phiên"></label>
    </form>
    <div class="tl-sessions" id="trace-sessions" aria-live="polite"></div>
    <div class="tl-foot">
      <span class="status-line" id="trace-count"></span>
      <button class="quiet-button" id="trace-more" type="button" hidden>Tải thêm</button>
    </div>
  </section>

  <section id="trace-detail-view" aria-label="Chi tiết lượt" hidden>
    <button class="quiet-button tl-back" id="trace-back" type="button">${icon('arrow')}<span>Quay lại danh sách</span></button>
    <p class="page-status" id="trace-detail-status" role="status" hidden></p>
    <div id="trace-detail-body"></div>
  </section>
</div></div>`;

/* ---------- formatters (vi-VN; same rules as the dashboard page) ---------- */

const fmtInt = n => (typeof n === 'number' && Number.isFinite(n)) ? n.toLocaleString('vi-VN') : '—';
/* USD stays a $ figure; cost_usd null -> em dash, never an invented $0. */
function fmtCost(usd) {
  if (typeof usd !== 'number' || !Number.isFinite(usd)) return '—';
  return '$' + usd.toFixed(usd >= 1 ? 2 : usd >= 0.01 ? 4 : 6);
}
/* latency_ms -> "N ms" / "x.x giây" / "m ph y giây". */
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
const fmtTime = iso => {
  const d = new Date(iso);
  return Number.isFinite(d.getTime())
    ? d.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' })
    : '';
};
const safeJson = v => { try { return JSON.stringify(v, null, 2); } catch { return String(v); } };
const safeDecode = s => { try { return decodeURIComponent(s); } catch { return s; } };

/* Assistant text is markdown — same shape as chat's contentHtml: the shared
   sanitizer renders it once loaded, escaped paragraphs cover the gap before. */
const contentHtml = text => window.formatMarkdown
  ? window.formatMarkdown(text)
  : escapeHTML(String(text || '')).split(/\n{2,}/).map(p => `<p>${p.replace(/\n/g, '<br>')}</p>`).join('');

/* ---------- bots: live roster, id text when the bot is gone ---------- */

const botFor = id => (id && getBot(id)) || null;
const botName = id => { const b = botFor(id); return b ? b.name : (id || '—'); };
/* avatar() only renders roster bots — 'expression-preview' keeps list seals
   static (excluded from blink/sync loops). Callers print the raw id text
   next to a missing bot; the seals row swaps in a dashed id chip instead. */
const botSeal = id => {
  const b = botFor(id);
  return b ? avatar(b, b.state, 'expression-preview') : '';
};
const sealOrIdChip = id => botSeal(id)
  || `<span class="tl-botid" title="${escapeHTML(id || '')}">${escapeHTML(id || '—')}</span>`;

function statusChip(status) {
  const [label, cls] = STATUS_META[status] || [status || 'không rõ', ''];
  return `<span class="tl-chip ${cls}">${escapeHTML(label)}</span>`;
}

/* ---------- list mode ---------- */

/* Payload order is newest-first; group by session_id keeping first-seen order. */
function sessionGroups() {
  const map = new Map();
  for (const row of tr.rows) {
    let g = map.get(row.session_id);
    if (!g) { g = { sid: row.session_id, turns: [] }; map.set(row.session_id, g); }
    g.turns.push(row);
  }
  return [...map.values()];
}

/* Union of every bot id attributed anywhere in the group's loaded turns. */
function groupBotIds(g) {
  const ids = [];
  const push = id => { if (id && !ids.includes(id)) ids.push(id); };
  for (const t of g.turns) {
    (t.session_bots || []).forEach(push);
    (t.bot_breakdown || []).forEach(e => push(e.bot_id));
    push(t.bot_id);
    push(t.session_dm);
  }
  return ids;
}

/* Bots that actually worked this turn: bot_id first, then breakdown order. */
function turnBotIds(t) {
  const ids = [];
  const push = id => { if (id && !ids.includes(id)) ids.push(id); };
  push(t.bot_id);
  (t.bot_breakdown || []).forEach(e => push(e.bot_id));
  return ids;
}

function turnRowHtml(t) {
  const ids = turnBotIds(t);
  const botText = ids.length ? botName(ids[0]) + (ids.length > 1 ? ` +${ids.length - 1}` : '') : '—';
  return `<li><button type="button" class="tl-turn" data-session="${escapeHTML(t.session_id)}" data-turn="${t.turn_index}"
      title="Mở lượt #${t.turn_index} của “${escapeHTML(t.title || t.session_id)}”">
    <span class="tl-turn-idx">#${t.turn_index}</span>
    <span class="tl-turn-main">
      <span class="tl-turn-line"><b>${escapeHTML(botText)}</b><span class="tl-turn-title">${escapeHTML(t.title || '—')}</span></span>
      <span class="tl-turn-sub"><span>${escapeHTML(fmtDateTime(t.started_at))}</span><span>${escapeHTML(t.model || '—')}</span><span>${fmtInt(t.total_tokens)} tok</span><span>${fmtCost(t.cost_usd)}</span><span>${fmtDur(t.latency_ms)}</span></span>
    </span>
    ${statusChip(t.status)}
  </button></li>`;
}

function sessionCardHtml(g) {
  const t0 = g.turns[0];                                    // newest turn of the session
  const title = t0.title || g.sid;
  const ids = groupBotIds(g);
  const seals = ids.slice(0, 3).map(sealOrIdChip).join('')
    + (ids.length > 3 ? `<span class="tl-more-bots">+${ids.length - 3}</span>` : '');
  const failed = g.turns.filter(t => t.status === 'failed').length;
  const warned = g.turns.filter(t => t.status === 'context_limit' || t.status === 'loop_limit').length;
  const headChip = failed ? `<span class="tl-chip s-error">${failed} lượt lỗi</span>`
    : warned ? `<span class="tl-chip s-warn">${warned} lượt dừng sớm</span>` : '';
  const kind = t0.session_kind === 'dm' ? 'Trò chuyện riêng' : 'Hội thoại chung';
  return `<article class="paper-card tl-session">
    <header class="tl-session-head">
      <div class="tl-session-seals">${seals}</div>
      <div class="tl-session-main">
        <h2 class="tl-session-title">${escapeHTML(title)}</h2>
        <p class="tl-session-meta"><span>${kind}</span><span class="tl-sid">${escapeHTML(g.sid)}</span><span>${g.turns.length} lượt</span><span>mới nhất ${escapeHTML(fmtDateTime(t0.started_at))}</span></p>
      </div>
      ${headChip}
    </header>
    <ol class="tl-turns">${g.turns.map(turnRowHtml).join('')}</ol>
  </article>`;
}

function anyFilter() {
  const f = tr.filters;
  return !!(f.from || f.to || f.q || f.model || f.status);
}

function renderFoot() {
  const more = $('#trace-more');
  $('#trace-count').textContent = tr.loading && !tr.rows.length ? 'Đang đọc trace…'
    : tr.rows.length ? `Hiển thị ${fmtInt(tr.rows.length)} trong ${fmtInt(tr.total)} lượt`
    : tr.error ? '' : '';
  more.hidden = tr.loading || tr.rows.length >= tr.total || !!tr.error;
  more.disabled = tr.loading;
  more.textContent = tr.loading ? 'Đang tải…' : 'Tải thêm';
}

function renderList() {
  const box = $('#trace-sessions');
  box.setAttribute('aria-busy', String(tr.loading));
  setStatus(tr.error ? 'Không tải được danh sách lượt: ' + tr.error : '', tr.error ? 's-error' : '');
  if (!tr.rows.length) {
    box.innerHTML = (!tr.loading && !tr.error)
      ? `<div class="empty-note"><strong>Không có lượt nào khớp.</strong>${anyFilter()
          ? 'Nới bộ lọc hoặc đổi khoảng ngày để xem thêm.'
          : 'Chưa có lượt nào được ghi lại — các lượt sẽ xuất hiện sau khi bot trò chuyện.'}</div>`
      : '';
    renderFoot();
    return;
  }
  box.innerHTML = sessionGroups().map(sessionCardHtml).join('');
  renderFoot();
}

/* ---------- detail mode ---------- */

function setStatus(text, cls) {
  const el = $('#trace-status');
  el.hidden = !text;
  el.className = 'page-status' + (cls ? ' ' + cls : '');
  el.textContent = text || '';
}
function setDetailStatus(text, cls) {
  const el = $('#trace-detail-status');
  el.hidden = !text;
  el.className = 'page-status' + (cls ? ' ' + cls : '');
  el.textContent = text || '';
}

function showMode(mode) {
  tr.mode = mode;
  $('#trace-list-view').hidden = mode !== 'list';
  $('#trace-detail-view').hidden = mode !== 'detail';
  if (mode === 'detail') $('#trace-scroll').scrollTop = 0;
}

/* Synthetic stop markers (context/loop guard) and bus envelope rows are not
   provider calls — same exclusions the backend uses for request counts. */
const isSyntheticStop = m => {
  const meta = m.meta || {};
  return (meta.status === 'context_limit' || meta.status === 'loop_limit') && meta.kind !== 'message';
};
const isBusRow = m => {
  const meta = m.meta || {};
  return meta.bus_event === true || BUS_KIND_LABEL[meta.kind] !== undefined;
};

/* Quiet metadata line for system/sidecar/bus rows. */
function metaLineHtml(m, time) {
  const meta = m.meta || {};
  const kind = meta.kind;
  let label, cls = '';
  if (m.role === 'system') label = 'Hệ thống';
  else if (SIDE_KIND_LABEL[kind]) label = SIDE_KIND_LABEL[kind];
  else if (STOP_LABEL[meta.status]) { label = STOP_LABEL[meta.status]; cls = ' s-warn'; }
  else if (meta.bus_event === true || BUS_KIND_LABEL[kind]) label = BUS_KIND_LABEL[kind] || 'Sự kiện bot';
  else label = kind || m.role || 'meta';
  const who = meta.bot_id ? ' · ' + botName(meta.bot_id) : '';
  const preview = String(m.content ?? '').trim();
  return `<div class="tl-meta${cls}"><span class="tl-meta-kind">${escapeHTML(label)}${escapeHTML(who)}</span>` +
    (preview ? `<span class="tl-meta-text">${escapeHTML(preview.length > 240 ? preview.slice(0, 240) + '…' : preview)}</span>` : '') +
    `<time>${escapeHTML(time)}</time></div>`;
}

function userHtml(m, time) {
  const meta = m.meta || {};
  const to = meta.bot_id ? `<span class="tl-msg-meta">→ ${escapeHTML(botName(meta.bot_id))}</span>` : '';
  return `<div class="tl-item tl-item-user"><div class="tl-user"><p>${escapeHTML(String(m.content ?? '')).replace(/\n/g, '<br>')}</p><footer>${to}<time>${escapeHTML(time)}</time></footer></div></div>`;
}

/* reasoning + reasoning_details -> one "Suy nghĩ" fold (encrypted parts
   collapse to a marker — the provider never sends readable text for them). */
function reasonHtml(m) {
  const parts = [];
  if (typeof m.reasoning === 'string' && m.reasoning.trim()) parts.push(m.reasoning);
  (Array.isArray(m.reasoning_details) ? m.reasoning_details : []).forEach(d => {
    if (!d || typeof d !== 'object') return;
    if (d.type === 'reasoning.text' && d.text) parts.push(String(d.text));
    else if (d.type === 'reasoning.summary' && d.summary) parts.push(String(d.summary));
    else if (d.type === 'reasoning.encrypted') parts.push('(đoạn suy nghĩ đã mã hoá)');
  });
  if (!parts.length) return '';
  return `<details class="tl-reason"><summary>Suy nghĩ</summary><pre class="tl-code">${escapeHTML(parts.join('\n\n'))}</pre></details>`;
}

/* One numbered step per tool call; the matching tool message supplies output.
   parse_error on the call or is_error on the result mark the step s-error. */
function stepHtml(n, call, res) {
  const name = call.skill || call.name || 'tool';
  const flags = [];
  if (call.parse_error) flags.push('Lỗi cú pháp');
  if (res && res.meta && res.meta.is_error === true) flags.push('Thực thi lỗi');
  const metaBits = [];
  if (call.skill && call.name && call.skill !== call.name) metaBits.push('tool ' + call.name);
  if (res && res.meta && typeof res.meta.latency_ms === 'number') metaBits.push(fmtDur(res.meta.latency_ms));
  if (call.id) metaBits.push(call.id);
  const resErr = !!(res && res.meta && res.meta.is_error === true);
  const outHtml = res
    ? `<pre class="tl-code${resErr ? ' tl-code-err' : ''}">${escapeHTML(String(res.content ?? '')) || '(trống)'}</pre>`
    : '<p class="tl-step-meta">Chưa ghi nhận output.</p>';
  return `<li class="tl-step${flags.length ? ' s-error' : ''}"><span class="tl-step-num">${String(n).padStart(2, '0')}</span>
    <div><p class="tl-step-title">${escapeHTML(name)}${flags.map(f => ` <span class="tl-step-flag">${escapeHTML(f)}</span>`).join('')}</p>
    ${metaBits.length ? `<p class="tl-step-meta">${escapeHTML(metaBits.join(' · '))}</p>` : ''}
    <details class="tl-step-data"><summary>Tham số &amp; kết quả</summary>
      <p class="tl-io-label">Input</p><pre class="tl-code">${escapeHTML(safeJson(call.arguments ?? {}))}</pre>
      <p class="tl-io-label">Output</p>${outHtml}
      ${call.parse_error ? `<p class="tl-io-label">Parse error</p><pre class="tl-code tl-code-err">${escapeHTML(call.parse_error)}</pre>` : ''}
    </details></div></li>`;
}

/* Tool result with no matching call id — still surfaced as its own step. */
function orphanStepHtml(n, m, time) {
  const resErr = !!(m.meta && m.meta.is_error === true);
  return `<li class="tl-step${resErr ? ' s-error' : ''}"><span class="tl-step-num">${String(n).padStart(2, '0')}</span>
    <div><p class="tl-step-title">Kết quả tool${resErr ? ' <span class="tl-step-flag">Thực thi lỗi</span>' : ''}</p>
    <p class="tl-step-meta">${escapeHTML([m.tool_call_id ? 'call ' + m.tool_call_id : '', time].filter(Boolean).join(' · '))}</p>
    <p class="tl-io-label">Output</p>
    <pre class="tl-code${resErr ? ' tl-code-err' : ''}">${escapeHTML(String(m.content ?? '')) || '(trống)'}</pre></div></li>`;
}

function timelineHtml(d) {
  const messages = Array.isArray(d.messages) ? d.messages : [];
  const results = new Map();      // tool_call_id -> tool message
  const callIds = new Set();
  for (const m of messages) {
    if (!m) continue;
    if (m.role === 'tool' && m.tool_call_id) results.set(m.tool_call_id, m);
    (m.tool_calls || []).forEach(c => { if (c && c.id) callIds.add(c.id); });
  }
  let stepNo = 0;
  const out = [];

  for (const m of messages) {
    if (!m) continue;
    const meta = m.meta || {};
    const kind = meta.kind;
    const time = fmtTime(m.ts);

    if (m.role === 'system' || SIDE_KIND_LABEL[kind]) { out.push(metaLineHtml(m, time)); continue; }
    if (m.role === 'user') { out.push(userHtml(m, time)); continue; }
    if (m.role === 'tool') {
      /* Exactly one message per call id satisfies results.get()===m — that one
         is rendered inside the call's step; dups still get their own step. */
      if (m.tool_call_id && callIds.has(m.tool_call_id)
          && results.get(m.tool_call_id) === m) continue;
      out.push(`<ol class="tl-steps">${orphanStepHtml(++stepNo, m, time)}</ol>`);
      continue;
    }
    if (m.role === 'assistant') {
      if (isSyntheticStop(m) || isBusRow(m)) { out.push(metaLineHtml(m, time)); continue; }
      const botId = meta.bot_id || d.bot_id || '';
      const b = botFor(botId);
      const bits = [];
      if (meta.model) bits.push(meta.model);
      if (typeof meta.latency_ms === 'number') bits.push(fmtDur(meta.latency_ms));
      if (meta.usage && typeof meta.usage.total_tokens === 'number') bits.push(fmtInt(meta.usage.total_tokens) + ' tok');
      if (typeof meta.round === 'number' && meta.round > 1) bits.push('vòng ' + meta.round);
      if (meta.finish_reason && meta.finish_reason !== 'stop' && meta.finish_reason !== 'tool_calls') bits.push('kết thúc ' + meta.finish_reason);
      const errMeta = meta.error && typeof meta.error === 'object'
        ? `<p class="tl-error">${escapeHTML(meta.error.code || 'error')}: ${escapeHTML(meta.error.message || '')}</p>` : '';
      const body = contentHtml(m.content);
      const steps = (m.tool_calls || []).map(call =>
        stepHtml(++stepNo, call, call.id ? results.get(call.id) : null));
      out.push(`<div class="tl-item tl-item-assistant"${b ? ` style="${paletteStyle(b)}"` : ''}>
        <header class="tl-byline">${botSeal(botId)}<span>${escapeHTML(botName(botId))}</span><time>${escapeHTML(time)}</time>${bits.length ? `<span class="tl-byline-meta">${escapeHTML(bits.join(' · '))}</span>` : ''}</header>
        ${errMeta}${reasonHtml(m)}
        ${body ? `<div class="tl-body markdown-body">${body}</div>` : ''}
        ${steps.length ? `<ol class="tl-steps">${steps.join('')}</ol>` : ''}
      </div>`);
      continue;
    }
    /* Unknown role — surfaced as a metadata line, never dropped silently. */
    out.push(metaLineHtml(m, time));
  }
  return out.join('') || `<div class="empty-note"><strong>Lượt này không ghi message nào.</strong></div>`;
}

function detailHeadHtml(d) {
  const ids = turnBotIds(d);
  const byId = new Map((d.bot_breakdown || []).map(e => [e.bot_id, e]));
  const botsHtml = ids.length ? ids.map(id => {
    const e = byId.get(id);
    const bits = [];
    if (e && typeof e.requests === 'number' && e.requests) bits.push(fmtInt(e.requests) + ' req');
    if (e && typeof e.total_tokens === 'number') bits.push(fmtInt(e.total_tokens) + ' tok');
    const b = botFor(id);
    return `<span class="tl-bot"${b ? ` style="${paletteStyle(b)}"` : ''}>${botSeal(id)}<span>${escapeHTML(botName(id))}${bits.length ? ' · ' + bits.join(' · ') : ''}</span></span>`;
  }).join('') : '—';
  const rows = [
    ['Phiên', `<span class="tl-mono">${escapeHTML(d.session_id)}</span>`],
    ['Thời gian', `${escapeHTML(fmtDateTime(d.started_at))} → ${escapeHTML(fmtTime(d.ended_at) || '—')}`],
    ['Bot', `<span class="tl-bots">${botsHtml}</span>`],
    ['Model', escapeHTML(d.model || '—')],
    ['Gọi model', `${fmtInt(d.requests)} request · ${fmtInt(d.rounds)} vòng · ${fmtInt(d.tool_calls)} tool`],
    ['Token', `vào ${fmtInt(d.prompt_tokens)} · cache ${fmtInt(d.cached_tokens)} · ra ${fmtInt(d.completion_tokens)} · tổng ${fmtInt(d.total_tokens)}`],
    ['Chi phí', fmtCost(d.cost_usd)],
    ['Thời lượt', fmtDur(d.latency_ms)],
  ];
  if (d.trace_id) rows.push(['Trace', `<span class="tl-mono">${escapeHTML(d.trace_id)}</span>`]);
  if (d.work_id) rows.push(['Work', `<span class="tl-mono">${escapeHTML(d.work_id)}</span>`]);
  const err = d.error && (d.error.message || d.error.code)
    ? `<p class="tl-error-line"><b>Lỗi${d.error.code ? ' · ' + escapeHTML(d.error.code) : ''}</b>${escapeHTML(d.error.message || '')}</p>`
    : '';
  return `<article class="paper-card tl-detail-head">
    <div class="eyebrow">Lượt #${d.turn_index} ${statusChip(d.status)}</div>
    <h2 class="tl-detail-title">${escapeHTML(d.title || d.session_id)}</h2>
    <ul class="meta-list tl-detail-meta">${rows.map(([k, v]) => `<li><span>${k}</span><span>${v}</span></li>`).join('')}</ul>
    ${err}
  </article>`;
}

function renderDetail() {
  const box = $('#trace-detail-body');
  if (tr.detailError) {
    setDetailStatus('Không đọc được lượt ' + tr.detailKey + ': ' + tr.detailError, 's-error');
    box.innerHTML = `<div class="empty-note"><strong>Không mở được trace này.</strong>Lượt có thể đã bị xoá, hoặc phiên không còn đọc được. Quay lại danh sách để chọn lượt khác.</div>`;
    return;
  }
  const d = tr.detail;
  if (!d) return;
  setDetailStatus('');
  box.innerHTML = detailHeadHtml(d) + `<div class="tl-timeline">${timelineHtml(d)}</div>`;
}

/* ---------- data ---------- */

/* API: GET /api/traces?from&to&limit&offset&q&model&status — paged, newest first. */
async function loadTraces(reset) {
  if (reset) tr.rows = [];
  const seq = ++tr.request;
  tr.loading = true;
  tr.error = '';
  setStatus('');
  renderList();
  const q = new URLSearchParams();
  const f = tr.filters;
  if (f.from) q.set('from', f.from);
  if (f.to) q.set('to', f.to);
  if (f.q) q.set('q', f.q);
  if (f.model) q.set('model', f.model);
  if (f.status) q.set('status', f.status);
  if (tr.botScope) q.set('bot_id', tr.botScope);
  q.set('limit', String(TRACE_PAGE_SIZE));
  q.set('offset', String(tr.rows.length));
  try {
    const data = await getJson('/api/traces?' + q);
    if (seq !== tr.request) return;
    tr.rows = tr.rows.concat(Array.isArray(data.traces) ? data.traces : []);
    tr.total = typeof data.total === 'number' ? data.total : tr.rows.length;
    tr.rows.forEach(t => { if (t.model) tr.models.add(t.model); });
    fillModelOptions();
  } catch (err) {
    if (seq !== tr.request) return;
    tr.error = err.message || 'lỗi không rõ';
  } finally {
    if (seq === tr.request) tr.loading = false;
  }
  if (seq === tr.request) renderList();
}

/* API: GET /api/traces/<session_id>/<turn_index> — one turn + expanded messages. */
async function openTurn(sid, turn, { push = true } = {}) {
  if (tr.mode === 'list') tr.listScroll = $('#trace-scroll').scrollTop;
  tr.detail = null;
  tr.detailError = '';
  tr.detailKey = sid + '/' + turn;
  const seq = ++tr.detailRequest;
  showMode('detail');
  setDetailStatus('');
  $('#trace-detail-body').innerHTML = '<p class="status-line">Đang đọc lượt…</p>';
  if (push) history.replaceState(null, '', location.pathname + pageQuery() + '#' + encodeURIComponent(sid) + '/' + turn);
  try {
    const detail = await getJson(`/api/traces/${encodeURIComponent(sid)}/${turn}`);
    if (seq !== tr.detailRequest) return;
    tr.detail = detail;
    renderDetail();
  } catch (err) {
    if (seq !== tr.detailRequest) return;
    tr.detailError = err.message || 'không đọc được';
    renderDetail();
  }
}

/* Page URL keeps the bot scope as ?bot=<id>; a turn detail appends its
   #sid/turn hash on top so deep links still resolve. */
function pageQuery() {
  return tr.botScope ? '?bot=' + encodeURIComponent(tr.botScope) : '';
}

/* Sidebar routing stays on this page: a bot card scopes the turn list to
   that bot (server-side bot_id filter). Clicking the scoped bot again, or
   "Bỏ lọc", clears it; a group/session scopes to the owning bot. A turn
   detail already open keeps showing — only the list underneath reloads. */
function updateScopeChip() {
  const btn = $('#scope-clear');
  const b = tr.botScope && getBot(tr.botScope);
  btn.hidden = !tr.botScope;
  btn.textContent = tr.botScope ? 'Bỏ lọc: ' + (b ? b.name : tr.botScope) + ' ✕' : '';
  $('#page-sub').textContent = tr.botScope
    ? `Các lượt của ${b ? b.name : tr.botScope} — mở một lượt để xem từng bước.`
    : 'Mở một lượt để xem bot gọi công cụ nào, nghĩ gì và tốn bao nhiêu token.';
}
function selectBot(id) {
  tr.botScope = !id || tr.botScope === id ? '' : id;
  selectedBot = tr.botScope;
  renderTeam();
  updateScopeChip();
  history.replaceState(null, '', location.pathname + pageQuery() + location.hash);
  loadTraces(true);                                    // API: GET /api/traces?…&bot_id=<id>
}
function loadSession(id, title, botId) { selectBot(botId || ''); }

function backToList({ push = true } = {}) {
  tr.detailRequest += 1;    // a detail fetch already in flight lands nowhere
  tr.detail = null;
  tr.detailKey = '';
  tr.detailError = '';
  if (push) history.replaceState(null, '', location.pathname + pageQuery());
  showMode('list');
  setDetailStatus('');
  $('#trace-scroll').scrollTop = tr.listScroll || 0;
}

/* Hash form: #<session_id>/<turn_index>. Query form: ?s=<id>&t=<index>. */
function routeFromUrl() {
  const hash = location.hash.slice(1);
  if (hash) {
    const sep = hash.indexOf('/');
    if (sep > 0) {
      const sid = safeDecode(hash.slice(0, sep));
      const t = safeDecode(hash.slice(sep + 1));
      if (sid && /^\d+$/.test(t)) return { sid, turn: parseInt(t, 10) };
    }
    return null;
  }
  const p = new URLSearchParams(location.search);
  const s = p.get('s'), t = p.get('t');
  if (s && t !== null && /^\d+$/.test(t)) return { sid: s, turn: parseInt(t, 10) };
  return null;
}

/* ---------- events ---------- */

function syncFilters() {
  tr.filters.from = $('#f-from').value;
  tr.filters.to = $('#f-to').value;
  tr.filters.q = $('#f-q').value.trim();
  tr.filters.model = $('#f-model').value;
  tr.filters.status = $('#f-status').value;
}

function reloadList() {
  const f = tr.filters;
  if (f.from && f.to && f.from > f.to) { toast('Ngày bắt đầu phải trước hoặc bằng ngày kết thúc.'); return; }
  loadTraces(true);
}

const makeOption = (value, text) => Object.assign(document.createElement('option'), { value, textContent: text });
function fillModelOptions() {
  const sel = $('#f-model'), cur = tr.filters.model;
  const list = [...tr.models].sort();
  sel.replaceChildren(
    makeOption('', 'Tất cả model'),
    ...list.map(m => makeOption(m, m)),
    ...(cur && !list.includes(cur) ? [makeOption(cur, cur)] : []));
  sel.value = cur;
}

function wirePage() {
  const onFilter = () => { syncFilters(); reloadList(); };
  ['f-from', 'f-to', 'f-model', 'f-status'].forEach(id => $('#' + id).addEventListener('change', onFilter));
  let qTimer = null;
  $('#f-q').addEventListener('input', () => {
    clearTimeout(qTimer);
    qTimer = setTimeout(onFilter, 350);
  });
  $('#trace-filters').addEventListener('submit', e => { e.preventDefault(); onFilter(); });
  $('#trace-reload').addEventListener('click', () => {
    /* On a turn the button re-reads that turn; on the picker it re-queries the list. */
    if (tr.mode === 'detail' && tr.detailKey) {
      const sid = tr.detailKey.slice(0, tr.detailKey.lastIndexOf('/'));
      openTurn(sid, Number(tr.detailKey.slice(tr.detailKey.lastIndexOf('/') + 1)), { push: false });
    } else {
      syncFilters();
      loadTraces(true);
    }
  });
  $('#scope-clear').addEventListener('click', () => selectBot(''));
  $('#trace-more').addEventListener('click', () => loadTraces(false));
  $('#trace-sessions').addEventListener('click', e => {
    const btn = e.target.closest('.tl-turn');
    if (btn) openTurn(btn.dataset.session, Number(btn.dataset.turn));
  });
  $('#trace-back').addEventListener('click', () => backToList());
  addEventListener('hashchange', () => {
    const route = routeFromUrl();
    if (route) openTurn(route.sid, route.turn, { push: false });
    else backToList({ push: true });
  });
}

/* ---------- boot ---------- */

function boot() {
  $('.workspace').insertAdjacentHTML('beforeend', PAGE_HTML);
  wirePage();
  /* ?bot=<id> deep link scopes the first fetch already; if the roster says
     the id is stale the scope clears (and refetches) once bootShell lands. */
  const wanted = new URLSearchParams(location.search).get('bot');
  if (wanted) tr.botScope = wanted;
  const route = routeFromUrl();
  loadTraces(true);                                   // API: GET /api/traces?…
  if (route) openTurn(route.sid, route.turn);         // API: GET /api/traces/<s>/<t>
  bootShell()                                         // API: GET /api/bots + /api/sessions
    .then(() => {
      if (tr.botScope && !getBot(tr.botScope)) { tr.botScope = ''; selectBot(''); }
      else if (tr.botScope) { selectedBot = tr.botScope; renderTeam(); }
      updateScopeChip();
      /* Rows rendered before the roster arrived drew id-text fallbacks —
         repaint once bots are in so seals/names resolve. */
      renderList();
      if (tr.detail) renderDetail();
    })
    .catch(err => toast('Không tải được đội bot: ' + (err.message || '')));
}
boot();
