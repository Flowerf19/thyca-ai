---
status: done
created: 2026-08-24
last_updated: 2026-08-24
---

# Chat — nhắc nhớ sau 15 phút im

## Summary

Phiên Chat im 15 phút thì UI hỏi “Phiên im 15 phút — nhớ gì không?”. Không tự `memory_remember`. Không ghi raw vào daily. Timer **trên trình duyệt**, chỉ khi `chatLive` (`thyca --serve`).

## Tasks

### GOAL-001: Nudge + timer

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | Markup `#idle-nudge` trong composer (câu + Nhớ + Để sau). `hidden` mặc định. CSS token sẵn, không theme mới | x | 2026-08-24 |
| TASK-002 | `app.js`: `IDLE_MS = 15 * 60 * 1000`. `armIdle` sau gửi thành công / gõ / phiên mới / đổi mode. `showIdle` chỉ chat + live + không busy + đã có `.entry-user`. Nhớ = gửi “Hãy nhớ những điều đáng giữ trong phiên này.” Để sau = ẩn + `armIdle` lại | x | 2026-08-24 |

Xong khi: parse HTML có `#idle-nudge`; `app.js` có `IDLE_MS = 15 * 60 * 1000`; mock tĩnh không hiện nudge (không `chatLive`).

## Test Plan

- `tests/test_serve_chat.py`: đọc `index.html` + `app.js` — có id và hằng 15 phút; không chờ timer.
- `uv run pytest -q`. Không live LLM.

## Assumptions

1. Không API mới. Không cron server.
2. Đóng tab = hết timer. Đúng “nhắc UI”.
3. Một lần mỗi chuỗi idle; activity reset.
4. Phiên trống không hỏi.
5. Câu gửi khi bấm Nhớ là tin user thật — model tự gọi `memory_remember` nếu đáng.
