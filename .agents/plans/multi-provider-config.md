---
status: done
created: 2026-09-21
last_updated: 2026-09-21
---

# Multi-provider + multi-model config

## Summary

Hôm nay Thyca chỉ có đúng 1 provider dùng chung: `provider.{baseUrl,apiKey}` + `models[id].baseUrl=""` nghĩa là "kế thừa global"
(`thyca/config.py`). Trang Provider khi lưu không snapshot endpoint vào từng model
(`thyca/webui/provider.js::collectValues`, dòng `baseUrl: values.models[model]?.baseUrl || ""`),
và chat gửi per-turn override chỉ gồm `{model, effort}` (`thyca/webui/app.js::composerTurn`),
backend chỉ thay `provider.model` (`thyca/chat_app.py::overlay_turn_cfg`).
Hậu quả đã tái hiện live: đổi model sang `z-ai/glm-5.3-flash` trong khi endpoint là `api.meta.ai`
→ provider trả `404 model_not_found`, UI chỉ hiện "Không gửi được — thử lại."
(`SEND_ERROR_STATUS`), server không log gì (`~/.thyca/serve.log` chỉ có dòng bind URL).

Plan này làm màn hình config model/provider lại cho đúng:

- Nhiều provider, mỗi provider có `baseUrl` + key riêng.
- Mỗi model thuộc về 1 provider; đổi model trong chat thì endpoint + key đi theo model.
- Lỗi provider hiện message thật ở UI và có 1 dòng log ở server (đúng complaint gốc).
- Config cũ (1 provider global) migrate tự động, không mất key/giá/limits.

**Không làm:** non-OpenAI protocol mới (Anthropic/Google Chat class giữ nguyên, factory không đổi),
per-session sticky model, attach/voice, sửa pricing engine.

## Tasks

### GOAL-001: Schema config nhiều provider (backend, chưa UI)

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | Thêm `ProviderEntry`: `{baseUrl, apiKeyEnv, apiKey, reasoningEffort}` (tách từ `ProviderCfg`, giữ nguyên `api_key()` gồm `!cmd`); `Config` thêm `providers: dict[str, ProviderEntry]` + `defaultModel: str`; `ModelCfg` thêm `provider: str = ""` (rỗng = provider mặc định). Thứ tự resolve endpoint: `models[id].baseUrl` (giữ escape hatch 0.8.2) → `providers[models[id].provider].baseUrl` → provider mặc định. | x | 2026-09-21 |
| TASK-002 | Migrate khi load: config cũ không có `providers` → `providers["default"]` từ block `provider` cũ, `defaultModel = provider.model`, mọi model thiếu `provider` → `"default"`. `to_dict()` ghi shape mới; block `provider` cũ không ghi lại (đọc vẫn chấp nhận để tương thích). | x | 2026-09-21 |
| TASK-003 | Thay `effective_provider()` bằng `effective_provider_for(model_id) -> (ProviderEntry, model_id)` dùng cho turn path; giữ `effective_provider()` như alias cho `defaultModel` để CLI/legacy không vỡ. Model lạ (không phải default, không trong `models`) → `InvalidTurnOption("invalid model")` như hiện nay. | x | 2026-09-21 |
| TASK-004 | Validate: `providers` không rỗng; key match `[A-Za-z0-9_-]+` (giống `mcpServers`); `models[id].provider` (khi non-empty) phải tồn tại; `defaultProvider` phải tồn tại; `defaultModel` non-empty, ĐƯỢC PHÉP chưa đăng ký (giữ hành vi `test_config_post_last_model_keep_default`: unregistered → resolve qua default provider). | x | 2026-09-21 |

### GOAL-002: Turn path resolve provider theo model

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-005 | `chat_app.overlay_turn_cfg` + `_run_turn`: từ `model` override tìm `ModelCfg.provider` → dựng `ProviderCfg` tương đương (baseUrl/key/effort của provider đó + model đã chọn) rồi `ConnectFactory.create` như cũ. `AgentLoop(model=…)` giữ id model để trace/pricing. | x | 2026-09-21 |
| TASK-006 | CLI `--model` (`cli.py:119`) resolve cùng hàm với turn path (không chỉ `replace(model=…)`), để `-p --model <id-khác-provider>` gọi đúng endpoint. `--debug` in thêm provider id + baseUrl (không in key). | x | 2026-09-21 |
| TASK-007 | Backward-compat: config chỉ có 1 provider thì mọi hành vi cũ giữ nguyên (test cũ phải xanh không sửa, trừ khi assert shape `to_dict`). | x | 2026-09-21 |

### GOAL-003: API config + verify theo provider

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-008 | `GET /api/config`: `values.providers[*].apiKey` đều mask `""`; `meta.hasApiKey` thành `meta.providers: {id: bool}` (giữ `hasApiKey` = default provider có key, để onboarding cũ không vỡ). | x | 2026-09-21 |
| TASK-009 | `POST /api/config`: merge key đã lưu theo từng provider id (`apiKey == ""` → giữ key cũ của đúng id đó, không bao giờ copy chéo). Save giữ permissive như cũ (không enforce key-resolvable, để edit limits khi chưa có key vẫn 200 + `ready:false`); thiếu key được phát hiện bởi test-after-save (GOAL-008). | x | 2026-09-21 |
| TASK-010 | `POST /api/onboarding/verify` nhận thêm `providerId?`: nếu có và `apiKey` rỗng thì dùng key đã lưu của provider đó (song song với fallback hiện tại ở `serve.py`). Không log key; message lỗi giữ nguyên văn hiện tại. | x | 2026-09-21 |

### GOAL-004: Màn Provider quản lý nhiều provider + model

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-011 | Layout: danh sách provider (thêm/đổi tên/xóa) + panel chi tiết 1 provider: `baseUrl`, key, nút "Kiểm tra & tải model" (gọi verify với đúng key của provider đó), danh sách model của provider. Model thêm bằng 2 cách: chọn từ `/models` vừa verify, hoặc nhập tay ID. | x | 2026-09-21 |
| TASK-012 | Mỗi model: thinking map + effort mặc định, limits override, giá input/cache/output (tái dùng validation hiện tại: đủ cả 3 hoặc trống cả 3). Model lưu mới tự gán `provider` = provider đang xem; không cho model "mồ côi". | x | 2026-09-21 |
| TASK-013 | Xóa/đổi tên provider: xóa provider còn model → chặn, báo số model, yêu cầu chuyển model sang provider khác (dropdown) hoặc xóa model trước. Đổi tên id → cập nhật mọi `models[id].provider` trong cùng 1 lần save. Xóa model đang là default → bắt chọn default khác trước. | x | 2026-09-21 |
| TASK-014 | Onboarding `provider.html?required=1` vẫn chạy với shape mới: lần đầu (chưa provider nào có key) tạo provider `default`, flow key → verify → pick model → save như cũ. | x | 2026-09-21 |
| TASK-015 | Sau save thành công: giữ popup "Provider đã sẵn sàng" hiện tại; status ghi rõ `Đã lưu · <n> provider · <m> model · default: <id>`. | x | 2026-09-21 |

### GOAL-005: Chat composer theo provider

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-016 | Dropdown model group theo provider (`<optgroup label="<providerId> — <baseUrl host>">`), giá trị option vẫn là model id (body `{model}` không đổi). Model default từ `defaultModel`. | x | 2026-09-21 |
| TASK-017 | Đổi model trong composer chỉ refresh effort choices (giữ hành vi hiện tại), không ghi config. Effort choices lấy từ `models[id]` của model đang chọn (đã có `effortChoicesFor`). | x | 2026-09-21 |

### GOAL-006: Nhìn thấy lỗi provider (complaint gốc)

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-018 | Server log: `bridge.py` khi turn failed (cả `/turn` và NDJSON `turn.failed`, kể cả pre-accept) in 1 dòng stderr: `turn failed session=<id> model=<id> provider=<id> code=<code> msg=<redacted…>` (dùng text đã redact/cap của `LLMError`; không in key/path/traceback). Ráp vào `serve.log` hiện có. | x | 2026-09-21 |
| TASK-019 | UI: `app.js` catch ở `sendMessage`/`retryMessage` hiện message thật từ `ApiError` (đã có `terminal.message` từ backend) thay vì chỉ `SEND_ERROR_STATUS` chung chung. Giữ dòng ngắn: `Không gửi được — <message>`; message đã bị backend cap 500 ký tự. | x | 2026-09-21 |
| TASK-020 | ~~Turn failed để lại trace row có `error`.~~ Làm ở GOAL-009 (stamp `meta.error` vào user message, không thêm message mới). | x | 2026-09-21 |

### GOAL-008: Tự test API sau khi lưu config

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-023 | `onboarding.test_chat(base_url, api_key, model, timeout)`: POST `/chat/completions` 1 message "ping", `stream:false`, timeout ~20s; trả `{model, latency_ms}` hoặc raise `ProviderProbeError` với message đã redact (tái dùng pattern của `validate_provider`). | x | 2026-09-21 |
| TASK-024 | `POST /api/providers/test {providerId, model?}`: resolve đúng provider + key đã lưu (rỗng key → 422), gọi `test_chat`, trả `{ok, latencyMs, model}` hoặc `{error}`; server log 1 dòng stderr mỗi lần test (ok/fail, không key). | x | 2026-09-21 |
| TASK-025 | Provider page: sau save thành công tự gọi test cho default model, hiện `Đã test API: OK (<latency>)` hoặc message lỗi; panel mỗi provider có nút "Test" gọi cùng endpoint cho 1 model của provider đó. Test fail không rollback save (save vẫn giữ), chỉ báo đỏ. | x | 2026-09-21 |

### GOAL-007: Tương thích + docs

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-021 | Roundtrip test: config 0.8.5 (1 provider + models `baseUrl=""` + 1 model `baseUrl` riêng) load → save → load: key/limits/giá/thinking map nguyên vẹn; model `baseUrl` riêng vẫn gọi đúng host cũ. | x | 2026-09-21 |
| TASK-022 | Cập nhật `CHANGELOG.md` (mục Unreleased) + `thyca/read_after_config.md` nếu nó mô tả shape config (kiểm tra trước, chỉ sửa phần lệch). | x | 2026-09-21 |

### GOAL-009: Mark error lên turn failed (làm nốt TASK-020)

Thiết kế (chốt sau khi đọc code): không thêm message mới (tránh đổi status/rounds/render + nguy cơ `content:null` lọt vào prompt gửi provider). Thay vào đó stamp `{code, message}` vào `meta.error` của user message của turn (rewrite file, best-effort, không bao giờ mask lỗi gốc). Trace đọc marker này. Cancel không mark. Retry strip marker cũ.

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-026 | `SessionManager.mark_turn_error(code, message)`: stamp `meta.error` vào user message cuối + rewrite; False khi chưa có user. `truncate_to_last_user` strip marker khỏi user message được giữ (kể cả no-op tail). | x | 2026-09-21 |
| TASK-027 | `ChatApp._run_turn`: bọc `loop.run` — `CancelledError` re-raise không mark; `LLMError` mark `(llm_error, str)`; Exception khác mark `(chat_unavailable, chat unavailable)`; best-effort. | x | 2026-09-21 |
| TASK-028 | `trace.py`: slice có `meta.error` → status `failed`; `TurnSummary.error` + `to_payload["error"]`. | x | 2026-09-21 |
| TASK-029 | `trace.js`: dòng lỗi dưới description của turn failed + CSS `.trace-turn-error` (đỏ `accent-deep`). | x | 2026-09-21 |
| TASK-030 | Test: mark persist + reload; truncate strip; BoomLLM turn → user meta có error + `trace_list` có error; retry-after-failure không còn marker cũ khi turn mới chạy. | x | 2026-09-21 |
| TASK-031 | Full pytest xanh (674) + smoke live (trace API có field `error`, daemon healthy). Marking qua HTTP chứng minh bằng integration test (không phá config live để gây lỗi thật). | x | 2026-09-21 |

## Test Plan

- Unit config (`tests/test_config.py` style): migrate cũ→mới; `effective_provider_for` resolve đúng provider/endpoint/key/effort; model lạ → lỗi; provider id sai charset → `ConfigError`; model trỏ provider không tồn tại → `ConfigError`; roundtrip không mất key (`repr` không lộ key).
- Turn (`tests/test_chat_app.py`, `test_turn_stream.py` style): 2 provider fake (http server stub khác port, khác key) + 2 model; gửi turn model A rồi model B → assert mỗi turn tới đúng port với đúng `Authorization`; model không đăng ký → 400 `invalid model`.
- API (`tests/test_serve_config.py` style): GET mask mọi key; POST `apiKey:""` giữ đúng key từng provider, không copy chéo; POST xóa default model → 422; verify với `providerId` + key rỗng dùng key đã lưu.
- CLI: `-p --model <id-provider-B>` tới đúng endpoint (stub), `--debug` có provider id, không lộ key.
- GOAL-006: stub trả 404 `model_not_found` → assert 1 dòng stderr có `code=llm_error` + `msg` chứa `model_not_found`, không chứa key; NDJSON terminal `turn.failed` message giữ nguyên để UI hiện.
- UI (manual checklist, không thêm e2e framework): tạo 2 provider → verify từng cái → thêm model → đặt default → chat đổi model qua lại đều xanh; xóa provider còn model bị chặn; reload giữ nguyên; onboarding required với config trắng.

## Assumptions

- Mọi provider đều OpenAI-compatible (`/models`, `/chat/completions`); không thêm protocol mới.
- 1 model thuộc đúng 1 provider; muốn cùng model id ở 2 endpoint thì đăng ký 2 tên khác nhau (vd `glm-meta`, `glm-zai`) — chấp nhận để tránh composite key `provider/model` ở mọi nơi (composer body, trace, pricing, CLI).
- Giữ `models[id].baseUrl` như override cấp cao nhất để không phá config 0.8.2 có `baseUrl` riêng; UI mới không expose field này (tránh 2 chỗ cùng quyết endpoint), chỉ backend + test biết.
- Key lưu trong `config.json` 0600 như hiện tại; chưa làm keyring/OS secret store.
- `pricing` legacy giữ nguyên (migrate → `models` đã có); không đụng pricing engine.
- TASK-020 là "best effort trong 1 task": nếu trace-failed-row phức tạp hơn dự kiến thì tách plan riêng, không chặn các GOAL khác.
