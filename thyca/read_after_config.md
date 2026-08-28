# Hướng dẫn config Thyca (dành cho agent)

Bạn (agent) đang được gọi để giúp user cấu hình Thyca. Đọc file này trước khi
sửa `~/.thyca/config.json`. File được cài kèm thyca và lưu tại `~/.thyca/read_after_config.md`.

## Vị trí và định dạng

- File config: `~/.thyca/config.json` — JSON, thư mục `~/.thyca` mode 0700, file mode 0600.
- Sửa bằng tay hoặc qua WebUI (`thyca --serve` → nút Cài đặt, http://127.0.0.1:8765).
- Không commit config; nó chứa secrets.

## Các trường

```json
{
  "provider": {
    "baseUrl": "https://api.openai.com/v1",
    "apiKeyEnv": "THYCA_TOKEN",
    "model": "gpt-4o-mini",
    "reasoningEffort": "high",
    "apiKey": null
  },
  "mcpServers": {},
  "timeline": { "timezone": "<IANA zone của máy>" },
  "limits": { "loopMax": 200, "hotTailKB": 4, "contextTokens": 32000 },
  "pricing": {
    "<model>": { "input": 0.15, "cache": 0.075, "output": 0.60 }
  }
}
```

### provider

- `baseUrl`: OpenAI-compatible endpoint. Phải bắt đầu `http://` hoặc `https://`.
  Không thêm `/models` hay `/chat/completions` — thyca tự nối.
- `apiKey`: key trực tiếp trong config (ưu tiên cao hơn env). NULL/ruột = đọc env.
- `apiKeyEnv`: tên biến môi trường chứa key. Mặc định `THYCA_TOKEN` — đừng để
  `OPENAI_API_KEY` làm mặc định: dễ bị harness/công cụ khác quét và dùng trùng key.
- `model`: tên model đúng như provider trả trong `GET {baseUrl}/models`.
- `reasoningEffort`: `low` | `medium` | `high` (mặc định `high`). Thyca gửi
  `reasoning_effort` lên Chat Completions; model không hỗ trợ (ví dụ gpt-4o)
  sẽ tự được retry không có param — không cần xoá tay.

### timeline

- `timezone`: tên IANA (`Asia/Ho_Chi_Minh`, `Europe/Paris`…). Config mới tạo mặc
  định theo giờ hệ thống (`/etc/localtime`). Sai zone làm lệch toàn bộ memory/timeline.

### limits

- `loopMax` 1..200 — số vòng agent tối đa mỗi turn.
- `hotTailKB` 1..64 — nhớ nóng giữ lại cuối context.
- `contextTokens` 1000..200000 — trần ngữ cảnh gửi lên provider.

### pricing (optional, phục vụ tracing/cost)

- USD / 1M tokens, 3 loại bắt buộc: `input`, `cache` (cached input), `output`.
- Thiếu thì thyca dùng bảng builtin (`thyca/llm/pricing.py`); chỉ thêm model
  bạn thật sự dùng. Alias `cached_input` được đọc thành `cache`.

### mcpServers

- Mỗi server: `{ "command": "...", "args": [...], "env": {...} }` — stdio MCP.
- Tên server phải khớp `[A-Za-z0-9_-]+`.

## Quy tắc khi sửa

1. Đọc config hiện tại trước, chỉ đổi trường user yêu cầu.
2. Giữ nguyên mode file 0600 và không in key ra stdout/log.
3. Sau khi sửa, kiểm bằng: `thyca --version` (không lỗi parse) — hoặc mở WebUI
   panel Cài đặt và bấm "Tải danh sách model" để verify key + baseUrl + lấy model.
4. Không bao giờ đặt `apiKey` vào file khác ngoài `~/.thyca/config.json`.
