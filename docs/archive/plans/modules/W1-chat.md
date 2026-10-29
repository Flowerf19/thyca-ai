---
status: done
created: 2026-09-22
last_updated: 2026-09-22
---

# W1 — Shell / Chat module plan

## Summary

Module W1 gồm trang trò chuyện (`index.html` — shell mặc định của WebUI) và
logic chat: sidebar phiên (CRUD + pager), render hội thoại, live-turn
streaming/follow/poll, composer (model + reasoning-effort), idle-nudge.
`app.js` (916 dòng) vượt ngưỡng JS (>400) nên phải tách; `chat-view.js`
(442 dòng) vượt nhẹ nên tách settled/live. `reasoning-effort.js` dùng chung
với provider (W5) nên thuộc `shared/`. `navigation.js` load ở mọi trang —
đề xuất giao W6 sở hữu (lý do ở Assumptions), W1 chỉ giữ interface contract.

Nguyên tắc: tách file + scope CSS theo SRP, không đổi hành vi/URL/visual,
không thêm dependency/build step. `.html` giữ flat (URL contract).

## Tasks

### GOAL-001: Physical layout — dời file W1 vào `pages/chat/` + `shared/` (sau layout agent GOAL-002 của plan tổng, hoặc do coding team thực hiện nếu layout chưa chạy)

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | `git mv thyca/webui/app.js -> thyca/webui/pages/chat/app.js` (entry, giữ tên để diff nhỏ) | | |
| TASK-002 | `git mv thyca/webui/backend/chat-view.js -> thyca/webui/pages/chat/chat-view.js`; `git mv thyca/webui/backend/chat-thinking.js -> thyca/webui/pages/chat/chat-thinking.js`; `git mv thyca/webui/backend/chat-status.js -> thyca/webui/pages/chat/chat-status.js` | | |
| TASK-003 | `git mv thyca/webui/backend/reasoning-effort.js -> thyca/webui/shared/js/reasoning-effort.js` (dùng chung app.js + provider.js — bằng chứng `thyca/webui/provider.js:2`) | | |
| TASK-004 | Cập nhật `index.html`: `<script type="module" src="./app.js">` -> `./pages/chat/app.js`; `<script src="./navigation.js">` giữ nguyên cho tới khi W6 dời navigation (lúc đó đổi theo contract W6) | | |
| TASK-005 | Cập nhật import trong `pages/chat/*.js`: `./backend/api.js` -> `../shared/js/api.js` (sau khi W6 dời api/format/markdown — phối hợp path cuối với W6, xem Risks); import nội bộ chat (`./chat-status.js`, `./chat-thinking.js`) thành cùng thư mục; `reasoning-effort.js` thành `../shared/js/reasoning-effort.js` | | |
| TASK-006 | Không dời `navigation.js` trong W1; không dời `styles.css`/`screens.css`/`backend.css` (thuộc W6); không động `provider.js` ngoài việc giữ import reasoning-effort hoạt động (thuộc W5) | | |

### GOAL-002: Tách `app.js` (916 dòng) theo biên SRP — thứ tự tách

Thứ tự: sidebar trước (độc lập nhất), rồi turn-follow, rồi composer, entry
cuối cùng. Mỗi file mới budget ≤300 dòng, export rõ ràng, entry `app.js`
giữ `el`/`state`/orchestration.

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-007 | Tách `pages/chat/sessions-sidebar.js` (budget ≤300): pager (`sessionsPageCount`, `clampPage`, `buildPager`, `syncPager` — `app.js:82-128`), row render (`sessionButton`, `sessionMeta`, `renderSessions`, `refreshSessions`, `revealSession` — `app.js:224-316`), rename/delete dialogs (`openRename`, `submitRename`, `openDelete`, `submitDelete` — `app.js:318-378`); nhận `state`/`el` qua tham số, không import ngược `app.js` | | |
| TASK-008 | Tách `pages/chat/turn-follow.js` (budget ≤200): `followTurn`, `renderPolledProgress`, `watchRunning` (`app.js:439-546`) + sở hữu `liveTurns`/`streamingSessions` registries (`app.js:57-65`); export registry + 3 hàm, `app.js` dùng lại | | |
| TASK-009 | Tách `pages/chat/composer.js` (budget ≤250): `ensureSession`, `composerTurn`, `sendMessage`, `stopTurn`, `retryMessage` (`app.js:592-758`) + idle-nudge (`IDLE_MS`, `idleArmed`, `showIdle`, `armIdle`, `noteSend` — `app.js:45-46,57,163-190`); export các hàm send/stop/retry + armIdle | | |
| TASK-010 | Entry `pages/chat/app.js` còn lại (budget ≤300): `el`, `state`, `renderDetail`, `loadSession`, `newSession`, `fillComposerControls`, `bind`, `boot` + scroll/title helpers; xóa code đã chuyển, giữ nguyên hành vi (409-guard, reveal-rule, generation-guard) | | |

### GOAL-003: Tách `chat-view.js` (442 dòng) settled vs live

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-011 | Tách `pages/chat/transcript.js` (budget ≤250): `userMessage`, `assistantMessage`, `foldToolOnlyParts`, `renderConversation`, `renderEmpty`, `renderError` (`chat-view.js:99-288`); `chat-view.js` re-export để giữ import cũ hoạt động trong khi migrate | | |
| TASK-012 | Tách `pages/chat/live-status.js` (budget ≤200): `createLiveStatus`, `resetLiveStatus`, `updateLiveStatus` + helpers `startThinkingSegment`, `syncRoundUsage`, `replySegment` (`chat-view.js:289-442`); `chat-view.js` còn brand helpers (`chatBrandHeader`, `setChatBrand`) hoặc thành façade re-export — team chọn 1, ghi lý do vào plan khi thực hiện | | |
| TASK-013 | Cập nhật `turn-follow.js`/`composer.js`/`app.js` import từ module mới; xóa `chat-view.js` cũ khi không còn ai import (kiểm tra bằng grep, không đoán) | | |

### GOAL-004: CSS scoping phía W1 (không giành việc W6)

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-014 | Liệt kê selectors chat-only đang nằm global để W6 scope: `.composer*`, `.again`, `.usage-row*`, `.live-card`, `.chat-brand*`, `.thinking-note*`, `.thought-*`, `.reply-live`, `.conversation-*`, `.chat-canvas`, `.music-watermark`, `.to-bottom`, `.session-pager*`, `.session-row`, `.session-actions`, `.session-action` (bằng chứng ở SOLID findings); W1 không tự tách CSS, chỉ không thêm selector global mới | | |
| TASK-015 | Xóa duplicate `.composer.is-loading .send-button` — `styles.css:1240` vs `backend.css:206`: giữ 1 bản (bản nào do W6 quyết, W1 ghi nhận), verify visual composer loading không đổi | | |

## Test Plan

Test gate (chạy focused, không sửa test để pass — các test assert literal
path ở Risks cần orchestrator quyết ở GOAL-002 plan tổng):

- `tests/test_webui_concurrent_streams.py` — ownership concurrent-send (đọc `app.js` source + import `chat-view.js` thật).
- `tests/test_webui_live_recovery.py`, `tests/test_webui_live_rounds.py` — follow/poll recovery + live rounds.
- `tests/test_webui_markdown.py`, `tests/test_webui_stream.py`, `tests/test_webui_format.py` — render markdown/stream/format.
- `tests/test_turn_status.py` (`chat-status.js`), `tests/test_chat_meter.py` — status helpers + meter.
- `tests/test_serve_chat.py`, `tests/test_serve_memory_stats.py::test_index_html_parses` — serve + wiring `index.html`.
- Sau move: full `uv run pytest -q` xanh như baseline 719 passed; `git diff --check` sạch.
- Visual check (chứng minh không vỡ): mở `index.html` qua serve loopback —
  sidebar phiên render + pager, gửi 1 message đi-về (optimistic bubble +
  live card + transcript settled), rename/delete dialog mở được, so với base
  bằng mắt (Chrome screenshot desktop + 1 mobile width theo GOAL-004 plan tổng).

## Assumptions

1. `navigation.js` giao **W6 sở hữu** (shared shell), W1 chỉ giữ contract.
   Lý do: file load ở **mọi** trang (`index.html:153`, `settings.html:64`,
   `profile.html:66`, `dashboard.html:236`, `memories.html:101`,
   `provider.html:96`); nội dung đa-concern không liên quan chat —
   prefs theme (`navigation.js:3-15`), nav dialog + routes
   (`navigation.js:17+`, routes gồm memories/profile/dashboard/settings),
   settings-shell compact (`navigation.js:~130`), `.thyca-search`
   (`navigation.js:~150`); chat chỉ cần menu-button dialog + header
   datetime/edition. 6 teams cùng sửa 1 file là conflict chắc chắn.
   Contract W1 yêu cầu W6 giữ: `.menu-button` opener + dialog
   `#screen-navigation`, `#header-datetime`, `#header-edition` selectors,
   classic `defer` script (không chuyển module — tránh delay menu), fetch
   `/api/config/status` giữ nguyên.
2. `reasoning-effort.js` vào `shared/js/` vì `provider.js:2` import chung với
   `app.js:2`; W5 phối hợp path cuối. `api.js`/`format.js`/`markdown.js`
   thuộc W6 (`shared/js/`); W1 chỉ cập nhật import phía mình.
3. Các test assert literal path (`test_index_html_parses` assert
   `'./app.js' in raw`; `test_webui_concurrent_streams.py:166`,
   `test_webui_live_recovery.py:166` đọc `./thyca/webui/app.js`;
   `test_serve_chat.py:676-678` đọc `WEBUI/app.js` +
   `backend/chat-view.js`) **sẽ đỏ sau move** — việc cập nhật chúng thuộc
   layout GOAL-002/orchestrator duyệt, W1 tuyệt đối không sửa test để pass.
4. `serve/static.py::safe_file` đã phục vụ subdir bất kỳ (resolve +
   `relative_to(webui)` check) — không cần sửa serve; nếu layout agent phát
   hiện khác thì báo orchestrator, ngoài scope W1.
5. DIP/ISP/OCP: `app.js` import trực tiếp concrete backend modules là phù
   hợp MPA không build step — không thêm abstraction/factory. Không nhồi
   pattern vào JS/CSS.
6. Không có deep-link vào chat ngoài `sessionStorage thyca.activeSessionId`
   (`app.js:380-387`) và redirect provider (`app.js:boot`) — giữ cả hai.

## SOLID findings (bằng chứng file:line)

- **SRP — `app.js` 916 dòng trộn 5 concern trong 1 module**: element/state
  (`app.js:15-80`), journal-pager (`app.js:82-128`), sidebar CRUD +
  rename/delete (`app.js:224-378`), scroll/title helpers
  (`app.js:389-406`), live follow/poll (`app.js:439-546`), session
  load/new (`app.js:548-590`), composer send/retry/stop (`app.js:592-758`),
  bind + model/effort controls + boot (`app.js:760-916`). Triệu chứng cụ
  thể: `sendMessage` (`app.js:615`) và `retryMessage` (`app.js:705`) duplicate
  ~50 dòng (ensureSession → streamingSessions → createLiveStatus →
  postNdjson → showTurnDetail → refreshSessions → noteSend → armIdle, cùng
  reveal-rule `pageAtSend`); `composerBusy` (`app.js:192`) phụ thuộc cả
  `state.sending` lẫn `streamingSessions` — logic ownership nằm rải rác.
- **SRP — `chat-view.js` 442 dòng trộn settled + live**: settled transcript
  (`userMessage`/`assistantMessage`/`renderConversation`/`renderEmpty`/`renderError`,
  `chat-view.js:99-288`) vs live NDJSON lifecycle
  (`createLiveStatus`/`resetLiveStatus`/`updateLiveStatus`,
  `chat-view.js:289-442`) — hai lý do đổi khác nhau (đổi markdown render vs
  đổi protocol event), chỉ chia sẻ brand header + usage-row builders.
- **SRP — `navigation.js` 156 dòng, 5 việc không liên quan chat**: prefs
  (`navigation.js:2-15`), nav dialog/routes (`navigation.js:17-~108`),
  header datetime/edition (`navigation.js:~110-128`), settings-shell compact
  (`navigation.js:~130-~148`), `.thyca-search` (`navigation.js:~150-156`).
  (Thuộc W6 — ghi ở đây để biện minh ownership.)
- **SRP/CSS — selectors chat-only nằm global `styles.css`**: `.composer*`
  (`styles.css:1007-1096,1180-1244,1400-1487`), `.again`
  (`styles.css:778-796`), `.usage-row*` (`styles.css:808-827`), `.live-card`/
  `.chat-brand*` (`styles.css:619-~700`), `.conversation-scroll`
  (`styles.css:520-544`) — đổi chat là đổi global, không scope được.
- **SRP/CSS — selector dùng chung cross-page, W1 không được scope một mình**:
  `.session-item`/`.session-name`/`.session-icon` dùng ở chat + memories +
  profile + dashboard (`styles.css:320-418`; bằng chứng dùng chung
  `tests/test_serve_memory_stats.py` assert shape `session-icon`/
  `session-name` cho profile); `.screen-button`/`.screen-dialog`/
  `.screen-input` (`screens.css:707-781`) dùng bởi rename/delete dialogs của
  chat và mọi trang khác; `.session-pager-step` (tạo ở `app.js:91-112`) ăn
  style `.screen-button` global.
- **Duplicated CSS**: `.composer.is-loading .send-button` tồn tại cả ở
  `styles.css:1240` và `backend.css:206` — hai nguồn sự thật cho cùng trạng
  thái loading của composer.
- **DIP/ISP/OCP**: không có bằng chứng vi phạm cụ thể trong W1 (import trực
  tiếp là đúng cho MPA; không có interface thừa hay switch-type cần mở
  rộng) — không thay đổi.

## Risks

1. **`<script>` path trong `index.html`**: `./app.js` ->
   `./pages/chat/app.js`; test `test_index_html_parses` assert literal
   `'./app.js'` — cần orchestrator duyệt cập nhật test ở GOAL-002, W1 không
   tự sửa.
2. **`import` paths**: `app.js:1-13` (`./backend/api.js`,
   `./backend/reasoning-effort.js`, `./backend/chat-status.js`,
   `./backend/format.js`, `./backend/chat-view.js`) và
   `chat-view.js:1-8` (`./chat-status.js`, `./chat-thinking.js`,
   `./format.js`, `./markdown.js`) phải viết lại theo tree mới; path cuối
   của `api/format/markdown` phụ thuộc W6 — chốt cùng orchestrator, verify
   bằng import thật trong node tests (concurrent_streams/live_recovery đã
   import thật nên sẽ bắt lỗi path).
3. **Shared move `reasoning-effort.js` ảnh hưởng W5**: `provider.js:2`
   import cùng file — hai teams phải dùng chung đích `shared/js/`, không mỗi
   team một bản copy.
4. **Cross-page shared selectors**: `.session-item`, `.screen-button`,
   `.screen-dialog` dùng ở ≥3 trang — W1 refactor JS tạo DOM
   (`sessionButton` dùng `session-item`/`session-row`/`session-actions`,
   `buildPager` dùng `screen-button`) phải giữ class names nguyên vẹn cho
   tới khi W6 tách CSS xong.
5. **URL/deep-link**: `index.html` giữ flat (không dời); `boot()` redirect
   `./provider.html?required=1` giữ nguyên; `sessionStorage`
   `thyca.activeSessionId` giữ nguyên key.
6. **`serve/static.py` subdir**: đã hỗ trợ (resolve + relative_to guard) —
   verify bằng probe `GET /pages/chat/app.js` 200 + content-type
   `text/javascript` sau move; nếu fail thì là micro-fix của layout agent,
   không phải W1 logic.
7. **`url()` trong CSS**: W1 không dời CSS/assets nên không ảnh hưởng; ghi
   nhận `styles.css:155,173,380-381,720-721` dùng relative `images/...` — W6
   dời CSS phải rewrite, ngoài scope W1.

## Success criteria (đo được)

1. `pages/chat/app.js` (entry) ≤300 dòng; `sessions-sidebar.js` ≤300;
   `turn-follow.js` ≤200; `composer.js` ≤250; `transcript.js` ≤250;
   `live-status.js` ≤200; không file JS W1 nào >300 dòng.
2. `sendMessage`/`retryMessage` hết duplicate: phần chung (live-turn
   lifecycle) nằm 1 chỗ (`composer.js` helper hoặc `turn-follow.js`), diff
   chứng minh.
3. Không selector global mới; class names DOM (`session-*`, `screen-*`,
   `live-*`, `chat-brand*`, `usage-row`, `again`, `composer*`) giữ nguyên —
   grep diff xác nhận.
4. Test gate: focused tests (concurrent_streams, live_recovery, live_rounds,
   markdown, stream, format, turn_status, chat_meter, serve_chat) xanh sau
   khi orchestrator xử lý path-asserts (mục 3 Assumptions); full
   `uv run pytest -q` 719 passed/0 fail; `git diff --check` sạch.
5. Visual: chat page qua serve — sidebar + pager, 1 vòng gửi/nhận, 2 dialogs
   — giống base (screenshot desktop + 1 mobile width, không vỡ layout).

## Close-out (2026-09-22, orchestrator)
All module tasks landed and verified: branch refactor-webui-W1-chat (redo splits + harness concat), test 719/719, review approve. Merged into refactor/webui-solid, full suite 719 passed, Chrome gate pass, plan status done.
