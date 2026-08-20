# Thyca agent docs

Đọc theo thứ tự này:

1. `PROJECT_CONTEXT.md` — trạng thái repo, kiến trúc và ranh giới v1
2. `AGENT_RULES.md` — quy tắc làm việc và approval gate
3. `decisions/2026-08-15-l2-hybrid-v1.md` — quyết định L2 hybrid thuộc v1
4. `plans/thyca-harness-v1.md` — plan tổng
5. `plans/thyca-agent-architecture.md` — wiring và thứ tự service
6. `plans/services/*.md` — contract thi công từng service
7. `plans/l2-memory-retrieval.md` — contract chi tiết cold retrieval

Runtime hiện có: CLI stub, Config, Session (`thyca/sessions/`), ActiveMemory (`thyca/memory/active.py`), L2 archive lexical + TTL lifecycle + facade (`thyca/tools/memory.py`), LLM (`thyca/llm/` — `ConnectFactory`/`OpenAIChat`/`PromptManager`), Agent Loop (`thyca/agent/` — 4 pha + `Stage`). Chưa có: Tools registry/builtin, MCP, CLI wiring. Đừng bịa command hoặc feature ngoài plan đã được duyệt.
