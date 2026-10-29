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
  if (!sel) return;
  const ids = bots.map(b => b.id);
  const current = ids.includes(selected) ? selected : ids.includes(selectedBot) ? selectedBot : ids[0];
  sel.innerHTML = bots.map(b => `<option value="${escapeHTML(b.id)}"${b.id === current ? ' selected' : ''}>${escapeHTML(b.name)}</option>`).join('')
    || '<option value="">Chưa có bot nào</option>';
  paintSettingsBot(current);
}

function paintSettingsBot(id) {
  const holder = document.querySelector('#settings-bot-seal');
  if (!holder) return;
  const b = id && getBot(id);
  holder.innerHTML = b ? `${avatar(b)}<span><strong>${escapeHTML(b.name)}</strong><span>${escapeHTML(b.role || 'Người bạn trong đội')}</span></span>` : '';
  const color = document.querySelector('#settings-bot-color');
  if (color && b) color.value = b.color || '#315e4c';
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
const SETTINGS_CONFIG_SECTIONS = ['limits'];

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
    return;
  }
  if (seq !== settingsLoadSeq) return;       // stale response: never replace a newer render/draft
  // Edits typed while this read was in flight (or kept from before the
  // open) win over the fresh values; each keeps its original baseline so
  // a same-field concurrent change still compares against what the user saw.
  retainSettingsDraft();
  settingsConfigValues = data.values || {};
  settingsBaseline = structuredClone(settingsConfigValues);
  renderSettingsConfigForm(data.schema?.sections || []);
  applySettingsDraft(settingsDraft);
}

function settingsFreshOf(fresh, section, name) {
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
    window.dispatchEvent(new Event('thyca:config-saved'));
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
  // Snapshot the draft on close (a fully reverted form clears the draft
  // instead of resurrecting stale edits; with no controls rendered the
  // retained draft is kept for the next successful load).
  dlg.addEventListener('close', () => { retainSettingsDraft(); });
  document.querySelector('#config-form').onsubmit = e => { e.preventDefault(); submitSettingsConfig(e.target); };
  document.querySelector('#settings-bot').onchange = e => paintSettingsBot(e.target.value);
  document.querySelector('#settings-bot-color').oninput = e => {
    const id = document.querySelector('#settings-bot').value;
    if (id && (window.ThycaShell || window.ThycaPrototype)?.setBotColor(id, e.target.value)) paintSettingsBot(id);
  };
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

function openSettingsDialog() {
  const dlg = document.querySelector('#settings-dialog');
  if (!dlg) return false;
  bindSettingsDialog();
  hydrateSettingsPrefs(readPrefs());
  fillSettingsBots();
  settingsFormStatus('');                                           // clear the previous save/conflict note
  loadSettingsConfig();                                           // API: GET /api/config
  loadSettingsStatus();                                           // API: GET /api/config/status — on demand
  if (!dlg.open) dlg.showModal();
  return true;
}

const openSettingsBtn = document.querySelector('#open-settings');
if (openSettingsBtn) openSettingsBtn.onclick = () => { openSettingsDialog(); };
