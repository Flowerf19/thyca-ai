---
status: done
created: 2026-09-22
last_updated: 2026-09-22
---

# M8 — app (chat orchestration + CLI + onboarding + wire types)

## Summary

Tách 5 file top-level `thyca/*.py` thành `thyca/app/` mới, trừ `protocol.py`
chuyển sang `thyca/core/` mới. `protocol.py` được ~25 module thuộc
agent/llm/sessions/tools/trace/session_wire/turn_state import — đặt nó trong
`app/` sẽ đảo tầng phụ thuộc (sessions/agent/llm/tools → app), nên `core/` là
đích đúng. `chat_app.py` (537 dòng, vượt ngưỡng 400) tách trước tiên thành 4
file theo SRP; `cli.py` (295), `onboarding.py` (301), `chat_ui.py` (45),
`protocol.py` (196) giữ nguyên file, chỉ refactor wiring nhẹ. Mọi import ngoài
(runtime + tests + `__main__.py` + `pyproject.toml` entry `thyca.cli:main`)
đổi sang path mới dưới dạng shim/re-export có thời hạn do GOAL-002 (layout
agent) quyết định. Không đổi behavior, không thêm dependency.

Rủi ro cycle lớn nhất: `chat_app.py:39` import `TurnHub, TurnState` từ
`turn_state.py` — file này plan tổng giao cho M7 (`thyca/serve/`) — trong khi
M7 `serve.py:27` import `ChatApp` từ M8. Nếu cả hai chiều thành import package
chéo sẽ tạo cycle `thyca.serve ↔ thyca.app`. Plan này đề xuất chuyển
`turn_state.py` sang `thyca/core/` (cả app và serve cùng phụ thuộc core) và
giữ lazy import trong `cli.py:213-215`; quyết định cuối do orchestrator duyệt
cùng M7.

## Tasks

### GOAL-001: Physical move — file lẻ vào `thyca/app/` + `thyca/core/`

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | `git mv thyca/chat_app.py thyca/app/chat_app.py` (tạm nguyên 537 dòng, tách ở GOAL-002) | | |
| TASK-002 | `git mv thyca/chat_ui.py thyca/app/chat_ui.py`, `git mv thyca/cli.py thyca/app/cli.py`, `git mv thyca/onboarding.py thyca/app/onboarding.py` | | |
| TASK-003 | `git mv thyca/protocol.py thyca/core/protocol.py` + tạo `thyca/app/__init__.py`, `thyca/core/__init__.py` (re-export `Message`, `ToolCall`, `ToolResult`, `utc_now_ts`, `RESULT_CAP_BYTES`, `META_CAP_BYTES` để import cũ `thyca.protocol` vẫn chạy qua shim `thyca/protocol.py` do layout agent sở hữu) | | |
| TASK-004 | Sửa imports runtime: `thyca/__main__.py:3` (`from thyca.cli import main` → `from thyca.app.cli import main`), `thyca/cli.py` nội bộ (`thyca.chat_ui` → `thyca.app.chat_ui`, lazy `thyca.chat_app` → `thyca.app.chat_app` ở `cli.py:213`), `serve.py:27,30`, `bridge.py:24`, `trace_api.py:19` sang path mới; cập nhật `pyproject.toml:29` entry `thyca.cli:main` → `thyca.app.cli:main` | | |
| TASK-005 | Sửa imports tests M8: `tests/test_chat_app.py:16`, `tests/test_chat_ui.py:5`, `tests/test_cli.py:8`, `tests/test_onboarding.py:11-19`, `tests/test_serve_chat.py:18-19`, `tests/test_serve_errors.py:11`, `tests/test_onboarding.py:100` + `tests/test_serve_config.py:266` (`monkeypatch thyca.onboarding.urlopen` → `thyca.app.onboarding.urlopen`); protocol importers ngoài M8 (agent/llm/sessions/tools/trace tests) đổi `thyca.protocol` → `thyca.core.protocol` hoặc qua shim thống nhất của layout agent | | |

### GOAL-002: Tách `chat_app.py` oversize (537 dòng) — làm đầu tiên trong module

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-006 | Tách leaf không phụ thuộc nội bộ trước: `InvalidTurnOption`, `overlay_turn_cfg` (`chat_app.py:45,57-64`), `_clean_turn_text` + `TEXT_MAX` (`chat_app.py:43,66-77`) sang `thyca/app/turn_options.py` mới | | |
| TASK-007 | Tách cầu thread/asyncio: `_TurnJob`, `_LoopTurns`, `_CANCEL_WAIT_S` (`chat_app.py:44,79-149`) sang `thyca/app/loop_turns.py` mới; `TurnCancelled` ở lại file định nghĩa gần `ChatApp.cancel` hoặc sang `loop_turns.py` cùng bridge (ghi lý do trong code) | | |
| TASK-008 | Tách sidecar đặt tên: `_name_if_needed`, `_record_naming`, `session_title` (`chat_app.py:151-210,535-537`) sang `thyca/app/naming.py` mới | | |
| TASK-009 | `thyca/app/chat_app.py` còn lại chỉ `ChatApp` + `SessionIdle` + `_detail`; verify mỗi bước tách bằng focused tests GOAL-004, không sửa logic | | |

Target layout cuối (chính xác):

- `thyca/app/__init__.py` (mới, re-export `ChatApp`, `Cli`, `ChatUi`, onboarding API công khai)
- `thyca/app/chat_app.py` (từ `thyca/chat_app.py`, sau tách chỉ còn `ChatApp`)
- `thyca/app/turn_options.py` (mới, từ `chat_app.py:43-77`)
- `thyca/app/loop_turns.py` (mới, từ `chat_app.py:44,79-149`)
- `thyca/app/naming.py` (mới, từ `chat_app.py:151-210,535-537`)
- `thyca/app/cli.py` (từ `thyca/cli.py`, giữ nguyên biên file)
- `thyca/app/chat_ui.py` (từ `thyca/chat_ui.py`, giữ nguyên)
- `thyca/app/onboarding.py` (từ `thyca/onboarding.py`, giữ nguyên biên file)
- `thyca/core/__init__.py` + `thyca/core/protocol.py` (từ `thyca/protocol.py`, giữ nguyên)
- `thyca/protocol.py` shim (do layout agent quyết giữ/xóa, re-export từ `thyca.core.protocol`)

### GOAL-003: SOLID refactor nhẹ trong biên module (SRP là chính)

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-010 | SRP — `ChatApp.__init__` (`chat_app.py:212-290`): tách assembly (ToolRegistry + file/memory/MCP tools + schema + `Act`) thành factory/helper `_build_toolchain(root, cfg)` riêng; `ChatApp` chỉ giữ orchestration, behavior giữ nguyên | | |
| TASK-011 | SRP — `Cli._run` (`cli.py:133-211`): assembly registry/MCP/memory trong CLI trùng lặp với `ChatApp.__init__`; trích helper dùng chung trong `thyca/app/` (đặt cạnh toolchain của TASK-010) để CLI one-shot/REPL và ChatApp dùng một đường build, không copy logic | | |
| TASK-012 | DIP — `ChatApp._run_turn` (`chat_app.py:380-430`) và `Cli._run` (`cli.py:186-200`) tự `ConnectFactory.create(...)` concrete bên trong; cho phép inject `LLMPort` (đã có `connect` param ở `ChatApp.__init__` và `Cli.__init__`) đi qua toàn đường turn, factory chỉ là default — tests hiện dùng FakeLLM/ScriptedLLM (`test_serve_chat.py:29-56`) chứng minh seam đã đủ, không thêm abstraction mới | | |
| TASK-013 | SRP — `onboarding.py`: giữ một file (301 dòng dưới ngưỡng) nhưng phân biên rõ probe (`validate_provider`, `test_chat`, `test_responses_chat`, `test_provider_api`) vs mutation (`apply_provider` ở cuối file, import `replace` cục bộ trong hàm); tách `apply_provider` sang module config là việc của M3 nếu orchestrator duyệt — M8 không tự chuyển file sang module khác | | |
| TASK-014 | Giữ lazy import `cli.py:212-215` (`from thyca.chat_app import ChatApp`, `from thyca.serve ...` trong `_serve`) dưới dạng lazy sang path mới; cấm import top-level `thyca.serve`/`thyca.bridge` trong `thyca/app/*` để giữ chiều phụ thuộc serve→app một chiều | | |

SOLID findings (có evidence):

- SRP — `ChatApp` (`chat_app.py:212-533`) kiêm session CRUD (`list_payload`, `create`, `get_payload`, `rename_session`, `delete_session`), turn orchestration + claim/release (`turn`, `cancel`, `_run_turn`), quản lý thread/event-loop (`_run_loop`, `_submit`, `shutdown`), MCP spawn + registry assembly (`__init__:267-290`), naming sidecar, config reload mỗi turn (`_current_cfg`), payload shaping (`_detail`) — 7 trách nhiệm trong 1 class/file 537 dòng.
- SRP — `_LoopTurns` (`chat_app.py:89-149`) trộn quản lý `asyncio.Task`, `concurrent.futures.Future`, `threading.Lock` và `call_soon_threadsafe` — cầu đồng bộ/hóa đúng nhưng phải sống riêng file để test độc lập.
- SRP — `Cli` (`cli.py:48-295`) kiêm parse/validate flags (`main`: mutually-exclusive `--continue/--session`, `--serve` combos, `--port` range), REPL + one-shot (`_repl`, `_oneshot`), serve dispatch (`_serve`), và build toàn bộ agent toolchain (`_run`) — 4 trách nhiệm.
- SRP — `onboarding.py` trộn network probe (`validate_provider`, `test_chat`, `test_responses_chat`) với config mutation (`apply_provider`, cuối file); evidence: `apply_provider` là hàm duy nhất nhận `Config` và trả `Config` mới, phụ thuộc M3.
- DIP — `ChatApp.__init__` và `Cli._run` `new` trực tiếp `SessionManager`, `ActiveMemory`, `ToolRegistry`, `MCPManager`, `ConnectFactory`, `Act/Assemble/Think/Observe` (evidence `chat_app.py:222-290`, `cli.py:150-200`); seam inject hiện chỉ có `connect` (`ChatApp.__init__` param, `Cli.__init__` param) — mở rộng seam đó, không vẽ interface mới.
- OCP (giữ, không đụng) — `_wire_retry_events` (`chat_app.py` ~`_wire_retry_events`) dùng duck-typing `set_retry_hook` nên provider mới không phải sửa `ChatApp`; ghi nhận để team sau không "chuẩn hóa" thành isinstance chain.

Không phát hiện vi phạm ISP cụ thể: `LLMPort` (`agent/think.py`, dùng ở `chat_app.py:20`, `cli.py:13`) đã là interface hẹp (`chat` + optional `aclose`/`set_retry_hook` duck-typed); không tách thêm.

### GOAL-004: Verify + gate

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-015 | Chạy focused tests: `tests/test_chat_app.py`, `tests/test_chat_ui.py`, `tests/test_cli.py`, `tests/test_onboarding.py`, `tests/test_serve_chat.py`, `tests/test_serve_errors.py`, `tests/test_serve_config.py`, `tests/test_serve_daemon.py`, `tests/test_serve_memory_stats.py` — xanh trừ baseline đã biết | | |
| TASK-016 | Chạy full `uv run pytest -q` (so baseline: chỉ fail đã biết `test_cli.py::test_debug_prints_prompt_flags` `tools=7` vs thực tế) + `python -c "import thyca.app.cli, thyca.app.chat_app, thyca.core.protocol"` không circular + `git diff --check` sạch | | |

## Test Plan

- Baseline giữ nguyên: `tests/test_cli.py::test_debug_prints_prompt_flags` fail đã biết (`tools=7` trong assert vs số tool thực tế) — không sửa số này, không sửa test để pass (AGENT_RULES).
- Focused gate (chạy sau mỗi GOAL): `test_chat_app.py` (turn + naming events), `test_chat_ui.py` (render không màu/có màu), `test_cli.py` (flags, REPL, oneshot), `test_onboarding.py` + phần probe của `test_serve_config.py:266` (mock `urlopen`, server local — không gọi provider thật, không lộ key), `test_serve_chat.py` (suite HTTP 2158 dòng, dùng FakeLLM/ScriptedLLM), `test_serve_errors.py`, `test_serve_daemon.py` + `test_serve_memory_stats.py` (đường `Cli._serve`).
- Contract cần giữ: entry `thyca` (`pyproject.toml:29`, `thyca/__main__.py:3`) vẫn `main(argv) -> int`; mã exit CLI (`2` lỗi flags, `1` lỗi config/LLM/session, `0` ok); `ChatApp.turn/create/list_payload/get_payload/rename/delete/cancel/running_sessions/follow_hub` payload shape cho serve; `validate_provider` trả ids đã sort, `test_chat`/`test_responses_chat` trả `{"model", "latency_ms"}`; message `ProviderProbeError` không chứa key (có tests key-leak trong `test_onboarding.py`).
- Không broad rewrite: tests của agent/llm/sessions/tools/memory chỉ chạy để phát hiện cycle do move `protocol.py`, không sửa logic chúng.

## Assumptions

1. `protocol.py → thyca/core/` (mới) thay vì `thyca/app/`: ~25 importers ngoài M8 gồm `agent/stage.py:6`, `act.py:8`, `think.py:7`, `assemble.py:5`, `observe.py:3`, `llm/llm_base.py:8`, `openai_chat.py:10`, `openai_parse.py:9`, `openai_responses.py:15`, `streaming.py:11`, `responses_parse.py:10`, `sessions/models.py:6`, `store.py:9`, `manager.py:11`, `title.py:9`, `compaction.py:5`, `ask_remember.py:6`, `tools/registry.py:8`, `mcp.py:17`, `task_store.py:13`, `background.py:14`, `skills.py:19`, `trace.py:7`, `trace_api.py:15`, `session_wire.py:18`, `turn_state.py:17` — đặt wire types dưới app/ sẽ bắt các tầng nền import tầng trên (layer inversion); `core/` không import bất kỳ module `thyca.*` nào khác nên không thể cycle.
2. `turn_state.py` thuộc M7 theo plan tổng nhưng `chat_app.py:39` phụ thuộc nó trong khi `serve.py:27` phụ thuộc `ChatApp` — đề xuất chuyển `turn_state.py` sang `thyca/core/` để cả M7 và M8 cùng phụ thuộc core; nếu orchestrator giữ ở `thyca/serve/`, M8 import `thyca.serve.turn_state` trực tiếp và M7 bị cấm tuyệt đối import `thyca.app` ở top-level (chỉ lazy trong hàm) — orchestrator chốt cùng Team-serve trước GOAL-002.
3. `session_wire.py` thuộc M5; `chat_app.py:27` import `session_detail, session_summary` là chiều app→sessions hợp lệ, giữ nguyên; M8 không đụng payload shape của M5.
4. Shim `thyca/protocol.py` (re-export) thuộc về commit layout GOAL-002 của orchestrator — M8 coding không tự ý xóa file shim nếu nó tồn tại, tránh break importers của module khác; toàn bộ import mới trong M8 dùng `thyca.core.protocol` / `thyca.app.*`.
5. Chỉ `chat_app.py` vượt ngưỡng 400 dòng nên chỉ nó bị tách; `cli.py`/`onboarding.py` dưới ngưỡng giữ biên file, refactor giới hạn trong trích helper (TASK-010–012).
6. Onboarding probe tests dùng HTTP server local và mock `urlopen` — team M8 không gọi provider thật trong test; mọi message lỗi probe đã là tiếng Việt có sẵn, giữ nguyên wording để tests key-leak/message khớp.

## Close-out (2026-09-22, orchestrator)
All module tasks landed and verified: branch refactor/M8-app commit c3f734d, test 719/719, review approve (toolchain.py accepted; merge overlap with M1 resolved M8-side). Merged into refactor/backend-solid, full suite 719 passed, plan status done.
