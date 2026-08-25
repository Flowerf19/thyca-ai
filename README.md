# Thyca

Thyca là harness trợ lý cá nhân chạy trong terminal, lấy cảm hứng từ pi: một process, một vòng lặp nhỏ, và năng lực đến từ tool thay vì framework agent. Thyca không phải coding agent và không có mục tiêu clone OpenClaw hoặc Hermes.

## Trạng thái hiện tại

Repo đang trong giai đoạn xây dựng v1. Đã có và có test: **Config**, **Session**, **ActiveMemory**, L2 archive lexical (FTS5 + trigram, TTL lifecycle, `MemoryFacade`), **LLM** (`ConnectFactory` + `OpenAIChat` + `PromptManager`) và **Agent Loop** (Assemble/Think/Act/Observe + `Stage`). CLI đã nối loop: `thyca -p`, REPL, `--continue` / `--session` / `--model`. Chưa có: Tools registry/builtin, MCP. Trạng thái mỗi service theo checklist trong `.agents/plans/thyca-agent-architecture.md`.

Memory v1 đã được chốt là **L2 hybrid**: markdown dưới `~/.thyca` là nguồn sự thật; SQLite là index suy ra. Lexical retrieval (FTS5 + trigram) **đã chạy**; semantic retrieval (exact vector + RRF) chỉ tồn tại trong kiến trúc frozen của plan — embedding runtime đã được gỡ (580ae03) nên `semantic=true` chưa có model, không reintroduce như đã implement. Daily memory chỉ được index sau khi đóng ngày; `SOUL.md` và `USER.md` luôn indexable. `MEMORY.md` đã bỏ — `memory_remember` chỉ ghi `memory/YYYY-MM-DD.md`. Chi tiết nằm ở `.agents/plans/l2-memory-retrieval.md` và quyết định `.agents/decisions/2026-08-15-l2-hybrid-v1.md`.

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
  "limits": { "loopMax": 200, "hotTailKB": 4, "contextTokens": 32000 }
}
```

API Python của config ở module level là `load()`, `ensure_default()` và `save()`. Provider secrets được resolve lúc gọi qua `ProviderCfg.api_key()`: giá trị `apiKey` trong JSON thắng nếu có, nếu không mới đọc `apiKeyEnv`; `apiKey` không hiện trong `repr`. Cấu hình embedding model đã được gỡ khỏi config cùng embedding runtime.

## Kiến trúc dự kiến

Package dùng flat layout `thyca/`, không có `src/`. Các module chính đã có: `config.py`, `protocol.py` (canonical `Message`/`ToolCall`/`ToolResult`), `sessions/` (4 class SOLID), `memory/` (active + archived/chunk + writer + heading), `llm/` (`llm_base`/`llm_factory`/`openai_chat`/`openai_responses`/`google_chat`/`anthropic_chat`/`prompt`), `agent/` (Assemble/Think/Act/Observe + `Stage`), `tools/memory.py` (facade `memory_*`).

Vòng lặp agent là `assemble → think → act → observe` trên một `Stage`; `AgentLoop.run` (loop.py) giữ `SessionManager.current`, `Assemble` nhét `PromptManager.build` khi `hot` là `ActiveSnapshot`. Tools registry/builtin và MCP chưa có — `ToolDispatcher` trong `Act` là port cho registry sau này. `memory_remember` là writer duy nhất cho memory files; v1 không có confirmation gate.

## Development và testing

Runtime tests (`uv run pytest -q`, 83 tests) phủ Config, Session, ActiveMemory, L2 archive lexical + TTL lifecycle, LLM factory/OpenAI chat/prompt, và agent loop 4 pha. `uv sync --locked` phải cài được project cùng dev group; `uv run pytest -q` là lệnh kiểm chứng chuẩn. Live provider/network không nằm trong unit suite.

Khi thêm service mới, chỉ đánh dấu plan `in-progress` sau khi contract được duyệt. Task hoàn tất phải cập nhật plan lifecycle và có focused tests trước khi service sau dựa vào nó. Không dùng ad-hoc package cài ngoài `pyproject.toml` để làm bằng chứng; `uv sync --locked` phải tái tạo được môi trường test.

Ngoài scope v1: web/TUI, Telegram/Discord, subagent, plan mode, background memory watcher, ANN/vector database, provider catalog và confirmation gate. Nhạc/ảnh hoặc integration chuyên biệt đi qua MCP về sau, không thêm vào core trước khi vertical slice CLI chạy ổn.

## Tài liệu agent

Đọc `.agents/README.md` trước, sau đó `PROJECT_CONTEXT.md`, `AGENT_RULES.md`, các decision và plans. Chỉ implement service có plan được duyệt (`status: in-progress`); trạng thái hiện tại theo checklist trong `.agents/plans/thyca-agent-architecture.md` — Config/Session/Memory done, LLM in-progress, Tools/MCP draft.
