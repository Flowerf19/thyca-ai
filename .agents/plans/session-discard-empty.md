---
status: done
created: 2026-08-24
last_updated: 2026-08-24
---

# Discard empty sessions

## Summary

`POST /api/sessions` ghi JSONL rỗng nên sidebar đầy «Phiên trống». Phiên trống không vào list; file rỗng bị xóa; New Chat là trang nháp local, chỉ persist khi gửi tin đầu.

Success: GET list không chứa phiên không có user text; New Chat không POST; gửi tin đầu tạo session và hiện trên list; file rỗng cũ bị xóa khi list/create.

## Tasks

### GOAL-001: Bỏ phiên trống khỏi list và đĩa

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | `is_blank` trong `title.py`. `SessionStore.delete`. `SessionManager.discard_empty(keep=)` xóa file không có user text | x | 2026-08-24 |
| TASK-002 | `ChatApp.list_payload` / `create` gọi `discard_empty`. List không trả phiên blank | x | 2026-08-24 |
| TASK-003 | `createChatSession` không POST — nháp `emptyPage`, `activeSessionId=null`. `sendChatTurn` vẫn create khi chưa có id | x | 2026-08-24 |

## Test Plan

- `discard_empty` xóa blank, giữ phiên có user, không đụng `keep`.
- HTTP: POST create → GET list rỗng; sau turn → list có id đó.
- `uv run pytest -q tests/test_session.py tests/test_serve_chat.py`.

## Assumptions

1. Blank = không có user text (cùng `display_title` → `Phiên trống`).
2. CLI `--continue` trên file rỗng vẫn được; web không list chúng.
3. Không DELETE API; dọn trong list/create.
