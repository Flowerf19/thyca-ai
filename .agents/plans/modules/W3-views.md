---
status: in-progress
created: 2026-09-22
last_updated: 2026-09-22
---

# W3 — Cost + Trace views (module plan)

## Summary

Module W3 gồm 7 file trong `thyca/webui/`: `cost.js` (451 dòng) + `cost.css`
(137) + `backend/cost-data.js` (263) là Chi phí journal; `trace.js`
(1016 dòng) + `trace.css` (222) + `backend/trace-data.js` (409) là Trace
journal; `trace.html` là redirect stub giữ flat. Cả hai views đều render
bên trong `dashboard.html` (blocks `#chi-phi`, `#trace`, thuộc sở hữu của
W2) và không sở hữu file `.html` nào ngoài stub. Kế hoạch: move cơ học 6
file JS/CSS vào `pages/dashboard/` (giữ `trace.html` + `dashboard.html`
flat theo URL contract), sau đó refactor SRP — trọng tâm là tách
`trace.js` (file JS lớn nhất repo) và loại bỏ pager duplicate giữa
`cost.js`/`trace.js` bằng pager dùng chung do W6 sở hữu. Không đổi hành
vi, visual, URL, deep-link `?session=&turn=` và hash `#trace`.

Phạm vi ranh giới với W2/W6 (đã đọc code, không đoán):

- W2 sở hữu `dashboard.html`, `dashboard.js`, `request.js`, `usage.js`,
  `backend/dashboard-today.js`, `backend/analytics-data.js`. W3 chỉ đọc:
  `cost.js` gọi `fetchAllTraces` từ `dashboard-today.js` (`cost.js:2`) và
  `rollingRange`/`selectModels`/`splitPromptTokens` từ `analytics-data.js`
  (`cost.js:15-19`); `cost.css` chia sẻ selectors với section Request của
  W2 (xem SOLID-003).
- W6 sở hữu `backend/api.js`, `backend/format.js`, `screens.css`
  (`.journal-*` kit), pager dùng chung tương lai. W3 chỉ đọc, không tách
  các file này.
- `cost-data.js` import `selectedModelConfig`/`tokenCost` từ
  `trace-data.js` (`cost-data.js:13`); `cost.js:296` link
  `./trace.html?session=...` đi qua stub redirect — cả hai phải giữ
  nguyên hành vi sau move.

## Tasks

### GOAL-001: Di dời cơ học vào `pages/dashboard/` (layout, không refactor logic)

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | Chạy đúng 6 lệnh move sau trên nhánh team từ `refactor/webui-solid` (sau commit layout GOAL-002 của orchestrator, không tự move nếu layout agent đã làm): `git mv thyca/webui/cost.js thyca/webui/pages/dashboard/cost.js`, `git mv thyca/webui/cost.css thyca/webui/pages/dashboard/cost.css`, `git mv thyca/webui/trace.js thyca/webui/pages/dashboard/trace.js`, `git mv thyca/webui/trace.css thyca/webui/pages/dashboard/trace.css`, `git mv thyca/webui/backend/cost-data.js thyca/webui/pages/dashboard/cost-data.js`, `git mv thyca/webui/backend/trace-data.js thyca/webui/pages/dashboard/trace-data.js`. `trace.html` và `dashboard.html` giữ flat, không đụng vào. | | |
| TASK-002 | Cập nhật đúng các path tham chiếu sau, không sửa logic: `dashboard.html:10-15` (`./cost.css`, `./trace.css` → `./pages/dashboard/cost.css`, `./pages/dashboard/trace.css`) và `dashboard.html:228-231` (`./cost.js`, `./trace.js` → `./pages/dashboard/...`); import trong `cost.js:1-20` (`./backend/api.js`, `./backend/dashboard-today.js`, `./backend/cost-data.js`, `./backend/analytics-data.js`, `./backend/format.js`) và `trace.js:1-23` (`./backend/api.js`, `./backend/analytics-data.js`, `./backend/format.js`, `./backend/trace-data.js`) trỏ đúng vị trí mới (tương đối từ `pages/dashboard/`: `../...` hoặc `shared/...` theo layout đã duyệt, thống nhất với W2/W6); import nội bộ `cost-data.js:11-13` (`./format.js`, `./analytics-data.js`, `./trace-data.js`) và `trace-data.js:1` (`./format.js`) tương ứng. Giữ `cost.js:296` (`./trace.html?session=...`) nguyên vì `trace.html` vẫn flat. | | |
| TASK-003 | Verify sau move: `uv run pytest tests/test_cost_journal.py tests/test_trace_journal.py tests/test_dashboard_journal.py tests/test_journal_visual.py tests/test_webui_markdown.py tests/test_serve_memory_stats.py -q` xanh (cập nhật path cứng trong tests ở TASK-009 nếu layout đổi path), serve `dashboard.html`/`trace.html` trả 200, `git diff --check` sạch. | | |

### GOAL-002: Tách `trace.js` (1016 dòng) + `cost.js` (451) theo SRP

Thứ tự tách (mỗi bước xong phải chạy lại test gate TASK-010 trước khi sang bước tiếp):

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-004 | Bước 0 — dùng pager dùng chung: xoá block duplicate `cost.js:45-93` và `trace.js:38-76` (`JOURNAL_PAGE_SIZE`, `journalPageCount`, `journalClampPage`, `buildPager`, `syncPager` — hai bản copy giống hệt nhau), import pager từ `shared/js/` do W6 cung cấp (nếu W6 chưa xong, W3 tạm import từ vị trí pager đã duyệt trong layout commit, không tự tạo file shared thứ hai). Cập nhật khối `// >>> journal-pager` mà `test_cost_journal.py:379-381` và `test_trace_journal.py:367-371` extract — xem TASK-009. Ngân sách: `cost.js` giảm ~49 dòng → còn ~400; `trace.js` giảm ~39 dòng → còn ~975. | | |
| TASK-005 | Bước 1 — tách `trace.js` thành 3 file trong `pages/dashboard/`, mỗi file ≤ 400 dòng, giữ nguyên export names và DOM ids: `trace-view.js` (~300 dòng: `el`, `state`, `boot`, `loadAllTurns`, `applyTraceResult`, `reloadTrace`, `renderSessions`, `sessionEntry`, `selectGroup`, `backToSessions`, `showSession`, pager session-list) + `trace-turns.js` (~350 dòng: `turnEntry`, `renderTurns`, `stepEntry`, `stepDataFold`, `payloadSection`, `payloadCode`, `ioBlock`, `renderStepsInto`, `turnDiagnostics`, `fillTurnBody`, `ensureDetail`, `invalidateTurnDetails`, `turnStateFor`, `stampNode`, `sessionCostLabel`, `noteNode`) + `trace-deeplink.js` (~280 dòng: `resolveDeepLink`, `openTurn`, `deepLinkEntry`, `settleDeepLinkReveal`, `watchReveal`, `watchTraceViewVisibility`, `isRendered`, `isInViewport`, `revealScrollOptions`, `syncUrl`, `syncSessionUrl`, `clearTraceUrl`, `deepLinkUnavailable`, `showTimezoneWarning`, `bind`, `setStatus`, `setNote`, `updateNote`, `messageOf`, `activeGroup`). `trace.js` còn lại là entry ~60 dòng import 3 module và gọi `boot()`. Không đổi `data-turn-index`, thứ tự render, hay URL side-effects. | | |
| TASK-006 | Bước 2 — tách `cost.js` (~400 sau TASK-004) thành 2 file, mỗi file ≤ 350 dòng: `cost-view.js` (state `el`/`view`/`sort`/`generation`, `load`, `render`, `renderOverview`, `coverageNotes`, `setCoverage`, `resetView`, event bindings) + `cost-rows.js` (`modelEntry`, `sessionEntry`, `pricingDetails`, `renderModels`, `renderSessions`, `metricValue`, `metricMeta`, `partialNote`, `emptyItem`, `openPricing`). Pager models/sessions dùng pager chung từ TASK-004. | | |
| TASK-007 | Bước 3 — tách `backend/trace-data.js` (409 dòng) thành 2 file, mỗi file ≤ 300 dòng, giữ nguyên mọi export name (tests import trực tiếp): `trace-data.js` giữ grouping + paging (`groupTraceTurns`, `collectTracePages`, `finiteCost`, `finiteMs`) + time (`traceTimeFormatter`, `formatTraceTimestamp`, `formatStepPayload`, `formatRecordText`) và `trace-steps.js` mới chứa step/tool parsing (`executionStepsFromDetail`, `activityStepsFromDetail`, `toolBatchesFromDetail`, `toolBatches`, `toolResults`, `toolCallsFromAssistant`, `toolsFromDetail`, `groupToolCalls`, `toolDisplayName`, `asArguments`, `firstUserText`, `finalAssistantText`). Riêng `selectedModelConfig` + `tokenCost` (`trace-data.js:384-397`) chuyển sang `cost-data.js` (nơi duy nhất dùng: `cost-data.js:13,255-262`), xoá import chéo `cost-data.js:13` khỏi `trace-data.js` và bỏ comment tránh-cycle ở `trace-data.js:1-4` (xem SOLID-005). `backend/cost-data.js` (263 + ~15 dòng pricing = ~280) không tách thêm. | | |
| TASK-008 | Scope CSS: `cost.css` — đổi các selectors dùng chung với Request (`cost.css:15-60`: `.cost-overview`, `.cost-total`, `.cost-chart-card` và `cost.css:74-89`: `.cost-chart-area`, `.cost-chart-line`, `.cost-chart-point` do `request.js:99` vẽ) thành rules có scope theo section (`#chi-phi ...` / `#request ...`) hoặc chuyển phần của Request sang file W2 sở hữu (quyết chung với W2, orchestrator duyệt; mặc định: giữ file, thêm scope, không đổi visual). Giữ nguyên `#chi-phi h2.cost-journal-heading` (`cost.css:64`), `#request h2.cost-model-heading` (`cost.css:68`), `#chi-phi details.cost-pricing` (`cost.css:92-104`). `trace.css` đã scope `#trace` toàn file — không tách, chỉ cập nhật path nếu cần. Không thêm token màu/font mới; `.trace-code` giữ `var(--trace-code-bg/ink)` (`trace.css:203-221`). | | |

### GOAL-003: Khóa hợp đồng redirect + deep-link và test gate

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-009 | Liệt kê mọi path cứng mà tests pin tới file W3 và đề xuất cập nhật cơ học tương ứng (không đổi assertion logic, orchestrator duyệt riêng vì chạm tests ngoài module): `test_cost_journal.py:20-22` (`backend/cost-data.js`, `backend/dashboard-today.js`, `cost.js`), `test_trace_journal.py:20-21` (`backend/trace-data.js`, `trace.js`) + extract block pager `test_cost_journal.py:379-381` / `test_trace_journal.py:367-371` (sau TASK-004 phải trỏ sang pager shared hoặc giữ shim re-export), `test_webui_markdown.py:204-231,303-371` (`trace.css`, `trace.html`, `trace.js`, `cost.js`, `cost.css`), `test_journal_visual.py:45-76` (`cost.css`, `trace.css`), `test_serve_memory_stats.py:221-226,311-323` (`trace.html`, `trace.js`, `cost.js`, `dashboard.html` chứa `./cost.js`), `test_dashboard_journal.py:354` (`cost.js`), `test_serve_chat.py:1513` (`trace.js` selectors), `test_chat_meter.py:51` (`cost.js` trong haystack). Coder W3 không tự sửa tests — gửi danh sách này cho orchestrator/W6-test. | | |
| TASK-010 | Test gate của W3 (chạy sau mỗi TASK-004→008): `uv run pytest tests/test_cost_journal.py tests/test_trace_journal.py tests/test_dashboard_journal.py tests/test_journal_visual.py tests/test_webui_markdown.py tests/test_serve_memory_stats.py tests/test_serve_chat.py tests/test_chat_meter.py -q` toàn xanh + `git diff --check` sạch. Check thủ công bằng serve thật: mở `dashboard.html#trace`, `dashboard.html#chi-phi`, `trace.html?session=<id>&turn=<n>` redirect giữ nguyên params và mở đúng lượt (so với base), back ra picker thì URL sạch params (`clearTraceUrl`). | | |

## Test Plan

- Baseline 2026-09-22: full pytest 719 passed / 0 fail. Mọi fail mới là regression; cấm sửa test để pass trừ cập nhật path cơ học đã duyệt ở TASK-009.
- Focused gate (W3 chạy sau mỗi bước tách): `test_cost_journal.py`
  (cost-data pure helpers + snapshot semantics null-vs-zero + pager block),
  `test_trace_journal.py` (execution steps, deep-link time helpers,
  `collectTracePages`, pager block, boot lifecycle với fake DOM),
  `test_dashboard_journal.py` (four-view contract, `?session=` mở Trace,
  `fetchAllTraces`), `test_journal_visual.py` (pager shared trong
  `screens.css`, `.trace-code` opaque scoped), `test_webui_markdown.py::test_trace_typography_matches_profile_screen`
  + `::test_cost_panel_renders_bar_rows_and_uses_shared_toolbar`
  (redirect stub byte-check + `#trace` scoping + journal kit),
  `test_serve_memory_stats.py` (serve 200 + redirect assertions),
  `test_serve_chat.py::test_chat_row_uses_the_shared_session_item`,
  `test_chat_meter.py` (haystack gồm `cost.js`).
- URL/deep-link gate: `trace.html` redirect stub byte-for-byte
  (`location.replace("./dashboard.html" + location.search + "#trace")` +
  noscript refresh + link fallback); `dashboard.js` deep-link
  (`?session=`/`?turn=` mở Trace) và `trace.js:resolveDeepLink` +
  `syncUrl`/`syncSessionUrl`/`clearTraceUrl` giữ nguyên hành vi đã test
  trong `test_dashboard_journal.py` và `test_trace_journal.py:600+`.
- Visual gate: chụp Chrome `dashboard.html#chi-phi` và `#trace`
  (picker + 1 session mở disclosure) ở desktop 1440 + mobile ~375, theme
  light + dusk, so với base — vỡ layout là fail, chênh font/rendering
  cho qua. Riêng `.trace-code` vs base phải cùng màu nền/chữ ở cả 2
  theme (assert đã có trong `test_journal_visual.py` là điều kiện cần,
  screenshot là điều kiện đủ).

## SOLID findings (có file:line)

- SOLID-001 (SRP — pager duplicate, ưu tiên cao nhất): `cost.js:45-93`
  và `trace.js:38-76` là hai bản copy giống hệt
  (`JOURNAL_PAGE_SIZE=12`, `journalPageCount`, `journalClampPage`,
  `buildPager`, `syncPager`). Hai views cùng phụ thuộc một kit phân
  trang nhưng mỗi file tự sở hữu một bản — sửa pager phải sửa 2 nơi.
  Bằng chứng: tests phải extract cùng một block ở cả hai file
  (`test_cost_journal.py:379-381`, `test_trace_journal.py:367-371`).
  Fix ở TASK-004 (dùng pager shared của W6).
- SOLID-002 (SRP — `trace.js` 1016 dòng trộn 6 mối quan tâm trong một
  module phẳng): boot/load (`boot:928`, `loadAllTurns:806`,
  `applyTraceResult:958`, `reloadTrace:1005`), danh sách session
  (`renderSessions:303`, `sessionEntry:268`), journal lượt/bước
  (`renderTurns:409`, `turnEntry:343`, `stepEntry:552`,
  `renderStepsInto:621`, `fillTurnBody:724`, `ensureDetail:690`),
  deep-link + reveal (`resolveDeepLink:829`, `openTurn:876`,
  `deepLinkEntry:100`, `settleDeepLinkReveal:141`,
  `watchReveal:118`, `watchTraceViewVisibility:152`), đồng bộ URL
  (`syncUrl:773`, `syncSessionUrl:782`, `clearTraceUrl:791`), state
  global khả biến (`state:160-189` + `turnStateFor:190`,
  `revealMonitor:116`). Fix ở TASK-005 (3 file theo ranh giới trên,
  ngân sách mỗi file ≤ 400 dòng).
- SOLID-003 (SRP — `cost.css` selectors không scope, chạm W2):
  `.cost-overview`/`.cost-total`/`.cost-chart-card` (`cost.css:15-60`)
  và `.cost-chart-area`/`.cost-chart-line`/`.cost-chart-point`
  (`cost.css:74-89`) là unscoped nhưng markup dùng chung nằm ở section
  Request (`dashboard.html:57-68`) và `request.js:99` vẽ
  `.cost-chart-*`; comment `cost.css:12-15` tự thừa nhận share với
  Request. Sửa Cost có thể vỡ Request và ngược lại. Chỉ các rules
  `#chi-phi ...` (`cost.css:64`, `92-104`) là thực sự của W3. Fix ở
  TASK-008. Đối chứng: `trace.css` scope `#trace` toàn file
  (`trace.css:22-221`) — mẫu đúng để noi theo.
- SOLID-004 (SRP — `cost.js` 451 dòng trộn tải dữ liệu và render):
  `load:384` (3 fetch song song `getJson(/api/traces/stats)`,
  `fetchAllTraces(/api/traces)`, `getJson(/api/config)` + `generation`
  guard) lẫn với render (`renderOverview:136`, `renderModels:332`,
  `renderSessions:355`, `modelEntry:232`, `sessionEntry:282`,
  `pricingDetails:204`) và pager/search/sort state
  (`modelsPage:57`, `openPricing:62`). Fix ở TASK-006.
- SOLID-005 (DIP/ISP — `cost-data.js` phụ thuộc `trace-data.js` cho
  pricing): `cost-data.js:13` import `selectedModelConfig, tokenCost`
  từ `trace-data.js:384-397` trong khi `trace-data.js:1-4` comment thừa
  nhận duplicate `finiteCost` (`trace-data.js:6` vs `cost-data.js:36`)
  để "tránh import cycle". Pricing là mối quan tâm của Cost, không
  phải của Trace — Trace phục vụ 2 client không liên quan (Trace view
  cần grouping/steps/time; Cost cần pricing). Fix ở TASK-007 (chuyển
  2 helpers pricing về `cost-data.js`, mỗi file tự sở hữu `finiteCost`
  hoặc import từ `format.js` của W6 nếu W6 cung cấp).
- SOLID-006 (DIP — endpoint cứng trong view layer, bằng chứng cụ thể):
  `cost.js:load:384-422` hardcode 3 URL (`/api/traces/stats`,
  `/api/traces`, `/api/config`) qua `getJson` trực tiếp; `trace.js:806`
  và `trace.js:698` hardcode `/api/traces...` tương tự. Điểm sáng:
  `collectTracePages(fetchPage)` (`trace-data.js:270`) đã inject
  `fetchPage` nên test được không cần fetch/DOM — giữ nguyên pattern
  này, không trừu tượng hoá thêm (không thêm fetch wrapper/DI
  framework). Không phát hiện vi phạm OCP/ISP nào khác có bằng chứng
  cụ thể — không nhồi pattern.

## Risks

- RISK-001 (path wiring sau move): `dashboard.html` load 4 scripts
  (`dashboard.html:228-231`: `navigation.js`, `cost.js`, `request.js`,
  `usage.js`, `trace.js`, `dashboard.js`) và 6 CSS
  (`dashboard.html:10-15`); imports tương đối trong `cost.js:1-20`,
  `trace.js:1-23`, `cost-data.js:11-13`, `trace-data.js:1` đều phải đổi
  cùng lúc với move. Mitigate: TASK-002 liệt kê exhaustive từng dòng;
  verify serve 200 mọi asset trong TASK-003 (DevTools network không
  404).
- RISK-002 (tests pin path flat hiện tại): 8 files tests đọc file W3
  qua path cứng (liệt kê ở TASK-009). Sau move, các tests này đỏ vì
  không tìm thấy file — đó là path break, không phải regression logic.
  Mitigate: coder W3 không tự sửa tests; gửi TASK-009 cho orchestrator
  duyệt cập nhật cơ học trong GOAL-002/W6-test.
- RISK-003 (`trace.html` stub + deep-link): stub phải giữ byte-for-byte
  (`location.replace("./dashboard.html" + location.search + "#trace")`,
  noscript refresh, link fallback) — `test_webui_markdown.py:217-220`
  và `test_serve_memory_stats.py:319-323` assert từng chuỗi. Rủi ro
  phụ: `cost.js:296` link `./trace.html?session=` (tương đối từ
  `dashboard.html`) — sau move file `cost.js` vào subdir nhưng link
  chạy ở runtime từ URL của `dashboard.html` (flat) nên không đổi;
  tuyệt đối không sửa thành `./pages/dashboard/trace.html`. Mitigate:
  TASK-010 check redirect + deep-link trên serve thật.
- RISK-004 (selectors dùng chung cross-page): `.cost-overview`,
  `.cost-chart-*`, `.cost-model-heading` dùng bởi cả `#chi-phi`,
  `#request` (W2) và `#trace` (`dashboard.html:68,219,224`);
  `.journal-*` kit thuộc `screens.css` (W6). Rescope sai sẽ vỡ Request
  hoặc Trace. Mitigate: TASK-008 quyết chung với W2 + visual gate so
  base cả 4 views dashboard, không chỉ 2 views W3.
- RISK-005 (`serve/static.py` subdir serving): `safe_file`
  (`thyca/serve/static.py:25-37`) resolve `(webui / rel)` và chặn
  traversal — phục vụ subdir (`pages/dashboard/cost.js`) cùng cơ chế
  với flat hiện tại, nhưng phải verify thực tế vì lần đầu webui có
  subdir JS/CSS. Nếu 404, micro-fix thuộc orchestrator (ngoài scope
  W3). Mitigate: TASK-003 probe GET từng asset đã move qua server
  thật.
- RISK-006 (tranh file với W2/W6): `dashboard.html` (W2 sở hữu),
  `screens.css` + `backend/api.js`/`format.js` (W6 sở hữu),
  `backend/dashboard-today.js` + `backend/analytics-data.js` (W2 sở
  hữu, W3 chỉ import). W3 không tách/sửa các file này; mọi thay đổi
  cần thiết (pager shared, rescope CSS) đi qua owner + orchestrator.

## Assumptions

1. `.html` giữ flat (URL contract); chỉ JS/CSS move. `trace.html`
   stub giữ byte-for-byte; `dashboard.html` do W2 sở hữu — W3 chỉ đề
   xuất sửa path `<link>`/`<script>` (TASK-002), W2/orchestrator áp.
2. Layout đích mặc định theo plan tổng: `pages/dashboard/` chứa flat
   cả 4 views (cost, request, usage, trace) — W3 không tách sâu hơn
   (không `pages/dashboard/cost/`, `pages/dashboard/trace/`) vì Cost
   (~400 sau tách) và Trace (~300-350/file sau tách) không đủ lớn để
   biện minh thêm cấp thư mục.
3. Pager dùng chung (`shared/js/`) do W6 sở hữu và cung cấp; giả định
   nó tồn tại trước khi W3 làm TASK-004. Nếu chưa, W3 block TASK-004
   và báo orchestrator thay vì tự tạo pager shared thứ hai.
4. `backend/` sau refactor: `cost-data.js` + `trace-data.js` (+
   `trace-steps.js` mới) về `pages/dashboard/` cùng views dùng chúng;
   `api.js`/`format.js`/`markdown.js` về `shared/js/` do W6 quyết.
   Import paths cuối cùng theo commit layout GOAL-002; TASK-002 dùng
   paths đó, không tự phát minh.
5. Không thêm dependency, build step, framework, fetch wrapper hay DI
   mới — giữ MPA + ES modules như hiện tại; SOLID thực dụng, SRP là
   chính.
6. Ngưỡng tách: JS > 400 dòng, CSS theo quyết định team + lý do.
   `cost.js` 451 và `trace-data.js` 409 vượt nhẹ nên tách tối thiểu
   (TASK-006/007); `cost.css` 137 và `trace.css` 222 không tách file,
   chỉ rescope (TASK-008).
