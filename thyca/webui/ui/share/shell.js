'use strict';

function thycaHeader(current) {
  const currentLabel = (Array.isArray(SHELL_PAGES) && SHELL_PAGES.find(([id]) => id === current)?.[1]) || 'Trò chuyện';
  return `<header class="site-header"><div class="header-left"><button class="icon-button menu-button" type="button" aria-label="Mở mục lục" aria-controls="chat-sidebar" aria-expanded="false"><svg class="icon"><use href="#i-menu"/></svg></button><span class="wordmark" aria-hidden="true">thyca</span><span class="header-rule"></span></div><div class="header-right"><button type="button" class="page-menu-toggle" id="page-menu-toggle" aria-expanded="false" aria-controls="page-links-nav" aria-haspopup="true"><span class="page-menu-label">${escapeHTML(currentLabel)}</span></button><nav class="page-links" id="page-links-nav" aria-label="Điều hướng">${pageLinksMarkup(current)}</nav></div></header>`;
}

function thycaSidebarMarkup() {
  return `<aside class="sidebar" id="chat-sidebar" aria-label="Đội bot">
 <div class="sessions-heading"><h2 id="team-nav-title">Đội</h2><span class="bot-count">0 người bạn</span></div>
 <div class="side-scroll"><nav class="session-list" id="team-bot-list" aria-label="Bot trong đội"></nav></div>
 <div class="rail-scroll"><nav class="rail-avatar-list" id="rail-avatar-list" aria-label="Bot trong đội"></nav></div>
</aside>
<button type="button" class="splitter" aria-label="Thu gọn thanh bot" aria-controls="chat-sidebar" aria-expanded="true" title="Thu gọn/Mở rộng thanh bot">‹</button>`;
}

function thycaDialogs() {
  return `<div class="mobile-overlay" id="mobile-overlay" hidden></div>
<dialog class="screen-dialog" id="expressions-dialog" aria-label="Xưởng biểu cảm">
<button class="icon-button dialog-close expressions-close" aria-label="Đóng"><svg class="icon"><use href="#i-close"/></svg></button>
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
<dialog class="screen-dialog" id="group-dialog" aria-labelledby="group-dialog-title"><div class="dialog-heading"><div><h2 id="group-dialog-title">Một cuộc trò chuyện chung.</h2><p>Chọn những người bạn sẽ cùng tham gia.</p></div><button class="icon-button dialog-close" aria-label="Đóng"><svg class="icon"><use href="#i-close"/></svg></button></div><form class="dialog-body group-form" id="group-form"><label class="group-name-label" for="group-name">Tên nhóm (không bắt buộc, nên 3–5 từ)</label><input type="text" id="group-name" name="group-name" maxlength="80" placeholder="Ví dụ: Đội hỗ trợ Discord" autocomplete="off"><small class="group-name-hint">Tên ngắn 3–5 từ; để trống nhóm sẽ hiện “Hội thoại chung”.</small><div id="group-options"></div><div class="dialog-actions"><button class="primary-action" type="submit">Tạo hội thoại</button></div></form></dialog>
<div class="toast" role="status" id="toast" hidden></div><div class="sr-only" role="status" aria-live="polite" id="announcer"></div>`;
}

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
    document.documentElement.dataset.typeScale = next.typeScale;
    document.documentElement.dataset.ornaments = next.ornaments ? 'on' : 'off';
    for (const view of document.querySelectorAll('.workspace')) view.dataset.ornaments = next.ornaments ? 'on' : 'off';
    motionPaused = Boolean(next.reduceMotion) || (typeof reduced !== 'undefined' && reduced.matches);
    document.body.classList.toggle('motion-paused', motionPaused);
  } catch {  }
  if (save) try { localStorage.setItem(PREFS_KEY, JSON.stringify(next)); } catch {  }
}
applyPrefs(prefs, { save: false });

function emitShell(current, { title = 'Thyca' } = {}) {
  const main = document.createElement('main');
  main.className = `notebook-shell ${current}-shell`;
  main.setAttribute('aria-label', `${title} — ${current}`);
  main.innerHTML = `${thycaHeader(current)}<div class="main-grid">${thycaSidebarMarkup()}<section class="workspace" aria-label="Nội dung ${current}"></section></div>`;
  document.body.prepend(main);
  document.body.insertAdjacentHTML('beforeend', thycaDialogs());
  return $('.workspace', main);
}

async function bootShell() {
  applyPrefs(readPrefs(), { save: false });
  await loadRoster();
  renderTeam();
  bots.forEach(updateCard);
  $('.bot-count').textContent = bots.length + ' người bạn';
  rebuildExpressionBots();
  try { await refreshConversations(); renderGroups(); renderHeaderBots(); } catch {}
}
