---
status: done
created: 2026-08-25
last_updated: 2026-08-25
---

# Builtin `bash` — lệnh máy, không sandbox

## Summary

`services/tools.md` TASK-310 **đã bỏ bash** (2026-08-20, “phức tạp, để sau”). Registry + `read`/`write`/`edit` đã chạy. Slice này **chỉ** thêm tool `bash` — POSIX, quyền user, không hỏi, không container.

`tools.md` ghi cap 20KB. Runtime đã cap mọi tool ở `ToolRegistry` `RESULT_CAP_BYTES` (32768). **Không** cap lần hai. 20KB superseded.

```python
async def bash(command: str, timeout: int = 30) -> str: ...
```

```text
/bin/bash -c <command>
cwd = getcwd()
env  = inherit
stdout+stderr gộp
timeout → kill process group (start_new_session)
```

Kết quả text:

```
exit: <n>
<output>
```

Timeout: luôn `exit: 124` + `timed_out: true` sau SIGKILL group (không dùng returncode `-9`).

Không PTY, không job nền, không login shell (`-l`).

## Tasks

### GOAL-001: Tool `bash`

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | `thyca/tools/builtin/bash.py`: `bash_spec()`. `command` required string. `timeout` mặc định 30, agent chọn số dương (không kẹp max). `parallel_safe=False`, `resource_key` cố định `"bash"` | x | 2026-08-25 |
| TASK-002 | Chạy `/bin/bash -c`, `cwd=getcwd()`, inherit env, `stdout=stderr` gộp, `start_new_session=True`. Timeout: `os.killpg` SIGKILL rồi `communicate`. Envelope `exit:` / `timed_out:` như trên | x | 2026-08-25 |
| TASK-003 | `register_file_tools` đăng ký `bash`. Cli + ChatApp đã gọi hàm này — không wire thêm | x | 2026-08-25 |
| TASK-004 | `PromptManager._RULES`: một câu — `bash` chạy ngay, quyền user, không sandbox, có thể lách PathGuard. Không dạy viết L2 bằng bash | x | 2026-08-25 |

## Test Plan

`tests/test_tool_bash.py` (không live LLM):

- `echo ok` → `is_error` false, `exit: 0`, chứa `ok`.
- `false` → `exit: 1` (không phải dispatch error trừ khi handler raise).
- `timeout=1` + `sleep 5` → `timed_out: true`, `exit: 124`, process group hết (không zombie).
- `timeout` thiếu → 30; `30.0` → 30; `0` / âm / `1.5` → lỗi; `121` giữ 121.
- Output > 32KB bị `_cap` (đuôi giữ, không cắt giữa UTF-8).
- Schema: `bash` trong `to_openai_schema()`, thiếu `command` → missing argument.
- Hai `bash` cùng lúc serialize (cùng lock `bash`).

`uv run pytest -q tests/test_tool_bash.py tests/test_tool_files.py`.

## Assumptions

1. Không sandbox, không cửa xác nhận — harness-v1 đã chốt.
2. `bash -c` không `-l` / `-i`. PATH = env process thyca.
3. Một `bash` tại một thời điểm (lock `"bash"`). MCP tools khác vẫn song song.
4. PathGuard không áp cho bash. Agent `bash echo >> ~/.thyca/memory/…` được — chấp nhận v1.
5. Không PTY: `vim` / password prompt không thuộc slice.
6. `false` / non-zero = thành công tool, `exit:` ≠ 0 trong content. Crash handler mới `is_error`.
7. `tools.md` giữ `done`; slice này file plan riêng. Không mở lại TASK-310.
