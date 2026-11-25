# Contributor guide

Read the project guidance in this order before planning, editing, or reviewing:

1. `FEATURE_MAP.md` — feature IDs, entrypoints/routes/CLI, owning modules, invariants, and verification flows.
2. `PROJECT_CONTEXT.md` — current runtime state at HEAD, canonical storage, data-safety constraints, and open gaps.
3. `AGENT_RULES.md` — project-specific release status, CHANGELOG rules, architectural boundaries, and accepted WebUI decisions.
4. Relevant decisions under `decisions/`; read `decisions/2026-08-15-l2-hybrid-v1.md` before changing memory contracts.
5. Archived plans live under `plans/done/`. Do not treat archived plans as current guidance; live code, tests, `FEATURE_MAP.md`, and `PROJECT_CONTEXT.md` are the source of truth.

## Current Package Snapshot

Phiên bản gói hiện tại là `0.88.0.dev4` (đồng bộ giữa `pyproject.toml`, `thyca/__init__.py`, `README.md` và `.agents/`). Lượt gốc và lượt con dùng chung `BotRuntime`. CLI đi qua `ChatApp.turn` với `--bot <bot_id>` (DM) hoặc `--group <group_id>` (nhóm). Hội thoại công khai chỉ lưu trong SQLite tại `thyca/conversations/store/` (`AdminStore`/`AgentStore`, schema v2, WAL). Mọi `/api/sessions*` nhận `404`. `WorkspaceStore` và `WorkspaceTimeline` theo từng bot (`thyca/bots/timeline.py`) là nguồn canonical cho thực thi.

## Workflow & Verification Gates

This repository follows the Inverted Trust Pyramid workflow:

1. **Tier 1 (0 errors):** `bash scripts/checks/check-tier1.sh` (runs `ruff check .`, `node --check`, boundary + version sync checks, `scripts/checks/check-no-weakened-tests.sh`, `scripts/checks/check-comment-ban.sh`, and `git diff --check`).
2. **Tier 2 (Live runtime proof + Verification Artifacts):** run focused tests and `uv run pytest -q` when required, exercise the real product flow (CLI/loopback HTTP/Node/CDP), export proof to `.agents/artifacts/manifest.md` (gitignored), and validate with `bash scripts/checks/check-artifacts.sh`.
3. **Tier 3 (Docs):** keep `FEATURE_MAP.md`, `PROJECT_CONTEXT.md`, and `AGENT_RULES.md` in sync with verified code.

Preserve unrelated dirty files and untracked user artifacts. Do not access or recreate `~/.thyca` data. Do not claim independent review or real-provider quality without evidence.
