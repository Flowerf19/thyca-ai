// Shared app shell markup for every Thyca page except chat (chat keeps its
// own static markup). Two phases because studio.js/sidebar.js bind elements
// at load time:
//   emitShell()  — synchronous DOM insert; call BEFORE avatars/sidebar/studio
//                  scripts. Page content goes into the returned .workspace.
//   bootShell()  — async roster/session fetch; call from the page's JS once
//                  every shared script has loaded.
// Script order for each page:
//   util.js -> data.js -> shell.js -> emitShell() -> avatars.js -> idle.js
//   -> sidebar.js -> studio.js -> shell-app.js -> js/<page>.js (calls bootShell)
'use strict';

/* SHELL_PAGES + pageLinksMarkup live in util.js (loaded everywhere) so the
   chat page — which ships static markup instead of emitShell — renders the
   same nav. */
function thycaHeader(current) {
  return `<header class="site-header"><div class="header-left"><button class="icon-button menu-button" type="button" aria-label="Mở mục lục" aria-controls="chat-sidebar" aria-expanded="false"><svg class="icon"><use href="#i-menu"/></svg></button><span class="wordmark">thyca</span><span class="header-rule"></span></div><div class="header-right"><nav class="page-links" aria-label="Điều hướng">${pageLinksMarkup(current)}</nav></div></header>`;
}

/* Sidebar for non-chat pages: team roster only. The "Hội thoại chung"
   section + create button live in chat's own static markup — group
   conversations only make sense on the chat page. */
function thycaSidebarMarkup() {
  return `<aside class="sidebar" id="chat-sidebar" aria-label="Đội bot">
 <div class="sessions-heading"><h2 id="team-nav-title">Đội</h2><span class="bot-count">0 người bạn</span></div>
 <div class="side-scroll"><nav class="session-list" id="team-bot-list" aria-label="Bot trong đội"></nav></div>
 <footer class="sidebar-footer"><button id="open-settings" aria-label="Cài đặt"><svg class="icon"><use href="#i-face"/></svg>Cài đặt</button></footer>
</aside>
<div class="splitter" role="separator" tabindex="0" aria-label="Thay đổi chiều rộng thanh bot" aria-orientation="vertical" aria-valuemin="310" aria-valuemax="470" aria-valuenow="374" title="Kéo để thay đổi chiều rộng · dùng phím trái/phải"></div>`;
}

/* The same dialogs chat ships statically — needed before studio.js binds. */
function thycaDialogs() {
  return `<div class="mobile-overlay" id="mobile-overlay" hidden></div>
<dialog class="screen-dialog" id="expressions-dialog" aria-labelledby="expressions-title">
<div class="dialog-heading"><div><h2 id="expressions-title">Một giọt mực, nhiều tâm trạng.</h2><p>Đôi mắt kể chuyện, đôi tay giữ nhịp. Thử từng biểu cảm hoặc xem trọn một lượt làm việc.</p></div><button class="icon-button dialog-close" aria-label="Đóng"><svg class="icon"><use href="#i-close"/></svg></button></div>
<div class="dialog-body"><div class="dialog-controls"><label for="expression-bot">Người bạn</label><select id="expression-bot"></select><label for="bot-color">Màu bot</label><input type="color" id="bot-color" value="#315e4c" aria-label="Màu của bot" style="width:28px;height:28px;border:0;background:none;padding:0;cursor:pointer"><div class="palette-preview" id="palette-preview"></div></div>
<div class="shape-row"><span class="shape-label" id="shape-label">Hình dạng</span><div class="shape-picker" id="shape-picker" role="group" aria-labelledby="shape-label"></div></div>
<div class="studio-tabs" role="tablist" aria-label="Nhóm biểu cảm"><button type="button" class="studio-tab" id="work-tab" role="tab" aria-selected="true" aria-controls="lifecycle-panel">Khi làm việc <small>11</small></button><button type="button" class="studio-tab" id="idle-tab" role="tab" aria-selected="false" aria-controls="idle-panel" tabindex="-1">Lúc rảnh <small>8</small></button></div>
<section class="expression-stage" id="expression-stage" aria-label="Biểu cảm đang xem"><div class="stage-portrait" id="lab-portrait"></div><div class="stage-copy"><div class="stage-kicker"><span class="status-dot"></span><span id="lab-bot-name">thyca</span><span>·</span><span>Cận cảnh</span></div><h3 id="lab-state"></h3><p id="lab-description"></p><p class="lab-speech" id="lab-speech"></p></div></section>
<div class="lab-actions"><button type="button" class="lab-primary" id="lab-play"><svg class="icon"><use href="#i-play"/></svg>Chạy trọn một lượt</button><button type="button" id="lab-replay"><svg class="icon"><use href="#i-retry"/></svg>Phát lại biểu cảm</button><button type="button" id="lab-done"><svg class="icon"><use href="#i-star"/></svg>Thử hoàn tất</button><button type="button" id="lab-stop" hidden>Dừng</button><span id="lab-playback-note">Chọn biểu cảm để xem chuyển động.</span></div>
<div id="lifecycle-panel" role="tabpanel" aria-labelledby="work-tab"><h3 class="expression-section-title">Tám nhịp làm việc <small>Gắn với trạng thái của bot</small></h3><div class="expression-grid" id="expression-grid"></div>
<h3 class="expression-section-title">Những phản xạ nhỏ <small>Khi được gọi, cần hỏi và cùng làm</small></h3><div class="expression-grid" id="expression-extras"></div>
<section class="group-preview" id="group-preview" hidden><p class="group-preview-title">Cùng một nhịp. Chọn người đang nói.</p><div class="group-preview-members" id="group-preview-members"></div></section>
</div><section id="idle-panel" role="tabpanel" aria-labelledby="idle-tab" hidden><h3 class="idle-panel-heading">Rảnh một chút, vui một chút.</h3><p class="idle-panel-copy">Mắt thỉnh thoảng nhìn quanh. Một cử chỉ nhỏ, một câu nói vu vơ — rồi lại yên lặng. Mỗi người bạn có một nhịp riêng.</p><div class="idle-controls"><label><input type="checkbox" id="idle-auto-toggle" checked>Ngẫu nhiên khi rảnh</label><label><input type="checkbox" id="idle-comments-toggle" checked>Thỉnh thoảng nói nhỏ</label><button type="button" class="idle-random-button" id="idle-random"><svg class="icon"><use href="#i-retry"/></svg>Ngẫu nhiên ngay</button></div><div id="idle-expression-grid"></div><p class="idle-preview-hint">Lời nhắn xuất hiện trên thẻ bot. Khi bạn gọi hoặc giao việc, bot trở lại tập trung ngay.</p></section>
<p class="dialog-note">Mỗi bot giữ màu, hình dạng và nhịp chớp mắt riêng. Biểu cảm theo trạng thái công việc; lúc rảnh có thêm cử chỉ và lời nhắn ngẫu nhiên, có thể tắt riêng.</p></div></dialog>
<dialog class="screen-dialog" id="bot-drawer" aria-labelledby="bot-drawer-name"><div class="dialog-heading"><div><h2 id="bot-drawer-name">Thông tin bot</h2><p id="bot-drawer-subtitle">Một người bạn trong đội.</p></div><button class="icon-button dialog-close" aria-label="Đóng"><svg class="icon"><use href="#i-close"/></svg></button></div><div class="dialog-body" id="bot-drawer-body"></div></dialog>
<dialog class="screen-dialog" id="group-dialog" aria-labelledby="group-dialog-title"><div class="dialog-heading"><div><h2 id="group-dialog-title">Một cuộc trò chuyện chung.</h2><p>Chọn những người bạn sẽ cùng tham gia.</p></div><button class="icon-button dialog-close" aria-label="Đóng"><svg class="icon"><use href="#i-close"/></svg></button></div><form class="dialog-body group-form" id="group-form"><label class="sr-only" for="group-title">Tên hội thoại</label><input type="text" id="group-title" maxlength="70" placeholder="Tên cuộc trò chuyện" required><div id="group-options"></div><div class="dialog-actions"><button class="primary-action" type="submit">Tạo hội thoại</button></div></form></dialog>
<dialog class="screen-dialog" id="settings-dialog" aria-labelledby="settings-title"><div class="dialog-heading"><div><h2 id="settings-title">Cài đặt</h2><p>Bot của bạn, giao diện máy này, giới hạn chạy và trạng thái máy chủ — chung cho mọi trang.</p></div><button class="icon-button dialog-close" aria-label="Đóng"><svg class="icon"><use href="#i-close"/></svg></button></div><div class="dialog-body" id="settings-body">
 <section class="paper-card" aria-labelledby="settings-bots-title">
  <div class="eyebrow">Bot của bạn</div>
  <h3 class="card-title" id="settings-bots-title">Người bạn đồng hành</h3>
  <p class="card-copy">Đổi màu và hình dạng giọt mực ở xưởng biểu cảm — mọi lựa chọn giữ nguyên như trước.</p>
  <div class="bot-pick"><span id="settings-bot-seal" class="bot-pick-seal"></span>
   <label class="field" for="settings-bot"><span>Bot</span><select id="settings-bot"></select></label>
   <input type="color" id="settings-bot-color" value="#315e4c" aria-label="Màu của bot" style="width:28px;height:28px;border:0;background:none;padding:0;cursor:pointer;align-self:end">
  </div>
  <div class="form-footer"><button type="button" class="quiet-button" id="open-studio">Xưởng biểu cảm</button></div>
 </section>
 <section class="paper-card" aria-labelledby="settings-ui-title">
  <div class="eyebrow">Giao diện · máy này</div>
  <h3 class="card-title" id="settings-ui-title">Trang trải cho vừa tay</h3>
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
 <section class="paper-card" aria-labelledby="settings-limits-title">
  <div class="eyebrow">Giới hạn &amp; thời gian chạy</div>
  <h3 class="card-title" id="settings-limits-title">Nhịp làm việc của đội</h3>
  <p class="card-copy">Các mức trần áp cho mọi bot: vòng agent, nhớ nóng và ngữ cảnh gửi lên model. Provider, model mặc định và API key chỉnh ở trang riêng; model riêng của từng bot chỉnh ở trang Hồ sơ, mục của bot đó.</p>
  <form id="config-form" novalidate>
   <div id="config-fields"><p class="empty-note">Đang tải cấu hình…</p></div>
   <div class="form-footer">
    <button class="primary-action" type="submit" id="save-config" disabled>Lưu cài đặt</button>
    <span class="form-hint">Các phần không hiển thị (provider, model, MCP, API key) được giữ nguyên.</span>
    <p class="form-status" id="settings-form-status" role="status" hidden></p>
   </div>
  </form>
 </section>
 <section class="paper-card" aria-labelledby="settings-status-title">
  <div class="eyebrow">Trạng thái</div>
  <h3 class="card-title" id="settings-status-title">Máy chủ Thyca</h3>
  <ul class="meta-list">
   <li><span>Backend</span><span class="status-line" id="status-ready">Đang kiểm tra…</span></li>
   <li><span>Phiên bản</span><span id="status-version">…</span></li>
  </ul>
  <div class="form-footer"><button type="button" class="quiet-button" id="status-refresh">Kiểm tra lại</button></div>
 </section></div></dialog>
<div class="toast" role="status" id="toast" hidden></div><div class="sr-only" role="status" aria-live="polite" id="announcer"></div>`;
}

/* The shared paper artwork now lives on .notebook-shell::before (base.css),
   so workspaces no longer inject per-pane SVG ornaments. */

/* Interface preferences shared by every page (moved here from the old
   per-page settings module so they apply globally from startup and
   reload). Persisted under 'thyca.ui.preferences' as
   {typeScale,reduceMotion,ornaments}; same key, shape and defaults the
   old settings page used, so saved values carry over. Missing/unreadable
   storage fails closed to defaults (artwork visible, motion allowed).
   Runs at script load — before shell-app.js defines toggleMotion — so it
   touches the body class directly instead of calling it. */
const PREFS_KEY = 'thyca.ui.preferences';
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
  try {
    if (typeof document === 'undefined' || !document.documentElement) return;
    document.documentElement.dataset.typeScale = next.typeScale;                    // zoom hook in base.css
    document.documentElement.dataset.ornaments = next.ornaments ? 'on' : 'off';    // shared paper artwork hook in base.css
    for (const view of document.querySelectorAll('.workspace')) view.dataset.ornaments = next.ornaments ? 'on' : 'off';
    motionPaused = Boolean(next.reduceMotion) || (typeof reduced !== 'undefined' && reduced.matches);
    document.body.classList.toggle('motion-paused', motionPaused);
  } catch { /* markup still renders; hooks default on next load */ }
  if (save) try { localStorage.setItem(PREFS_KEY, JSON.stringify(next)); } catch { /* controls still apply when storage is unavailable */ }
}
applyPrefs(prefs, { save: false });

/* Synchronous DOM insert. Returns the .workspace element the page fills. */
function emitShell(current, { title = 'Thyca' } = {}) {
  const main = document.createElement('main');
  main.className = `notebook-shell ${current}-shell`;
  main.setAttribute('aria-label', `${title} — ${current}`);
  main.innerHTML = `${thycaHeader(current)}<div class="main-grid">${thycaSidebarMarkup()}<section class="workspace" aria-label="Nội dung ${current}"></section></div>`;
  document.body.prepend(main);
  document.body.insertAdjacentHTML('beforeend', thycaDialogs());
  return $('.workspace', main);
}

/* Async boot: live roster + sessions into the shared sidebar. */
async function bootShell() {
  applyPrefs(readPrefs(), { save: false });  // re-apply the saved preferences for this page
  await loadRoster();                                        // API: GET /api/bots
  renderTeam();
  bots.forEach(updateCard);
  $('.bot-count').textContent = bots.length + ' người bạn';
  rebuildExpressionBots();
  try { await refreshSessions(); renderGroups(); } catch {}  // API: GET /api/sessions
}
