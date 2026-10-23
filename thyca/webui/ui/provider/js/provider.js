// Retained provider controller; declarations stay private to this closure.
ThycaRouter.register('provider', { title: 'Provider', stylesheet: '/ui/provider/providercss/provider.css', mount(ctx) {
  const root = ctx.root;
  const $ = (selector, base = root) => selector === '.workspace' ? root : base.querySelector(selector);
  const $$ = (selector, base = root) => [...base.querySelectorAll(selector)];
  let selectedBot = '';
// Provider & model screen — wired to the real backend, no mock data.
// The shared shell (header/sidebar/dialogs) is emitted by emitShell() in
// index.html before avatars/sidebar/studio bind; this script fills
// .workspace, then calls bootShell() for the live roster.
// API: GET /api/config, POST /api/config,
//      POST /api/onboarding/verify, POST /api/providers/test.


/* avatars.js setState() reaches `runs`/`cancelTurn` on error/offline state
   changes (e.g. the studio's error preview). This screen owns no turn
   stream — an empty map keeps that path inert. selectBot/loadSession and
   all other shell chrome come from share/shell-app.js. */
const runs = new Map();
function cancelTurn() { return false; }

const PROVIDER_ID_RE = /^[A-Za-z0-9_-]+$/;
const API_LABELS = { openai_chat: 'Chat completions', openai_responses: 'Responses' };
const FALLBACK_LIMITS = { loopMax: [1, 200], hotTailKB: [1, 64], contextTokens: [1000, 2000000] };

const state = {
  schema: null,
  values: null,        // whole `values` payload — mutated draft, posted as-is
  meta: {},
  saving: false,
  editingProvider: '', // provider id loaded into the form ('' = add mode)
  editingModel: '',    // model id loaded into the form ('' = add mode)
  testing: {},         // providerId -> {pending|ok, text}
  verifying: false,
  verifyProvider: '',  // providerId the last quick-verify ran against
};

const workspace = $('.workspace');
document.querySelector('.notebook-shell').setAttribute('aria-label', 'Thyca — provider & model');
workspace.setAttribute('aria-label', 'Nội dung provider');

/* .page-nav sits outside .page-scroll so the title strip stays fixed while
   the cards scroll beneath it (same layout as the settings page). */
workspace.insertAdjacentHTML('beforeend', `
<header class="page-nav">
  <div class="page-title">
    <div class="eyebrow">Kết nối</div>
    <h1>Provider &amp; model</h1>
    <p class="page-sub">Mỗi provider key riêng, mỗi model gọi đúng provider của nó.</p>
  </div>
  <div class="page-actions">
    <button class="quiet-button" id="reload-config" type="button">${icon('retry')} Tải lại</button>
    <button class="primary-action" id="save-config" type="button">${icon('check')} Lưu cấu hình</button>
  </div>
</header>
<div class="page-scroll"><div class="page-inner">
<div class="page-status" id="page-status" role="status" aria-live="polite" hidden></div>
<div class="section-stack">
  <section class="paper-card" aria-labelledby="providers-title">
    <div class="eyebrow">Providers</div>
    <h2 class="card-title" id="providers-title">Mỗi provider một chìa khóa riêng</h2>
    <p class="card-copy">Provider giữ endpoint, loại API và key. "Kiểm tra" gọi thẳng provider bằng model nó sở hữu — xác nhận key và endpoint còn sống trước khi model chạy thật.</p>
    <ul class="entry-list" id="provider-list" aria-label="Danh sách provider"></ul>
    <form class="panel form-grid" id="provider-form" novalidate>
      <h3 class="panel-title" id="provider-form-title">Thêm provider</h3>
      <label class="field"><span>Tên (id)</span><input id="pv-id" maxlength="64" autocomplete="off" spellcheck="false" placeholder="vd: openai" required></label>
      <label class="field"><span>Base URL</span><input id="pv-base-url" type="url" inputmode="url" autocomplete="off" spellcheck="false" placeholder="https://api.openai.com/v1" required></label>
      <label class="field"><span>Kiểu API</span><select id="pv-api"><option value="openai_chat">Chat completions</option><option value="openai_responses">Responses</option></select></label>
      <label class="field"><span>Mức suy luận mặc định</span><select id="pv-effort"></select></label>
      <label class="field field-wide"><span>API key <em id="pv-key-note">chỉ gửi về máy chủ Thyca trên máy bạn</em></span>
        <input id="pv-key" type="password" autocomplete="new-password" maxlength="200" placeholder="sk-…">
      </label>
      <div class="form-actions">
        <button class="primary-action" type="submit" id="pv-submit">${icon('plus')} Thêm provider</button>
        <button class="quiet-button" type="button" id="pv-cancel" hidden>Hủy sửa</button>
        <span class="field-hint">Key không bao giờ hiển thị — để trống để giữ key đã lưu.</span>
      </div>
    </form>
  </section>
  <section class="paper-card" aria-labelledby="models-title">
    <div class="eyebrow">Models</div>
    <h2 class="card-title" id="models-title">Mỗi model gọi đúng provider của nó</h2>
    <p class="card-copy">Model trỏ tới một provider (hoặc kế thừa provider mặc định), kèm giá và giới hạn riêng. Ô để trống sẽ kế thừa cấu hình chung.</p>
    <div class="defaults-row">
      <label class="field"><span>Provider mặc định</span><select id="cfg-default-provider"></select></label>
      <label class="field"><span>Model mặc định</span><select id="cfg-default-model"></select></label>
    </div>
    <ul class="entry-list" id="model-list" aria-label="Danh sách model"></ul>
    <form class="panel form-grid" id="model-form" novalidate>
      <h3 class="panel-title" id="model-form-title">Thêm / sửa model</h3>
      <label class="field"><span>Model ID</span><input id="m-id" maxlength="200" autocomplete="off" spellcheck="false" placeholder="vd: gpt-5" required></label>
      <label class="field"><span>Provider</span><select id="m-provider"></select></label>
      <label class="field"><span>Mức suy luận</span><select id="m-effort"></select></label>
      <label class="field"><span>Ngữ cảnh (tokens)</span><input id="m-context" type="number" min="1000" max="2000000" step="1" inputmode="numeric" placeholder="kế thừa"></label>
      <label class="field"><span>Số vòng tối đa</span><input id="m-loop" type="number" min="1" max="200" step="1" inputmode="numeric" placeholder="kế thừa"></label>
      <label class="field"><span>Nhớ nóng (KB)</span><input id="m-hot" type="number" min="1" max="64" step="1" inputmode="numeric" placeholder="kế thừa"></label>
      <label class="field"><span>Giá input ($/1M)</span><input id="m-in" type="number" min="0" step="any" inputmode="decimal" placeholder="0"></label>
      <label class="field"><span>Giá cache ($/1M)</span><input id="m-cache" type="number" min="0" step="any" inputmode="decimal" placeholder="0"></label>
      <label class="field"><span>Giá output ($/1M)</span><input id="m-out" type="number" min="0" step="any" inputmode="decimal" placeholder="0"></label>
      <div class="form-actions">
        <button class="primary-action" type="submit" id="m-submit">${icon('plus')} Thêm model</button>
        <button class="quiet-button" type="button" id="m-cancel" hidden>Hủy sửa</button>
        <span class="field-hint">Giá: điền đủ cả 3 ô hoặc để trống cả 3 (dùng giá backend).</span>
      </div>
    </form>
  </section>
  <section class="paper-card" aria-labelledby="verify-title">
    <div class="eyebrow">Kết nối nhanh</div>
    <h2 class="card-title" id="verify-title">Kiểm tra trước khi lưu</h2>
    <p class="card-copy">Gọi thử <code>/models</code> của một endpoint để xác nhận key, hoặc chọn provider đã lưu để dùng lại key của nó.</p>
    <form class="panel form-grid" id="verify-form" novalidate>
      <label class="field field-wide"><span>Base URL</span><input id="v-base-url" type="url" inputmode="url" autocomplete="off" spellcheck="false" placeholder="https://api.openai.com/v1" required></label>
      <label class="field"><span>Dùng key của provider</span><select id="v-provider"></select></label>
      <label class="field"><span>API key</span><input id="v-key" type="password" autocomplete="off" maxlength="200" placeholder="để trống = key đã lưu"></label>
      <div class="form-actions">
        <button class="primary-action" type="submit" id="v-submit">${icon('check')} Kiểm tra &amp; tải model</button>
        <span class="field-hint">Key để trống: backend tự lấy key đã lưu của provider đã chọn (hoặc provider mặc định).</span>
      </div>
    </form>
    <div class="verify-result" id="verify-result" hidden>
      <p class="status-line" id="verify-status" role="status"></p>
      <div class="chip-list verify-models" id="verify-models"></div>
      <p class="field-hint" id="verify-hint" hidden>Bấm một model để điền sẵn vào form phía trên.</p>
    </div>
  </section>
</div>
</div></div>`);

/* ---------- status + field errors ------------------------------------- */
let statusTimer = null;
function setStatus(text, kind = '') {
  const el = $('#page-status'); if (!el) return;
  clearTimeout(statusTimer); statusTimer = null;
  el.hidden = !text; el.textContent = text || '';
  el.className = 'page-status' + (kind ? ' s-' + kind : '');
  if (text && kind !== 'error') statusTimer = setTimeout(() => { el.hidden = true; }, 8000);
}
class FieldError extends Error { constructor(message, fieldId) { super(message); this.fieldId = fieldId; } }
function clearFieldErrors(root) { $$('[aria-invalid]', root).forEach(n => n.removeAttribute('aria-invalid')); }
function fail(err, fallback) {
  if (err && err.fieldId) { const n = $(err.fieldId); if (n) { n.setAttribute('aria-invalid', 'true'); n.focus(); } }
  setStatus((err && err.message) || fallback, 'error');
}

/* ---------- values helpers -------------------------------------------- */
function providerIds() { return Object.keys(state.values?.providers || {}); }
function providerOf(modelId) {
  const registered = state.values?.models?.[modelId];
  return registered?.provider && state.values.providers?.[registered.provider] ? registered.provider : state.values?.defaultProvider || '';
}
function modelsOf(pid) { return Object.keys(state.values?.models || {}).filter(id => providerOf(id) === pid).sort(); }
function schemaField(key) {
  return (state.schema?.sections || []).flatMap(s => s.fields || []).find(f => f.key === key) || null;
}
function effortChoices() { return schemaField('provider.reasoningEffort')?.choices || ['low', 'high', 'max']; }
function effortDefault() { return schemaField('provider.reasoningEffort')?.default || 'high'; }
function limitRange(key) { const f = schemaField('limits.' + key); return f ? [f.min, f.max] : FALLBACK_LIMITS[key]; }
const fmtMoney = n => '$' + (Number.isFinite(n) ? String(n) : '0');
const fmtInt = n => Number.isFinite(n) ? n.toLocaleString('vi-VN') : '';

/* ---------- parse + validate ------------------------------------------ */
function parseBaseUrl(raw, fieldId) {
  const value = String(raw || '').trim().replace(/\/+$/, '');
  let url;
  try { url = new URL(value); } catch { throw new FieldError('Base URL phải là URL hợp lệ.', fieldId); }
  if (!['http:', 'https:'].includes(url.protocol)) throw new FieldError('Base URL phải bắt đầu bằng http:// hoặc https://.', fieldId);
  return value;
}
function intOrNull(input, label, range) {
  const raw = input.value.trim();
  if (!raw) return null;                                   // blank = inherit
  const n = Number(raw), [min, max] = range;
  if (!Number.isInteger(n) || n < min || n > max) throw new FieldError(`${label} phải là số nguyên ${min}–${max}.`, '#' + input.id);
  return n;
}
/* Prices mirror the old rule: all three set, or all three blank (backend
   pricing). A partial set is a form error, never a silent 0. */
function parsePrices() {
  const fields = [$('#m-in'), $('#m-cache'), $('#m-out')];
  const raws = fields.map(f => f.value.trim());
  if (raws.every(r => r === '')) return null;
  const emptyIndex = raws.findIndex(r => r === '');
  if (emptyIndex >= 0) throw new FieldError('Điền đủ cả ba giá Input, Cache và Output; hoặc để trống cả ba.', '#' + fields[emptyIndex].id);
  const nums = raws.map(Number);
  const badIndex = nums.findIndex(n => !Number.isFinite(n) || n < 0);
  if (badIndex >= 0) throw new FieldError('Giá phải là số lớn hơn hoặc bằng 0.', '#' + fields[badIndex].id);
  const [input, cache, output] = nums;
  return { input, cache, output };
}

/* ---------- renders ---------------------------------------------------- */
function fillOptions(select, options, current) {
  select.replaceChildren(...options.map(([value, text]) => Object.assign(document.createElement('option'), { value, textContent: text })));
  if (options.some(([value]) => value === current)) select.value = current;
}

function renderProviders() {
  const list = $('#provider-list'); if (!list || !state.values) return;
  const ids = providerIds().sort();
  if (!ids.length) {
    list.innerHTML = '<li class="empty-note"><strong>Chưa có provider nào.</strong>Thêm provider đầu tiên bằng form phía dưới.</li>';
    return;
  }
  list.innerHTML = ids.map(id => {
    const p = state.values.providers[id] || {};
    const hasKey = !!state.meta.providers?.[id];
    const isDefault = id === state.values.defaultProvider;
    const test = state.testing[id];
    return `<li class="entry-row" data-default="${isDefault}">
      <div class="entry-main">
        <div class="entry-heading">
          <span class="entry-name">${escapeHTML(id)}</span>
          ${isDefault ? `<span class="chip" data-active="true">${icon('star')}Mặc định</span>` : ''}
          <span class="chip key-chip ${hasKey ? 'has-key' : 'no-key'}">${hasKey ? 'Đã có key' : 'Chưa có key'}</span>
        </div>
        <div class="entry-meta">
          <span class="entry-url">${escapeHTML(p.baseUrl || '—')}</span>
          <span>${escapeHTML(API_LABELS[p.api] || p.api || '—')}</span>
          <span>suy luận ${escapeHTML(p.reasoningEffort || '—')}</span>
          <span>${modelsOf(id).length} model</span>
        </div>
        <span class="test-result ${test ? (test.pending ? '' : test.ok ? 's-ok' : 's-error') : ''}" role="status">${test ? (test.pending ? 'Đang kiểm tra…' : escapeHTML(test.text)) : ''}</span>
      </div>
      <div class="row-actions">
        <button class="quiet-button" data-action="test" data-id="${escapeHTML(id)}" ${test?.pending ? 'disabled' : ''}>${icon('check')}Kiểm tra</button>
        <button class="quiet-button" data-action="edit" data-id="${escapeHTML(id)}">Sửa</button>
        <button class="quiet-button is-danger" data-action="delete" data-id="${escapeHTML(id)}">Xóa</button>
      </div>
    </li>`;
  }).join('');
}

function renderModels() {
  const list = $('#model-list'); if (!list || !state.values) return;
  const ids = Object.keys(state.values.models || {}).sort();
  if (!ids.length) {
    list.innerHTML = '<li class="empty-note"><strong>Chưa có model nào.</strong>Thêm model bằng form phía dưới, hoặc kiểm tra nhanh để tải danh sách model của provider.</li>';
    return;
  }
  list.innerHTML = ids.map(id => {
    const m = state.values.models[id] || {};
    const isDefault = id === state.values.defaultModel;
    const priced = [m.input, m.cache, m.output].some(n => Number.isFinite(n) && n > 0);
    const price = priced ? `in ${fmtMoney(m.input)} · cache ${fmtMoney(m.cache)} · out ${fmtMoney(m.output)}/1M` : 'giá backend';
    const limits = [
      Number.isInteger(m.contextTokens) ? `${fmtInt(m.contextTokens)} tok` : '',
      Number.isInteger(m.loopMax) ? `${m.loopMax} vòng` : '',
      Number.isInteger(m.hotTailKB) ? `nhớ nóng ${m.hotTailKB}KB` : '',
    ].filter(Boolean).join(' · ');
    const effort = m.reasoningEffort ? `suy luận ${m.reasoningEffort}` : 'suy luận kế thừa';
    const efforts = (m.reasoningEfforts || []).length ? `map: ${m.reasoningEfforts.join(', ')}` : '';
    const baseUrl = m.baseUrl ? `endpoint riêng: ${m.baseUrl}` : '';
    return `<li class="entry-row" data-default="${isDefault}">
      <div class="entry-main">
        <div class="entry-heading">
          <span class="entry-name">${escapeHTML(id)}</span>
          ${isDefault ? `<span class="chip" data-active="true">${icon('star')}Mặc định</span>` : ''}
        </div>
        <div class="entry-meta">
          <span>provider: ${escapeHTML(providerOf(id) || '—')}</span>
          <span>${escapeHTML(price)}</span>
          ${limits ? `<span>${escapeHTML(limits)}</span>` : ''}
          <span>${escapeHTML(effort)}</span>
          ${efforts ? `<span>${escapeHTML(efforts)}</span>` : ''}
          ${baseUrl ? `<span class="entry-url">${escapeHTML(baseUrl)}</span>` : ''}
        </div>
      </div>
      <div class="row-actions">
        <button class="quiet-button" data-action="edit" data-id="${escapeHTML(id)}">Sửa</button>
        <button class="quiet-button is-danger" data-action="delete" data-id="${escapeHTML(id)}">Xóa</button>
      </div>
    </li>`;
  }).join('');
}

/* Selects that mirror the draft: defaults get the saved value, free selects
   keep whatever the user already picked. */
function renderSelects() {
  const ids = providerIds().sort();
  const defP = $('#cfg-default-provider');
  if (defP) { fillOptions(defP, ids.map(id => [id, id])); defP.value = state.values?.defaultProvider || ''; }
  const mids = Object.keys(state.values?.models || {}).sort();
  const defM = $('#cfg-default-model');
  if (defM) {
    const all = [...new Set([state.values?.defaultModel, ...mids].filter(Boolean))];
    fillOptions(defM, all.map(id => [id, mids.includes(id) ? id : id + ' (chưa đăng ký)']));
    defM.value = state.values?.defaultModel || '';
  }
  const mp = $('#m-provider');
  if (mp) fillOptions(mp, [['', `Kế thừa (${state.values?.defaultProvider || 'mặc định'})`], ...ids.map(id => [id, id])], mp.value);
  const vp = $('#v-provider');
  if (vp) fillOptions(vp, [['', '— nhập key tay —'], ...ids.map(id => [id, id])], vp.value);
  const pe = $('#pv-effort');
  if (pe) fillOptions(pe, effortChoices().map(c => [c, c]), pe.value || effortDefault());
}

function renderAll() { renderProviders(); renderModels(); renderSelects(); }

/* ---------- API: GET /api/config --------------------------------------- */
async function refreshConfig() {
  const payload = await getJson('/api/config');                 // API: GET /api/config
  state.schema = payload.schema || {};
  state.values = payload.values || {};
  state.values.providers = state.values.providers || {};
  state.values.models = state.values.models || {};
  state.meta = payload.meta || {};
  renderAll();
}

/* ---------- API: POST /api/config -------------------------------------- */
async function saveConfig(note) {
  if (state.saving) return false;
  state.saving = true;
  const btn = $('#save-config'); if (btn) btn.disabled = true;
  setStatus('Đang lưu cấu hình…');
  try {
    const res = await postJson('/api/config', state.values);    // API: POST /api/config — whole values; empty apiKey keeps the stored key
    await refreshConfig();                                      // fresh masked keys + meta.providers
    const readyNote = res.ready ? 'Sẵn sàng để dùng.' : 'Chưa sẵn sàng — kiểm tra key của provider mặc định.';
    setStatus(`${note || 'Đã lưu cấu hình.'} ${readyNote}`, res.ready ? 'ok' : 'error');
    toast(note || 'Đã lưu cấu hình.');
    announce(note || 'Đã lưu cấu hình.');
    return true;
  } catch (err) {
    setStatus('Không lưu được cấu hình: ' + err.message, 'error');
    return false;
  } finally {
    state.saving = false;
    if (btn) btn.disabled = false;
  }
}

/* ---------- API: POST /api/providers/test ------------------------------ */
async function testProvider(pid) {
  if (!state.values || state.testing[pid]?.pending) return;
  const payload = { providerId: pid };
  const owned = modelsOf(pid);
  if (owned[0]) payload.model = owned[0];          // else the backend falls back to defaultModel
  state.testing[pid] = { pending: true };
  renderProviders();
  try {
    const res = await postJson('/api/providers/test', payload);   // API: POST /api/providers/test
    state.testing[pid] = { ok: true, text: `OK · ${res.model} · ${res.latencyMs}ms` };
    setStatus(`Provider "${pid}" hoạt động — ${res.model} · ${res.latencyMs}ms.`, 'ok');
  } catch (err) {
    state.testing[pid] = { ok: false, text: err.message };
    setStatus(`Kiểm tra "${pid}" thất bại: ${err.message}`, 'error');
  }
  renderProviders();
}

/* ---------- API: POST /api/onboarding/verify --------------------------- */
async function verifyQuick(e) {
  e.preventDefault();
  if (state.verifying) return;
  clearFieldErrors($('#verify-form'));
  let baseUrl;
  try { baseUrl = parseBaseUrl($('#v-base-url').value, '#v-base-url'); }
  catch (err) { fail(err, 'Base URL chưa đúng.'); return; }
  const pid = $('#v-provider').value;
  const apiKey = $('#v-key').value.trim();
  const payload = { baseUrl };                                     // API: POST /api/onboarding/verify
  if (pid) payload.providerId = pid;                               // blank key + providerId -> stored key
  if (apiKey) payload.apiKey = apiKey;
  state.verifying = true; state.verifyProvider = pid;
  const btn = $('#v-submit'); btn.disabled = true;
  const box = $('#verify-result'); box.hidden = false;
  const status = $('#verify-status');
  status.className = 'status-line'; status.textContent = 'Đang kiểm tra…';
  $('#verify-models').innerHTML = ''; $('#verify-hint').hidden = true;
  try {
    const res = await postJson('/api/onboarding/verify', payload);
    const found = Array.isArray(res.models) ? res.models : [];
    status.className = 'status-line s-ok';
    status.textContent = found.length ? `Kết nối thành công · tìm thấy ${found.length} model.` : 'Kết nối thành công · provider không trả danh sách model.';
    $('#verify-models').innerHTML = found.map(id => `<button type="button" class="chip" data-model="${escapeHTML(id)}">${escapeHTML(id)}</button>`).join('');
    $('#verify-hint').hidden = !found.length;
    announce('Kết nối thành công.');
  } catch (err) {
    status.className = 'status-line s-error';
    status.textContent = 'Kiểm tra thất bại: ' + err.message;
    announce('Kiểm tra thất bại.');
  } finally {
    state.verifying = false; btn.disabled = false;
  }
}

/* ---------- provider form ---------------------------------------------- */
function fillProviderForm(id) {
  const p = state.values?.providers?.[id]; if (!p) return;
  state.editingProvider = id;
  $('#provider-form-title').textContent = `Sửa provider "${id}"`;
  $('#pv-id').value = id; $('#pv-id').disabled = true;            // id is the map key: renaming means delete + add
  $('#pv-base-url').value = p.baseUrl || '';
  $('#pv-api').value = p.api === 'openai_responses' ? 'openai_responses' : 'openai_chat';
  fillOptions($('#pv-effort'), effortChoices().map(c => [c, c]), p.reasoningEffort || effortDefault());
  const key = $('#pv-key'); key.value = '';
  key.placeholder = state.meta.providers?.[id] ? '••• đã lưu' : 'sk-…';
  $('#pv-key-note').textContent = state.meta.providers?.[id] ? 'để trống để giữ key đã lưu' : 'key chỉ gửi về máy chủ Thyca trên máy bạn';
  $('#pv-submit').innerHTML = icon('check') + ' Lưu provider';
  $('#pv-cancel').hidden = false;
  clearFieldErrors($('#provider-form'));
  $('#provider-form').scrollIntoView({ block: 'nearest', behavior: motionPaused ? 'auto' : 'smooth' });
  $('#pv-base-url').focus();
}
function resetProviderForm() {
  state.editingProvider = '';
  $('#provider-form').reset();
  $('#pv-id').disabled = false;
  $('#provider-form-title').textContent = 'Thêm provider';
  $('#pv-submit').innerHTML = icon('plus') + ' Thêm provider';
  $('#pv-cancel').hidden = true;
  $('#pv-key').placeholder = 'sk-…';
  $('#pv-key-note').textContent = 'key chỉ gửi về máy chủ Thyca trên máy bạn';
  fillOptions($('#pv-effort'), effortChoices().map(c => [c, c]), effortDefault());
  clearFieldErrors($('#provider-form'));
}
async function submitProvider(e) {
  e.preventDefault();
  if (state.saving || !state.values) return;
  clearFieldErrors($('#provider-form'));
  try {
    const editing = state.editingProvider;
    const id = editing || $('#pv-id').value.trim();
    if (!id) throw new FieldError('Tên provider không được để trống.', '#pv-id');
    if (!PROVIDER_ID_RE.test(id)) throw new FieldError('Tên provider chỉ gồm chữ, số, _ và -.', '#pv-id');
    if (!editing && state.values.providers[id]) throw new FieldError(`Provider "${id}" đã tồn tại — bấm Sửa ở danh sách để chỉnh.`, '#pv-id');
    const baseUrl = parseBaseUrl($('#pv-base-url').value, '#pv-base-url');
    const api = $('#pv-api').value === 'openai_responses' ? 'openai_responses' : 'openai_chat';
    const effort = $('#pv-effort').value;
    if (!effort) throw new FieldError('Chọn mức suy luận mặc định.', '#pv-effort');
    const apiKey = $('#pv-key').value.trim();
    const prev = state.values.providers[id] || {};
    const entry = { apiKeyEnv: 'THYCA_TOKEN', ...prev, baseUrl, api, reasoningEffort: effort, apiKey };
    // apiKey '' merges back the stored key server-side (merge_saved_keys).
    state.values.providers = { ...state.values.providers, [id]: entry };
    if (await saveConfig(editing ? `Đã lưu provider "${id}".` : `Đã thêm provider "${id}".`)) resetProviderForm();
  } catch (err) {
    fail(err, 'Không lưu được provider.');
  }
}
function deleteProvider(id) {
  if (!state.values?.providers?.[id]) return;
  const ids = providerIds();
  if (ids.length <= 1) { setStatus('Không xóa được provider cuối cùng.', 'error'); return; }
  const owned = modelsOf(id);
  if (owned.length) { setStatus(`"${id}" còn ${owned.length} model (${owned.join(', ')}) — chuyển model sang provider khác trước.`, 'error'); return; }
  if (!window.confirm(`Xóa provider "${id}"?`)) return;
  const values = state.values;
  delete values.providers[id];
  if (values.defaultProvider === id) values.defaultProvider = Object.keys(values.providers).sort()[0];
  if (state.editingProvider === id) resetProviderForm();
  delete state.testing[id];
  renderAll();
  saveConfig(`Đã xóa provider "${id}".`);
}

/* ---------- model form -------------------------------------------------- */
function modelEffortOptions(modelId) {
  // A model with its own reasoningEfforts map may only pin a level from it.
  const own = state.values?.models?.[modelId]?.reasoningEfforts;
  return own && own.length ? own : effortChoices();
}
function fillModelForm(id) {
  const m = state.values?.models?.[id]; if (!m) return;
  state.editingModel = id;
  $('#model-form-title').textContent = `Sửa model "${id}"`;
  $('#m-id').value = id; $('#m-id').disabled = true;
  $('#m-provider').value = m.provider || '';
  fillOptions($('#m-effort'), [['', 'Kế thừa'], ...modelEffortOptions(id).map(c => [c, c])], m.reasoningEffort || '');
  $('#m-context').value = Number.isInteger(m.contextTokens) ? m.contextTokens : '';
  $('#m-loop').value = Number.isInteger(m.loopMax) ? m.loopMax : '';
  $('#m-hot').value = Number.isInteger(m.hotTailKB) ? m.hotTailKB : '';
  const priced = [m.input, m.cache, m.output].some(n => Number.isFinite(n) && n > 0);
  $('#m-in').value = priced ? m.input : '';
  $('#m-cache').value = priced ? m.cache : '';
  $('#m-out').value = priced ? m.output : '';
  $('#m-submit').innerHTML = icon('check') + ' Lưu model';
  $('#m-cancel').hidden = false;
  clearFieldErrors($('#model-form'));
  $('#model-form').scrollIntoView({ block: 'nearest', behavior: motionPaused ? 'auto' : 'smooth' });
  $('#m-provider').focus();
}
function resetModelForm() {
  state.editingModel = '';
  $('#model-form').reset();
  $('#m-id').disabled = false;
  $('#model-form-title').textContent = 'Thêm / sửa model';
  $('#m-submit').innerHTML = icon('plus') + ' Thêm model';
  $('#m-cancel').hidden = true;
  fillOptions($('#m-effort'), [['', 'Kế thừa'], ...effortChoices().map(c => [c, c])], '');
  clearFieldErrors($('#model-form'));
}
function prefillModel(modelId) {
  resetModelForm();
  $('#m-id').value = modelId;
  if (state.verifyProvider && state.values?.providers?.[state.verifyProvider]) $('#m-provider').value = state.verifyProvider;
  $('#model-form').scrollIntoView({ block: 'nearest', behavior: motionPaused ? 'auto' : 'smooth' });
  $('#m-context').focus();
  setStatus(`Đã điền "${modelId}" vào form model — kiểm tra các ô rồi bấm Thêm model.`, 'ok');
}
async function submitModel(e) {
  e.preventDefault();
  if (state.saving || !state.values) return;
  clearFieldErrors($('#model-form'));
  try {
    const editing = state.editingModel;
    const id = editing || $('#m-id').value.trim();
    if (!id) throw new FieldError('Nhập Model ID trước.', '#m-id');
    const provider = $('#m-provider').value;
    const effort = $('#m-effort').value;
    const contextTokens = intOrNull($('#m-context'), 'Cửa sổ ngữ cảnh', limitRange('contextTokens'));
    const loopMax = intOrNull($('#m-loop'), 'Số vòng', limitRange('loopMax'));
    const hotTailKB = intOrNull($('#m-hot'), 'Nhớ nóng', limitRange('hotTailKB'));
    const prices = parsePrices();
    const prev = (state.values.models || {})[id] || {};
    const entry = { ...prev };                                   // keeps baseUrl, reasoningEfforts and any stored extras
    if (provider) entry.provider = provider; else delete entry.provider;
    if (contextTokens != null) entry.contextTokens = contextTokens; else delete entry.contextTokens;
    if (loopMax != null) entry.loopMax = loopMax; else delete entry.loopMax;
    if (hotTailKB != null) entry.hotTailKB = hotTailKB; else delete entry.hotTailKB;
    if (effort) entry.reasoningEffort = effort; else delete entry.reasoningEffort;
    const values = state.values;
    if (prices) {
      entry.input = prices.input; entry.cache = prices.cache; entry.output = prices.output;
      values.pricing = { ...(values.pricing || {}), [id]: { ...prices } };
    } else {
      delete entry.input; delete entry.cache; delete entry.output;
      if (values.pricing) delete values.pricing[id];
    }
    values.models = { ...(values.models || {}), [id]: entry };
    if (await saveConfig(editing ? `Đã lưu model "${id}".` : `Đã thêm model "${id}".`)) resetModelForm();
  } catch (err) {
    fail(err, 'Không lưu được model.');
  }
}
function deleteModel(id) {
  if (!state.values?.models?.[id]) return;
  if (id === state.values.defaultModel) { setStatus(`"${id}" đang là model mặc định — đặt model khác làm mặc định trước.`, 'error'); return; }
  if (!window.confirm(`Xóa model "${id}"?`)) return;
  delete state.values.models[id];
  if (state.values.pricing) delete state.values.pricing[id];
  if (state.editingModel === id) resetModelForm();
  renderAll();
  saveConfig(`Đã xóa model "${id}".`);
}

/* ---------- page bindings + boot ---------------------------------------- */
function bind() {
  $('#save-config').onclick = () => saveConfig();
  $('#reload-config').onclick = async () => {
    try { await refreshConfig(); setStatus('Đã tải lại cấu hình.', 'ok'); }
    catch (err) { setStatus('Không tải được cấu hình: ' + err.message, 'error'); }
  };
  $('#provider-form').onsubmit = submitProvider;
  $('#pv-cancel').onclick = resetProviderForm;
  $('#model-form').onsubmit = submitModel;
  $('#m-cancel').onclick = resetModelForm;
  $('#verify-form').onsubmit = verifyQuick;
  $('#v-provider').onchange = e => { const p = state.values?.providers?.[e.target.value]; if (p?.baseUrl) $('#v-base-url').value = p.baseUrl; };
  $('#provider-list').addEventListener('click', e => {
    const btn = e.target.closest('button[data-action]'); if (!btn) return;
    const id = btn.dataset.id;
    if (btn.dataset.action === 'test') testProvider(id);
    else if (btn.dataset.action === 'edit') fillProviderForm(id);
    else if (btn.dataset.action === 'delete') deleteProvider(id);
  });
  $('#model-list').addEventListener('click', e => {
    const btn = e.target.closest('button[data-action]'); if (!btn) return;
    const id = btn.dataset.id;
    if (btn.dataset.action === 'edit') fillModelForm(id);
    else if (btn.dataset.action === 'delete') deleteModel(id);
  });
  $('#verify-models').addEventListener('click', e => {
    const chip = e.target.closest('[data-model]'); if (chip) prefillModel(chip.dataset.model);
  });
  // API: POST /api/config — both defaults ride the same whole-values payload.
  $('#cfg-default-provider').onchange = e => { state.values.defaultProvider = e.target.value; saveConfig(`Provider mặc định: "${e.target.value}".`); };
  $('#cfg-default-model').onchange = e => { state.values.defaultModel = e.target.value; saveConfig(`Model mặc định: "${e.target.value}".`); };
}

function selectBot(id) {
  if (id && getBot(id)) ctx.navigate('profile', { search: '?bot=' + encodeURIComponent(id) });
}
function loadSession(id, title, botId) { if (botId) selectBot(botId); }
function labelProfileLinks() {
  if (!ctx.isActive()) return;
  for (const button of [...ctx.sidebar.querySelectorAll('[data-action="select"]')]) {
    const bot = getBot(button.dataset.botId);
    if (bot) button.setAttribute('aria-label', `Hồ sơ của ${bot.name}`);
  }
}
new MutationObserver(labelProfileLinks).observe(ctx.sidebar, { childList: true, subtree: true });

async function boot() {
  bind();
  resetProviderForm();
  resetModelForm();
  try {
    await ctx.shellReady;                                          // API: GET /api/bots + GET /api/sessions -> shared sidebar
    labelProfileLinks();
  } catch (err) {
    setStatus('Không kết nối được máy chủ Thyca: ' + err.message, 'error');
  }
  try {
    await refreshConfig();                                      // API: GET /api/config -> provider/model cards
  } catch (err) {
    setStatus('Không tải được cấu hình: ' + err.message, 'error');
  }
}
  const ready = boot();
  return { ready, selectBot, loadSession, sidebarLabel(_id, name) { return `Hồ sơ của ${name}`; } };
} });
