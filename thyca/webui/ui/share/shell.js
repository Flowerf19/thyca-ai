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
 <footer class="sidebar-footer"><button id="open-customize"><svg class="icon"><use href="#i-face"/></svg>Custom bot</button><button class="motion-button" id="motion-toggle" aria-label="Tạm dừng chuyển động" aria-pressed="false" title="Tạm dừng chuyển động"><svg class="icon"><use href="#i-pause"/></svg></button></footer>
</aside>
<div class="splitter" role="separator" tabindex="0" aria-label="Thay đổi chiều rộng thanh bot" aria-orientation="vertical" aria-valuemin="310" aria-valuemax="470" aria-valuenow="374" title="Kéo để thay đổi chiều rộng · dùng phím trái/phải"></div>`;
}

/* The same dialogs chat ships statically — needed before studio.js binds. */
function thycaDialogs() {
  return `<div class="mobile-overlay" id="mobile-overlay" hidden></div>
<dialog class="screen-dialog" id="expressions-dialog" aria-labelledby="expressions-title">
<div class="dialog-heading"><div><h2 id="expressions-title">Một con dấu, nhiều tâm trạng.</h2><p>Đôi mắt kể chuyện, vành dấu giữ nhịp. Thử từng biểu cảm hoặc xem trọn một lượt làm việc.</p></div><button class="icon-button dialog-close" aria-label="Đóng"><svg class="icon"><use href="#i-close"/></svg></button></div>
<div class="dialog-body"><div class="dialog-controls"><label for="expression-bot">Người bạn</label><select id="expression-bot"></select><label for="bot-color">Màu bot</label><input type="color" id="bot-color" value="#315e4c" aria-label="Màu của bot" style="width:28px;height:28px;border:0;background:none;padding:0;cursor:pointer"><div class="palette-preview" id="palette-preview"></div></div>
<div class="studio-tabs" role="tablist" aria-label="Nhóm biểu cảm"><button type="button" class="studio-tab" id="work-tab" role="tab" aria-selected="true" aria-controls="lifecycle-panel">Khi làm việc <small>11</small></button><button type="button" class="studio-tab" id="idle-tab" role="tab" aria-selected="false" aria-controls="idle-panel" tabindex="-1">Lúc rảnh <small>8</small></button></div>
<section class="expression-stage" id="expression-stage" aria-label="Biểu cảm đang xem"><div class="stage-portrait" id="lab-portrait"></div><div class="stage-copy"><div class="stage-kicker"><span class="status-dot"></span><span id="lab-bot-name">thyca</span><span>·</span><span>Cận cảnh</span></div><h3 id="lab-state"></h3><p id="lab-description"></p><p class="lab-speech" id="lab-speech"></p></div></section>
<div class="lab-actions"><button type="button" class="lab-primary" id="lab-play"><svg class="icon"><use href="#i-play"/></svg>Chạy trọn một lượt</button><button type="button" id="lab-replay"><svg class="icon"><use href="#i-retry"/></svg>Phát lại biểu cảm</button><button type="button" id="lab-done"><svg class="icon"><use href="#i-star"/></svg>Thử hoàn tất</button><button type="button" id="lab-stop" hidden>Dừng</button><span id="lab-playback-note">Chọn biểu cảm để xem chuyển động.</span></div>
<div id="lifecycle-panel" role="tabpanel" aria-labelledby="work-tab"><h3 class="expression-section-title">Tám nhịp làm việc <small>Gắn với trạng thái của bot</small></h3><div class="expression-grid" id="expression-grid"></div>
<h3 class="expression-section-title">Những phản xạ nhỏ <small>Khi được gọi, cần hỏi và cùng làm</small></h3><div class="expression-grid" id="expression-extras"></div>
<section class="group-preview" id="group-preview" hidden><p class="group-preview-title">Cùng một nhịp. Chọn người đang nói.</p><div class="group-preview-members" id="group-preview-members"></div></section>
</div><section id="idle-panel" role="tabpanel" aria-labelledby="idle-tab" hidden><h3 class="idle-panel-heading">Rảnh một chút, vui một chút.</h3><p class="idle-panel-copy">Mắt thỉnh thoảng nhìn quanh. Một cử chỉ nhỏ, một câu nói vu vơ — rồi lại yên lặng. Mỗi người bạn có một nhịp riêng.</p><div class="idle-controls"><label><input type="checkbox" id="idle-auto-toggle" checked>Ngẫu nhiên khi rảnh</label><label><input type="checkbox" id="idle-comments-toggle" checked>Thỉnh thoảng nói nhỏ</label><button type="button" class="idle-random-button" id="idle-random"><svg class="icon"><use href="#i-retry"/></svg>Ngẫu nhiên ngay</button></div><div id="idle-expression-grid"></div><p class="idle-preview-hint">Lời nhắn xuất hiện trên thẻ bot. Khi bạn gọi hoặc giao việc, bot trở lại tập trung ngay.</p></section>
<p class="dialog-note">Mỗi bot giữ màu và nhịp chớp mắt riêng. Biểu cảm theo trạng thái công việc; lúc rảnh có thêm cử chỉ và lời nhắn ngẫu nhiên, có thể tắt riêng.</p></div></dialog>
<dialog class="screen-dialog" id="bot-drawer" aria-labelledby="bot-drawer-name"><div class="dialog-heading"><div><h2 id="bot-drawer-name">Thông tin bot</h2><p id="bot-drawer-subtitle">Một người bạn trong đội.</p></div><button class="icon-button dialog-close" aria-label="Đóng"><svg class="icon"><use href="#i-close"/></svg></button></div><div class="dialog-body" id="bot-drawer-body"></div></dialog>
<dialog class="screen-dialog" id="group-dialog" aria-labelledby="group-dialog-title"><div class="dialog-heading"><div><h2 id="group-dialog-title">Một cuộc trò chuyện chung.</h2><p>Chọn những người bạn sẽ cùng tham gia.</p></div><button class="icon-button dialog-close" aria-label="Đóng"><svg class="icon"><use href="#i-close"/></svg></button></div><form class="dialog-body group-form" id="group-form"><label class="sr-only" for="group-title">Tên hội thoại</label><input type="text" id="group-title" maxlength="70" placeholder="Tên cuộc trò chuyện" required><div id="group-options"></div><div class="dialog-actions"><button class="primary-action" type="submit">Tạo hội thoại</button></div></form></dialog>
<div class="toast" role="status" id="toast" hidden></div><div class="sr-only" role="status" aria-live="polite" id="announcer"></div>`;
}

/* The workspace SVG ornaments chat draws inline — same strokes. */
const WORKSPACE_ART = `<svg class="ornament" viewBox="0 0 280 520" fill="none" stroke="currentColor" stroke-width="1" aria-hidden="true"><path d="M228 510C200 410 87 346 117 225S210 105 237 11"/><path d="M211 465C138 458 124 414 123 393c43 7 67 35 88 72ZM181 419c54-33 63-73 55-102-38 31-49 66-55 102ZM140 357c-70-13-91-44-92-77 43 10 76 41 92 77ZM120 301c59-17 79-52 82-86-39 15-69 48-82 86ZM119 235c-42-25-48-58-42-88 28 20 41 49 42 88ZM151 169c44 2 72-18 88-48-37-1-68 17-88 48ZM184 119c-31-31-33-58-22-83 20 23 26 52 22 83ZM220 59c33-3 47-26 50-47-27 7-44 23-50 47Z"/></svg>
<svg class="staff" viewBox="0 0 420 300" fill="none" stroke="currentColor" aria-hidden="true"><path d="M0 140Q180 180 420 60M0 152Q180 192 420 72M0 164Q180 204 420 84M0 176Q180 216 420 96M0 188Q180 228 420 108"/><path d="M220 142v-58l37-8v55" stroke-width="2"/><ellipse cx="214" cy="144" rx="8" ry="5" fill="currentColor"/><ellipse cx="251" cy="133" rx="8" ry="5" fill="currentColor"/></svg>`;

/* Synchronous DOM insert. Returns the .workspace element the page fills. */
function emitShell(current, { title = 'Thyca', ornaments = true } = {}) {
  const main = document.createElement('main');
  main.className = `notebook-shell ${current}-shell`;
  main.setAttribute('aria-label', `${title} — ${current}`);
  main.innerHTML = `${thycaHeader(current)}<div class="main-grid">${thycaSidebarMarkup()}<section class="workspace" aria-label="Nội dung ${current}">${ornaments ? WORKSPACE_ART : ''}</section></div>`;
  document.body.prepend(main);
  document.body.insertAdjacentHTML('beforeend', thycaDialogs());
  return $('.workspace', main);
}

/* Async boot: live roster + sessions into the shared sidebar. */
async function bootShell() {
  await loadRoster();                                        // API: GET /api/bots
  renderTeam();
  bots.forEach(updateCard);
  $('.bot-count').textContent = bots.length + ' người bạn';
  rebuildExpressionBots();
  try { await refreshSessions(); renderGroups(); } catch {}  // API: GET /api/sessions
}
