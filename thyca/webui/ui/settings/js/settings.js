// Retained settings controller; declarations stay private to this closure.
ThycaRouter.register('settings', { title: 'Cài đặt', stylesheet: '/ui/settings/settingscss/settings.css', mount(ctx) {
  const root = ctx.root;
  const $ = (selector, base = root) => selector === '.workspace' ? root : base.querySelector(selector);
  const $$ = (selector, base = root) => [...base.querySelectorAll(selector)];
  let selectedBot = '';
// Settings page: schema-driven config form (limits + timeline), read-only
// bot roster, localStorage-only UI preferences, backend status card.
// The shared shell (header/sidebar/dialogs) is emitted by emitShell() in
// the SPA entry before avatars/sidebar/studio bind; this script loads last
// and calls bootShell() for the live roster.
//
// API: GET /api/config -> {schema, values, meta}; POST /api/config <- edited
//      values; GET /api/config/status -> {ready, version}; GET /api/bots +
//      GET /api/sessions run inside bootShell() (share/shell.js).


/* avatars.js setState() reaches `runs`/`cancelTurn` on error/offline state
   changes (e.g. the studio's error preview). Settings owns no turn stream —
   an empty map keeps that path inert. */
const runs = new Map();
function cancelTurn() { return false; }

const workspace = $('.workspace');
document.querySelector('.notebook-shell').setAttribute('aria-label', 'Thyca — sổ điều chỉnh');
workspace.setAttribute('aria-label', 'Nội dung cài đặt');

workspace.insertAdjacentHTML('beforeend', `
<header class="page-nav">
 <div class="page-title"><div class="eyebrow">Cài đặt</div><h1>Sổ điều chỉnh</h1><p class="page-sub">Giới hạn vòng lặp, dung lượng nhớ và nhịp làm việc của đội bot — lưu thẳng vào tệp cấu hình của Thyca.</p></div>
 <div class="page-actions"><a class="quiet-button" href="../provider/index.html">Provider &amp; API key${icon('arrow')}</a></div>
</header>
<div class="page-scroll"><div class="page-inner">
 <div class="page-status" id="page-status" hidden></div>
 <div class="section-stack">

 <section class="paper-card" id="limits-card" aria-labelledby="limits-title">
  <div class="eyebrow">Giới hạn &amp; thời gian chạy</div>
  <h2 class="card-title" id="limits-title">Nhịp làm việc của đội</h2>
  <p class="card-copy">Các mức trần áp cho mọi bot: vòng agent, nhớ nóng và ngữ cảnh gửi lên model. Provider, model mặc định và API key chỉnh ở trang riêng; model riêng của từng bot chỉnh ở trang Hồ sơ, mục của bot đó.</p>
  <form id="config-form" novalidate>
   <div id="config-fields"><p class="empty-note">Đang tải cấu hình…</p></div>
   <div class="form-footer">
    <button class="primary-action" type="submit" id="save-config" disabled>Lưu cài đặt</button>
    <span class="form-hint">Các phần không hiển thị (provider, model, MCP, API key) được giữ nguyên.</span>
   </div>
  </form>
 </section>

 <section class="paper-card" id="bots-card" aria-labelledby="bots-title">
  <div class="eyebrow">Đội bot</div>
  <h2 class="card-title" id="bots-title">Những người bạn trong đội</h2>
  <p class="card-copy">Danh sách chỉ đọc — thêm hay chỉnh bot qua trò chuyện với Thyca, đổi màu và hình dạng giọt mực ở “Custom bot” bên thanh đội.</p>
  <div class="bot-roster" id="bot-roster"><p class="empty-note">Đang tải đội bot…</p></div>
 </section>

 <section class="paper-card" id="ui-card" aria-labelledby="ui-title">
  <div class="eyebrow">Giao diện · máy này</div>
  <h2 class="card-title" id="ui-title">Trang trải cho vừa tay</h2>
  <p class="card-copy">Chỉ lưu trong trình duyệt này (localStorage) — không gửi lên máy chủ.</p>
  <div class="pref-rows">
   <label class="field" for="pref-type-scale"><span>Cỡ chữ</span>
    <select id="pref-type-scale"><option value="small">Nhỏ</option><option value="medium">Vừa</option><option value="large">Lớn</option></select>
   </label>
   <label class="checkbox-row" for="pref-reduce-motion"><input type="checkbox" id="pref-reduce-motion"><span>Giảm chuyển động — dừng nhịp thở và cử chỉ rảnh của giọt mực</span></label>
   <label class="checkbox-row" for="pref-ornaments"><input type="checkbox" id="pref-ornaments"><span>Hiển thị họa tiết trang trí trên trang</span></label>
  </div>
  <div class="form-footer"><button type="button" class="quiet-button" id="pref-reset">Về mặc định</button></div>
 </section>

 <section class="paper-card" id="status-card" aria-labelledby="status-title">
  <div class="eyebrow">Trạng thái</div>
  <h2 class="card-title" id="status-title">Máy chủ Thyca</h2>
  <ul class="meta-list">
   <li><span>Backend</span><span class="status-line" id="status-ready">Đang kiểm tra…</span></li>
   <li><span>Phiên bản</span><span id="status-version">…</span></li>
  </ul>
  <div class="form-footer"><button type="button" class="quiet-button" id="status-refresh">${icon('retry')}<span>Kiểm tra lại</span></button></div>
 </section>

 </div>
</div></div>`);

function showPageStatus(text, cls = '') {
  const el = $('#page-status');
  el.textContent = text; el.hidden = false;
  el.className = 'page-status' + (cls ? ' ' + cls : '');
}

/* ---------- Config form (API-backed) ---------- */

let configValues = null;
let savingConfig = false;
// schema.sections also carries 'provider' — that form lives on the provider
// page; this card only renders the scalar limits/timeline sections.
const CONFIG_SECTIONS = ['limits', 'timeline'];

function rangeHint(f) {
  return f.min !== undefined && f.max !== undefined ? `<small class="field-range">${f.min} – ${f.max}</small>` : '';
}

function fieldControl(f, sectionKey, value) {
  const name = f.key.split('.').pop();
  const id = `cfg-${sectionKey}-${name}`;
  const common = `id="${id}" data-section="${sectionKey}" data-name="${escapeHTML(name)}" data-label="${escapeHTML(f.label)}"`;
  if (Array.isArray(f.choices) && f.choices.length) {
    return `<label class="field" for="${id}"><span>${escapeHTML(f.label)}${rangeHint(f)}</span><select ${common}>${f.choices.map(c => `<option value="${escapeHTML(c)}"${String(value ?? '') === String(c) ? ' selected' : ''}>${escapeHTML(c)}</option>`).join('')}</select></label>`;
  }
  if (f.type === 'bool') {
    return `<label class="checkbox-row" for="${id}"><input type="checkbox" ${common}${value ? ' checked' : ''}><span>${escapeHTML(f.label)}</span></label>`;
  }
  if (f.type === 'number') {
    return `<label class="field" for="${id}"><span>${escapeHTML(f.label)}${rangeHint(f)}</span><input type="number" step="1" ${common} value="${value ?? ''}"${f.min !== undefined ? ` min="${f.min}"` : ''}${f.max !== undefined ? ` max="${f.max}"` : ''}></label>`;
  }
  return `<label class="field" for="${id}"><span>${escapeHTML(f.label)}${rangeHint(f)}</span><input type="text" ${common} value="${escapeHTML(value ?? '')}"></label>`;
}

function renderConfigForm(sections) {
  const parts = CONFIG_SECTIONS.map(key => {
    const section = sections.find(s => s.key === key);
    const fields = (section?.fields || []).filter(f => !f.hidden && !f.secret);   // hidden/secret never render here; secrets stay on the provider page
    if (!fields.length) return '';
    const values = configValues[key] || {};
    return `<div class="cfg-section"><h3 class="cfg-section-title">${escapeHTML(section.label || key)}</h3><div class="card-grid">${fields.map(f => fieldControl(f, key, values[f.key.split('.').pop()])).join('')}</div></div>`;
  }).join('');
  $('#config-fields').innerHTML = parts || '<p class="empty-note"><strong>Không có mục nào để chỉnh.</strong>Schema hiện tại chỉ chứa các trường ẩn.</p>';
  $('#save-config').disabled = !parts;
}

async function loadConfig() {
  try {
    const data = await getJson('/api/config');                    // API: GET /api/config
    configValues = data.values || {};
    renderConfigForm(data.schema?.sections || []);
  } catch (err) {
    $('#config-fields').innerHTML = `<p class="empty-note"><strong>Không tải được cấu hình.</strong>${escapeHTML(err.message)} — kiểm tra máy chủ rồi tải lại trang.</p>`;
  }
}

$('#config-form').onsubmit = async e => {
  e.preventDefault();
  if (!configValues || savingConfig) return;
  const patch = {};
  for (const el of $$('#config-form [data-section]')) {
    const sec = el.dataset.section, name = el.dataset.name;
    if (!sec || !name) continue;
    if (!patch[sec]) patch[sec] = {};
    if (el.type === 'checkbox') patch[sec][name] = el.checked;
    else if (el.type === 'number') {
      const num = el.valueAsNumber;
      if (Number.isNaN(num)) { toast(`“${el.dataset.label}” cần một con số.`); return; }
      patch[sec][name] = num;
    } else patch[sec][name] = el.value;
  }
  savingConfig = true;
  const btn = $('#save-config'); btn.disabled = true;
  try {
    const latest = await getJson('/api/config');
    const next = structuredClone(latest.values || {});
    for (const [section, values] of Object.entries(patch)) next[section] = { ...(next[section] || {}), ...values };
    await postJson('/api/config', next);                          // API: POST /api/config — merge only this form's current limits/timeline patch
    configValues = next;
    window.dispatchEvent(new Event('thyca:config-saved'));
    toast('Đã lưu cài đặt.');
    loadStatus();
  } catch (err) {
    toast('Không lưu được: ' + err.message);                      // no stale whole-values POST if the fresh read fails
  } finally {
    savingConfig = false;
    btn.disabled = false;
  }
};

/* ---------- Đội bot (read-only roster) ---------- */

function renderRoster() {
  const host = $('#bot-roster');
  if (!bots.length) {
    host.innerHTML = '<p class="empty-note"><strong>Chưa có bot nào.</strong>Đội sẽ hiện ở đây khi có bot đăng ký — nhắn Thyca ở trang Chat để tạo.</p>';
    return;
  }
  host.innerHTML = bots.map(b => `<article class="bot-row">
   <span class="bot-row-seal">${avatar(b)}</span>
   <span class="bot-row-info"><strong>${escapeHTML(b.name)}</strong><span>${escapeHTML(b.role || 'Người bạn trong đội')}${b.system ? ' · bot hệ thống' : ''}</span></span>
   <span class="status-line${['error', 'offline'].includes(b.state) ? ' s-error' : ' s-ok'}">${escapeHTML(states[b.state]?.[1] || b.state)}</span>
   <a class="quiet-button" href="../chat/index.html?bot=${encodeURIComponent(b.id)}">Mở chat${icon('arrow')}</a>
  </article>`).join('');
}

/* Keep the card in step with the shared sidebar's roster updates
   (syncRoster rewrites #team-bot-list / data-state attributes). */
let rosterTimer = null;
new MutationObserver(() => { clearTimeout(rosterTimer); rosterTimer = setTimeout(renderRoster, 120); })
  .observe(ctx.sidebar, { childList: true, subtree: true, attributes: true, attributeFilter: ['data-state'] });

/* ---------- Giao diện (máy này) — localStorage-only, không gọi API ---------- */

const PREFS_KEY = 'thyca.ui.preferences';   // same key the old settings page used
const PREF_DEFAULTS = { typeScale: 'medium', reduceMotion: false, ornaments: true };

function readPrefs() {
  try {
    const saved = JSON.parse(localStorage.getItem(PREFS_KEY) || 'null');
    const next = saved && typeof saved === 'object' ? { ...PREF_DEFAULTS, ...saved } : { ...PREF_DEFAULTS };
    if (!['small', 'medium', 'large'].includes(next.typeScale)) next.typeScale = 'medium';
    return next;
  } catch { return { ...PREF_DEFAULTS }; }
}
let prefs = readPrefs();

function applyPrefs(next = prefs, { save = true } = {}) {
  prefs = next;
  document.documentElement.dataset.typeScale = next.typeScale;                    // css zoom hook in settings.css
  toggleMotion(Boolean(next.reduceMotion) || reduced.matches);                    // body.motion-paused + syncs #motion-toggle; OS-level reduced motion still wins
  workspace.dataset.ornaments = next.ornaments ? 'on' : 'off';                    // hides .workspace>.ornament/.staff via dataset
  if (save) try { localStorage.setItem(PREFS_KEY, JSON.stringify(next)); } catch { /* controls still apply when storage is unavailable */ }
}

function hydratePrefs(next) {
  $('#pref-type-scale').value = next.typeScale;
  $('#pref-reduce-motion').checked = Boolean(next.reduceMotion);
  $('#pref-ornaments').checked = next.ornaments !== false;
}

function bindPrefs() {
  const collect = () => ({ typeScale: $('#pref-type-scale').value, reduceMotion: $('#pref-reduce-motion').checked, ornaments: $('#pref-ornaments').checked });
  ['#pref-type-scale', '#pref-reduce-motion', '#pref-ornaments'].forEach(sel => $(sel).addEventListener('change', () => applyPrefs(collect())));
  $('#pref-reset').onclick = () => { hydratePrefs(PREF_DEFAULTS); applyPrefs({ ...PREF_DEFAULTS }); toast('Đã đưa giao diện về mặc định.'); };
}

/* ---------- Status card ---------- */

async function loadStatus() {
  const ready = $('#status-ready');
  try {
    const s = await getJson('/api/config/status');               // API: GET /api/config/status
    ready.textContent = s.ready ? 'Sẵn sàng' : 'Chưa sẵn sàng — cần cấu hình provider';
    ready.className = 'status-line' + (s.ready ? ' s-ok' : ' s-error');
    $('#status-version').textContent = s.version || '—';
  } catch {
    ready.textContent = 'Không kết nối được';
    ready.className = 'status-line s-error';
    $('#status-version').textContent = '—';
  }
}
$('#status-refresh').onclick = loadStatus;

/* ---------- Boot ---------- */

function selectBot(id) { if (id && getBot(id)) ctx.navigate('chat', { search: '?bot=' + encodeURIComponent(id) }); }
function loadSession(id) { if (id) ctx.navigate('chat', { search: '?s=' + encodeURIComponent(id) }); }

async function boot() {
  hydratePrefs(prefs);
  applyPrefs(prefs, { save: false });
  bindPrefs();
  loadStatus();                                                 // API: GET /api/config/status
  const cfgDone = loadConfig();                                 // API: GET /api/config — card reports its own failure
  try {
    await ctx.shellReady;                                          // API: GET /api/bots + GET /api/sessions -> shared sidebar
  } catch (err) {
    showPageStatus('Không kết nối được máy chủ Thyca: ' + err.message, 's-error');
  }
  renderRoster();
  await cfgDone;
}
  const ready = boot();
  return { ready, selectBot, loadSession };
} });
