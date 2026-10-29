---
status: done
created: 2026-10-26
last_updated: 2026-10-26
---

# Model maxTokens (trần output) như Pi

## Goal

Thêm một field `maxTokens` (trần token output) cho từng model, khai báo một chỗ
và được nạp xuyên suốt: config → resolve theo model → payload cả 2 kiểu API →
form Provider. Backend hiện chỉ có `contextTokens` (trần input/ngữ cảnh, mặc
định 272000) và không gửi trần output nào; provider tự quyết độ dài trả lời.

## Approved scope

- Chỉ backend + 1 ô nhập trong form Provider (cạnh "Ngữ cảnh (token)").
- Không thêm field input token riêng (giống Pi: `contextWindow` + `maxTokens`).
- Không đổi `LimitsCfg` global; không đổi cách `contextTokens` cắt input.

## Accepted decisions (user, 2026-10-26)

- Chưa khai `maxTokens` → vẫn gửi mặc định **16384** như Pi.
- Chỉ khai theo từng model (`models[].maxTokens`); không có tầng global.
- Chat API gửi `max_tokens`; Responses API gửi `max_output_tokens`.
  (Giới hạn đã chấp nhận: model chỉ nhận `max_completion_tokens` không được
  hỗ trợ ở vòng này.)

## Flow hiện tại (điểm phải nạp config)

1. Khai báo model: `thyca/config/models.py` `ModelCfg` (sparse: `None` = chưa
   khai) + validation range qua `LIMIT_RANGES` như `contextTokens`.
2. Đọc/ghi file: `thyca/config/parsing.py` (`_MODEL_KEYS`, `_parse_models`
   dùng `_optional_int`) và `thyca/config/models.py` `_model_to_dict`
   (chỉ ghi khi có giá trị).
3. Resolve theo model: `thyca/config/providers.py` `ProviderCfg` + `resolve()`
   — nơi `reasoningEffort`/set của model thắng provider. `ProviderCfg` là thứ
   tầng `llm/` nhận được, nên `maxTokens` phải đi theo đây với mặc định 16384
   khi model không khai.
4. Payload:
   - `thyca/llm/openai_chat.py` `chat()` → thêm `max_tokens` khi có giá trị.
   - `thyca/llm/openai_responses.py` `chat()` → thêm `max_output_tokens`.
5. Form Provider: `thyca/webui/ui/provider/js/provider.js` — ô mới cạnh
   `#m-context`, theo đúng pattern sparse hiện tại (trống = không khai,
   placeholder nêu mặc định 16384; snapshot/dirty gồm field mới;
   `draftForSave` parse + `mergeEditorDraft` ghi/xóa key như `contextTokens`).

## Tasks

- [x] TASK-001: `defaults.py` thêm hằng mặc định 16384 + range cho `maxTokens`
      (min 1; max theo trần context hiện hữu 2,000,000 để validation nhất quán).
- [x] TASK-002: `ModelCfg.maxTokens: int | None = None` + validation;
      `ProviderCfg.maxTokens` mặc định 16384 + `resolve()` để model thắng.
- [x] TASK-003: parsing (`_MODEL_KEYS`, `_parse_models`) + `_model_to_dict`
      round-trip; config cũ không có key vẫn load nguyên trạng.
- [x] TASK-004: payload Chat (`max_tokens`) + Responses (`max_output_tokens`),
      kèm kiểm tra cơ chế drop-retry hiện có không nuốt mất field này sai lệch.
- [x] TASK-005: form Provider thêm ô "Output tối đa (token)" cạnh Ngữ cảnh,
      placeholder "Mặc định 16384"; lưu trống → không ghi key. Steer bổ sung
      (user): step spinner cả hai ô = 16000 (ước chung của 272000/128000).
      Steer cùng vòng (ngoài plan, đã xong): bỏ nút "Chỉ lưu cấu hình"
      (mọi save đều "Lưu và dùng" — đơn giản hóa `submitEditor`/`draftForSave`/
      `mergeEditorDraft`, bỏ `useSelected`) + bỏ nút "Tải lại" header
      (`reloadConfig` giữ lại cho setup-retry recovery).
- [x] TASK-006: tests — config parse/validate/round-trip + resolve model thắng/
      mặc định; wire golden/chat/responses assert field trong payload; provider
      frontend assert lưu/đọc/xóa key.

## Checks

- Focused: `tests/test_config.py`, `tests/test_llm_openai_chat.py`,
  `tests/test_llm_openai_responses.py`, wire golden liên quan,
  `tests/test_provider_frontend.py`.
- Full gate `uv run pytest -q` trước khi báo xong (field mới chạm config dùng
  chung). Kiểm tra tay trên preview 19503: ô mới hiện cạnh Ngữ cảnh.
- Chưa kiểm chứng live provider (không gọi API thật ở vòng này).
