---
status: done
created: 2026-11-17
last_updated: 2026-11-17
---

# Memories Optimize — 3 Sổ Hành Vi (`Task` / `Habit` / `Other`), Timeline Task & Delta Flush

## Mục tiêu & Phạm vi

Khắc phục tình trạng băm nhỏ lá nhớ và nổ hàng trăm `##` khi flush mà không thêm graph DB hay tách thành nhiều file `.md` mỗi ngày:

1. **Giữ nền tảng lưu trữ hiện tại:** 1 file ngày `memory/YYYY-MM-DD.md` + SQLite derived index (`memory.sqlite`), dùng các trường JSON có sẵn trên heading (`task`, `cont`, `kind`, `status`, `habit`, `habit_explicit`, `bots`, `chat`, `proj`).
2. **Sổ 1 — Task:** 1 mục tiêu (`task-<8 hex>`) = 1 thẻ sống xuyên timeline, cập nhật tiến độ tại chỗ vào `latest_memory_id` (khung `done / processing / next`), không đẻ chuỗi `##` lẻ mỗi lần flush. Bổ sung trạng thái `cancelled` vào `TASK_STATUSES` (`open`, `done`, `cancelled`).
3. **Sổ 2 — Habit:** Giữ lá nhỏ như hiện tại (`kind="habit"` + `habit="<label>"`). Trùng nhãn chuẩn hóa (`Chunker.normalize`) trong ngày hoặc xuyên ngày cùng phạm vi owner → `reinforce` lá hiện có (gia hạn TTL) và cộng `count + 1` cho ngày mới (lặp lại trong cùng ngày không cộng count), nâng `habit_explicit=True` nếu bản mới là user nói trực tiếp, không tạo `##` mới. Khi `count >= 3` hoặc `habit_explicit=True` → đẩy lên `bots/<id>/USER.md` (`sweep_bot_habits`). Fetch toàn bộ `known_habit_labels` vào prompt để tránh đặt nhãn đồng nghĩa.
4. **Sổ 3 — Other:** 1 nhật ký theo ngày / phạm vi `(bots, chat, proj)`. Fact mới hoặc đổi giá trị được nối thêm dưới dạng dòng `  - HH:mm — ...` thụt 2 dấu cách vào thân mục nhật ký trong ngày (cùng việc gộp chủ đề mới vào tiêu đề `##` tối đa 80 ký tự); trùng chuẩn hóa hoặc đồng nghĩa gần (`rapidfuzz` `token_set_ratio >= 88`) → `skip`. Open task không được hút fact ổn định (OS, policy, cấu hình, guild snapshot…). Tách cài đặt 3 sổ sang `thyca/memories/ledgers.py`, khóa ranh giới `chat` để chống lộ DM sang Group, và cập nhật `last_day` khi sửa thẻ Task xuyên ngày.
5. **ActiveMemory & Delta Flush:** `<memory_tasks>` chỉ nạp task `status="open"` (rút ngay khi `done`/`cancelled`), kèm `[<latest_memory_id>]` và tóm tắt hiện tại. `<today>` hiển thị nhãn sổ gọn trên heading Task/Habit. Extractor nhận thêm `today_entries` + summary của `open_tasks` để trích xuất theo delta.

## Quyết định đã chốt

1. Không phải thiếu kind/graph — schema `task`/`habit`/`other` hiện tại đã đủ.
2. Nguyên nhân lỗi: flush extract mỗi lần sắp compact luôn gọi `remember` tạo session mới, không đối chiếu dữ liệu đã ghi trong ngày, và để open task hút cả fact `other` → hàng trăm `##` lẻ.
3. `Other`: gom thành 1 nhật ký / ngày / phạm vi (append dòng log thụt 2 dấu cách để `Chunker` giữ chung lá), trùng thì skip — không mint lá ngắn liên tục.
4. `Task`: 1 thẻ / mục tiêu, cập nhật tiến độ / checkpoint tại chỗ trên `latest_memory_id`; 3 trạng thái `open` | `done` | `cancelled`.
5. `Habit`: giữ lá nhỏ như hiện tại; trùng label trong ngày → skip/reinforce.

## Tasks

| ID | Task | Status | Notes |
|---|---|---|---|
| TASK-001 | Mở rộng `TASK_STATUSES` (`cancelled`), cập nhật tool/rules/WebUI label | done | `heading.py`, `memory_tools.py`, `rules.md`, `memories.js` |
| TASK-002 | Bổ sung `update_memory(time=...)`, tách `ledgers.py`, sửa cách ly cửa sổ `chat`, `last_day`, `GROUP_CONCAT`, gộp chủ đề & lọc mờ `Other` | done | `writer.py`, `facade.py`, `ledgers.py`, `archive_store.py` |
| TASK-003 | Nâng cấp `extract_from_transcript` & `apply_extracted` thành delta 3 sổ, nối `today_entries` trong `_flush_bot_memory` | done | `jobs.py`, `background_jobs/service.py` |
| TASK-004 | Cập nhật `format_task_catalog` & `split_today_by_session` (`_here_block`) | done | `facade.py`, `active.py` |
| TASK-005 | Cập nhật & bổ sung bộ test kiểm chứng, kiểm tra diff và chốt plan | done | `tests/test_memory_*.py`, `tests/test_bots_*.py` |

## Verification

- Focused suite (`tests/test_memory_heading.py`, `test_memory_task_habits.py`, `test_memory_active.py`, `test_memory_archived.py`, `test_memory_habits.py`, `test_memory_tools.py`, `test_memory_stats.py`, `test_memory_session_fixes.py`, `test_bots_jobs.py`, `test_bots_memory.py`, `test_bots_context.py`, `test_bots_context_fixes.py`, `test_workspace_context.py`, `test_workspace_e2e.py`, `test_webui_memory_edit.py`): **263 passed, 11 skipped** (`test_webui_memory_edit.py` bỏ qua vì chưa cài `node`).
- Suite memory + bots + workspace + agent_loop (`tests/test_memory_*.py`, `tests/test_bots_*.py`, `tests/test_serve_memory_stats.py`, `tests/test_agent_loop.py`, `tests/test_workspace_context.py`, `tests/test_workspace_e2e.py`): **479 passed**.
- Diff check: `git diff --check` sạch.
