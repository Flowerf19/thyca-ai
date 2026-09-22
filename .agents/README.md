# Thyca agent docs

Đọc theo thứ tự này:

1. `PROJECT_CONTEXT.md` — runtime, ranh giới, env
2. `AGENT_RULES.md` — an toàn, workflow, gotcha đã verify
3. `decisions/2026-08-15-l2-hybrid-v1.md` — L2 hybrid thuộc v1
4. `decisions/2026-08-28-skills-agent-skills-v1.md` — skills file-first, không tool mới
5. Plans refactor đã done cả 2 (2026-09-22, chờ merge main): `plans/backend-solid-refactor.md` (nhánh `refactor/backend-solid`, 8 modules) + `plans/webui-solid-refactor.md` (nhánh `refactor/webui-solid`, 6 modules); plans cũ ở `plans/done/`
6. `plans/modules/{M<n>,W<n>}-<name>.md` — plan từng module (đã done cả 14, kèm close-out evidence)
7. `plans/services/*.md` — contract từng service

Runtime **0.8.5.dev0**: CLI (`thyca/app/cli.py`: `-p`, REPL, `--continue` / `--session` / `--model`), Config (multi-provider + `auth.json`, `reasoningEffort`), Provider/Settings WebUI (schema-driven, fetch models, onboarding), Session JSONL, ActiveMemory + L2 lexical + Skills index (`thyca/skills/`), LLM OpenAI-compat chat + responses (`normalize_usage` + `cost_for`), Agent Loop 4 pha (ghi `Message.meta` usage/cost/latency), Tools registry + `memory_*` + MCP stdio, `thyca/core/protocol.py` wire types, WebUI `thyca --serve` (`thyca/serve/`: Chat / Memories / Trace / Dashboard).

Đừng bịa command hoặc feature. Evidence = tree hiện tại, không phải chat.
