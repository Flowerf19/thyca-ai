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

/* Page markup fills the .workspace that emitShell() already placed (with the
   shared ornaments) — see the script order in index.html. */
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
  <div class="page-actions">
    <button class="quiet-button" id="profile-shared" type="button" hidden>Hồ sơ chung</button>
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

/* Per-bot model settings state (scoped form only). `tab` stays the single
   source of scope; this holds the fetched payload and request guards. */
const botCfg = { settings: null, seq: 0, loading: false, savingFor: null, error: '' };
let botCatalog = null;     // {models, defaultModel, defaultProvider, providers}
let botSchemaChoices = [];
let loadedCfg = null;      // {bot, model, thinking} | null — last server-confirmed baseline
let scopeSeq = 0;          // bumped on every scope change; refreshes abort on mismatch
let rosterReady = false;   // true once bootShell has answered
let catalogDirty = false, catalogGeneration = 0, refreshAllPromise = null, drainingCatalog = false;

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

/* ---------- Riêng — one bot's own dossier + its model form ---------- */

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
    <p class="profile-readonly">Chỉ đọc — bot tự cập nhật persona qua chat; USER.md riêng của bot chưa có API để hiển thị.</p>
  </section>
  <section class="paper-card" id="bot-model-card" aria-labelledby="bot-model-title">
    <div class="eyebrow">Model &amp; mức suy nghĩ</div>
    <h2 class="card-title" id="bot-model-title">Model của ${escapeHTML(b.name)}</h2>
    <p class="card-copy">Model và mức suy luận riêng của bot này; để trống là theo mặc định chung. Provider suy ra từ model đã chọn.</p>
    <form id="bot-settings-form" novalidate aria-label="Model và mức suy luận của bot">
      <div id="bot-settings-fields"><p class="empty-note">Đang tải cài đặt bot…</p></div>
      <div class="form-footer">
       <button class="primary-action" type="submit" id="save-bot-settings" disabled>Lưu cài đặt bot</button>
       <span class="form-hint" id="bot-settings-hint"></span>
      </div>
    </form>
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
  if (b) renderBotTab(b);
  else if (state.tab) renderUnknownBot(state.tab);
  else renderShared();
}

/* Bot tab: the dossier re-renders freely, but the model form keeps the
   user's unsaved draft across shared-file reloads. A draft is the current
   field values when they differ from the last LOADED settings of the SAME
   bot — never another bot's values, never the restored draft itself. */
function renderBotTab(b, { preserveDraft = true } = {}) {
  const draft = preserveDraft ? botFormDraft() : null;
  renderBotDossier(b);
  if (draft) fillBotFormFields(botCfg.settings, draft);
  else renderBotForm();
}

function showShared(anchor = '') {
  bootTouched = true;
  scopeSeq++;
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
  scopeSeq++;
  state.tab = id;
  el.title.textContent = 'Bạn và ' + b.name;
  selectedBot = id;
  ctx.highlightBot(id);
  el.shared.hidden = false;
  ctx.renderTeam();
  fixSidebarLabels();
  // Drop the previous bot's settings BEFORE painting so a stale payload
  // can never appear under the new bot's heading.
  botCfg.settings = null;
  botCfg.error = '';
  loadedCfg = null;
  botCfg.loading = true;
  renderContent();
  loadBotSettings();
  ctx.updateRoute({ search: '?bot=' + encodeURIComponent(id), hash: '' });
  routeKey = ctx.route.search + ctx.route.hash;
  closeMobile();
  if (anchor) requestAnimationFrame(() => document.getElementById(anchor)?.scrollIntoView({ block: 'start' }));
}

/* Bot settings submit: delegated (the form is re-created on every render).
   Only ever POSTs /api/bots/<id>/settings — never /api/config or files. */
if (!el.root.dataset.botSubmitBound) {
  el.root.dataset.botSubmitBound = '1';
  el.root.addEventListener('submit', async e => {
    if (!e.target || e.target.id !== 'bot-settings-form') return;
    e.preventDefault();
    const id = state.tab;
    const b = getBot(id);
    const modelSel = $('#bot-model'), thinkSel = $('#bot-thinking');
    // Never save without a scope, a catalog, rendered controls and loaded
    // settings — a catalog load failure cannot submit. Never save to a
    // fallback bot, while loading, or while another save is in flight.
    if (!id || !b || !botCatalog || !modelSel || !thinkSel) return;
    if (!botCfg.settings || botCfg.loading || botCfg.savingFor) return;
    const seq = ++botCfg.seq;
    botCfg.savingFor = { bot: id, seq };
    $('#save-bot-settings').disabled = true;
    $('#bot-settings-hint').textContent = 'Đang lưu…';
    const payload = { model: { id: modelSel.value, thinking: thinkSel.value || null } };
    try {
      const data = await postJson(`/api/bots/${encodeURIComponent(id)}/settings`, payload);   // API: POST full replacement
      if (seq !== botCfg.seq || state.tab !== id) return;         // stale: switched meanwhile, released below
      botCfg.settings = data;
      const live = getBot(id);
      if (live) {
        live.model = data.model;
        ctx.renderTeam();
        fixSidebarLabels();
        renderBotTab(live, { preserveDraft: false });
      } else {
        renderBotForm();
      }
      $('#bot-settings-hint').textContent = 'Đã lưu.';
      toast(`Đã lưu cài đặt cho ${b.name}.`);
    } catch (err) {
      if (seq !== botCfg.seq || state.tab !== id) return;
      // Keep the error draft: only the hint updates, fields stay editable.
      $('#bot-settings-hint').textContent = 'Không lưu được: ' + err.message;
    } finally {
      // Release unconditionally for THIS operation, then recompute the
      // current form's Save from current state — never repaint a stale
      // response onto another bot, never erase an unsaved draft there.
      if (botCfg.savingFor && botCfg.savingFor.seq === seq) {
        botCfg.savingFor = null;
        if (state.tab === id) {
          if ($('#bot-settings-hint').textContent === 'Đang lưu…') {
            $('#bot-settings-hint').textContent = '';
            renderBotForm();
          } else {
            updateBotSave();
          }
        } else {
          updateBotSave();
        }
      }
      if (catalogDirty && !botCfg.savingFor && ctx.isActive()) void drainDirtyCatalog();
    }
  });
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
  if (getBot(id)) showTab(id);
}
function loadSession(id, title, botId) { if (botId) selectBot(botId); }

/* ---------- Per-bot model settings (scoped form, same page) ---------- */

function botModelIds() {
  const models = botCatalog?.models && typeof botCatalog.models === 'object' ? botCatalog.models : {};
  const def = typeof botCatalog?.defaultModel === 'string' ? botCatalog.defaultModel : '';
  return [...new Set(['default', def, ...Object.keys(models)].filter(Boolean))];
}

function botModelLabel(id) {
  const def = botCatalog?.defaultModel || '';
  return id === 'default' ? `Theo mặc định chung${def ? ' (' + def + ')' : ''}` : id;
}

function botEffortChoices(modelId) {
  const registered = botCatalog?.models?.[modelId];
  const own = Array.isArray(registered?.reasoningEfforts) ? registered.reasoningEfforts.filter(Boolean) : [];
  return own.length ? own : botSchemaChoices;
}

function botDraftProviderId(modelId) {
  const models = botCatalog?.models && typeof botCatalog.models === 'object' ? botCatalog.models : {};
  const defProvider = typeof botCatalog?.defaultProvider === 'string' ? botCatalog.defaultProvider : '';
  if (modelId === 'default') {
    // Inheritance resolves through the configured default MODEL first
    // (then its registered provider); defaultProvider is only the
    // fallback for unregistered models.
    const dm = botCatalog?.defaultModel || '';
    const entry = dm ? models[dm] : null;
    return (entry?.provider && botCatalog?.providers?.[entry.provider]) ? entry.provider : defProvider;
  }
  // An unknown model id is never fake-routed to the default provider.
  if (!Object.hasOwn(models, modelId)) {
    return modelId === botCatalog?.defaultModel ? defProvider : '';
  }
  const entry = models[modelId];
  return (entry?.provider && botCatalog?.providers?.[entry.provider]) ? entry.provider : defProvider;
}

/* A saved-but-unregistered model id: 'default' always counts, registered
   ids count, and an explicit id equal to a custom defaultModel counts
   (the backend routes it through defaultProvider). Anything else is
   unknown: shown honestly, never silently reassigned, Save stays off. */
function botModelKnown(id) {
  const models = botCatalog?.models && typeof botCatalog.models === 'object' ? botCatalog.models : {};
  const def = botCatalog?.defaultModel || '';
  return id === 'default' || Object.hasOwn(models, id) || (!!def && id === def);
}

/* Single Save-gate: loading, in-flight save, no bot, no settings, or an
   unavailable selected id all keep Save disabled. */
function updateBotSave() {
  const save = $('#save-bot-settings');
  const sel = $('#bot-model');
  if (!save) return;
  save.disabled = botCfg.loading || !!botCfg.savingFor || !getBot(state.tab)
    || !botCfg.settings || !sel || !botModelKnown(sel.value);
}

function paintBotProviderPreview() {
  const sel = $('#bot-model'), el = $('#bot-provider-preview');
  if (!sel || !el || !botCatalog) return;
  const selVal = sel.value || 'default';
  if (!botModelKnown(selVal)) {
    el.textContent = 'Provider: — (model không còn trong danh mục)';
    return;
  }
  const draftPid = botDraftProviderId(selVal);
  const savedPid = botCfg.settings?.effective?.provider || '';
  el.textContent = draftPid
    ? (draftPid === savedPid ? `Provider: ${draftPid}` : `Provider: ${draftPid} (dự kiến — chưa lưu)`)
    : 'Provider: —';
}

function botFieldsHtml(modelId, thinking, effective) {
  const ids = botModelIds();
  const def = botCatalog.defaultModel || '';
  const known = botModelKnown(modelId);
  const list = known ? ids : [...ids, modelId];
  const choices = botEffortChoices(modelId === 'default' ? def : modelId);
  const effModel = (effective && effective.model) || modelId;
  const effThinking = effective && effective.thinking;
  const effProvider = effective && effective.provider;
  return `
   <div class="card-grid">
    <label class="field" for="bot-model"><span>Model</span>
     <select id="bot-model">${list.map(id => `<option value="${escapeHTML(id)}"${id === modelId ? ' selected' : ''}>${escapeHTML(botModelLabel(id) + (!known && id === modelId ? ' (không còn trong danh mục)' : ''))}</option>`).join('')}</select></label>
    <label class="field" for="bot-thinking"><span>Mức suy luận</span>
     <select id="bot-thinking"><option value="">Theo model/provider</option>${choices.map(c => `<option value="${escapeHTML(c)}"${String(thinking) === String(c) ? ' selected' : ''}>${escapeHTML(c)}</option>`).join('')}</select></label>
   </div>
   <p class="field-hint" id="bot-model-notice"${known ? ' hidden' : ''}>${known ? '' : 'Model đã lưu không còn trong danh mục — chọn model khác rồi lưu.'}</p>
   <div class="bot-model-meta">
    <p class="bot-provider-line" id="bot-provider-preview"></p>
    <p class="bot-effective">Đang dùng: <strong>${escapeHTML(effModel)}</strong>${effThinking ? ` · suy luận ${escapeHTML(effThinking)}` : ''}${effProvider ? ` · Provider: ${escapeHTML(effProvider)}` : ''}</p>
   </div>`;
}

function bindBotFormControls() {
  const def = botCatalog.defaultModel || '';
  $('#bot-model').onchange = () => {
    const next = $('#bot-thinking');
    const keep = next.value;
    const list = botEffortChoices($('#bot-model').value === 'default' ? def : $('#bot-model').value);
    next.replaceChildren(
      Object.assign(document.createElement('option'), { value: '', textContent: 'Theo model/provider' }),
      ...list.map(c => Object.assign(document.createElement('option'), { value: c, textContent: c })),
    );
    next.value = list.includes(keep) ? keep : '';
    paintBotProviderPreview();
    updateBotSave();
  };
  paintBotProviderPreview();
  updateBotSave();
}

/* The draft currently in the form: current field values when they differ
   from the last LOADED settings of the SAME bot (never another bot's
   values, never the restored draft itself). */
function botFormDraft() {
  const m = $('#bot-model'), t = $('#bot-thinking');
  if (!m || !t || !loadedCfg || loadedCfg.bot !== state.tab) return null;
  const cur = { model: m.value, thinking: t.value || null };
  return (cur.model !== loadedCfg.model || cur.thinking !== loadedCfg.thinking) ? cur : null;
}

function renderBotForm() {
  const host = $('#bot-settings-fields');
  const hint = $('#bot-settings-hint');
  if (!host || !hint) return;
  const b = getBot(state.tab);
  if (!b || !botCatalog) {
    host.innerHTML = !b
      ? `<p class="empty-note"><strong>Không tìm thấy bot “${escapeHTML(state.tab || '')}”.</strong>Bot có thể đã bị xóa — trang không bao giờ lưu sang bot khác.</p>`
      : '<p class="empty-note"><strong>Chưa tải được danh mục model.</strong>Tải lại rồi thử lại.</p>';
    hint.textContent = '';
    updateBotSave();
    return;
  }
  const s = botCfg.settings;
  if (!s) {
    host.innerHTML = botCfg.loading
      ? '<p class="empty-note">Đang tải cài đặt bot…</p>'
      : `<p class="empty-note"><strong>Không tải được cài đặt bot.</strong>${escapeHTML(botCfg.error || 'Máy chủ bận.')} — thử lại sau.</p>`;
    hint.textContent = '';
    updateBotSave();
    return;
  }
  const configured = s.model || { id: 'default', thinking: null };
  host.innerHTML = botFieldsHtml(configured.id, configured.thinking ?? '', s.effective || {});
  loadedCfg = { bot: state.tab, model: configured.id, thinking: configured.thinking ?? null };
  hint.textContent = '';
  bindBotFormControls();
}

/* Rebuild a rendered form around explicit values (draft restore): same
   builder as a fresh render, then the chosen values. */
function fillBotFormFields(settings, draft) {
  const host = $('#bot-settings-fields');
  const hint = $('#bot-settings-hint');
  if (!host || !hint || !settings) { renderBotForm(); return; }
  host.innerHTML = botFieldsHtml(draft.model, draft.thinking ?? null, settings.effective || {});
  hint.textContent = '';
  bindBotFormControls();
}

async function loadBotSettings(preserved) {
  const id = state.tab;
  if (!id || !getBot(id)) { renderBotForm(); return; }
  const seq = ++botCfg.seq;
  botCfg.loading = true;
  botCfg.settings = null;
  botCfg.error = '';
  loadedCfg = null;
  renderBotForm();
  const save = $('#save-bot-settings');
  if (save) save.disabled = true;
  const hint = $('#bot-settings-hint');
  if (hint) hint.textContent = 'Đang tải…';
  try {
    const data = await getJson(`/api/bots/${encodeURIComponent(id)}/settings`);   // API: GET /api/bots/<id>/settings
    if (seq !== botCfg.seq || state.tab !== id) return;                    // stale: user switched meanwhile
    botCfg.settings = data;
  } catch (err) {
    if (seq !== botCfg.seq || state.tab !== id) return;
    botCfg.error = err.message;
  } finally {
    if (seq === botCfg.seq && state.tab === id) {
      botCfg.loading = false;
      if (botCfg.settings) {
        const m = botCfg.settings.model || {};
        loadedCfg = { bot: id, model: m.id || 'default', thinking: m.thinking ?? null };
      }
      if (preserved) fillBotFormFields(botCfg.settings, preserved);
      else renderBotForm();
    }
  }
}


/* ---------- shared catalog + scope refresh ---------- */

async function refreshBotConfig() {
  // Whole-values config: secrets stay blank server-side (merge_saved_keys).
  const payload = await getJson('/api/config');                 // API: GET /api/config
  const values = payload.values || {};
  const sections = (payload.schema && payload.schema.sections) || [];
  const field = sections.flatMap(s => s.fields || []).find(f => f.key === 'provider.reasoningEffort');
  botCatalog = {
    models: (values.models && typeof values.models === 'object') ? values.models : {},
    defaultModel: typeof values.defaultModel === 'string' ? values.defaultModel : '',
    defaultProvider: typeof values.defaultProvider === 'string' ? values.defaultProvider : '',
    providers: (values.providers && typeof values.providers === 'object') ? values.providers : {},
  };
  botSchemaChoices = Array.isArray(field?.choices) ? field.choices : ['low', 'medium', 'high', 'xhigh', 'max'];
}

/* Reload button: config + current scope, preserving any unsaved bot draft.
   The draft is captured BEFORE anything re-renders, then restored only if
   its model/effort are still valid options. */
async function performRefreshAll() {
  const draft = botFormDraft();
  const myScope = scopeSeq, myTab = state.tab;
  setStatus(el.status, 'Đang tải lại…');
  let cfgOk = true;
  try {
    await refreshBotConfig();
  } catch (err) {
    cfgOk = false;
    setStatus(el.status, 'Không tải được cấu hình: ' + err.message, 's-error');
  }
  if (myScope !== scopeSeq || state.tab !== myTab) return false;   // scope moved: catalog kept, no stale attach
  if (getBot(state.tab)) {
    await loadBotSettings(draft);
    if (myScope !== scopeSeq || state.tab !== myTab) return false;
    if (!cfgOk) setStatus(el.status, 'Không tải được cấu hình — giữ danh mục cũ.', 'error');
    else if (!botCfg.error) setStatus(el.status, '');
  } else if (!state.tab) {
    await loadFiles();
  } else {
    renderContent();   // unknown bot: honest error stays, no fallback
    setStatus(el.status, '');
  }
  return cfgOk;
}
function refreshAll() {
  if (refreshAllPromise) return refreshAllPromise;
  const running = performRefreshAll();
  refreshAllPromise = running;
  return running.finally(() => { if (refreshAllPromise === running) refreshAllPromise = null; });
}
async function drainDirtyCatalog() {
  if (!catalogDirty || botCfg.savingFor || !ctx.isActive() || drainingCatalog) return;
  drainingCatalog = true;
  try {
    while (catalogDirty && !botCfg.savingFor && ctx.isActive()) {
      const generation = catalogGeneration;
      catalogDirty = false;
      const ok = await refreshAll();
      if (!ok) { catalogDirty = true; break; }
      if (generation !== catalogGeneration) catalogDirty = true;
    }
  } finally {
    drainingCatalog = false;
  }
}

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

window.addEventListener('thyca:config-saved', () => { catalogDirty = true; catalogGeneration++; });
el.reload.addEventListener('click', () => refreshAll());
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
  // Parse ?bot= first; the scope is applied after roster + files + catalog
  // arrive, and a user click before boot finishes (bootTouched) always wins.
  state.tab = new URLSearchParams(ctx.route.search).get('bot') || '';
  await loadFiles();                                 // API: GET /api/memory/stats
  try { await ctx.shellReady; }                         // API: GET /api/bots + GET /api/sessions
  catch (err) { toast('Không tải được đội bot: ' + (err.message || '')); }
  rosterReady = true;
  try { await refreshBotConfig(); }                   // API: GET /api/config (scoped model catalog)
  catch (err) { setStatus(el.status, 'Không tải được cấu hình: ' + err.message, 'error'); }
  routeKey = ctx.route.search + ctx.route.hash;
  applyRoute();
}
  const ready = boot();
  return { ready, selectBot, loadSession, sidebarLabel(_id, name) { return `Hồ sơ của ${name}`; }, onEnter() {
    void drainDirtyCatalog();
  }, onRoute(route) {
    const key = route.search + route.hash;
    if (key === routeKey) return;
    routeKey = key;
    state.tab = new URLSearchParams(route.search).get('bot') || '';
    applyRoute();
  } };
} });
