// Shared Settings dialog: one overlay for bot customization shortcuts, this
// machine's UI preferences, the global run limits and backend status.
// Opened from the sidebar footer (#open-settings) on every page and for
// legacy /ui/settings* URLs (router maps those to chat + opens this).
// Replaces the old per-page settings workspace; the dialog is an overlay so
// drafts and streams on the underlying page are never disturbed.
'use strict';

/* ---------- Bot shortcut (existing customization stays in the studio) ---------- */

function fillSettingsBots(selected) {
  const sel = document.querySelector('#settings-bot');
  if (!sel) return '';
  const ids = bots.map(b => b.id);
  const activeBot = typeof activeSidebarBot === 'function'
    ? activeSidebarBot()
    : (typeof window !== 'undefined' && window.ThycaRouter?.highlightedBot?.()) || '';
  const current = ids.includes(selected) ? selected
    : ids.includes(activeBot) ? activeBot
    : ids.includes(selectedBot) ? selectedBot
    : ids[0] || '';
  sel.innerHTML = bots.map(b => `<option value="${escapeHTML(b.id)}"${b.id === current ? ' selected' : ''}>${escapeHTML(b.name)}</option>`).join('')
    || '<option value="">Chưa có bot nào</option>';
  sel.value = current;
  paintSettingsBot(current);
  return current;
}

function paintSettingsBot(id) {
  const holder = document.querySelector('#settings-bot-seal');
  if (!holder) return;
  const b = id && getBot(id);
  holder.innerHTML = b ? `${avatar(b)}<span><strong>${escapeHTML(b.name)}</strong><span>${escapeHTML(b.role || 'Người bạn trong đội')}</span></span>` : '';
}

/* ---------- UI preferences (same key/shape as the old settings page) ---------- */

function hydrateSettingsPrefs(next) {
  const type = document.querySelector('#pref-type-scale');
  if (type) type.value = next.typeScale;
  const motion = document.querySelector('#pref-reduce-motion');
  if (motion) motion.checked = Boolean(next.reduceMotion);
  const art = document.querySelector('#pref-ornaments');
  if (art) art.checked = next.ornaments !== false;
}

function collectSettingsPrefs() {
  return {
    typeScale: document.querySelector('#pref-type-scale').value,
    reduceMotion: document.querySelector('#pref-reduce-motion').checked,
    ornaments: document.querySelector('#pref-ornaments').checked,
  };
}

/* ---------- Global limits (schema-driven, limits section only) ---------- */

let settingsConfigValues = null;
// Values the form was painted from (refreshed on every successful render
// and patched with saved fields on submit): dirty detection compares the
// live controls against it, so an untouched field is never stored as an edit.
let settingsBaseline = null;
// Draft of genuinely dirty fields only ({section: {name: {check|value,
// base}}}), each keeping the baseline value it diverged from. Re-applied
// after every render so close/reopen and in-flight loads keep edits; the
// original base survives reloads so a same-field concurrent change still
// compares against what the user actually saw.
let settingsDraft = null;
// Conflicts already surfaced to the user ({section: {name: serverValue}}):
// resubmitting unchanged against the same server value is informed consent
// and proceeds; a moved server value warns anew. Cleared on save.
let settingsConflictSeen = null;
let settingsSaving = false;
let settingsLoadSeq = 0;
let settingsConfigSavedSelf = false;
const SETTINGS_CONFIG_SECTIONS = ['limits'];

/* ---------- Per-bot model & thinking settings (scoped to #settings-bot) ---------- */

let settingsBotCatalog = null; // { models, defaultModel, defaultProvider, providers }
let settingsBotSchemaChoices = [];
let settingsBotCatalogDirty = false;
const botCfg = { bot: '', settings: null, seq: 0, loading: false, savingFor: null, error: '' };
let botLoadedCfg = null;       // { bot, model, thinking } | null
const botDrafts = new Map();   // botId -> { model, thinking }

function currentSettingsBot() {
  const sel = document.querySelector('#settings-bot');
  return (sel && sel.value) || botCfg.bot || '';
}

function extractBotCatalog(payload) {
  const values = payload?.values || {};
  const sections = payload?.schema?.sections || [];
  const field = sections.flatMap(s => s.fields || []).find(f => f.key === 'provider.reasoningEffort');
  settingsBotCatalog = {
    models: (values.models && typeof values.models === 'object') ? values.models : {},
    defaultModel: typeof values.defaultModel === 'string' ? values.defaultModel : '',
    defaultProvider: typeof values.defaultProvider === 'string' ? values.defaultProvider : '',
    providers: (values.providers && typeof values.providers === 'object') ? values.providers : {},
  };
  settingsBotSchemaChoices = Array.isArray(field?.choices) ? field.choices : ['low', 'medium', 'high', 'xhigh', 'max'];
}

function botModelIds() {
  const models = settingsBotCatalog?.models && typeof settingsBotCatalog.models === 'object' ? settingsBotCatalog.models : {};
  const def = typeof settingsBotCatalog?.defaultModel === 'string' ? settingsBotCatalog.defaultModel : '';
  return [...new Set(['default', def, ...Object.keys(models)].filter(Boolean))];
}

function botModelLabel(id) {
  const def = settingsBotCatalog?.defaultModel || '';
  return id === 'default' ? `Theo mặc định chung${def ? ' (' + def + ')' : ''}` : id;
}

function botEffortChoices(modelId) {
  const registered = settingsBotCatalog?.models?.[modelId];
  const own = Array.isArray(registered?.reasoningEfforts) ? registered.reasoningEfforts.filter(Boolean) : [];
  return own.length ? own : settingsBotSchemaChoices;
}

function botDraftProviderId(modelId) {
  const models = settingsBotCatalog?.models && typeof settingsBotCatalog.models === 'object' ? settingsBotCatalog.models : {};
  const defProvider = typeof settingsBotCatalog?.defaultProvider === 'string' ? settingsBotCatalog.defaultProvider : '';
  if (modelId === 'default') {
    const dm = settingsBotCatalog?.defaultModel || '';
    const entry = dm ? models[dm] : null;
    return (entry?.provider && settingsBotCatalog?.providers?.[entry.provider]) ? entry.provider : defProvider;
  }
  if (!Object.hasOwn(models, modelId)) {
    return modelId === settingsBotCatalog?.defaultModel ? defProvider : '';
  }
  const entry = models[modelId];
  return (entry?.provider && settingsBotCatalog?.providers?.[entry.provider]) ? entry.provider : defProvider;
}

function botModelKnown(id) {
  const models = settingsBotCatalog?.models && typeof settingsBotCatalog.models === 'object' ? settingsBotCatalog.models : {};
  const def = settingsBotCatalog?.defaultModel || '';
  return id === 'default' || Object.hasOwn(models, id) || (!!def && id === def);
}

function updateBotSave() {
  const save = document.querySelector('#save-bot-settings');
  const sel = document.querySelector('#bot-model');
  if (!save) return;
  const active = currentSettingsBot();
  save.disabled = botCfg.loading || !!botCfg.savingFor || !active || !getBot(active)
    || !settingsBotCatalog || !botCfg.settings || !sel || !botModelKnown(sel.value);
}

function paintBotProviderPreview() {
  const sel = document.querySelector('#bot-model'), el = document.querySelector('#bot-provider-preview');
  if (!sel || !el || !settingsBotCatalog) return;
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
  const def = settingsBotCatalog.defaultModel || '';
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
  const def = settingsBotCatalog?.defaultModel || '';
  const modelSel = document.querySelector('#bot-model');
  if (!modelSel) return;
  modelSel.onchange = () => {
    const next = document.querySelector('#bot-thinking');
    const keep = next.value;
    const list = botEffortChoices(modelSel.value === 'default' ? def : modelSel.value);
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

function botFormDraft(forBot = currentSettingsBot()) {
  const m = document.querySelector('#bot-model'), t = document.querySelector('#bot-thinking');
  if (!m || !t || !botLoadedCfg || !forBot || botLoadedCfg.bot !== forBot) return null;
  const cur = { model: m.value, thinking: t.value || null };
  return (cur.model !== botLoadedCfg.model || cur.thinking !== botLoadedCfg.thinking) ? cur : null;
}

function retainBotSettingsDraft(forBot = botCfg.bot || currentSettingsBot()) {
  if (!botLoadedCfg?.bot || !forBot || botLoadedCfg.bot !== forBot) return;
  const draft = botFormDraft(forBot);
  if (draft) botDrafts.set(botLoadedCfg.bot, draft);
  else if (document.querySelector('#bot-model')) botDrafts.delete(botLoadedCfg.bot);
}

function renderBotForm() {
  const host = document.querySelector('#bot-settings-fields');
  const hint = document.querySelector('#bot-settings-hint');
  if (!host || !hint) return;
  const id = currentSettingsBot();
  const b = id && getBot(id);
  if (!b || !settingsBotCatalog) {
    host.innerHTML = !b
      ? `<p class="empty-note"><strong>Không tìm thấy bot${id ? ` “${escapeHTML(id)}”` : ''}.</strong>Chọn một bot trong danh sách phía trên.</p>`
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
  const modelId = configured.id || 'default';
  const thinking = configured.thinking ?? '';
  host.innerHTML = botFieldsHtml(modelId, thinking, s.effective || {});
  const modelSel = document.querySelector('#bot-model'), thinkSel = document.querySelector('#bot-thinking');
  if (modelSel) modelSel.value = modelId;
  if (thinkSel) thinkSel.value = thinking;
  botLoadedCfg = { bot: id, model: modelId, thinking: configured.thinking ?? null };
  hint.textContent = '';
  bindBotFormControls();
}

function fillBotFormFields(settings, draft) {
  const host = document.querySelector('#bot-settings-fields');
  const hint = document.querySelector('#bot-settings-hint');
  if (!host || !hint || !settings || !settingsBotCatalog) { renderBotForm(); return; }
  const id = currentSettingsBot();
  const configured = settings.model || { id: 'default', thinking: null };
  botLoadedCfg = { bot: id, model: configured.id || 'default', thinking: configured.thinking ?? null };
  host.innerHTML = botFieldsHtml(draft.model, draft.thinking ?? '', settings.effective || {});
  const modelSel = document.querySelector('#bot-model'), thinkSel = document.querySelector('#bot-thinking');
  if (modelSel) modelSel.value = draft.model;
  if (thinkSel) thinkSel.value = draft.thinking ?? '';
  hint.textContent = '';
  bindBotFormControls();
}

async function loadBotSettings(preserved = null) {
  const id = currentSettingsBot();
  if (!id || !getBot(id)) {
    botCfg.settings = null;
    botLoadedCfg = null;
    botCfg.loading = false;
    renderBotForm();
    return;
  }
  const seq = ++botCfg.seq;
  botCfg.loading = true;
  botCfg.settings = null;
  botCfg.error = '';
  botLoadedCfg = null;
  renderBotForm();
  const save = document.querySelector('#save-bot-settings');
  if (save) save.disabled = true;
  const hint = document.querySelector('#bot-settings-hint');
  if (hint) hint.textContent = 'Đang tải…';
  try {
    const data = await getJson(`/api/bots/${encodeURIComponent(id)}/settings`);
    if (seq !== botCfg.seq || currentSettingsBot() !== id) return;
    botCfg.settings = data;
  } catch (err) {
    if (seq !== botCfg.seq || currentSettingsBot() !== id) return;
    botCfg.error = err.message;
  } finally {
    if (seq === botCfg.seq && currentSettingsBot() === id) {
      botCfg.loading = false;
      if (botCfg.settings) {
        const m = botCfg.settings.model || {};
        botLoadedCfg = { bot: id, model: m.id || 'default', thinking: m.thinking ?? null };
      }
      const draft = preserved || botDrafts.get(id) || null;
      if (draft && botCfg.settings) fillBotFormFields(botCfg.settings, draft);
      else renderBotForm();
    }
  }
}

function selectSettingsBot(id, { preserveCurrentDraft = true } = {}) {
  if (preserveCurrentDraft && botCfg.bot && botCfg.bot !== id) retainBotSettingsDraft(botCfg.bot);
  botCfg.bot = id || '';
  const sel = document.querySelector('#settings-bot');
  if (sel && sel.value !== botCfg.bot) sel.value = botCfg.bot;
  paintSettingsBot(botCfg.bot);
  void loadBotSettings(botDrafts.get(botCfg.bot) || null);
}

async function submitBotSettings() {
  const id = currentSettingsBot();
  const b = id && getBot(id);
  const modelSel = document.querySelector('#bot-model'), thinkSel = document.querySelector('#bot-thinking');
  if (!id || !b || !settingsBotCatalog || !modelSel || !thinkSel) return;
  if (!botCfg.settings || botCfg.loading || botCfg.savingFor || !botModelKnown(modelSel.value)) return;
  const seq = ++botCfg.seq;
  botCfg.savingFor = { bot: id, seq };
  const save = document.querySelector('#save-bot-settings');
  if (save) save.disabled = true;
  const hint = document.querySelector('#bot-settings-hint');
  if (hint) hint.textContent = 'Đang lưu…';
  const payload = { model: { id: modelSel.value, thinking: thinkSel.value || null } };
  try {
    const data = await postJson(`/api/bots/${encodeURIComponent(id)}/settings`, payload);
    botDrafts.delete(id);
    const live = getBot(id);
    if (live) {
      live.model = data.model;
      if (typeof renderTeam === 'function') renderTeam();
      if (typeof renderHeaderBots === 'function') renderHeaderBots();
      paintSettingsBot(currentSettingsBot());
    }
    window.dispatchEvent(new CustomEvent('thyca:bot-settings-saved', { detail: { botId: id, model: data.model, effective: data.effective } }));
    if (seq !== botCfg.seq || currentSettingsBot() !== id) return;
    botCfg.settings = data;
    renderBotForm();
    const h = document.querySelector('#bot-settings-hint');
    if (h) h.textContent = 'Đã lưu.';
    toast(`Đã lưu cài đặt cho ${b.name}.`);
  } catch (err) {
    if (seq !== botCfg.seq || currentSettingsBot() !== id) return;
    const h = document.querySelector('#bot-settings-hint');
    if (h) h.textContent = 'Không lưu được: ' + err.message;
  } finally {
    if (botCfg.savingFor && botCfg.savingFor.seq === seq) {
      botCfg.savingFor = null;
      if (currentSettingsBot() === id) {
        const h = document.querySelector('#bot-settings-hint');
        if (h && h.textContent === 'Đang lưu…') {
          h.textContent = '';
          renderBotForm();
        } else {
          updateBotSave();
        }
      } else {
        updateBotSave();
      }
    }
    if (settingsBotCatalogDirty && !botCfg.savingFor && document.querySelector('#settings-dialog')?.open) {
      settingsBotCatalogDirty = false;
      void loadSettingsConfig();
    }
  }
}

function settingsRangeHint(f) {
  return f.min !== undefined && f.max !== undefined ? `<small class="field-range">${f.min} – ${f.max}</small>` : '';
}

function settingsFieldControl(f, sectionKey, value) {
  const name = f.key.split('.').pop();
  const id = `setcfg-${sectionKey}-${name}`;
  const common = `id="${id}" data-section="${sectionKey}" data-name="${escapeHTML(name)}" data-label="${escapeHTML(f.label)}"`;
  if (Array.isArray(f.choices) && f.choices.length) {
    return `<label class="field" for="${id}"><span>${escapeHTML(f.label)}${settingsRangeHint(f)}</span><select ${common}>${f.choices.map(c => `<option value="${escapeHTML(c)}"${String(value ?? '') === String(c) ? ' selected' : ''}>${escapeHTML(c)}</option>`).join('')}</select></label>`;
  }
  if (f.type === 'bool') {
    return `<label class="checkbox-row" for="${id}"><input type="checkbox" ${common}${value ? ' checked' : ''}><span>${escapeHTML(f.label)}</span></label>`;
  }
  if (f.type === 'number') {
    return `<label class="field" for="${id}"><span>${escapeHTML(f.label)}${settingsRangeHint(f)}</span><input type="number" step="1" ${common} value="${value ?? ''}"${f.min !== undefined ? ` min="${f.min}"` : ''}${f.max !== undefined ? ` max="${f.max}"` : ''}></label>`;
  }
  return `<label class="field" for="${id}"><span>${escapeHTML(f.label)}${settingsRangeHint(f)}</span><input type="text" ${common} value="${escapeHTML(value ?? '')}"></label>`;
}

function renderSettingsConfigForm(sections) {
  const parts = SETTINGS_CONFIG_SECTIONS.map(key => {
    const section = sections.find(s => s.key === key);
    const fields = (section?.fields || []).filter(f => !f.hidden && !f.secret);   // hidden/secret never render here; secrets stay on the provider page
    if (!fields.length) return '';
    const values = settingsConfigValues[key] || {};
    return `<div class="cfg-section"><h3 class="cfg-section-title">${escapeHTML(section.label || key)}</h3><div class="card-grid">${fields.map(f => settingsFieldControl(f, key, values[f.key.split('.').pop()])).join('')}</div></div>`;
  }).join('');
  document.querySelector('#config-fields').innerHTML = parts || '<p class="empty-note"><strong>Không có mục nào để chỉnh.</strong>Schema hiện tại chỉ chứa các trường ẩn.</p>';
  document.querySelector('#save-config').disabled = !parts;
}

function settingsReadControl(el) {
  // {ok, value} for one rendered control; ok:false marks a number field
  // with no numeric value so submit can report it instead of saving NaN.
  if (el.type === 'checkbox') return { ok: true, value: !!el.checked };
  if (el.type === 'number') {
    const raw = typeof el.valueAsNumber === 'number' ? el.valueAsNumber : Number(el.value);
    return Number.isNaN(raw) ? { ok: false } : { ok: true, value: raw };
  }
  return { ok: true, value: el.value };
}

function settingsBaselineOf(section, name) {
  const group = settingsBaseline ? settingsBaseline[section] : undefined;
  return group && typeof group === 'object' ? group[name] : undefined;
}

function settingsValuesDiffer(a, b) {
  // Number-aware comparison that tolerates rendered strings vs stored
  // numbers; missing values only equal other missing values.
  if (a === undefined || b === undefined) return a !== b;
  if (typeof a === 'number' || typeof b === 'number') {
    const x = Number(a), y = Number(b);
    if (Number.isNaN(x) || Number.isNaN(y)) return String(a) !== String(b);
    return x !== y;
  }
  return String(a) !== String(b);
}

function settingsFieldBase(sec, name) {
  // The value this field diverged from: its original draft base when it
  // is a retained edit, else the value the form was painted from.
  const kept = settingsDraft && settingsDraft[sec] ? settingsDraft[sec][name] : undefined;
  if (kept && 'base' in kept && kept.base !== undefined) return kept.base;
  return settingsBaselineOf(sec, name);
}

function snapshotSettingsForm() {
  // Only genuinely changed values are kept — untouched controls are never
  // stored, so a later render cannot turn their stale values into edits.
  // Comparison is against the freshly rendered server values, so reverting
  // to an older value still counts as a deliberate change; a retained entry
  // keeps its original baseline so conflicts still compare against what the
  // user saw when the field diverged. An invalid (e.g. cleared) numeric
  // still counts as a deliberate edit: its raw text is kept so close/reopen
  // or a pending load restores it instead of the server value, while
  // submit-time validation keeps blocking the write.
  const draft = {};
  for (const el of document.querySelectorAll('#config-form [data-section]')) {
    const sec = el.dataset.section, name = el.dataset.name;
    if (!sec || !name) continue;
    const rendered = settingsBaselineOf(sec, name);
    const cur = el.type === 'checkbox' ? !!el.checked : el.value;
    const differs = settingsReadControl(el).ok ? settingsValuesDiffer(cur, rendered) : String(cur ?? '') !== String(rendered ?? '');
    if (!differs) continue;
    if (!draft[sec]) draft[sec] = {};
    const prev = settingsDraft && settingsDraft[sec] ? settingsDraft[sec][name] : undefined;
    const base = (prev && 'base' in prev && prev.base !== undefined) ? prev.base : rendered;
    draft[sec][name] = el.type === 'checkbox' ? { check: !!el.checked, base: base ?? false } : { value: el.value, base };
  }
  return draft;
}

function retainSettingsDraft() {
  // Replace the stored draft, except when no form controls exist: with
  // nothing rendered there is nothing to compare against, so a retained
  // draft (e.g. after a failed load destroyed the controls) must survive
  // until controls render again.
  const incoming = snapshotSettingsForm();
  // NB: querySelectorAll (never the singular querySelector: it would
  // auto-vivify a matching stub and defeat the emptiness check).
  if (Object.keys(incoming).length || document.querySelectorAll('#config-form [data-section]').length) settingsDraft = incoming;
}

function applySettingsDraft(draft) {
  if (!draft) return;
  for (const el of document.querySelectorAll('#config-form [data-section]')) {
    const sec = el.dataset.section, name = el.dataset.name;
    const saved = sec && name && draft[sec] ? draft[sec][name] : undefined;
    if (!saved) continue;
    if (el.type === 'checkbox' && 'check' in saved) el.checked = !!saved.check;
    else if ('value' in saved && el.type !== 'checkbox') el.value = saved.value;
  }
}

function settingsFormStatus(message, kind) {
  // Save feedback rendered INSIDE the modal (role=status persists there)
  // plus the global toast the existing flows already announce through.
  const el = document.querySelector('#settings-form-status');
  if (el) {
    el.textContent = message || '';
    el.hidden = !message;
    el.className = 'form-status' + (kind ? ' ' + kind : '');
    el.setAttribute('role', 'status');
  }
  if (message) toast(message);
}

function effectiveDefaultModelContext(values) {
  const name = values?.defaultModel;
  const models = values?.models;
  if (!name || !models || typeof models !== 'object' || !Object.hasOwn(models, name)) return undefined;
  const ctx = models[name]?.contextTokens;
  return Number.isInteger(ctx) ? ctx : undefined;
}

/* Repaint the per-bot form after a config (re)load. The draft is re-read
   for the bot selected NOW: a switch or an edit made while the config GET
   was in flight wins over whatever was on screen when the load started, so
   one bot's draft can never be painted onto another bot's form. */
function repaintBotForm() {
  if (!botCfg.bot || botCfg.savingFor) return;
  const current = currentSettingsBot();
  if (!current || current !== botCfg.bot) return;
  const formLive = !!document.querySelector('#bot-model') && botLoadedCfg?.bot === current;
  const draft = formLive ? botFormDraft() : (botDrafts.get(current) || null);
  if (draft && draft.model && botCfg.settings) fillBotFormFields(botCfg.settings, draft);
  else renderBotForm();
}

async function loadSettingsConfig() {
  const seq = ++settingsLoadSeq;
  let data;
  try {
    data = await getJson('/api/config');                         // API: GET /api/config
  } catch (err) {
    if (seq !== settingsLoadSeq) return;     // a newer load started; leave its outcome alone
    // Snapshot BEFORE replacing the controls: the visible draft survives
    // the failed load and a later successful reopen restores it.
    retainSettingsDraft();
    document.querySelector('#config-fields').innerHTML = `<p class="empty-note"><strong>Không tải được cấu hình.</strong>${escapeHTML(err.message)} — kiểm tra máy chủ rồi mở lại Cài đặt.</p>`;
    repaintBotForm();
    return;
  }
  if (seq !== settingsLoadSeq) return;       // stale response: never replace a newer render/draft
  // Edits typed while this read was in flight (or kept from before the
  // open) win over the fresh values; each keeps its original baseline so
  // a same-field concurrent change still compares against what the user saw.
  retainSettingsDraft();
  settingsConfigValues = structuredClone(data.values || {});
  settingsBaseline = structuredClone(settingsConfigValues);
  const effCtx = effectiveDefaultModelContext(data.values);
  if (effCtx !== undefined && settingsConfigValues.limits && typeof settingsConfigValues.limits === 'object') {
    settingsConfigValues.limits.contextTokens = effCtx;
    settingsBaseline.limits.contextTokens = effCtx;
  }
  renderSettingsConfigForm(data.schema?.sections || []);
  applySettingsDraft(settingsDraft);
  extractBotCatalog(data);
  repaintBotForm();
}

function settingsFreshOf(fresh, section, name) {
  if (section === 'limits' && name === 'contextTokens') {
    const effCtx = effectiveDefaultModelContext(fresh);
    if (effCtx !== undefined) return effCtx;
  }
  const group = fresh && typeof fresh === 'object' ? fresh[section] : undefined;
  return group && typeof group === 'object' ? group[name] : undefined;
}

async function submitSettingsConfig(form) {
  if (!settingsConfigValues || !settingsBaseline || settingsSaving) return;
  // Dirty-check against the freshly rendered server baseline: only values
  // the user actually changed go out. Reverting to an older value still
  // counts (it differs from what the server now reports), while untouched
  // fields can never become edits.
  const dirty = {};
  for (const el of form.querySelectorAll('[data-section]')) {
    const sec = el.dataset.section, name = el.dataset.name;
    if (!sec || !name) continue;
    const read = settingsReadControl(el);
    if (!read.ok) { settingsFormStatus(`“${el.dataset.label || name}” cần một con số.`, 'is-error'); return; }
    if (!settingsValuesDiffer(read.value, settingsBaselineOf(sec, name))) continue;
    if (!dirty[sec]) dirty[sec] = {};
    dirty[sec][name] = read.value;
  }
  if (!Object.keys(dirty).length) { settingsFormStatus('Không có gì thay đổi.', ''); return; }
  settingsSaving = true;
  const btn = document.querySelector('#save-config'); if (btn) btn.disabled = true;
  try {
    const latest = await getJson('/api/config');                 // API: GET /api/config
    const fresh = latest.values || {};
    // Same-field concurrency: when the server moved a dirty field since
    // its original baseline and the form still disagrees with the server,
    // surface the conflict instead of overwriting. Resubmitting unchanged
    // against the same server value is informed consent and proceeds.
    const clashes = [];
    for (const [section, values] of Object.entries(dirty)) {
      for (const name of Object.keys(values)) {
        const base = settingsFieldBase(section, name);
        const server = settingsFreshOf(fresh, section, name);
        if (base === undefined || server === undefined) continue;
        if (!settingsValuesDiffer(server, base) || !settingsValuesDiffer(values[name], server)) continue;
        // Consent is bound to the warned (server, draft) pair: resubmitting
        // unchanged against the same server value is informed consent, but
        // editing the draft afterwards must warn anew instead of posting.
        const seen = settingsConflictSeen && settingsConflictSeen[section] ? settingsConflictSeen[section][name] : undefined;
        if (seen && !settingsValuesDiffer(seen.server, server) && !settingsValuesDiffer(seen.draft, values[name])) continue;
        clashes.push({ section, name, server, draft: values[name] });
      }
    }
    if (clashes.length) {
      if (!settingsConflictSeen) settingsConflictSeen = {};
      const bits = clashes.map(c => {
        if (!settingsConflictSeen[c.section]) settingsConflictSeen[c.section] = {};
        settingsConflictSeen[c.section][c.name] = { server: c.server, draft: c.draft };
        const control = form.querySelector(`[data-section="${c.section}"][data-name="${c.name}"]`);
        const label = (control && control.dataset.label) || c.name;
        return `«${label}» (bản nháp: ${c.draft}, máy chủ: ${c.server})`;
      });
      settingsFormStatus(`Xung đột: ${bits.join('; ')} đã đổi ở nơi khác. Bản nháp được giữ nguyên — sửa theo máy chủ rồi lưu lại, hoặc lưu lại nguyên để ghi đè.`, 'is-error');
      return;
    }
    // Merge the dirty fields onto the fresh read: unrendered sections stay
    // current and untouched fields keep the fresh server value, so only an
    // explicitly edited field overrides the server. A write-write race in
    // between still fails closed with 409. The draft stays for retry.
    const next = structuredClone(fresh);
    for (const [section, values] of Object.entries(dirty)) next[section] = { ...(next[section] || {}), ...values };
    // The limits field shows the EFFECTIVE context (the default model's own
    // value when it has one, else the global). Sync the per-model copy only
    // when the model already carries one: creating a pin for a model that
    // inherits the global would shadow every future global edit for it.
    if (dirty.limits && dirty.limits.contextTokens !== undefined
        && next.defaultModel && next.models && Object.hasOwn(next.models, next.defaultModel)
        && next.models[next.defaultModel] && typeof next.models[next.defaultModel] === 'object'
        && Number.isInteger(next.models[next.defaultModel].contextTokens)) {
      next.models[next.defaultModel] = { ...next.models[next.defaultModel], contextTokens: dirty.limits.contextTokens };
    }
    // Conditional write when the server reports a revision.
    const body = latest.revision != null ? { ...next, baseRevision: latest.revision } : next;
    try {
      await postJson('/api/config', body);                        // API: POST /api/config — dirty limits fields only
    } catch (err) {
      if (err?.status === 409) {
        settingsFormStatus(err?.data?.code === 'model_in_use' && err.message ? err.message : 'Cấu hình đã thay đổi ở nơi khác. Bản nháp được giữ nguyên — kiểm tra lại rồi lưu lại.', 'is-error');
        return;
      }
      throw err;
    }
    settingsConfigValues = next;
    settingsDraft = null;
    settingsConflictSeen = null;
    // The display now shows the saved values: anchor the baseline to them
    // so the refresh snapshot below does not mistake them for new edits.
    for (const [section, values] of Object.entries(dirty)) {
      if (!settingsBaseline[section] || typeof settingsBaseline[section] !== 'object') settingsBaseline[section] = {};
      Object.assign(settingsBaseline[section], structuredClone(values));
    }
    settingsFormStatus('Đã lưu cài đặt.', 'is-ok');
    settingsConfigSavedSelf = true;
    try { window.dispatchEvent(new Event('thyca:config-saved')); } finally { settingsConfigSavedSelf = false; }
    loadSettingsStatus();
    // Refresh the display and its baseline from what was saved; edits typed
    // during the save are re-captured by the load snapshot and kept.
    await loadSettingsConfig();                                  // API: GET /api/config
  } catch (err) {
    settingsFormStatus('Không lưu được: ' + err.message, 'is-error');   // no stale POST if the fresh read fails
  } finally {
    settingsSaving = false;
    if (btn) btn.disabled = false;
  }
}

/* ---------- Backend status (on demand) ---------- */

async function loadSettingsStatus() {
  const ready = document.querySelector('#status-ready');
  if (!ready) return;
  try {
    const s = await getJson('/api/config/status');                // API: GET /api/config/status
    ready.textContent = s.ready ? 'Sẵn sàng' : 'Chưa sẵn sàng — cần cấu hình provider';
    ready.className = 'status-line' + (s.ready ? ' s-ok' : ' s-error');
    document.querySelector('#status-version').textContent = s.version || '—';
  } catch {
    ready.textContent = 'Không kết nối được';
    ready.className = 'status-line s-error';
    document.querySelector('#status-version').textContent = '—';
  }
}

/* ---------- Dialog wiring ---------- */

function bindSettingsDialog() {
  const dlg = document.querySelector('#settings-dialog');
  if (!dlg || dlg.dataset.settingsBound) return;
  dlg.dataset.settingsBound = 'true';
  // Snapshot drafts on close so edits survive reopen without cross-bot bleed.
  dlg.addEventListener('close', () => {
    retainSettingsDraft();
    retainBotSettingsDraft();
  });
  document.querySelector('#config-form').onsubmit = e => { e.preventDefault(); submitSettingsConfig(e.target); };
  const botForm = document.querySelector('#bot-settings-form');
  if (botForm) botForm.onsubmit = e => { e.preventDefault(); void submitBotSettings(); };
  document.querySelector('#settings-bot').onchange = e => selectSettingsBot(e.target.value);
  document.querySelector('#open-studio').onclick = () => {
    const id = document.querySelector('#settings-bot').value;
    if (id && getBot(id)) {
      stopLab(false);
      document.querySelector('#expression-bot').value = id;
      lab.lastChoice = expressionFor(getBot(id));
      document.querySelector('#bot-color').value = getBot(id).color;
      buildExpressions();
    }
    document.querySelector('#expressions-dialog').showModal();
  };
  const collect = () => ({
    typeScale: document.querySelector('#pref-type-scale').value,
    reduceMotion: document.querySelector('#pref-reduce-motion').checked,
    ornaments: document.querySelector('#pref-ornaments').checked,
  });
  for (const sel of ['#pref-type-scale', '#pref-reduce-motion', '#pref-ornaments']) {
    document.querySelector(sel).addEventListener('change', () => applyPrefs(collect()));
  }
  document.querySelector('#pref-reset').onclick = () => {
    hydrateSettingsPrefs({ ...PREF_DEFAULTS }); applyPrefs({ ...PREF_DEFAULTS }); toast('Đã đưa giao diện về mặc định.');
  };
  document.querySelector('#status-refresh').onclick = loadSettingsStatus;
}

window.addEventListener('thyca:config-saved', () => {
  if (settingsConfigSavedSelf) return;
  if (botCfg.savingFor) { settingsBotCatalogDirty = true; return; }
  const dlg = document.querySelector('#settings-dialog');
  if (dlg?.open) void loadSettingsConfig();
  else settingsBotCatalogDirty = true;
});

function openSettingsDialog() {
  const dlg = document.querySelector('#settings-dialog');
  if (!dlg) return false;
  bindSettingsDialog();
  hydrateSettingsPrefs(readPrefs());
  retainBotSettingsDraft();
  const currentBot = fillSettingsBots();
  if (!(botCfg.bot === currentBot && botCfg.savingFor?.bot === currentBot)) {
    selectSettingsBot(currentBot, { preserveCurrentDraft: true });
  } else {
    paintSettingsBot(currentBot);
    updateBotSave();
  }
  settingsBotCatalogDirty = false;
  settingsFormStatus('');                                           // clear the previous save/conflict note
  loadSettingsConfig();                                           // API: GET /api/config
  loadSettingsStatus();                                           // API: GET /api/config/status — on demand
  if (!dlg.open) dlg.showModal();
  return true;
}

const openSettingsBtn = document.querySelector('#open-settings');
if (openSettingsBtn) openSettingsBtn.onclick = () => { openSettingsDialog(); };
