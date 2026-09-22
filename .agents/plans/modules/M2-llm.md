---
status: in-progress
created: 2026-09-22
last_updated: 2026-09-22
---

# M2 — llm module plan

## Summary

Module `thyca/llm/` (~1.440 dòng, 9 file `.py` + `prompts/`) đã nằm đúng thư mục đích
(`thyca/llm/`, giữ nguyên theo plan tổng) — **không cần `git mv` vật lý**.
Phạm vi: kết nối provider (`OpenAIChat` chat-completions, `OpenAIResponses` responses API),
parse JSON/SSE, streaming forwarders, `normalize_usage` chuẩn hóa, `pricing.cost_for`,
`PromptManager.build`. Giữ nguyên contract: `ChatReply.usage` chuẩn hóa
(`prompt_tokens` / `cached_tokens` / `completion_tokens` / `total_tokens` + `reasoning_tokens?`),
`cost_for` unknown model → `None`, Google/Anthropic `ConnectFactory` vẫn `ValueError`.
Không file nào vượt ngưỡng 400 dòng → **không tách file oversize**; refactor giới hạn ở
khử trùng lặp `_redact`/`_cap`/retry giữa hai connect và dọn `normalize_usage` trong file,
không thêm abstraction mới, không đổi behavior. Baseline focused tests: 65 passed.

## Tasks

### GOAL-001: Khử trùng lặp connect + dọn normalize_usage (trong file, không API change)

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | Trích `_redact`, `_cap`, `_RETRY_STATUS`, `_BODY_CAP`, `_sleep_retry_after` dùng chung từ `openai_chat.py:17-27,221` và `openai_responses.py:28-38,202` vào `thyca/llm/_http.py` mới; hai connect import lại, giữ nguyên retry đúng 3 attempts + `set_retry_hook`/`_notify_retry` semantics | | |
| TASK-002 | Tách `normalize_usage` trong `thyca/llm/llm_base.py:27-114` thành các extractor private theo provider (`_extract_openai`, `_extract_responses`, `_extract_anthropic`, `_extract_google`, `_extract_generic`) trong cùng file; giữ nguyên chữ ký và mọi nhánh (kể cả anthropic/google forward-compat) | | |
| TASK-003 | Chạy focused tests `tests/test_llm_factory.py tests/test_llm_openai_chat.py tests/test_llm_openai_responses.py tests/test_llm_pricing.py tests/test_llm_prompt_manager.py` xanh sau GOAL-001 | | |

### GOAL-002: Xác minh biên module + đóng plan

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-004 | Verify không import ngược: `thyca/config`, `thyca/protocol`, `thyca/memory/active.py` không import `thyca.llm` (grep), và `python -c "import thyca.llm.llm_factory, thyca.agent.think, thyca.chat_app"` không circular | | |
| TASK-005 | Chạy consumer smoke `tests/test_agent_think.py tests/test_agent_loop.py tests/test_chat_app.py` + `git diff --check` sạch; full `uv run pytest -q` bằng hoặc hơn baseline (trừ `test_debug_prints_prompt_flags` đã biết fail) | | |

## Test Plan

- Focused gate (baseline 2026-09-22: **65 passed**): `tests/test_llm_factory.py`,
  `tests/test_llm_openai_chat.py` (687 dòng, bao SSE/retry/redact/usage),
  `tests/test_llm_openai_responses.py` (369 dòng), `tests/test_llm_pricing.py`,
  `tests/test_llm_prompt_manager.py`.
- Consumer smoke: `tests/test_agent_think.py`, `tests/test_agent_loop.py`
  (`cost_for` wiring), `tests/test_chat_app.py` (connect inject + retry hook),
  `tests/test_skills.py` (`PromptManager.build` skills block).
- Merge gate: full `uv run pytest -q` không regression mới vs baseline; known failure duy nhất
  được chấp nhận là `tests/test_cli.py::test_debug_prints_prompt_flags` (`tools=7` baseline) —
  không sửa test này. `git diff --check` sạch, không circular import.
- Không sửa test để pass; mọi đổi contract (nếu phát sinh) phải ghi vào plan và được
  orchestrator duyệt trước.

## Assumptions

1. Layout đã đúng: `thyca/llm/` giữ nguyên, GOAL-002 plan tổng không move file M2;
   team coding rebase sau commit layout để nhận đổi import path từ M3/M4 (nếu có).
2. `ConnectFactory.create` chữ ký `(kind, provider)` giữ nguyên — consumers
   `thyca/chat_app.py:427`, `thyca/cli.py:170`, `scripts/retitle_sessions.py:33` và các test
   inject fake connect phụ thuộc vào nó.
3. `thyca/agent/think.py:13-21` `LLMPort` Protocol đã tách agent khỏi concrete connect —
   không thêm interface/abstraction mới cho M2.
4. `thyca/onboarding.py` (`test_provider_api`/`test_chat`/`test_responses_chat`) duplicate
   logic wire riêng — thuộc M8, M2 không đụng tới.
5. Không thêm dependency, provider mới, hay feature; Google/Anthropic qua factory vẫn
   `ValueError` theo `tests/test_llm_factory.py`.

---

## Phụ lục evidence (không thi hành, chỉ để coding team tra cứu)

### 1. Target layout — moves chính xác (git mv)

Không có move nào. Module đã ở thư mục đích:

| Từ | Sang | Ghi chú |
|----|------|---------|
| *(không)* | `thyca/llm/` giữ nguyên | `__init__.py`, `llm_base.py` (148), `llm_factory.py` (32), `openai_chat.py` (221), `openai_parse.py` (332), `openai_responses.py` (202), `pricing.py` (73), `prompt_manager.py` (65), `responses_parse.py` (271), `streaming.py` (100), `prompts/` — tất cả giữ nguyên path |

### 2. Thứ tự tách file oversize + ranh giới mới

Không file nào >400 dòng (lớn nhất `openai_parse.py` 332, `responses_parse.py` 271) →
không tách file. Ranh giới mới duy nhất: `thyca/llm/_http.py` (TASK-001) chứa helpers
dùng chung của hai connect; không tách thêm.

### 3. SOLID findings (có evidence file:line)

- **SRP (chính) — helpers trùng lặp giữa hai connect:** `_redact`/`_cap` định nghĩa
  lặp ở `openai_chat.py:17-27`, `openai_parse.py:13-22`, `openai_responses.py:28-38`,
  `responses_parse.py:19-29` (bản `_redact` riêng trong `streaming.py:22-26`);
  hằng retry `_RETRY_STATUS`/`_BODY_CAP`/`_RETRY_AFTER_CAP_S` + `_sleep_retry_after` lặp
  nguyên khối `openai_chat.py:15-16,197-212` vs `openai_responses.py:25-26,194-212`;
  vòng retry 3-attempt + `set_retry_hook`/`_notify_retry` mirror
  `openai_chat.py:53-131` vs `openai_responses.py:50-125`. → TASK-001 gom phần http/retry
  chung vào `_http.py`; parse-side `_redact`/`_cap` giữ tại chỗ (khác `limit` signature).
- **SRP — `normalize_usage` đa nhánh trong `llm_base.py:27-114`:** một hàm rẽ 5 nhánh
  provider (openai/chat/compat, openai_responses, anthropic, google, generic) + suy luận
  `prompt`/`completion`/`total` + clamp `cached_tokens`. Nhánh anthropic/google hiện không
  tới được qua factory (`llm_factory.py:10-17` `_KINDS` chỉ có openai*, factory raise
  `ValueError` cho `google`/`anthropic` — pin bởi `tests/test_llm_factory.py`) nhưng
  parse/tests vẫn dùng trực tiếp (`tests/test_llm_openai_chat.py:146-164`) nên giữ nhánh,
  chỉ tách extractor private trong file. → TASK-002.
- **DIP (đã tốt, không đổi):** `thyca/agent/think.py:13-21` định nghĩa `LLMPort` Protocol,
  `Think` phụ thuộc Protocol chứ không phụ thuộc `Connect` concrete — M2 không cần thêm
  abstraction. `ConnectFactory._KINDS` (`llm_factory.py:10-17`) đóng-mở tốt (thêm provider =
  thêm entry), giữ nguyên.
- **ISP/OCP:** không có evidence vi phạm — `Connect.chat` callbacks đều optional có default
  (`llm_base.py:42-49`); `Think._chat_with_callbacks` đã degrade per-kwarg. Không đổi.

### 4. Cycle/import risks với module khác + cách tránh

- **Hướng phụ thuộc hiện tại là một chiều, an toàn:** M2 import `thyca.config`
  (`llm_factory.py:3`, `openai_chat.py:9`, `openai_responses.py:14`, `pricing.py:6`),
  `thyca.protocol` (`llm_base.py:8`, `openai_chat.py:10`, `openai_parse.py:9`,
  `openai_responses.py:15`, `responses_parse.py:10`, `streaming.py:11`),
  `thyca.memory.active` (`prompt_manager.py:5` — chỉ type `ActiveSnapshot`).
  Consumers chiều ngược: `thyca/agent/loop.py:3` (`cost_for`),
  `thyca/agent/think.py:6` (`ChatReply`), `thyca/agent/assemble.py:3,12` (`PromptManager`),
  `thyca/chat_app.py:22-24`, `thyca/cli.py:20-22`, `thyca/bridge.py:26`,
  `thyca/sessions/title.py:8` (`LLMError`). Rủi ro duy nhất là M3/M4 đổi path
  (`ProviderCfg`/`PricingCfg`/`ActiveSnapshot`) sau layout — cách tránh: coding branch từ
  sau commit layout GOAL-002, và TASK-004 grep xác nhận không import ngược trước khi merge.
- **`prompt_manager.py:5` → M4 `memory.active`:** nếu Team-memory move/đổi `ActiveSnapshot`,
  import này gãy — phối hợp qua orchestrator, M2 không tự sửa code M4.
- **`_http.py` mới chỉ được import nội bộ M2** (hai connect); không export qua `__init__`
  (hiện trống), không cho module khác import trực tiếp để tránh biên mờ.

### 5. Success criteria đo được + test gate

- `git status` M2 chỉ chạm `thyca/llm/*`: tối đa 1 file mới (`_http.py`) + sửa
  `llm_base.py`, `openai_chat.py`, `openai_responses.py`; zero `git mv`; public API
  (`Connect.chat`, `ConnectFactory.create`, `normalize_usage`, `cost_for`,
  `PromptManager.build`, `ChatReply` fields) byte-compatible.
- Focused gate: 5 file `test_llm_*` ≥ 65 passed (baseline 65 passed 2026-09-22).
- Consumer smoke: `test_agent_think`, `test_agent_loop`, `test_chat_app` xanh.
- Full suite: không regression mới vs baseline; `git diff --check` sạch; import probe
  `python -c "import thyca.llm.llm_factory, thyca.agent.think, thyca.chat_app"` thành công.
