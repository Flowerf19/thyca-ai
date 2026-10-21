// Nhật ký (Memories) page — real memory stats + leaf actions, no sample data.
// Shared shell: emitShell('memories') already ran before this file loaded
// (see index.html); bootShell() fills the sidebar roster.
// API: GET  /api/memory/stats[?bot_id=<id>] — stats row + leaf list
//      POST /api/memory/reinforce {session_id}          — renew a leaf's TTL
//      POST /api/memory/update {session_id, topic, summary} — edit a leaf
//      POST /api/memory/forget {session_id}             — drop a leaf
'use strict';

/* Same rules as thyca/memory/stats.py so per-leaf badges match the
   server-computed expiring/suggest_removal lists after a local update. */
const EXPIRE_SOON_DAYS = 14;
const SUGGEST_IDLE_DAYS = 7;
const LEAF_PAGE = 60;     // leaves per "Tải thêm" click — big scopes hold thousands

const memState = {
  scope: '',            // '' = all bots, otherwise a bot id
  stats: null,
  byChunk: new Map(),   // chunk_id -> leaf (action lookup)
  expiring: new Set(),  // chunk_ids from stats.expiring
  suggest: new Set(),   // chunk_ids from stats.suggest_removal
  query: '',
  kind: '',             // '' = all kinds; 'none' = untyped leaf, else TASK_KINDS value
  use: '',              // '' = all; 'used' = get_count>=1, 'untouched' = never get+search
  page: 1,              // 1-based leaf page — reset on scope/query/new stats
  request: 0,           // stats request token — stale answers drop out
  editing: null,        // leaf being edited in the dialog
};

/* ---------- workspace markup ---------- */

const PAGE_HTML = `
<header class="page-nav">
  <div class="page-title">
    <div class="eyebrow">Nhật ký</div>
    <h1>Bộ nhớ</h1>
    <p class="page-sub">Bộ nhớ các bot ghi lại</p>
  </div>
  <div class="page-actions">
    <select class="inline-field mem-kind-field" id="memory-kind" aria-label="Lọc loại kí ức">
      <option value="">Lọc loại</option>
      <option value="none">Ghi nhớ</option>
      <option value="habit">Thói quen</option>
      <option value="checkpoint">Task</option>
    </select>
    <select class="inline-field mem-use-field" id="memory-use" aria-label="Lọc mức dùng">
      <option value="">Lọc mức dùng</option>
      <option value="used">Dùng nhiều</option>
      <option value="untouched">Không dùng bao giờ</option>
    </select>
    <input class="inline-field" id="memory-search" type="search" autocomplete="off"
      placeholder="Tìm trong nhật ký…" aria-label="Tìm trong nhật ký">
    <button class="quiet-button" id="memory-reload" type="button">${icon('retry')}<span>Tải lại</span></button>
  </div>
</header>
<div class="page-scroll">
  <div class="page-inner section-stack">
    <p class="page-status" id="memory-status" hidden></p>
    <article class="paper-card" id="memory-scope-card" hidden></article>
    <article class="paper-card" aria-label="Thống kê bộ nhớ">
      <div class="eyebrow">Tổng quan</div>
      <div class="mem-stats" id="memory-stats"></div>
    </article>
    <article class="paper-card" aria-label="Các lá nhớ">
      <div class="eyebrow">Lá nhớ</div>
      <div class="mem-list-top">
        <span class="status-line" id="memory-loading">Đang đọc bộ nhớ…</span>
        <span class="mem-count" id="memory-count"></span>
      </div>
      <div id="memory-leaves" aria-live="polite"></div>
    </article>
  </div>
</div>`;

/* Page-local edit dialog — shared dialog wiring in shell-app.js ran before
   this markup exists, so memories.js binds its own close/backdrop. */
const DIALOG_HTML = `
<dialog class="screen-dialog" id="memory-edit-dialog" aria-labelledby="memory-edit-title">
  <div class="dialog-heading"><div>
    <h2 id="memory-edit-title">Sửa trang nhật ký</h2>
    <p>Đổi tiêu đề và nội dung tóm tắt của lá nhớ này.</p>
  </div><button class="icon-button dialog-close" aria-label="Đóng" type="button">${icon('close')}</button></div>
  <form class="dialog-body" id="memory-edit-form">
    <label class="field"><span>Tiêu đề</span>
      <input id="mem-edit-topic" maxlength="120" required autocomplete="off" placeholder="Tên trang nhật ký"></label>
    <label class="field"><span>Nội dung</span>
      <textarea id="mem-edit-summary" required maxlength="400" placeholder="Nội dung ghi lại từ phiên trò chuyện"></textarea></label>
    <div class="dialog-actions">
      <button class="quiet-button" id="mem-edit-cancel" type="button">Hủy</button>
      <button class="primary-action" type="submit">Lưu trang</button>
    </div>
  </form>
</dialog>`;

/* ---------- helpers ---------- */

/* Leaf heading is "## HH:mm — title" (machine comment stripped); tolerate a
   trailing " HH:MM" too, and plain titles with no time at all. */
function splitHeading(leaf) {
  const raw = String(leaf?.heading || '').replace(/^##\s*/, '').trim();
  let match = raw.match(/^(\d{2}:\d{2})\s*[—–-]\s*(.+)$/);
  if (match) return { time: match[1], title: match[2].trim() };
  match = raw.match(/^(.+?)\s+(\d{2}:\d{2})$/);
  if (match) return { time: match[2], title: match[1].trim() };
  return { time: '', title: raw || 'Trang không tên' };
}

const isoDay = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

function dayLabel(leaf) {
  const day = leaf.timeline_day;
  const today = isoDay(new Date());
  if (day === today || leaf.is_today) return 'Hôm nay';
  if (!day) return 'Không rõ ngày';
  if (day === isoDay(new Date(Date.now() - 864e5))) return 'Hôm qua';
  const parsed = new Date(day + 'T00:00:00');
  return Number.isFinite(parsed.getTime()) ? parsed.toLocaleDateString('vi-VN') : day;
}

function fmtTs(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (!Number.isFinite(d.getTime())) return String(iso);
  return d.toLocaleDateString('vi-VN', { day: 'numeric', month: 'numeric', year: 'numeric' })
    + ' ' + d.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' });
}

/* Owners: null/["team"] reads as shared memory, ids map to live bot names. */
function ownerLabel(bots) {
  if (!Array.isArray(bots) || !bots.length) return 'chung';
  return bots.map(id => id === 'team' ? 'chung' : escapeHTML((getBot(id) || { name: id }).name)).join(', ');
}

const KIND_LABEL = { habit: 'thói quen', checkpoint: 'task' };
const STATUS_LABEL = { open: 'đang mở', done: 'đã xong' };

/* Client mirror of stats.py expiring_soon / suggest rules — reused after a
   reinforce response updates expires_at in place. */
function leafExpiring(leaf) {
  if (leaf.is_today || !leaf.expires_at) return false;
  const exp = new Date(leaf.expires_at);
  return Number.isFinite(exp.getTime()) && exp.getTime() <= Date.now() + EXPIRE_SOON_DAYS * 864e5;
}
function leafSuggest(leaf) {
  if (leaf.is_today || leaf.get_count >= 1 || leaf.search_count >= 1) return false;
  const touched = Date.parse(leaf.last_get_at || leaf.last_search_at || (leaf.timeline_day ? leaf.timeline_day + 'T00:00:00Z' : ''));
  return Number.isFinite(touched) && Date.now() - touched >= SUGGEST_IDLE_DAYS * 864e5;
}

/* Snippet is raw markdown leaf text (≤250 chars, may cut mid-structure);
   render with the shared sanitizer when it loaded, escaped text otherwise. */
function snippetHtml(text) {
  if (window.formatMarkdown) {
    const html = window.formatMarkdown(text);
    if (html) return html;
  }
  const t = String(text || '').trim();
  return t ? `<p>${escapeHTML(t).replace(/\n/g, '<br>')}</p>` : '<p><em>(trống)</em></p>';
}

function chipsHtml(leaf) {
  const chips = [];
  if (leaf.is_today) chips.push('<span class="chip" data-active="true">Hôm nay</span>');
  if (memState.expiring.has(leaf.chunk_id) || leafExpiring(leaf)) chips.push('<span class="chip">Sắp hết hạn</span>');
  if (memState.suggest.has(leaf.chunk_id) || leafSuggest(leaf)) chips.push('<span class="chip">Gợi ý xóa</span>');
  if (leaf.kind) chips.push(`<span class="chip">${escapeHTML(KIND_LABEL[leaf.kind] || leaf.kind)}${leaf.habit ? ' · ' + escapeHTML(leaf.habit) : ''}</span>`);
  if (leaf.status) chips.push(`<span class="chip">${escapeHTML(STATUS_LABEL[leaf.status] || leaf.status)}</span>`);
  return chips.join('');
}

function metaHtml(leaf) {
  const parts = [];
  if (leaf.timeline_day) parts.push(`<span>${escapeHTML(dayLabel(leaf))}</span>`);
  parts.push(`<span>đã dùng ${leaf.get_count || 0}</span>`);
  parts.push(`<span>tìm thấy ${leaf.search_count || 0}</span>`);
  if (leaf.expires_at) parts.push(`<span class="mem-expiring">hết hạn ${escapeHTML(fmtTs(leaf.expires_at))}</span>`);
  parts.push(`<span>${ownerLabel(leaf.bots)}</span>`);
  parts.push(`<span class="mem-sid">${escapeHTML(leaf.session_id || '')}</span>`);
  return parts.join('');
}

/* ---------- rendering ---------- */

function renderStats() {
  const s = memState.stats;
  const tiles = s ? [
    ['Tổng lá', s.total],
    ['Đã dùng', s.used],
    ['Chưa dùng', s.unused],
    ['Được tìm', s.searched],
    ['Chưa đụng tới', s.untouched],
    ['Sắp hết hạn', (s.expiring || []).length, 'm-warn'],
    ['Gợi ý xóa', (s.suggest_removal || []).length, 'm-danger'],
  ] : [['Tổng lá', '—'], ['Đã dùng', '—'], ['Chưa dùng', '—'], ['Được tìm', '—'], ['Chưa đụng tới', '—'], ['Sắp hết hạn', '—', 'm-warn'], ['Gợi ý xóa', '—', 'm-danger']];
  $('#memory-stats').innerHTML = tiles.map(([label, value, cls]) =>
    `<div class="mem-stat${cls ? ' ' + cls : ''}"><b>${value ?? 0}</b><span>${label}</span></div>`).join('');
}

function renderScopeCard() {
  const card = $('#memory-scope-card');
  const b = memState.scope && getBot(memState.scope);
  if (!b) { card.hidden = true; card.innerHTML = ''; return; }
  const count = memState.stats ? memState.stats.total ?? 0 : 0;
  const state = states[b.state] ? states[b.state][1] : '';
  card.hidden = false;
  card.style.cssText = paletteStyle(b);
  card.innerHTML = `<div class="mem-scope">${avatar(b)}<div class="mem-scope-main">
    <h3 class="mem-scope-name">${escapeHTML(b.name)} <span class="mem-scope-role">${escapeHTML(b.role || '')}</span></h3>
    <p class="mem-scope-meta">${count} lá nhớ${state ? ' · ' + escapeHTML(state) : ''}</p></div>
    <button class="quiet-button mem-scope-clear" type="button">Bỏ lọc</button></div>`;
}

function filteredLeaves() {
  let leaves = memState.stats?.leaves || [];
  /* kind mirrors TASK_KINDS in memory/heading.py — 'none' means untyped. */
  if (memState.kind === 'none') leaves = leaves.filter(l => !l.kind);
  else if (memState.kind) leaves = leaves.filter(l => l.kind === memState.kind);
  /* use mirrors stats.py: used = get_count>=1, untouched = no get and no search. */
  if (memState.use === 'used') leaves = leaves.filter(l => (l.get_count || 0) >= 1);
  else if (memState.use === 'untouched') leaves = leaves.filter(l => !(l.get_count || 0) && !(l.search_count || 0));
  const needle = memState.query.trim().toLocaleLowerCase('vi');
  if (!needle) return leaves;
  return leaves.filter(leaf => {
    const { title } = splitHeading(leaf);
    return `${title} ${leaf.snippet || ''} ${leaf.session_id || ''} ${leaf.heading || ''}`
      .toLocaleLowerCase('vi').includes(needle);
  });
}

function leafHtml(leaf) {
  const { time, title } = splitHeading(leaf);
  return `<article class="mem-leaf" data-chunk="${escapeHTML(leaf.chunk_id)}">
  <div class="mem-leaf-main">
    <div class="mem-leaf-head"><h4 class="mem-leaf-title">${escapeHTML(title)}</h4>${time ? `<time class="mem-leaf-time">${escapeHTML(time)}</time>` : ''}</div>
    <div class="mem-chips">${chipsHtml(leaf)}</div>
    <div class="mem-snippet markdown-body">${snippetHtml(leaf.snippet)}</div>
    <p class="mem-meta">${metaHtml(leaf)}</p>
  </div>
  <div class="mem-actions">
    <button class="quiet-button" data-act="reinforce" type="button">${icon('retry')}<span>Gia hạn</span></button>
    <button class="quiet-button" data-act="edit" type="button">Sửa</button>
    <button class="quiet-button is-danger" data-act="forget" type="button">Quên</button>
  </div></article>`;
}

function renderLeaves() {
  const box = $('#memory-leaves');
  const leaves = filteredLeaves();
  const total = (memState.stats?.leaves || []).length;
  const pages = Math.max(1, Math.ceil(leaves.length / LEAF_PAGE));
  memState.page = Math.min(Math.max(1, memState.page), pages);
  const visible = leaves.slice((memState.page - 1) * LEAF_PAGE, memState.page * LEAF_PAGE);
  $('#memory-count').textContent = memState.stats
    ? (leaves.length === total ? `${total} lá` : `${leaves.length}/${total} lá`)
    : '';
  if (!memState.stats) { box.innerHTML = ''; return; }
  if (!leaves.length) {
    box.innerHTML = `<div class="empty-note"><strong>${memState.query ? 'Không tìm thấy lá nào' : 'Chưa có lá nhớ nào'}</strong>${memState.query ? 'Thử từ khóa khác hoặc xóa bộ lọc tìm kiếm.' : (memState.scope ? 'Bot này chưa ghi trang nào vào bộ nhớ.' : 'Các bot sẽ ghi lại điều học được sau mỗi phiên trò chuyện.')}</div>`;
    return;
  }
  /* Group by timeline_day — Hôm nay/Hôm qua/date, payload order kept inside. */
  const groups = new Map();
  for (const leaf of visible) {
    const key = leaf.timeline_day || '';
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(leaf);
  }
  const days = [...groups.keys()].sort((a, b) => (a === '') - (b === '') || b.localeCompare(a));
  box.innerHTML = days.map(day => {
    const items = groups.get(day);
    const label = dayLabel(items[0]);
    /* The date suffix only adds info under a relative label. */
    const parsedDay = (label === 'Hôm nay' || label === 'Hôm qua') && day ? new Date(day + 'T00:00:00') : null;
    const sub = parsedDay && Number.isFinite(parsedDay.getTime())
      ? `<small>${escapeHTML(parsedDay.toLocaleDateString('vi-VN'))}</small>` : '';
    return `<section class="mem-day"><h3 class="mem-day-title">${escapeHTML(label)} ${sub}</h3>${items.map(leafHtml).join('')}</section>`;
  }).join('') + (pages > 1 ? `<div class="mem-pager">
    <button class="quiet-button" data-act="prev" type="button"${memState.page <= 1 ? ' disabled' : ''}>Trước</button>
    <span class="mem-pager-info">Trang ${memState.page}/${pages}</span>
    <button class="quiet-button" data-act="next" type="button"${memState.page >= pages ? ' disabled' : ''}>Sau</button>
  </div>` : '');
}

function renderAll() {
  memState.page = 1;
  if (memState.stats) {
    memState.byChunk = new Map((memState.stats.leaves || []).map(l => [l.chunk_id, l]));
    memState.expiring = new Set((memState.stats.expiring || []).map(l => l.chunk_id));
    memState.suggest = new Set((memState.stats.suggest_removal || []).map(l => l.chunk_id));
  } else {
    memState.byChunk.clear(); memState.expiring.clear(); memState.suggest.clear();
  }
  renderStats();
  renderScopeCard();
  renderLeaves();
}

/* ---------- data ---------- */

function setLoading(on) {
  $('#memory-loading').hidden = !on;
  $('#memory-leaves').setAttribute('aria-busy', String(on));
}
function setStatus(text, cls = '') {
  const el = $('#memory-status');
  el.textContent = text || '';
  el.className = 'page-status' + (cls ? ' ' + cls : '');
  el.hidden = !text;
}

/* API: GET /api/memory/stats, or ...?bot_id=<id> when a bot scope is chosen. */
async function loadStats() {
  const request = ++memState.request;
  const scope = memState.scope;
  setLoading(true); setStatus('');
  try {
    const url = scope ? `/api/memory/stats?bot_id=${encodeURIComponent(scope)}` : '/api/memory/stats';
    const stats = await getJson(url);                                   // API: GET /api/memory/stats[?bot_id=<id>]
    if (request !== memState.request) return;
    /* The server echoes the applied scope — a mismatched answer is stale. */
    if ((stats.bot_id || null) !== (scope || null))
      throw new Error('Phản hồi bộ nhớ không khớp phạm vi đã chọn.');
    memState.stats = { ...stats, leaves: Array.isArray(stats.leaves) ? stats.leaves : [] };
    renderAll();
    setStatus('');
  } catch (err) {
    if (request !== memState.request) return;
    memState.stats = null;
    renderAll();
    setStatus('Không tải được bộ nhớ: ' + (err.message || ''), 's-error');
  } finally {
    if (request === memState.request) setLoading(false);
  }
}

/* POST /api/memory/{reinforce,update,forget} — one leaf at a time. */
async function mutate(path, body, okMsg, { reload = true } = {}) {
  try {
    const res = await postJson(path, body);                             // API: POST /api/memory/*
    if (okMsg) toast(okMsg);
    if (reload) await loadStats();
    return res;
  } catch (err) {
    toast('Không lưu được: ' + (err.message || ''));
    return null;
  }
}

/* Re-render one leaf's chips + meta in place after reinforce returns a new
   expires_at — avoids a full stats reload for a single-field change. */
function refreshLeafDom(leaf) {
  const el = $(`#memory-leaves [data-chunk="${leaf.chunk_id}"]`);
  if (!el) return;
  const meta = $('.mem-meta', el); if (meta) meta.innerHTML = metaHtml(leaf);
  const chips = $('.mem-chips', el); if (chips) chips.innerHTML = chipsHtml(leaf);
}

function openEdit(leaf) {
  memState.editing = leaf;
  const { title } = splitHeading(leaf);
  $('#mem-edit-topic').value = title;
  $('#mem-edit-summary').value = String(leaf.snippet || '').replace(/^[-*]\s+/, '').trim();
  $('#memory-edit-dialog').showModal();
}

/* ---------- events ---------- */

function wirePage() {
  $('#memory-kind').onchange = e => { memState.kind = e.target.value; memState.page = 1; renderLeaves(); };
  $('#memory-use').onchange = e => { memState.use = e.target.value; memState.page = 1; renderLeaves(); };
  $('#memory-scope-card').addEventListener('click', e => { if (e.target.closest('.mem-scope-clear')) selectBot(''); });
  $('#memory-search').addEventListener('input', e => { memState.query = e.target.value; memState.page = 1; renderLeaves(); });
  $('#memory-reload').onclick = () => loadStats();

  /* Leaf actions — delegation over the rendered list. */
  $('#memory-leaves').addEventListener('click', async e => {
    const btn = e.target.closest('[data-act]');
    if (!btn) return;
    if (btn.dataset.act === 'prev') { memState.page--; renderLeaves(); return; }
    if (btn.dataset.act === 'next') { memState.page++; renderLeaves(); return; }
    const article = btn.closest('[data-chunk]');
    const leaf = article && memState.byChunk.get(article.dataset.chunk);
    if (!leaf) return;
    const sid = leaf.session_id;
    if (btn.dataset.act === 'edit') { openEdit(leaf); return; }
    if (btn.dataset.act === 'forget') {
      const { title } = splitHeading(leaf);
      if (!confirm(`Quên “${title || 'trang này'}” khỏi bộ nhớ?`)) return;
      btn.disabled = true;
      await mutate('/api/memory/forget', { session_id: sid }, 'Đã quên trang.');   // API: POST /api/memory/forget
      btn.disabled = false;
      return;
    }
    if (btn.dataset.act === 'reinforce') {
      btn.disabled = true;
      const res = await postJson('/api/memory/reinforce', { session_id: sid }).catch(err => { toast('Không gia hạn được: ' + (err.message || '')); return null; });  // API: POST /api/memory/reinforce
      btn.disabled = false;
      if (res && res.ok) {
        if (res.expires_at) { leaf.expires_at = res.expires_at; refreshLeafDom(leaf); }
        toast('Đã gia hạn trang.');
      }
    }
  });

  /* Edit dialog — page-local wiring (see DIALOG_HTML note). */
  const dlg = $('#memory-edit-dialog');
  $('.dialog-close', dlg).onclick = () => dlg.close();
  dlg.addEventListener('click', e => { if (e.target === dlg) dlg.close(); });
  $('#mem-edit-cancel').onclick = () => dlg.close();
  $('#memory-edit-form').onsubmit = async e => {
    e.preventDefault();
    const leaf = memState.editing;
    if (!leaf) return;
    const topic = $('#mem-edit-topic').value.trim();
    /* The writer stores summary as one "- …" line — newlines would split
       into stray leaf lines, so collapse them like cleanText does. */
    const summary = $('#mem-edit-summary').value.replace(/\s+/g, ' ').trim();
    if (!topic || !summary) return;
    const res = await mutate('/api/memory/update', { session_id: leaf.session_id, topic, summary }, '');  // API: POST /api/memory/update
    if (res && res.ok) { dlg.close(); toast('Đã cập nhật trang.'); }
  };
}

/* Sidebar routing stays on this page: clicking a bot card or one of its
   drawer sessions scopes the journal to that bot (instead of the shared
   default, which opens chat). Clicking the scoped bot again clears the
   filter; a group row scopes to its first member. */
function selectBot(id) {
  memState.scope = !id || memState.scope === id ? '' : id;
  selectedBot = memState.scope;
  renderTeam();                                        // highlight the scoped bot
  const q = memState.scope ? '?bot=' + encodeURIComponent(memState.scope) : '';
  history.replaceState(null, '', location.pathname + q);
  loadStats();
}
function loadSession(id, title, botId) { selectBot(botId || ''); }

function buildPage() {
  const workspace = $('.workspace');
  workspace.insertAdjacentHTML('beforeend', PAGE_HTML);
  document.body.insertAdjacentHTML('beforeend', DIALOG_HTML);
  wirePage();
}

async function boot() {
  buildPage();
  /* Deep link ?bot=<id> — stale ids clear once the roster answers below. */
  const wanted = new URLSearchParams(location.search).get('bot');
  if (wanted) memState.scope = wanted;
  loadStats();                                     // API: GET /api/memory/stats[?bot_id=<id>]
  try { await bootShell(); }                       // API: GET /api/bots + GET /api/sessions
  catch (err) { toast('Không tải được đội bot: ' + (err.message || '')); }
  /* A stale ?bot=<id> deep link clears once the real roster answers. */
  if (memState.scope && !getBot(memState.scope)) { memState.scope = ''; history.replaceState(null, '', location.pathname); }
  selectedBot = memState.scope;
  renderTeam();
  renderScopeCard();
}

boot();
