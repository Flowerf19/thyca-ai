---
status: in-progress
created: 2026-09-22
last_updated: 2026-09-22
---

# WebUI SOLID refactor — plan tổng (orchestrator)

## Summary

Refactor + tối ưu `thyca/webui/` (MPA ~11k dòng, 6 trang): code sạch, SOLID,
không đổi giao diện/hành vi/URL. Tổ chức page-first: `.html` giữ flat ở root
(URL contract), JS+CSS gom theo trang vào `pages/<name>/`, đồ dùng chung vào
`shared/`.

- Nhánh: `refactor/webui-solid` (từ `refactor/backend-solid` tip; rebase theo main sau khi backend merge).
- Orchestrator: main session (`meta/muse-spark-1.3`). Teams: planning/coding/test
  `meta/muse-spark-1.3-contributor` effort `xhigh`; review `meta/muse-spark-1.3` effort `max`.
- Prompts cho agents viết TIẾNG ANH. Coder KHÔNG commit; reviewer commit 1 commit sạch khi pass.
- Mỗi module 1 team, 4 chặng có gate: **planning → coding → test → review**.
- Coding chạy `isolation: worktree`, mỗi team 1 nhánh từ `refactor/webui-solid`
  (lưu ý: git cấm nhánh con dưới nhánh đã tồn tại — đặt tên phẳng kiểu
  `refactor-webui-W<n>-<name>`); orchestrator merge sau khi team pass review.

### Module-table (6 modules, webui-only)

| # | Module | Nguồn hiện tại | Dòng ~ | Team |
|---|--------|----------------|--------|------|
| W1 | shell/chat | `index.html`, `app.js` 916, `navigation.js`, `backend/chat-view.js` 442, `chat-status.js`, `chat-thinking.js`, `reasoning-effort.js` | ~1.800 | Team-chat |
| W2 | dashboard core | `dashboard.html`, `dashboard.js`, `usage.js`/`.css`, `request.js`, `backend/dashboard-today.js`, `bar-chart.js`, `analytics-data.js` | ~1.200 | Team-dashboard |
| W3 | cost+trace views | `cost.js`/`.css`, `backend/cost-data.js`, `trace.js` 1016/`.css`, `backend/trace-data.js` 409, `trace.html` stub | ~2.600 | Team-views |
| W4 | memories+profile | `memories.html`/`.js`/`.css`, `profile.html`/`.js`/`.css`, `backend/memory-data.js` | ~1.200 | Team-memory-ui |
| W5 | provider+settings | `provider.html`/`.js` 656, `settings.html`/`.js`/`.css` (settings.css đang xài chung 2 trang) | ~1.100 | Team-config-ui |
| W6 | shared | `styles.css` 1530, `screens.css` 1029, `backend.css`, `backend/api.js`, `format.js`, `markdown.js`, `vendor/`, `images/` | ~3.000 | Team-shared |

File oversize phải tách: `trace.js` 1016, `app.js` 916, `provider.js` 656 (ngưỡng JS: team quyết,
mặc định >400 dòng), `styles.css` 1530, `screens.css` 1029 (ngưỡng CSS: team quyết + lý do).

Target layout (đề xuất, teams được chỉnh với lý do, orchestrator duyệt):
`webui/*.html` giữ flat; `webui/pages/{chat,dashboard,memories,profile,provider,settings}/`
chứa JS+CSS của trang (dashboard views cost/request/usage/trace nằm flat trong
`pages/dashboard/` trừ khi team chứng minh cần tách sâu hơn); `webui/shared/`
cho đồ dùng chung (đề xuất `shared/css/`, `shared/js/`); `vendor/` + `images/`
giữ nguyên vị trí trừ khi team chứng minh lợi ích di chuyển.

### GOAL-001: Planning per-module (6 plans)

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | 6 planning agents đọc sâu module được giao (html/js/css + tests + consumers + `<link>`/`<script>` wiring), viết `.agents/plans/modules/W<n>-<name>.md` theo format skill implementation-planner, ghi rõ target layout + thứ tự tách file + rủi ro | x (6/6 plans, wf_b708f8363bfe) | 2026-09-22 |
| TASK-002 | Orchestrator duyệt 6 module plans (layout nhất quán, không tranh file, URL không đổi) rồi mới mở GOAL-002 | x (orchestrator approved 6/6 + 13 layout decisions §dưới; chờ user duyệt mới chạy layout agent) | 2026-09-22 |

### GOAL-002: Physical layout (1 agent, mechanical)

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-003 | Layout agent move JS/CSS vào `pages/` + `shared/` theo layout đã duyệt, sửa `<link>`/`<script>`/`import` paths (kể cả `url()` trong CSS nếu dời assets), không refactor logic trong bước này | x (34 mv + pager.js + test literals, commit 9c88492) | 2026-09-22 |
| TASK-004 | Verify sau move: pytest webui-subset + full xanh như baseline, mọi URL `.html` giữ nguyên (check serve 200), `serve/static.py` phục vụ subdirs đúng, `git diff --check` sạch → 1 commit `chore(layout): ...` | x (719 parity, 6/6 html 200 + assets probe, old paths 404, diff clean — orchestrator re-verified tree) | 2026-09-22 |

### GOAL-003: SOLID refactor per-module (6 teams song song)

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-005 | W1 chat: refactor theo module plan → test → review độc lập | | |
| TASK-006 | W2 dashboard: refactor theo module plan → test → review độc lập | | |
| TASK-007 | W3 views: refactor theo module plan → test → review độc lập (giữ `trace.html` redirect stub + deep-link params) | | |
| TASK-008 | W4 memory-ui: refactor theo module plan → test → review độc lập | | |
| TASK-009 | W5 config-ui: refactor theo module plan → test → review độc lập (giải quyết settings.css xài chung) | | |
| TASK-010 | W6 shared: refactor theo module plan → test → review độc lập (tokens/kit tách nhưng giữ nguyên visual) | | |
| TASK-011 | Orchestrator merge 6 nhánh team vào `refactor/webui-solid`, giải quyết conflict (ưu tiên giữ behavior + tests xanh) | | |

### GOAL-004: Integration + visual + docs

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-012 | Full `uv run pytest -q` + `git diff --check` + URL probes trên nhánh tích hợp | | |
| TASK-013 | Chrome screenshot gate: chụp 6 trang (desktop + 1 mobile width) so với base — không vỡ layout mới pass | | |
| TASK-014 | Review tổng độc lập 1 lượt toàn diff (scope, SOLID, không behavior change lén) | | |
| TASK-015 | Cập nhật docs (AGENT_RULES, PROJECT_CONTEXT, README, CHANGELOG) theo tree mới | | |

### Layout decisions (orchestrator, 2026-09-22 — sau review 6 module plans)

1. **navigation.js → W6 scope** (theo đề xuất W1): move `shared/js/navigation.js`, update 6 html; move-only, không tách (156 dòng, dưới ngưỡng).
2. **Layout agent tạo `shared/js/pager.js`** (copy cơ học block journal-pager giống hệt nhau) trong GOAL-002; W3 xóa 2 bản dup + import shared (tránh W3 block chờ W6).
3. **analytics-data.js, bar-chart.js, dashboard-today.js → `shared/js/`** (theo W2, W4 đồng thuận); GIỮ tên `dashboard-today.js` (bác rename → ít churn test).
4. **memory-data.js → `shared/js/`** (W4 sở hữu nội dung, chỉ 2 consumers W4 — không ai khác đụng).
5. **reasoning-effort.js → `shared/js/`** (W1 move, W5 follow import).
6. **Chart kit**: `usage-*` → W2 (`usage.css`), `cost-*` → W3 (`cost.css`); journal kit ở lại shared (W6).
7. **settings.css → `shared/css/settings.css` nguyên vẹn** (duyệt evidence W5, không split).
8. **api.js split**: W6 tạo `http.js` + `streams.js`, GIỮ `api.js` barrel vĩnh viễn (idiomatic ESM, zero flag-day); mọi team import trực tiếp `http.js`/`streams.js` theo export contract W6 đã chốt (names cố định trong W6 plan).
9. **W2 messageOf/setStatus → BẮT BUỘC file mới `shared/js/status.js`** (cấm đụng `api.js` đang split — tránh conflict merge với W6).
10. **W2 TASK-006** (Usage báo lỗi khi trace page dở thay vì im lặng) duyệt như bugfix; reviewer phải verify tests + visual.
11. **W3 pricing move**: giữ re-export `selectedModelConfig`/`tokenCost` trong `trace-data.js` (zero test churn), trừ khi team chứng minh cần update test.
12. **Test path-literals**: layout agent cập nhật cơ học theo lists trong 6 module plans (approved contract change, tương tự backend GOAL-002).
13. **W3 TASK-008 default duyệt** (scope cost.css tại chỗ, không tách file); W2 verify visual view Request.

### GOAL-005: Redo round (test-harness rule mới)

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-016 | W1 redo: rebase + splits + harness concat updates → test → review | | |
| TASK-017 | W3 redo: rebase + pager/trace/cost splits + extractor updates → test → review | | |
| TASK-018 | W5 fix: giữ split, update 1 substring assert → test → review | | |
| TASK-019 | W6 redo: full plan (plans đã commit) + CSS-pin updates → test → review | | |

## Test Plan

- Baseline: full pytest **719 passed / 0 fail** (backend branch tip). Mọi fail mới là regression.
- WebUI subset (mỗi team chạy focused theo module + subset này):
  `test_webui_concurrent_streams`, `test_webui_format`, `test_webui_live_recovery`,
  `test_webui_live_rounds`, `test_webui_markdown`, `test_webui_memory_edit`,
  `test_webui_stream`, `test_ndjson` + `test_serve_*` (serve phục vụ assets).
- URL stability: mọi `.html` ở đúng path cũ, serve 200; `trace.html` redirect + deep-link
  `?session=&turn=` giữ nguyên; legacy hash `#trace` giữ nguyên.
- Chrome gate (TASK-013): orchestrator chụp base vs head, so bằng mắt 6 trang —
  pixel-perfect không bắt buộc (font/rendering), vỡ layout là fail.
- Không sửa test để pass trừ khi contract đổi có ghi trong module plan đã duyệt.

## Assumptions

1. `.html` không dời (URL contract); chỉ JS/CSS move. `trace.html` stub giữ nguyên.
2. `thyca/serve/` ngoài scope — layout agent chỉ verify `static.py` phục vụ subdirs;
   nếu cần sửa serve thì là micro-fix do orchestrator duyệt riêng, không gộp refactor.
3. Không thêm dependency (kể cả npm/build step — giữ MPA + ES modules + classic scripts
   như hiện tại), không đổi visual, không thêm feature.
4. SOLID thực dụng: SRP là chính (tách file oversize, CSS scoped theo trang, giảm
   duplicate selectors); không nhồi pattern vào JS/CSS.
5. Module plans được orchestrator duyệt mới có hiệu lực; layout trong plan tổng là
   đề xuất, teams được đề xuất chỉnh với lý do.
6. Mỗi team branch (tên phẳng, không nested) từ `refactor/webui-solid` sau commit
   layout (GOAL-002).
7. Standing rules từ backend (giữ nguyên): contributor `xhigh`, review `1.3 max`,
   English prompts, coder-no-commit/reviewer-commits, skill `code-reviewer` đọc actual diff.
8. **Test-harness updates allowed** (user chốt 2026-09-22): tests webui pin implementation
   (substring/block-extract/source-eval) nên splits yêu cầu sửa cơ chế load/extract của
   harness. Locks: (a) assert lines byte-identical — diff test chỉ đụng loader/extractor/path
   lines; (b) code + harness update cùng 1 branch; (c) reviewer soi test-diff theo checklist;
   (d) full 719 xanh. Evidence: pager-block extract (test_cost/trace_journal), substring
   assert (test_serve_chat.py:737), source-eval harness (concurrent_streams/live_recovery).
