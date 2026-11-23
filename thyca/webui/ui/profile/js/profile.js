ThycaRouter.register('profile', { title: 'Hồ sơ', stylesheet: '/ui/profile/profilecss/profile.css', mount(ctx) {
  const root = ctx.root;
  const $ = (selector, base = root) => selector === '.workspace' ? root : base.querySelector(selector);
  const $$ = (selector, base = root) => [...base.querySelectorAll(selector)];
  let selectedBot = '';

const CANONICAL_META = {
  'USER.md': { tab: 'Về người dùng', title: 'Về người dùng', description: 'Thông tin, sở thích và bối cảnh Thyca dùng khi trả lời.' },
  'SOUL.md': { tab: 'Giọng nói', title: 'Giọng nói của Thyca', description: 'Cách Thyca trò chuyện và hỗ trợ bạn.' },
  'IDENTITY.md': { tab: 'Danh tính', title: 'Danh tính Thyca', description: 'Danh tính và giới hạn của trợ lý.' },
};
const CANONICAL_ORDER = { 'USER.md': 0, 'SOUL.md': 1, 'IDENTITY.md': 2 };
const FALLBACK_DESCRIPTION = 'File hồ sơ được đưa vào ngữ cảnh của Thyca.';
const BASH_LABELS = { ask: 'lệnh: hỏi trước', allow: 'lệnh: tự chạy', deny: 'lệnh: tắt' };

function selectCanonical(files) {
  return (Array.isArray(files) ? files : [])
    .filter(f => f && typeof f.name === 'string')
    .map(f => {
      const meta = CANONICAL_META[f.name] || { tab: f.name, title: f.name, description: FALLBACK_DESCRIPTION };
      return { name: f.name, content: String(f.content || ''), ...meta };
    })
    .sort((a, b) => (CANONICAL_ORDER[a.name] ?? 9) - (CANONICAL_ORDER[b.name] ?? 9) || a.name.localeCompare(b.name));
}

const profileTemplate = document.querySelector('template#tpl-profile');
root.insertAdjacentHTML('beforeend', profileTemplate ? profileTemplate.innerHTML : '');

const el = {
  root: $('#profile-root'),
  title: $('#profile-title'),
  status: $('#profile-status'),
  reload: $('#profile-reload'),
  shared: $('#profile-shared'),
};
const state = {
  files: [], tab: '', doc: '', busy: false, opener: null,
  editing: '', editBase: '', editDraft: '', editMode: 'split',
  sectionEdit: null,
};
let bootTouched = false;
let rosterReady = false;

function setStatus(node, text, kind = '') {
  if (!node) return;
  node.hidden = !text;
  node.className = 'page-status' + (kind ? ' ' + kind : '');
  node.textContent = text || '';
}

const contentHtml = text => window.formatMarkdown
  ? window.formatMarkdown(text)
  : escapeHTML(String(text || '')).split(/\n{2,}/).map(p => `<p>${p.replace(/\n/g, '<br>')}</p>`).join('');

function decodeHashValue(rawHash) {
  try { return decodeURIComponent(String(rawHash || '').replace(/^#/, '')); } catch { return ''; }
}
function decodeHash() {
  return decodeHashValue(ctx.route.hash);
}

function activeFile() {
  return state.files.find(f => f.name === state.doc) || state.files[0] || null;
}

function isEditDirty() {
  if (state.editing && state.editDraft !== state.editBase) return true;
  const se = state.sectionEdit;
  return Boolean(se) && (se.title !== se.baseTitle || se.body !== se.baseBody);
}

function confirmDiscardEdit() {
  if (!isEditDirty()) return true;
  return window.confirm('Có thay đổi chưa lưu. Bỏ bản nháp hiện tại?');
}

function defaultEditMode() {
  return (typeof window.matchMedia === 'function' && window.matchMedia('(max-width: 850px)').matches)
    ? 'write' : 'split';
}

function paintEditPreview() {
  const preview = $('#edit-preview'), meta = $('#edit-meta'), dirty = $('#edit-dirty');
  if (preview) {
    preview.innerHTML = state.editDraft.trim()
      ? contentHtml(state.editDraft)
      : '<p class="profile-preview-empty">Nội dung đang trống.</p>';
  }
  if (meta) {
    const lines = state.editDraft ? state.editDraft.split(/\r?\n/).length : 0;
    const chars = state.editDraft.length;
    meta.textContent = `${lines} dòng · ${chars} ký tự · Ctrl+S lưu`;
  }
  if (dirty) dirty.hidden = !isEditDirty();
}

function bindInlineEditor() {
  const form = $('#edit-form'), field = $('#edit-content'), cancel = $('#edit-cancel');
  if (!form || !field) return;
  field.value = state.editDraft;
  paintEditPreview();
  form.onsubmit = submitEdit;
  if (cancel) cancel.onclick = () => closeEdit();
  field.oninput = () => {
    state.editDraft = field.value;
    paintEditPreview();
  };
  field.onkeydown = e => {
    if ((e.ctrlKey || e.metaKey) && String(e.key || '').toLowerCase() === 's') {
      e.preventDefault();
      void submitEdit(e);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      closeEdit();
    }
  };
}

function hasVisibleMarkdown(text) {
  return String(text || '').replace(/<!--[\s\S]*?-->/g, '').trim().length > 0;
}

function splitSections(raw) {
  const normalized = String(raw || '').replace(/\r\n/g, '\n');
  if (!normalized) return { preamble: '', introBody: '', sections: [] };
  const lines = normalized.match(/[^\n]*\n|[^\n]+/g) || [];
  let fence = null;
  let preamble = '';
  const sections = [];
  for (const rawLine of lines) {
    const lineNoNl = rawLine.endsWith('\n') ? rawLine.slice(0, -1) : rawLine;
    const fenceMatch = lineNoNl.match(/^\s*(`{3,}|~{3,})/);
    if (fenceMatch) {
      const marker = fenceMatch[1];
      if (!fence) fence = { ch: marker[0], len: marker.length };
      else if (marker[0] === fence.ch && marker.length >= fence.len && /^\s*(`{3,}|~{3,})\s*$/.test(lineNoNl)) fence = null;
    }
    if (!fence && /^##\s+(.+?)\s*$/.test(lineNoNl)) {
      sections.push({
        index: sections.length,
        headingLine: rawLine,
        title: lineNoNl.replace(/^##\s+/, '').trim(),
        rawBody: '',
      });
    } else if (sections.length === 0) {
      preamble += rawLine;
    } else {
      sections[sections.length - 1].rawBody += rawLine;
    }
  }
  for (const sec of sections) {
    sec.editBody = sec.rawBody.replace(/^\n+/, '').replace(/\n+$/, '');
  }
  const introBody = preamble.replace(/^\s*#\s+[^\n]*\n?/, '').trim();
  return { preamble, introBody, sections };
}

function mergeSection(raw, index, newTitle, newBody) {
  const normalized = String(raw || '').replace(/\r\n/g, '\n');
  const title = String(newTitle || '').trim();
  const cleanBody = String(newBody || '').replace(/\r\n/g, '\n').replace(/^\n+/, '').replace(/\n+$/, '');
  if (index === -1) {
    const prefix = normalized.trimEnd();
    const heading = title || 'Mục mới';
    const block = cleanBody ? `## ${heading}\n\n${cleanBody}\n` : `## ${heading}\n`;
    return prefix ? `${prefix}\n\n${block}` : block;
  }
  const parsed = splitSections(normalized);
  const target = parsed.sections[index];
  if (!target) return normalized;
  const finalTitle = title || target.title;
  const sep = index < parsed.sections.length - 1 ? '\n\n' : '\n';
  const chunk = cleanBody ? `## ${finalTitle}\n\n${cleanBody}${sep}` : `## ${finalTitle}${sep}`;
  let preamble = parsed.preamble;
  if (index === 0 && preamble && !preamble.endsWith('\n')) preamble += '\n\n';
  const parts = parsed.sections.map((sec, idx) => (idx === index ? chunk : `${sec.headingLine}${sec.rawBody}`));
  return preamble + parts.join('');
}

function renderEditCard(file) {
  const modeButton = (mode, label) => `<button type="button" class="chip" data-edit-mode="${mode}" aria-pressed="${state.editMode === mode}">${label}</button>`;
  return `<section class="paper-card profile-doc" data-editing="true" id="file-${escapeHTML(file.name)}" aria-label="${escapeHTML(file.name)}">
    <div class="profile-doc-head">
      <div class="profile-doc-titles">
        <div class="profile-doc-title-row"><h2 class="card-title">${escapeHTML(file.title)}</h2><code class="profile-file-badge">${escapeHTML(file.name)}</code></div>
        <p class="card-copy">${escapeHTML(file.description)}</p>
      </div>
      <div class="profile-doc-actions">
        <span class="profile-dirty-badge" id="edit-dirty"${isEditDirty() ? '' : ' hidden'}>Chưa lưu</span>
        <div class="profile-edit-modes" role="group" aria-label="Chế độ soạn thảo">${modeButton('write', 'Viết')}${modeButton('split', 'Song song')}${modeButton('preview', 'Xem trước')}</div>
      </div>
    </div>
    <form class="profile-inline-editor" id="edit-form" data-mode="${escapeHTML(state.editMode)}">
      <input type="hidden" id="edit-name" value="${escapeHTML(file.name)}">
      <div class="profile-editor-panes">
        <div class="profile-editor-write">
          <label class="sr-only" for="edit-content">Nội dung ${escapeHTML(file.name)}</label>
          <textarea id="edit-content" spellcheck="false"></textarea>
        </div>
        <div class="profile-editor-preview">
          <div class="profile-preview-kicker">Xem trước</div>
          <article class="markdown-body profile-content" id="edit-preview"></article>
        </div>
      </div>
      <p class="page-status" id="edit-status" role="status" hidden></p>
      <div class="profile-editor-footer">
        <button class="primary-action" type="submit" id="edit-save">Lưu file</button>
        <button class="quiet-button" type="button" id="edit-cancel">Hủy</button>
        <span class="profile-editor-hint" id="edit-meta"></span>
      </div>
    </form>
  </section>`;
}

function renderSectionEditorForm(isNew) {
  return `<form class="profile-section-form" id="section-edit-form">
    <label class="field">
      <span>Tên mục</span>
      <input id="section-edit-title" type="text" autocomplete="off" placeholder="Ví dụ: Thói quen làm việc">
    </label>
    <label class="field">
      <span>Nội dung markdown</span>
      <textarea id="section-edit-body" spellcheck="false" placeholder="Ghi ý chính dạng gạch đầu dòng hoặc đoạn ngắn..."></textarea>
    </label>
    <p class="page-status" id="section-edit-status" role="status" hidden></p>
    <div class="profile-section-footer">
      <button class="primary-action" type="submit" id="section-edit-save">${isNew ? 'Thêm mục' : 'Lưu mục'}</button>
      <button class="quiet-button" type="button" id="section-edit-cancel">Hủy</button>
      <span class="profile-editor-hint">Ctrl+S lưu</span>
    </div>
  </form>`;
}

function renderSectionCard(sec, isEditingSection) {
  if (isEditingSection) {
    return `<article class="profile-section-card" data-editing="true" data-section-index="${sec.index}">
      <div class="profile-section-head">
        <h3 class="profile-section-title">${escapeHTML(sec.title)}</h3>
      </div>
      ${renderSectionEditorForm(false)}
    </article>`;
  }
  const hasVisible = hasVisibleMarkdown(sec.editBody);
  return `<article class="profile-section-card"${hasVisible ? '' : ' data-empty="true"'} data-section-index="${sec.index}">
    <div class="profile-section-head">
      <h3 class="profile-section-title">${escapeHTML(sec.title)}</h3>
      <button class="quiet-button profile-section-btn" type="button" data-edit-section="${sec.index}"${state.busy ? ' disabled' : ''}>Sửa</button>
    </div>
    ${hasVisible
      ? `<div class="markdown-body profile-section-body">${contentHtml(sec.editBody)}</div>`
      : '<p class="profile-section-empty">Chưa có nội dung — nhấn Sửa để điền.</p>'}
  </article>`;
}

function bindSectionEditor() {
  const form = $('#section-edit-form'), titleEl = $('#section-edit-title'),
    bodyEl = $('#section-edit-body'), cancel = $('#section-edit-cancel');
  if (!form || !bodyEl || !state.sectionEdit) return;
  if (titleEl) {
    titleEl.value = state.sectionEdit.title;
    titleEl.oninput = () => { if (state.sectionEdit) state.sectionEdit.title = titleEl.value; };
  }
  bodyEl.value = state.sectionEdit.body;
  bodyEl.oninput = () => { if (state.sectionEdit) state.sectionEdit.body = bodyEl.value; };
  form.onsubmit = submitSectionEdit;
  if (cancel) cancel.onclick = () => closeSectionEdit();
  const onKey = e => {
    if ((e.ctrlKey || e.metaKey) && String(e.key || '').toLowerCase() === 's') {
      e.preventDefault();
      void submitSectionEdit(e);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      closeSectionEdit();
    }
  };
  bodyEl.onkeydown = onKey;
  if (titleEl) titleEl.onkeydown = onKey;
}

function renderShared() {
  const file = activeFile();
  if (!file) {
    el.root.innerHTML = '<p class="empty-note"><strong>Chưa có file hồ sơ.</strong></p>';
    return;
  }
  state.doc = file.name;
  const tabsHtml = `<div class="profile-doc-tabs" id="profile-doc-tabs" role="tablist" aria-label="Tài liệu hồ sơ">${
    state.files.map(item => {
      const active = item.name === file.name;
      return `<button type="button" class="profile-doc-tab" role="tab" data-doc="${escapeHTML(item.name)}" aria-selected="${active}" data-active="${active}"><span>${escapeHTML(item.tab)}</span><small>${escapeHTML(item.name)}</small></button>`;
    }).join('')
  }</div>`;

  if (state.editing === file.name) {
    el.root.innerHTML = tabsHtml + renderEditCard(file);
    bindInlineEditor();
    return;
  }

  const parsed = splitSections(file.content);
  const se = state.sectionEdit?.file === file.name ? state.sectionEdit : null;
  const hasContent = file.content.trim().length > 0;
  let bodyHtml = '';
  if (!hasContent && !se) {
    bodyHtml = '<p class="empty-note"><strong>File đang trống.</strong>Nhấn “Sửa toàn file” hoặc “+ Thêm mục” để viết nội dung cho trang này.</p>';
  } else if (parsed.sections.length === 0 && !se) {
    bodyHtml = `<article class="markdown-body profile-content">${contentHtml(file.content)}</article>`;
  } else {
    const introHtml = hasVisibleMarkdown(parsed.introBody)
      ? `<div class="markdown-body profile-doc-intro">${contentHtml(parsed.introBody)}</div>` : '';
    const cardsHtml = parsed.sections.map(sec => renderSectionCard(sec, Boolean(se && se.index === sec.index))).join('');
    const newCardHtml = se && se.index === -1
      ? `<article class="profile-section-card" data-editing="true" data-section-index="-1">
           <div class="profile-section-head"><h3 class="profile-section-title">Mục mới</h3></div>
           ${renderSectionEditorForm(true)}
         </article>`
      : '';
    const single = parsed.sections.length + (se?.index === -1 ? 1 : 0) <= 1 && !se;
    bodyHtml = `${introHtml}<div class="profile-section-grid"${single ? ' data-single="true"' : ''}>${cardsHtml}${newCardHtml}</div>`;
  }

  el.root.innerHTML = `${tabsHtml}<section class="paper-card profile-doc" id="file-${escapeHTML(file.name)}" aria-label="${escapeHTML(file.name)}">
    <div class="profile-doc-head">
      <div class="profile-doc-titles">
        <div class="profile-doc-title-row"><h2 class="card-title">${escapeHTML(file.title)}</h2><code class="profile-file-badge">${escapeHTML(file.name)}</code></div>
        <p class="card-copy">${escapeHTML(file.description)}</p>
      </div>
      <div class="profile-doc-actions">
        <button class="quiet-button" data-add-section="${escapeHTML(file.name)}" type="button"${state.busy ? ' disabled' : ''}>+ Thêm mục</button>
        <button class="quiet-button" data-edit="${escapeHTML(file.name)}" type="button"${state.busy ? ' disabled' : ''}>Sửa toàn file</button>
      </div>
    </div>
    ${bodyHtml}
  </section>`;
  if (se) bindSectionEditor();
}

function botFacts(b) {
  return [
    b.model && b.model.id ? `model: ${b.model.id}${b.model.thinking ? ' · suy nghĩ ' + b.model.thinking : ''}` : '',
    b.bash ? (BASH_LABELS[b.bash] || `bash: ${b.bash}`) : '',
    b.memory ? (b.memory.read && b.memory.write ? 'bộ nhớ: đọc + ghi' : b.memory.read ? 'bộ nhớ: chỉ đọc' : 'bộ nhớ: tắt') : '',
    b.system ? 'bot hệ thống' : '',
  ].filter(Boolean);
}

function renderBotDossier(b) {
  const facts = botFacts(b);
  el.root.innerHTML = `<section class="paper-card profile-bot">
    <div class="profile-bot-grid">
      <aside class="profile-bot-aside">
        <button class="quiet-button profile-back-shared" type="button" data-action="show-shared">← Hồ sơ chung</button>
        <div class="profile-bot-identity">
          ${avatar(b)}
          <div class="profile-bot-titles">
            <h2 class="card-title">${escapeHTML(b.name)}</h2>
            ${b.role ? `<div class="profile-bot-role">${escapeHTML(b.role)}</div>` : ''}
          </div>
        </div>
        ${b.description ? `<p class="card-copy profile-bot-desc">${escapeHTML(b.description)}</p>` : ''}
        ${facts.length ? `<ul class="profile-bot-specs">${facts.map(f => `<li><span class="chip profile-spec-item">${escapeHTML(f)}</span></li>`).join('')}</ul>` : ''}
        <button class="quiet-button profile-settings-btn" type="button" id="profile-open-settings" data-action="open-settings">Cài đặt bot</button>
      </aside>
      <div class="profile-bot-body">
        <div class="profile-twin-head">
          <code class="profile-file-badge">TWIN.md</code>
          <span class="profile-readonly">Chỉ đọc — bot tự cập nhật qua chat</span>
        </div>
        ${b.persona.trim()
          ? `<article class="markdown-body profile-content">${contentHtml(b.persona)}</article>`
          : '<p class="empty-note"><strong>Chưa có persona.</strong>TWIN.md của bot này không có phần thân.</p>'}
      </div>
    </div>
  </section>`;
}

function renderUnknownBot(id) {
  el.root.innerHTML = `<section class="paper-card profile-bot">
    <div class="profile-bot-bar">
      <button class="quiet-button profile-back-shared" type="button" data-action="show-shared">← Hồ sơ chung</button>
    </div>
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

function selectDoc(name) {
  if (!name || name === state.doc) return;
  if (!state.files.some(f => f.name === name)) return;
  if (!confirmDiscardEdit()) return;
  state.editing = ''; state.editBase = ''; state.editDraft = '';
  state.sectionEdit = null;
  state.doc = name;
  renderContent();
  ctx.updateRoute({ search: '', hash: '#' + encodeURIComponent(name) });
  routeKey = ctx.route.search + ctx.route.hash;
}

function showShared(anchor = '') {
  if (!confirmDiscardEdit()) return false;
  bootTouched = true;
  state.editing = ''; state.editBase = ''; state.editDraft = '';
  state.sectionEdit = null;
  state.tab = '';
  if (anchor) {
    const fileName = anchor.replace(/^file-/, '');
    if (state.files.some(f => f.name === fileName)) state.doc = fileName;
  }
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
  return true;
}

function showTab(id, anchor = '') {
  const b = getBot(id);
  if (!b) return false;
  if (!confirmDiscardEdit()) return false;
  bootTouched = true;
  state.editing = ''; state.editBase = ''; state.editDraft = '';
  state.sectionEdit = null;
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
  return true;
}

function fixSidebarLabels() {
  if (!ctx.isActive()) return;
  for (const btn of [...ctx.sidebar.querySelectorAll('[data-action="select"]')]) {
    const b = getBot(btn.dataset.botId);
    if (b) btn.setAttribute('aria-label', `Hồ sơ của ${b.name}`);
  }
}
new MutationObserver(() => fixSidebarLabels())
  .observe(ctx.sidebar, { childList: true, subtree: true });

function selectBot(id) {
  if (!id) { showShared(); return; }
  if (getBot(id)) showTab(id);
}
function loadConversation(id, botId) { if (botId) selectBot(botId); }

function openBotSettings() {
  if (typeof openSettingsDialog === 'function') openSettingsDialog();
  else document.querySelector('#open-settings')?.click();
}

async function loadFiles() {
  setStatus(el.status, 'Đang đọc hồ sơ…');
  try {
    const stats = await getJson('/api/memory/stats');
    state.files = selectCanonical(stats.files);
    if (state.files.length && !state.files.some(f => f.name === state.doc)) state.doc = state.files[0].name;
    setStatus(el.status, '');
    if (!state.tab || (!getBot(state.tab) && rosterReady)) renderContent();
    return true;
  } catch (err) {
    setStatus(el.status, 'Không tải được hồ sơ: ' + err.message, 's-error');
    return false;
  }
}

async function reloadProfile() {
  if (!confirmDiscardEdit()) return;
  state.editing = ''; state.editBase = ''; state.editDraft = '';
  state.sectionEdit = null;
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
  $$('#edit-form button, #edit-form textarea, #section-edit-form button, #section-edit-form input, #section-edit-form textarea, [data-edit], [data-doc], [data-edit-mode], [data-edit-section], [data-add-section]', el.root)
    .forEach(c => { c.disabled = busy; });
}

function openEdit(name, opener) {
  const file = state.files.find(f => f.name === name);
  if (!file) return false;
  if (((state.editing && state.editing !== name) || state.sectionEdit) && !confirmDiscardEdit()) return false;
  state.sectionEdit = null;
  state.opener = opener;
  state.doc = file.name;
  state.editing = file.name;
  state.editBase = file.content;
  state.editDraft = file.content;
  state.editMode = defaultEditMode();
  setStatus(el.status, '');
  renderContent();
  $('#edit-content')?.focus();
  return true;
}

function openSectionEdit(fileName, index, opener) {
  const file = state.files.find(f => f.name === fileName);
  if (!file) return false;
  const same = state.sectionEdit && state.sectionEdit.file === fileName && state.sectionEdit.index === index;
  if (!same && (state.editing || state.sectionEdit) && !confirmDiscardEdit()) return false;
  state.editing = ''; state.editBase = ''; state.editDraft = '';
  state.opener = opener;
  state.doc = file.name;
  let initTitle = '';
  let initBody = '';
  if (index >= 0) {
    const sec = splitSections(file.content).sections[index];
    if (!sec) return false;
    initTitle = sec.title;
    initBody = sec.editBody;
  }
  state.sectionEdit = {
    file: file.name, index,
    baseTitle: initTitle, baseBody: initBody,
    title: initTitle, body: initBody,
  };
  setStatus(el.status, '');
  renderContent();
  (index === -1 ? $('#section-edit-title') : $('#section-edit-body'))?.focus();
  return true;
}

function closeSectionEdit({ force = false, restoreFocus = true } = {}) {
  if (!state.sectionEdit) return false;
  if (!force && !confirmDiscardEdit()) return false;
  const { file, index } = state.sectionEdit;
  state.sectionEdit = null;
  renderContent();
  if (restoreFocus) {
    const target = index >= 0
      ? el.root.querySelector(`[data-edit-section="${index}"]`)
      : el.root.querySelector(`[data-add-section="${file}"]`);
    (target || state.opener)?.focus?.();
  }
  return true;
}

async function submitSectionEdit(e) {
  e?.preventDefault?.();
  if (state.busy || !state.sectionEdit) return false;
  const se = state.sectionEdit;
  const file = state.files.find(f => f.name === se.file);
  if (!file) return false;
  const title = ($('#section-edit-title')?.value ?? se.title).trim();
  const body = $('#section-edit-body')?.value ?? se.body;
  se.title = title;
  se.body = body;
  if (!title) {
    setStatus($('#section-edit-status'), 'Vui lòng nhập tiêu đề mục.', 's-error');
    $('#section-edit-title')?.focus();
    return false;
  }
  const content = mergeSection(file.content, se.index, title, body);
  const savedIndex = se.index >= 0 ? se.index : splitSections(content).sections.length - 1;
  setBusy(true);
  setStatus($('#section-edit-status'), 'Đang lưu…');
  let focusAfterSave = null;
  try {
    await postJson('/api/memory/canonical', { name: file.name, content });
    file.content = content;
    state.sectionEdit = null;
    const reloaded = await loadFiles();
    if (!reloaded) renderContent();
    const nextOpener = [...el.root.querySelectorAll('[data-edit-section]')]
      .find(btn => Number(btn.dataset.editSection) === savedIndex);
    if (nextOpener) { state.opener = nextOpener; focusAfterSave = nextOpener; }
    setStatus(
      el.status,
      reloaded ? `Đã lưu ${file.name}.` : `Đã lưu ${file.name}, nhưng chưa tải lại được hồ sơ.`,
      reloaded ? 's-ok' : 's-error',
    );
    return true;
  } catch (err) {
    setStatus($('#section-edit-status'), 'Không lưu được mục: ' + err.message, 's-error');
    return false;
  } finally {
    setBusy(false);
    focusAfterSave?.focus();
  }
}

function closeEdit({ force = false, restoreFocus = true } = {}) {
  if (!state.editing) return false;
  if (!force && !confirmDiscardEdit()) return false;
  const edited = state.editing;
  state.editing = ''; state.editBase = ''; state.editDraft = '';
  renderContent();
  if (restoreFocus) {
    const target = el.root.querySelector(`[data-edit="${edited}"]`) || state.opener;
    target?.focus?.();
  }
  return true;
}

function setEditMode(mode) {
  if (!['write', 'split', 'preview'].includes(mode)) return;
  state.editMode = mode;
  const form = $('#edit-form');
  if (form) form.dataset.mode = mode;
  $$('[data-edit-mode]', el.root).forEach(btn => btn.setAttribute('aria-pressed', String(btn.dataset.editMode === mode)));
  if (mode !== 'write') paintEditPreview();
}

async function submitEdit(e) {
  e?.preventDefault?.();
  if (state.busy) return false;
  const name = $('#edit-name')?.value || state.editing;
  const content = $('#edit-content')?.value ?? state.editDraft;
  state.editDraft = content;
  if (!content.trim() && !confirm(`Xoá trắng nội dung “${name}”?`)) return false;
  setBusy(true);
  setStatus($('#edit-status'), 'Đang lưu…');
  let focusAfterSave = null;
  try {
    await postJson('/api/memory/canonical', { name, content });
    const targetFile = state.files.find(f => f.name === name);
    if (targetFile) targetFile.content = content;
    state.editing = ''; state.editBase = ''; state.editDraft = '';
    const reloaded = await loadFiles();
    if (!reloaded) renderContent();
    const nextOpener = [...el.root.querySelectorAll('[data-edit]')].find(button => button.dataset.edit === name);
    if (nextOpener) { state.opener = nextOpener; focusAfterSave = nextOpener; }
    setStatus(
      el.status,
      reloaded ? `Đã lưu ${name}.` : `Đã lưu ${name}, nhưng chưa tải lại được hồ sơ.`,
      reloaded ? 's-ok' : 's-error',
    );
    return true;
  } catch (err) {
    setStatus($('#edit-status'), 'Không lưu được file: ' + err.message, 's-error');
    return false;
  } finally {
    setBusy(false);
    focusAfterSave?.focus();
  }
}

window.addEventListener('thyca:bot-settings-saved', () => {
  if (state.tab && getBot(state.tab)) renderContent();
});
el.reload.addEventListener('click', () => reloadProfile());
el.shared.onclick = () => showShared();
el.root.addEventListener('click', e => {
  const docBtn = e.target.closest('[data-doc]');
  if (docBtn) { selectDoc(docBtn.dataset.doc); return; }
  const modeBtn = e.target.closest('[data-edit-mode]');
  if (modeBtn) { setEditMode(modeBtn.dataset.editMode); return; }
  const addSecBtn = e.target.closest('[data-add-section]');
  if (addSecBtn) { openSectionEdit(addSecBtn.dataset.addSection, -1, addSecBtn); return; }
  const editSecBtn = e.target.closest('[data-edit-section]');
  if (editSecBtn) { openSectionEdit(state.doc, Number(editSecBtn.dataset.editSection), editSecBtn); return; }
  const editBtn = e.target.closest('[data-edit]');
  if (editBtn) { openEdit(editBtn.dataset.edit, editBtn); return; }
  const actionBtn = e.target.closest('[data-action]');
  if (actionBtn?.dataset.action === 'show-shared') { showShared(); return; }
  if (actionBtn?.dataset.action === 'open-settings') { openBotSettings(); }
});

function applyRoute(nextTab = state.tab, hash = decodeHash()) {
  if (nextTab && getBot(nextTab)) return showTab(nextTab);
  if (nextTab) {
    if (!confirmDiscardEdit()) return false;
    state.editing = ''; state.editBase = ''; state.editDraft = '';
    state.sectionEdit = null;
    state.tab = nextTab;
    selectedBot = ''; ctx.highlightBot(''); ctx.renderTeam(); fixSidebarLabels();
    el.shared.hidden = false; el.title.textContent = 'Không tìm thấy bot';
    renderContent();
    routeKey = ctx.route.search + ctx.route.hash;
    return true;
  }
  if (state.files.some(f => f.name === hash)) return showShared('file-' + hash);
  return showShared();
}
let routeKey = '';

function handleRoute(route) {
  const key = route.search + route.hash;
  if (key === routeKey) return;
  const nextTab = new URLSearchParams(route.search).get('bot') || '';
  const nextHash = decodeHashValue(route.hash);
  if (applyRoute(nextTab, nextHash) === false) {
    ctx.updateRoute({
      search: state.tab ? '?bot=' + encodeURIComponent(state.tab) : '',
      hash: !state.tab && state.doc ? '#' + encodeURIComponent(state.doc) : '',
    });
    routeKey = ctx.route.search + ctx.route.hash;
    return;
  }
  routeKey = ctx.route.search + ctx.route.hash;
}

async function boot() {
  const initialTab = new URLSearchParams(ctx.route.search).get('bot') || '';
  state.tab = initialTab;
  await loadFiles();
  try { await ctx.shellReady; }
  catch (err) { toast('Không tải được đội bot: ' + (err.message || '')); }
  rosterReady = true;
  routeKey = ctx.route.search + ctx.route.hash;
  applyRoute(initialTab, decodeHash());
}
  const ready = boot();
  return { ready, selectBot, loadConversation, sidebarLabel(_id, name) { return `Hồ sơ của ${name}`; }, onRoute(route) {
    handleRoute(route);
  } };
} });
