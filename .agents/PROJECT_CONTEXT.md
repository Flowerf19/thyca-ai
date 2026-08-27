# Project context

`thyca-ai` 0.5.2 — harness trợ lý cá nhân (terminal + webui local). Cảm hứng pi (vòng lặp nhỏ, ít abstraction). Không phải coding agent, không clone OpenClaw/Hermes.

## Runtime (verified 2026-08-27)

Một process `thyca`. Entry: `uv run thyca --help`.

- **CLI:** `-p` one-shot, REPL, `--continue` / `--session` / `--model` / `--debug`.
- **Serve:** `--serve` (mặc định `127.0.0.1:8765`), `--daemon`, `--stop`, `--port`.
- **Config:** `~/.thyca/config.json` — `provider`, `mcpServers`, `timeline`, `limits`, optional `pricing` (USD / 1M tokens: `input` / `cache` / `output`; alias đọc `cached_input`). Secret chỉ qua `provider.apiKey` hoặc env `apiKeyEnv`.
- **Session:** `thyca/sessions/` (4 class SOLID). JSONL dưới `~/.thyca/sessions/`.
- **Memory:** markdown là nguồn sự thật. ActiveMemory inject full `SOUL.md` / `USER.md` / `IDENTITY.md` + daily tail `hotTailKB`. L2 lexical (FTS5 + trigram, TTL) qua `MemoryFacade`; `memory_remember` ghi `memory/YYYY-MM-DD.md`. Không `MEMORY.md`.
- **LLM:** `ConnectFactory` → `OpenAIChat`. `ChatReply.usage` chuẩn hóa `prompt_tokens` / `cached_tokens` / `completion_tokens` / `total_tokens` (+ `reasoning_tokens?`); `ChatReply.model` echo từ provider. `thyca/llm/pricing.py` `cost_for` — unknown model → `None`. Google/Anthropic vẫn `NotImplementedError`.
- **Loop:** `assemble → think → act → observe`. Think/Act đo `perf_counter`. Observe ghi `Message.meta` (`kind`, `round`, `model`, `latency_ms`, `usage`, `cost_usd`, `finish_reason`); tool message: `latency_ms` / `round`. Naming title chưa persist `kind: "naming"`.
- **Tools:** registry builtin `memory_remember|search|recent|get|forget|reinforce|update` + MCP stdio (`thyca/tools/mcp.py`).
- **WebUI:** `webui/` — Chat / Memories (Hồ sơ = USER.md rồi SOUL.md / IDENTITY.md) / Trace. API loopback `/api/sessions*`, `/api/memory*`, `/api/traces*`.

## Ngoài scope hiện tại

Telegram/Discord, subagent, plan mode, GUI popup, confirmation gate, ANN/vector DB, catalog hàng chục provider. Embedding runtime đã gỡ (580ae03).

## Tests

`uv run pytest -q`. Baseline đã biết: `tests/test_cli.py::test_debug_prints_prompt_flags` fail vì `tools=11` vs `tools=7` — không phải regression của pricing.

Chi tiết plan: `.agents/plans/thyca-harness-v1.md`, `l2-memory-retrieval.md`, decision `2026-08-15-l2-hybrid-v1.md`. Trace cost: `plans/thyca-trace-cost.md`. UI sổ nghe: `plans/thyca-trace-notebook.md`.
