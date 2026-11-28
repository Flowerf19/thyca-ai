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
  leaves: [],
  merged: false,
  byChunk: new Map(),
  expiring: new Set(),
  suggest: new Set(),
  expanded: new Set(),
  query: '',
  kind: '',
  use: '',
  page: 1,
  request: 0,
  editing: null,
};

function mergeMemoryLeaves(rawLeaves, expiringIds, suggestIds) {
  const byKey = new Map();
  for (const raw of rawLeaves || []) {
    const key = raw.memory_id || raw.chunk_id;
    const prev = byKey.get(key);
    if (!prev) {
      byKey.set(key, {
        ...raw,
        chunk_ids: [raw.chunk_id],
        _snippets: raw.snippet ? [String(raw.snippet).trim()] : [],
        _expiring: expiringIds.has(raw.chunk_id),
        _suggest: suggestIds.has(raw.chunk_id),
      });
      continue;
    }
    prev.chunk_ids.push(raw.chunk_id);
    prev.get_count = (prev.get_count || 0) + (raw.get_count || 0);
    prev.search_count = (prev.search_count || 0) + (raw.search_count || 0);
    if (raw.last_get_at && (!prev.last_get_at || raw.last_get_at > prev.last_get_at)) prev.last_get_at = raw.last_get_at;
    if (raw.last_search_at && (!prev.last_search_at || raw.last_search_at > prev.last_search_at)) prev.last_search_at = raw.last_search_at;
    if (raw.snippet) {
      const part = String(raw.snippet).trim();
      if (part && !prev._snippets.includes(part)) prev._snippets.push(part);
    }
    if (expiringIds.has(raw.chunk_id)) prev._expiring = true;
    if (suggestIds.has(raw.chunk_id)) prev._suggest = true;
  }
  const merged = [];
  for (const entry of byKey.values()) {
    const snippet = entry._snippets.join('\n\n') || entry.snippet || '';
    if (entry._expiring) expiringIds.add(entry.chunk_id);
    if (entry._suggest && !entry.get_count && !entry.search_count) suggestIds.add(entry.chunk_id);
    else if (entry.get_count || entry.search_count) suggestIds.delete(entry.chunk_id);
    delete entry._snippets;
    delete entry._expiring;
    delete entry._suggest;
    entry.snippet = snippet;
    merged.push(entry);
  }
  return merged;
}

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

function fmtShortDate(d) {
  const opts = d.getFullYear() === new Date().getFullYear()
    ? { day: 'numeric', month: 'numeric' }
    : { day: 'numeric', month: 'numeric', year: 'numeric' };
  return d.toLocaleDateString('vi-VN', opts);
}

function ownerLabel(bots) {
  if (!Array.isArray(bots) || !bots.length) return 'chung';
  return bots.map(id => id === 'team' ? 'chung' : escapeHTML((getBot(id) || { name: id }).name)).join(', ');
}

function shouldShowOwner(leaf) {
  if (!memState.scope) return true;
  if (!Array.isArray(leaf.bots) || leaf.bots.length !== 1) return true;
  return leaf.bots[0] !== memState.scope;
}

const KIND_LABEL = { habit: 'Thói quen', checkpoint: 'Task' };
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
function isLeafExpiring(leaf) { return memState.expiring.has(leaf.chunk_id) || leafExpiring(leaf); }
function isLeafSuggest(leaf) { return memState.suggest.has(leaf.chunk_id) || leafSuggest(leaf); }
function isLeafAlert(leaf) { return isLeafExpiring(leaf) || isLeafSuggest(leaf); }

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

function fmtExpiry(iso, soon) {
  if (!iso) return '';
  const d = new Date(iso);
  if (!Number.isFinite(d.getTime())) return `<span class="${soon ? 'mem-tag mem-tag-danger' : 'mem-meta-item'}">Hết hạn ${escapeHTML(iso)}</span>`;
  const full = fmtTs(iso);
  const shortDate = fmtShortDate(d);
  if (soon) {
    const days = Math.ceil((d.getTime() - Date.now()) / 864e5);
    const label = days > 1
      ? `Sắp hết hạn · còn ${days} ngày`
      : days === 1
        ? 'Sắp hết hạn · ngày mai'
        : days === 0
          ? 'Sắp hết hạn · hôm nay'
          : `Hết hạn ${shortDate}`;
    return `<span class="mem-tag mem-tag-danger" title="Hết hạn ${escapeHTML(full)}">${escapeHTML(label)}</span>`;
  }
  return `<span class="mem-meta-item" title="Hết hạn ${escapeHTML(full)}">Hết hạn ${escapeHTML(shortDate)}</span>`;
}

function metaHtml(leaf) {
  const parts = [];
  const soon = isLeafExpiring(leaf);
  const suggest = isLeafSuggest(leaf);
  if (leaf.kind) {
    const kindText = KIND_LABEL[leaf.kind] || leaf.kind;
    const statusText = leaf.status ? ` · ${STATUS_LABEL[leaf.status] || leaf.status}` : '';
    parts.push(`<span class="mem-tag mem-tag-kind">${escapeHTML(kindText + statusText)}</span>`);
  }
  if (suggest) parts.push('<span class="mem-tag mem-tag-danger">Gợi ý xóa</span>');
  if (leaf.expires_at) parts.push(fmtExpiry(leaf.expires_at, soon));
  else if (soon) parts.push('<span class="mem-tag mem-tag-danger">Sắp hết hạn</span>');
  const used = leaf.get_count || 0;
  const searched = leaf.search_count || 0;
  if (used > 0) parts.push(`<span class="mem-meta-item">Đã dùng ${used}</span>`);
  if (searched > 0) parts.push(`<span class="mem-meta-item">Tìm thấy ${searched}</span>`);
  if (shouldShowOwner(leaf)) parts.push(`<span class="mem-meta-item">${ownerLabel(leaf.bots)}</span>`);
  return parts.join('');
}

function renderStats() {
  const s = memState.stats;
  const list = memState.leaves;
  const useMergedCounts = memState.merged;
  const totalCount = s ? (useMergedCounts ? list.length : (s.total ?? 0)) : '—';
  const usedCount = s ? (useMergedCounts ? list.filter(l => (l.get_count || 0) >= 1).length : (s.used ?? 0)) : '—';
  const expCount = s ? (useMergedCounts ? list.filter(l => isLeafExpiring(l)).length : (s.expiring || []).length) : 0;
  const sugCount = s ? (useMergedCounts ? list.filter(l => isLeafSuggest(l)).length : (s.suggest_removal || []).length) : 0;
  const tabs = [
    ['', 'Tất cả', totalCount, ''],
    ['used', 'Đã dùng', usedCount, ''],
    ['expiring', 'Sắp hết hạn', s ? expCount : '—', expCount > 0 ? 'm-alert' : ''],
    ['suggest', 'Gợi ý xóa', s ? sugCount : '—', sugCount > 0 ? 'm-alert' : ''],
  ];
  $('#memory-stats').innerHTML = tabs.map(([filter, label, value, cls]) => {
    const active = memState.use === filter;
    return `<button type="button" class="mem-use-tab${cls ? ' ' + cls : ''}" data-stat-filter="${filter}" aria-pressed="${active}"><span>${label}</span><b>${value}</b></button>`;
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
  const count = memState.stats ? (memState.merged ? memState.leaves.length : (memState.stats.total ?? 0)) : 0;
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
  let leaves = memState.leaves;
  if (memState.kind === 'none') leaves = leaves.filter(l => !l.kind);
  else if (memState.kind) leaves = leaves.filter(l => l.kind === memState.kind);
  if (memState.use === 'used') leaves = leaves.filter(l => (l.get_count || 0) >= 1);
  else if (memState.use === 'expiring') leaves = leaves.filter(l => isLeafExpiring(l));
  else if (memState.use === 'suggest') leaves = leaves.filter(l => isLeafSuggest(l));
  const needle = memState.query.trim().toLocaleLowerCase('vi');
  if (!needle) return leaves;
  return leaves.filter(leaf => {
    const { title } = splitHeading(leaf);
    return `${title} ${leaf.snippet || ''} ${leaf.heading || ''}`
      .toLocaleLowerCase('vi').includes(needle);
  });
}

function leafHtml(leaf, autoOpen) {
  const { time, title } = splitHeading(leaf);
  const alertCls = isLeafAlert(leaf) ? ' is-alert' : '';
  const openAttr = (autoOpen || memState.expanded.has(leaf.chunk_id)) ? ' open' : '';
  return `<details class="mem-leaf${alertCls}" data-chunk="${escapeHTML(leaf.chunk_id)}"${openAttr}>
  <summary class="mem-leaf-summary">
    <span class="mem-leaf-caret" aria-hidden="true">${icon('chevron')}</span>
    <h4 class="mem-leaf-title">${escapeHTML(title)}</h4>
    ${time ? `<time class="mem-leaf-time">${escapeHTML(time)}</time>` : ''}
  </summary>
  <div class="mem-leaf-body">
    <div class="mem-snippet markdown-body">${snippetHtml(leaf.snippet)}</div>
    <div class="mem-leaf-foot">
      <p class="mem-meta">${metaHtml(leaf)}</p>
      <div class="mem-actions">
        <button class="quiet-button" data-act="reinforce" type="button">Gia hạn</button>
        <button class="quiet-button" data-act="edit" type="button">Sửa</button>
        <button class="quiet-button is-danger" data-act="forget" type="button">Quên</button>
      </div>
    </div>
  </div>
</details>`;
}

function renderLeaves() {
  const box = $('#memory-leaves');
  const leaves = filteredLeaves();
  const total = memState.leaves.length;
  const pages = Math.max(1, Math.ceil(leaves.length / LEAF_PAGE));
  memState.page = Math.min(Math.max(1, memState.page), pages);
  const visible = leaves.slice((memState.page - 1) * LEAF_PAGE, memState.page * LEAF_PAGE);
  $('#memory-count').textContent = memState.stats
    ? (leaves.length === total ? `${total} lá` : `${leaves.length}/${total} lá`)
    : '';
  if (!memState.stats) { box.innerHTML = ''; return; }
  const hasQuery = Boolean(memState.query.trim());
  const hasFilter = Boolean(hasQuery || memState.kind || memState.use);
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
    return `<section class="mem-day"><h3 class="mem-day-title">${escapeHTML(label)} ${sub}</h3>${items.map(leaf => leafHtml(leaf, hasQuery)).join('')}</section>`;
  }).join('') + (pages > 1 ? `<div class="mem-pager">
    <button class="quiet-button" data-act="prev" type="button"${memState.page <= 1 ? ' disabled' : ''}>Trước</button>
    <span class="mem-pager-info">Trang ${memState.page}/${pages}</span>
    <button class="quiet-button" data-act="next" type="button"${memState.page >= pages ? ' disabled' : ''}>Sau</button>
  </div>` : '');
}

function renderAll() {
  memState.page = 1;
  if (memState.stats) {
    const rawLeaves = memState.stats.leaves || [];
    memState.expiring = new Set((memState.stats.expiring || []).map(l => l.chunk_id));
    memState.suggest = new Set((memState.stats.suggest_removal || []).map(l => l.chunk_id));
    memState.leaves = mergeMemoryLeaves(rawLeaves, memState.expiring, memState.suggest);
    memState.merged = memState.leaves.length < rawLeaves.length;
    memState.byChunk = new Map(memState.leaves.map(l => [l.chunk_id, l]));
    for (const chunkId of [...memState.expanded]) {
      if (!memState.byChunk.has(chunkId)) memState.expanded.delete(chunkId);
    }
  } else {
    memState.leaves = []; memState.merged = false;
    memState.byChunk.clear(); memState.expiring.clear(); memState.suggest.clear(); memState.expanded.clear();
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
  if (el.classList && typeof el.classList.toggle === 'function') {
    el.classList.toggle('is-alert', isLeafAlert(leaf));
  }
  const meta = $('.mem-meta', el); if (meta) meta.innerHTML = metaHtml(leaf);
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
  $('#memory-stats').addEventListener('click', e => {
    const statBtn = e.target.closest('[data-stat-filter]');
    if (!statBtn) return;
    const filter = statBtn.dataset.statFilter || '';
    memState.use = (filter && memState.use === filter) ? '' : filter;
    memState.page = 1;
    renderStats();
    renderLeaves();
  });
  $('#memory-scope-card').addEventListener('click', e => { if (e.target.closest('.mem-scope-clear')) selectBot(''); });
  $('#memory-search').addEventListener('input', e => { memState.query = e.target.value; memState.page = 1; renderLeaves(); });

  $('#memory-leaves').addEventListener('toggle', e => {
    const details = e.target;
    const chunk = details?.dataset?.chunk;
    if (!chunk) return;
    if (details.open) memState.expanded.add(chunk);
    else memState.expanded.delete(chunk);
  }, true);

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
            const ids = new Set(leaf.chunk_ids || [leaf.chunk_id]);
            for (const cid of ids) memState.expiring.delete(cid);
            if (Array.isArray(memState.stats?.expiring)) {
              memState.stats.expiring = memState.stats.expiring.filter(x => !ids.has(x.chunk_id));
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
  const pageTpl = document.querySelector('template#tpl-memories');
  const dialogTpl = document.querySelector('template#tpl-memories-dialog');
  workspace.insertAdjacentHTML('beforeend', pageTpl ? pageTpl.innerHTML : '');
  root.insertAdjacentHTML('beforeend', dialogTpl ? dialogTpl.innerHTML : '');
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
