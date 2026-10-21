// Profile screen: "Bạn và <bot>" — one tab per relationship. The shared
// canonical files (USER.md / SOUL.md / IDENTITY.md) are the user–Thyca
// bond; every other bot keeps its own TWIN.md persona. Editing rewrites a
// whole canonical file — there is no per-section endpoint, and no read or
// write API for a bot's TWIN.md/USER.md yet (bot tabs are read-only).
// API: GET /api/memory/stats, POST /api/memory/canonical {name, content},
//      GET /api/bots via bootShell roster.
'use strict';

/* Friendly labels + order — the same mapping the old UI kept in
   shared/js/memory-data.js; unknown files keep their real name. */
const CANONICAL_META = {
  'USER.md': { title: 'Hồ sơ của bạn', description: 'Tên, sở thích và bối cảnh Thyca dùng khi trả lời.' },
  'SOUL.md': { title: 'Giọng nói của Thyca', description: 'Cách Thyca trò chuyện và hỗ trợ bạn.' },
  'IDENTITY.md': { title: 'Danh tính Thyca', description: 'Danh tính và giới hạn của trợ lý.' },
};
const CANONICAL_ORDER = { 'USER.md': 0, 'SOUL.md': 1, 'IDENTITY.md': 2 };
const FALLBACK_DESCRIPTION = 'File hồ sơ được đưa vào ngữ cảnh của Thyca.';

function selectCanonical(files) {
  return (Array.isArray(files) ? files : [])
    .filter(f => f && typeof f.name === 'string')
    .map(f => ({ name: f.name, content: String(f.content || ''), ...(CANONICAL_META[f.name] || { title: f.name, description: FALLBACK_DESCRIPTION }) }))
    .sort((a, b) => (CANONICAL_ORDER[a.name] ?? 9) - (CANONICAL_ORDER[b.name] ?? 9) || a.name.localeCompare(b.name));
}

/* Page markup fills the .workspace that emitShell() already placed (with the
   shared ornaments) — see the script order in index.html. */
$('.workspace').insertAdjacentHTML('beforeend', `
<header class="page-nav">
  <div class="page-title">
    <div class="eyebrow">Hồ sơ</div>
    <h1 id="profile-title">Bạn và đội</h1>
    <p class="page-sub">Mỗi người bạn giữ một trang riêng — sửa là ghi đè toàn bộ file.</p>
  </div>
  <div class="page-actions">
    <button class="quiet-button" id="profile-reload" type="button">${icon('retry')}<span>Tải lại</span></button>
  </div>
</header>
<div class="page-scroll">
  <div class="page-inner section-stack">
    <p class="page-status" id="profile-status" role="status" aria-live="polite" hidden></p>
    <div id="profile-root"></div>
  </div>
</div>`);

const el = {
  root: $('#profile-root'),
  title: $('#profile-title'),
  status: $('#profile-status'),
  reload: $('#profile-reload'),
  dialog: $('#edit-dialog'),
  form: $('#edit-form'),
  editName: $('#edit-name'),
  editLabel: $('#edit-name-label'),
  field: $('#edit-content'),
  dialogStatus: $('#edit-status'),
  cancel: $('#edit-cancel'),
};
const state = { files: [], tab: '', busy: false, opener: null };

function setStatus(node, text, kind = '') {
  node.hidden = !text;
  node.className = 'page-status' + (kind ? ' ' + kind : '');
  node.textContent = text || '';
}

/* share/markdown.js is a module — it can evaluate after the first render, so
   the escaped fallback mirrors chat's contentHtml for that brief window. */
const contentHtml = text => window.formatMarkdown
  ? window.formatMarkdown(text)
  : escapeHTML(String(text || '')).split(/\n{2,}/).map(p => `<p>${p.replace(/\n/g, '<br>')}</p>`).join('');

function decodeHash() {
  try { return decodeURIComponent(location.hash.replace(/^#/, '')); } catch { return ''; }
}

/* The system bot owns the shared canonical files — its tab is "Bạn và Thyca". */
function sharedBotId() {
  return (bots.find(b => b.system) || bots[0] || {}).id || 'thyca';
}

/* ---------- Chung — canonical files, one scroll ---------- */

function renderShared() {
  el.root.innerHTML = state.files.map(file => {
    const hasContent = file.content.trim().length > 0;
    return `<section class="paper-card profile-doc" id="file-${escapeHTML(file.name)}" aria-label="${escapeHTML(file.name)}">
      <div class="eyebrow">${escapeHTML(file.name)}</div>
      <div class="profile-doc-head">
        <div class="profile-doc-titles">
          <h2 class="card-title">${escapeHTML(file.title)}</h2>
          <p class="card-copy">${escapeHTML(file.description)}</p>
        </div>
        <button class="quiet-button" data-edit="${escapeHTML(file.name)}" type="button"${state.busy ? ' disabled' : ''}>Sửa file</button>
      </div>
      ${hasContent
        ? `<article class="markdown-body profile-content">${contentHtml(file.content)}</article>`
        : '<p class="empty-note"><strong>File đang trống.</strong>Nhấn “Sửa file” để viết nội dung cho trang này — Thyca đọc nó trước mỗi câu trả lời.</p>'}
    </section>`;
  }).join('') || '<p class="empty-note"><strong>Chưa có file hồ sơ.</strong></p>';
}

/* ---------- Riêng — one bot's own profile ---------- */

function botFacts(b) {
  return [
    b.model && b.model.id ? `model: ${b.model.id}${b.model.thinking ? ' · suy nghĩ ' + b.model.thinking : ''}` : '',
    b.bash ? `bash: ${b.bash}` : '',
    b.memory ? (b.memory.read && b.memory.write ? 'bộ nhớ: đọc + ghi' : b.memory.read ? 'bộ nhớ: chỉ đọc' : 'bộ nhớ: tắt') : '',
    b.system ? 'bot hệ thống' : '',
  ].filter(Boolean);
}

function renderBot(b) {
  el.root.innerHTML = `<section class="paper-card profile-bot">
    <div class="profile-bot-head">
      ${avatar(b)}
      <div class="profile-bot-titles">
        <h2 class="card-title">${escapeHTML(b.name)} <span class="profile-bot-role">${escapeHTML(b.role || '')}</span></h2>
        ${b.description ? `<p class="card-copy">${escapeHTML(b.description)}</p>` : ''}
        <div class="chip-list">${botFacts(b).map(f => `<span class="chip">${escapeHTML(f)}</span>`).join('')}</div>
      </div>
    </div>
    <div class="eyebrow">TWIN.md</div>
    ${b.persona.trim()
      ? `<article class="markdown-body profile-content">${contentHtml(b.persona)}</article>`
      : '<p class="empty-note"><strong>Chưa có persona.</strong>TWIN.md của bot này không có phần thân.</p>'}
    <p class="profile-readonly">Chỉ đọc — bot tự cập nhật persona qua chat; USER.md riêng của bot chưa có API để hiển thị.</p>
  </section>`;
}

function renderContent() {
  const b = getBot(state.tab);
  if (state.tab === sharedBotId() || !b) renderShared();
  else renderBot(b);
}

function showTab(id, anchor = '') {
  state.tab = id;
  el.title.textContent = 'Bạn và ' + (getBot(id) || { name: 'đội' }).name;
  selectedBot = id;
  renderTeam();
  renderContent();
  const q = id !== sharedBotId() ? '?bot=' + encodeURIComponent(id) : '';
  history.replaceState(null, '', location.pathname + q);
  if (anchor) requestAnimationFrame(() => document.getElementById(anchor)?.scrollIntoView({ block: 'start' }));
}

/* Sidebar routing stays on this page: a bot card opens its "Bạn và <bot>"
   tab; the shared tab is Thyca's, so clicking Thyca shows the common files. */
function selectBot(id) {
  if (getBot(id)) showTab(id);
}
function loadSession(id, title, botId) { selectBot(botId || ''); }

/* ---------- canonical file load + edit ---------- */

async function loadFiles() {
  setStatus(el.status, 'Đang đọc hồ sơ…');
  try {
    const stats = await getJson('/api/memory/stats');            // API: GET /api/memory/stats
    state.files = selectCanonical(stats.files);
    setStatus(el.status);
    renderContent();
  } catch (err) {
    setStatus(el.status, 'Không tải được hồ sơ: ' + err.message, 's-error');
  }
}

function setBusy(busy) {
  state.busy = busy;
  $$('button, input, textarea', el.dialog).forEach(c => { c.disabled = busy; });
  $$('[data-edit]', el.root).forEach(c => { c.disabled = busy; });
}

function openEdit(name, opener) {
  const file = state.files.find(f => f.name === name);
  if (!file) return;
  state.opener = opener;
  el.editName.value = file.name;
  el.editLabel.textContent = file.name;
  el.field.value = file.content;
  setStatus(el.dialogStatus);
  el.dialog.showModal();
  el.field.focus();
}

/* ---------- events ---------- */

el.reload.addEventListener('click', () => loadFiles());
el.root.addEventListener('click', e => {
  const btn = e.target.closest('[data-edit]');
  if (btn) openEdit(btn.dataset.edit, btn);
});
el.cancel.addEventListener('click', () => el.dialog.close());
el.dialog.addEventListener('close', () => state.opener?.focus());
el.form.addEventListener('submit', async e => {
  e.preventDefault();
  const name = el.editName.value;
  const content = el.field.value;
  // The textarea is not `required`: blanking a profile file is a real action,
  // so it asks first instead of being blocked by a native message.
  if (!content.trim() && !confirm(`Xoá trắng nội dung “${name}”?`)) return;
  setBusy(true);
  setStatus(el.dialogStatus, 'Đang lưu…');
  try {
    await postJson('/api/memory/canonical', { name, content });   // API: POST /api/memory/canonical {name, content}
    el.dialog.close();
    await loadFiles();
    setStatus(el.status, `Đã lưu ${name}.`, 's-ok');
  } catch (err) {
    setStatus(el.dialogStatus, 'Không lưu được file: ' + err.message, 's-error');
  } finally {
    setBusy(false);
  }
});

/* Deep links: ?bot=<id> opens that bot's tab; #<file> lands the shared tab
   on the right section. A stale bot id falls back to the shared tab. */
function applyRoute() {
  const wanted = new URLSearchParams(location.search).get('bot');
  const hash = decodeHash();
  if (wanted && getBot(wanted)) { showTab(wanted); return; }
  if (state.files.some(f => f.name === hash)) { showTab(sharedBotId(), 'file-' + hash); return; }
  showTab(state.tab || sharedBotId());
}
window.addEventListener('hashchange', () => {
  const wanted = decodeHash();
  if (state.files.some(f => f.name === wanted)) showTab(sharedBotId(), 'file-' + wanted);
});

async function boot() {
  state.tab = new URLSearchParams(location.search).get('bot') || sharedBotId();
  await loadFiles();                                 // API: GET /api/memory/stats
  try { await bootShell(); }                         // API: GET /api/bots + GET /api/sessions
  catch (err) { toast('Không tải được đội bot: ' + (err.message || '')); }
  applyRoute();
}

boot();
