---
status: in-progress
created: 2026-09-22
last_updated: 2026-09-22
---

# M7 serve — module plan

## Summary

Chuyển 7 file lẻ `thyca/serve.py` (600 dòng), `thyca/bridge.py` (486),
`thyca/serve_daemon.py` (84), `thyca/serve_memory.py` (57), `thyca/trace.py`
(301), `thyca/trace_api.py` (171), `thyca/turn_state.py` (128) vào package mới
`thyca/serve/`, tách 2 file oversize trước (mục tiêu số 1), giữ nguyên behavior
và 2 ràng buộc bất biến: serve chỉ bind loopback, API không lộ secret / path
nội bộ / stack. Không đụng `thyca/webui/`, không đổi memory/session contract.

## Tasks

### GOAL-001: Physical move vào `thyca/serve/` (mechanical, không refactor logic)

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | `git mv` 7 file theo bảng target layout §1 (giữ nguyên nội dung, chỉ sửa import path `thyca.X` → `thyca.serve.X`) | | |
| TASK-002 | Viết `thyca/serve/__init__.py` re-export bề mặt cũ (`ServeError`, `make_server`, `run`, `default_webui`, `SENTINEL`, `public_turn_error`) để consumers/tests cũ vẫn import được trong bước chuyển | | |
| TASK-003 | Cập nhật import consumers: `thyca/cli.py:214-215` → `thyca.serve`, `thyca/chat_app.py:39` → `thyca.serve.turn_state`, tests `test_serve_*.py` + `test_trace.py` + `test_session.py:600-673` → path mới | | |
| TASK-004 | Verify sau move: `uv run pytest tests/test_serve_chat.py tests/test_serve_config.py tests/test_serve_daemon.py tests/test_serve_errors.py tests/test_serve_memory_stats.py tests/test_serve_trace.py tests/test_trace.py tests/test_session.py -q` xanh bằng baseline, `python -c "import thyca.serve"` không circular, `git diff --check` sạch | | |

### GOAL-002: Tách `serve.py` 600 dòng (oversize #1)

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-005 | Tách khối config/onboarding/provider (`serve.py:66-110` `_config_values`, `_config_meta`, `_merge_saved_key`, `_parse_config_payload` + handler methods `serve.py:308-457`) → `thyca/serve/config_api.py` (hàm nhận `handler`, không import handler class) | | |
| TASK-006 | Tách static file (`serve.py:545-600` `_static`, `_safe_file`, `_content_type`, `_TYPES`) → `thyca/serve/static.py` | | |
| TASK-007 | Phần còn lại thành `thyca/serve/server.py` (bootstrap: `ServeError`, `LOOPBACK`, `make_server`, `run`, `_QuietHTTPServer`, `default_webui`) + `thyca/serve/routes.py` (factory `_handler` + dispatch `do_GET/POST/DELETE/PATCH`) | | |
| TASK-008 | Verify ranh giới mới: mỗi file < 400 dòng, `tests/test_serve_config.py` vẫn xanh, spot-check API key trong `GET /api/config` vẫn là `""` (`serve.py:66-75`) | | |

### GOAL-003: Tách `bridge.py` 486 dòng (oversize #2)

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-009 | Tách mapping lỗi + parse body (`bridge.py:37-94` `parse_turn_body`, `public_turn_error`) → `thyca/serve/errors.py` | | |
| TASK-010 | Tách stream machinery (`bridge.py:96-293` `bridge_sink`, `bridge_worker`, `write_line`, `_stream_end`, `_log_turn_failure`, `pump_stream`, `stream_turn`) → `thyca/serve/turn_stream.py` | | |
| TASK-011 | Phần còn lại (`bridge.py:295-486` `session_*` endpoints + `_sessions_error`, `_missing_chat`) → `thyca/serve/sessions_api.py` | | |
| TASK-012 | Verify: `tests/test_serve_chat.py` (2158 dòng, gate chính) + `tests/test_serve_errors.py` + `tests/test_turn_stream.py` xanh; mỗi file mới < 400 dòng | | |

### GOAL-004: Khóa ràng buộc + chống cycle

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-013 | Chuyển import runtime `ChatApp` trong `serve.py:25` và `bridge.py:32` sang `TYPE_CHECKING` + annotation string theo mẫu sẵn `trace_api.py:18-21`; handler/bridge chỉ dùng duck-type surface (`_json`/`_read_json`/`wfile`) như docstring `bridge.py:1-14` đã cam kết | | |
| TASK-014 | Giữ `turn_state.py` nguyên file (TurnHub + TurnState, đã SRP đúng); M8 (`chat_app.py:39`) import một chiều `thyca.serve.turn_state`, không cho `thyca/serve/*` import `thyca.app`/`chat_app` ở runtime | | |
| TASK-015 | Giữ nguyên import private `from thyca.config import _parse_dict` trong hàm (`serve.py:106-110`): không inline, ghi chú phối hợp M3 thay bằng API public khi M3 có | | |
| TASK-016 | Full gate: `uv run pytest -q` bằng/baseline hơn, `python -c "import thyca.serve.server, thyca.serve.sessions_api, thyca.serve.turn_state, thyca.chat_app"`, `git diff --check` sạch | | |

## Target layout (bảng move chính xác)

| Nguồn | Đích | Ghi chú |
|-------|------|---------|
| `thyca/serve.py` (600) | `thyca/serve/server.py` + `thyca/serve/routes.py` + `thyca/serve/config_api.py` + `thyca/serve/static.py` | Tách theo GOAL-002; `server.py` là entry bootstrap |
| `thyca/bridge.py` (486) | `thyca/serve/errors.py` + `thyca/serve/turn_stream.py` + `thyca/serve/sessions_api.py` | Tách theo GOAL-003 |
| `thyca/serve_daemon.py` (84) | `thyca/serve/daemon.py` | Move nguyên file |
| `thyca/serve_memory.py` (57) | `thyca/serve/memory.py` | Move nguyên file |
| `thyca/trace.py` (301) | `thyca/serve/trace.py` | Move nguyên file, giữ tên |
| `thyca/trace_api.py` (171) | `thyca/serve/trace_api.py` | Move nguyên file, sửa `from thyca.trace import` → `from thyca.serve.trace import` (`trace_api.py:16,107`) |
| `thyca/turn_state.py` (128) | `thyca/serve/turn_state.py` | Move nguyên file, không tách |
| (mới) | `thyca/serve/__init__.py` | Re-export bề mặt cũ cho bước chuyển |

Thứ tự tách file oversize: `serve.py` trước (GOAL-002) vì là router trung tâm
mọi test serve chạm vào; `bridge.py` sau (GOAL-003) vì phụ thuộc surface
`_json`/`_stream_headers` của handler. Ranh giới mới: `routes.py` chỉ dispatch
regex→hàm (`_SESSION_RE`, `_TURN_RE`, `_TRACE_*` giữ nguyên grammar);
`config_api.py`/`sessions_api.py`/`turn_stream.py` nhận `handler` duck-type,
không import class Handler — đúng cam kết hiện tại trong `bridge.py:1-14`.

## SOLID findings (có evidence)

- **SRP — `serve.py:199-577` (Handler, ~380 dòng methods trong 1 class):** một
  class vừa routing (`do_GET:200-239`, `do_POST:241-282`), vừa config CRUD +
  onboarding + provider test (`308-457`), vừa trace proxy (`469-508`), vừa
  static + JSON plumbing (`523-577`). Tách `config_api.py` + `static.py` +
  `routes.py` như GOAL-002.
- **SRP — `bridge.py:68-486`:** `public_turn_error` (mapping lỗi thuần) +
  `pump_stream`/`stream_turn`/`bridge_worker` (NDJSON + thread) +
  `session_list/get/create/turn/cancel/follow/rename/delete` (HTTP CRUD)
  cùng một module. Tách `errors.py` + `turn_stream.py` + `sessions_api.py`
  như GOAL-003.
- **SRP nhẹ — `trace.py:109-175` (`_sum_tokens`, ~65 dòng, 7 giá trị trả về):**
  cộng dồn tokens/cost/latency/model/rounds trong một hàm. Giữ nguyên ở bước
  này (dưới ngưỡng 400, có test `test_trace.py` phủ); chỉ tách nếu team
  chứng minh thêm case sai số khi sửa.
- **DIP (có bằng chứng) — `serve.py:25` (`from thyca.chat_app import ChatApp`)
  + `bridge.py:32` (tương tự) vs `chat_app.py:39`
  (`from thyca.turn_state import TurnHub, TurnState`):** sau move thành cạnh
  hai chiều `thyca.serve` ↔ `thyca.app`. Fix: `TYPE_CHECKING` + annotation
  string (mẫu `trace_api.py:18-21`), runtime chỉ duck-type — TASK-013.
- **ISP (đã tốt, giữ):** `bridge.py` chỉ chạm surface `_json`/`_read_json`/
  `_read_body`/`_stream_headers`/`wfile` của handler (docstring `bridge.py:1-14`);
  ranh giới mới giữ nguyên hợp đồng hẹp này, không mở rộng.
- **OCP (không làm):** dispatch `do_GET/do_POST` là chuỗi `if` trên regex
  (`serve.py:200-282`) — có thể thay bằng bảng route, nhưng không có yêu cầu
  thêm route mới nên giữ nguyên để tránh rewrite.

## Cycle / import risks

1. **`thyca.serve` ↔ `thyca.app` (nguy cơ chính):** `chat_app.py:39` cần
   `TurnHub/TurnState`; `serve.py:25` + `bridge.py:32` cần `ChatApp` cho type.
   Tránh bằng TASK-013/014: `turn_state.py` không import gì từ `chat_app`;
   phía serve chỉ `TYPE_CHECKING` import `ChatApp`.
2. **`serve_daemon.py:10` (`from thyca.serve import ServeError`):** sau move
   thành import nội package (`from thyca.serve.server import ServeError` hoặc
   từ `__init__`); `cli.py:214-215` cập nhật một lần theo TASK-003.
3. **Private cross-module `serve.py:106-110`
   (`from thyca.config import _parse_dict` trong hàm):** giữ lazy import trong
   hàm, không đưa lên top-level (tránh cycle với M3); phối hợp M3 khi có API
   public thay thế.
4. **`trace_api.py:16` (`from thyca.trace import ...`) + `trace_api.py:107`
   (lazy `from thyca.trace import aggregate`):** sau move cùng package, gộp
   thành một import top-level `from thyca.serve.trace import ...`, bỏ lazy
   import trong hàm.
5. **Tests chạm private (`test_serve_trace.py:95-204`
   `trace_api._trace_sessions`):** giữ tên module `trace_api` + tên biến
   `_trace_sessions` nguyên vẹn; chỉ đổi import path, không đổi tên private.

## Test Plan

- Baseline: `tools=7` fail đã biết ở `test_debug_prints_prompt_flags` không
  phải regression — không sửa số này (AGENT_RULES).
- Gate sau move (TASK-004): `test_serve_chat` + `test_serve_config` +
  `test_serve_daemon` + `test_serve_errors` + `test_serve_memory_stats` +
  `test_serve_trace` + `test_trace` + `test_session` xanh bằng baseline.
- Gate sau tách serve.py (TASK-008): `test_serve_config.py` (614 dòng, phủ
  `_config_values`/`_merge_saved_key`/provider test) + kiểm tra thủ công
  `GET /api/config` trả `apiKey: ""`.
- Gate sau tách bridge.py (TASK-012): `test_serve_chat.py` (2158 dòng, gate
  chính stream/turn) + `test_serve_errors.py` (mapping `public_turn_error` —
  không lộ stack/path/secret) + `test_turn_stream.py` + `test_turn_status.py`.
- Gate cuối (TASK-016): full `uv run pytest -q` + import smoke không cycle +
  `git diff --check` sạch. Không sửa test để pass.

## Assumptions

1. `thyca/webui/` ngoài scope; `default_webui()` (`serve.py:120-121`) và cơ chế
   `_safe_file` chặn path-traversal (`serve.py:589-600`) giữ nguyên behavior.
2. Bind loopback là bất biến: check `host not in LOOPBACK` (`serve.py:154-155`)
   giữ nguyên trong `server.py`, không thêm host mới.
3. Thông điệp lỗi public giữ nguyên chuỗi (vd `chat unavailable`,
   `session not found`): bất kỳ thay đổi contract nào cũng phải ghi vào plan
   đã duyệt trước khi sửa test.
4. `turn_state.py` thuộc M7 (dù `chat_app.py` dùng nó): M8 chỉ consumer một
   chiều; tranh chấp biên do orchestrator quyết ở GOAL-002 plan tổng.
5. Không thêm dependency, route, hay field API mới trong refactor này.
