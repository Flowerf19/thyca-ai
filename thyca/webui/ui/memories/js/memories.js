ThycaRouter.register('memories', { title: 'Nhật ký', stylesheet: '/ui/memories/memoriescss/memories.css', mount(ctx) {
  let routeKey = '';
  const root = ctx.root;
  const $ = (selector, base = root) => selector === '.workspace' ? root : base.querySelector(selector);
  const $$ = (selector, base = root) => [...base.querySelectorAll(selector)];

const EXPIRE_SOON_DAYS = 14;
const SUGGEST_IDLE_DAYS = 7;
const LEAF_PAGE = 60;

const memState = {
  scope: '',
  stats: null,
  byChunk: new Map(),
  expiring: new Set(),
  suggest: new Set(),
  query: '',
  kind: '',
  use: '',
  page: 1,
  request: 0,
  editing: null,
};

const PAGE_HTML = `
<header class="page-nav">
  <div class="page-title">
    <div class="eyebrow">Nhật ký</div>
    <h1 id="memory-title">Bộ nhớ</h1>
    <p class="page-sub">Bộ nhớ các bot ghi lại</p>
  </div>
</header>
<div class="page-scroll">
  <div class="page-inner section-stack">
    <p class="page-status" id="memory-status" hidden></p>
    <article class="paper-card" id="memory-scope-card" hidden></article>
    <div class="mem-filter-row page-actions" id="memory-filter-row">
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
        <option value="expiring">Sắp hết hạn</option>
        <option value="suggest">Gợi ý xóa</option>
      </select>
      <input class="inline-field" id="memory-search" type="search" autocomplete="off"
        placeholder="Tìm trong nhật ký…" aria-label="Tìm trong nhật ký">
      <button class="quiet-button" id="memory-reload" type="button">${icon('retry')}<span>Tải lại</span></button>
    </div>
    <article class="paper-card" aria-label="Thống kê bộ nhớ">
      <div class="eyebrow">Tổng quan</div>
      <div class="mem-stats" id="memory-stats"></div>
    </article>
    <article class="paper-card" aria-label="Các lá nhớ">
      <div class="mem-list-top">
        <div class="mem-list-heading"><span class="eyebrow">Lá nhớ</span><span class="mem-count" id="memory-count"></span></div>
        <span class="status-line" id="memory-loading">Đang đọc bộ nhớ…</span>
      </div>
      <div id="memory-leaves" aria-live="polite"></div>
    </article>
  </div>
</div>`;

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

function ownerLabel(bots) {
  if (!Array.isArray(bots) || !bots.length) return 'chung';
  return bots.map(id => id === 'team' ? 'chung' : escapeHTML((getBot(id) || { name: id }).name)).join(', ');
}

const KIND_LABEL = { habit: 'thói quen', checkpoint: 'task' };
const STATUS_LABEL = { open: 'đang mở', done: 'đã xong', cancelled: 'đã hủy' };

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

function snippetHtml(text) {
  const raw = String(text || '').trim();
  const single = (/^[-*]\s+/.test(raw) && !/\n\s*(?:[-*+]|\d+\.)\s+/.test(raw))
    ? raw.replace(/^[-*]\s+/, '').trim()
    : raw;
  if (window.formatMarkdown) {
    const html = window.formatMarkdown(single);
    if (html) return html;
  }
  return single ? `<p>${escapeHTML(single).replace(/\n/g, '<br>')}</p>` : '<p><em>(trống)</em></p>';
}

function chipsHtml(leaf) {
  const chips = [];
  if (memState.expiring.has(leaf.chunk_id) || leafExpiring(leaf)) chips.push('<span class="chip mem-chip-warn">Sắp hết hạn</span>');
  if (memState.suggest.has(leaf.chunk_id) || leafSuggest(leaf)) chips.push('<span class="chip mem-chip-danger">Gợi ý xóa</span>');
  if (leaf.kind) chips.push(`<span class="chip mem-chip-kind">${escapeHTML(KIND_LABEL[leaf.kind] || leaf.kind)}${leaf.habit ? ' · ' + escapeHTML(leaf.habit) : ''}</span>`);
  if (leaf.status) chips.push(`<span class="chip mem-chip-status">${escapeHTML(STATUS_LABEL[leaf.status] || leaf.status)}</span>`);
  return chips.join('');
}

function fmtExpiry(iso, soon) {
  if (!iso) return '';
  const d = new Date(iso);
  if (!Number.isFinite(d.getTime())) return `<span class="${soon ? 'mem-expiring' : 'mem-expiry'}">hết hạn ${escapeHTML(iso)}</span>`;
  const full = fmtTs(iso);
  const shortDate = d.toLocaleDateString('vi-VN');
  if (soon) {
    const days = Math.ceil((d.getTime() - Date.now()) / 864e5);
    const label = days > 1 ? `hết hạn sau ${days} ngày` : days === 1 ? 'hết hạn ngày mai' : days === 0 ? 'hết hạn hôm nay' : `hết hạn ${shortDate}`;
    return `<span class="mem-expiring" title="Hết hạn ${escapeHTML(full)}">${escapeHTML(label)}</span>`;
  }
  return `<span class="mem-expiry" title="Hết hạn ${escapeHTML(full)}">hết hạn ${escapeHTML(shortDate)}</span>`;
}

function metaHtml(leaf) {
  const parts = [];
  const used = leaf.get_count || 0;
  const searched = leaf.search_count || 0;
  if (used > 0) parts.push(`<span>đã dùng ${used}</span>`);
  if (searched > 0) parts.push(`<span>tìm thấy ${searched}</span>`);
  if (!used && !searched) parts.push('<span class="mem-idle">chưa dùng</span>');
  if (leaf.expires_at) {
    const soon = memState.expiring.has(leaf.chunk_id) || leafExpiring(leaf);
    parts.push(fmtExpiry(leaf.expires_at, soon));
  }
  parts.push(`<span>${ownerLabel(leaf.bots)}</span>`);
  if (leaf.memory_id) parts.push(`<span class="mem-sid">${escapeHTML(leaf.memory_id)}</span>`);
  return parts.join('');
}

function renderStats() {
  const s = memState.stats;
  const kpis = [
    ['', s ? (s.total ?? 0) : '—', 'Tổng lá', s ? `chưa đụng tới ${s.untouched ?? 0}` : '—', ''],
    ['used', s ? (s.used ?? 0) : '—', 'Đã dùng', s ? `Được tìm ${s.searched ?? 0}` : '—', ''],
    ['expiring', s ? (s.expiring || []).length : '—', 'Sắp hết hạn', 'trong 14 ngày', 'm-warn'],
    ['suggest', s ? (s.suggest_removal || []).length : '—', 'Gợi ý xóa', 'chưa dùng >7 ngày', 'm-danger'],
  ];
  $('#memory-stats').innerHTML = kpis.map(([filter, value, label, sub, cls]) => {
    const active = memState.use === filter;
    return `<button type="button" class="mem-stat${cls ? ' ' + cls : ''}" data-stat-filter="${filter}" aria-pressed="${active}"><b>${value}</b><span class="mem-stat-label">${label}</span><small class="mem-stat-sub">${sub}</small></button>`;
  }).join('');
}

function updateMemoryTitle() {
  const titleEl = $('#memory-title');
  if (!titleEl) return;
  const b = memState.scope && getBot(memState.scope);
  titleEl.textContent = b ? `Bộ nhớ · ${b.name}` : 'Bộ nhớ';
}
function renderScopeCard() {
  updateMemoryTitle();
  const card = $('#memory-scope-card');
  const filterRow = $('#memory-filter-row');
  const b = memState.scope && getBot(memState.scope);
  if (!b) {
    card.hidden = true; card.innerHTML = '';
    if (filterRow) { if (typeof filterRow.removeAttribute === 'function') filterRow.removeAttribute('style'); else if (filterRow.style) filterRow.style.cssText = ''; }
    return;
  }
  const count = memState.stats ? memState.stats.total ?? 0 : 0;
  const state = states[b.state] ? states[b.state][1] : '';
  card.hidden = false;
  const css = paletteStyle(b);
  card.style.cssText = css;
  if (filterRow) filterRow.style.cssText = css;
  card.innerHTML = `<div class="mem-scope">${avatar(b)}<div class="mem-scope-main">
    <h3 class="mem-scope-name">${escapeHTML(b.name)} <span class="mem-scope-role">${escapeHTML(b.role || '')}</span></h3>
    <p class="mem-scope-meta">${count} lá nhớ${state ? ' · ' + escapeHTML(state) : ''}</p></div>
    <button class="quiet-button mem-scope-clear" type="button">Bỏ lọc</button></div>`;
}

function filteredLeaves() {
  let leaves = memState.stats?.leaves || [];
  if (memState.kind === 'none') leaves = leaves.filter(l => !l.kind);
  else if (memState.kind) leaves = leaves.filter(l => l.kind === memState.kind);
  if (memState.use === 'used') leaves = leaves.filter(l => (l.get_count || 0) >= 1);
  else if (memState.use === 'untouched') leaves = leaves.filter(l => !(l.get_count || 0) && !(l.search_count || 0));
  else if (memState.use === 'expiring') leaves = leaves.filter(l => memState.expiring.has(l.chunk_id) || leafExpiring(l));
  else if (memState.use === 'suggest') leaves = leaves.filter(l => memState.suggest.has(l.chunk_id) || leafSuggest(l));
  const needle = memState.query.trim().toLocaleLowerCase('vi');
  if (!needle) return leaves;
  return leaves.filter(leaf => {
    const { title } = splitHeading(leaf);
    return `${title} ${leaf.snippet || ''} ${leaf.memory_id || ''} ${leaf.heading || ''}`
      .toLocaleLowerCase('vi').includes(needle);
  });
}

function leafHtml(leaf) {
  const { time, title } = splitHeading(leaf);
  const chips = chipsHtml(leaf);
  return `<article class="mem-leaf" data-chunk="${escapeHTML(leaf.chunk_id)}">
  <div class="mem-leaf-top">
    <div class="mem-leaf-head">
      <h4 class="mem-leaf-title">${escapeHTML(title)}</h4>
      ${time ? `<time class="mem-leaf-time">${escapeHTML(time)}</time>` : ''}
      <div class="mem-chips">${chips}</div>
    </div>
    <div class="mem-actions">
      <button class="quiet-button" data-act="reinforce" type="button">${icon('retry')}<span>Gia hạn</span></button>
      <button class="quiet-button" data-act="edit" type="button">Sửa</button>
      <button class="quiet-button is-danger" data-act="forget" type="button">Quên</button>
    </div>
  </div>
  <div class="mem-snippet markdown-body">${snippetHtml(leaf.snippet)}</div>
  <p class="mem-meta">${metaHtml(leaf)}</p>
</article>`;
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
  const hasFilter = Boolean(memState.query.trim() || memState.kind || memState.use);
  if (!leaves.length) {
    box.innerHTML = `<div class="empty-note"><strong>${hasFilter ? 'Không tìm thấy lá nào' : 'Chưa có lá nhớ nào'}</strong>${hasFilter ? 'Thử từ khóa khác hoặc xóa bộ lọc đang chọn.' : (memState.scope ? 'Bot này chưa ghi trang nào vào bộ nhớ.' : 'Các bot sẽ ghi lại điều học được sau mỗi phiên trò chuyện.')}</div>`;
    return;
  }
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

async function loadStats() {
  const request = ++memState.request;
  const scope = memState.scope;
  setLoading(true); setStatus('');
  try {
    const url = scope ? `/api/memory/stats?bot_id=${encodeURIComponent(scope)}` : '/api/memory/stats';
    const stats = await getJson(url);
    if (request !== memState.request) return;
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

async function mutate(path, body, okMsg, { reload = true } = {}) {
  try {
    const res = await postJson(path, body);
    if (okMsg) toast(okMsg);
    if (reload) await loadStats();
    return res;
  } catch (err) {
    toast('Không lưu được: ' + (err.message || ''));
    return null;
  }
}

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

function wirePage() {
  $('#memory-kind').onchange = e => { memState.kind = e.target.value; memState.page = 1; renderLeaves(); };
  $('#memory-use').onchange = e => { memState.use = e.target.value; memState.page = 1; renderStats(); renderLeaves(); };
  $('#memory-stats').addEventListener('click', e => {
    const statBtn = e.target.closest('[data-stat-filter]');
    if (!statBtn) return;
    const filter = statBtn.dataset.statFilter || '';
    memState.use = (filter && memState.use === filter) ? '' : filter;
    const useSel = $('#memory-use');
    if (useSel) useSel.value = memState.use;
    memState.page = 1;
    renderStats();
    renderLeaves();
  });
  $('#memory-scope-card').addEventListener('click', e => { if (e.target.closest('.mem-scope-clear')) selectBot(''); });
  $('#memory-search').addEventListener('input', e => { memState.query = e.target.value; memState.page = 1; renderLeaves(); });
  $('#memory-reload').onclick = () => loadStats();

  $('#memory-leaves').addEventListener('click', async e => {
    const btn = e.target.closest('[data-act]');
    if (!btn) return;
    if (btn.dataset.act === 'prev') { memState.page--; renderLeaves(); return; }
    if (btn.dataset.act === 'next') { memState.page++; renderLeaves(); return; }
    const article = btn.closest('[data-chunk]');
    const leaf = article && memState.byChunk.get(article.dataset.chunk);
    if (!leaf) return;
    const sid = leaf.memory_id;
    if (btn.dataset.act === 'edit') { openEdit(leaf); return; }
    if (btn.dataset.act === 'forget') {
      const { title } = splitHeading(leaf);
      if (!confirm(`Quên “${title || 'trang này'}” khỏi bộ nhớ?`)) return;
      btn.disabled = true;
      await mutate('/api/memory/forget', { memory_id: sid }, 'Đã quên trang.');
      btn.disabled = false;
      return;
    }
    if (btn.dataset.act === 'reinforce') {
      btn.disabled = true;
      const res = await postJson('/api/memory/reinforce', { memory_id: sid }).catch(err => { toast('Không gia hạn được: ' + (err.message || '')); return null; });
      btn.disabled = false;
      if (res && res.ok) {
        if (res.expires_at) {
          leaf.expires_at = res.expires_at;
          if (!leafExpiring(leaf)) {
            memState.expiring.delete(leaf.chunk_id);
            if (Array.isArray(memState.stats?.expiring)) {
              memState.stats.expiring = memState.stats.expiring.filter(x => x.chunk_id !== leaf.chunk_id);
            }
          }
          refreshLeafDom(leaf);
          renderStats();
          if (memState.use === 'expiring') renderLeaves();
        }
        toast('Đã gia hạn trang.');
      }
    }
  });

  const dlg = $('#memory-edit-dialog');
  $('.dialog-close', dlg).onclick = () => dlg.close();
  dlg.addEventListener('click', e => { if (e.target === dlg) dlg.close(); });
  $('#mem-edit-cancel').onclick = () => dlg.close();
  $('#memory-edit-form').onsubmit = async e => {
    e.preventDefault();
    const leaf = memState.editing;
    if (!leaf) return;
    const topic = $('#mem-edit-topic').value.trim();
    const summary = $('#mem-edit-summary').value.replace(/\s+/g, ' ').trim();
    if (!topic || !summary) return;
    const res = await mutate('/api/memory/update', { memory_id: leaf.memory_id, topic, summary }, '');
    if (res && res.ok) { dlg.close(); toast('Đã cập nhật trang.'); }
  };
}

function selectBot(id) {
  memState.scope = !id || memState.scope === id ? '' : id;
  ctx.highlightBot(memState.scope);
  ctx.renderTeam();
  updateMemoryTitle();
  const q = memState.scope ? '?bot=' + encodeURIComponent(memState.scope) : '';
  ctx.updateRoute({ search: q });
  routeKey = q;
  loadStats();
}
function loadConversation(id, botId) { selectBot(botId || ''); }

function buildPage() {
  const workspace = $('.workspace');
  workspace.insertAdjacentHTML('beforeend', PAGE_HTML);
  root.insertAdjacentHTML('beforeend', DIALOG_HTML);
  wirePage();
}

async function boot() {
  buildPage();
  const wanted = new URLSearchParams(ctx.route.search).get('bot');
  if (wanted) memState.scope = wanted;
  loadStats();
  try { await ctx.shellReady; }
  catch (err) { toast('Không tải được đội bot: ' + (err.message || '')); }
  if (memState.scope && !getBot(memState.scope)) { memState.scope = ''; ctx.updateRoute({ search: '' }); }
  ctx.highlightBot(memState.scope);
  routeKey = ctx.route.search + ctx.route.hash;
  ctx.renderTeam();
  renderScopeCard();
}
  const ready = boot();
  return { ready, selectBot, loadConversation, onRoute(route) {
    const key = route.search + route.hash;
    if (key === routeKey) return;
    routeKey = key;
    const next = new URLSearchParams(route.search).get('bot') || '';
    if (next !== memState.scope) {
      memState.scope = next && getBot(next) ? next : '';
      ctx.highlightBot(memState.scope); ctx.renderTeam(); updateMemoryTitle(); loadStats();
    }
  } };
} });
