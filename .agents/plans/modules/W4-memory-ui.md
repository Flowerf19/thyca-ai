---
status: done
created: 2026-09-22
last_updated: 2026-09-22
---

# W4 memory-ui — plan module (Memories + Hồ sơ)

## Summary

Module W4 gồm 2 trang: Nhật ký (`memories.html`/`.js`/`.css`, 104/377/235 dòng)
và Hồ sơ (`profile.html`/`.js`/`.css`, 69/216/43 dòng), cộng mapping dùng chung
`backend/memory-data.js` (72 dòng). Tổng ~1116 dòng, không file nào vượt ngưỡng
(JS >400, CSS theo team quyết — đều dưới ngưỡng), nên **không tách file**:
refactor chỉ là di chuyển vật lý + vệ sinh SRP nội file (section comments),
giữ nguyên hành vi, URL, visual.

W4 là consumer của W6 (`backend/markdown.js` — `formatMarkdown`, `backend/api.js`,
`backend/format.js`) và W2 (`backend/analytics-data.js`, `backend/bar-chart.js`).
`memory-data.js` chỉ được import bởi 2 file W4 (`memories.js:5`, `profile.js:4`) —
verified bằng grep, không consumer ngoài.

## Tasks

### GOAL-001: Di chuyển vật lý đúng target layout (input cho layout agent, GOAL-002 master)

`.html` giữ flat (URL contract). JS+CSS vào `pages/<name>/`; `memory-data.js`
(dùng chung nội bộ 2 trang W4) vào `shared/js/`.

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | `git mv thyca/webui/memories.js thyca/webui/pages/memories/memories.js` | | |
| TASK-002 | `git mv thyca/webui/memories.css thyca/webui/pages/memories/memories.css` | | |
| TASK-003 | `git mv thyca/webui/profile.js thyca/webui/pages/profile/profile.js` | | |
| TASK-004 | `git mv thyca/webui/profile.css thyca/webui/pages/profile/profile.css` | | |
| TASK-005 | `git mv thyca/webui/backend/memory-data.js thyca/webui/shared/js/memory-data.js` | | |
| TASK-006 | `memories.html`: sửa `./memories.css` → `./pages/memories/memories.css`, `./memories.js` → `./pages/memories/memories.js` (giữ nguyên `styles.css`/`screens.css`/`backend.css`/`navigation.js` theo layout agent quyết cho W6) | | |
| TASK-007 | `profile.html`: sửa `./profile.css` → `./pages/profile/profile.css`, `./profile.js` → `./pages/profile/profile.js` (tương tự TASK-006) | | |
| TASK-008 | Viết lại import tương đối trong file đã dời: `pages/memories/memories.js` 5 import (`./backend/X` → `../../shared/js/X` cho `api.js`, `analytics-data.js`, `bar-chart.js`, `format.js`, `memory-data.js`); `pages/profile/profile.js` 4 import (tương tự cho `api.js`, `format.js`, `markdown.js`, `memory-data.js`); `shared/js/memory-data.js` giữ `./format.js` (cùng thư mục). Mọi import giữ single-line để regex strip của test harness không vỡ (xem Rủi ro R7) | | |
| TASK-009 | Verify sau move: `uv run pytest tests/test_serve_memory_stats.py tests/test_webui_memory_edit.py tests/test_webui_markdown.py -q` xanh, `memories.html`/`profile.html` + assets mới serve 200, `git diff --check` sạch | | |

### GOAL-002: Vệ sinh SRP nội file trong layout mới (không tách, không đổi logic)

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-010 | `pages/memories/memories.js`: thêm section comments phân tách 5 cụm hiện có (search placement `placeSearch`, card render `memoryCard`, inline editor `editForm`/`startEdit`/`cancelEdit`, overview chart+stats, render/load/mutate/bind) — không dời, không đổi câu lệnh nào | | |
| TASK-011 | Giữ nguyên helper `messageOf` trùng lặp ở `memories.js:40` và `profile.js:30` (3 dòng; extract sang shared tốn coupling xuyên module với W6 — quyết định có ghi nhận, xem SOLID-2). Không tạo duplication mới | | |
| TASK-012 | Audit CSS scope: `memories.css` chỉ giữ selector prefix `memory-`/`memories-` + hook có chủ đích vào shared kit (`.sidebar .memory-search-cluster`, `.memories-shell .sidebar`, `.memory-search-bar`); `profile.css` chỉ prefix `profile-` + `#canonical-*`. Không thêm selector global, không copy rule từ `screens.css` | | |
| TASK-013 | Self-check diff: `git diff --check` sạch, diff chỉ gồm path + comments, không câu lệnh logic nào đổi (so từng hunk) | | |

### GOAL-003: Cổng verify W4 (test + visual + deep-link)

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-014 | Chạy focused gate: `tests/test_webui_memory_edit.py` (9 tests inline-edit concurrency), `tests/test_serve_memory_stats.py`, `tests/test_webui_markdown.py`, cộng webui subset master (`test_webui_format`, `test_webui_stream`, `test_ndjson`) và `test_serve_*` — tất cả xanh, baseline 719 passed/0 fail giữ nguyên | | |
| TASK-015 | Visual check `memories.html` + `profile.html` (desktop và width ≤56rem) so với base: sidebar nav 5 mục Nhật ký, search, overview chart + 3 stat, card nhật ký + editor inline, profile file switch + markdown body + dialog `#canonical-dialog` — không vỡ layout mới pass | | |
| TASK-016 | Deep-link check: mở `profile.html#USER.md` (và `#SOUL.md`, `#IDENTITY.md`) render đúng file; `hashchange` chuyển file; nút back `#profile-back` xóa hash. `memories.html` không có hash contract (view state in-memory) — confirm không regression | | |

## Test Plan

- Focused gate (TASK-014): `test_webui_memory_edit.py` khóa hành vi inline-edit
  concurrency (4 editor copies mobile, pending-disable, draft identity, focus);
  `test_serve_memory_stats.py` khóa serve + API `/api/memory/*` + cấu trúc 2 màn;
  `test_webui_markdown.py` khóa `selectMemories` ordering, `selectCanonical` gián
  tiếp qua profile assertions, và `formatMarkdown` mà profile consumer.
- Các test trên pin path cũ (`./thyca/webui/memories.js`,
  `WEBUI/"backend"/"memory-data.js"`, `'./memories.js' in html`,
  `'from "./backend/markdown.js"'`) — literal path được layout agent cập nhật cơ
  học trong GOAL-002 master (orchestrator duyệt), W4 refactor không sửa test để pass.
- Visual gate (TASK-015) + deep-link gate (TASK-016) là bằng chứng không breakage.
- Không thêm dependency, build step, hay test mới.

## SOLID findings (file:line)

- **SRP-1 — `memories.js` (377 dòng) trộn 5 cụm trong 1 file**: đặt search responsive
  (`placeSearch`, :16), render card (`memoryCard`, :58), editor inline (`editForm` :106,
  `firstVisible` :176, `startEdit` :180, `cancelEdit` :189), chart+stats overview
  (`drawOverviewChart` :205, `overviewChartCard` :225, `overviewCards` :238,
  `overviewStats` :259), render dispatch + data (`renderRows` :266, `render` :274,
  `loadStats` :323, `mutateMemory` :344, `bind` :365). 377 < ngưỡng JS 400 của master
  plan → **không tách**, chỉ section comments (TASK-010).
- **SRP-2 — trùng lặp `messageOf` y hệt nhau** ở `memories.js:40-42` và `profile.js:30-32`
  (cộng cặp `setStatus`/`setBusy` cùng shape ở `memories.js:44-56` / `profile.js:34-54`).
  Extract sang `shared/js/` thuộc sở hữu W6 → coupling xuyên team cho 3 dòng;
  **quyết định: giữ duplication**, ghi nhận tại TASK-011.
- **SRP-3 — `memory-data.js` (72 dòng) gộp 2 selector** `selectMemories` (leaf → card rows,
  sort theo view) và `selectCanonical` (files → USER/SOUL/IDENTITY order) phục vụ 2 trang.
  Tách đôi tăng import churn mà không giảm coupling (cả hai cùng import `format.js:1`).
  **Quyết định: giữ 1 file** tại `shared/js/memory-data.js` (W4-internal shared, không
  consumer ngoài — đã verify bằng grep).
- **SRP-4 — CSS scoped tốt, không cần tách**: `memories.css` (235 dòng) mọi rule mang
  prefix `memory-`/`memories-` trừ hook có chủ đích (`.sidebar .memory-search-cluster`,
  `.memories-shell .sidebar` :102, `.memory-search-bar`, `.memory-view`); không copy
  journal kit (kit nằm ở `screens.css`, test `test_cost_panel...` khóa
  `.memory-card` thuộc shared). `profile.css` (43 dòng) toàn prefix `profile-` +
  `#canonical-dialog`/`#canonical-content`. Không selector global, không duplicate —
  **không tách, không đổi**.
- **DIP — import trực tiếp module cụ thể** (`memories.js:1-5`, `profile.js:1-4`,
  `memory-data.js:1`): không có abstraction để đảo ngược; MPA + ES modules hiện tại
  không cần DI. **Không thay đổi.**
- **ISP/OCP — không phát hiện vi phạm cần sửa**: mỗi import chỉ dùng 1–3 named export;
  dispatch view bằng chuỗi `data-view` (`memories.js:274`, `memory-data.js` branch
  `used-more`/`searched-more`/`used-less`) thêm view = thêm branch, nhưng 5 views cố
  định, không dự báo mở rộng. **Không thay đổi.**

## Rủi ro

- **R1 — path updates**: 2 `<link>` + 2 `<script type="module">` trong 2 `.html`
  (dòng link 14–17 và script 102/67), 9 import tương đối trong 2 `.js` dời, 0 `url()`
  trong CSS W4 (đã grep — không có). Font Google ngoài giữ nguyên.
- **R2 — tests pin path cũ** (layout agent cập nhật cơ học, orchestrator duyệt):
  `test_webui_memory_edit.py:225` `readFileSync('./thyca/webui/memories.js')`;
  `test_serve_memory_stats.py:78-87` (`/memories.html`, `/memories.js`, `./memories.js`
  trong html), `:219-248` (`is_file` cho `memories.js`/`profile.js`, đọc
  `WEBUI/"memories.js"`, `WEBUI/"profile.js"`, `WEBUI/"backend"/"memory-data.js"`,
  assert `'./profile.js'`, `'./navigation.js'`, `'from "./backend/markdown.js"'`),
  `:305-312` map html→script; `test_webui_markdown.py:13` `MEMORY_DATA` path.
- **R3 — shared selectors xuyên trang** (W6 sở hữu, W4 chỉ consumer): `.session-item`/
  `.session-icon`/`.session-name`, kit `.screen-*`, `.memory-card` trong `screens.css`
  (gồm `:885 .memories-shell .sessions`); `dashboard.css:22` comment khóa typography
  "profile standard" (Source Serif 1.15rem) — W6 refactor tokens/kit phải giữ visual.
- **R4 — URL/deep-link**: `.html` giữ flat nên nav `navigation.js:18-19` không đổi;
  deep-link hash `#USER.md`/`#SOUL.md`/`#IDENTITY.md` (`profile.js:122,138,175,209-210`,
  qua `decodeHash` của W6) phải sống sót sau move (TASK-016). Memories không có hash
  contract.
- **R5 — `serve/static.py`**: `safe_file` resolve mọi path tương đối dưới webui root
  nên phục vụ subdirs (`pages/`, `shared/js/`) không cần sửa serve — chỉ verify 200
  (TASK-009). Mọi sửa serve là micro-fix do orchestrator duyệt riêng.
- **R6 — consumer markdown của W6**: `profile.js:3` dùng `formatMarkdown`; W6 phải giữ
  signature + luật safe-link/image (đã khóa bởi `test_webui_markdown.py`). W4 không
  đụng `markdown.js`.
- **R7 — harness regex**: `test_webui_memory_edit.py` strip import bằng
  `/^import[\s\S]*?from .*?;\n/gm` — import viết lại ở TASK-008 phải giữ single-line,
  cấm import nhiều dòng trong file W4.

## Success criteria (đo được)

1. 5 `git mv` đúng bảng GOAL-001; `memories.html`/`profile.html` vẫn ở root, serve 200.
2. Không file nào vượt budget dòng hiện tại (memories.js ≤377, profile.js ≤216,
   memories.css ≤235, profile.css ≤43, memory-data.js ≤72); diff logic rỗng (chỉ path + comments).
3. Test gate xanh: `test_webui_memory_edit.py` (9/9), `test_serve_memory_stats.py`,
   `test_webui_markdown.py`, webui subset + `test_serve_*`; full suite giữ baseline
   719 passed/0 fail.
4. Visual gate: screenshots Nhật ký + Hồ sơ (desktop + mobile ≤56rem) so base —
   không vỡ layout; deep-link `#USER.md`/`#SOUL.md`/`#IDENTITY.md` đúng file.

## Assumptions

1. `shared/js/` chứa `api.js`, `format.js`, `markdown.js` (W6) và `analytics-data.js`,
   `bar-chart.js` (W2) sau layout — import `../../shared/js/X` trong TASK-008 dựa trên
   giả định này; nếu layout duyệt vị trí khác, recompute tương đối, không đổi logic.
2. `navigation.js` (classic script, dùng chung mọi trang, không thuộc module table) do
   layout agent đặt vị trí; W4 chỉ cập nhật `<script src>` theo quyết định đó.
3. Literal path trong tests do layout agent (GOAL-002 master) cập nhật cơ học có duyệt;
   W4 coding bắt đầu từ commit layout xanh.
4. Không tách file W4 (lý do tại SOLID-1..4); mọi thay đổi ngoài 7 file module
   (`memories.html`/`.js`/`.css`, `profile.html`/`.js`/`.css`, `memory-data.js`) đều
   ngoài scope.

## Close-out (2026-09-22, orchestrator)
All module tasks landed and verified: branch refactor-webui-W4-memory-ui, test 719/719, review approve. Merged into refactor/webui-solid, full suite 719 passed, Chrome gate pass, plan status done.
