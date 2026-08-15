# Thyca

Thyca là harness trợ lý cá nhân chạy trong terminal, lấy cảm hứng từ pi: một process, một vòng lặp nhỏ, và năng lực đến từ tool thay vì framework agent. Thyca không phải coding agent và không có mục tiêu clone OpenClaw hoặc Hermes.

## Trạng thái hiện tại

Repo mới ở giai đoạn skeleton. CLI và **Config service** đã có; các service Session, LLM, Tools, MCP, Memory và Agent Loop chưa được triển khai. Vì vậy `thyca -p "ping"` hiện chỉ báo harness chưa được wiring, chưa gọi provider thật.

Memory v1 đã được chốt là **L2 hybrid**: markdown dưới `~/.thyca` là nguồn sự thật; SQLite là index suy ra. Lexical retrieval dùng FTS5 và trigram, semantic retrieval dùng exact vector search + RRF khi agent yêu cầu. Daily memory chỉ được index sau khi đóng ngày; `SOUL.md`, `USER.md` và `MEMORY.md` là canonical sources luôn indexable. Chi tiết nằm ở `.agents/plans/l2-memory-retrieval.md` và quyết định `.agents/decisions/2026-08-15-l2-hybrid-v1.md`.

## Prerequisites

- Linux là target chính.
- Python 3.14+
- `uv`
- API key của OpenAI-compatible provider khi Agent Loop được wiring: `OPENAI_API_KEY` hoặc env name tương ứng trong config.

## Quick start

```bash
uv sync
uv run thyca --help
uv run thyca --version
uv run pytest -q
```

Lần chạy CLI đầu tiên tạo `~/.thyca/config.json` nếu file chưa tồn tại. Config không lưu API key raw; chỉ lưu tên environment variable (`apiKeyEnv`). Không commit `~/.thyca` hoặc secrets vào repo.

## Configuration

Config mặc định dùng một provider OpenAI-compatible:

```json
{
  "provider": {
    "baseUrl": "https://api.openai.com/v1",
    "apiKeyEnv": "OPENAI_API_KEY",
    "model": "gpt-4o-mini"
  },
  "embedding": {
    "provider": "local",
    "model": "harrier-q4"
  },
  "mcpServers": {},
  "timeline": { "timezone": "Asia/Ho_Chi_Minh" },
  "limits": { "loopMax": 10, "hotTailKB": 4, "contextTokens": 32000 }
}
```

API Python của config ở module level là `load()`, `ensure_default()` và `save()`. Provider secrets được resolve lúc gọi qua `ProviderCfg.api_key()`; embedding OpenAI yêu cầu `baseUrl` và `apiKeyEnv`.

## Kiến trúc dự kiến

Package dùng flat layout `thyca/`, không có `src/`. Vòng lặp sau khi hoàn tất sẽ là:

```text
CLI → Session → refreshed hot memory → LLM
                                  ↘ ToolRegistry → builtin/MCP/memory
```

Read-only tools có thể chạy đồng thời. Mutating calls phải serialize theo resource để không làm mất dữ liệu. `write` và `edit` không được ghi dưới `~/.thyca`; `memory_remember` là writer duy nhất cho memory files. v1 không có confirmation gate, nhưng `run(call)` là seam để thêm gate về sau.

## Development và testing

Runtime test hiện tập trung vào Config service. `uv sync --locked` phải cài được project cùng dev group; `uv run pytest -q` là lệnh kiểm chứng chuẩn. Config tests bao phủ first-run creation, schema/type validation, environment key resolution, file permissions, custom paths, locked atomic writes và lock failure. Live provider/network không nằm trong unit suite.

Khi thêm service mới, chỉ đánh dấu plan `in-progress` sau khi contract được duyệt. Task hoàn tất phải cập nhật plan lifecycle và có focused tests trước khi service sau dựa vào nó. Không dùng ad-hoc package cài ngoài `pyproject.toml` để làm bằng chứng; `uv sync --locked` phải tái tạo được môi trường test.

Ngoài scope v1: web/TUI, Telegram/Discord, subagent, plan mode, background memory watcher, ANN/vector database, provider catalog và confirmation gate. Nhạc/ảnh hoặc integration chuyên biệt đi qua MCP về sau, không thêm vào core trước khi vertical slice CLI chạy ổn.

## Tài liệu agent

Đọc `.agents/README.md` trước, sau đó `PROJECT_CONTEXT.md`, `AGENT_RULES.md`, các decision và plans. Chỉ implement service có plan được duyệt; Config là service đã hoàn thành hiện tại.
