# Thyca agent docs

Đọc theo thứ tự này:

1. `PROJECT_CONTEXT.md` — runtime, ranh giới, env
2. `AGENT_RULES.md` — an toàn, workflow, gotcha đã verify
3. `decisions/2026-08-15-l2-hybrid-v1.md` — L2 hybrid thuộc v1
4. Plan đang chạy: `plans/thyca-trace-cost.md` (persist usage/cost — GOAL-001/002/003 phần lớn xong; UI sổ nghe → `plans/thyca-trace-notebook.md`)
5. `plans/thyca-harness-v1.md` — plan tổng
6. `plans/services/*.md` — contract từng service

Runtime **0.4.0**: CLI (`thyca -p`, REPL, `--continue` / `--session` / `--model`), Config (kèm `pricing`), Session JSONL, ActiveMemory + L2 lexical, LLM OpenAI-compat (`normalize_usage` + `cost_for`), Agent Loop 4 pha (ghi `Message.meta` usage/cost/latency), Tools registry + `memory_*` + MCP stdio, WebUI `thyca --serve` (Chat / Memories / Trace).

Đừng bịa command hoặc feature. Evidence = tree hiện tại, không phải chat.
