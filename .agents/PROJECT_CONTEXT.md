# Project context

`thyca-ai` 0.8.5.dev0 — harness trợ lý cá nhân (terminal + webui local). Cảm hứng pi (vòng lặp nhỏ, ít abstraction). Không phải coding agent, không clone OpenClaw/Hermes.

## Runtime (verified 2026-09-22)

Một process `thyca`. Entry: `uv run thyca --help` (`thyca/app/cli.py:main`).

- **CLI:** `-p` one-shot, REPL, `--continue` / `--session` / `--model` / `--debug`.
- **Serve:** `--serve` (mặc định `127.0.0.1:8765`), `--daemon`, `--stop`, `--port`. Code ở `thyca/serve/` (server/routes/bridge/sessions_api/config_api/trace/trace_api/turn_state/memory/daemon).
- **Config:** `~/.thyca/config.json` — `provider`, `mcpServers`, `timeline`, `limits`, optional `pricing` (USD / 1M tokens: `input` / `cache` / `output`; alias đọc `cached_input`). Secret chỉ qua `provider.apiKey` hoặc env `apiKeyEnv`.
- **Session:** `thyca/sessions/` (4 class SOLID + `wire.py`). JSONL dưới `~/.thyca/sessions/`.
- **Memory:** markdown là nguồn sự thật. ActiveMemory inject full `SOUL.md` / `USER.md` / `IDENTITY.md` + daily tail `hotTailKB`. L2 lexical (FTS5 + trigram, TTL) qua `MemoryFacade`; `memory_remember` ghi `memory/YYYY-MM-DD.md`. Không `MEMORY.md`.
- **LLM:** `ConnectFactory` → `OpenAIChat` (`openai_chat`) / `OpenAIResponses` (`openai_responses`, per-provider `api`). `ChatReply.usage` chuẩn hóa `prompt_tokens` / `cached_tokens` / `completion_tokens` / `total_tokens` (+ `reasoning_tokens?`); `ChatReply.model` echo từ provider. `thyca/llm/pricing.py` `cost_for` — unknown model → `None`. Google/Anthropic qua factory vẫn lỗi (chưa implement).
- **Loop:** `assemble → think → act → observe`. Think/Act đo `perf_counter`. Observe ghi `Message.meta` (`kind`, `round`, `model`, `latency_ms`, `usage`, `cost_usd`, `finish_reason`); tool message: `latency_ms` / `round`. Naming title chưa persist `kind: "naming"`.
- **Tools:** registry builtin `memory_remember|search|recent|get|forget|reinforce|update` + MCP stdio (`thyca/tools/mcp.py`). Skills index ở `thyca/skills/` (store + templates). Wire types (`Message`/`ToolCall`) ở `thyca/core/protocol.py` (leaf, stdlib-only).
- **WebUI:** `webui/` — Chat / Memories (Hồ sơ = USER.md rồi SOUL.md / IDENTITY.md) / Trace. API loopback `/api/sessions*`, `/api/memory*`, `/api/traces*`.

## Ngoài scope hiện tại

Telegram/Discord, subagent, plan mode, GUI popup, confirmation gate, ANN/vector DB, catalog hàng chục provider. Embedding runtime đã gỡ (580ae03).

## Tests

`uv run pytest -q`. Baseline 2026-09-22: **719 passed / 0 fail** (fail `test_debug_prints_prompt_flags` cũ không tái hiện — tools count đã khớp; nếu đỏ lại thì là regression).

Layout: `thyca/{agent,llm,config,memory,sessions,tools,skills,serve,app,core}/` — top-level chỉ còn `__init__.py` + `__main__.py`; entry serve qua `thyca/serve/`, chat/CLI qua `thyca/app/`.

Chi tiết plan: `plans/backend-solid-refactor.md` (in-progress, GOAL-004); history: `plans/done/thyca-harness-v1.md`, `plans/done/l2-memory-retrieval.md`, decision `2026-08-15-l2-hybrid-v1.md`.
