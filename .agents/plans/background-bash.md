---
status: done
created: 2026-09-18
last_updated: 2026-09-18
---

# Background bash — tool chạy nền, phiên không bị chặn

## Summary

Lệnh shell dài (OCR nhiều phút) giữ turn chiếm phiên (`SessionBusy`) trong toàn bộ
thời gian chạy; user không thể nói gì với Thyca trong phiên đó. Thêm chế độ chạy nền
cho tool `bash`: model truyền `background: true`, nhận ngay id, turn kết thúc bình
thường (phiên tự do); model poll kết quả bằng tool mới `bash_read(id, wait)`.

Không đổi vòng lặp agent, không đổi claim/SessionBusy, không thêm luồng ghi transcript.
Hành vi mặc định (foreground) giữ nguyên.

**Reuse**

- `asyncio.create_subprocess_exec(..., start_new_session=True)` + `kill_process_group` (`bash.py`)
- `RESULT_CAP_BYTES` (`protocol.py`) cho buffer tail mỗi tiến trình
- `register_file_tools` là điểm đăng ký duy nhất của builtin tools (CLI + ChatApp)

**Add**

- `thyca/tools/builtin/background.py`: `BackgroundProcs` (start / read / kill_all) + `bash_read_spec`
- `bash` spec: tham số `background: boolean`; manager truyền qua `bash_spec(background)` / `register_file_tools(..., background=)`
- `bash_read` spec: `{id: string, wait: integer 0..60 = 0}`
- CLI + ChatApp: tạo `BackgroundProcs()`, dọn tiến trình khi shutdown

## Tasks

### GOAL-001: Manager + tool wiring

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | `background.py`: `BackgroundProcs` — start (subprocess, start_new_session), drain stdout vào tail buffer giới hạn `RESULT_CAP_BYTES`, timeout kill process group, per-proc done event; `read` trả status running/done + tail; unknown id → ValueError kèm known ids; `kill_all` dọn sạch | x | 2026-09-18 |
| TASK-002 | `bash.py`: tham số `background` (schema + handler), default timeout 1800 cho background, thông báo poll `bash_read`; mô tả tool cập nhật | x | 2026-09-18 |
| TASK-003 | `bash_read_spec` (`id`, `wait` 0–60 default 0), đăng ký trong `register_file_tools` | x | 2026-09-18 |
| TASK-004 | CLI tạo manager và `kill_all` ở finally; ChatApp tạo manager, `kill_all` trong `shutdown()` | x | 2026-09-18 |

### GOAL-002: Tests + docs

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-005 | Tests: start→read done, read lúc running, unknown id báo known ids, timeout kill group (marker không được ghi), `wait` chờ đến khi xong, foreground không đổi | x | 2026-09-18 |
| TASK-006 | Baseline `tools=11` → `tools=12` (test_cli) + dòng tương ứng trong AGENT_RULES | x | 2026-09-18 |
| TASK-007 | CHANGELOG entry; README câu tools nếu cần | x | 2026-09-18 |

## Test Plan

- `pytest tests/test_tool_bash.py tests/test_tool_files.py tests/test_cli.py`
- `pytest` toàn bộ trước khi xong
- Thử tay: `bash {command: "sleep 30", background: true}` rồi `bash_read` → running, sau khi xong → exit 0

## Assumptions

- Một manager mỗi app (daemon hoặc một lần chạy CLI); handler chỉ chạy trên loop
  của app đó nên không cần khóa thread.
- Background không stream ra UI — model poll; UI vẫn thấy "Đang dùng: bash" trong
  round gọi, còn giữa các turn thì phiên tự do nên user chat được.
- `wait` cap 60s để không giữ event loop lâu; model có thể gọi lại.
- Không thêm cap timeout mới trong task này (keep scope); default 1800s cho background.
