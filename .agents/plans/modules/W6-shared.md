---
status: in-progress
created: 2026-09-22
last_updated: 2026-09-22
---

# W6 shared — module plan

## Summary

Module W6 là lớp dùng chung của WebUI: `styles.css` (1530 dòng), `screens.css`
(1029), `backend.css` (285), `backend/api.js` (170), `backend/format.js` (148),
`backend/markdown.js` (42), `vendor/marked.esm.js`, `images/` (3 png + 3 svg).
Phát hiện chính: `styles.css` tuy nằm ở "shared" nhưng ~80% là rules
chat-scoped (notebook shell, session list, conversation, composer) mà cả 6
trang đều phải tải; `screens.css` trộn screen chrome với journal/chart kit và
rules chỉ trang Nhật ký dùng. Kế hoạch: tách tokens ra trước, đẩy rules
page-scoped về `pages/<name>/`, giữ kit thật sự chung trong `shared/`,
tách `api.js` thành http/streams, giữ nguyên `format.js`, `vendor/`,
`images/` và toàn bộ URL `.html`.

Mâu thuẫn với ghi chú module: `format.js` + `markdown.js` không nằm ở root mà
nằm ở `backend/` (`backend/format.js`, `backend/markdown.js`). Plan này dùng
đường dẫn thực tế đã verify.

## Tasks

### GOAL-001: Layout vật lý (do layout agent GOAL-002 của plan tổng thực hiện, W6 liệt kê moves của mình)

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | Move JS dùng chung (giữ nguyên nội dung, chỉ đổi path + sửa import): `git mv thyca/webui/backend/api.js thyca/webui/shared/js/api.js`, `git mv thyca/webui/backend/format.js thyca/webui/shared/js/format.js`, `git mv thyca/webui/backend/markdown.js thyca/webui/shared/js/markdown.js` | | |
| TASK-002 | Giữ nguyên vị trí: `thyca/webui/vendor/marked.esm.js`, toàn bộ `thyca/webui/images/` (chỉ `markdown.js` và CSS trỏ tới chúng thì rewrite relative path, không dời assets) | | |
| TASK-003 | Cập nhật mọi consumer theo vị trí mới sau khi GOAL-002 move page JS vào `pages/<name>/`: `pages/chat/app.js`, `pages/dashboard/{cost,request,usage,trace,dashboard}.js`, `pages/memories/memories.js`, `pages/profile/profile.js`, `pages/provider/provider.js` đổi `./backend/api.js` → `../shared/js/api.js` (tương tự `format.js`, `markdown.js`); `shared/js/markdown.js` đổi `../vendor/marked.esm.js` → `../../vendor/marked.esm.js` | | |
| TASK-004 | Cập nhật thứ tự `<link>` trên 6 trang (index, dashboard, memories, profile, provider, settings) giữ nguyên cascade cũ `styles → screens → page → backend` dưới tên mới: `../shared/css/tokens.css → ../shared/css/kit.css → ../shared/css/states.css → ../shared/css/markdown.css → <page>.css`; rewrite `url("images/...")` trong CSS đã dời thành `url("../../images/...")` (styles.css:155,173,380-381,720-721; screens.css:771) | | |
| TASK-005 | Cập nhật path assertions trong tests do đổi contract (liệt kê ở Test Plan, cần orchestrator duyệt — đây là đổi contract đã ghi plan, không phải sửa test để pass) | | |

### GOAL-002: Tách tokens + đẩy CSS page-scoped về đúng trang

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-006 | Tách `shared/css/tokens.css` (budget ≤150 dòng) từ `styles.css:1-127` (`:root` palette/fonts/spacing/radius, `html/body` base, `html[data-type-scale]`, `html[data-theme="dusk"]`, `button/textarea/svg` resets, `.visually-hidden`) + `screens.css:1-14` (`:root` bổ sung, screen base). Không đổi một giá trị token nào | | |
| TASK-007 | Phần còn lại của `styles.css` (notebook-shell, sidebar, session-list/row/actions, workspace, conversation, message-user/assistant, chat-brand, thinking, composer, to-bottom, keyframes, responsive, `.session-pager` placement) move nguyên khối thành `pages/chat/chat.css` (W1 sở hữu refactor tiếp; W6 chỉ tách cơ học, giữ nguyên thứ tự rules) | | |
| TASK-008 | Từ `screens.css` trích `.memory-list/.memory-card/.memory-copy/.memory-tags/.memory-more/.memory-meta` (screens.css:~315-385) sang `pages/memories/` (W4 sở hữu); trích journal-chart kit (`usage-*`, `cost-*` — screens.css:~589-627) sang `pages/dashboard/` (W2/W3 sở hữu, orchestrator phân xử ranh giới cost vs usage) | | |
| TASK-009 | Phần còn lại của `screens.css` thành `shared/css/kit.css` (budget ≤450 dòng): screen-shell/surface/heading/toolbar, filters, `.thyca-search`, row-action, screen-card/badge, journal kit `:is(.dashboard-shell,.trace-shell)`, fold kit, screen-button/input/select, screen-nav, screen-dialog, focus-visible, compact pagers. Journal kit ở lại shared vì dashboard + trace cùng dùng | | |
| TASK-010 | Tách `backend.css` (285) thành `shared/css/markdown.css` (block `.markdown-body*` + `.md-table-wrap`, ~90 dòng) và `shared/css/states.css` (empty/error states, idle-nudge, composer-loading, disabled, screen-status, provider-actions, trace-status bits, media queries, ~195 dòng) | | |

### GOAL-003: SOLID refactor JS shared + khử duplicate CSS

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-011 | Tách `shared/js/api.js` (170) thành `shared/js/http.js` (`ApiError`, `requestJson`, `getJson/postJson/deleteJson/patchJson`, ~70 dòng) và `shared/js/streams.js` (`yieldToRender`, `readNdjson`, `openNdjson`, `postNdjson`, `getNdjson`, ~100 dòng); `api.js` thành re-export barrel để consumer cũ không vỡ trong quá trình chuyển đổi, xóa barrel ở cuối khi mọi consumer đã trỏ thẳng | | |
| TASK-012 | Giữ nguyên `shared/js/format.js` (148 dòng, dưới ngưỡng JS 400, toàn pure functions) — không tách, chỉ sắp lại theo nhóm domain bằng comment sections (escape/text → date/time → number/money → hash/labels) nếu chạm file | | |
| TASK-013 | Giữ nguyên `shared/js/markdown.js` (42 dòng) — chỉ đổi path import vendor theo TASK-003; renderer sanitizer (`safeHref`, escape `html/link/image`, table-wrap postprocess) không đổi hành vi | | |
| TASK-014 | Khử duplicate đã verify: `.conversation-content` (styles.css:544 vs backend.css:4-6) và `.composer.is-loading .send-button` (styles.css:~1240 vs backend.css:~198) — giữ một định nghĩa ở file đúng chủ (chat.css / states.css), xóa bản còn lại, giữ computed style đồng nhất | | |
| TASK-015 | Verify cuối: `uv run pytest -q` full xanh, `git diff --check` sạch, probe serve 200 cho 6 `.html` + `images/*` + `vendor/*`, chụp Chrome 6 trang so base (desktop + 1 mobile width) không vỡ layout | | |

## Test Plan

Focused tests cho W6 (chạy sau mỗi GOAL):

- `tests/test_webui_format.py` — import path mới của `format.js`; nếu GOAL-001 đổi vị trí file thì `SCRIPT` trong test phải trỏ `shared/js/format.js` (contract change đã duyệt ở TASK-005).
- `tests/test_webui_markdown.py` — pin `vendor/marked.esm.js` tồn tại (giữ nguyên vị trí nên test này phải xanh không cần sửa).
- `tests/test_journal_visual.py` — pin scoped contract (pager trong `screens.css`, `.session-pager` placement trong `styles.css`); sau tách, assertions trỏ sang `shared/css/kit.css` và `pages/chat/chat.css` (contract change đã duyệt ở TASK-005).
- `tests/test_serve_chat.py` (đọc `backend.css`, `styles.css`, `backend/api.js` flat + assert `app.js` import `./backend/api.js`), `tests/test_serve_memory_stats.py` (assert literal `'./backend/markdown.js'`, `'./backend/format.js'`) — cập nhật literal paths theo layout mới (contract change đã duyệt ở TASK-005).
- WebUI subset từ plan tổng: `test_webui_concurrent_streams`, `test_webui_live_recovery`, `test_webui_live_rounds`, `test_webui_stream`, `test_webui_memory_edit`, `test_ndjson` + `test_serve_*`.
- Gate cuối: full `uv run pytest -q` (baseline 719 passed / 0 fail), URL probes giữ nguyên (`.html` flat, serve 200; `trace.html` redirect stub không chạm), Chrome screenshot gate 6 trang desktop + mobile so với base — vỡ layout là fail.
- Không sửa test để pass ngoài các path updates đã liệt kê ở TASK-005.

## Assumptions

1. `.html` giữ flat (URL contract); `trace.html` redirect stub không thuộc W6, không chạm.
2. `vendor/marked.esm.js` và `images/` giữ nguyên vị trí — test markdown pin vendor path, CSS `url()` chỉ rewrite relative prefix; không thêm dependency hay build step.
3. Thứ tự cascade sau tách phải tương đương thứ tự link cũ trên mỗi trang; file page CSS load sau shared CSS như hiện tại.
4. Ranh giới với W1/W2/W3/W4: W6 tách cơ học và giao `pages/chat/chat.css` cho W1, `pages/dashboard/` chart kit cho W2/W3, `pages/memories/` rules cho W4; orchestrator phân xử nếu tranh chấp selector.
5. `format.js` không tách (dưới ngưỡng, pure, consumers dùng subsets chồng lấn); `api.js` tách vì streaming chỉ chat dùng (ISP/SRP có evidence).
6. Mọi test path updates được liệt kê trước ở TASK-005/Test Plan và cần orchestrator duyệt theo quy tắc plan tổng ("không sửa test để pass trừ khi contract đổi có ghi trong module plan đã duyệt").

## Phụ lục: SOLID findings (evidence)

- SRP — `styles.css` một file gánh 5 mối quan tâm: design tokens (`:root` styles.css:7), element base (styles.css:70-127), chat shell/sidebar (styles.css:140-470), conversation/brand (styles.css:498-900), composer/responsive/pager (styles.css:900-1530). Cả 6 trang đều link file này (index.html:14, dashboard.html:12, memories.html:12, profile.html:12, provider.html:12, settings.html:12) nên trang Settings/Provider phải tải ~1300 dòng rules chat không bao giờ dùng.
- SRP — `screens.css` trộn screen chrome (`.screen-shell/.screen-surface/.screen-heading` screens.css:17-60), search kit (`.thyca-search` screens.css:161-270), journal kit (`:is(.dashboard-shell,.trace-shell)` screens.css:391-537), chart kit (`.usage-*/.cost-*` screens.css:589-627), dialog/nav/buttons (screens.css:700-800). `.memory-*` (screens.css:315-385) chỉ trang Nhật ký dùng nhưng 5 trang còn lại vẫn tải.
- SRP — `backend.css` trộn markdown rendering (`.markdown-body` backend.css:60-150), empty/error states (backend.css:14-58), chat nudge (`.chat-idle-nudge` backend.css:160-198), provider actions (`provider-actions` backend.css:225-232), trace bits (`#trace-status` backend.css:233-240).
- SRP/ISP — `backend/api.js` gộp JSON-RPC (`requestJson` api.js:12-50 + verbs api.js:52-84) với NDJSON streaming (`readNdjson/openNdjson/postNdjson/getNdjson` api.js:86-170); streaming chỉ `app.js` (chat) dùng — mọi consumer khác (`cost.js:1`, `usage.js:1`, `request.js:1`, `memories.js:1`, `profile.js:1`, `provider.js:1`, `trace.js:1`) chỉ cần `getJson/postJson` nhưng phụ thuộc cả module stream.
- Duplication — `.conversation-content` định nghĩa 2 lần (styles.css:544 vs backend.css:4); `.composer.is-loading .send-button` định nghĩa 2 lần (styles.css:~1240 vs backend.css:~198).
- DIP/OCP — không vi phạm đáng kể: pages phụ thuộc trực tiếp ESM functions thuần (không có abstraction thừa để tách), theming mở qua CSS vars + `html[data-theme]` (OCP tốt, giữ nguyên).

## Phụ lục: Risks

1. `<link>` order/cascade: đổi tên + thứ tự file CSS có thể đổi specificity thực tế nếu đảo thứ tự; giảm thiểu bằng mapping 1-1 thứ tự cũ → mới ở TASK-004 và screenshot gate.
2. `url()` trong CSS: `styles.css:155,173,380-381,720-721` và `screens.css:771` trỏ `images/` relative — sau khi CSS vào `shared/css/` phải thêm prefix `../../`, miss một chỗ là vỡ background/icon. Verify bằng probe serve 200 từng asset + screenshot.
3. Cross-page shared selectors: `.screen-button/.screen-input/.screen-nav/.thyca-search` dùng ở mọi trang — ở lại `kit.css`, không cho page nào override global; page CSS chỉ scoped selector.
4. Pager contract (`test_journal_visual.py`): block pager chung hook class pager, không hook `.screen-button` — khi tách phải giữ nguyên block, không "tiện tay" gộp vào button base.
5. `serve/static.py` `safe_file` resolve mọi subpath dưới `webui/` nên phục vụ `shared/` + `pages/` không cần sửa serve; rủi ro còn lại là quên cập nhật `<link>/<script>/import` dẫn 404 — probe toàn bộ paths sau move.
6. Test literals (`./backend/api.js`, `./backend/format.js`, `./backend/markdown.js`, flat css names) vỡ sau move — đã liệt kê cập nhật ở TASK-005, thuộc contract change duyệt trước, không phải sửa test để pass.
