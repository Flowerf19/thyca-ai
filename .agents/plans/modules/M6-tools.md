---
status: in-progress
created: 2026-09-22
last_updated: 2026-09-22
---

# M6 tools — module plan

## Summary

Refactor `thyca/tools/` + `thyca/skills.py` trong scope M6, không đổi behavior. Quyết định layout:
`skills.py` **không** gom vào `tools/` mà thành package mới `thyca/skills/` — vì `thyca/tools/memory.py`
đã import `thyca.memory.*`, trong khi `thyca/memory/active.py:14` import `SkillStore`; nhét skills
dưới `tools/` sẽ tạo cycle package M4↔M6. Không file nào vượt 400 dòng nên không tách file oversize;
`memory.py` (353 dòng) giữ nguyên facade với lý do ghi rõ. Giữ nguyên memory tools contract
(7 tool `memory_*` + schema/parameters) và MCP stdio behavior.

## Tasks

### GOAL-001: Physical layout (mechanical, sau duyệt)

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | `git mv thyca/skills.py thyca/skills/store.py` + tạo `thyca/skills/__init__.py` re-export (`SkillStore`, `SkillMeta`, `NAME_MAX`, `DESCRIPTION_MAX`, `INDEX_DESCRIPTION_CHARS`, `_NAME_RE`, `_PACKAGED_SKILLS`) để mọi consumer `from thyca.skills import ...` chạy unchanged | | |
| TASK-002 | `git mv thyca/skills_templates thyca/skills/skills_templates` + cập nhật `_PACKAGED_SKILLS` trong `skills/store.py` (`Path(__file__).parent / "skills_templates"`); verify `pyproject.toml` (`packages = ["thyca"]`) vẫn ship templates | | |
| TASK-003 | Sửa imports do move: `thyca/memory/active.py:14`, `thyca/agent/skill_event.py:24`, `tests/test_skills.py:10` — giữ nguyên tên import (`from thyca.skills import ...`), chỉ đổi file nguồn; không đụng logic | | |
| TASK-004 | Verify sau move: `uv run pytest -q` xanh bằng baseline, `python -c "import thyca.skills, thyca.tools, thyca.memory.active, thyca.agent.skill_event"` không circular, `git diff --check` sạch | | |

Target layout cuối (không move gì thêm trong `tools/` — đã đúng vị trí):

| Nguồn | Đích | Cách |
|-------|------|------|
| `thyca/skills.py` | `thyca/skills/store.py` (+ `thyca/skills/__init__.py` mới, chỉ re-export) | `git mv` + 1 file mới |
| `thyca/skills_templates/` | `thyca/skills/skills_templates/` | `git mv` |
| `thyca/tools/registry.py` | giữ nguyên | — |
| `thyca/tools/task_store.py` | giữ nguyên | — |
| `thyca/tools/path_guard.py` | giữ nguyên | — |
| `thyca/tools/memory.py` | giữ nguyên | — |
| `thyca/tools/memory_tools.py` | giữ nguyên | — |
| `thyca/tools/mcp.py` | giữ nguyên | — |
| `thyca/tools/builtin/*.py` | giữ nguyên | — |

### GOAL-002: SOLID refactor (nhỏ, trong module)

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-005 | `tools/memory.py`: trích `_promote_in_order_span` + `_phrase_key` (cuối file, ~dòng 320-353, ranking policy dùng `chunker.normalize`) sang `tools/memory_rank.py` mới, `memory.py` import lại; facade và signature public giữ nguyên | | |
| TASK-006 | `tools/mcp.py`: chuyển `_McpSession` Protocol (dòng 99-107) + `ProcessFactory` (dòng 154) lên gần đầu file sau `CALL_TIMEOUT`, gom pure helpers (`merge_env`, `resolve_command`, `model_name`, `join_text_blocks`, `_is_object_schema`) thành khối liền mạch; không đổi logic, không đổi public API (`MCPManager`, `MCPProcess`, `StartupDiagnostic`) | | |
| TASK-007 | `tools/builtin/background.py`: trích hằng số rendezvous (`_READ_WAIT_MAX_S`, `_DRAIN_GRACE_S`, `_EXIT_POLL_S`) + `parse_timeout`-tương đương nếu trùng với `bash.py` — chỉ dedup khi chữ ký giống hệt, ngược lại giữ duplicate có comment lý do | | |
| TASK-008 | Chạy focused tests M6 (liệt kê ở Test Plan) + full `uv run pytest -q`; mọi fail mới phải có evidence file:line, không sửa test để pass | | |

Thứ tự tách file oversize: không có file >400 dòng trong module
(`memory.py` 353, `mcp.py` 293, `memory_tools.py` 250, `background.py` 204,
`registry.py` 181, `task_store.py` 143, `bash.py` 135, `skills.py` 140 — đo bằng `wc -l`
2026-09-22). `memory.py` sát ngưỡng nhưng giữ nguyên facade vì 7 method map 1:1 với
7 memory tool contract; tách sẽ phân tán contract đã được AGENT_RULES chốt. Chỉ trích
2 hàm ranking thuần (TASK-005) ra `memory_rank.py`.

SOLID findings (có evidence):

- SRP (làm — TASK-005): ranking policy `_promote_in_order_span`/`_phrase_key`
  (`tools/memory.py` cuối file) không thuộc trách nhiệm facade (remember/forget/reinforce/get);
  trích sang `tools/memory_rank.py`.
- SRP (giữ, compliant): `registry.py` — `ToolRegistry` (dispatch/lock/timeout) tách khỏi
  `ToolSpec` dataclass (dòng 19-33) và pure helpers `_result`/`_validate_args` (cuối file);
  `task_store.py` — `TaskStore` + `_TrackedTask` + `tool_read_spec` cùng một bounded context
  escalation (143 dòng), không tách.
- SRP (giữ, compliant): `mcp.py` — `MCPProcess` (transport stdio) tách khỏi `MCPManager`
  (lifecycle `spawn_all` dòng 241/`tool_specs`/`shutdown`); validation `_tool_error`/`_spec_for`
  đã là hàm thuần riêng. TASK-006 chỉ sắp xếp lại, không tách class.
- DIP (giữ, compliant): `MemoryFacade.__init__` (`tools/memory.py:52-64`) đã nhận
  `archive`/`writer` inject; `MCPManager.__init__` (`tools/mcp.py:238-240`) đã nhận
  `process_factory` inject (`ProcessFactory`, dòng 154); `registry.py:11-12` dùng
  `TYPE_CHECKING` import `TaskStore` để tránh runtime cycle — giữ cả ba pattern.
- DIP (ghi nhận, không đổi): `tools/mcp.py:16` `from thyca.config import McpServerCfg`
  phụ thuộc concrete type của M3 và đọc `cfg.command/args/env`; đây là value object ổn định,
  không bọc Protocol trừ khi M3 đổi shape — phối hợp qua re-export (xem cycle risks).
- OCP/ISP (compliant, không đổi): mở rộng tool qua `registry.register(ToolSpec)`
  (`chat_app.py:236-237`, `cli.py:154-156`) không sửa registry; `_McpSession`
  (`tools/mcp.py:99-107`) là Protocol hẹp đúng ISP; không phát hiện violation.

### GOAL-003: Contract + review gate

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-009 | Đối chiếu memory tools contract: 7 tên `memory_remember|search|recent|get|forget|reinforce|update`, parameters/required trong `memory_tools.py:35-250` giữ nguyên; `bind_chat_session`/`reset_chat_session` ContextVar dùng bởi `chat_app.py:35` giữ nguyên chữ ký | | |
| TASK-010 | Đối chiếu builtin contract: `register_file_tools` (`builtin/__init__.py`) đăng ký đủ `read/write/edit/bash/bash_read`; `tool_read_spec` (`task_store.py:105`, `escalates=True`) và `bash` (`bash.py`, `escalates=True`) giữ flag escalation; `PathGuard.deny_write` rule (chặn `MEMORY.md`, `memory.sqlite*`, `sessions/`, `memory/`) giữ nguyên | | |
| TASK-011 | Review độc lập diff M6: đúng file layout trên, không behavior change, `git diff --check` sạch, rồi báo orchestrator merge | | |

## Test Plan

Focused tests (chạy trước, phải xanh 100%):

- `tests/test_tool_registry.py`, `tests/test_tool_task_store.py`
- `tests/test_tool_background.py`, `tests/test_tool_bash.py`, `tests/test_tool_files.py`
- `tests/test_memory_tools.py`, `tests/test_skills.py`
- `tests/test_mcp.py`, `tests/test_skill_event.py`

Gate merge:

- Full `uv run pytest -q` xanh bằng hoặc hơn baseline plan tổng; known failure duy nhất được
  chấp nhận là `tests/test_cli.py::test_debug_prints_prompt_flags` (`tools=7` vs thực tế `tools=13`
  — baseline đã biết, AGENT_RULES cấm sửa số này).
- Không circular import:
  `python -c "import thyca.skills, thyca.tools, thyca.memory.active, thyca.agent.skill_event, thyca.chat_app, thyca.cli"`.
- `git diff --check` sạch. Không sửa test để pass trừ contract đổi đã ghi trong plan này và được duyệt.

## Assumptions

1. `thyca/skills/` là package mới thuộc scope M6 theo quyền team quyết trong plan tổng
   (gom `skills.py` vào `tools/` hoặc `skills/` mới); orchestrator duyệt layout này ở GOAL-002 tổng.
2. `thyca.protocol` (`RESULT_CAP_BYTES`, `ToolCall`, `ToolResult` — dùng tại `registry.py:8`,
   `builtin/background.py:14`, `skills.py` qua `RESULT_CAP_BYTES`) do M8 sở hữu; M6 giữ nguyên
   `from thyca.protocol import ...`, M8 đảm bảo re-export nếu move sang `core/`.
3. `McpServerCfg` (`tools/mcp.py:16`) do M3 sở hữu; M6 giữ nguyên import path, M3 đảm bảo
   re-export từ `thyca.config` nếu gộp `config_schema.py`.
4. `thyca.agent.skill_event` (M1) chỉ import grammar skills (`_NAME_RE`, `NAME_MAX` —
   xem `skill_event.py:24,68`); `thyca/skills/__init__.py` giữ hai tên này export vĩnh viễn.
5. `MemoryFacade` public API do M4/M7 dùng (`serve_memory.py:5`, `serve.py:38`, `cli.py:27`,
   `chat_app.py:34`) — TASK-005/009 không đổi chữ ký hay semantics; L2 hybrid v1 decision
   không bị đụng (facade là caller, không phải contract owner).
6. Cycle risks và cách tránh:
   - M4↔M6: `tools/memory.py:8-31` import 6 submodule `thyca.memory.*`; chiều ngược
     `memory/active.py:14` chỉ import `thyca.skills` (leaf, chỉ phụ thuộc `yaml`+stdlib+
     `protocol`) — không import `thyca.tools` nên không cycle. Quy tắc: `thyca/skills/`
     không được import `thyca.tools` hay `thyca.memory` bao giờ.
   - M6→M3: chỉ via type `McpServerCfg`; nếu M3 move, M6 đổi đúng 1 dòng import.
   - M6→M8 (`protocol`): chỉ via wire types; nếu M8 move sang `core/`, M8 giữ shim.
   - `builtin/bash.py` ↔ `builtin/background.py`: `background.py:15` import
     `kill_process_group, select_shell` từ `bash.py`, còn `bash.py` chỉ `TYPE_CHECKING`-import
     `BackgroundProcs` — giữ hướng import một chiều này, không cho `bash.py` import runtime
     từ `background.py`.
