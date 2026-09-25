---
status: done
created: 2026-09-22
last_updated: 2026-09-22
---

# M1 — agent (`thyca/agent/`) module plan

## Summary

Module M1 là vòng loop 4 pha `assemble → think → act → observe` + `Stage` (shared workspace
một lượt user) + events (`TurnEvent`, `ThinkingDelta`, `ContentDelta`, `skill_event`).
Module đã nằm đúng thư mục đích `thyca/agent/` (12 file, 721 dòng, file lớn nhất
`observe.py` 150 dòng — không file oversize). Công việc của team M1: refactor in-place theo
SRP (tách helper khỏi `AgentLoop.run` và `Observe`), sửa 2 vi phạm DIP cụ thể
(`Assemble` tự dựng `PromptManager`, `skill_event` import private của M6), giữ nguyên
behavior và toàn bộ import surface cho consumers (M5/M7/M8 + tests). Không move file,
không broad rewrite ngoài module.

## Tasks

### GOAL-001: Giữ layout + refactor SRP/DIP in-place

Target layout: **không `git mv` file nào** — module đã ở thư mục đích. Toàn bộ 12 file giữ
nguyên path:

| File hiện tại | File đích | Ghi chú |
|---|---|---|
| `thyca/agent/__init__.py` (0 dòng) | giữ nguyên | giữ rỗng; consumers import deep path (`thyca.agent.act`, ...) nên không thêm re-export |
| `thyca/agent/loop.py` | giữ nguyên | refactor nội bộ (TASK-002) |
| `thyca/agent/assemble.py` | giữ nguyên | refactor nội bộ (TASK-003) |
| `thyca/agent/think.py` | giữ nguyên | giữ `LLMPort` Protocol nguyên trạng |
| `thyca/agent/thinking.py` | giữ nguyên | không đổi |
| `thyca/agent/act.py` | giữ nguyên | refactor nội bộ nhẹ (TASK-004) |
| `thyca/agent/observe.py` | giữ nguyên | tách helper sang file mới (TASK-005) |
| `thyca/agent/stage.py` | giữ nguyên | không đổi (dataclass đã tối giản) |
| `thyca/agent/events.py` | giữ nguyên | không đổi (contract event đã ổn) |
| `thyca/skills/skill_event.py` | giữ nguyên | bỏ import private M6 (TASK-006) |
| `thyca/agent/reply.py` | giữ nguyên | không đổi |
| `thyca/agent/README.md` | cập nhật | ghi ranh giới mới sau refactor (1 TASK, chỉ doc) |

Thứ tự tách file oversize: **không áp dụng** — không file nào >400 dòng
(lớn nhất `observe.py` 150, `loop.py` 115, `events.py` 110, `act.py` 100, còn lại <100).
Ranh mới duy nhất: 1 file mới `thyca/agent/meta.py` chứa meta-builder tách từ `Observe`
(TASK-005), vì đó là cụm logic SRP rõ nhất và test được độc lập.

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | Chốt baseline: chạy `uv run pytest tests/test_agent_act.py tests/test_agent_assemble.py tests/test_agent_events.py tests/test_agent_loop.py tests/test_agent_observe.py tests/test_agent_stage.py tests/test_agent_think.py tests/test_skill_event.py -q`, ghi số pass/fail làm mốc (fail duy nhất được chấp nhận là baseline đã biết ngoài module) | | |
| TASK-002 | `thyca/agent/loop.py`: tách 2 closure `on_reasoning`/`on_content` trong `AgentLoop.run` thành hàm module `_reasoning_callback(event_sink, round_no)` / `_content_callback(event_sink, round_no)` (trả về `None` khi `event_sink` là `None`), và tách cụm model-echo + `cost_for` fallback thành `_resolve_cost(stage, self._model, self._pricing)`; `run` chỉ còn orchestrate 4 pha. Giữ nguyên thứ tự emit event và persist | | |
| TASK-003 | `thyca/agent/assemble.py:11-12`: bỏ `prompts or PromptManager()` tự dựng concrete trong constructor; đổi signature thành `def __init__(self, prompts: PromptManager) -> None` và sửa 2 call-site cùng module scope (`thyca/cli.py`, `thyca/chat_app.py` thuộc M8 — mở PR phối hợp, M1 cung cấp patch 1 dòng mỗi nơi truyền `PromptManager()` tường minh). Behavior mặc định giữ nguyên vì call-site vẫn truyền cùng object | | |
| TASK-004 | `thyca/agent/act.py`: tách `Act._one` thành 2 hàm module `_build_result(call, dispatched_or_error)` (đóng gói `ToolResult` từ dispatch/exception/`parse_error`) và giữ `_one` chỉ lo emit event + gọi dispatcher; giữ nguyên cặp beat `tool.started`/`tool.finished` và `skill.*` classification, giữ đo latency từng tool trong `act` | | |
| TASK-005 | `thyca/agent/observe.py`: chuyển `_assistant_meta`, `_tool_message`, `_reasoning`, `_reasoning_details` sang file mới `thyca/agent/meta.py` dưới dạng hàm public `assistant_meta(stage, *, kind)`, `tool_message(result, latency_ms, round_no)`; `Observe` chỉ còn persist (`compact`/`user`/`assistant`/`observe`/`loop_limit`) + `_order_results`. Import trong `observe.py` đổi sang `from .meta import ...`; không đổi nội dung meta | | |
| TASK-006 | `thyca/skills/skill_event.py:24`: bỏ `from thyca.skills import _NAME_RE, NAME_MAX` (import private xuyên module sang M6); inline grammar ngay trong file này: `NAME_MAX = 64`, `_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")` (copy byte-for-byte từ `thyca/skills.py:21,25`, comment dẫn nguồn), `is_skill_name`/`classify_skill_read`/`skill_name_for_call`/`public_skill_name` giữ nguyên logic | | |
| TASK-007 | Cập nhật `thyca/agent/README.md`: sửa cây thư mục (thêm `meta.py`, `events.py`, `skill_event.py`, `thinking.py`, `reply.py` còn thiếu), ghi ranh giới `meta.py` (pure, không I/O) và quy tắc grammar skill (M6 sở hữu grammar, M1 giữ bản copy dẫn nguồn) | | |

SOLID findings (có evidence, SRP là chính):

- SRP — `thyca/agent/loop.py` (`AgentLoop.run`, khoảng dòng 40–110): một method vừa
  orchestrate 4 pha, vừa dựng closure callback (`on_reasoning`/`on_content`), vừa resolve
  model-echo + `cost_for` fallback. Tách theo TASK-002; `run` chỉ còn đọc như flowchart
  trong README.
- SRP — `thyca/agent/observe.py` (`_assistant_meta` dòng ~60–85 + `_tool_message`,
  `_reasoning`, `_reasoning_details`): message/meta construction lẫn với session persist.
  Tách sang `meta.py` pure theo TASK-005; `Observe` chỉ còn I/O qua `SessionManager`.
- SRP (nhẹ) — `thyca/agent/act.py` (`_one`, dòng ~60–110): event emission + result
  construction lẫn nhau. Tách theo TASK-004; không đổi wire event.
- DIP — `thyca/agent/assemble.py:11-12`: `self._prompts = prompts or PromptManager()`
  tự dựng concrete dependency trong constructor. Sửa theo TASK-003 (inject bắt buộc).
  `Think` (`think.py:8-10`, `LLMPort` Protocol) và `Act` (`act.py:12-13`,
  `ToolDispatcher` Protocol) đã đúng DIP — giữ nguyên, làm mẫu.
- DIP/ISP — `thyca/skills/skill_event.py:24`: `from thyca.skills import _NAME_RE, NAME_MAX`
  phụ thuộc vào private (dấu `_`) của module M6, tạo coupling ngược M1→M6 vào chi tiết
  scan-time. Sửa theo TASK-006 (copy grammar có dẫn nguồn, cắt import).
- Không phát hiện OCP cần sửa: `TurnEvent._ALLOWED_FIELDS` (`events.py:30-41`) mở rộng
  bằng thêm entry dict — đã open/closed đủ; `Stage` dataclass non-frozen thêm field
  carrier (`stage.py:20-24`) là điểm mở rộng có chủ ý — giữ nguyên.

Cycle/import risks với module khác + cách tránh:

- M1→M5 (`thyca.sessions.SessionManager` trong `loop.py:4`, `observe.py:4`): M5 không
  import ngược M1 hôm nay (grep `from thyca.agent` chỉ thấy `session_wire.py:16` import
  `skill_event`, là M5→M1 một chiều, hợp lệ). Cách tránh: `Observe` giữ dependency này
  ở constructor, không để `sessions/` import `observe`/`loop`; nếu M5 sau này cần type
  từ M1 thì dùng `TYPE_CHECKING`. Không đổi trong plan này.
- M1→M2 (`thyca.llm.pricing.cost_for` trong `loop.py:3`, `PromptManager` trong
  `assemble.py:3`, `ChatReply` trong `think.py:6`): M2 không import M1 (verified bằng
  grep — không có `from thyca.agent` trong `thyca/llm/`). Cách tránh: TASK-002 giữ
  `cost_for` như hàm pure đã inject qua `pricing` dict, không import thêm từ M2;
  không đụng `llm_base`/`prompt_manager` contract.
- M1→M6 (`thyca.skills` private trong `skill_event.py:24`): rủi ro M6 refactor đổi tên
  `_NAME_RE` làm M1 vỡ import. Cách tránh: TASK-006 cắt hẳn import, M1 tự sở hữu bản
  copy có comment dẫn nguồn `thyca/skills.py:21,25`.
- M1→`thyca/protocol.py` (top-level, M8 có thể dời vào `thyca/core/`): 5 file M1 import
  (`stage.py:6`, `act.py:8`, `think.py:7`, `assemble.py:5`, `observe.py:3`). Cách tránh:
  M1 không tự dời `protocol.py`; sau commit layout GOAL-002 của orchestrator, M1 chỉ
  sửa prefix import theo shim/tree mới trong cùng 5 file, verify bằng focused tests.
- Consumers M1 không được vỡ (import surface giữ nguyên): `thyca/cli.py:13-17` + 
  `thyca/chat_app.py:15-20` (M8: `Act`/`Assemble`/`AgentLoop`/`Observe`/`Think`/`LLMPort`),
  `thyca/bridge.py:21-23` (M7: `TurnEvent`/`ThinkingDelta`/`ContentDelta`),
  `thyca/session_wire.py:16` + `thyca/trace_api.py:14` (M5/M7: `skill_name_for_call`).
  Mọi refactor giữ tên class/hàm/signature public; TASK-003 là ngoại lệ duy nhất đổi
  signature và đã có patch phối hợp M8.

### GOAL-002: Verify + review gate

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-008 | Chạy focused gate: `uv run pytest tests/test_agent_act.py tests/test_agent_assemble.py tests/test_agent_events.py tests/test_agent_loop.py tests/test_agent_observe.py tests/test_agent_stage.py tests/test_agent_think.py tests/test_skill_event.py tests/test_chat_app.py tests/test_serve_chat.py tests/test_llm_openai_responses.py -q` xanh bằng hoặc hơn baseline TASK-001; sau đó full `uv run pytest -q` không regression mới; `python -c "import thyca.agent.loop, thyca.agent.observe, thyca.agent.meta"` không circular; `git diff --check` sạch | | |
| TASK-009 | Review độc lập 1 lượt diff M1 (scope trong `thyca/agent/` + 2 dòng patch M8 cho TASK-003 + README): không behavior change (thứ tự event, nội dung meta, persist session byte-for-byte), không sửa test để pass, SOLID findings trên đã đóng | | |

## Test Plan

- Baseline (TASK-001): focused 8 file `tests/test_agent_*.py` + `tests/test_skill_event.py`;
  mốc so sánh cho TASK-008.
- Gate chính (TASK-008): focused gồm 8 file agent + `test_skill_event.py` (skill
  classification sau TASK-006) + 3 consumer tests `test_chat_app.py` (M8 dựng loop),
  `test_serve_chat.py` (M7 dùng `TurnEvent`), `test_llm_openai_responses.py:350` (dùng
  `Observe`/`Stage` trực tiếp). Pass = behavior giữ nguyên qua refactor.
- Full suite `uv run pytest -q` sau focused: không fail mới so với baseline plan tổng
  (fail đã biết `test_debug_prints_prompt_flags` do đếm tool — cấm sửa số này).
- Không sửa test để pass; nếu focused đỏ, fix runtime trong `thyca/agent/` rồi chạy lại.

## Assumptions

1. Module M1 không có file oversize (>400 dòng) nên không tách file lớn; ranh mới duy
   nhất là `meta.py` pure tách từ `Observe` (lý do SRP + testability, đã ghi ở TASK-005).
2. `thyca/agent/` đã là thư mục đích theo plan tổng nên GOAL-002 (physical layout) không
   chạm M1; M1 chỉ cập nhật prefix import `thyca.protocol` nếu orchestrator/M8 dời file
   đó, trong bước layout mechanical, không tính là refactor.
3. TASK-003 đổi signature `Assemble.__init__` cần patch 2 call-site M8 (`thyca/cli.py`,
   `thyca/chat_app.py`); mặc định orchestrator giao M8 apply patch do M1 cung cấp —
   nếu M8 từ chối, fallback giữ signature cũ với `prompts: PromptManager | None = None`
   và ghi lý do vào plan (không giữ `or PromptManager()` lén).
4. Grammar skill (`^[a-z0-9]+(-[a-z0-9]+)*$`, max 64) do M6 (`thyca/skills.py`) sở hữu;
   bản copy trong `skill_event.py` sau TASK-006 là read-only có dẫn nguồn, M1 không tự
   đổi grammar — mọi đổi grammar thuộc về M6.
5. Không thêm dependency, abstraction, hay event type mới; `TurnEvent` allowlist và
   `Stage` fields giữ nguyên.

## Close-out (2026-09-22, orchestrator)
All module tasks landed and verified: branch refactor/backend-solid-M1-agent commit 527f939, test 719/719, review approve (1 minor: TASK-003 fallback accepted). Merged into refactor/backend-solid, full suite 719 passed, plan status done.
