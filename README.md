# Thyca

Thyca là harness trợ lý cá nhân chạy trong terminal, lấy cảm hứng từ pi: một process, một vòng lặp nhỏ, và năng lực đến từ tool thay vì framework agent. Thyca không phải coding agent và không có mục tiêu clone OpenClaw hoặc Hermes.

## Trạng thái hiện tại

**0.4.0.** CLI nối Agent Loop: `thyca -p`, REPL, `--continue` / `--session` / `--model`. Tools registry + `memory_*` + MCP stdio đã có. WebUI local: `thyca --serve` (mặc định `127.0.0.1:8765`, `--daemon` / `--stop` / `--port`) — Chat, Memories, Trace.

Config / Session JSONL / ActiveMemory / L2 lexical / LLM OpenAI-compat / loop 4 pha đều có test. `ChatReply.usage` chuẩn hóa token (prompt / cached / completion / total); `cost_for` tính USD từ bảng `pricing` (builtin + overlay config). Observe ghi `usage` / `cost_usd` / `latency_ms` vào `Message.meta` trên JSONL. Google/Anthropic connect vẫn stub.

Memory v1 là **L2 hybrid**: markdown dưới `~/.thyca` là nguồn sự thật; SQLite là index suy ra. Lexical (FTS5 + trigram) đã chạy; semantic/vector chỉ còn trong plan frozen — embedding runtime đã gỡ (580ae03). Daily index sau khi đóng ngày; `SOUL.md` / `USER.md` / `IDENTITY.md` luôn inject. `MEMORY.md` đã bỏ — `memory_remember` ghi `memory/YYYY-MM-DD.md`. Chi tiết: `.agents/plans/l2-memory-retrieval.md`, `.agents/decisions/2026-08-15-l2-hybrid-v1.md`.

## Prerequisites

- Linux là target chính.
- Python 3.14+
- `uv`
- API key của OpenAI-compatible provider cho LLM service: `OPENAI_API_KEY` hoặc env name tương ứng trong config.

## Quick start

```bash
curl -LsSf https://raw.githubusercontent.com/Flowerf19/thyca-ai/main/install.sh | sh
thyca --version
```

Đã có `uv`:

```bash
uv tool install --python 3.14 git+https://github.com/Flowerf19/thyca-ai.git
```

Upgrade: `uv tool upgrade thyca-ai` hoặc chạy lại `install.sh`.

Lần chạy CLI đầu tiên tạo `~/.thyca/config.json` nếu file chưa tồn tại. `ProviderCfg.api_key()` lấy `provider.apiKey` trong JSON trước, trống thì đọc `apiKeyEnv`. Không commit `~/.thyca` hoặc secrets vào repo.

### Development

```bash
uv sync
uv run thyca --help
uv run thyca --version
uv run thyca --serve --daemon   # webui http://127.0.0.1:8765
uv run pytest -q
```

## Configuration

Config mặc định dùng một provider OpenAI-compatible:

```json
{
  "provider": {
    "baseUrl": "https://api.openai.com/v1",
    "apiKeyEnv": "OPENAI_API_KEY",
    "model": "gpt-4o-mini"
  },
  "mcpServers": {},
  "timeline": { "timezone": "Asia/Ho_Chi_Minh" },
  "limits": { "loopMax": 200, "hotTailKB": 4, "contextTokens": 32000 },
  "pricing": {
    "gpt-4o-mini": { "input": 0.15, "cache": 0.075, "output": 0.60 }
  }
}
```

`pricing` là optional (USD / 1M tokens). Thiếu thì dùng bảng builtin trong `thyca/llm/pricing.py`. Alias đọc `cached_input` → ghi ra `cache`. API Python: `load()`, `ensure_default()`, `save()`. `ProviderCfg.api_key()`: `apiKey` JSON thắng `apiKeyEnv`; `apiKey` không hiện trong `repr`.

## Kiến trúc

Package flat `thyca/`, không `src/`. Module chính: `config.py`, `protocol.py`, `sessions/` (4 class SOLID), `memory/`, `llm/` (`llm_base` + `pricing` + `openai_chat` …), `agent/` (Assemble/Think/Act/Observe + `Stage`), `tools/` (registry + `memory_*` + MCP), `serve.py` + `webui/`.

Loop: `assemble → think → act → observe`. `Assemble` nhét `PromptManager.build` khi `hot` là `ActiveSnapshot`. `memory_remember` là writer duy nhất cho memory files; v1 không confirmation gate.

## Development và testing

`uv run pytest -q` là lệnh kiểm chứng chuẩn (≈319 passed). Baseline đã biết: `tests/test_cli.py::test_debug_prints_prompt_flags` (`tools=11` vs `tools=7`). Live provider/network không nằm trong unit suite. `uv sync --locked` phải tái tạo được môi trường.

Ngoài scope: Telegram/Discord, subagent, plan mode, confirmation gate, ANN/vector database, catalog hàng chục provider.

## Tài liệu agent

Đọc `.agents/README.md` trước. Plan đang chạy: `review-split-oversize.md` (tách file >400 dòng). Trace đã đóng: `.agents/plans/done/thyca-trace-notebook.md`, `thyca-trace-cost.md`.
