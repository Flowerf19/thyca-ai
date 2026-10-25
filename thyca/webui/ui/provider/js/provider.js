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
const FALLBACK_LIMITS = { loopMax: [1, 200], hotTailKB: [1, 64], contextTokens: [1000, 2000000] };
const CHOOSE_MODEL = '__choose__';
const state = {
  schema: null, values: null, meta: {}, loading: true, saving: false, reloading: false,
  editingProvider: '', providerBaseline: null, modelBaseline: null, editorBaseline: null, loadedModelId: '', cleanStatus: 'Đã tải cấu hình',
  testing: Object.create(null), configRevision: 0, connectionRevision: 0,
  connectionFingerprint: null, verifying: false, verifyGeneration: 0,
  activeVerify: null, discovery: null, discoveryStatus: '', manualModel: false,
  selectedModel: '', selectedModelFromDiscovery: false, defaultProviderTouched: false, modelProviderTouched: false,
};

const workspace = $('.workspace');
document.querySelector('.notebook-shell').setAttribute('aria-label', 'Thyca — kết nối AI');
workspace.setAttribute('aria-label', 'Nội dung kết nối AI');
workspace.insertAdjacentHTML('beforeend', `
<header class="page-nav">
  <div class="page-title"><h1 id="provider-title">Kết nối AI</h1></div>
  <div class="page-actions"><span class="save-status" id="save-status" role="status" aria-live="polite">Đang tải cấu hình…</span><button class="primary-action" id="save-use" type="submit" form="connection-form">Lưu và dùng</button><button class="quiet-button" id="reload-config" type="button">${icon('retry')} Tải lại</button></div>
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
          <label class="field field-wide"><span>API key <em id="pv-key-note"></em></span><input id="pv-key" type="password" autocomplete="new-password" maxlength="200" placeholder="Nhập API key" title="Key được lưu trên máy này và gửi tới URL API khi kết nối."></label>
          <div class="load-row field-wide"><button class="quiet-button" id="v-submit" type="button" title="Chỉ tải danh sách, không gọi model hay lưu cấu hình.">${icon('list')} Tải model</button></div>
        </div>
        <p class="discovery-status" id="discovery-status" role="status" aria-live="polite" hidden></p>
        <div class="model-editor">
          <label class="field model-picker-field"><span>Model để dùng</span><select id="m-picker" aria-label="Chọn model"><option value="__choose__">Chọn model…</option><option value="" data-manual="true">Nhập model ID thủ công</option></select></label>
          <label class="field manual-model-field" id="manual-model-field" hidden><span>Model ID</span><input id="m-id" maxlength="200" autocomplete="off" spellcheck="false" placeholder="Nhập model ID"><small id="model-id-note">Không cần tải danh sách nếu bạn đã biết ID.</small></label>
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
        <details class="advanced editor-advanced" id="editor-advanced"><summary>Nâng cao</summary>
          <div class="advanced-grid">
            <label class="field"><span>Kiểu API</span><select id="pv-api"><option value="openai_chat">Chat completions</option><option value="openai_responses">Responses</option></select></label>
            <label class="field"><span>Suy luận mặc định</span><select id="pv-effort"></select></label>
            <label class="field"><span>Kết nối của model</span><select id="m-provider"></select><small>Chuyển model sang kết nối khác chỉ sau xác nhận.</small></label>
            <label class="field"><span>Suy luận của model</span><select id="m-effort"></select></label>
            <label class="field"><span>Ngữ cảnh (token)</span><input id="m-context" type="number" min="1000" max="2000000" step="1" inputmode="numeric" placeholder="Kế thừa"></label>
            <label class="field"><span>Số vòng tối đa</span><input id="m-loop" type="number" min="1" max="200" step="1" inputmode="numeric" placeholder="Kế thừa"></label>
            <label class="field"><span>Nhớ nóng (KB)</span><input id="m-hot" type="number" min="1" max="64" step="1" inputmode="numeric" placeholder="Kế thừa"></label>
            <label class="field"><span>Kết nối mặc định dự phòng</span><select id="cfg-default-provider"></select></label>
          </div>
          <div class="advanced-actions">
            <button class="quiet-button" type="button" id="test-provider" disabled>Thử gọi model (có thể tính phí)</button>
            <button class="quiet-button" type="button" id="delete-provider" hidden>Xóa kết nối</button>
            <button class="quiet-button is-danger" type="button" id="delete-model" hidden>Xóa model đã lưu</button>
            <button class="quiet-button" type="button" id="save-catalog">Chỉ lưu cấu hình</button>
          </div>
          <p class="test-result" id="test-result" role="status" hidden></p>
        </details>
        <p class="form-status" id="editor-status" role="status" aria-live="polite" hidden></p>
        <div class="current-default" id="current-default" aria-live="polite"></div>
        <div class="editor-footer"><button class="quiet-button" type="button" id="cancel-editor">Hủy thay đổi</button></div>
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
function effortChoices() { return schemaField('provider.reasoningEffort')?.choices || ['low', 'high', 'max']; }
function effortDefault() { return schemaField('provider.reasoningEffort')?.default || 'high'; }
function modelEffortOptions(id) {
  const models = state.values?.models || {}, model = Object.hasOwn(models, id) ? models[id] : null;
  return model?.reasoningEfforts?.length ? model.reasoningEfforts : effortChoices();
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
  if (state.manualModel) return $('#m-id').value.trim();
  const option = $('#m-picker').value;
  return option.startsWith('model:') ? option.slice(6) : '';
}
function modelIsSaved(id = selectedModelId(), values = state.values) { return !!id && Object.hasOwn(values?.models || {}, id); }
function providerSnapshot() { return ['pv-id', 'pv-base-url', 'pv-api', 'pv-effort', 'pv-key'].map(id => $( '#' + id).value); }
function modelSnapshot({ includeAutoProvider = false } = {}) {
  const snapshot = [$('#m-picker').value, state.manualModel, $('#m-id').value, $('#m-provider').value, $('#m-effort').value, $('#m-context').value, $('#m-loop').value, $('#m-hot').value, $('#m-in').value, $('#m-cache').value, $('#m-out').value];
  if (!includeAutoProvider && !state.modelProviderTouched && state.modelBaseline) snapshot[3] = state.modelBaseline[3];
  return snapshot;
}
function providerIsDirty() { return !!state.providerBaseline && JSON.stringify(providerSnapshot()) !== JSON.stringify(state.providerBaseline); }
function modelIsDirty() { return !!state.modelBaseline && JSON.stringify(modelSnapshot()) !== JSON.stringify(state.modelBaseline); }
function editorSnapshot() { return [providerSnapshot(), modelSnapshot(), $('#cfg-default-provider').value]; }
function editorIsDirty() { return !!state.editorBaseline && JSON.stringify(editorSnapshot()) !== JSON.stringify(state.editorBaseline); }
function syncDirtyStatus() {
  if (!state.values || state.loading || state.saving || state.reloading) return;
  const dirty = editorIsDirty();
  setSaveStatus(dirty ? 'Chưa lưu thay đổi' : state.cleanStatus, dirty ? 'warn' : '');
}
function providerFingerprint(values, meta) { return JSON.stringify({ providers: values?.providers || {}, defaultProvider: values?.defaultProvider || '', storedKeys: meta?.providers || {} }); }
function advanceConfigRevision() { state.configRevision++; for (const [id, result] of Object.entries(state.testing)) if (!result.pending) delete state.testing[id]; }
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
  state.loading = false; advanceConfigRevision(); renderAll(); refreshBusyState();
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
function renderProviderSelects({ modelEffortValue = null } = {}) {
  const ids = providerIds().sort();
  const draftId = $('#pv-id')?.value.trim();
  const modelIds = [...new Set([...ids, ...(draftId && !ids.includes(draftId) ? [draftId] : [])])].sort();
  fillOptions($('#m-provider'), modelIds.map(id => [id, id]), $('#m-provider').value || state.editingProvider || draftId);
  const defaultSelect = $('#cfg-default-provider');
  const oldDefault = defaultSelect.value;
  fillOptions(defaultSelect, ids.map(id => [id, id]), state.defaultProviderTouched ? oldDefault : state.values.defaultProvider);
  const apiSelect = $('#pv-api');
  const providers = state.values?.providers || {};
  const entry = state.editingProvider && Object.hasOwn(providers, state.editingProvider) ? providers[state.editingProvider] : null;
  if (entry) {
    if (!apiSelect.dataset.edited) apiSelect.value = entry.api === 'openai_responses' ? 'openai_responses' : 'openai_chat';
  } else if (!apiSelect.dataset.edited) apiSelect.value = 'openai_chat';
  const providerEffort = $('#pv-effort').value || entry?.reasoningEffort || effortDefault();
  const providerEffortChoices = effortChoices().map(choice => [choice, choice]);
  if (!providerEffortChoices.some(([value]) => value === providerEffort)) providerEffortChoices.push([providerEffort, `${providerEffort} · đã lưu`]);
  fillOptions($('#pv-effort'), providerEffortChoices, providerEffort);
  const id = selectedModelId();
  const effort = $('#m-effort');
  const modelEffort = modelEffortValue ?? effort.value ?? '';
  const modelEffortChoices = [['', 'Kế thừa'], ...modelEffortOptions(id).map(choice => [choice, choice])];
  if (modelEffort && !modelEffortChoices.some(([value]) => value === modelEffort)) modelEffortChoices.push([modelEffort, `${modelEffort} · đã lưu`]);
  fillOptions(effort, modelEffortChoices, modelEffort);
}
function renderModelPicker() {
  const select = $('#m-picker'); if (!select || !state.values) return;
  const current = state.manualModel ? '' : (select.value || CHOOSE_MODEL);
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
  if (state.selectedModel && !byId.has(state.selectedModel) && !state.manualModel && state.selectedModelFromDiscovery) {
    options.push([`model:${state.selectedModel}`, `${state.selectedModel} — danh sách cũ, tải lại`, { disabled: 'disabled', 'data-stale': 'true', 'data-model': state.selectedModel }]);
  }
  options.push(['', 'Nhập model ID thủ công', { 'data-manual': 'true' }]);
  const encodedCurrent = state.manualModel ? '' : (current.startsWith('model:') ? current : (state.selectedModel ? `model:${state.selectedModel}` : current));
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
  $('#test-provider').disabled = !pid || state.saving || state.reloading || !!state.testing[pid]?.pending;
  $('#test-result').hidden = !pid || !state.testing[pid];
  if (pid && state.testing[pid]) {
    const result = state.testing[pid]; $('#test-result').textContent = result.pending ? 'Đang thử gọi model…' : result.text;
    $('#test-result').className = 'test-result' + (result.kind ? ' s-' + result.kind : '');
  }
  $('#pv-id').readOnly = !!pid;
  $('#pv-key-note').textContent = pid && providerHasStoredKey(pid) ? 'Đã lưu key' : '';
  $('#pv-key').placeholder = pid && providerHasStoredKey(pid) ? 'Để trống để giữ key' : 'Nhập API key';
}
function renderAll() {
  renderConnectionPicker(); renderProviderSelects(); renderModelPicker(); renderCurrentDefault(); renderEditorActions();
}
function refreshBusyState() {
  $('#editor-controls').disabled = state.loading || !state.values || state.saving || state.reloading;
  $('#reload-config').disabled = state.loading || state.saving || state.reloading;
  $('#v-submit').disabled = state.verifying || state.loading || !state.values || state.saving || state.reloading;
  $('#save-use').disabled = state.loading || !state.values || state.saving || state.reloading;
  $('#save-catalog').disabled = state.loading || !state.values || state.saving || state.reloading;
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
function modelFieldsSnapshot() { return modelSnapshot({ includeAutoProvider: true }); }
function setModelFields(id, { manual = false } = {}) {
  state.selectedModel = id; state.manualModel = manual; state.selectedModelFromDiscovery = !!(!manual && state.discovery?.models.includes(id));
  $('#m-picker').value = manual ? '' : (id ? `model:${id}` : CHOOSE_MODEL);
  $('#manual-model-field').hidden = !manual; $('#m-id').value = manual ? id : '';
  const models = state.values?.models || {};
  const model = !manual && id && Object.hasOwn(models, id) ? models[id] : null;
  state.loadedModelId = model ? id : '';
  $('#m-in').value = model && (Number(model.input) > 0 || Number(model.cache) > 0 || Number(model.output) > 0) ? model.input : '';
  $('#m-cache').value = model && (Number(model.input) > 0 || Number(model.cache) > 0 || Number(model.output) > 0) ? model.cache : '';
  $('#m-out').value = model && (Number(model.input) > 0 || Number(model.cache) > 0 || Number(model.output) > 0) ? model.output : '';
  if (model && ![model.input, model.cache, model.output].some(value => Number.isFinite(value) && value > 0)) {
    const pricing = state.values.pricing || {};
    const legacy = Object.hasOwn(pricing, id) ? pricing[id] : null;
    if (legacy) { $('#m-in').value = legacy.input ?? ''; $('#m-cache').value = legacy.cache ?? ''; $('#m-out').value = legacy.output ?? ''; }
  }
  const pid = ($('#pv-id').value || state.editingProvider).trim();
  $('#m-provider').value = model ? (model.provider || modelProviderId(state.values, id)) : pid;
  state.modelProviderTouched = false;
  $('#m-context').value = Number.isInteger(model?.contextTokens) ? model.contextTokens : '';
  $('#m-loop').value = Number.isInteger(model?.loopMax) ? model.loopMax : '';
  $('#m-hot').value = Number.isInteger(model?.hotTailKB) ? model.hotTailKB : '';
  const legacyUrl = model?.baseUrl || '';
  $('#legacy-url-warning').hidden = !legacyUrl;
  $('#legacy-url-warning').textContent = legacyUrl ? `Model này đang giữ URL riêng ${legacyUrl}; khi lưu sẽ không bị thay đổi.` : '';
  $('#ownership-warning').hidden = true; $('#ownership-warning').textContent = '';
  renderModelPicker(); renderProviderSelects({ modelEffortValue: model?.reasoningEffort || '' });
  state.modelBaseline = modelFieldsSnapshot();
  renderEditorActions();
}
function chooseModel(id, { manual = false, confirmDiscard = true } = {}) {
  if (!manual && id && state.discovery?.models.includes(id) && (!state.discovery || state.discovery.connectionRevision !== state.connectionRevision)) {
    setDiscoveryStatus('Danh sách model đã cũ. Tải lại trước khi chọn.', 'error'); return false;
  }
  if (!manual && id && modelIsSaved(id) && modelProviderId(state.values, id) !== ($('#pv-id').value || state.editingProvider)) {
    setDiscoveryStatus(`Model đã thuộc kết nối “${modelProviderId(state.values, id)}”. Chọn kết nối đó để sửa.`, 'error'); return false;
  }
  if (!manual && id === state.selectedModel && !state.manualModel) return true;
  if (confirmDiscard && modelIsDirty() && !window.confirm('Đổi model sẽ bỏ các thay đổi model chưa lưu. Tiếp tục?')) {
    renderModelPicker(); $('#m-picker').value = state.manualModel ? '' : (state.selectedModel ? `model:${state.selectedModel}` : CHOOSE_MODEL); return false;
  }
  state.selectedModel = id || ''; state.manualModel = manual;
  setModelFields(id || '', { manual });
  return true;
}
function fillConnection(id, { initial = false } = {}) {
  state.editingProvider = id || '';
  const providers = state.values?.providers || {};
  const provider = id && Object.hasOwn(providers, id) ? providers[id] : null;
  $('#pv-id').value = id || ''; $('#pv-base-url').value = provider?.baseUrl || ''; $('#pv-key').value = '';
  $('#pv-api').value = provider?.api === 'openai_responses' ? 'openai_responses' : 'openai_chat';
  $('#pv-api').dataset.edited = '';
  $('#pv-effort').value = provider?.reasoningEffort || effortDefault();
  $('#editor-advanced').open = false;
  state.discovery = null; state.selectedModel = ''; state.manualModel = false; state.selectedModelFromDiscovery = false;
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
  state.defaultProviderTouched = false; $('#cfg-default-provider').value = state.values?.defaultProvider || '';
  fillConnection(''); $('#connection-picker').value = '__new__'; $('#pv-id').focus(); return true;
}
function openExistingConnection(id) {
  if (!Object.hasOwn(state.values?.providers || {}, id)) return false;
  if (id === state.editingProvider) return true;
  if (editorIsDirty() && !window.confirm('Mở kết nối khác sẽ bỏ các thay đổi chưa lưu. Tiếp tục?')) {
    $('#connection-picker').value = state.editingProvider ? `provider:${state.editingProvider}` : '__new__'; return false;
  }
  state.defaultProviderTouched = false; fillConnection(id); $('#connection-picker').value = `provider:${id}`; return true;
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
    setDiscoveryStatus('Nhập API key để tải model, hoặc nhập model ID.'); return false;
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
    setDiscoveryStatus(models.length ? `Đã tải ${models.length} model.` : 'Không có model. Có thể nhập thủ công.', models.length ? 'ok' : '');
    return true;
  } catch (error) {
    if (state.activeVerify !== snapshot || snapshot.generation !== state.verifyGeneration) return false;
    setDiscoveryStatus(`Không tải được model: ${error.message}`, 'error'); return false;
  } finally {
    if (state.activeVerify === snapshot) { state.activeVerify = null; state.verifying = false; refreshBusyState(); }
  }
}
function selectedProviderId() { return ($('#m-provider').value || $('#pv-id').value || state.editingProvider).trim(); }
function draftForSave(useSelected) {
  const id = $('#pv-id').value.trim();
  if (!id) throw new FieldError('Nhập tên kết nối.', '#pv-id');
  if (!PROVIDER_ID_RE.test(id)) throw new FieldError('Tên kết nối chỉ gồm chữ, số, _ và -.', '#pv-id');
  const baseUrl = parseBaseUrl($('#pv-base-url').value);
  const effort = $('#pv-effort').value || effortDefault();
  const modelId = selectedModelId();
  if (!modelId) throw new FieldError('Chọn model hoặc nhập Model ID thủ công.', '#m-picker');
  if (state.selectedModelFromDiscovery && (!state.discovery || state.discovery.connectionRevision !== state.connectionRevision || state.discovery.sourceUrl !== baseUrl || state.discovery.providerId !== (state.editingProvider || ''))) {
    throw new FieldError('URL hoặc kết nối đã đổi. Tải lại danh sách hoặc nhập model thủ công.', '#m-picker');
  }
  const prices = parsePrices();
  const contextTokens = parseIntOrNull($('#m-context'), 'Ngữ cảnh', limitRange('contextTokens'));
  const loopMax = parseIntOrNull($('#m-loop'), 'Số vòng tối đa', limitRange('loopMax'));
  const hotTailKB = parseIntOrNull($('#m-hot'), 'Nhớ nóng', limitRange('hotTailKB'));
  const provider = selectedProviderId();
  if (!provider) throw new FieldError('Chọn kết nối cho model.', '#m-provider');
  return { id, baseUrl, api: $('#pv-api').value === 'openai_responses' ? 'openai_responses' : 'openai_chat', effort, apiKey: $('#pv-key').value.trim(), modelId, provider, prices, contextTokens, loopMax, hotTailKB, modelEffort: $('#m-effort').value, useSelected };
}
function mergeEditorDraft(fresh, draft) {
  const values = cloneValues(fresh.values); values.providers ||= {}; values.models ||= {};
  const editing = state.editingProvider;
  if (editing && !Object.hasOwn(values.providers, editing)) throw new FieldError('Kết nối này đã bị xóa. Tải lại rồi chọn kết nối khác.', '#connection-picker');
  if (!editing && Object.hasOwn(values.providers, draft.id)) throw new FieldError(`Kết nối “${draft.id}” đã có. Chọn kết nối đó để sửa.`, '#pv-id');
  if (editing && draft.id !== editing) throw new FieldError('Không thể đổi tên kết nối trong cùng một thao tác.', '#pv-id');
  const prevProvider = Object.hasOwn(values.providers, draft.id) ? values.providers[draft.id] : {};
  values.providers = { ...values.providers, [draft.id]: { apiKeyEnv: 'THYCA_TOKEN', ...prevProvider, baseUrl: draft.baseUrl, api: draft.api, reasoningEffort: draft.effort, apiKey: draft.apiKey } };
  const previousModel = Object.hasOwn(values.models, draft.modelId) ? values.models[draft.modelId] : null;
  if (state.manualModel && previousModel && state.loadedModelId !== draft.modelId) {
    throw new FieldError('Model này đã được lưu. Chọn model trong danh sách để giữ các cài đặt hiện có.', '#m-picker');
  }
  const previousOwner = previousModel ? modelProviderId(values, draft.modelId) : '';
  if (previousModel && previousOwner !== draft.provider) {
    if (!state.modelProviderTouched) throw new FieldError(`Model này đã thuộc kết nối “${previousOwner}”. Chọn kết nối sở hữu model trước khi chuyển.`, '#m-picker');
    if (!window.confirm(`Chuyển model “${draft.modelId}” từ “${previousOwner}” sang “${draft.provider}”?`)) throw new CancelOperation();
  }
  if (!previousModel && draft.provider !== draft.id) {
    if (!state.modelProviderTouched) throw new FieldError('Model mới phải dùng kết nối đang chỉnh.', '#m-provider');
    if (!window.confirm(`Gắn model “${draft.modelId}” vào kết nối “${draft.provider}”?`)) throw new CancelOperation();
  }
  const entry = { ...(previousModel || {}), provider: draft.provider };
  if (draft.prices) {
    Object.assign(entry, draft.prices);
    values.pricing = { ...(values.pricing || {}), [draft.modelId]: { ...draft.prices } };
  } else {
    delete entry.input; delete entry.cache; delete entry.output;
    if (values.pricing) delete values.pricing[draft.modelId];
  }
  for (const [key, value] of [['contextTokens', draft.contextTokens], ['loopMax', draft.loopMax], ['hotTailKB', draft.hotTailKB], ['reasoningEffort', draft.modelEffort]]) {
    if (value === null || value === '') delete entry[key]; else entry[key] = value;
  }
  values.models = { ...values.models, [draft.modelId]: entry };
  if (draft.useSelected) values.defaultModel = draft.modelId;
  if (state.defaultProviderTouched) values.defaultProvider = $('#cfg-default-provider').value;
  return values;
}
function configSavedEvent() { window.dispatchEvent(new Event('thyca:config-saved')); }
async function commitValues(buildValues, { note = '', onPosted = null } = {}) {
  if (state.loading || state.saving || state.reloading || !state.values) return false;
  state.saving = true; setSaveStatus('Đang lưu…'); setFormStatus(''); refreshBusyState();
  try {
    const fresh = await getJson('/api/config');
    const next = buildValues(fresh);
    if (!Object.hasOwn(next.providers || {}, next.defaultProvider)) throw new FieldError('Kết nối mặc định không còn tồn tại.', '#cfg-default-provider');
    const response = await postJson('/api/config', next);
    advanceConfigRevision();
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
async function submitEditor(event, useSelected = true) {
  event?.preventDefault?.();
  if (state.saving || state.loading || state.reloading) return false;
  clearFieldErrors(); setFormStatus('');
  let draft; try { draft = draftForSave(useSelected); } catch (error) { fail(error); return false; }
  return commitValues(fresh => mergeEditorDraft(fresh, draft), {
    note: useSelected ? `Đang dùng ${draft.modelId}.` : `Đã lưu kết nối và model “${draft.modelId}”.`,
    onPosted: () => {
      state.editingProvider = draft.id; state.defaultProviderTouched = false; state.modelProviderTouched = false;
      state.discovery = null;
      setModelFields(draft.modelId);
    },
  });
}
function updateTestResult(pid, token, result) {
  if (state.testing[pid]?.token !== token) return false;
  state.testing[pid] = result;
  if (state.editingProvider === pid) renderEditorActions();
  return true;
}
async function testProvider(pid) {
  if (!pid || !Object.hasOwn(state.values?.providers || {}, pid) || state.saving || state.reloading || state.testing[pid]?.pending) return false;
  const request = { revision: state.configRevision, token: {} }; state.testing[pid] = { pending: true, token: request.token }; renderEditorActions();
  try {
    const response = await postJson('/api/providers/test', { providerId: pid, ...(modelsOf(pid)[0] ? { model: modelsOf(pid)[0] } : {}) });
    if (state.testing[pid]?.token !== request.token) return false;
    updateTestResult(pid, request.token, request.revision === state.configRevision
      ? { kind: 'ok', text: `Gọi model thành công · ${response.model} · ${response.latencyMs}ms` }
      : { kind: 'muted', text: 'Kết nối đã đổi; bỏ qua kết quả thử cũ.' });
  } catch (error) {
    if (state.testing[pid]?.token !== request.token) return false;
    updateTestResult(pid, request.token, request.revision === state.configRevision
      ? { kind: 'error', text: `Thử gọi model thất bại: ${error.message}. Tải danh sách không xác nhận gọi model.` }
      : { kind: 'muted', text: 'Kết nối đã đổi; bỏ qua kết quả thử cũ.' });
  }
  return true;
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
  $('#connection-form').onsubmit = event => submitEditor(event, true);
  $('#save-catalog').onclick = () => submitEditor(null, false);
  $('#new-connection').onclick = openNewConnection;
  $('#connection-picker').onchange = event => {
    const value = event.target.value;
    const opened = value === '__new__' ? openNewConnection()
      : value.startsWith('provider:') ? openExistingConnection(value.slice('provider:'.length)) : false;
    if (!opened) renderConnectionPicker();
  };
  $('#cancel-editor').onclick = cancelEditor;
  $('#reload-config').onclick = reloadConfig;
  $('#v-submit').onclick = verifyQuick;
  $('#m-picker').onchange = event => {
    const value = event.target.value;
    event.target.value = state.manualModel ? '' : (state.selectedModel ? `model:${state.selectedModel}` : CHOOSE_MODEL);
    chooseModel(value.startsWith('model:') ? value.slice(6) : '', { manual: value === '' });
  };
  $('#m-id').addEventListener('input', () => { state.selectedModel = $('#m-id').value.trim(); syncDirtyStatus(); });
  for (const selector of ['#pv-id', '#pv-base-url', '#pv-key']) $(selector).addEventListener('input', () => {
    if (selector !== '#pv-id') invalidateDiscovery();
    else if (!state.editingProvider) {
      if (!state.modelProviderTouched) $('#m-provider').value = $('#pv-id').value.trim();
      renderModelPicker(); renderProviderSelects();
    }
    syncDirtyStatus();
  });
  $('#connection-form').addEventListener('input', syncDirtyStatus);
  $('#connection-form').addEventListener('change', syncDirtyStatus);
  $('#pv-api').addEventListener('change', () => { $('#pv-api').dataset.edited = 'true'; });
  $('#pv-effort').addEventListener('change', () => {});
  $('#m-provider').addEventListener('change', () => { state.modelProviderTouched = true; syncDirtyStatus(); });
  $('#cfg-default-provider').addEventListener('change', () => { state.defaultProviderTouched = true; syncDirtyStatus(); });
  $('#test-provider').onclick = () => testProvider(state.editingProvider);
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
