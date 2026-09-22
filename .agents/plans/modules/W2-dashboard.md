---
status: in-progress
created: 2026-09-22
last_updated: 2026-09-22
---

# W2 — Dashboard core (usage + request views)

## Summary

Module W2 gồm view chuyển trang (`dashboard.js`), hai view panel
(`usage.js`, `request.js` + `usage.css`), và phần khung dashboard trong
`dashboard.html`/`dashboard.css`. `dashboard.html` đồng thời tải 2 view ngoài
module (W3: `cost.js`, `trace.js`) — mọi thay đổi phải giữ wiring 4
`<script type="module">` còn hoạt động.

Không có file oversize trong module (JS lớn nhất `request.js` 213 dòng <
ngưỡng 400; CSS lớn nhất `usage.css` 222 dòng) nên **không tách file**.
Trọng tâm refactor là SRP: gom 2 implementation paging `/api/traces` về một,
xóa helpers trùng lặp giữa `usage.js`/`request.js`, dọn dead selectors trong
`usage.css`, và đưa helpers dùng chung chéo module (`analytics-data.js`,
`bar-chart.js`, `dashboard-today.js`) vào `shared/js/` do W6 sở hữu.

Phát hiện quan trọng cho orchestrator: 3 file master plan xếp vào W2 nhưng
đang được W1/W3/W4 import trực tiếp — `backend/analytics-data.js`
(W1 `trace.js`, W3 `cost.js`, W4 `memories.js`), `backend/bar-chart.js`
(W4 `memories.js`, W2 `usage.js`), `backend/dashboard-today.js`
(W3 `cost.js`). Nhét chúng vào `pages/dashboard/` sẽ tạo cross-page import
ngược; plan này đề xuất chúng về `shared/js/` (W6 sở hữu, W2 là consumer).

## Tasks

### GOAL-001: Physical layout (sau khi orchestrator duyệt, do layout agent GOAL-002 làm; W2 team verify)

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | Move W2-owned (giữ `dashboard.html` flat theo URL contract): `git mv thyca/webui/dashboard.js thyca/webui/pages/dashboard/dashboard.js`, `git mv thyca/webui/usage.js thyca/webui/pages/dashboard/usage.js`, `git mv thyca/webui/usage.css thyca/webui/pages/dashboard/usage.css`, `git mv thyca/webui/request.js thyca/webui/pages/dashboard/request.js`, `git mv thyca/webui/dashboard.css thyca/webui/pages/dashboard/dashboard.css` | | |
| TASK-002 | Chuyển helpers dùng chung chéo module vào `shared/js/` (W6 sở hữu, cần orchestrator duyệt vì chạm W1/W3/W4): `git mv thyca/webui/backend/analytics-data.js thyca/webui/shared/js/analytics-data.js`, `git mv thyca/webui/backend/bar-chart.js thyca/webui/shared/js/bar-chart.js`, `git mv thyca/webui/backend/dashboard-today.js thyca/webui/shared/js/fetch-traces.js` (đổi tên để hết nhãn "today" lỗi thời — view "Hôm nay" đã xóa; nếu orchestrator muốn giữ tên thì giữ `dashboard-today.js`) | | |
| TASK-003 | Cập nhật paths trong `dashboard.html` (5 `<script>`, 7 `<link>`): `./dashboard.js` → `./pages/dashboard/dashboard.js`, `./usage.js` → `./pages/dashboard/usage.js`, `./request.js` → `./pages/dashboard/request.js`, `./usage.css` → `./pages/dashboard/usage.css`, `./dashboard.css` → `./pages/dashboard/dashboard.css`; giữ nguyên `./cost.js`, `./trace.js`, `./navigation.js` (W1/W3 sở hữu) và `./styles.css`, `./screens.css`, `./cost.css`, `./trace.css`, `./backend.css` (W6/W3 sở hữu) cho tới khi các team đó move | | |
| TASK-004 | Cập nhật import trong W2 files: `pages/dashboard/usage.js` và `request.js` đổi `./backend/api.js` → `../../shared/js/api.js` (sau khi W6 move `api.js`; nếu W6 chưa move thì trỏ `../..` tương đối đúng tree tại thời điểm merge — layout agent quyết 1 lần), `./backend/analytics-data.js` → `../../shared/js/analytics-data.js`, `./backend/bar-chart.js` → `../../shared/js/bar-chart.js`; W3 `cost.js` đổi import `fetchAllTraces` sang path mới của TASK-002 (team W3 thực hiện, W2 verify) | | |
| TASK-005 | Verify sau move (chưa refactor logic): chạy test gate W2 (xem Test Plan), mở `dashboard.html` qua serve, bấm 4 view + đổi period/search/sort trên Usage và Request, `git diff --check` sạch | | |

### GOAL-002: SOLID refactor (SRP trước, scope chỉ trong W2 + import-shared)

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-006 | `usage.js:103-116` `loadAllTraces` (paging thô: không dedupe, không phát hiện page dở) → reuse `fetchAllTraces` từ `shared/js/fetch-traces.js` (`dashboard-today.js:29`, dedupe theo `(session_id, turn_index)` + `TracesIncompleteError`); `usage.js` truyền `fetchPage` closure gọi `getJson(traceRangeUrl("/api/traces", days, 200) + "&offset=…")`, bắt `TracesIncompleteError` → `setStatus(…, "error")` thay vì im lặng thiếu dữ liệu | | |
| TASK-007 | Xóa trùng `messageOf`/`setStatus`: `usage.js:24-31` và `request.js:20-28` giống hệt nhau → 1 helper dùng chung (đặt cạnh `getJson` trong `shared/js/api.js` do W6 sở hữu, hoặc file `shared/js/status.js` mới ~15 dòng nếu W6 từ chối đụng `api.js`); cả 2 view import về | | |
| TASK-008 | `request.js:42-52` `completeRequests` fill ngày khuyết trùng logic `analytics-data.js:97-109` `completeDays` ở dạng hẹp hơn → reuse `completeDays` (map `{day, requests}` sang shape `{day, input/cache/output/turns/requests}` hoặc mở rộng `completeDays` nhận factory default-row; không đổi output `drawChart`) | | |
| TASK-009 | `request.js:30-36` `svg()` trùng `bar-chart.js:18-27` `svg()` → export helper `svg` từ `shared/js/bar-chart.js` (hoặc `shared/js/svg.js` nếu W6 muốn), `request.js` và `bar-chart.js` cùng dùng | | |
| TASK-010 | Dọn dead/duplicated CSS: xóa `usage.css:1-3` và `usage.css:197-199` (`.usage-heading select` — `dashboard.html` không còn class `usage-heading`, header dùng `.screen-heading`); đối chiếu `screens.css:97` (`.usage-chart-top` layout) và `screens.css:592` (`.usage-heading label`, dead cùng lý do) với W6 — nếu W6 giữ kit trong `screens.css` thì W2 không đụng, chỉ xóa phía `usage.css` | | |
| TASK-011 | `dashboard.js` (72 dòng, 1 nhiệm vụ view-switcher) giữ nguyên logic; chỉ đổi path nếu GOAL-001 yêu cầu. Không tách `request.js` (213) / `usage.js` (149): dưới ngưỡng 400, mỗi file đã là 1 view panel gắn kết | | |
| TASK-012 | Full test gate W2 + check visual (xem Test Plan) rồi gửi review | | |

Ghi chú không tách file (oversize split order): không có. Thứ tự ưu tiên nếu
reviewer yêu cầu tách sau này: `request.js` (custom `drawChart` line-chart
`request.js:70-110` là ứng viên tách đầu tiên thành
`pages/dashboard/request-chart.js`) — hiện tại chưa cần vì 213 dòng còn đọc
được một mạch và chart gắn chặt render.

## Test Plan

Focused gate (chạy sau mỗi GOAL, phải xanh như baseline 719 passed):

- `tests/test_request_chart.py` — boot `request.js` trên fake DOM (đọc path
  `thyca/webui/request.js`; sau GOAL-001 cần orchestrator duyệt update path
  trong test — W2 team không tự sửa test để pass).
- `tests/test_dashboard_journal.py` — contract 4-view + `fetchAllTraces`
  (đọc `backend/dashboard-today.js`, `dashboard.js`, `dashboard.html`;
  assert chuỗi import `"./backend/dashboard-today.js"` trong `cost.js:2` —
  sẽ phải update theo TASK-002/TASK-004, do orchestrator duyệt).
- `tests/test_cost_journal.py` — đọc `backend/cost-data.js`,
  `backend/dashboard-today.js`, `cost.js` (W3 consumer của `fetchAllTraces`;
  W2 verify không vỡ).
- `tests/test_webui_markdown.py` — static checks `dashboard.html`,
  `dashboard.js`, `usage.js`, `request.js`, `dashboard.css`, `trace.html`
  redirect (giữ `./dashboard.html#trace`).
- `tests/test_serve_memory_stats.py` (`test_default_webui_has_index` liệt kê
  `usage.js`), `tests/test_chat_meter.py` (liệt kê `usage.js`,
  `analytics-data.js`) — path-pinning, cùng lưu ý như trên.
- `tests/test_serve_*.py` còn lại — serve phục vụ subdirs sau move.

Visual check (chứng minh không breakage, chạy trên serve loopback
`127.0.0.1:8765`): mở `dashboard.html`, (1) default view là Sử dụng token,
hash `#su-dung`; (2) bấm 4 nút sidebar — mỗi view hiện đúng 1 block, 3 block
kia `hidden`, `aria-pressed` đồng bộ; (3) Usage: đổi period 30/7/1 ngày chart
vẽ lại, toggle Token/Lượt đổi legend; (4) Request: đổi period/sort/search —
bar cùng baseline, search khớp tên; (5) `dashboard.html?session=<id>#trace`
mở thẳng view Trace (W3 code, W2 chỉ verify wiring còn nguyên);
(6) mobile width (~360px) sidebar thành pill row, không mất nút.

## Assumptions

1. `.html` giữ flat (URL contract); `trace.html` redirect stub ngoài scope W2
   (W3 sở hữu) — W2 chỉ verify deep-link `?session=&turn=#trace` còn đi qua
   `dashboard.js` đúng view Trace.
2. Layout vật lý do layout agent (GOAL-002 plan tổng) thực hiện 1 lần cho cả
   6 modules; các test path-pinning (`test_request_chart.py:21`,
   `test_dashboard_journal.py:25-28,354-355`, `test_webui_markdown.py`,
   `test_serve_memory_stats.py:226`, `test_chat_meter.py:51-54`) được update
   path trong bước layout có orchestrator duyệt — W2 coding không sửa test.
3. `analytics-data.js` / `bar-chart.js` / `dashboard-today.js` về `shared/js/`
   cần W6 đồng sở hữu và W1/W3/W4 update import (`trace.js:2`,
   `cost.js:2-3,19`, `memories.js:2-3`); nếu orchestrator bác, fallback là
   `pages/dashboard/` + import chéo có document.
4. `api.js` / `format.js` / `screens.css` / `styles.css` thuộc W6 — W2 chỉ đổi
   import path trỏ tới, không refactor nội dung.
5. Không đổi visual, không thêm dependency/build step, không behavior change
   ngoài việc Usage báo lỗi rõ khi trace page dở (TASK-006: trước đây im lặng
   vẽ thiếu — đây là fix incomplete-state, status text là thay đổi duy nhất).
6. Đổi tên `dashboard-today.js` → `fetch-traces.js` là đề xuất (tên cũ gắn
   view "Hôm nay" đã xóa từ GOAL-010 TASK-029); giữ tên cũ nếu orchestrator
   muốn diff nhỏ nhất.

## Risks

- **Path updates lan rộng**: `dashboard.html` có 5 `<script>` + 7 `<link>`;
  `usage.js:1-4` / `request.js:1-3` import `./backend/*`; `cost.js:2`
  import `fetchAllTraces`; `trace.js:2`, `memories.js:2-3` import
  `analytics-data.js`/`bar-chart.js`. Sót 1 path là trắng chart — bắt bằng
  test gate + visual check mục (3)(4).
- **Cross-page shared selectors**: `.screen-*`, `.journal-*`, `.usage-chart-*`,
  `.cost-chart-*` sống trong `screens.css` (W6); `usage.css` selectors dạng
  global (`.usage-*`) nhưng file page-scoped theo quy ước link-per-page —
  sau move sang `pages/dashboard/` vẫn load global qua `<link>`, không có
  scoping thực sự; không đổi thứ tự `<link>` trong `dashboard.html` để giữ
  cascade.
- **URL/deep-link breaks**: `dashboard.js:17-22,54-62` quản hash
  `#su-dung/#request/#chi-phi/#trace` + strip `?session=&turn=` khi rời
  Trace; `trace.html` redirect giữ params. Không đụng logic này (TASK-011).
- **`serve/static.py` subdir serving**: `safe_file` resolve `webui/<rel>`
  nên `pages/dashboard/*.js` phục vụ được về lý thuyết — verify bằng
  `test_serve_*` + mở page thật qua serve, không assume.
- **`url()` trong CSS**: `usage.css`/`dashboard.css` hiện không có `url()`
  (đã grep) — move CSS không kéo assets; nếu W6 move `images/`/`vendor/` sau
  này thì check lại.
