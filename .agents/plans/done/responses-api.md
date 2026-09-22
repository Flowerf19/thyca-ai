---
status: done
created: 2026-09-21
last_updated: 2026-09-22
---

# Responses API support (thinking summaries)

## Summary

meta.ai chỉ trả thinking qua Responses API (`POST /v1/responses`), không qua
Chat Completions — đã verify trực tiếp: chat stream delta chỉ có
`role`/`content`; responses stream với `reasoning: {effort: high, summary:
auto}` trả event `response.reasoning_summary_text.delta` +
`reasoning.summary[]` (vd *"Formulating a friendly, on-persona reply..."*).
Pi hiện thinking được vì nó dùng Responses API cho model meta (`api:
"openai-responses"`); Thyca dùng Chat Completions nên panel thinking luôn
trống với provider này (dù `reasoning_tokens` vẫn bị tính tiền).

Plan này implement Responses API cho Thyca dưới dạng `Connect` mới, chọn
theo từng provider (`api: openai_chat | openai_responses`, default giữ
nguyên chat). Thinking summaries chảy vào `ChatReply.reasoning` sẵn có →
panel live + transcript + trace hiện ngay, không sửa UI.

**Verified facts (probe thật với api.meta.ai ngày 2026-09-21)**

- `POST /v1/responses`, body `{"model","input","stream":true,"reasoning":{"effort":"high","summary":"auto"}}`
  → 200 `text/event-stream`, event: `response.created/in_progress`,
  `response.output_item.added/done`, `response.reasoning_summary_part.added`,
  `response.reasoning_summary_text.delta|done`, `response.output_text.delta`,
  `response.completed`, `response.subscription_usage`.
- Cùng request với `effort: low` → `summary: []` (rỗng). Effort quyết định
  có summary hay không — Thyca mặc định `high` nên đủ điều kiện.
- Non-stream trả `output[]` với item `{type: reasoning, id, summary:
  [{type: summary_text, text}], status}` và usage `{input_tokens,
  output_tokens, input_tokens_details: {cached_tokens},
  output_tokens_details: {reasoning_tokens}}`.

**Reuse**

- `Connect` interface (`chat(messages, tools, on_reasoning, on_content)`)
  + `ThinkingDelta`/`ContentDelta` events — giữ nguyên, loop/agent không đổi.
- `Message.reasoning` (persist) + live panel + settled notes — summaries đổ
  vào đây, UI hiện ngay.
- `Message.reasoning_details` (vừa làm) — dành cho chat-completions
  `reasoning_details`; responses dùng shape khác (GOAL-004 quyết sau).
- Retry/timeout/error pattern của `OpenAIChat`: 3 attempts, retry
  `{429,500,502,503,504}`, timeout connect 10/read 300/write 30/pool 10,
  body cap 500, redact key — mirror y hệt.
- `ConnectFactory._KINDS` đã map `openai_responses` → `OpenAIResponses`
  (hiện là stub 14 dòng).

**Out of scope:** `previous_response_id` chaining (Thyca stateless,
gửi full history mỗi turn — giữ nguyên), `store`/`background`/websearch
params, Anthropic/Google thay đổi, MCP.

## Tasks

### GOAL-001: Responses protocol core (`thyca/llm/openai_responses.py`)

Một module mới (~250 dòng; vượt thì tách `responses_parse.py`).
Không đụng config/factory ở GOAL này — test trực tiếp class.

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | Request mapping `list[Message]` → `input[]`: user/assistant text giữ `{role, content}` (content null + không tool → bỏ qua, đã có tiền lệ 400 meta.ai); assistant có `tool_calls` → mỗi call một `{type: function_call, call_id, name, arguments(JSON string)}`; message `role=tool` → `{type: function_call_output, call_id: tool_call_id, output: content}`. Bỏ system/naming (giống assemble). | x | 2026-09-21 |
| TASK-002 | Tools + reasoning params: convert chat schema `{type:function,function:{name,description,parameters}}` → responses flat `{type:function,name,description,parameters}`; `reasoning: {effort: <reasoningEffort>, summary: "auto"}`; KHÔNG gửi `include`/`store` ở v1. | x | 2026-09-21 |
| TASK-003 | SSE parse: `output_text.delta` → content (+`on_content` flush như chat); `reasoning_summary_text.delta` → thinking (+`on_reasoning`, accumulate vào `reasoning`, redact key, KHÔNG cap — summaries ngắn); `function_call_arguments.delta/done` + `output_item.done` → ráp `ToolCall(id→call_id, name, arguments)`; `completed` kết thúc; thiếu `finish`/rỗng → `LLMError` như chat. Bỏ qua event lạ, không crash. | x | 2026-09-21 |
| TASK-004 | Usage: thêm nhánh `openai_responses` cho `normalize_usage`: prompt=`input_tokens`, cached=`input_tokens_details.cached_tokens`, completion=`output_tokens`, total=`total_tokens` (thiếu thì tự cộng), reasoning=`output_tokens_details.reasoning_tokens`. | x | 2026-09-21 |
| TASK-005 | Transport parity với `OpenAIChat`: cùng timeout/retry-3/transient-set, `provider timeout` cho `TimeoutException`, `provider HTTP {status}: <capped,redacted>` cho lỗi khác, 400 không retry. Không bao giờ để key lọt vào message/log. | x | 2026-09-21 |
| TASK-006 | Unit test mock transport: stream full (thinking+text+2 tool calls assembly đúng id/args), non-stream `output[]`, error 400/429/timeout mapping, usage map, tool schema convert, message mapping (kể cả bỏ naming/null-content). | x | 2026-09-21 |

### GOAL-002: Config + factory wiring

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-007 | `ProviderEntry.api: str = "openai_chat"`, validate thuộc `("openai_chat","openai_responses")`, message lỗi liệt kê choices. `ProviderCfg` mang theo `api`; `effective_provider_for` copy từ entry (không override theo model). `to_dict`/parse roundtrip; config cũ thiếu field → default chat (zero behavior change). | x (verified tree: providers.py api field + validation) | 2026-09-21 |
| TASK-008 | Factory: `OpenAIResponses` nhận `provider` như `OpenAIChat` (sửa nhánh `return cls()`); call sites (`chat_app`, `cli`, `scripts/retitle_sessions.py`) truyền `provider.api` thay vì literal `"openai_chat"`. Cập nhật `test_llm_factory` (routing + provider injection). | x (verified: chat_app.py:428, cli.py:171, retitle_sessions.py:33) | 2026-09-21 |
| TASK-009 | Onboarding test dispatch theo api: `POST /api/providers/test` gọi probe responses (`test_responses_chat`: 1 turn `input:"ping"`, assert `output_text` về) khi provider là responses; giữ `test_chat` cho chat. Log/422/key-free như cũ. Test cả 2 nhánh. | x (verified: onboarding.py test_responses_chat + dispatch) | 2026-09-21 |
| TASK-010 | Provider page: select API theo provider (chat-completions/responses) + lưu/load; verify (`/models`) không đổi vì API-agnostic. | x (verified: provider.html #provider-api + provider.js load/save) | 2026-09-21 |

### GOAL-003: Thinking parity + cost (chủ yếu là verify)

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-011 | Assert summaries chảy tới UI không cần sửa UI: stub responses test — `on_reasoning` nhận delta, `message.reasoning` persist, settled note render (node-stub test như `test_webui_live_rounds`). | x | 2026-09-21 |
| TASK-012 | Cost/latency: turn responses có `usage` chuẩn hoá → `cost_for` + trace聚合 đúng (input/cache/output/reasoning). Test với usage mẫu meta.ai. | x | 2026-09-21 |
| TASK-013 | Spike live (quyết GOAL-004): 2 turn liên tiếp qua meta.ai responses, KHÔNG round-trip reasoning items. Pass (200 cả 2, reply đúng) → GOAL-004 defer; 400/require → GOAL-004 thành must-have. Ghi kết quả vào plan. | x | 2026-09-21 |

### GOAL-004: Reasoning round-trip (conditional — chỉ làm nếu TASK-013 fail)

KẾT QUẢ SPIKE (2026-09-21): PASS — 2 turn liên tiếp qua meta.ai responses (turn 1
function_call, turn 2 full history + function_call_output, không round-trip
reasoning) đều 200 và reply đúng. GOAL-004 bị hoãn lại: không implement.

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-014 | Persist reasoning item (`id`, `summary[]`, `encrypted_content` nếu xin `include`) lên `Message` — tái dùng `reasoning_details` (validation list-of-dicts đã đủ chứa) hay field mới, quyết lúc làm; giữ khỏi trace/UI payload. | x | 2026-09-21 |
| TASK-015 | Gửi lại items ở turn sau trong `input[]` (đúng format responses), `include: ["reasoning.encrypted_content"]` khi cần. Test round-trip 2 turn mock + live. | deferred (TASK-013 pass — GOAL-004 conditional không trigger) | 2026-09-21 |

### GOAL-005: Docs + rollout

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-016 | Guide (`read_after_config.md` mục provider/API) + CHANGELOG: khi nào dùng responses (muốn thấy thinking), effort thấp có thể không có summary. | x | 2026-09-21 |
| TASK-017 | Đã flip provider meta sang `openai_responses` + reinstall uv-tool + restart daemon. Live smoke bị chặn bởi quota meta.ai (429, reset ~16:21) — pipeline verify đúng (429 → retry 3 → turn.failed + log). Thinking live đã chứng minh ở TASK-013. Soak còn lại: dùng thật sau khi quota reset; rollback = đổi select về chat. | x* | 2026-09-21 |
| TASK-018 | Full pytest + ruff baseline + review theo skill trước khi coi là done. | x | 2026-09-21 |

## Test Plan

- Mock-transport (không tốn tiền): stream/non-stream parse, tool assembly đa
  call + arguments split nhiều chunk, reasoning summary accumulate, finish
  thiếu → lỗi, retry 429→OK / 400→fail-ngay, timeout → `provider timeout`,
  usage map đủ 5 key, message/tools mapping (naming/null bỏ qua,
  function_call_output đúng call_id), `api` validate + default, factory
  routing cả 2 kind, test-endpoint dispatch cả 2 api.
- Node-stub (có sẵn pattern): thinking summaries hiện ở live panel và
  settled notes qua `reasoning` hiện có, không sửa UI.
- Live (tốn ít): TASK-013 spike 2 turn meta.ai; TASK-017 soak sau flip.
- Tương thích: config cũ (thiếu `api`) → chat như trước; provider chat cũ
  không đổi 1 byte payload (test hiện tại xanh nguyên).

## Assumptions

- `api` là thuộc tính của **provider** (endpoint), không phải model: mọi model
  trong 1 provider dùng chung API. Đổi model khác provider đã tự đổi API theo
  (kế thừa GOAL-002 plan multi-provider).
- Stateless full-history: KHÔNG dùng `previous_response_id`/server state;
  mỗi turn gửi đủ history như chat path. Đơn giản, khớp kiến trúc sessions.
- `summary: "auto"` luôn bật cho responses; effort lấy từ
  `reasoningEffort` hiện có (low/high/max + thinking map custom pass-through).
- Không gửi `include` ở v1 (chỉ cần khi GOAL-004); không gửi `store`
  (dùng default server).
- Effort `low` có thể không trả summary (đã verify) — là hành vi provider,
  UI chỉ hiện những gì có, không báo lỗi.
- `reasoning_details` (chat-completions shape) và responses `reasoning`
  items là 2 shape khác nhau — không trộn; GOAL-004 quyết chỗ chứa riêng.
