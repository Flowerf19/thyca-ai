---
status: in-progress
created: 2026-09-22
last_updated: 2026-09-22
---

# M5 sessions — module plan

## Summary

Module `thyca/sessions/` đã là hình mẫu SOLID của repo (AGENT_RULES cố định 4 class
`Session` / `SessionStore` / `SessionCompactor` / `SessionManager`): không file oversize
(file lớn nhất `store.py` 295 dòng), không logic sai chỗ giữa 4 class, không circular import.
Phạm vi M5 vì vậy nhỏ và cơ học: gom `thyca/session_wire.py` vào `sessions/` dưới tên
`wire.py`, sửa 2 import consumers, 2 micro-cleanup DRY có evidence, còn lại giữ nguyên
behavior và giữ nguyên public API (`thyca/sessions/__init__.py` không đổi).

- Tên file đích cho `session_wire.py`: **`thyca/sessions/wire.py`** — giữ từ "wire" đúng
  với docstring gốc ("Wire contract for one chat session"), ngắn, khớp convention module.
- Không tách file oversize: không file nào vượt 400 dòng (xem bảng Target layout).
- Không shim `thyca/session.py` hay `thyca/session_wire.py` sau move (theo precedent AGENT_RULES);
  2 consumers cập nhật import trong cùng commit layout.
- Move vật lý do layout agent (GOAL-002 plan tổng) thực hiện; team M5 verify parity rồi mới
  làm GOAL-002 của plan này.

## Tasks

Target layout (kích thước đo ngày 2026-09-22, nhánh `refactor/backend-solid`):

| File nguồn | File đích | Dòng | Cách move |
|------------|-----------|------|-----------|
| `thyca/session_wire.py` | `thyca/sessions/wire.py` | 137 | `git mv thyca/session_wire.py thyca/sessions/wire.py` |
| `thyca/sessions/__init__.py` | giữ nguyên | 19 | không move; `__all__` không đổi |
| `thyca/sessions/models.py` | giữ nguyên | 18 | không move (`Session`) |
| `thyca/sessions/store.py` | giữ nguyên | 295 | không move, không tách |
| `thyca/sessions/manager.py` | giữ nguyên | 285 | không move, không tách |
| `thyca/sessions/compaction.py` | giữ nguyên | 88 | không move |
| `thyca/sessions/title.py` | giữ nguyên | 171 | không move |
| `thyca/sessions/ask_remember.py` | giữ nguyên | 48 | không move |
| `thyca/sessions/errors.py` | giữ nguyên | 35 | không move |

Import updates đi kèm move (layout agent sửa trong cùng commit, không refactor logic):

- `thyca/chat_app.py:27`: `from thyca.session_wire import session_detail, session_summary`
  → `from thyca.sessions.wire import session_detail, session_summary`.
- `thyca/bridge.py:27`: `from thyca.session_wire import delete_error, rename_error`
  → `from thyca.sessions.wire import delete_error, rename_error`.
- `thyca/sessions/wire.py:19-27`: 3 import tuyệt đối (`from thyca.sessions import ...`,
  `from thyca.sessions.ask_remember import ask_remember`,
  `from thyca.sessions.title import display_title`) → relative
  (`from . import ...`, `from .ask_remember import ...`, `from .title import ...`).
- `scripts/retitle_sessions.py:11-12` không đổi (chỉ dùng `thyca.sessions`, không đụng wire).

Thứ tự tách file oversize: không có. File lớn nhất `store.py` (295 dòng) và `manager.py`
(285 dòng) đều dưới ngưỡng 400 của plan tổng và mỗi file đúng một trách nhiệm
(I/O bền vững / orchestration + lock), nên giữ nguyên — tách thêm là abstraction thừa.

### GOAL-001: Layout + parity sau move

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | Verify `thyca/sessions/wire.py` tồn tại, `thyca/session_wire.py` đã xóa, 3 import updates ở `chat_app.py:27`, `bridge.py:27`, `wire.py:19-27` đúng như bảng trên; `grep -rn "session_wire" thyca/ tests/ scripts/` về 0 | | |
| TASK-002 | Verify public API parity: `thyca/sessions/__init__.py` `__all__` 10 names không đổi; `python -c "from thyca.sessions.wire import session_title, updated_at, session_summary, session_detail, message_dict, tool_call_dict, rename_error, delete_error"` pass | | |
| TASK-003 | Chạy focused tests arrival-gate: `uv run pytest -q tests/test_session.py tests/test_ask_remember.py tests/test_trace.py` xanh trước khi đụng code | | |

### GOAL-002: Micro-cleanup có evidence (giữ 4 class, không đổi behavior)

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-004 | `store.py`: `latest()` (dòng 160-178) dựng lại harvest candidates đang trùng với `list_paths()` (dòng 149-158, glob + filter symlink/`_ID_RE` + sort mtime); refactor `latest()` tái dùng `list_paths()` rồi scan từng path, giữ nguyên semantics "bỏ qua file corrupt, lấy mới nhất hợp lệ" | | |
| TASK-005 | `manager.py`: 6 chỗ raise `SessionError("no current session — ...")` giống hệt nhau (dòng 45, 120, 134, 178, 203, 243) trích thành helper `_current_locked()` (assume lock đã giữ, ghi rõ trong docstring), giữ nguyên message text để tests match không vỡ | | |
| TASK-006 | Guard 4-class boundary: diff của GOAL-002 không di chuyển logic giữa `models.py` (`Session` pure data), `store.py` (I/O), `compaction.py` (pure policy, không I/O), `manager.py` (orchestration + lock); `git diff --stat` chỉ chạm `store.py`, `manager.py` | | |

### GOAL-003: Cycle check + full gate

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-007 | Chạy cycle check: `python -c "import thyca.sessions.wire, thyca.chat_app, thyca.bridge, thyca.cli, thyca.serve, thyca.turn_state, thyca.trace_api, thyca.agent.loop, thyca.agent.observe"` pass; `grep -rn "from thyca.chat_app\|from thyca.bridge\|from thyca.serve\|from thyca.turn_state\|from thyca.cli" thyca/sessions/` về 0 | | |
| TASK-008 | Full gate: `uv run pytest -q` xanh bằng hoặc hơn baseline, `git diff --check` sạch; `test_debug_prints_prompt_flags` giữ nguyên trạng thái baseline (không sửa số `tools=`) | | |

## Test Plan

- Arrival gate (GOAL-001): `uv run pytest -q tests/test_session.py tests/test_ask_remember.py
  tests/test_trace.py` — `test_session.py` (~800 dòng) là spec chính của 4 class,
  `test_ask_remember.py` pin policy read-only, `test_trace.py` pin `Session` dùng bởi trace.
- Sau TASK-004/005: chạy lại 3 file trên + consumer tests của wire payload:
  `tests/test_serve_chat.py` (`test_session_detail_tags_skill_loads`,
  `test_session_summary_counts_turns_the_way_trace_does`), `tests/test_chat_app.py`,
  `tests/test_serve_errors.py`, `tests/test_chat_meter.py::test_session_detail_carries_meta_for_meter`.
- Merge gate (TASK-008): full `uv run pytest -q` + import check TASK-007 + `git diff --check`.
- Không sửa test để pass; baseline fail đã biết `test_debug_prints_prompt_flags` không thuộc M5.

## Assumptions

1. Move vật lý + sửa import consumers do layout agent (GOAL-002 plan tổng) làm; nếu layout
   chưa xong khi team start, team được tự chạy `git mv` theo đúng bảng Target layout.
2. SOLID findings SRP-chính: 4-class boundary đã đúng — `models.py:7-18` (`Session` dataclass
   thuần data), `store.py:17` (`SessionStore` "Durable JSONL I/O. No compaction policy"),
   `compaction.py:23` (`SessionCompactor` "Turn-safe tail policy. No I/O"),
   `manager.py:20` (`SessionManager` "Orchestrate store + compactor behind a single-process lock").
   Chỉ 2 điểm DRY trong cùng-class (TASK-004/005), không di chuyển trách nhiệm liên class.
3. DIP: 3 import concrete xuyên module được chấp nhận giữ nguyên vì ổn định và không cycle —
   `manager.py:10` (`thyca.config`: `LimitsCfg`, `DEFAULT_TIMELINE_TIMEZONE`),
   `title.py:8` (`thyca.llm.llm_base`: `LLMError`, chỉ dùng trong `except` của `retitle_missing`),
   `sessions/wire.py:16-17` (`thyca.agent.skill_event`: `skill_name_for_call`,
   `thyca.config`: `Config` chỉ làm type annotation của `session_detail`).
4. ISP/OCP: không tìm thấy evidence vi phạm trong module — public API là hàm thuần và
   exception classes nhỏ, không interface thừa, không nhánh `isinstance` đòi mở rộng.
5. Cycle risks đã verify một chiều (grep ngày 2026-09-22 không có reverse import từ
   `thyca/sessions/` hay `session_wire.py` về `chat_app`/`bridge`/`serve`/`turn_state`/`cli`):
   M5 phụ thuộc M1 (`agent.skill_event` qua wire), M2 (`llm_base.LLMError` qua `title.py`),
   M3 (`config` qua `manager.py` + wire), protocol (`Message`/`ToolCall` — thuộc M8,
   nếu M8 chuyển `protocol.py` sang `thyca/core/` thì M5 cập nhật import cơ học theo).
   Consumers của M5: M8 (`chat_app.py:27-30`, `cli.py:24`), M7 (`bridge.py:27-28`,
   `serve.py:37`, `turn_state.py:18`, `trace_api.py:20-21`), M1 (`agent/loop.py:4`,
   `agent/observe.py:4`), `trace.py:8`, `scripts/retitle_sessions.py:11-12`.
   Cách tránh: wire không import ngược app/serve layers; `title.py:13-14` import
   `SessionManager` giữ `TYPE_CHECKING`-only; TASK-007 pin bằng import check.
6. Không thêm dependency, file mới (ngoài `wire.py` từ move), hay API mới; behavior giữ nguyên.
