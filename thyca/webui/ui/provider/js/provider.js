ThycaRouter.register('provider', { title: 'Provider', stylesheet: '/ui/provider/providercss/provider.css', mount(ctx) {
  const root = ctx.root;
  const $ = (selector, base = root) => selector === '.workspace' ? root : base.querySelector(selector);
  const $$ = (selector, base = root) => [...base.querySelectorAll(selector)];
  let selectedBot = '';

const PROVIDER_ID_RE = /^[A-Za-z0-9_-]+$/;
const FALLBACK_LIMITS = { loopMax: [1, 200], hotTailKB: [1, 64], contextTokens: [1000, 2000000], maxTokens: [1, 2000000] };
const CHOOSE_MODEL = '__choose__';
const ADVANCED_FIELDS = new Set(['#m-in', '#m-cache', '#m-out', '#m-context', '#m-max']);
const OFFICIAL_URLS = {
  openai: 'https://api.openai.com/v1',
  google_gemini: 'https://generativelanguage.googleapis.com/v1beta',
  anthropic_messages: 'https://api.anthropic.com/v1',
};
const OFFICIAL_URL_SET = new Set(Object.values(OFFICIAL_URLS));
const WIRE_API_MAP = {
  openai: 'openai_responses',
  google_gemini: 'google_gemini',
  anthropic_messages: 'anthropic_messages',
  openai_chat: 'openai_chat',
  openai_responses: 'openai_responses',
};
const state = {
  schema: null, values: null, meta: {}, loading: true, saving: false, reloading: false,
  editingProvider: '', providerBaseline: null, modelBaseline: null, editorBaseline: null, cleanStatus: '',
  connectionFingerprint: null, verifying: false, verifyGeneration: 0,
  connectionRevision: 0,
  activeVerify: null, discovery: null, discoveryStatus: '',
  selectedModel: '', selectedModelFromDiscovery: false,
};
let externalConfigDirty = false;
let externalSyncInFlight = false;

const workspace = $('.workspace');
document.querySelector('.notebook-shell').setAttribute('aria-label', 'Thyca — kết nối AI');
workspace.setAttribute('aria-label', 'Nội dung kết nối AI');
const providerTemplate = document.querySelector('template#tpl-provider');
workspace.insertAdjacentHTML('beforeend', providerTemplate ? providerTemplate.innerHTML : '');

function setStatus(text, kind = '') {
  const el = $('#page-status'); el.hidden = !text; el.textContent = text || '';
  el.className = 'page-status' + (kind ? ' s-' + kind : '');
}
function setSaveStatus(text, kind = '') {
  const el = $('#save-status'), className = 'save-status' + (kind ? ' s-' + kind : '');
  if (el.textContent === text && el.className === className) return;
  el.textContent = text; el.className = className;
}
function setFormStatus(text, kind = '') {
  const el = $('#editor-status'); el.hidden = !text; el.textContent = text || '';
  el.className = 'form-status' + (kind ? ' s-' + kind : '');
}
function setDiscoveryStatus(text = '', kind = '') {
  state.discoveryStatus = text; const el = $('#discovery-status');
  el.hidden = !text; el.textContent = text || ''; el.className = 'discovery-status' + (kind ? ' s-' + kind : '');
}
class FieldError extends Error { constructor(message, fieldId) { super(message); this.fieldId = fieldId; } }
class CancelOperation extends Error {}
function clearFieldErrors() { $$('[aria-invalid]').forEach(node => node.removeAttribute('aria-invalid')); }
function fail(err, fallback = 'Không thể lưu.') {
  if (err?.fieldId) {
    const field = $(err.fieldId);
    let details = field?.closest('details') || (ADVANCED_FIELDS.has(err.fieldId) ? $('#model-advanced') : null);
    while (details) { details.open = true; details = details.parentElement?.closest('details') || null; }
    field?.setAttribute('aria-invalid', 'true'); field?.focus();
  }
  setFormStatus(err?.message || fallback, 'error');
}
function providerIds(values = state.values) { return Object.keys(values?.providers || {}); }
function providerHasStoredKey(id, meta = state.meta) { return Object.hasOwn(meta?.providers || {}, id) && !!meta.providers[id]; }
function schemaField(key) { return (state.schema?.sections || []).flatMap(section => section.fields || []).find(field => field.key === key) || null; }
function effortChoices() { return schemaField('provider.reasoningEffort')?.choices || ['low', 'medium', 'high', 'xhigh', 'max']; }
function effortDefault() { return schemaField('provider.reasoningEffort')?.default || 'high'; }
function checkedEfforts() {
  const box = $('#m-efforts');
  if (!box) return [];
  return [...box.querySelectorAll('input:checked')].map(input => input.value);
}
function effortChecksKey() {
  const box = $('#m-efforts');
  if (!box) return '';
  return [...box.querySelectorAll('input')].map(node => (node.checked ? '1' : '0') + node.value).join('|');
}
function effortLevelsForChecks(model) {
  const base = [...effortChoices()];
  const saved = Array.isArray(model?.reasoningEfforts) ? model.reasoningEfforts.filter(Boolean) : [];
  const extras = [...new Set([...saved, ...(model?.reasoningEffort ? [model.reasoningEffort] : [])])].filter(level => !base.includes(level));
  return [...base, ...extras];
}
function paintEffortChips(box) {
  for (const label of box.querySelectorAll('label.effort-chip')) {
    const input = label.querySelector('input');
    const on = !!(input && input.checked);
    label.setAttribute('data-active', on ? 'true' : 'false');
  }
}
function renderEffortChecks(model) {
  const box = $('#m-efforts'); if (!box) return;
  const saved = Array.isArray(model?.reasoningEfforts) ? model.reasoningEfforts.filter(Boolean) : [];
  const initial = saved.length ? saved : [...effortChoices()];
  box.replaceChildren(...effortLevelsForChecks(model).map(level => {
    const label = document.createElement('label');
    label.className = 'effort-chip';
    label.setAttribute('data-level', level);
    label.setAttribute('data-active', initial.includes(level) ? 'true' : 'false');
    const input = Object.assign(document.createElement('input'), { type: 'checkbox', value: level });
    input.className = 'effort-check';
    if (initial.includes(level)) input.checked = true;
    input.setAttribute('data-effort', level);
    const name = document.createElement('span');
    name.className = 'effort-name';
    name.textContent = level;
    label.append(input, name);
    return label;
  }));
}
function limitRange(key) { const field = schemaField('limits.' + key); return field ? [field.min, field.max] : FALLBACK_LIMITS[key]; }
const fmtMoney = value => '$' + (Number.isFinite(value) ? String(value) : '0');
function cloneValues(values) { return JSON.parse(JSON.stringify(values || {})); }
function maskedProjection(values) {
  const safe = cloneValues(values);
  for (const provider of Object.values(safe.providers || {})) if (provider && typeof provider === 'object') provider.apiKey = '';
  return safe;
}
function modelProviderId(values, id) {
  const models = values?.models || {};
  const model = Object.hasOwn(models, id) ? models[id] : null;
  return model?.provider && Object.hasOwn(values?.providers || {}, model.provider) ? model.provider : values?.defaultProvider || '';
}
function modelsOf(pid, values = state.values) { return Object.keys(values?.models || {}).filter(id => modelProviderId(values, id) === pid).sort(); }
function selectedModelId() {
  const option = $('#m-picker').value;
  return option.startsWith('model:') ? option.slice(6) : '';
}
function modelIsSaved(id = selectedModelId(), values = state.values) { return !!id && Object.hasOwn(values?.models || {}, id); }
function providerSnapshot() { return ['pv-id', 'pv-base-url', 'pv-api', 'pv-key'].map(id => $( '#' + id).value); }
function modelSnapshot() {
  const snapshot = [$('#m-picker').value, effortChecksKey(), $('#m-context').value, $('#m-max').value, $('#m-in').value, $('#m-cache').value, $('#m-out').value];
  return snapshot;
}
function providerIsDirty() { return !!state.providerBaseline && JSON.stringify(providerSnapshot()) !== JSON.stringify(state.providerBaseline); }
function modelIsDirty() { return !!state.modelBaseline && JSON.stringify(modelSnapshot()) !== JSON.stringify(state.modelBaseline); }
function editorSnapshot() { return [providerSnapshot(), modelSnapshot()]; }
function editorIsDirty() { return !!state.editorBaseline && JSON.stringify(editorSnapshot()) !== JSON.stringify(state.editorBaseline); }
function syncDirtyStatus() {
  if (!state.values || state.loading || state.saving || state.reloading) return;
  const dirty = editorIsDirty();
  setSaveStatus(dirty ? 'Chưa lưu thay đổi' : state.cleanStatus, dirty ? 'warn' : '');
}
function providerFingerprint(values, meta) { return JSON.stringify({ providers: values?.providers || {}, defaultProvider: values?.defaultProvider || '', storedKeys: meta?.providers || {} }); }
function safeMeta(values, oldMeta, keyPresence = {}) {
  const providers = Object.create(null);
  for (const id of Object.keys(values.providers || {})) providers[id] = !!keyPresence[id] || providerHasStoredKey(id, oldMeta) || !!values.providers[id]?.apiKey;
  return { ...oldMeta, providers, hasApiKey: Object.hasOwn(providers, values.defaultProvider) && !!providers[values.defaultProvider] };
}
function applyConfig(payload) {
  const values = payload.values || {}; values.providers ||= {}; values.models ||= {};
  const meta = payload.meta || {}; const fingerprint = providerFingerprint(values, meta);
  if (state.connectionFingerprint !== null && state.connectionFingerprint !== fingerprint) {
    state.connectionRevision++; invalidateDiscovery('Kết nối đã thay đổi. Tải lại danh sách model.');
  }
  state.connectionFingerprint = fingerprint; state.schema = payload.schema || {}; state.values = values; state.meta = meta;
  state.loading = false; renderAll(); refreshBusyState();
}
async function refreshConfig() { const payload = await getJson('/api/config'); applyConfig(payload); return payload; }

async function syncExternalConfig() {
  if (externalSyncInFlight || state.loading || state.saving || state.reloading || !state.values || editorIsDirty()) return;
  externalSyncInFlight = true;
  externalConfigDirty = false;
  try {
    const activeProvider = state.editingProvider;
    const activeModel = state.selectedModel;
    const savedDiscovery = state.discovery;
    await refreshConfig();
    if (!state.values || editorIsDirty()) return;
    if (!activeProvider) {
      fillConnection('');
      if (savedDiscovery && savedDiscovery.connectionRevision === state.connectionRevision) {
        state.discovery = savedDiscovery;
        renderModelPicker();
      }
      if (activeModel && state.discovery && state.discovery.models.includes(activeModel)) {
        chooseModel(activeModel, { confirmDiscard: false });
      }
      state.providerBaseline = providerSnapshot();
      state.modelBaseline = modelFieldsSnapshot();
      state.editorBaseline = editorSnapshot();
      syncDirtyStatus();
    } else if (Object.hasOwn(state.values.providers || {}, activeProvider)) {
      fillConnection(activeProvider);
      if (savedDiscovery && savedDiscovery.connectionRevision === state.connectionRevision) {
        state.discovery = savedDiscovery;
        renderModelPicker();
      }
      if (!activeModel) {
        setModelFields('');
      } else if ((Object.hasOwn(state.values.models || {}, activeModel) && modelProviderId(state.values, activeModel) === activeProvider)
          || (state.discovery && state.discovery.models.includes(activeModel))) {
        chooseModel(activeModel, { confirmDiscard: false });
      }
      state.providerBaseline = providerSnapshot();
      state.modelBaseline = modelFieldsSnapshot();
      state.editorBaseline = editorSnapshot();
      syncDirtyStatus();
    } else {
      fillConnection(state.values.defaultProvider || Object.keys(state.values.providers || {})[0] || '', { initial: true });
    }
  } catch {
    externalConfigDirty = true;
  } finally {
    externalSyncInFlight = false;
  }
}

function fillOptions(select, options, current) {
  if (!select) return;
  select.replaceChildren(...options.map(([value, text, attrs = {}]) => {
    const option = Object.assign(document.createElement('option'), { value, textContent: text });
    for (const [key, value] of Object.entries(attrs)) option.setAttribute(key, value);
    return option;
  }));
  if (options.some(([value]) => value === current)) select.value = current;
}
function defaultOwnerId() {
  const id = state.values?.defaultModel || '';
  return id ? modelProviderId(state.values, id) : (state.values?.defaultProvider || '');
}
function renderConnectionTabs() {
  const bar = $('#connection-tabs'); if (!bar) return;
  const defOwner = defaultOwnerId();
  const busy = state.loading || state.saving || state.reloading;
  bar.replaceChildren(...providerIds().sort().map(id => {
    const active = state.editingProvider === id;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'conn-tab';
    btn.setAttribute('role', 'tab');
    btn.dataset.provider = id;
    btn.dataset.active = active ? 'true' : 'false';
    btn.setAttribute('aria-selected', active ? 'true' : 'false');
    btn.disabled = busy;
    if (id === defOwner) {
      const dot = document.createElement('span');
      dot.className = 'conn-tab-dot';
      dot.setAttribute('aria-hidden', 'true');
      btn.appendChild(dot);
    }
    const name = document.createElement('span');
    name.className = 'conn-tab-name';
    name.textContent = id;
    btn.appendChild(name);
    btn.onclick = () => { openExistingConnection(id); };
    return btn;
  }));
  const newBtn = $('#new-connection');
  if (newBtn) {
    const isNew = !state.editingProvider;
    newBtn.dataset.active = isNew ? 'true' : 'false';
    newBtn.setAttribute('aria-selected', isNew ? 'true' : 'false');
  }
}
function normalizeBaseUrl(raw) { return String(raw || '').trim().replace(/\/+$/, ''); }
function selectValueForEntry(entry) {
  if (!entry) return 'openai_chat';
  if (entry.api === 'google_gemini') return 'google_gemini';
  if (entry.api === 'anthropic_messages') return 'anthropic_messages';
  if (entry.api === 'openai_responses') {
    return normalizeBaseUrl(entry.baseUrl) === OFFICIAL_URLS.openai ? 'openai' : 'openai_responses';
  }
  return 'openai_chat';
}
function wireApiForSelect(value) { return Object.hasOwn(WIRE_API_MAP, value) ? WIRE_API_MAP[value] : 'openai_chat'; }
function syncUrlPlaceholder(selectValue) {
  const input = $('#pv-base-url');
  if (input) input.placeholder = OFFICIAL_URLS[selectValue] || 'https://api.example.com/v1';
}
function renderProviderSelects() {
  const apiSelect = $('#pv-api');
  const providers = state.values?.providers || {};
  const entry = state.editingProvider && Object.hasOwn(providers, state.editingProvider) ? providers[state.editingProvider] : null;
  if (entry) {
    if (!apiSelect.dataset.edited) apiSelect.value = selectValueForEntry(entry);
  } else if (!apiSelect.dataset.edited) apiSelect.value = 'openai_chat';
  syncUrlPlaceholder(apiSelect.value);
}
function renderModelPicker() {
  const select = $('#m-picker'); if (!select || !state.values) return;
  const current = select.value || CHOOSE_MODEL;
  const pid = ($('#pv-id').value || state.editingProvider).trim();
  const options = [[CHOOSE_MODEL, 'Chọn model…']];
  const byId = new Map();
  for (const id of modelsOf(pid)) byId.set(id, { label: id, disabled: false });
  for (const id of state.discovery?.models || []) {
    const owner = modelIsSaved(id) ? modelProviderId(state.values, id) : '';
    if (owner && owner !== pid) byId.set(id, { label: `${id} — đã thuộc ${owner}`, disabled: true });
    else byId.set(id, { label: id, disabled: false });
  }
  for (const [id, data] of [...byId.entries()].sort(([a], [b]) => a.localeCompare(b))) {
    options.push([`model:${id}`, data.label, data.disabled ? { disabled: 'disabled', 'data-owner': modelProviderId(state.values, id), 'data-model': id } : { 'data-model': id }]);
  }
  if (state.selectedModel && !byId.has(state.selectedModel) && state.selectedModelFromDiscovery) {
    options.push([`model:${state.selectedModel}`, `${state.selectedModel} — danh sách cũ, tải lại`, { disabled: 'disabled', 'data-stale': 'true', 'data-model': state.selectedModel }]);
  }
  const encodedCurrent = current.startsWith('model:') ? current : (state.selectedModel ? `model:${state.selectedModel}` : current);
  fillOptions(select, options, options.some(([value]) => value === encodedCurrent) ? encodedCurrent : CHOOSE_MODEL);
}
function renderCurrentDefault() {
  const id = state.values?.defaultModel || '';
  const provider = id ? modelProviderId(state.values, id) : state.values?.defaultProvider || '';
  const el = $('#current-default');
  el.textContent = id ? `Hiện tại: ${id} · ${provider || 'kết nối mặc định'}` : 'Hiện tại: Chưa chọn model mặc định.';
  el.dataset.hasDefault = id ? 'true' : 'false';
}
function renderEditorActions() {
  const pid = state.editingProvider;
  const modelId = selectedModelId();
  const saved = modelIsSaved(modelId);
  $('#delete-provider').hidden = !pid;
  $('#delete-model').hidden = !saved;
  const mode = $('#model-editor-mode');
  if (mode) {
    mode.hidden = !modelId;
    mode.textContent = modelId ? (saved ? 'Đang sửa' : 'Model mới') : '';
    mode.dataset.mode = saved ? 'edit' : 'new';
  }
  const idField = $('#pv-id-field');
  if (idField) idField.hidden = !!pid;
  $('#pv-id').readOnly = !!pid;
  $('#pv-id').title = pid ? 'Tên kết nối đã lưu không sửa trực tiếp; dùng “Thêm kết nối” nếu muốn tạo tên khác.' : '';
  $('#pv-key-note').textContent = pid && providerHasStoredKey(pid) ? 'Đã lưu key' : '';
  $('#pv-key').placeholder = pid && providerHasStoredKey(pid) ? 'Để trống để giữ key' : 'Nhập API key';
}
function formatTokenCount(value) {
  if (!Number.isFinite(value) || value <= 0) return '';
  return Number.isInteger(value) && value >= 1000 && value % 1000 === 0 ? `${value / 1000}k` : String(value);
}
function savedModelMeta(id) {
  const model = state.values?.models?.[id] || {};
  const bits = [];
  if (model.input || model.cache || model.output) bits.push(`$${model.input}/${model.cache}/${model.output}`);
  if (Number.isInteger(model.contextTokens)) bits.push(`${formatTokenCount(model.contextTokens)} ctx`);
  if (Number.isInteger(model.maxTokens)) bits.push(`${formatTokenCount(model.maxTokens)} max`);
  if (Array.isArray(model.reasoningEfforts) && model.reasoningEfforts.length) bits.push(model.reasoningEfforts.join(', '));
  return bits.join(' · ');
}
function renderAdvancedSummary() {
  const el = $('#model-advanced-summary'); if (!el) return;
  const pIn = String($('#m-in')?.value ?? '').trim();
  const pCache = String($('#m-cache')?.value ?? '').trim();
  const pOut = String($('#m-out')?.value ?? '').trim();
  const pricePart = (!pIn && !pCache && !pOut)
    ? 'Chưa đặt giá'
    : (pIn && pCache && pOut ? `$${pIn}/$${pCache}/$${pOut}` : 'Giá chưa đủ 3 mức');
  const activeEfforts = checkedEfforts().length;
  const effortPart = activeEfforts ? `${activeEfforts} mức suy luận` : 'Suy luận: kế thừa';
  const ctxNum = Number(String($('#m-context')?.value ?? '').trim());
  const maxNum = Number(String($('#m-max')?.value ?? '').trim());
  const ctxPart = Number.isInteger(ctxNum) && ctxNum > 0 ? formatTokenCount(ctxNum) : 'Kế thừa';
  const maxPart = Number.isInteger(maxNum) && maxNum > 0 ? formatTokenCount(maxNum) : '';
  const limitPart = maxPart ? `${ctxPart} / ${maxPart} max` : ctxPart;
  el.textContent = [pricePart, effortPart, limitPart].join(' · ');
}
function renderSavedModels() {
  const list = $('#saved-model-rows'); if (!list || !state.values) return;
  const note = $('#saved-models-note');
  const pid = state.editingProvider;
  const ids = pid ? modelsOf(pid) : [];
  const def = state.values.defaultModel || '';
  const busy = state.loading || state.saving || state.reloading;
  if (note) note.textContent = pid
    ? (ids.length ? `${ids.length} model cho kết nối “${pid}”.` : `Chưa có model nào cho kết nối “${pid}”. Lưu form để thêm model đầu tiên.`)
    : 'Chọn hoặc tạo kết nối để xem model đã lưu.';
  list.replaceChildren(...ids.map(id => {
    const item = document.createElement('li');
    item.className = 'saved-model-row';
    item.dataset.editing = id === state.selectedModel ? 'true' : 'false';
    const info = document.createElement('div');
    info.className = 'saved-model-info';
    const name = document.createElement('span');
    name.className = 'saved-model-name'; name.textContent = id;
    name.title = id;
    info.appendChild(name);
    const meta = document.createElement('span');
    meta.className = 'saved-model-meta'; meta.textContent = savedModelMeta(id);
    info.appendChild(meta);
    item.appendChild(info);
    const actions = document.createElement('span');
    actions.className = 'saved-model-actions';
    if (id === def) {
      const badge = document.createElement('span');
      badge.className = 'saved-model-default'; badge.textContent = 'Mặc định';
      actions.appendChild(badge);
    } else {
      const use = document.createElement('button');
      use.type = 'button'; use.className = 'quiet-button';
      use.dataset.action = 'default'; use.dataset.model = id;
      use.textContent = 'Đặt mặc định'; use.disabled = busy;
      use.onclick = () => { void setDefaultModel(id); };
      actions.appendChild(use);
    }
    const edit = document.createElement('button');
    edit.type = 'button'; edit.className = 'quiet-button';
    edit.dataset.action = 'edit'; edit.dataset.model = id;
    edit.textContent = 'Sửa'; edit.disabled = busy;
    edit.onclick = () => chooseModel(id);
    actions.appendChild(edit);
    const del = document.createElement('button');
    del.type = 'button'; del.className = 'quiet-button is-danger';
    del.dataset.action = 'delete'; del.dataset.model = id;
    del.textContent = 'Xóa'; del.disabled = busy || id === def;
    if (id === def) del.title = 'Đang là model mặc định. Chọn model khác trước khi xóa.';
    del.onclick = () => { void deleteSavedModel(id); };
    actions.appendChild(del);
    item.appendChild(actions);
    return item;
  }));
}
async function setDefaultModel(id) {
  if (state.saving || state.loading || state.reloading || !state.values) return false;
  if (!id || !modelIsSaved(id)) { setFormStatus('Model chưa được lưu.', 'error'); return false; }
  if (id === state.values.defaultModel) return true;
  if (editorIsDirty()) { setFormStatus('Lưu hoặc hủy thay đổi form trước khi đặt model mặc định.', 'error'); return false; }
  clearFieldErrors();
  return commitValues(fresh => {
    if (!Object.hasOwn(fresh.values?.models || {}, id)) throw new FieldError(`Model “${id}” không còn được lưu. Tải lại rồi chọn lại.`, '#m-picker');
    const values = cloneValues(fresh.values);
    values.defaultModel = id;
    return values;
  }, { note: `Đang dùng ${id}.` });
}
async function deleteSavedModel(id) {
  if (!id || !modelIsSaved(id)) return false;
  if (id === state.values?.defaultModel) { setFormStatus('Đang là model mặc định. Chọn model khác trước.', 'error'); return false; }
  if (editorIsDirty()) { setFormStatus('Lưu hoặc hủy thay đổi form trước khi xóa model.', 'error'); return false; }
  if (!window.confirm(`Xóa model “${id}”?`)) return false;
  return commitValues(fresh => {
    const values = cloneValues(fresh.values);
    if (!Object.hasOwn(values.models || {}, id)) throw new FieldError('Model đã bị xóa.', '#m-picker');
    if (id === values.defaultModel) throw new FieldError('Đang là model mặc định. Chọn model khác trước.', '#m-picker');
    delete values.models[id]; if (values.pricing) delete values.pricing[id]; return values;
  }, { note: `Đã xóa model “${id}”.`, onPosted: () => { if (selectedModelId() === id) setModelFields(''); } });
}
function renderAll() {
  renderConnectionTabs(); renderProviderSelects(); renderModelPicker(); renderCurrentDefault(); renderEditorActions(); renderSavedModels(); renderAdvancedSummary();
}
function refreshBusyState() {
  $('#editor-controls').disabled = state.loading || !state.values || state.saving || state.reloading;
  $('#v-submit').disabled = state.verifying || state.loading || !state.values || state.saving || state.reloading;
  $('#save-use').disabled = state.loading || !state.values || state.saving || state.reloading;
}

function parseBaseUrl(raw) {
  const value = String(raw || '').trim().replace(/\/+$/, ''); let url;
  try { url = new URL(value); } catch { throw new FieldError('URL API phải hợp lệ.', '#pv-base-url'); }
  if (!['http:', 'https:'].includes(url.protocol)) throw new FieldError('URL API phải bắt đầu bằng http:// hoặc https://.', '#pv-base-url');
  return value;
}
function parseIntOrNull(input, label, range) {
  const raw = String(input.value ?? '').trim(); if (!raw) return null;
  const value = Number(raw); const [min, max] = range;
  if (!Number.isInteger(value) || value < min || value > max) throw new FieldError(`${label} phải là số nguyên ${min}–${max}.`, '#' + input.id);
  return value;
}
function parsePrices() {
  const fields = [$('#m-in'), $('#m-cache'), $('#m-out')]; const raws = fields.map(field => String(field.value ?? '').trim());
  if (raws.every(value => value === '')) return null;
  const empty = raws.findIndex(value => value === '');
  if (empty >= 0) throw new FieldError('Điền đủ cả ba giá Input, Cache và Output; hoặc để trống cả ba.', '#' + fields[empty].id);
  const values = raws.map(Number); const bad = values.findIndex(value => !Number.isFinite(value) || value < 0);
  if (bad >= 0) throw new FieldError('Giá phải là số lớn hơn hoặc bằng 0.', '#' + fields[bad].id);
  return { input: values[0], cache: values[1], output: values[2] };
}
function modelFieldsSnapshot() { return modelSnapshot(); }
function setModelFields(id) {
  state.selectedModel = id; state.selectedModelFromDiscovery = !!(state.discovery?.models.includes(id));
  $('#m-picker').value = id ? `model:${id}` : CHOOSE_MODEL;
  const models = state.values?.models || {};
  const model = id && Object.hasOwn(models, id) ? models[id] : null;
  $('#m-in').value = model && (Number(model.input) > 0 || Number(model.cache) > 0 || Number(model.output) > 0) ? model.input : '';
  $('#m-cache').value = model && (Number(model.input) > 0 || Number(model.cache) > 0 || Number(model.output) > 0) ? model.cache : '';
  $('#m-out').value = model && (Number(model.input) > 0 || Number(model.cache) > 0 || Number(model.output) > 0) ? model.output : '';
  if (model && ![model.input, model.cache, model.output].some(value => Number.isFinite(value) && value > 0)) {
    const pricing = state.values.pricing || {};
    const legacy = Object.hasOwn(pricing, id) ? pricing[id] : null;
    if (legacy) { $('#m-in').value = legacy.input ?? ''; $('#m-cache').value = legacy.cache ?? ''; $('#m-out').value = legacy.output ?? ''; }
  }
  renderEffortChecks(model);
  const contextFallback = schemaField('limits.contextTokens')?.default ?? 272000;
  $('#m-context').value = Number.isInteger(model?.contextTokens) ? model.contextTokens : contextFallback;
  $('#m-max').value = Number.isInteger(model?.maxTokens) ? model.maxTokens : '';
  const legacyUrl = model?.baseUrl || '';
  $('#legacy-url-warning').hidden = !legacyUrl;
  $('#legacy-url-warning').textContent = legacyUrl ? `Model này đang giữ URL riêng ${legacyUrl}; khi lưu sẽ không bị thay đổi.` : '';
  $('#ownership-warning').hidden = true; $('#ownership-warning').textContent = '';
  renderModelPicker(); renderProviderSelects();
  state.modelBaseline = modelFieldsSnapshot();
  renderEditorActions();
  renderSavedModels();
  renderAdvancedSummary();
}
function chooseModel(id, { confirmDiscard = true } = {}) {
  if (id && state.discovery?.models.includes(id) && (!state.discovery || state.discovery.connectionRevision !== state.connectionRevision)) {
    setDiscoveryStatus('Danh sách model đã cũ. Tải lại trước khi chọn.', 'error'); return false;
  }
  if (id && modelIsSaved(id) && modelProviderId(state.values, id) !== ($('#pv-id').value || state.editingProvider)) {
    setDiscoveryStatus(`Model đã thuộc kết nối “${modelProviderId(state.values, id)}”. Chọn kết nối đó để sửa.`, 'error'); return false;
  }
  if (id && id === state.selectedModel) return true;
  if (confirmDiscard && modelIsDirty() && !window.confirm('Đổi model sẽ bỏ các thay đổi model chưa lưu. Tiếp tục?')) {
    renderModelPicker(); $('#m-picker').value = state.selectedModel ? `model:${state.selectedModel}` : CHOOSE_MODEL; return false;
  }
  state.selectedModel = id || '';
  setModelFields(id || '');
  return true;
}
function fillConnection(id, { initial = false } = {}) {
  state.editingProvider = id || '';
  const providers = state.values?.providers || {};
  const provider = id && Object.hasOwn(providers, id) ? providers[id] : null;
  $('#pv-id').value = id || ''; $('#pv-base-url').value = provider?.baseUrl || ''; $('#pv-key').value = '';
  const apiValue = selectValueForEntry(provider);
  $('#pv-api').value = apiValue;
  $('#pv-api').dataset.edited = '';
  syncUrlPlaceholder(apiValue);
  state.discovery = null; state.selectedModel = ''; state.selectedModelFromDiscovery = false;
  const ids = modelsOf(id);
  const preferred = initial && state.values?.defaultModel && ids.includes(state.values.defaultModel)
    ? state.values.defaultModel : (ids.includes(state.values?.defaultModel) ? state.values.defaultModel : '');
  setModelFields(preferred);
  state.providerBaseline = providerSnapshot(); state.modelBaseline = modelFieldsSnapshot();
  renderAll(); state.editorBaseline = editorSnapshot(); syncDirtyStatus(); setFormStatus('');
  setDiscoveryStatus('');
}
function openNewConnection() {
  if (editorIsDirty() && !window.confirm('Mở kết nối mới sẽ bỏ các thay đổi chưa lưu. Tiếp tục?')) return false;
  fillConnection(''); $('#pv-id').focus(); return true;
}
function openExistingConnection(id) {
  if (!Object.hasOwn(state.values?.providers || {}, id)) return false;
  if (id === state.editingProvider) return true;
  if (editorIsDirty() && !window.confirm('Mở kết nối khác sẽ bỏ các thay đổi chưa lưu. Tiếp tục?')) return false;
  fillConnection(id); return true;
}
function cancelEditor() {
  if (state.editingProvider && Object.hasOwn(state.values?.providers || {}, state.editingProvider)) fillConnection(state.editingProvider);
  else fillConnection('');
  renderAll();
}
function invalidateDiscovery(message = 'URL/key đã đổi. Tải lại model.') {
  state.verifyGeneration++; state.discovery = null;
  if (state.activeVerify) { state.activeVerify = null; state.verifying = false; }
  renderModelPicker(); setDiscoveryStatus(message); refreshBusyState();
}
function sourceDraft() { return { url: $('#pv-base-url').value, key: $('#pv-key').value.trim(), api: wireApiForSelect($('#pv-api').value), providerId: state.editingProvider }; }
async function verifyQuick() {
  if (state.verifying || state.loading || state.saving || state.reloading || !state.values) return false;
  clearFieldErrors(); setFormStatus('');
  const source = sourceDraft();
  let baseUrl; try { baseUrl = parseBaseUrl(source.url); } catch (error) { fail(error); return false; }
  const existingId = source.providerId && Object.hasOwn(state.values.providers || {}, source.providerId) ? source.providerId : '';
  if (!source.key && !existingId) {
    setDiscoveryStatus('Nhập API key để tải model.', ''); return false;
  }
  const payload = { baseUrl, api: source.api };
  if (source.key) payload.apiKey = source.key;
  else payload.providerId = existingId;
  const snapshot = { generation: ++state.verifyGeneration, baseUrl, api: source.api, key: source.key, providerId: existingId, connectionRevision: state.connectionRevision };
  state.activeVerify = snapshot; state.verifying = true; refreshBusyState(); setDiscoveryStatus('Đang tải model…');
  try {
    const response = await postJson('/api/onboarding/verify', payload);
    if (state.activeVerify !== snapshot || snapshot.generation !== state.verifyGeneration || snapshot.connectionRevision !== state.connectionRevision || JSON.stringify(sourceDraft()) !== JSON.stringify(source)) return false;
    const models = Array.isArray(response.models) ? response.models.filter(id => typeof id === 'string' && id.length) : [];
    state.discovery = { models, sourceUrl: baseUrl, sourceApi: source.api, providerId: snapshot.providerId, connectionRevision: snapshot.connectionRevision, generation: snapshot.generation };
    renderModelPicker();
    setDiscoveryStatus(models.length ? `Đã tải ${models.length} model.` : 'Không có model nào trên kết nối này.', models.length ? 'ok' : '');
    return true;
  } catch (error) {
    if (state.activeVerify !== snapshot || snapshot.generation !== state.verifyGeneration) return false;
    setDiscoveryStatus(`Không tải được model: ${error.message}`, 'error'); return false;
  } finally {
    if (state.activeVerify === snapshot) { state.activeVerify = null; state.verifying = false; refreshBusyState(); }
  }
}
function draftForSave() {
  const id = $('#pv-id').value.trim();
  if (!id) throw new FieldError('Nhập tên kết nối.', '#pv-id');
  if (!PROVIDER_ID_RE.test(id)) throw new FieldError('Tên kết nối chỉ gồm chữ, số, _ và -.', '#pv-id');
  const baseUrl = parseBaseUrl($('#pv-base-url').value);
  const api = wireApiForSelect($('#pv-api').value);
  const apiKey = $('#pv-key').value.trim();
  const modelId = selectedModelId();
  if (!modelId) {
    if (state.editingProvider && providerIsDirty()) {
      return { id, baseUrl, api, apiKey, modelId: '', provider: id, prices: null, contextTokens: null, maxTokens: null, efforts: [], connectionOnly: true };
    }
    throw new FieldError('Chọn model trong danh sách.', '#m-picker');
  }
  if (state.selectedModelFromDiscovery && (!state.discovery || state.discovery.connectionRevision !== state.connectionRevision || state.discovery.sourceUrl !== baseUrl || state.discovery.sourceApi !== api || state.discovery.providerId !== (state.editingProvider || ''))) {
    throw new FieldError('URL hoặc kết nối đã đổi. Tải lại danh sách model.', '#m-picker');
  }
  const prices = parsePrices();
  const contextTokens = parseIntOrNull($('#m-context'), 'Ngữ cảnh', limitRange('contextTokens'));
  const maxTokens = parseIntOrNull($('#m-max'), 'Output tối đa', limitRange('maxTokens'));
  const provider = id;
  const efforts = checkedEfforts();
  return { id, baseUrl, api, apiKey, modelId, provider, prices, contextTokens, maxTokens, efforts, connectionOnly: false };
}
function mergeEditorDraft(fresh, draft) {
  const values = cloneValues(fresh.values); values.providers ||= {}; values.models ||= {};
  const editing = state.editingProvider;
  if (editing && !Object.hasOwn(values.providers, editing)) throw new FieldError('Kết nối này đã bị xóa. Tải lại rồi chọn kết nối khác.', '#connection-tabs');
  if (!editing && Object.hasOwn(values.providers, draft.id)) throw new FieldError(`Kết nối “${draft.id}” đã có. Chọn kết nối đó để sửa.`, '#pv-id');
  if (editing && draft.id !== editing) throw new FieldError('Không thể đổi tên kết nối trong cùng một thao tác.', '#pv-id');
  const prevProvider = Object.hasOwn(values.providers, draft.id) ? values.providers[draft.id] : {};
  const keptEffort = prevProvider.reasoningEffort || effortDefault();
  values.providers = { ...values.providers, [draft.id]: { apiKeyEnv: 'THYCA_TOKEN', ...prevProvider, baseUrl: draft.baseUrl, api: draft.api, reasoningEffort: keptEffort, apiKey: draft.apiKey } };
  if (!draft.connectionOnly) {
    const previousModel = Object.hasOwn(values.models, draft.modelId) ? values.models[draft.modelId] : null;
    const previousOwner = previousModel ? modelProviderId(values, draft.modelId) : '';
    if (previousModel && previousOwner !== draft.provider) throw new FieldError(`Model này đã thuộc kết nối “${previousOwner}”. Chọn kết nối đó để sửa.`, '#m-picker');
    const entry = { ...(previousModel || {}), provider: draft.provider };
    if (draft.prices) {
      Object.assign(entry, draft.prices);
      values.pricing = { ...(values.pricing || {}), [draft.modelId]: { ...draft.prices } };
    } else {
      delete entry.input; delete entry.cache; delete entry.output;
      if (values.pricing) delete values.pricing[draft.modelId];
    }
    for (const [key, value] of [['contextTokens', draft.contextTokens], ['maxTokens', draft.maxTokens], ['reasoningEfforts', draft.efforts.length ? [...draft.efforts] : null]]) {
      if (value === null || value === '') delete entry[key]; else entry[key] = value;
    }
    delete entry.reasoningEffort;
    values.models = { ...values.models, [draft.modelId]: entry };
  }
  if (!Object.hasOwn(values.providers, values.defaultProvider)) values.defaultProvider = draft.id;
  return values;
}
function configSavedEvent() { window.dispatchEvent(new Event('thyca:config-saved')); }
async function commitValues(buildValues, { note = '', onPosted = null } = {}) {
  if (state.loading || state.saving || state.reloading || !state.values) return false;
  state.saving = true; setSaveStatus('Đang lưu…'); setFormStatus(''); refreshBusyState();
  try {
    const fresh = await getJson('/api/config');
    const next = buildValues(fresh);
    if (!Object.hasOwn(next.providers || {}, next.defaultProvider)) throw new FieldError('Kết nối mặc định không còn tồn tại.', '#connection-tabs');
    const body = fresh.revision ? { ...next, baseRevision: fresh.revision } : next;
    let response;
    try {
      response = await postJson('/api/config', body);
    } catch (error) {
      if (error?.status === 409) {
        setSaveStatus('Chưa lưu', 'error');
        if (error?.data?.code === 'model_in_use') {
          setFormStatus(error.message || 'Không thể xóa model đang được bot sử dụng.', 'error');
        } else {
          setFormStatus('Cấu hình đã thay đổi ở nơi khác. Bản nháp được giữ nguyên — kiểm tra lại rồi lưu lại.', 'error');
          try { await refreshConfig(); } catch {}
        }
        return false;
      }
      throw error;
    }
    const keyPresence = Object.create(null);
    for (const [id, entry] of Object.entries(next.providers || {})) keyPresence[id] = !!entry.apiKey || providerHasStoredKey(id, fresh.meta);
    state.values = maskedProjection(next); state.meta = safeMeta(state.values, fresh.meta || state.meta, keyPresence);
    state.schema = fresh.schema || state.schema; state.connectionFingerprint = providerFingerprint(state.values, state.meta);
    for (const entry of Object.values(next.providers || {})) if (entry && typeof entry === 'object') entry.apiKey = '';
    $('#pv-key').value = '';
    onPosted?.(); configSavedEvent(); renderAll();
    state.providerBaseline = providerSnapshot(); state.modelBaseline = modelFieldsSnapshot(); state.editorBaseline = editorSnapshot();
    state.cleanStatus = 'Đã lưu';
    setSaveStatus('Đã lưu', response.ready === false ? 'warn' : 'ok');
    try {
      await refreshConfig();
      setSaveStatus('Đã lưu', response.ready === false ? 'warn' : 'ok');
      const notReadyText = !state.values?.defaultModel
        ? (note || 'Đã lưu cấu hình. Chọn “Đặt mặc định” ở danh sách bên dưới để bắt đầu trò chuyện.')
        : 'Đã lưu cấu hình. Chưa thể xác nhận model chạy được; kiểm tra API key/kết nối.';
      setFormStatus(response.ready === false ? notReadyText : note, response.ready === false ? 'warn' : 'ok');
    } catch (error) {
      setSaveStatus('Đã lưu', 'warn');
      setFormStatus(`Đã lưu thành công nhưng chưa tải lại được cấu hình: ${error.message}${response.ready === false ? '. Cấu hình chưa sẵn sàng cho lượt chat.' : ''}`, 'warn');
    }
    return true;
  } catch (error) {
    if (error instanceof CancelOperation) return false;
    if (error instanceof FieldError) fail(error);
    else {
      setSaveStatus('Chưa lưu', 'error');
      setFormStatus(`Chưa lưu được: ${error.message}`, 'error');
    }
    return false;
  } finally {
    state.saving = false; refreshBusyState(); renderAll(); syncDirtyStatus();
  }
}
async function submitEditor(event) {
  event?.preventDefault?.();
  if (state.saving || state.loading || state.reloading) return false;
  clearFieldErrors(); setFormStatus('');
  let draft; try { draft = draftForSave(); } catch (error) { fail(error); return false; }
  return commitValues(fresh => mergeEditorDraft(fresh, draft), {
    note: draft.connectionOnly
      ? `Đã lưu kết nối “${draft.id}”.`
      : `Đã lưu ${draft.modelId}. Chọn “Đặt mặc định” ở danh sách bên dưới để dùng model này.`,
    onPosted: () => {
      state.editingProvider = draft.id;
      state.discovery = null;
      if (!draft.connectionOnly) setModelFields(draft.modelId);
    },
  });
}
async function deleteProvider() {
  const id = state.editingProvider;
  if (!id || !window.confirm(`Xóa kết nối “${id}”?`)) return false;
  return commitValues(fresh => {
    const values = cloneValues(fresh.values); const ids = Object.keys(values.providers || {});
    if (!Object.hasOwn(values.providers || {}, id)) throw new FieldError('Kết nối đã bị xóa.', '#connection-tabs');
    if (ids.length <= 1) throw new FieldError('Không thể xóa kết nối cuối cùng.', '#connection-tabs');
    const owned = modelsOf(id, values);
    if (owned.length) throw new FieldError(`Kết nối còn model: ${owned.join(', ')}. Chuyển hoặc xóa model trước.`, '#connection-tabs');
    delete values.providers[id];
    if (values.defaultProvider === id) values.defaultProvider = Object.keys(values.providers).sort()[0];
    return values;
  }, { note: `Đã xóa kết nối “${id}”.`, onPosted: () => { state.editingProvider = ''; fillConnection(''); } });
}
async function deleteModel() {
  return deleteSavedModel(selectedModelId());
}
async function reloadConfig() {
  if (state.loading || state.saving || state.reloading) return false;
  if (editorIsDirty() && !window.confirm('Tải lại sẽ bỏ các thay đổi chưa lưu. Tiếp tục?')) return false;
  state.reloading = true; setSaveStatus('Đang tải…'); refreshBusyState();
  try {
    await refreshConfig();
    if (!Object.hasOwn(state.values.providers || {}, state.editingProvider)) fillConnection(state.values.defaultProvider || Object.keys(state.values.providers)[0] || '');
    else fillConnection(state.editingProvider);
    state.cleanStatus = ''; setSaveStatus(state.cleanStatus); setFormStatus('Đã tải lại cấu hình.', 'ok'); return true;
  } catch (error) {
    setSaveStatus('Chưa tải được', 'error'); setFormStatus(`Không tải lại được cấu hình: ${error.message}`, 'error'); return false;
  } finally { state.reloading = false; refreshBusyState(); }
}
function bind() {
  $('#connection-form').onsubmit = event => submitEditor(event);
  $('#new-connection').onclick = openNewConnection;
  $('#cancel-editor').onclick = cancelEditor;
  $('#v-submit').onclick = verifyQuick;
  $('#m-picker').onchange = event => {
    const value = event.target.value;
    event.target.value = state.selectedModel ? `model:${state.selectedModel}` : CHOOSE_MODEL;
    chooseModel(value.startsWith('model:') ? value.slice(6) : '');
  };
  for (const selector of ['#pv-id', '#pv-base-url', '#pv-key']) $(selector).addEventListener('input', () => {
    if (selector !== '#pv-id') invalidateDiscovery();
    else if (!state.editingProvider) {
      renderModelPicker(); renderProviderSelects();
    }
    syncDirtyStatus();
  });
  $('#connection-form').addEventListener('input', () => { renderAdvancedSummary(); syncDirtyStatus(); });
  $('#connection-form').addEventListener('change', () => { renderAdvancedSummary(); syncDirtyStatus(); });
  $('#pv-api').addEventListener('change', () => {
    const val = $('#pv-api').value;
    $('#pv-api').dataset.edited = 'true';
    const urlInput = $('#pv-base-url');
    const currentUrl = normalizeBaseUrl(urlInput.value);
    if (Object.hasOwn(OFFICIAL_URLS, val)) {
      if (!currentUrl || OFFICIAL_URL_SET.has(currentUrl)) urlInput.value = OFFICIAL_URLS[val];
    } else if (OFFICIAL_URL_SET.has(currentUrl)) {
      urlInput.value = '';
    }
    syncUrlPlaceholder(val);
    invalidateDiscovery('Kiểu API đã đổi. Tải lại model.');
    syncDirtyStatus();
  });
  $('#m-efforts').addEventListener('change', () => { paintEffortChips($('#m-efforts')); renderAdvancedSummary(); syncDirtyStatus(); });
  $('#delete-provider').onclick = deleteProvider;
  $('#delete-model').onclick = deleteModel;
}
function selectBot(id) { if (id && getBot(id)) ctx.navigate('profile', { search: '?bot=' + encodeURIComponent(id) }); }
function loadConversation(id, botId) { if (botId) selectBot(botId); }
function labelProfileLinks() {
  if (!ctx.isActive()) return;
  for (const button of [...ctx.sidebar.querySelectorAll('[data-action="select"]')]) {
    const bot = getBot(button.dataset.botId); if (bot) button.setAttribute('aria-label', `Hồ sơ của ${bot.name}`);
  }
}
new MutationObserver(labelProfileLinks).observe(ctx.sidebar, { childList: true, subtree: true });
function initAdvancedState() {
  const adv = $('#model-advanced');
  if (adv && typeof window.matchMedia === 'function') {
    adv.open = !!window.matchMedia('(min-width: 851px)').matches;
  }
}
async function boot() {
  bind(); initAdvancedState(); refreshBusyState();
  window.addEventListener('thyca:setup-retry', () => {
    if (ctx.isActive() && !state.values && !state.loading && !state.reloading) void reloadConfig();
  });
  window.addEventListener('thyca:config-saved', () => {
    if (state.saving || state.loading || state.reloading || !state.values) return;
    if (typeof ThycaRouter !== 'undefined' && typeof ThycaRouter.setupState === 'function' && ThycaRouter.setupState() !== 'ready') return;
    externalConfigDirty = true;
    if (ctx.isActive() && !editorIsDirty()) void syncExternalConfig();
  });
  try { await ctx.shellReady; labelProfileLinks(); } catch (error) { setStatus(`Không kết nối được máy Thyca: ${error.message || ''}`, 'error'); }
  try {
    await refreshConfig();
    const first = state.values.defaultProvider || Object.keys(state.values.providers)[0] || '';
    fillConnection(first, { initial: true }); renderAll(); state.cleanStatus = ''; setSaveStatus(state.cleanStatus);
  } catch (error) {
    state.loading = false; refreshBusyState(); setSaveStatus('Chưa tải được', 'error'); setStatus(`Không tải được cấu hình: ${error.message}`, 'error');
  }
}
  const ready = boot();
  return { ready, selectBot, loadConversation, sidebarLabel(_id, name) { return `Hồ sơ của ${name}`; }, onEnter() { if (externalConfigDirty && !state.loading && !state.saving && !state.reloading && state.values && !editorIsDirty()) void syncExternalConfig(); } };
} });
