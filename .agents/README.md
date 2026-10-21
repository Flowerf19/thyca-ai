# Contributor guide

Read the project context and working rules first:

1. `PROJECT_CONTEXT.md` — current runtime, canonical storage, data-safety, and verification status.
2. `AGENT_RULES.md` — permissions, workflow, and verified project constraints.
3. Relevant decisions under `decisions/`; read the L2 hybrid decision before changing memory contracts.
4. Relevant plans under `plans/`. `plans/bot-workspace-channels.md` contains historical workstream evidence; its **Current canonical-only hard cut** section is authoritative. Migration/shadow plans and their old “waiting for promotion” status are superseded.
5. Relevant service or module plans only when they affect the task.

The package version is `0.86.5.dev0` (verify against `pyproject.toml`). Root and child turns use the shared `BotRuntime`; CLI adapts through `ChatApp.turn`. `WorkspaceStore` and per-bot `WorkspaceTimeline` are authoritative. Public room history is compact; canonical span detail is available through Trace. Do not infer current behavior from superseded plans.

Run the approved focused checks for the change; run `uv run pytest -q` only when the task calls for the full gate. Preserve unrelated dirty files and untracked user artifacts. Do not access or recreate the user-authorized-reset `~/.thyca` data. Do not claim independent review or real-provider quality without evidence.
