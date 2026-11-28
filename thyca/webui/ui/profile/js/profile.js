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
const BASH_LABELS = { ask: 'Lệnh: hỏi trước', allow: 'Lệnh: tự chạy', deny: 'Lệnh: tắt' };
const BOT_LIMIT_DEFS = [
  { key: 'loopMax', label: 'Số vòng agent tối đa', min: 1, max: 200, def: 200 },
  { key: 'hotTailKB', label: 'Dung lượng nhớ nóng (KB)', min: 1, max: 64, def: 4 },
  { key: 'contextTokens', label: 'Ngữ cảnh (token)', min: 32000, max: 1000000, def: 272000 },
  { key: 'softTimeoutS', label: 'Soft timeout chờ tool (giây)', min: 1, max: 300, def: 60 },
];

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
};
const state = {
  files: [], tab: '', doc: '', busy: false, opener: null,
  editing: '', editBase: '', editDraft: '', editMode: 'split',
  sectionEdit: null, openSections: new Set(),
};
let bootTouched = false;
let rosterReady = false;

let botCatalog = null;
let botCatalogError = '';
let botSchemaChoices = ['low', 'medium', 'high', 'xhigh', 'max'];
let botLimitDefs = BOT_LIMIT_DEFS.map(item => ({ ...item }));
let botCatalogSeq = 0;
let botCatalogDirty = false;
const botCfg = { bot: '', settings: null, seq: 0, loading: false, savingFor: null, error: '' };
let botLoadedCfg = null;
const botDrafts = new Map();

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

function extractBotCatalog(payload) {
  const values = payload?.values || {};
  const sections = payload?.schema?.sections || [];
  const allFields = sections.flatMap(s => s.fields || []);
  const effortField = allFields.find(f => f.key === 'provider.reasoningEffort');
  botCatalog = {
    models: (values.models && typeof values.models === 'object') ? values.models : {},
    defaultModel: typeof values.defaultModel === 'string' ? values.defaultModel : '',
    defaultProvider: typeof values.defaultProvider === 'string' ? values.defaultProvider : '',
    providers: (values.providers && typeof values.providers === 'object') ? values.providers : {},
    limits: (values.limits && typeof values.limits === 'object') ? values.limits : {},
  };
  botSchemaChoices = Array.isArray(effortField?.choices) ? effortField.choices : ['low', 'medium', 'high', 'xhigh', 'max'];
  botLimitDefs = BOT_LIMIT_DEFS.map(item => {
    const schema = allFields.find(f => f.key === `limits.${item.key}`);
    return {
      key: item.key,
      label: item.label,
      min: Number.isInteger(schema?.min) ? schema.min : item.min,
      max: Number.isInteger(schema?.max) ? schema.max : item.max,
      def: Number.isInteger(schema?.default) ? schema.default : item.def,
    };
  });
}

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
    const dm = botCatalog?.defaultModel || '';
    const entry = dm ? models[dm] : null;
    return (entry?.provider && botCatalog?.providers?.[entry.provider]) ? entry.provider : defProvider;
  }
  if (!Object.hasOwn(models, modelId)) {
    return modelId === botCatalog?.defaultModel ? defProvider : '';
  }
  const entry = models[modelId];
  return (entry?.provider && botCatalog?.providers?.[entry.provider]) ? entry.provider : defProvider;
}

function botModelKnown(id) {
  const models = botCatalog?.models && typeof botCatalog.models === 'object' ? botCatalog.models : {};
  const def = botCatalog?.defaultModel || '';
  return id === 'default' || Object.hasOwn(models, id) || (!!def && id === def);
}

function hasCustomBotLimits(limits) {
  if (!limits || typeof limits !== 'object') return false;
  return BOT_LIMIT_DEFS.some(item => limits[item.key] !== null && limits[item.key] !== undefined && String(limits[item.key]).trim() !== '');
}

function normalizeBotLimitStrings(limits) {
  const out = {};
  for (const item of BOT_LIMIT_DEFS) {
    const raw = limits && typeof limits === 'object' ? limits[item.key] : null;
    out[item.key] = (raw === null || raw === undefined) ? '' : String(raw);
  }
  return out;
}

function botLimitsEqual(a, b) {
  return BOT_LIMIT_DEFS.every(item => String(a?.[item.key] ?? '') === String(b?.[item.key] ?? ''));
}

function botEffectiveLimitFallback(key, effective) {
  const eff = effective?.limits?.[key];
  if (Number.isInteger(eff)) return eff;
  const globalVal = botCatalog?.limits?.[key];
  if (Number.isInteger(globalVal)) return globalVal;
  return botLimitDefs.find(item => item.key === key)?.def ?? '';
}

function readBotLimitInputs() {
  const out = {};
  for (const item of BOT_LIMIT_DEFS) {
    const input = $(`#bot-limit-${item.key}`);
    out[item.key] = input ? String(input.value ?? '').trim() : '';
  }
  return out;
}

function loadedBotSnapshot(id, settings) {
  const configured = settings?.model || { id: 'default', thinking: null };
  return {
    bot: id,
    model: configured.id || 'default',
    thinking: configured.thinking ?? null,
    limits: normalizeBotLimitStrings(settings?.limits),
    advOpen: false,
  };
}

function botFormDraft(forBot = state.tab) {
  const m = $('#bot-model'), t = $('#bot-thinking');
  if (!m || !t || !botLoadedCfg || !forBot || botLoadedCfg.bot !== forBot) return null;
  const limits = readBotLimitInputs();
  const adv = $('#bot-advanced');
  const cur = { model: m.value, thinking: t.value || null, limits, advOpen: Boolean(adv?.open) };
  const changed = cur.model !== botLoadedCfg.model
    || cur.thinking !== botLoadedCfg.thinking
    || !botLimitsEqual(cur.limits, botLoadedCfg.limits);
  return changed ? cur : null;
}

function retainBotSettingsDraft(forBot = botCfg.bot || state.tab) {
  if (!botLoadedCfg?.bot || !forBot || botLoadedCfg.bot !== forBot) return;
  const draft = botFormDraft(forBot);
  if (draft) botDrafts.set(botLoadedCfg.bot, draft);
  else if ($('#bot-model')) botDrafts.delete(botLoadedCfg.bot);
}

function updateBotSave({ clearError = false } = {}) {
  const save = $('#save-bot-settings');
  const cancel = $('#cancel-bot-settings');
  const hint = $('#bot-settings-hint');
  const sel = $('#bot-model');
  if (!save) return;
  const active = state.tab;
  const dirty = Boolean(botFormDraft(active));
  const ready = !botCfg.loading && !botCfg.savingFor && !!active && !!getBot(active)
    && !!botCatalog && !!botCfg.settings && !!sel && botModelKnown(sel.value);
  save.disabled = !ready || !dirty;
  if (cancel) cancel.hidden = !dirty || botCfg.loading || !!botCfg.savingFor;
  if (hint && !botCfg.loading && !botCfg.savingFor) {
    if (dirty) {
      if (clearError || !hint.textContent || hint.textContent === 'Đã lưu.') hint.textContent = 'Chưa lưu';
    } else if (hint.textContent && hint.textContent !== 'Đã lưu.') {
      hint.textContent = '';
    }
  }
}

function botInheritedThinking(modelId, effective) {
  const pid = botDraftProviderId(modelId);
  const provEffort = pid ? (botCatalog?.providers?.[pid]?.reasoningEffort || '') : '';
  if (provEffort) return provEffort;
  if (botLoadedCfg && botLoadedCfg.thinking === null && modelId === botLoadedCfg.model) {
    return effective?.thinking || '';
  }
  return '';
}

function paintBotProviderPreview() {
  const sel = $('#bot-model'), thinkSel = $('#bot-thinking'), preview = $('#bot-provider-preview'), effEl = $('#bot-effective');
  if (!sel || !preview || !botCatalog) return;
  const selVal = sel.value || 'default';
  const thinkVal = thinkSel ? thinkSel.value : '';
  if (effEl) {
    const eff = botCfg.settings?.effective || {};
    const inherited = selVal === 'default' || !thinkVal;
    const effModel = selVal === 'default' ? (botCatalog.defaultModel || eff.model || '') : selVal;
    const effThinking = thinkVal || botInheritedThinking(selVal, eff);
    const text = effModel ? `Đang dùng: ${effModel}${effThinking ? ` · ${effThinking}` : ''}` : '';
    effEl.hidden = !inherited || !text || !botModelKnown(selVal);
    effEl.textContent = text;
  }
  if (!botModelKnown(selVal)) {
    preview.textContent = 'Provider: — (model không còn trong danh mục)';
    return;
  }
  const draftPid = botDraftProviderId(selVal);
  const savedPid = botCfg.settings?.effective?.provider || '';
  preview.textContent = draftPid
    ? (draftPid === savedPid ? `Provider: ${draftPid}` : `Provider: ${draftPid} (dự kiến — chưa lưu)`)
    : 'Provider: —';
}

function syncBotLimitsChrome() {
  const badge = $('#bot-limits-badge');
  const resetBtn = $('#bot-limits-reset');
  const custom = hasCustomBotLimits(readBotLimitInputs());
  if (badge) {
    badge.textContent = custom ? 'Tự cấu hình' : 'Mặc định';
    badge.dataset.custom = String(custom);
  }
  if (resetBtn) resetBtn.hidden = !custom;
}

function botFieldsHtml(modelId, thinking, effective, limitValues, advOpen) {
  const ids = botModelIds();
  const def = botCatalog.defaultModel || '';
  const known = botModelKnown(modelId);
  const list = known ? ids : [...ids, modelId];
  const choices = botEffortChoices(modelId === 'default' ? def : modelId);
  const effModel = modelId === 'default' ? (def || (effective && effective.model) || '') : modelId;
  const effThinking = thinking || botInheritedThinking(modelId, effective);
  const showEffective = known && (modelId === 'default' || !thinking) && Boolean(effModel);
  const effectiveText = effModel ? `Đang dùng: ${effModel}${effThinking ? ` · ${effThinking}` : ''}` : '';
  const initProvider = known
    ? (botDraftProviderId(modelId) || (effective && effective.provider) || '')
    : '';
  const initProviderLine = !known
    ? 'Provider: — (model không còn trong danh mục)'
    : (initProvider ? `Provider: ${initProvider}` : 'Provider: —');
  const custom = hasCustomBotLimits(limitValues);
  const limitsHtml = botLimitDefs.map(item => {
    const val = limitValues?.[item.key] ?? '';
    const placeholder = botEffectiveLimitFallback(item.key, effective);
    return `<label class="field" for="bot-limit-${item.key}"><span class="field-label-row"><span>${escapeHTML(item.label)}</span><small class="field-range">${item.min} – ${item.max}</small></span><input id="bot-limit-${item.key}" type="number" step="1" min="${item.min}" max="${item.max}" inputmode="numeric" value="${escapeHTML(val)}" placeholder="${escapeHTML(placeholder)}"></label>`;
  }).join('');
  return `
   <div class="profile-bot-fields">
    <label class="field" for="bot-model"><span>Model</span>
     <select id="bot-model">${list.map(id => `<option value="${escapeHTML(id)}"${id === modelId ? ' selected' : ''}>${escapeHTML(botModelLabel(id) + (!known && id === modelId ? ' (không còn trong danh mục)' : ''))}</option>`).join('')}</select></label>
    <label class="field" for="bot-thinking"><span>Mức suy luận</span>
     <select id="bot-thinking"><option value="">Theo model/provider</option>${choices.map(c => `<option value="${escapeHTML(c)}"${String(thinking) === String(c) ? ' selected' : ''}>${escapeHTML(c)}</option>`).join('')}</select></label>
   </div>
   <p class="field-hint" id="bot-model-notice"${known ? ' hidden' : ''}>${known ? '' : 'Model đã lưu không còn trong danh mục — chọn model khác rồi lưu.'}</p>
   <div class="bot-model-meta">
    <span class="bot-provider-line" id="bot-provider-preview">${escapeHTML(initProviderLine)}</span>
    <span class="bot-effective" id="bot-effective"${showEffective ? '' : ' hidden'}>${escapeHTML(effectiveText)}</span>
   </div>
   <details class="profile-bot-advanced" id="bot-advanced"${advOpen ? ' open' : ''}>
    <summary class="profile-adv-toggle"><span>Nâng cao</span><span class="profile-adv-badge" id="bot-limits-badge" data-custom="${custom}">${custom ? 'Tự cấu hình' : 'Mặc định'}</span></summary>
    <div class="profile-limits-body">
     <div class="profile-limits-grid" id="bot-limits-fields">${limitsHtml}</div>
     <div class="profile-limits-foot">
      <span class="profile-limits-note">Để trống để dùng mặc định hệ thống.</span>
      <button type="button" class="quiet-button profile-limits-reset" id="bot-limits-reset"${custom ? '' : ' hidden'}>Dùng mặc định</button>
     </div>
    </div>
   </details>`;
}

function bindBotSettingsControls() {
  const form = $('#bot-settings-form');
  if (form) form.onsubmit = e => { e.preventDefault(); void submitBotSettings(); };
  const def = botCatalog?.defaultModel || '';
  const modelSel = $('#bot-model');
  const thinkSel = $('#bot-thinking');
  const cancelBtn = $('#cancel-bot-settings');
  if (cancelBtn) {
    cancelBtn.onclick = () => {
      if (state.tab) botDrafts.delete(state.tab);
      renderBotSettingsForm();
    };
  }
  if (!modelSel) return;
  modelSel.onchange = () => {
    const next = $('#bot-thinking');
    const keep = next.value;
    const list = botEffortChoices(modelSel.value === 'default' ? def : modelSel.value);
    next.replaceChildren(
      Object.assign(document.createElement('option'), { value: '', textContent: 'Theo model/provider' }),
      ...list.map(c => Object.assign(document.createElement('option'), { value: c, textContent: c })),
    );
    next.value = list.includes(keep) ? keep : '';
    paintBotProviderPreview();
    updateBotSave({ clearError: true });
  };
  if (thinkSel) {
    thinkSel.onchange = () => {
      paintBotProviderPreview();
      updateBotSave({ clearError: true });
    };
  }
  for (const item of BOT_LIMIT_DEFS) {
    const input = $(`#bot-limit-${item.key}`);
    if (input) input.oninput = () => { syncBotLimitsChrome(); updateBotSave({ clearError: true }); };
  }
  const resetBtn = $('#bot-limits-reset');
  if (resetBtn) {
    resetBtn.onclick = () => {
      for (const item of BOT_LIMIT_DEFS) {
        const input = $(`#bot-limit-${item.key}`);
        if (input) input.value = '';
      }
      syncBotLimitsChrome();
      updateBotSave({ clearError: true });
    };
  }
  syncBotLimitsChrome();
  paintBotProviderPreview();
  updateBotSave();
}

function renderBotSettingsForm() {
  const host = $('#bot-settings-fields');
  const hint = $('#bot-settings-hint');
  if (!host || !hint) return;
  const id = state.tab;
  const b = id && getBot(id);
  if (!b || !botCatalog) {
    host.innerHTML = !b
      ? ''
      : botCatalogError
        ? `<p class="empty-note"><strong>Không tải được danh mục model.</strong> ${escapeHTML(botCatalogError)}</p>`
        : '<p class="empty-note">Đang tải danh mục model…</p>';
    hint.textContent = '';
    updateBotSave();
    return;
  }
  const s = botCfg.settings;
  if (!s || botCfg.bot !== id) {
    host.innerHTML = botCfg.loading
      ? '<p class="empty-note">Đang tải cài đặt bot…</p>'
      : `<p class="empty-note"><strong>Không tải được cài đặt bot.</strong>${escapeHTML(botCfg.error || 'Máy chủ bận.')} — thử lại sau.</p>`;
    hint.textContent = '';
    updateBotSave();
    return;
  }
  const snap = loadedBotSnapshot(id, s);
  botLoadedCfg = snap;
  host.innerHTML = botFieldsHtml(snap.model, snap.thinking ?? '', s.effective || {}, snap.limits, false);
  const modelSel = $('#bot-model'), thinkSel = $('#bot-thinking');
  if (modelSel) modelSel.value = snap.model;
  if (thinkSel) thinkSel.value = snap.thinking ?? '';
  for (const item of BOT_LIMIT_DEFS) {
    const input = $(`#bot-limit-${item.key}`);
    if (input) input.value = snap.limits[item.key];
  }
  hint.textContent = '';
  bindBotSettingsControls();
}

function fillBotSettingsFields(settings, draft) {
  const host = $('#bot-settings-fields');
  const hint = $('#bot-settings-hint');
  if (!host || !hint || !settings || !botCatalog) { renderBotSettingsForm(); return; }
  const id = state.tab;
  const snap = loadedBotSnapshot(id, settings);
  botLoadedCfg = snap;
  const limits = draft.limits ? normalizeBotLimitStrings(draft.limits) : snap.limits;
  const advOpen = Boolean(draft.advOpen);
  host.innerHTML = botFieldsHtml(draft.model, draft.thinking ?? '', settings.effective || {}, limits, advOpen);
  const modelSel = $('#bot-model'), thinkSel = $('#bot-thinking');
  if (modelSel) modelSel.value = draft.model;
  if (thinkSel) thinkSel.value = draft.thinking ?? '';
  for (const item of BOT_LIMIT_DEFS) {
    const input = $(`#bot-limit-${item.key}`);
    if (input) input.value = limits[item.key];
  }
  hint.textContent = '';
  bindBotSettingsControls();
}

function repaintBotSettings() {
  if (!state.tab || !getBot(state.tab) || botCfg.savingFor) return;
  const current = state.tab;
  if (botCfg.bot !== current) return;
  const formLive = !!$('#bot-model') && botLoadedCfg?.bot === current;
  const draft = formLive ? botFormDraft(current) : (botDrafts.get(current) || null);
  if (draft && draft.model && botCfg.settings) fillBotSettingsFields(botCfg.settings, draft);
  else renderBotSettingsForm();
}

async function loadBotCatalog() {
  const seq = ++botCatalogSeq;
  try {
    const data = await getJson('/api/config');
    if (seq !== botCatalogSeq) return;
    botCatalogError = '';
    extractBotCatalog(data);
    repaintBotSettings();
  } catch (err) {
    if (seq !== botCatalogSeq) return;
    botCatalogError = err?.message || 'Máy chủ bận.';
    repaintBotSettings();
  }
}

async function loadBotSettings(id, preserved = null) {
  if (!id || !getBot(id)) {
    botCfg.bot = '';
    botCfg.settings = null;
    botLoadedCfg = null;
    botCfg.loading = false;
    renderBotSettingsForm();
    return;
  }
  const seq = ++botCfg.seq;
  botCfg.bot = id;
  botCfg.loading = true;
  botCfg.settings = null;
  botCfg.error = '';
  botLoadedCfg = null;
  renderBotSettingsForm();
  const save = $('#save-bot-settings');
  if (save) save.disabled = true;
  const hint = $('#bot-settings-hint');
  if (hint) hint.textContent = 'Đang tải…';
  try {
    const data = await getJson(`/api/bots/${encodeURIComponent(id)}/settings`);
    if (seq !== botCfg.seq || state.tab !== id) return;
    botCfg.settings = data;
  } catch (err) {
    if (seq !== botCfg.seq || state.tab !== id) return;
    botCfg.error = err.message;
  } finally {
    if (seq === botCfg.seq && state.tab === id) {
      botCfg.loading = false;
      if (botCfg.settings) botLoadedCfg = loadedBotSnapshot(id, botCfg.settings);
      const draft = preserved || botDrafts.get(id) || null;
      if (draft && botCfg.settings) fillBotSettingsFields(botCfg.settings, draft);
      else renderBotSettingsForm();
    }
  }
}

function parseBotLimitsPayload() {
  const hadCustom = hasCustomBotLimits(botCfg.settings?.limits);
  const parsed = {};
  let anySet = false;
  for (const item of botLimitDefs) {
    const input = $(`#bot-limit-${item.key}`);
    if (input?.validity?.badInput) {
      return { ok: false, error: `${item.label} phải là số nguyên (${item.min} – ${item.max}).` };
    }
    const raw = input ? String(input.value ?? '').trim() : '';
    if (!raw) {
      parsed[item.key] = null;
      continue;
    }
    if (!/^-?\d+$/.test(raw)) {
      return { ok: false, error: `${item.label} phải là số nguyên (${item.min} – ${item.max}).` };
    }
    const num = Number(raw);
    if (!Number.isSafeInteger(num) || num < item.min || num > item.max) {
      return { ok: false, error: `${item.label} chỉ nhận từ ${item.min} đến ${item.max}.` };
    }
    parsed[item.key] = num;
    anySet = true;
  }
  if (!anySet) {
    return hadCustom ? { ok: true, include: true, value: null } : { ok: true, include: false, value: null };
  }
  return { ok: true, include: true, value: parsed };
}

async function submitBotSettings() {
  const id = state.tab;
  const b = id && getBot(id);
  const modelSel = $('#bot-model'), thinkSel = $('#bot-thinking');
  if (!id || !b || !botCatalog || !modelSel || !thinkSel) return;
  if (!botCfg.settings || botCfg.loading || botCfg.savingFor || !botModelKnown(modelSel.value)) return;
  const limitsCheck = parseBotLimitsPayload();
  const hint = $('#bot-settings-hint');
  if (!limitsCheck.ok) {
    const adv = $('#bot-advanced');
    if (adv) adv.open = true;
    if (hint) hint.textContent = limitsCheck.error;
    return;
  }
  const seq = ++botCfg.seq;
  botCfg.savingFor = { bot: id, seq };
  const save = $('#save-bot-settings');
  if (save) save.disabled = true;
  if (hint) hint.textContent = 'Đang lưu…';
  const payload = { model: { id: modelSel.value, thinking: thinkSel.value || null } };
  if (limitsCheck.include) payload.limits = limitsCheck.value;
  try {
    const data = await postJson(`/api/bots/${encodeURIComponent(id)}/settings`, payload);
    botDrafts.delete(id);
    const live = getBot(id);
    if (live) {
      live.model = data.model;
      live.limits = data.limits;
      ctx.renderTeam();
      if (typeof renderHeaderBots === 'function') renderHeaderBots();
    }
    window.dispatchEvent(new CustomEvent('thyca:bot-settings-saved', { detail: { botId: id, model: data.model, limits: data.limits, effective: data.effective } }));
    if (seq !== botCfg.seq || state.tab !== id) return;
    botCfg.settings = data;
    renderBotSettingsForm();
    const h = $('#bot-settings-hint');
    if (h) h.textContent = 'Đã lưu.';
    toast(`Đã lưu cài đặt cho ${b.name}.`);
  } catch (err) {
    if (seq !== botCfg.seq || state.tab !== id) return;
    const h = $('#bot-settings-hint');
    if (h) h.textContent = 'Không lưu được: ' + err.message;
  } finally {
    if (botCfg.savingFor && botCfg.savingFor.seq === seq) {
      botCfg.savingFor = null;
      if (state.tab === id) {
        const h = $('#bot-settings-hint');
        if (h && h.textContent === 'Đang lưu…') {
          h.textContent = '';
          renderBotSettingsForm();
        } else {
          updateBotSave();
        }
      } else {
        updateBotSave();
      }
    }
    if (botCatalogDirty && !botCfg.savingFor && ctx.isActive()) {
      botCatalogDirty = false;
      void loadBotCatalog();
    }
  }
}

function openBotStudio(id) {
  if (typeof openExpressionStudio === 'function') {
    openExpressionStudio(id);
    return;
  }
  const b = id && getBot(id);
  const dlg = document.querySelector('#expressions-dialog');
  if (!b || !dlg) return;
  if (typeof stopLab === 'function') stopLab(false);
  const expBot = document.querySelector('#expression-bot');
  if (expBot) expBot.value = b.id;
  if (typeof lab !== 'undefined' && typeof expressionFor === 'function') lab.lastChoice = expressionFor(b);
  const colorEl = document.querySelector('#bot-color');
  if (colorEl) colorEl.value = b.color;
  if (typeof buildExpressions === 'function') buildExpressions();
  dlg.showModal();
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
      <div class="profile-section-edit-head">
        <h3 class="profile-section-title">${escapeHTML(sec.title)}</h3>
      </div>
      ${renderSectionEditorForm(false)}
    </article>`;
  }
  const hasVisible = hasVisibleMarkdown(sec.editBody);
  const isOpen = state.openSections.has(sec.index);
  return `<details class="profile-section-card"${hasVisible ? '' : ' data-empty="true"'} data-section-index="${sec.index}"${isOpen ? ' open' : ''}>
    <summary class="profile-section-head">
      <span class="profile-section-caret" aria-hidden="true">${icon('chevron')}</span>
      <h3 class="profile-section-title">${escapeHTML(sec.title)}</h3>
      ${hasVisible ? '' : '<span class="profile-section-tag">Trống</span>'}
    </summary>
    <div class="profile-section-content">
      ${hasVisible
        ? `<div class="markdown-body profile-section-body">${contentHtml(sec.editBody)}</div>`
        : '<p class="profile-section-empty">Chưa có nội dung — nhấn Sửa để điền.</p>'}
      <div class="profile-section-foot">
        <button class="quiet-button profile-section-btn" type="button" data-edit-section="${sec.index}"${state.busy ? ' disabled' : ''}>Sửa</button>
      </div>
    </div>
  </details>`;
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
      return `<button type="button" class="profile-doc-tab" role="tab" data-doc="${escapeHTML(item.name)}" title="${escapeHTML(item.name)}" aria-selected="${active}" data-active="${active}"><span>${escapeHTML(item.tab)}</span></button>`;
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
           <div class="profile-section-edit-head"><h3 class="profile-section-title">Mục mới</h3></div>
           ${renderSectionEditorForm(true)}
         </article>`
      : '';
    bodyHtml = `${introHtml}<div class="profile-section-grid">${cardsHtml}${newCardHtml}</div>`;
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
    b.bash ? (BASH_LABELS[b.bash] || `Lệnh: ${b.bash}`) : '',
    b.memory ? (b.memory.read && b.memory.write ? 'Bộ nhớ: đọc + ghi' : b.memory.read ? 'Bộ nhớ: chỉ đọc' : 'Bộ nhớ: tắt') : '',
    b.system ? 'Bot hệ thống' : '',
  ].filter(Boolean);
}

function renderBotDossier(b) {
  const facts = botFacts(b);
  el.root.innerHTML = `<section class="paper-card profile-bot">
    <div class="profile-bot-grid">
      <aside class="profile-bot-aside">
        <div class="profile-aside-actions">
          <button class="quiet-button profile-back-shared" type="button" data-action="show-shared">← Hồ sơ chung</button>
          <button class="quiet-button profile-studio-btn" type="button" id="open-studio" data-action="open-studio">Biểu cảm</button>
        </div>
        <div class="profile-bot-identity">
          ${avatar(b)}
          <div class="profile-bot-titles">
            <h2 class="card-title">${escapeHTML(b.name)}</h2>
            ${b.role ? `<div class="profile-bot-role">${escapeHTML(b.role)}</div>` : ''}
          </div>
        </div>
        ${b.description ? `<p class="card-copy profile-bot-desc">${escapeHTML(b.description)}</p>` : ''}
        ${facts.length ? `<ul class="profile-bot-specs">${facts.map(f => `<li><span class="chip profile-spec-item">${escapeHTML(f)}</span></li>`).join('')}</ul>` : ''}
        <form id="bot-settings-form" class="profile-bot-settings" novalidate aria-label="Cấu hình bot">
          <div id="bot-settings-fields"><p class="empty-note">Đang tải cài đặt bot…</p></div>
          <div class="profile-bot-save-row">
            <button class="primary-action" type="submit" id="save-bot-settings" disabled>Lưu</button>
            <button class="quiet-button" type="button" id="cancel-bot-settings" hidden>Hủy</button>
            <span class="form-hint" id="bot-settings-hint"></span>
          </div>
        </form>
      </aside>
      <div class="profile-bot-body">
        <div class="profile-twin-head">
          <span class="profile-twin-title" title="TWIN.md">Tính cách</span>
          <span class="profile-readonly">Chỉ đọc — bot tự cập nhật qua chat</span>
        </div>
        ${b.persona.trim()
          ? `<article class="markdown-body profile-content">${contentHtml(b.persona)}</article>`
          : '<p class="empty-note"><strong>Chưa có persona.</strong>Bot này chưa có mô tả tính cách.</p>'}
      </div>
    </div>
  </section>`;
  if (!botCatalog) void loadBotCatalog();
  if (botCfg.bot === b.id && (botCfg.settings || botCfg.loading)) {
    const draft = botDrafts.get(b.id) || null;
    if (draft && botCfg.settings) fillBotSettingsFields(botCfg.settings, draft);
    else renderBotSettingsForm();
  } else {
    void loadBotSettings(b.id, botDrafts.get(b.id) || null);
  }
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
  state.openSections.clear();
  state.doc = name;
  renderContent();
  ctx.updateRoute({ search: '', hash: '#' + encodeURIComponent(name) });
  routeKey = ctx.route.search + ctx.route.hash;
}

function showShared(anchor = '') {
  if (!confirmDiscardEdit()) return false;
  retainBotSettingsDraft();
  bootTouched = true;
  state.editing = ''; state.editBase = ''; state.editDraft = '';
  state.sectionEdit = null;
  state.openSections.clear();
  state.tab = '';
  if (anchor) {
    const fileName = anchor.replace(/^file-/, '');
    if (state.files.some(f => f.name === fileName)) state.doc = fileName;
  }
  el.title.textContent = 'Hồ sơ chung';
  selectedBot = '';
  ctx.highlightBot('');
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
  if (state.tab && state.tab !== id) retainBotSettingsDraft(state.tab);
  bootTouched = true;
  state.editing = ''; state.editBase = ''; state.editDraft = '';
  state.sectionEdit = null;
  state.tab = id;
  el.title.textContent = 'Bạn và ' + b.name;
  selectedBot = id;
  ctx.highlightBot(id);
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
    state.openSections.add(index);
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
    state.openSections.add(savedIndex);
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
    state.openSections.clear();
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

window.addEventListener('thyca:config-saved', () => {
  if (botCfg.savingFor || !ctx.isActive()) { botCatalogDirty = true; return; }
  void loadBotCatalog();
});
el.root.addEventListener('toggle', e => {
  const details = e.target;
  if (!details || details.tagName !== 'DETAILS' || !details.classList?.contains('profile-section-card')) return;
  const idx = Number(details.dataset?.sectionIndex);
  if (!Number.isInteger(idx)) return;
  if (details.open) state.openSections.add(idx);
  else state.openSections.delete(idx);
}, true);
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
  if (actionBtn?.dataset.action === 'open-studio') { openBotStudio(state.tab); }
});

function applyRoute(nextTab = state.tab, hash = decodeHash()) {
  if (nextTab && getBot(nextTab)) return showTab(nextTab);
  if (nextTab) {
    if (!confirmDiscardEdit()) return false;
    retainBotSettingsDraft();
    state.editing = ''; state.editBase = ''; state.editDraft = '';
    state.sectionEdit = null;
    state.tab = nextTab;
    selectedBot = ''; ctx.highlightBot(''); ctx.renderTeam(); fixSidebarLabels();
    el.title.textContent = 'Không tìm thấy bot';
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
  void loadBotCatalog();
  routeKey = ctx.route.search + ctx.route.hash;
  applyRoute(initialTab, decodeHash());
}
  const ready = boot();
  return { ready, selectBot, loadConversation, sidebarLabel(_id, name) { return `Hồ sơ của ${name}`; }, onLeave() { retainBotSettingsDraft(); }, onEnter() { if (botCatalogDirty && !botCfg.savingFor) { botCatalogDirty = false; void loadBotCatalog(); } }, onRoute(route) { handleRoute(route); } };
} });
