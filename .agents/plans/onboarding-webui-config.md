---
status: done
created: 2026-08-28
last_updated: 2026-08-28
---

# Settings WebUI: config panel + fetch models + chọn model (data-driven)

## Summary

Nút **Cài đặt** (`#settings-btn`) đã có trong sidebar nhưng chưa bind. Nó mở
**settings panel** đọc/ghi `~/.thyca/config.json` qua API. Có thêm
`provider.reasoningEffort` (default **high**, choice low/medium/high) chỉnh được
trong UI; loop gửi `reasoning_effort` lên Chat Completions và tự rút khi provider
từ chối (400 có "reasoning_effort" trong message) để không vỡ với model thường. Thiết kế **data-driven**:

- Backend sinh **config schema** từ dataclass trong `thyca/config.py`
  (field + type + min/max + label hiển thị). Thêm field mới vào `Config` →
  panel tự render thêm, **không sửa code frontend**.
- Riêng nhóm `provider`: ngoài form còn nút **"Tải danh sách model"** →
  backend probe `GET {baseUrl}/models` bằng key đang nhập → user chọn model
  từ `<select>` (vẫn gõ tay được nếu provider không có `/models`).
- Lần đầu cài bằng curl mà chưa có key: mở webui → boot thấy provider chưa
  dùng được → **tự mở settings panel** ở tab provider, chat bị chặn đến khi
  lưu key hợp lệ.

## Interface mới

### Backend

- `thyca/config_schema.py` (module mới):
  - `config_schema() -> dict` — sinh từ dataclass `Config`:
    `{sections: [{key, label, fields: [{key, type, label, default, min?, max?, secret?}]}]}`.
    Type map: `str` → text (secret nếu tên chứa `apikey`, case-insensitive),
    `int` → number (min/max lấy từ `__post_init__` hằngKnown — ghi bảng range
    cạnh dataclass), `dict` → nested section, `float` → number.
    Label tiếng Việt đặt trong 1 bảng `_LABELS` duy nhất; field không có label
    → dùng chính key (vẫn hiện, không bỏ sót field mới).
- `thyca/onboarding.py` (module mới):
  - `provider_ready(cfg) -> bool` — `cfg.provider.api_key()` không raise.
  - `validate_provider(baseUrl, apiKey, timeout=10.0) -> list[str]` —
    `GET {baseUrl.rstrip('/')}/models`, `Authorization: Bearer`.
    200 → `sorted({id})` từ `data[].id`; schema lạ → rỗng + raise
    `ProviderProbeError` khi lỗi mạng/401/403/HTTP lỗi. Message không chứa key.
- Route trong `serve.py`:
  - `GET /api/config` → `{schema, values}`. `values` = `cfg.to_dict()` nhưng
    `provider.apiKey` thay bằng `""` (đã đặt) — không bao giờ trả key về client.
  - `POST /api/config` — body = values JSON cùng shape; `provider.apiKey == ""`
    → giữ key cũ (merge trước khi parse). Validate qua `_parse_dict` (mua sẵn
    toàn bộ rule `ConfigError` của config.py) → `config.save()` → `{ok: true}`.
    Lỗi → 422 `{error}` (message từ ConfigError, không echo key).
  - `POST /api/onboarding/verify` — body `{baseUrl, apiKey?}` (apiKey rỗng →
    dùng key đã lưu) → 200 `{models: [...]}` hoặc 422 `{error}` / 504 timeout.
  - `GET /api/config/status` → `{ready: bool}` cho boot gate.
- Handler không log key; error response không chứa key.

### Frontend (webui/)

- `js/settings.js` + `css/settings.css` (mới) + `<dialog id="settings">` trong
  `index.html` (dùng `<dialog>` có sẵn của nền tảng, khớp pattern hiện tại).
- Renderer **generic**: đọc `schema` → mỗi section 1 group, mỗi field 1 control
  theo `type` (text/number/password). Không hardcode tên field nào trong JS —
  chỉ có 1 hook: field `provider.baseUrl`/`provider.apiKey`/`provider.model`
  được gắn nút "Tải model" + `<select>` gợi ý (select vẫn cho gõ tay:
  `<input list>` + `<datalist>`).
- Luồng: mở panel → `GET /api/config` đổ values → sửa → "Tải danh sách model"
  (verify) → chọn/enter model → "Lưu" → `POST /api/config` → nếu trước đó
  `ready == false` → `location.reload()`.
- Boot (`js/app.js`): gọi `/api/config/status`; `!ready` → mở settings tự động,
  disable composer; sau khi lưu → reload vào chat.

## Tasks

### GOAL-001: Backend schema + API

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | `thyca/config_schema.py`: `config_schema()` sinh từ dataclass, label bảng `_LABELS`, secret detection, range int, choice (provider.reasoningEffort) | x | 2026-08-28 |
| TASK-002 | `thyca/onboarding.py`: `provider_ready`, `validate_provider` (GET /models, parse `data[].id`), `ProviderProbeError`, không log key | x | 2026-08-28 |
| TASK-003 | Route `GET /api/config` (schema + values, apiKey mask), `POST /api/config` (merge key rỗng, `_parse_dict`, save, 422 lỗi) | x | 2026-08-28 |
| TASK-004 | Route `POST /api/onboarding/verify` + `GET /api/config/status` | x | 2026-08-28 |
| TASK-005 | `Config`: thêm `provider.reasoningEffort` default `"high"`, choice `low/medium/high`, validate trong `__post_init__` | x | 2026-08-28 |
| TASK-006 | `OpenAIChat.chat`: `reasoningEffort` set → thêm `reasoning_effort` vào payload; khi provider trả 400 và body có `reasoning_effort` → retry đúng 1 lần không có param, giữ nguyên behavior khác | x | 2026-08-28 |

### GOAL-002: Frontend settings panel

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-101 | `index.html`: `<dialog id="settings-dialog">`; `css/settings.css`; bind `#settings-btn` | x | 2026-08-28 |
| TASK-102 | `js/settings.js`: generic renderer từ schema (text/number/secret/choices/dict), `<datalist>` + nút verify cho provider.model | x | 2026-08-28 |
| TASK-103 | Save flow + reload khi ready; boot gate trong `js/app.js` (auto-open, disable composer) | x | 2026-08-28 |

### GOAL-003: Test + docs

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-201 | `tests/test_config_schema.py` → gộp vào `tests/test_serve_config.py`: schema khớp Config (mọi field hiện, type đúng, secret mask, choices) | x | 2026-08-28 |
| TASK-202 | `tests/test_onboarding.py`: probe parse 200 / schema sai / non-JSON / 401 / connection refused / bad url; `provider_ready`; `apply_provider` | x | 2026-08-28 |
| TASK-203 | `tests/test_serve_config.py`: GET/POST /api/config (merge key, validate 422), verify (stub /models server), status; key không xuất hiện trong GET sau khi POST | x | 2026-08-28 |
| TASK-204 | `tests/test_llm_openai_chat.py` thêm case: payload có `reasoning_effort` khi set; 400 invalid reasoning_effort → retry không param thành công | x | 2026-08-28 |
| TASK-205 | README: mục Quick start (flow sau curl install) + ghi chú `reasoningEffort` + panel Cài đặt schema-driven | x | 2026-08-28 |

## Test Plan

- `uv run pytest tests/test_config_schema.py tests/test_onboarding.py tests/test_serve_config.py -q`
- Full suite `uv run pytest -q` (hiện 342) phải pass.
- Thủ công: HOME tạm → `uv run thyca --serve` → mở webui → settings tự mở →
  điền baseUrl+key → "Tải model" → chọn → Lưu → reload vào chat. Sửa thử
  `limits.loopMax` vượt range → 422 với message rõ.

## Assumptions / Defaults

- UI tiếng Việt, khớp tokens.css hiện có.
- API key lưu `provider.apiKey` trong config.json (0600 sẵn có); GET không trả key.
- `GET /models` là chuẩn OpenAI-compatible; provider không có → gõ tay model.
- "Thinking map" đã chốt: `provider.reasoningEffort` default `"high"`, choice
  `low/medium/high`, chỉnh được trong settings UI (schema-driven). `OpenAIChat`
  gửi `reasoning_effort` lên Chat Completions; nếu provider từ chối (400 có
  "reasoning_effort" trong body — ví dụ gpt-4o / gpt-5-chat-latest) → tự rút
  param và retry 1 lần, không đổi config.
- UI settings tái dùng tối đa CSS hiện có (tokens.css, shell.css, composer.css
  field/hint pattern); css mới chỉ cho layout panel.
