// Retained profile controller; declarations are isolated in this mount closure.
ThycaRouter.register('profile', { title: 'Hồ sơ', stylesheet: '/ui/profile/profilecss/profile.css', mount(ctx) {
  const root = ctx.root;
  const $ = (selector, base = root) => selector === '.workspace' ? root : base.querySelector(selector);
  const $$ = (selector, base = root) => [...base.querySelectorAll(selector)];
  let selectedBot = '';
// Profile screen: shared canonical docs plus one bot's own dossier.
// No ?bot= shows the shared USER.md / SOUL.md / IDENTITY.md cards
// (title "Hồ sơ chung", nothing selected). ?bot=<id> shows ONLY that bot:
// identity summary and read-only TWIN.md persona, followed by its separate
// model/thinking card. Editing canonical files rewrites the whole file — there is no
// per-section endpoint, and no read or write API for a bot's TWIN.md/USER.md
// (persona stays read-only; only the model settings form posts).
// API: GET /api/memory/stats, POST /api/memory/canonical {name, content},
//      GET /api/config -> {values {models, defaultModel, defaultProvider, providers}, schema},
//      GET /api/bots/<id>/settings -> {bot_id, model, effective},
//      POST /api/bots/<id>/settings <- {model:{id, thinking}} full replace,
//      GET /api/bots via bootShell roster.


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

/* Page markup fills the .workspace that emitShell() already placed (under the
   shared shell artwork) — loaded by the SPA entry (ui/chat/index.html). */
root.insertAdjacentHTML('beforeend', `<dialog class="screen-dialog" id="edit-dialog" aria-labelledby="edit-dialog-title">
<div class="dialog-heading"><div><h2 id="edit-dialog-title">Sửa file hồ sơ</h2><p>Ghi đè toàn bộ nội dung <b id="edit-name-label">file</b> — Thyca đọc lại file ngay sau khi lưu.</p></div><button class="icon-button dialog-close" aria-label="Đóng">${icon('close')}</button></div>
<form class="dialog-body" id="edit-form"><input type="hidden" id="edit-name"><label class="field"><span>Nội dung file (markdown)</span><textarea id="edit-content" spellcheck="false"></textarea></label><p class="page-status" id="edit-status" role="status" hidden></p><div class="dialog-actions"><button class="quiet-button" type="button" id="edit-cancel">Hủy</button><button class="primary-action" type="submit" id="edit-save">Lưu file</button></div></form>
</dialog>`);
root.insertAdjacentHTML('beforeend', `
<header class="page-nav">
  <div class="page-title">
    <div class="eyebrow">Hồ sơ</div>
    <h1 id="profile-title">Bạn và đội</h1>
    <p class="page-sub">Mỗi người bạn giữ một trang riêng — sửa là ghi đè toàn bộ file.</p>
  </div>
</header>
<div class="page-scroll">
  <div class="page-inner section-stack">
    <p class="page-status" id="profile-status" role="status" aria-live="polite" hidden></p>
    <div id="profile-root"></div>
    <div class="page-actions profile-footer-actions">
      <button class="quiet-button" id="profile-shared" type="button" hidden>Hồ sơ chung</button>
      <button class="quiet-button" id="profile-reload" type="button">${icon('retry')}<span>Tải lại</span></button>
    </div>
  </div>
</div>`);

const el = {
  root: $('#profile-root'),
  title: $('#profile-title'),
  status: $('#profile-status'),
  reload: $('#profile-reload'),
  shared: $('#profile-shared'),
  dialog: $('#edit-dialog'),
  form: $('#edit-form'),
  editName: $('#edit-name'),
  editLabel: $('#edit-name-label'),
  field: $('#edit-content'),
  dialogStatus: $('#edit-status'),
  cancel: $('#edit-cancel'),
};
const state = { files: [], tab: '', busy: false, opener: null };
let bootTouched = false;   // user clicked before boot finished
let rosterReady = false;   // true once bootShell has answered

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
  try { return decodeURIComponent(ctx.route.hash.replace(/^#/, '')); } catch { return ''; }
}

/* No-bot scope (?bot= absent) shows the shared canonical files. Every bot —
   Thyca included — gets its own scoped tab via ?bot=<id>. */

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

/* ---------- Riêng — one bot's own dossier ---------- */

function renderBotDossier(b) {
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
    <p class="profile-readonly">Chỉ đọc — bot tự cập nhật persona qua chat; USER.md riêng của bot chưa có API để hiển thị. Đổi model riêng trong Cài đặt → Bot của bạn.</p>
  </section>`;
}

function renderUnknownBot(id) {
  el.root.innerHTML = `<section class="paper-card profile-bot">
    <div class="eyebrow">Không tìm thấy bot</div>
    <h2 class="card-title">Bot “${escapeHTML(id || '')}” không tồn tại</h2>
    <p class="card-copy">Bot có thể đã bị xóa — trang không bao giờ hiển thị bot khác thay thế. Chọn một bot trong thanh bên, hoặc về Hồ sơ chung.</p>
  </section>`;
}

function renderContent() {
  const b = getBot(state.tab);
  if (b) renderBotDossier(b);
  else if (state.tab) renderUnknownBot(state.tab);
  else renderShared();
}

function showShared(anchor = '') {
  bootTouched = true;
  state.tab = '';
  el.title.textContent = 'Hồ sơ chung';
  selectedBot = '';
  ctx.highlightBot('');
  el.shared.hidden = true;
  ctx.renderTeam();
  fixSidebarLabels();
  renderContent();
  ctx.updateRoute({ search: '', hash: anchor ? '#' + encodeURIComponent(anchor.replace(/^file-/, '')) : '' });
  routeKey = ctx.route.search + ctx.route.hash;
  if (anchor) requestAnimationFrame(() => document.getElementById(anchor)?.scrollIntoView({ block: 'start' }));
}

function showTab(id, anchor = '') {
  const b = getBot(id);
  if (!b) return;
  bootTouched = true;
  state.tab = id;
  el.title.textContent = 'Bạn và ' + b.name;
  selectedBot = id;
  ctx.highlightBot(id);
  el.shared.hidden = false;
  ctx.renderTeam();
  fixSidebarLabels();
  renderContent();
  ctx.updateRoute({ search: '?bot=' + encodeURIComponent(id), hash: '' });
  routeKey = ctx.route.search + ctx.route.hash;
  closeMobile();
  if (anchor) requestAnimationFrame(() => document.getElementById(anchor)?.scrollIntoView({ block: 'start' }));
}

/* Sidebar a11y: select buttons scope the editor here, so relabel them
   page-locally after every shared roster render (no global change). */
function fixSidebarLabels() {
  if (!ctx.isActive()) return;
  for (const btn of [...ctx.sidebar.querySelectorAll('[data-action="select"]')]) {
    const b = getBot(btn.dataset.botId);
    if (b) btn.setAttribute('aria-label', `Hồ sơ của ${b.name}`);
  }
}
new MutationObserver(() => fixSidebarLabels())
  .observe(ctx.sidebar, { childList: true, subtree: true });

/* Sidebar routing stays on this page: a bot card opens its own dossier tab
   (?bot=<id>); the shared files live on the no-bot tab. Thyca has its own
   bot tab like every other twin. */
function selectBot(id) {
  if (!id) { showShared(); return; }
  if (getBot(id)) showTab(id);
}
function loadConversation(id, botId) { if (botId) selectBot(botId); }

async function loadFiles() {
  setStatus(el.status, 'Đang đọc hồ sơ…');
  try {
    const stats = await getJson('/api/memory/stats');            // API: GET /api/memory/stats
    state.files = selectCanonical(stats.files);
    setStatus(el.status, '');
    // Shared files don't feed the bot dossier: a late read must never
    // rebuild a selected bot's form and erase its unsaved draft. Before
    // the first roster lands, an unknown id may just be an unloadable
    // roster — leave the first paint to applyRoute.
    if (!state.tab || (!getBot(state.tab) && rosterReady)) renderContent();
  } catch (err) {
    setStatus(el.status, 'Không tải được hồ sơ: ' + err.message, 's-error');
  }
}

async function reloadProfile() {
  await loadFiles();
  if (typeof loadRoster === 'function') {
    try {
      await loadRoster();
      ctx.renderTeam();
      fixSidebarLabels();
    } catch (err) {
      toast('Không tải được đội bot: ' + (err.message || ''));
    }
  }
  renderContent();
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

window.addEventListener('thyca:bot-settings-saved', () => {
  if (state.tab && getBot(state.tab)) renderContent();
});
el.reload.addEventListener('click', () => reloadProfile());
el.shared.onclick = () => showShared();
el.root.addEventListener('click', e => {
  const btn = e.target.closest('[data-edit]');
  if (btn) openEdit(btn.dataset.edit, btn);
});
el.dialog.querySelector('.dialog-close').onclick = () => el.dialog.close();
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
  let focusAfterSave = null;
  try {
    await postJson('/api/memory/canonical', { name, content });   // API: POST /api/memory/canonical {name, content}
    el.dialog.close();
    await loadFiles();
    const nextOpener = [...el.root.querySelectorAll('[data-edit]')].find(button => button.dataset.edit === name);
    if (nextOpener) { state.opener = nextOpener; focusAfterSave = nextOpener; }
    setStatus(el.status, `Đã lưu ${name}.`, 's-ok');
  } catch (err) {
    setStatus(el.dialogStatus, 'Không lưu được file: ' + err.message, 's-error');
  } finally {
    setBusy(false);
    focusAfterSave?.focus();
  }
});

/* Deep links: ?bot=<id> opens that bot's tab (?bot=ghost is preserved as an
   honest unknown-bot view, never another bot); #<file> lands the shared
   tab on the right section. */
function applyRoute() {
  const hash = decodeHash();
  if (state.tab && getBot(state.tab)) { showTab(state.tab); return; }
  if (state.tab) { selectedBot = ''; ctx.highlightBot(''); ctx.renderTeam(); fixSidebarLabels(); el.shared.hidden = false; el.title.textContent = 'Không tìm thấy bot'; renderContent(); return; }
  if (state.files.some(f => f.name === hash)) { showShared('file-' + hash); return; }
  showShared();
}
let routeKey = '';

async function boot() {
  // Parse ?bot= first; the scope is applied after roster + files arrive,
  // and a user click before boot finishes (bootTouched) always wins.
  state.tab = new URLSearchParams(ctx.route.search).get('bot') || '';
  await loadFiles();                                 // API: GET /api/memory/stats
  try { await ctx.shellReady; }                         // API: GET /api/bots + GET /api/conversations
  catch (err) { toast('Không tải được đội bot: ' + (err.message || '')); }
  rosterReady = true;
  routeKey = ctx.route.search + ctx.route.hash;
  applyRoute();
}
  const ready = boot();
  return { ready, selectBot, loadConversation, sidebarLabel(_id, name) { return `Hồ sơ của ${name}`; }, onRoute(route) {
    const key = route.search + route.hash;
    if (key === routeKey) return;
    routeKey = key;
    state.tab = new URLSearchParams(route.search).get('bot') || '';
    applyRoute();
  } };
} });
