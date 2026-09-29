---
status: done
created: 2026-09-29
last_updated: 2026-09-29
---

# Đập provider Thyca xây lại (gọn + đúng chuẩn OpenAI)

## Summary

Viết lại provider layer (`thyca/config/providers.py`, `thyca/llm/*`, network trong
`thyca/app/onboarding.py`) theo nguyên tắc đã chốt: **gửi chuẩn tối thiểu, đọc
tolerant, không compat matrix**. Mục tiêu là ít code hơn, 1 đường wire duy nhất
mỗi API, Test API đi đúng wire thật — không thêm option mới, không đổi
`config.json`/`auth.json`.

Vấn đề hiện tại (có evidence):
- `ProviderEntry` + `ProviderCfg` trùng field, 2 đường validate
  (`thyca/config/providers.py`), resolution fallback 3 tầng
  (`thyca/config/root.py:84-111`).
- 2 parser lớn trùng pattern: `openai_parse.py` (~300 dòng) +
  `responses_parse.py` (370 dòng); `ContentOut`/`ReasoningOut` trùng ~60%
  holdback/flush (`thyca/llm/streaming.py`).
- `onboarding.py` là HTTP client thứ hai (urllib sync) với error mapping và
  schema check riêng; Test API không gửi `reasoning_effort` nên có thể
  "test OK nhưng chat fail".
- `llm_factory.py` còn aliases legacy (`openai`, `openai_compat`, `responses`).
- Thiếu `store: false` trên Responses (server lưu response mồ côi).

Giữ ổn định tuyệt đối (không đàm phán trong plan này):
`config.json`/`auth.json` wire format, `Config` public API
(`load`/`save`/`effective_provider_for`/`provider`/`to_dict`),
`Connect.chat()` contract, `ChatReply` + `normalize_usage` shape, WebUI
`config_schema()` keys, response shape `providers_test`/`onboarding_verify`,
chuỗi lỗi tiếng Việt đã assert trong test.

Thay đổi wire duy nhất có chủ ý: Responses payload thêm `"store": false`.

### GOAL-001: Khóa hành vi hiện tại bằng golden tests

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | Thêm golden test: cùng 1 transcript (system/user/assistant+tool_call/tool) → assert bytes request JSON của chat và responses (ghi lại bytes hiện tại làm chuẩn) | x | 2026-09-29 |
| TASK-002 | Liệt kê contract đóng băng vào plan này (schema keys, endpoint, error strings đang assert) để GOAL sau đối chiếu | x | 2026-09-29 |

### Frozen contract (GOAL-001, 2026-09-29)

- Endpoints: chat `POST <base>/chat/completions`, responses `POST <base>/responses`, stream SSE; probe dùng `stream:false`.
- Chat payload: `model`, `messages[]`, `stream:true`, `stream_options:{include_usage:true}`, optional `tools` (passthrough), optional `reasoning_effort` verbatim. Item: `role`, `content`, optional `reasoning_details[]` (chỉ chat shapes), optional `tool_calls[]` (`id/type:function/function:{name,arguments}`), optional `tool_call_id`.
- Responses payload: `model`, `input[]`, `stream:true`, optional `tools` phẳng, optional `reasoning:{effort,summary:"auto"}`. Thay đổi cho phép duy nhất: thêm `"store":false`.
- Error strings đóng băng: xem agent result pr-golden (LLM + onboarding/serve tiếng Việt) — GOAL sau không đổi chữ nào đang assert.

### GOAL-002: Gộp config provider

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-003 | Viết lại `thyca/config/providers.py`: 1 dataclass stored + pure `resolve(entry, model_id, model_cfg)` trả connection frozen; gộp validate trùng; `effective_provider_for` chỉ còn tra `providers[pid]` (parse đã đảm bảo `defaultProvider` tồn tại, bỏ 2 fallback) | x | 2026-09-29 |
| TASK-004 | `tests/test_config.py` xanh, golden TASK-001 không đổi bytes | x | 2026-09-29 |

### GOAL-003: Viết lại llm theo 1 module 1 chuẩn

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-005 | Gộp `openai_parse.py` vào `openai_chat.py` (build + parse + SSE của chat ở 1 file), giữ tên export/import; xóa `openai_parse.py`. Ràng buộc: file gộp ≤400 dòng, mọi class ≤300 — tách helper thật-sự-chung (tool-call slot assembly, usage/error mapping) ra module private chung TRƯỚC khi gộp để vừa giới hạn (review 2026-09-29: gộp thô tạo file ~410–440 dòng, vi phạm discipline repo) | x | 2026-09-29 |
| TASK-006 | Gộp `responses_parse.py` vào `openai_responses.py` (cùng ràng buộc ≤400 dòng như TASK-005), thêm `"store": False`; xóa `responses_parse.py`; cập nhật golden responses (1 field mới). Verify endpoint Meta chấp nhận `store:false` (gộp vào TASK-016); nếu 400 thì cho `store` vào cơ chế drop-param retry của `_http.py` thay vì fail cứng (verify Meta dời sang TASK-016) | x | 2026-09-29 |
| TASK-007 | Gộp `ContentOut`/`ReasoningOut` thành 1 forwarder tham số hóa (`cap: int | None`) trong `streaming.py`, giữ hành vi holdback/redact/flush | x | 2026-09-29 |
| TASK-008 | Rút gọn `_http.py`: giữ `BaseConnect` retry 3 + drop-param + `redact`/`cap`/SSE-iterate; bỏ helper chết sau khi gộp | x | 2026-09-29 |
| TASK-009 | Rút gọn `llm_factory.py`: giữ `openai_chat`/`openai_responses` làm chuẩn; aliases legacy (`openai`, `openai_compat`, `responses`) GIỮ LẠI dưới dạng deprecated mapping có comment (config user cũ có thể chứa string này — `config.api` là free-form, review 2026-09-29); chỉ xóa khi đã có migration lúc load | x | 2026-09-29 |
| TASK-010 | Provider tests xanh (`test_llm_*`, `test_reasoning_contracts`), golden chat bytes giữ nguyên | x | 2026-09-29 |

### GOAL-004: Test API đi qua Connect thật

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-011 | `test_chat`/`test_responses_chat` trong `onboarding.py` gọi `Connect.chat()` bằng message `ping` (sync qua `asyncio.run`, timeout 20s giữ nguyên) thay vì urllib + schema check riêng; giữ nguyên `{"model", "latency_ms"}` và message lỗi tiếng Việt | x | 2026-09-29 |
| TASK-012 | Giữ `validate_provider` (GET `/models`) nguyên — listing không thuộc `Connect` contract | x | 2026-09-29 |
| TASK-013 | Xóa code urllib/schema-check chết sau TASK-011; `test_serve_config.py` + config tests xanh | x | 2026-09-29 |

### GOAL-005: Dọn và khóa

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-014 | Full `uv run pytest -q` + `ruff check` xanh; không file chết, không import chết | x | 2026-09-29 |
| TASK-015 | Đo line count `thyca/llm` + `thyca/config/providers.py` trước/sau, ghi vào plan; cập nhật CHANGELOG (1 entry, nêu `store:false` là wire change duy nhất) | x | 2026-09-29 |
| TASK-016 | Verify tay: Test API + chat 1 turn trên cả Meta (responses) và CommandCode (chat), đổi model giữa chừng (lọc `reasoning_details` khác chuẩn) | x | 2026-09-29 |

## Close-out

Done 2026-09-29. User verified live on 0.86.5.dev0 (Meta + CommandCode Test API and chat, mid-session model switch): stable. Merged to main.

## Test Plan

- Baseline 2026-09-29: 175 passed (`test_llm_openai_chat`, `test_llm_openai_responses`,
  `test_llm_factory`, `test_config`, `test_reasoning_contracts`, `test_serve_config`).
- Golden payload tests (TASK-001/006): chat bytes giữ nguyên 100%, responses chỉ
  thêm `store:false`.
- Full suite `uv run pytest -q` xanh (không chấp nhận fail mới).
- Tiêu chí gọn (ĐIỀU CHỈNH 2026-09-29, user duyệt (a)): gọn cấu trúc, không tăng dòng. Đo thực tế 1504 → 1537 dòng (+2.2%) — mục tiêu giảm ≥30% bỏ vì bề mặt dùng chung thật chỉ ~90 dòng (gộp verbatim giữ hành vi; providers.py +51 do viết rõ resolve policy). Đạt: -1 file (xóa 2 parse, thêm `_shared.py`), 1 module 1 chuẩn, helper single-home, xóa đường HTTP thứ hai ở probe, file 343/399, class ≤300, golden chat 100% + responses +1 field.
- Tiêu chí tương thích: `~/.thyca/config.json` hiện tại load không sửa;
  panel Cài đặt render đủ field; Test API pass cả 2 provider thật.

## Assumptions

- Giữ legacy migrations (single `provider` block, `pricing`→`models`,
  per-model `baseUrl`): rẻ (~40 dòng), bỏ là mất config user cũ.
- Giữ 2 APIs, effort passthrough verbatim (`low/high/max` + per-model
  `reasoningEfforts`), `summary:"auto"` hardcode; không thêm
  temperature/max_tokens/thinkingFormat/compat flags (quyết từ discussion
  chuẩn-tối-thiểu ngày 2026-09-29).
- Giữ tên file/module `llm` hiện tại (chỉ gộp parse vào wire module, xóa 2
  file parse); `prompt_manager.py` và `pricing.py` ngoài scope.
- Implement trên nhánh mới tách từ `dev/0.86.4` (tree đã sạch sau commit `be7625a`).
