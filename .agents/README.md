# Thyca agent docs

Đọc theo thứ tự này:

1. `PROJECT_CONTEXT.md` — runtime, ranh giới, env
2. `AGENT_RULES.md` — an toàn, workflow, gotcha đã verify
3. `decisions/2026-08-15-l2-hybrid-v1.md` — L2 hybrid thuộc v1
4. `decisions/2026-08-28-skills-agent-skills-v1.md` — skills file-first, không tool mới
5. Plan đang chạy: `plans/review-split-oversize.md` (tách file >400 dòng — GOAL-001/002 xong, còn GOAL-003/004/005; Trace đã đóng: `plans/done/thyca-trace-cost.md` + `plans/done/thyca-trace-notebook.md`)
6. `plans/thyca-harness-v1.md` — plan tổng
7. `plans/services/*.md` — contract từng service (skills: `plans/services/skills.md`, done 2026-08-28)

Runtime **0.5.0**: CLI (`thyca -p`, REPL, `--continue` / `--session` / `--model`), Config (kèm `pricing`), Session JSONL, ActiveMemory + L2 lexical + Skills index, LLM OpenAI-compat (`normalize_usage` + `cost_for`), Agent Loop 4 pha (ghi `Message.meta` usage/cost/latency), Tools registry + `memory_*` + MCP stdio, WebUI `thyca --serve` (Chat / Memories / Trace).

Đừng bịa command hoặc feature. Evidence = tree hiện tại, không phải chat.
