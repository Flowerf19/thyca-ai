// Retained provider controller; declarations stay private to this closure.
ThycaRouter.register('provider', { title: 'Provider', stylesheet: '/ui/provider/providercss/provider.css', mount(ctx) {
  const root = ctx.root;
  const $ = (selector, base = root) => selector === '.workspace' ? root : base.querySelector(selector);
  const $$ = (selector, base = root) => [...base.querySelectorAll(selector)];
  let selectedBot = '';
// Provider & model editor — discovery uses the unsaved connection draft.
// API: GET /api/config, POST /api/config, POST /api/onboarding/verify,
//      POST /api/providers/test.

const PROVIDER_ID_RE = /^[A-Za-z0-9_-]+$/;
const FALLBACK_LIMITS = { loopMax: [1, 200], hotTailKB: [1, 64], contextTokens: [1000, 2000000], maxTokens: [1, 2000000] };
const CHOOSE_MODEL = '__choose__';
const state = {
  schema: null, values: null, meta: {}, loading: true, saving: false, reloading: false,
  editingProvider: '', providerBaseline: null, modelBaseline: null, editorBaseline: null, cleanStatus: 'Đã tải cấu hình',
  connectionFingerprint: null, verifying: false, verifyGeneration: 0,
  connectionRevision: 0,
  activeVerify: null, discovery: null, discoveryStatus: '',
  selectedModel: '', selectedModelFromDiscovery: false,
};

const workspace = $('.workspace');
document.querySelector('.notebook-shell').setAttribute('aria-label', 'Thyca — kết nối AI');
workspace.setAttribute('aria-label', 'Nội dung kết nối AI');
workspace.insertAdjacentHTML('beforeend', `
<header class="page-nav">
  <div class="page-title"><h1 id="provider-title">Kết nối AI</h1></div>
  <div class="page-actions"><span class="save-status" id="save-status" role="status" aria-live="polite">Đang tải cấu hình…</span></div>
</header>
<div class="page-scroll"><div class="page-inner">
  <p class="page-status" id="page-status" role="status" aria-live="polite" hidden></p>
  <section class="paper-card connection-editor" id="connection-editor" aria-labelledby="provider-title">
    <fieldset id="editor-controls">
      <div class="editor-topline">
        <label class="field"><span>Kết nối</span><select id="connection-picker" aria-label="Chọn kết nối đã lưu"></select></label>
        <button class="quiet-button" id="new-connection" type="button">${icon('plus')} Thêm kết nối</button>
      </div>
      <form class="editor-form" id="connection-form" aria-label="Cấu hình kết nối và model" novalidate>
        <div class="connection-fields">
          <label class="field"><span>Tên kết nối</span><input id="pv-id" maxlength="64" autocomplete="off" spellcheck="false" placeholder="Không dấu, không khoảng trắng" required><small class="name-hint-space" aria-hidden="true"></small></label>
          <label class="field"><span>URL API</span><input id="pv-base-url" type="url" inputmode="url" autocomplete="off" spellcheck="false" placeholder="https://api.example.com/v1" required></label>
          <label class="field"><span>API key <em id="pv-key-note"></em></span><input id="pv-key" type="password" autocomplete="new-password" maxlength="200" placeholder="Nhập API key" title="Key được lưu trên máy này và gửi tới URL API khi kết nối."></label>
          <label class="field"><span>Kiểu API</span><select id="pv-api"><option value="openai_chat">Chat completions</option><option value="openai_responses">Responses</option></select></label>
          <div class="load-row field-wide"><button class="quiet-button" id="v-submit" type="button" title="Chỉ tải danh sách, không gọi model hay lưu cấu hình.">${icon('list')} Tải model</button></div>
        </div>
        <p class="discovery-status" id="discovery-status" role="status" aria-live="polite" hidden></p>
        <div class="model-editor">
          <label class="field model-picker-field"><span>Model để dùng</span><select id="m-picker" aria-label="Chọn model"><option value="__choose__">Chọn model…</option></select></label>
          <p class="ownership-warning field-wide" id="ownership-warning" role="status" hidden></p>
          <p class="legacy-url-warning field-wide" id="legacy-url-warning" role="status" hidden></p>
          <div class="price-heading field-wide" title="Giá chỉ dùng để ước tính chi phí; API không trả giá này. Điền đủ ba mức hoặc để trống cả ba."><strong>Giá (tùy chọn)</strong><span>USD / 1 triệu token</span></div>
          <div class="price-fields field-wide">
            <label class="field"><span>Input</span><input id="m-in" type="number" min="0" step="any" inputmode="decimal" placeholder="Không bắt buộc"></label>
            <label class="field"><span>Cache</span><input id="m-cache" type="number" min="0" step="any" inputmode="decimal" placeholder="Không bắt buộc"></label>
            <label class="field"><span>Output</span><input id="m-out" type="number" min="0" step="any" inputmode="decimal" placeholder="Không bắt buộc"></label>
          </div>
          <p class="field-hint field-wide">Nhập đủ 3 giá hoặc để trống.</p>
        </div>
        <div class="advanced" id="connection-extra">
          <div class="advanced-grid">
            <fieldset class="field field-wide" id="m-efforts-wrap"><legend>Mức suy luận model hỗ trợ</legend><div class="effort-checks" id="m-efforts"></div></fieldset>
            <label class="field"><span>Ngữ cảnh (token)</span><input id="m-context" type="number" min="1000" max="2000000" step="16000" inputmode="numeric" placeholder="Kế thừa"></label>
            <label class="field"><span>Output tối đa (token)</span><input id="m-max" type="number" min="1" max="2000000" step="16000" inputmode="numeric" placeholder="Mặc định 16384"></label>
          </div>
          <div class="advanced-actions">
            <button class="quiet-button" type="button" id="delete-provider" hidden>Xóa kết nối</button>
            <button class="quiet-button is-danger" type="button" id="delete-model" hidden>Xóa model đã lưu</button>
          </div>
        </div>
        <p class="form-status" id="editor-status" role="status" aria-live="polite" hidden></p>
        <div class="current-default" id="current-default" aria-live="polite"></div>
        <div class="editor-footer"><button class="primary-action" id="save-use" type="submit" form="connection-form">Lưu và dùng</button><button class="quiet-button" type="button" id="cancel-editor">Hủy thay đổi</button></div>
      </form>
    </fieldset>
  </section>
</div></div>`);

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
    const field = $(err.fieldId); let details = field?.closest('details');
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
  // Extra levels only ever come from previously stored config data now
  // that the form no longer adds custom levels itself.
  const extras = [...new Set([...saved, ...(model?.reasoningEffort ? [model.reasoningEffort] : [])])].filter(level => !base.includes(level));
  return [...base, ...extras];
}
function paintEffortChips(box) {
  for (const label of box.querySelectorAll('label.effort-chip')) {
    const input = label.querySelector('input');
    const tick = label.querySelector('.effort-tick');
    const on = !!(input && input.checked);
    label.setAttribute('data-active', on ? 'true' : 'false');
    if (tick) tick.textContent = on ? '✓' : '';
  }
}
function renderEffortChecks(model) {
  const box = $('#m-efforts'); if (!box) return;
  const saved = Array.isArray(model?.reasoningEfforts) ? model.reasoningEfforts.filter(Boolean) : [];
  // A model with no stored set starts with every global level ticked
  // (unticked = inherit). A stored set ticks exactly its own levels.
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
    const tick = document.createElement('span');
    tick.className = 'effort-tick';
    tick.textContent = initial.includes(level) ? '✓' : '';
    const name = document.createElement('span');
    name.className = 'effort-name';
    name.textContent = level;
    label.append(input, tick, name);
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

function fillOptions(select, options, current) {
  if (!select) return;
  select.replaceChildren(...options.map(([value, text, attrs = {}]) => {
    const option = Object.assign(document.createElement('option'), { value, textContent: text });
    for (const [key, value] of Object.entries(attrs)) option.setAttribute(key, value);
    return option;
  }));
  if (options.some(([value]) => value === current)) select.value = current;
}
function renderConnectionPicker() {
  const select = $('#connection-picker'); if (!select) return;
  const providers = state.values?.providers || {};
  const options = [['__new__', 'Thêm kết nối mới'], ...providerIds().sort().map(id => [`provider:${id}`, `${id} — ${providers[id]?.baseUrl || ''}`])];
  const selected = state.editingProvider ? `provider:${state.editingProvider}` : '__new__'; fillOptions(select, options, selected);
}
function renderProviderSelects() {
  const apiSelect = $('#pv-api');
  const providers = state.values?.providers || {};
  const entry = state.editingProvider && Object.hasOwn(providers, state.editingProvider) ? providers[state.editingProvider] : null;
  if (entry) {
    if (!apiSelect.dataset.edited) apiSelect.value = entry.api === 'openai_responses' ? 'openai_responses' : 'openai_chat';
  } else if (!apiSelect.dataset.edited) apiSelect.value = 'openai_chat';
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
  $('#current-default').textContent = id ? `Hiện tại: ${id} · ${provider || 'kết nối mặc định'}` : 'Hiện tại: Chưa chọn model mặc định.';
}
function renderEditorActions() {
  const pid = state.editingProvider;
  $('#delete-provider').hidden = !pid;
  $('#delete-model').hidden = !modelIsSaved();
  $('#pv-id').readOnly = !!pid;
  $('#pv-key-note').textContent = pid && providerHasStoredKey(pid) ? 'Đã lưu key' : '';
  $('#pv-key').placeholder = pid && providerHasStoredKey(pid) ? 'Để trống để giữ key' : 'Nhập API key';
}
function renderAll() {
  renderConnectionPicker(); renderProviderSelects(); renderModelPicker(); renderCurrentDefault(); renderEditorActions();
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
  // Sparse like contextTokens: empty means unset (falls back to the 16384 default).
  $('#m-max').value = Number.isInteger(model?.maxTokens) ? model.maxTokens : '';
  const legacyUrl = model?.baseUrl || '';
  $('#legacy-url-warning').hidden = !legacyUrl;
  $('#legacy-url-warning').textContent = legacyUrl ? `Model này đang giữ URL riêng ${legacyUrl}; khi lưu sẽ không bị thay đổi.` : '';
  $('#ownership-warning').hidden = true; $('#ownership-warning').textContent = '';
  renderModelPicker(); renderProviderSelects();
  state.modelBaseline = modelFieldsSnapshot();
  renderEditorActions();
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
  $('#pv-api').value = provider?.api === 'openai_responses' ? 'openai_responses' : 'openai_chat';
  $('#pv-api').dataset.edited = '';
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
  fillConnection(''); $('#connection-picker').value = '__new__'; $('#pv-id').focus(); return true;
}
function openExistingConnection(id) {
  if (!Object.hasOwn(state.values?.providers || {}, id)) return false;
  if (id === state.editingProvider) return true;
  if (editorIsDirty() && !window.confirm('Mở kết nối khác sẽ bỏ các thay đổi chưa lưu. Tiếp tục?')) {
    $('#connection-picker').value = state.editingProvider ? `provider:${state.editingProvider}` : '__new__'; return false;
  }
  fillConnection(id); $('#connection-picker').value = `provider:${id}`; return true;
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
function sourceDraft() { return { url: $('#pv-base-url').value, key: $('#pv-key').value.trim(), providerId: state.editingProvider }; }
async function verifyQuick() {
  if (state.verifying || state.loading || state.saving || state.reloading || !state.values) return false;
  clearFieldErrors(); setFormStatus('');
  const source = sourceDraft();
  let baseUrl; try { baseUrl = parseBaseUrl(source.url); } catch (error) { fail(error); return false; }
  const existingId = source.providerId && Object.hasOwn(state.values.providers || {}, source.providerId) ? source.providerId : '';
  if (!source.key && !existingId) {
    setDiscoveryStatus('Nhập API key để tải model.', ''); return false;
  }
  const payload = { baseUrl };
  if (source.key) payload.apiKey = source.key;
  else payload.providerId = existingId;
  const snapshot = { generation: ++state.verifyGeneration, baseUrl, key: source.key, providerId: existingId, connectionRevision: state.connectionRevision };
  state.activeVerify = snapshot; state.verifying = true; refreshBusyState(); setDiscoveryStatus('Đang tải model…');
  try {
    const response = await postJson('/api/onboarding/verify', payload);
    if (state.activeVerify !== snapshot || snapshot.generation !== state.verifyGeneration || snapshot.connectionRevision !== state.connectionRevision || JSON.stringify(sourceDraft()) !== JSON.stringify(source)) return false;
    const models = Array.isArray(response.models) ? response.models.filter(id => typeof id === 'string' && id.length) : [];
    state.discovery = { models, sourceUrl: baseUrl, providerId: snapshot.providerId, connectionRevision: snapshot.connectionRevision, generation: snapshot.generation };
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
  const modelId = selectedModelId();
  if (!modelId) throw new FieldError('Chọn model trong danh sách.', '#m-picker');
  if (state.selectedModelFromDiscovery && (!state.discovery || state.discovery.connectionRevision !== state.connectionRevision || state.discovery.sourceUrl !== baseUrl || state.discovery.providerId !== (state.editingProvider || ''))) {
    throw new FieldError('URL hoặc kết nối đã đổi. Tải lại danh sách model.', '#m-picker');
  }
  const prices = parsePrices();
  const contextTokens = parseIntOrNull($('#m-context'), 'Ngữ cảnh', limitRange('contextTokens'));
  const maxTokens = parseIntOrNull($('#m-max'), 'Output tối đa', limitRange('maxTokens'));
  // The model always belongs to the connection being edited: no separate
  // model→provider picker exists, so cross-connection picks are impossible.
  const provider = id;
  const efforts = checkedEfforts();
  return { id, baseUrl, api: $('#pv-api').value === 'openai_responses' ? 'openai_responses' : 'openai_chat', apiKey: $('#pv-key').value.trim(), modelId, provider, prices, contextTokens, maxTokens, efforts };
}
function mergeEditorDraft(fresh, draft) {
  const values = cloneValues(fresh.values); values.providers ||= {}; values.models ||= {};
  const editing = state.editingProvider;
  if (editing && !Object.hasOwn(values.providers, editing)) throw new FieldError('Kết nối này đã bị xóa. Tải lại rồi chọn kết nối khác.', '#connection-picker');
  if (!editing && Object.hasOwn(values.providers, draft.id)) throw new FieldError(`Kết nối “${draft.id}” đã có. Chọn kết nối đó để sửa.`, '#pv-id');
  if (editing && draft.id !== editing) throw new FieldError('Không thể đổi tên kết nối trong cùng một thao tác.', '#pv-id');
  const prevProvider = Object.hasOwn(values.providers, draft.id) ? values.providers[draft.id] : {};
  // The form no longer edits the connection default: keep the stored
  // provider effort, or the system default for a brand-new connection.
  const keptEffort = prevProvider.reasoningEffort || effortDefault();
  values.providers = { ...values.providers, [draft.id]: { apiKeyEnv: 'THYCA_TOKEN', ...prevProvider, baseUrl: draft.baseUrl, api: draft.api, reasoningEffort: keptEffort, apiKey: draft.apiKey } };
  const previousModel = Object.hasOwn(values.models, draft.modelId) ? values.models[draft.modelId] : null;
  const previousOwner = previousModel ? modelProviderId(values, draft.modelId) : '';
  // Defensive: the picker blocks cross-connection models upfront, so this
  // only fires on stale/race state — never silently transfer ownership.
  if (previousModel && previousOwner !== draft.provider) throw new FieldError(`Model này đã thuộc kết nối “${previousOwner}”. Chọn kết nối đó để sửa.`, '#m-picker');
  const entry = { ...(previousModel || {}), provider: draft.provider };
  if (draft.prices) {
    Object.assign(entry, draft.prices);
    values.pricing = { ...(values.pricing || {}), [draft.modelId]: { ...draft.prices } };
  } else {
    delete entry.input; delete entry.cache; delete entry.output;
    if (values.pricing) delete values.pricing[draft.modelId];
  }
  // loopMax/hotTailKB are intentionally untouched here: the form no longer
  // edits them, so a previously stored value rides along via previousModel.
  // reasoningEffort is actively deleted: the form no longer manages a
  // per-model default, so a stale one must not silently win over the
  // provider/global fallback.
  for (const [key, value] of [['contextTokens', draft.contextTokens], ['maxTokens', draft.maxTokens], ['reasoningEfforts', draft.efforts.length ? [...draft.efforts] : null]]) {
    if (value === null || value === '') delete entry[key]; else entry[key] = value;
  }
  delete entry.reasoningEffort;
  values.models = { ...values.models, [draft.modelId]: entry };
  // Every form save also selects: the only save path is "Lưu và dùng".
  values.defaultModel = draft.modelId;
  // First connection from an empty config (or a stale default): fall back
  // to the connection being saved so the commitValues guard passes. A
  // still-valid default is left untouched.
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
    if (!Object.hasOwn(next.providers || {}, next.defaultProvider)) throw new FieldError('Kết nối mặc định không còn tồn tại.', '#connection-picker');
    const response = await postJson('/api/config', next);
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
      setFormStatus(response.ready === false ? 'Đã lưu cấu hình. Chưa thể xác nhận model chạy được; kiểm tra API key/kết nối.' : note, response.ready === false ? 'warn' : 'ok');
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
    note: `Đang dùng ${draft.modelId}.`,
    onPosted: () => {
      state.editingProvider = draft.id;
      state.discovery = null;
      setModelFields(draft.modelId);
    },
  });
}
async function deleteProvider() {
  const id = state.editingProvider;
  if (!id || !window.confirm(`Xóa kết nối “${id}”?`)) return false;
  return commitValues(fresh => {
    const values = cloneValues(fresh.values); const ids = Object.keys(values.providers || {});
    if (!Object.hasOwn(values.providers || {}, id)) throw new FieldError('Kết nối đã bị xóa.', '#connection-picker');
    if (ids.length <= 1) throw new FieldError('Không thể xóa kết nối cuối cùng.', '#connection-picker');
    const owned = modelsOf(id, values);
    if (owned.length) throw new FieldError(`Kết nối còn model: ${owned.join(', ')}. Chuyển hoặc xóa model trước.`, '#connection-picker');
    delete values.providers[id];
    if (values.defaultProvider === id) values.defaultProvider = Object.keys(values.providers).sort()[0];
    return values;
  }, { note: `Đã xóa kết nối “${id}”.`, onPosted: () => { state.editingProvider = ''; fillConnection(''); } });
}
async function deleteModel() {
  const id = selectedModelId();
  if (!id || !modelIsSaved(id) || !window.confirm(`Xóa model “${id}”?`)) return false;
  if (id === state.values.defaultModel) { setFormStatus('Đang là model mặc định. Chọn model khác trước.', 'error'); return false; }
  return commitValues(fresh => {
    const values = cloneValues(fresh.values);
    if (!Object.hasOwn(values.models || {}, id)) throw new FieldError('Model đã bị xóa.', '#m-picker');
    if (id === values.defaultModel) throw new FieldError('Đang là model mặc định. Chọn model khác trước.', '#m-picker');
    delete values.models[id]; if (values.pricing) delete values.pricing[id]; return values;
  }, { note: `Đã xóa model “${id}”.`, onPosted: () => { setModelFields('', {}); } });
}
async function reloadConfig() {
  if (state.loading || state.saving || state.reloading) return false;
  if (editorIsDirty() && !window.confirm('Tải lại sẽ bỏ các thay đổi chưa lưu. Tiếp tục?')) return false;
  state.reloading = true; setSaveStatus('Đang tải…'); refreshBusyState();
  try {
    await refreshConfig();
    if (!Object.hasOwn(state.values.providers || {}, state.editingProvider)) fillConnection(state.values.defaultProvider || Object.keys(state.values.providers)[0] || '');
    else fillConnection(state.editingProvider);
    state.cleanStatus = 'Đã tải cấu hình'; setSaveStatus(state.cleanStatus, 'ok'); setFormStatus('Đã tải lại cấu hình.', 'ok'); return true;
  } catch (error) {
    setSaveStatus('Chưa tải được', 'error'); setFormStatus(`Không tải lại được cấu hình: ${error.message}`, 'error'); return false;
  } finally { state.reloading = false; refreshBusyState(); }
}
function bind() {
  $('#connection-form').onsubmit = event => submitEditor(event);
  $('#new-connection').onclick = openNewConnection;
  $('#connection-picker').onchange = event => {
    const value = event.target.value;
    const opened = value === '__new__' ? openNewConnection()
      : value.startsWith('provider:') ? openExistingConnection(value.slice('provider:'.length)) : false;
    if (!opened) renderConnectionPicker();
  };
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
  $('#connection-form').addEventListener('input', syncDirtyStatus);
  $('#connection-form').addEventListener('change', syncDirtyStatus);
  $('#pv-api').addEventListener('change', () => { $('#pv-api').dataset.edited = 'true'; });
  $('#m-efforts').addEventListener('change', () => { paintEffortChips($('#m-efforts')); syncDirtyStatus(); });
  $('#delete-provider').onclick = deleteProvider;
  $('#delete-model').onclick = deleteModel;
}
function selectBot(id) { if (id && getBot(id)) ctx.navigate('profile', { search: '?bot=' + encodeURIComponent(id) }); }
function loadSession(id, title, botId) { if (botId) selectBot(botId); }
function labelProfileLinks() {
  if (!ctx.isActive()) return;
  for (const button of [...ctx.sidebar.querySelectorAll('[data-action="select"]')]) {
    const bot = getBot(button.dataset.botId); if (bot) button.setAttribute('aria-label', `Hồ sơ của ${bot.name}`);
  }
}
new MutationObserver(labelProfileLinks).observe(ctx.sidebar, { childList: true, subtree: true });
async function boot() {
  bind(); refreshBusyState();
  window.addEventListener('thyca:setup-retry', () => {
    if (ctx.isActive() && !state.values && !state.loading && !state.reloading) void reloadConfig();
  });
  try { await ctx.shellReady; labelProfileLinks(); } catch (error) { setStatus(`Không kết nối được máy Thyca: ${error.message || ''}`, 'error'); }
  try {
    await refreshConfig();
    const first = state.values.defaultProvider || Object.keys(state.values.providers)[0] || '';
    fillConnection(first, { initial: true }); renderAll(); state.cleanStatus = 'Đã tải cấu hình'; setSaveStatus(state.cleanStatus);
  } catch (error) {
    state.loading = false; refreshBusyState(); setSaveStatus('Chưa tải được', 'error'); setStatus(`Không tải được cấu hình: ${error.message}`, 'error');
  }
}
  const ready = boot();
  return { ready, selectBot, loadSession, sidebarLabel(_id, name) { return `Hồ sơ của ${name}`; } };
} });
