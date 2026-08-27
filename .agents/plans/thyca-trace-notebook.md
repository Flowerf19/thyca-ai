---
status: in-progress
created: 2026-08-27
last_updated: 2026-08-27
---

# Trace notebook — sổ nghe, không dashboard

## Summary

API Trace (`/api/traces`, `/api/traces/stats`, detail) đã chạy. UI hiện tại **không phải bề mặt Thyca**: `hydrateTrace()` dump admin CRUD vào `.page-body` (6 số xanh Memories, `<select>` + `type=date`, bảng `by_model`, 50 `.trace-card` viền trái, detail chôn dưới list). Header / sidebar / mini-player vẫn mock `think → act` / `ses_7f3a` / **Phát lại lượt** (`render.js:90–111`, `data.js:130`).

Thyca đã có ngôn ngữ: sidebar ấm + giấy (`tokens.css` Sổ tay đồng hành), Chat = phiên `page-card`, Memories = `pagesFromStats` → overview `stat-row` + paper. Trace gốc (`thyca-web-notebook-redesign.md` TASK-012) là **sổ nghe**: `music-page` / `album-note` / `phase-list` / `track-rule` / mini-player pha.

Plan này **chỉ UI**. Không đổi backend, pricing, JSONL, endpoint. Thay GOAL-004 visual của `thyca-trace-cost.md` (TASK-015 layout). Giữ fetch + `traceScoreFromMessages`.

Success:

1. Mở Trace không còn H1 `think → act`, không còn sidebar `ses_7f3a`, không còn nút Phát lại. Badge mode = số lượt API.
2. Sidebar là `page-card` (overview + mỗi lượt), paper là **một** trang — giống Chat/Memories. Click card đổi giấy, không append.
3. Overview: 4 ô terracotta (`yêu cầu` · `vào` cache · `ra` · `chi phí`) + pills filter + `by_model` typographic. Không form, không table, không `.trace-card`.
4. Trang lượt: `music-page` + staff Bravura + `phase-list` think/act/observe/naming + token một dòng + `<details>Xem JSON`. Failed đọc được. Số/ngày `vi-VN`.
5. `python -m http.server --directory webui` giữ mock `music-page`. 320/375/414/768 không overflow. `uv run pytest -q` không thêm failure.

## Quyết định đã chốt

1. **IA = Memories.** `pages[0]` = Tổng quan; `pages[1:]` = một lượt / `page-card`. Không list trong giấy.
2. **Hydrate = gán `modes.trace.pages`, rồi `renderPage` + `renderPageList`.** Cấm `root.innerHTML = traceLayout(...)`. Pattern: `render.js:hydrateMemories` + `chat.js:fillChatAt`.
3. **Giấy lượt = `music-page`.** Tái `album-note`, `track-kicker`, `track-rule`, `phase-list`, `music-note`. Không card-in-card, không side-stripe.
4. **Filter = pills trên overview** (`Model` từ `stats.models`, `Trạng thái` all/ok/failed/loop_limit, `Ngày` 7d/30d/all) + ô search sidebar sẵn có. Pills refetch `/api/traces`. Không `<select>`, không `type=date`, không nút Lọc.
5. **Mini-player = plaque.** Hiện khi đang xem một lượt: `strong` = title, `small` = model ngắn · status. Ẩn `#mini-play`. History không replay NDJSON, không `bindPlayer`.
6. **Màu số Trace không đụng Memories.** Override `.notebook[data-mode="trace"] .stat-row strong` → `--color-trace`. Giữ `.stat-row strong { color: var(--color-memory) }` cho Memories.
7. **Unknown / thiếu usage:** không in `UNKNOWN`; tag bỏ trống hoặc `—`; `—` cho cost/token thiếu; không `$0.00`.
8. **Failed:** `tag: "lỗi"`, `data-status="failed"` trên `page-card`; kicker giấy dùng `--color-danger`. Staff cadence failed giữ grammar hiện có (`V whole`).
9. **Không assemble giả.** Timeline chỉ span có trong `messages` (`think#n`, tools → act, assistant text cuối → observe, `meta.kind==naming` → naming).
10. **Xóa CSS admin** sau khi layout sống: `.trace-card`, `.trace-filter`, `.trace-apply`, `.trace-list`. Giữ token `--color-trace*`.

## Tasks

### GOAL-001: Hydrate Trace như Memories/Chat

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | `trace.js`: `pagesFromTraces(stats, list, filter)` trả `[{overview}, ...turns]`. Overview `tone: "trace"`, `title: "Tổng quan"`, `tag: "stats"`, `date: "{n} lượt"`. Turn: `title` (session title), `date` `vi-VN` ngắn, `tag` = model ngắn hoặc `"lỗi"`, `sessionId` + `turnIndex` để fetch. Không HTML dump. | | |
| TASK-002 | `hydrateTrace()` gán `modes.trace = { label, listLabel: "Lượt gần đây", kicker, note, chips: [], pages }` rồi `renderPage(0)` + `renderPageList`. Cập nhật `.mode-count` = `totals.requests`. API lỗi / 404 → return, giữ mock `data.js`. | | |
| TASK-003 | `render.js:renderMode("trace")` bỏ nhánh render mock rồi hydrate. Gọi `hydrateTrace()` giống Memories (không `renderPage` mock trước). `renderPageList` click turn → `fillTraceAt(index)` rồi `renderPage` (như `fillChatAt`). Overview không fetch detail. | | |

Xong khi: vào Trace, sidebar không còn `ses_7f3a`; giấy đầu là Tổng quan; badge ≠ 2 nếu API có lượt.

### GOAL-002: Overview — 4 ô + pills, giọng sổ

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-004 | Body overview: `book-reading` + `stat-row` đúng 4 ô: yêu cầu · vào (nhỏ `cache {n}`) · ra · chi phí. Dưới: `progress-label` `p50` / `p90` (latency `vi-VN`). Không ô tổng, không `14209ms` thô. | | |
| TASK-005 | Pills ngay dưới stats, class tái chip hiện có (`suggestion-chip` hoặc `page-tag` row). Groups: model (all + `stats.models`) · status · 7d/30d/all. Active pill `aria-pressed`. Click → refetch list với `from`/`to` tính client (UTC `YYYY-MM-DD`), rebuild `pages[1:]`, ở lại overview. | | |
| TASK-006 | `by_model`: danh sách typographic `<p>` / `<ul>` không table. Một dòng `/ model ngắn · {req} lượt · {cost}`. Bỏ `<table>` / `.md-table-wrap`. | | |
| TASK-007 | Formatter thuần: `vi-VN` grouping cho token; cost `$0,0075` hoặc `—`; latency `458 ms` / `1,4 s` / `14 s`; ISO → `10:17 26 thg 8`; model `meta/muse-spark-1.2-contributor` → `muse-spark`. Dùng cho overview, card, giấy lượt. | | |

Xong khi: overview nhìn họ hàng Memories (4 số, không form). Pills đổi sidebar. Không `mm/dd/yyyy`.

### GOAL-003: Giấy lượt = sổ nghe

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-008 | `fillTraceAt`: `GET /api/traces/{session}/{turn}` → `page.body`. Header: kicker `ses_… · lượt n · {model} · {status}`, H1 = title, note = `vào {n} (cache p%) → ra {n} · {cost} · {lat}`. Badge cache chỉ khi `cached_tokens > 0`. | | |
| TASK-009 | Body: `music-page` > `album-note` (kicker pha, H2 title, p token một câu, `track-rule` tổng latency) + host staff (`entry-thyca` / `mountStaff` + `traceScoreFromMessages`) + `ol.phase-list` mỗi span `think #n` / tools / `observe` / `naming` với `is-done` và `track-rule` width = `span.latency_ms / turn.latency_ms` (0 nếu thiếu). `<details>Xem JSON` cuối, không phải block chính. Không `#player-button`. | | |
| TASK-010 | CSS: `.notebook[data-mode="trace"] .stat-row strong` và `.progress-label strong` = `--color-trace`. `.trace-timeline .track-rule` cao ≥ `0.35rem` (hairline 1px hiện không đọc được). `.page-card[data-status="failed"] .page-tag` danger. Không `border-inline-start` card. | | |

Xong khi: click một lượt muse-spark hiện staff + pha trong giấy; list không còn trong paper; failed khác completed.

### GOAL-004: Giết mock chrome, dọn admin CSS

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-011 | Mini-player: `hidden` trên overview và khi không có lượt. Trên giấy lượt: hiện plaque, `#mini-play { display: none }`, copy từ page.title / model. Xóa `bindPlayer` / `setTracePlaying` khỏi nhánh Trace (giữ hàm nếu mock tĩnh còn nút). | | |
| TASK-012 | `data.js` mock Trace: một `music-page` (không hai trang `ses_7f3a`). `kicker`/`note` khớp copy plan: `Trace · AgentLoop`, `model · cache · in/out là derived`. Fallback khi không API. | | |
| TASK-013 | Xóa `.trace-card`, `.trace-filter`, `.trace-apply`, `.trace-list` và markup tương ứng. Không thêm font, token, dependency. | | |

Xong khi: grep không còn `.trace-card`; mock `http.server` vẫn mở được Trace.

### GOAL-005: Verify

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-014 | Chrome `uv run thyca --serve --port 8766`: overview 4 ô terracotta; sidebar `page-card` + `is-active`; pills 7d cắt list; một lượt có staff + phase; failed tag lỗi; không H1 `think → act`; không overflow 320/375/414/768 (emulate, không chỉ resize cửa sổ kẹp ~500). | | |
| TASK-015 | `python -m http.server --directory webui` → mock music-page, không throw. `uv run pytest -q` không thêm failure ngoài baseline `test_debug_prints_prompt_flags`. | | |

## Test Plan

Không thêm pytest backend (API không đổi).

- `uv run pytest -q` — regress only.
- Manual serve local (source, không binary `~/.local/bin/thyca`):
  1. Trace default = Tổng quan, 4 ô, pills, không select/date/table/card stripe.
  2. Sidebar count khớp lượt; search sidebar lọc title.
  3. Click lượt → giấy `music-page`, staff, JSON collapsed; mini-player plaque không play.
  4. Failed = tag lỗi; unknown không in `UNKNOWN`; cost null = `—`.
  5. 320/375/414/768: không `overflow-x`, pill wrap, stat-row wrap 2×2 dưới ~600px.
  6. Static `http.server` không API = mock, không crash.

## Assumptions

1. Backend `thyca-trace-cost.md` GOAL-001–003 giữ nguyên; plan này không sửa `thyca/trace.py` / `serve.py` / `pricing.py`.
2. `renderPage` tiếp tục vẽ kicker/H1/note từ `page` — hydrate phải set field đó, không innerHTML header.
3. Search sidebar (`renderPageList` filter title/tag/date) đủ cho `q`; pills không thêm ô tìm trong giấy.
4. `7d`/`30d` = rolling từ hôm nay UTC, map `from` query đã có.
5. Staff grammar không đổi (`trace-score.js`); chỉ đổi chỗ mount (trong `music-page`, không dưới list).
6. Composer vẫn ẩn ngoài Chat. Chips Trace = `[]`.
7. Không LangSmith dark, không chart, không virtualize 50 hàng — 50 `page-card` sidebar chấp nhận được như Chat 41 phiên.
8. Copy UI tiếng Việt; identifier English.
