# Contributor guide

Read the project context and working rules first:

1. `PROJECT_CONTEXT.md` — current runtime, canonical storage, data-safety, and verification status.
2. `AGENT_RULES.md` — permissions, workflow, and verified project constraints.
3. Relevant decisions under `decisions/`; read the L2 hybrid decision before changing memory contracts.
4. Finished plans live under `plans/done/`. No plan is in-progress now (2026-11-09). The canonical facts live in `PROJECT_CONTEXT.md` and the current code. Do not treat an archived plan as current guidance.
5. Relevant service or module plans only when they affect the task.

Phiên bản gói là `0.87.0.dev2`. Hãy đối chiếu với `pyproject.toml` trước khi trích dẫn. Lượt gốc và lượt con dùng chung `BotRuntime`. CLI đi qua `ChatApp.turn` với `--bot <bot_id>` (DM) hoặc `--group <group_id>` (nhóm). Hội thoại công khai chỉ lưu trong SQLite tại `thyca/conversations/store/` (`AdminStore`/`AgentStore`, schema v2, WAL). Mọi `/api/sessions*` nhận `404`. `WorkspaceStore` và `WorkspaceTimeline` theo từng bot (`thyca/bots/timeline.py`) là nguồn canonical cho thực thi. Lịch sử phòng công khai là bản tổng hợp gọn. Chi tiết span canonical xem qua Trace. Đừng suy ra hành vi hiện tại từ kế hoạch đã bị thay thế.

Run the approved focused checks for the change; run `uv run pytest -q` only when the task calls for the full gate. Preserve unrelated dirty files and untracked user artifacts. Do not access or recreate the user-authorized-reset `~/.thyca` data. Do not claim independent review or real-provider quality without evidence.
