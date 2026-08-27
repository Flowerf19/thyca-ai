---
status: done
created: 2026-08-24
last_updated: 2026-08-24
---

# Session notebook title

## Summary

Sidebar «Phiên gần đây» đang lấy dòng user đầu (`session_title` trong `thyca/chat_app.py`) nên list toàn `alo` / `chào`. Đổi sang tiêu đề sổ tay: persist trên JSONL, sinh một lần sau lượt đầu, không quote câu user.

Success: phiên trống = `Phiên trống`; phiên có tin nhưng chưa có title = `Sáng 24 thg 8` (từ id); sau lượt đầu thành công = 3–6 chữ do model đặt, lưu lại, list/get/turn cùng một title; compaction không làm mất title; LLM title lỗi không fail lượt.

## Tasks

### GOAL-001: Persist title trên JSONL

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | `Session.title: str \| None`. `SessionStore.scan` đọc dòng `{"type":"meta","title"}` (không `role`) — last wins, không đưa vào `messages`. Dòng không-message khác vẫn `SessionCorrupt`. `load`/`latest` gắn title | x | 2026-08-24 |
| TASK-002 | `append_meta` durable như append message. `rewrite(..., title=)` ghi meta trước messages. `SessionManager.set_title` + `compact_if_needed` giữ title | x | 2026-08-24 |

### GOAL-002: Display + đặt tên sau lượt đầu

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-003 | `thyca/sessions/title.py`: `sanitize_title` (1 dòng, bỏ ngoặc, cắt 48), `fallback_title(id)` → `Sáng/Chiều/Tối D thg M`, `display_title(session)` — stored → fallback nếu có user → `Phiên trống`. Không lấy content user | x | 2026-08-24 |
| TASK-004 | `ChatApp._run_turn` sau `loop.run`: nếu chưa title, `connect.chat` một prompt ngắn (không tools). Thành → `set_title`. `LLMError`/rỗng → giữ fallback, không fail turn. Không đặt lại khi đã có title | x | 2026-08-24 |
| TASK-005 | `scripts/retitle_sessions.py` + `retitle_missing`: đặt title cho phiên cũ chưa có meta. Không ghi fallback. Chạy được trên `~/.thyca` | x | 2026-08-24 |

## Test Plan

- Store: meta + messages load đúng; last meta wins; thiếu `role` không phải meta → corrupt; rewrite/compact giữ title; messages trong API không chứa meta.
- `display_title`: trống; id `2026-08-24T10-56-24_abcd` → `Sáng 24 thg 8`; stored thắng utterance.
- HTTP: create = `Phiên trống`; turn + FakeLLM title `Cà phê với Hòa` persist qua GET list; title chat lần 2 không tools; title boom → turn 200 + fallback; turn 2 không gọi name lại.
- `uv run pytest -q`. Không live LLM.

## Assumptions

1. User chốt notebook title (2026-08-24). Không rename UI. Backfill phiên cũ bằng `scripts/retitle_sessions.py`, không ghi fallback.
2. Meta line không phải Message — không vào AgentLoop. Khác compaction summarizer: `services/session.md` vẫn cấm LLM khi compact; title là 1 `chat()` display-only sau lượt đầu.
3. Fallback 3 buổi: `<12` Sáng, `<18` Chiều, còn lại Tối. Giờ lấy từ session id (timezone lúc create).
4. Chỉ `ChatApp.turn` đặt tên. CLI chưa title → web list dùng fallback đến lượt web kế.
5. UI không đổi: đã render `item.title`.
