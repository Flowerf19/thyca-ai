---
status: done
created: 2026-09-22
last_updated: 2026-09-22
---

# W5 config-ui — Provider + Settings (module plan)

## Summary

Module W5 gồm 2 trang cùng họ "Cài đặt": `provider.html` + `provider.js` (656
dòng, oversize) và `settings.html` + `settings.js` (69 dòng, classic defer
IIFE) + `settings.css` (43 dòng, **đang xài chung bởi cả 2 trang**).
Không đổi giao diện, hành vi, URL. `.html` giữ flat (URL contract).
`provider.js` tách thành 5 module nhỏ theo SRP, mỗi file dưới ngưỡng 400 dòng.
`settings.css` **chuyển nguyên vẹn vào `shared/`** (quyết định có evidence ở
GOAL-003, không split — file 43 dòng mà tách sẽ đẻ 2–3 mảnh <20 dòng và phải
duplicate hệ thống `.settings-row`/`.settings-shell` dùng chung).
`settings.js` giữ nguyên classic script (không convert sang module: zero lợi
ích, đổi semantics load).

Tổng module ~1.100 dòng (provider.js 656 + settings.js 69 + settings.css 43 +
2 html 166). Không thêm dependency, không build step, không sửa test để pass.

### GOAL-001: Layout move + wiring khớp (sau khi orchestrator duyệt plan)

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | `git mv thyca/webui/provider.js thyca/webui/pages/provider/provider.js` (nếu layout agent GOAL-002 chưa làm; nếu đã làm thì verify path tồn tại) | | |
| TASK-002 | `git mv thyca/webui/settings.js thyca/webui/pages/settings/settings.js` (tương tự TASK-001) | | |
| TASK-003 | `git mv thyca/webui/settings.css thyca/webui/shared/css/settings.css` — giữ nguyên 43 dòng, không split (lý do ở GOAL-003) | | |
| TASK-004 | `provider.html` giữ flat, chỉ sửa 2 dòng wiring: line 14 `href="./settings.css"` → `href="./shared/css/settings.css"`, line 97 `<script type="module" src="./provider.js">` → `src="./pages/provider/provider.js"`; giữ nguyên thứ tự `navigation.js` (defer) trước module script | | |
| TASK-005 | `settings.html` giữ flat, chỉ sửa 2 dòng wiring: line 14 `href="./settings.css"` → `href="./shared/css/settings.css"`, line 65 `<script src="./settings.js" defer>` → `src="./pages/settings/settings.js"` (giữ `defer`, giữ classic — không thêm `type="module"`) | | |
| TASK-006 | Trong `pages/provider/provider.js` (và các module tách ra ở GOAL-002): rewrite 2 import `from "./backend/api.js"` (line 1), `from "./backend/reasoning-effort.js"` (line 2) sang vị trí shared cuối do W6 chốt (mặc định `../../shared/js/<name>.js`); nếu W6 chưa chốt thì dùng `../../backend/<name>.js` tạm và ghi rõ trong commit message để orchestrator reconcile | | |

### GOAL-002: Tách provider.js 656 dòng → 5 module (thứ tự từ leaf lên)

Thứ tự tạo file (mỗi TASK xong là một trạng thái load được trang, verify bằng
cách mở `provider.html` console sạch + pytest gate ở GOAL-004):

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-007 | Tạo `pages/provider/provider-state.js` (budget ≤120 dòng): dời `PRESETS` (provider.js:4-9), `PROVIDER_ID_RE` (:11), `STANDARD_EFFORTS` (:54), `state` (:56-65), `clone` (:67-69), `messageOf` (:71-73), `presetFor` (:97-100), `providerIds` (:109-111), `providerOf` (:113-115), `modelsOf` (:117-121), `modelSpec` (:123-125) — pure, không DOM, không fetch | | |
| TASK-008 | Tạo `pages/provider/provider-dom.js` (budget ≤260 dòng): dời `el` map (:13-51), `setStatus` (:75-78), `allControls` (:80-90), `setBusy` (:92-95), `syncProviderUi` (:102-107), `fillEffortOptions` (:127-134), `fillProviderEffort` (:136-138), `syncProviderApi` (:140-142), `fillDatalist` (:144-156), `fillSelect` (:158-166), `renderProviders` (:168-187), `renderModels` (:189-199), `applyModel` (:201-221), `renderLimits` (:225-230), `renderAll` (:232-238), `showReadyPopup` + `readyDialog` (:338-363); import state từ TASK-007 | | |
| TASK-009 | Tạo `pages/provider/provider-validate.js` (budget ≤130 dòng): dời `positiveNumber` (:240-246), `endpointValue` (:248-260), `askProviderId` (:262-270), `applyFormToState` (:272-335); import `{ el }` từ provider-dom, state/selectors từ provider-state; giữ nguyên mọi message lỗi tiếng Việt và mọi ngưỡng (loop 1–200, hotTail 1–64KB, context 1000–2.000.000) | | |
| TASK-010 | Tạo `pages/provider/provider-actions.js` (budget ≤250 dòng): dời `addProvider` (:365-381), `renameProvider` (:383-407), `deleteProvider` (:409-437), `addModel` (:439-459), `deleteModel` (:461-476), `setDefaultModel` (:478-491), `verifyProvider` (:493-520), `testProvider` (:522-538), `saveProvider` (:540-581); giữ nguyên endpoints `/api/onboarding/verify`, `/api/providers/test`, `/api/config` (GET+POST), giữ nguyên flow "lưu xong test luôn model mặc định + popup khi OK" | | |
| TASK-011 | Rút `pages/provider/provider.js` còn entry (budget ≤100 dòng): chỉ giữ imports, `bind` (:583-627), `boot` (:629-655), `void boot()` (:656); chiều phụ thuộc một chiều `entry → actions → validate → dom → state`, `actions → shared api`, không cycle | | |
| TASK-012 | `pages/settings/settings.js` giữ nguyên 69 dòng, không tách, không convert module (file < ngưỡng 400; IIFE đã cô lập scope; không có import nên move là đủ) | | |

### GOAL-003: Chốt settings.css sharing (move-to-shared, không split)

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-013 | Move nguyên vẹn `settings.css` → `shared/css/settings.css` theo TASK-003 (không sửa một selector nào trong bước này) | | |
| TASK-014 | Verify bằng evidence đã đo: 8/15 rule blocks cả 2 trang dùng chung (`.settings-row` + 2 rule con settings.css:1-11, `.settings-shell .screen-card` ×3 settings.css:27-35, 2 media queries settings.css:37-43 — provider.html:18,51-58,64-74,80-82,90 và settings.html:18,51-56,59 đều match); provider-only 4 blocks (`.settings-field` ×2, `.settings-unit`, `.model-note` — chỉ provider.html:51,64,70-74,81-82); settings-only 3 blocks (`.settings-check` ×2, `#giao-dien` — chỉ settings.html:48,54-56); file 43 dòng tách ra chỉ đẻ mảnh <20 dòng + duplicate hệ row/shell — nên keep-one-shared-file thắng split | | |
| TASK-015 | Ghi nhận classic-vs-module: `settings.js` classic defer IIFE (settings.html:65) chạy theo document order sau `navigation.js` defer; `provider.js` module (provider.html:97) cũng deferred; sau move giữ nguyên thứ tự 2 thẻ `<script>` trong mỗi html — không đổi semantics load, 2 trang không share JS nên không cần bridge giữa classic và module | | |

### GOAL-004: Gate kiểm chứng

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-016 | Chạy focused gate: `uv run pytest -q tests/test_serve_config.py tests/test_serve_memory_stats.py tests/test_serve_chat.py` — W5 KHÔNG sửa test; 3 tests pin flat path hiện tại (`test_serve_chat.py:735-739` đọc `WEBUI/"provider.js"`; `test_serve_memory_stats.py:226` assert `WEBUI/"provider.js"` is_file; `:310-315` assert `'./provider.js' in provider.html`) sẽ đỏ sau move cho tới khi orchestrator duyệt contract update ở GOAL-002 tổng — ghi kết quả thực tế, không tự sửa số | | |
| TASK-017 | Chạy full `uv run pytest -q` (baseline 719 passed / 0 fail 2026-09-22) + `git diff --check` sạch trên nhánh team | | |
| TASK-018 | Visual check: mở `provider.html`, `settings.html`, `provider.html?required=1` (entry onboarding từ `app.js:888`), `settings.html#chung`, `settings.html#giao-dien` — console không error, sidebar chéo 2 trang còn đúng (provider.html:34-36, settings.html:34-36), desktop + 1 mobile width không vỡ layout so với base; toggle Chủ đề/Cỡ chữ trên settings.html persist qua reload (localStorage `thyca.ui.preferences`) | | |

## Test Plan

- Focused (W5-owned behavior, backend còn nguyên): `tests/test_serve_config.py`
  (schema + settings endpoints mà provider.js gọi), `tests/test_serve_chat.py`
  (chat wiring + onboarding redirect `app.js:888`), `tests/test_serve_memory_stats.py`
  (webui file presence + html parse).
- WebUI subset theo plan tổng: `test_webui_concurrent_streams`,
  `test_webui_format`, `test_webui_live_recovery`, `test_webui_live_rounds`,
  `test_webui_markdown`, `test_webui_memory_edit`, `test_webui_stream`,
  `test_ndjson` + `test_serve_*`.
- Full `uv run pytest -q` phải xanh như baseline 719 passed / 0 fail (ngoại trừ
  các tests pin flat path đã liệt ở TASK-016 — thuộc contract update do
  orchestrator duyệt ở GOAL-002 tổng, W5 không chạm).
- Visual gate: 2 trang + `?required=1` + 2 anchors, desktop + 1 mobile, so với
  base — vỡ layout là fail; pixel-perfect không bắt buộc.
- Serve subdir: `thyca/serve/static.py:25-38` (`safe_file` resolve mọi subpath
  dưới webui root, không chặn nested) + mime `.js → text/javascript`,
  `.css → text/css` (`static.py:9-14`) nên `pages/` + `shared/` phục vụ được mà
  không cần sửa serve; verify bằng URL probe 200 cho 7 file đã move.

## Assumptions

1. `.html` không dời (URL contract): `provider.html`, `settings.html`,
   `settings.html#chung/#giao-dien`, `provider.html?required=1` giữ nguyên;
   chỉ JS/CSS move. `navigation.js:45,110-134` key theo filename nên không ảnh
   hưởng.
2. `settings.css` trong `settings.css` không có `url()` (đã đọc toàn file 43
   dòng — zero reference assets) nên move sang `shared/css/` không cần rewrite
   asset path.
3. `settings.js` không có import/export (IIFE thuần, đã đọc toàn file) nên
   move-only; giữ classic để không đổi load semantics.
4. Tên/dir `shared/css/` và `pages/provider/`, `pages/settings/` là đề xuất;
   W6 sở hữu `shared/`, orchestrator chốt cuối — W5 follow tên cuối, chỉ đổi
   prefix href/src, không đổi logic.
5. 3 tests pin flat path (liệt ở TASK-016) thuộc contract update ở GOAL-002
   tổng do orchestrator duyệt; W5 tuyệt đối không sửa test để pass.
6. SOLID thực dụng: chỉ tách SRP theo file (ngưỡng JS >400 dòng — provider.js
   656 vượt, settings.js 69 không); không thêm abstraction/pattern.
7. Không менять `thyca/serve/` (ngoài scope); nếu probe subdir fail thì báo
   orchestrator dưới dạng micro-fix riêng.

## SOLID findings (evidence file:line)

- **SRP-1 — provider.js 656 dòng gộp 5 concerns:** state/selectors thuần
  (:4-11,:54-73,:97-125), DOM render (:13-51,:75-95,:102-107,:127-238),
  validation (:240-270), form→state mutation `applyFormToState` (:272-335,
  64 dòng vừa validate vừa merge pricing), CRUD (:365-491), network I/O
  (:493-581), wiring (:583-656). Mỗi concern có đúng một lý do đổi khác nhau
  (schema backend đổi vs layout đổi vs rule validate đổi) → tách theo
  GOAL-002. `settings.js` 69 dòng một concern (localStorage prefs) → không tách.
- **SRP-2 — settings.css unscoped sharing:** 1 file phục vụ 2 trang, trong đó
  `.model-note` (settings.css:18-25) chỉ provider.html:51,64 dùng,
  `.settings-check` (:15-16) và `#giao-dien` (:36) chỉ settings.html:48,54-56
  dùng; sửa style provider có thể bleed sang settings và ngược lại. File chỉ
  43 dòng và 8/15 blocks dùng chung cả 2 trang (TASK-014) → resolve bằng
  move-to-shared nguyên vẹn, không split (split = duplicate shared system,
  vi phạm DRY để đổi lấy SRP hình thức).
- **SRP-3 — `showReadyPopup` (:340-363) build dialog bằng innerHTML trong file
  logic config:** sau tách nó nằm trong provider-dom.js (đúng nhà DOM), không
  tách file riêng (28 dòng — tách nữa là over-fragmentation).
- **DIP (observation, NO change):** provider.js:1-2 import trực tiếp concrete
  `./backend/api.js`, `./backend/reasoning-effort.js` (profile.js cũng cùng
  pattern `from "./backend/markdown.js"`). Không giới thiệu abstraction/interface
  — trái rule "no speculative abstractions" của plan tổng và phá consistency
  repo. Ghi nhận để reviewer biết là quyết định có ý thức.
- **ISP (observation, NO change):** `el` map 30 field (:13-51) + `allControls`
  (:80-90) disable toàn bộ ~30 controls trong mọi `setBusy` (kể cả nút Reset
  khi đang verify). Coarse nhưng là hành vi hiện tại (chống double-submit toàn
  form) → giữ nguyên để không đổi behavior; tách busy-group là follow-up ngoài
  scope W5.
- **OCP (positive, NO change):** `PRESETS` map (:4-9) + `presetFor` (:97-100)
  mở cho preset mới mà không sửa logic — giữ nguyên hình này khi dời sang
  provider-state.js.

## Risks

1. **Path rewrite sai:** 2 href/src mỗi html + 2 import trong provider.js +
   prefix `shared/css` phụ thuộc W6 chốt tên. Mitigate: TASK-004/005/006 liệt
   kê từng dòng; verify bằng URL probe 200 + console sạch (TASK-018).
2. **Cross-page shared selectors:** `.settings-row` grid, `.settings-shell`
   card, 2 media queries ảnh hưởng cả 2 trang cùng lúc. Mitigate: TASK-013 cấm
   sửa selector trong bước move; visual check cả 2 trang desktop + mobile
   (TASK-018).
3. **Tests pin flat path đỏ sau move:** `test_serve_chat.py:735-739`,
   `test_serve_memory_stats.py:226,310-315` assert vị trí/nội dung file cũ.
   Mitigate: W5 không sửa test; liệt kê trước ở TASK-016 để orchestrator duyệt
   contract update ở GOAL-002 tổng.
4. **URL/deep-link breaks:** `app.js:888` redirect `./provider.html?required=1`
   (onboarding entry), anchors `settings.html#chung/#giao-dien`, sidebar chéo
   (provider.html:34-36 ↔ settings.html:34-36), `navigation.js:45` coi
   provider.html thuộc section settings. Mitigate: html giữ flat + giữ
   filename → tất cả còn nguyên; TASK-018 probe từng URL/anchor.
5. **serve/static.py subdir serving:** `safe_file` (static.py:25-38) không chặn
   nested path và `_TYPES` có đủ `.js/.css` → rủi ro thấp; Mitigate: probe 200
   cho 7 file sau move (Test Plan); nếu fail thì micro-fix do orchestrator
   duyệt riêng, không gộp vào W5.
6. **Classic/module timing:** settings.js classic defer vs provider.js module
   deferred — đổi `src` mà giữ nguyên vị trí/thứ tự thẻ + thuộc tính
   (`defer`, `type="module"`) thì execution order không đổi (TASK-004/005);
   verify console sạch cả 2 trang.
7. **Import vào shared api của W6:** provider-actions.js phụ thuộc vị trí cuối
   của `backend/api.js` + `backend/reasoning-effort.js`. Mitigate: TASK-006
   default `../../backend/` tạm + commit message ghi rõ để orchestrator
   reconcile khi W6 chốt.

## Success criteria (đo được)

1. Không file JS nào của W5 vượt 400 dòng: provider-state ≤120, provider-dom
   ≤260, provider-validate ≤130, provider-actions ≤250, entry ≤100,
   settings.js = 69 không đổi (đếm bằng `wc -l`).
2. `settings.css` tồn tại đúng 1 bản ở `shared/css/settings.css`, byte-identical
   với bản cũ (`git diff` move-only, zero sửa selector).
3. `provider.html`, `settings.html` vẫn flat ở `thyca/webui/`, filename giữ
   nguyên; diff mỗi html đúng 2 dòng href/src.
4. Full `uv run pytest -q` xanh như baseline 719 passed / 0 fail (trừ các tests
   pin flat path đã khai báo ở TASK-016 chờ orchestrator duyệt contract).
5. Visual gate pass: provider + settings + `?required=1` + 2 anchors, desktop +
   1 mobile, console zero error, theme toggle persist qua reload.
6. Zero test edits, zero new dependencies, zero thay đổi `thyca/serve/`,
   `git diff --check` sạch.

## Close-out (2026-09-22, orchestrator)
All module tasks landed and verified: branch refactor-webui-W5-config-ui (split + 1-line test fix), test 719/719, review approve. Merged into refactor/webui-solid, full suite 719 passed, Chrome gate pass, plan status done.
