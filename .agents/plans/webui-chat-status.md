---
status: done
created: 2026-08-22
last_updated: 2026-08-26
---

> Superseded by `thyca-operational-music-trace.md` (2026-08-26):
> status/staff are an operational event trace, not a 1s poetry timer.
> Assumptions “Không SSE / stream” and the poetry pool no longer hold.

# Chat — gửi tin + status thơ inline

## Summary

Sau revert `bfd0869`, gửi tin vẫn chờ hết `POST /turn` rồi `renderPage` cả thread: ô soạn còn chữ, không có status cạnh tin vừa gửi, hint dưới composer nói “Thyca đang nghĩ…”.

Làm đúng flow đã chốt: **gửi ngay** → tin `you` hiện trên giấy với animation → **status inline** ngay dưới tin đó (cùng style `.entry-thyca`, gạch terracotta) → mỗi **1s** một câu từ pool thơ (random, không lặp 4 câu gần nhất) với **trượt dọc + blur nhẹ + scale**, không fade thuần → khi có reply, status trượt ra, nội dung thyca hiện **đúng ô đó**. Không đụng Trace, Memories, `serve.py`, AgentLoop.

Success: Enter / nút gửi (bookmark) xóa ô soạn liền; tin user + status nằm trên thread; composer hint không xoay câu; đổi câu không dùng `opacity` làm chuyển chính; `prefers-reduced-motion` chỉ đổi chữ, không motion; mock tĩnh vẫn không crash.

## Tasks

### GOAL-001: Gửi xong vẽ tin + status trên thread

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | `submitLine` (`webui/js/app.js`): validate → xóa textarea ngay → `beginOutgoingTurn(text)` → `setBusy` (disable gửi/ô/nút phiên mới, **không** ghi hint thinking) → `sendChatTurn` như hiện tại. Lỗi: gỡ status, giữ tin user, `showError`. Đổi mode lúc chờ: không `renderPage` đè Memories/Trace | x | 2026-08-22 |
| TASK-002 | `webui/js/chat.js`: `beginOutgoingTurn` tạo `.entry-list` nếu đang empty; append `article.entry.entry-user.is-enter` (reuse `entryHtml`); append `article.entry.entry-thyca.entry-status` ngay dưới — `<time>thyca</time>` + hàng `span.status-dots` (3 chấm CSS) + `span.status-ticker` một câu. Không nút Phát trong thread. `aria-label="Thyca đang nghĩ"`, `aria-live="off"` (tránh SR đọc mỗi giây) | x | 2026-08-22 |

### GOAL-002: Xoay câu 1s + thay status bằng reply

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-003 | Pool 23 câu (assumptions). `nextStatus(recent)` random, loại 4 câu vừa dùng. `setInterval` **1000ms**. Ticker: câu cũ class `is-out`, câu mới `is-in` cùng grid cell; sau 200ms xóa node cũ. `stopStatusCycle` clear interval khi xong / đổi mode / lỗi | x | 2026-08-22 |
| TASK-004 | Sau `sendChatTurn` thành công: `settleIncoming` — **không** `renderPage` cả trang. Đếm node thread trừ `.entry-status`; `replaceWith` `.entry-list` mới từ `page.body`; phần sau `kept` thêm `is-enter`. Status biến mất vì không có trong HTML mới. Cập nhật `h1`/`kicker` + `renderPageList`. Fallback `renderPage` chỉ khi không có `.entry-list`. Mock: chờ ≥1s rồi gỡ `.entry-status` | x | 2026-08-22 |

### GOAL-003: Motion tokens, không fade thuần

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-005 | `webui/css/workspace.css` only (token sẵn). Send/enter: `320ms var(--ease-out)` `translateY(0.6rem) scale(0.97) → none` (opacity phụ ≤ 0.15 khoảng, không đứng một mình). Status swap: `200ms` — out `translateY(-110%) scale(0.97) blur(2px)`; in từ `translateY(110%)` cùng scale/blur. Status không `opacity` transition. Dots: 3 nốt scale 0.7↔1 lệch phase 160ms, màu `var(--color-accent)`. Ticker `height: 1.55em; overflow: hidden`. Chrome đã ép reduced-motion `0.01ms` — không thêm JS trừ bỏ interval animation class khi `prefers-reduced-motion` (đổi `textContent` tại chỗ) | x | 2026-08-22 |

## Test Plan

- `uv run pytest -q` — không đổi Python; suite hiện 141.
- Parse `webui/index.html` vẫn pass (không đổi markup tĩnh).
- Tay: `thyca --serve` — gửi 1 tin: ô trống ngay, user trượt vào, status dưới tin (không dưới composer), câu đổi mỗi ~1s khác 4 câu trước, reply vào đúng ô status. Gửi lần 2 trên cùng phiên không mất tin cũ. Enter = gửi; Shift+Enter = xuống dòng. `python -m http.server --directory webui`: gửi mock không throw. Reduced motion: chữ đổi, không trượt.

## Assumptions

1. **Phát = nút gửi composer + Enter.** Shift+Enter xuống dòng. IME (keyCode 229 / isComposing) không gửi. Không thêm play trên thread (bản revert đã sai chỗ đó). Ô soạn vẫn dưới; “vùng trên” = giấy/thread.
2. Tone **nhẹ / thơ**, đúng 23 câu dưới. Không câu nghịch (“chữ đang trốn”).
3. Không biết ETA (một POST chặn hết loop) → **không** thiên vị “Sắp xong rồi…” những giây cuối.
4. Hint composer chỉ error/success. Không xoay status dưới đáy.
5. Không typewriter. Một kiểu swap: slide + blur + scale.
6. ~~Không SSE / stream. Status chạy tới khi JSON turn về.~~ Superseded: NDJSON operational events.
7. `renderPage` sau gửi là nguyên nhân “thô” — cấm trên happy path.
8. Pool (23):

```
Đang tìm vần…
Đang lắng nghe nhịp…
Đang tìm tứ thơ…
Nghe nhịp trong đầu…
Đang đợi cảm hứng…
Lắng nghe khoảng lặng…
Đang chọn từ…
Đang cân nhắc chữ…
Đang sắp xếp nhịp…
Đang tìm hình ảnh…
Đang buộc câu thơ…
Đang chỉnh nhịp điệu…
Hmm…
Đang suy nghĩ…
Tiếp tục suy nghĩ…
Đang để cảm xúc lắng…
Đang nghe trái tim…
Đang viết tiếp…
Đang làm thơ…
Đang viết khổ thơ…
Đang thả chữ xuống trang…
Đang để thơ tự đến…
Sắp xong rồi…
```
