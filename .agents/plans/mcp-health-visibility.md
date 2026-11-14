---
status: done
created: 2026-11-13
last_updated: 2026-11-13
---

# MCP Health Visibility — Báo trạng thái MCP server lỗi cho Agent

## Summary

`thyca` hiện đã cô lập lỗi MCP khi khởi động qua `StartupDiagnostic` (`thyca/tools/mcp.py`) và giữ `ChatApp._sync_mcp` không làm sập turn. Tuy nhiên các lỗi này mới chỉ in ra `stderr` qua `report_spawn_diags` (`thyca/app/toolchain.py`): khi một MCP server khởi động lỗi, tool của server đó không được đăng ký nên agent không biết server đó có cấu hình hay đang hỏng.

Mục tiêu là giữ nguyên hành vi fail-soft, ghi nhận các MCP server khởi động lỗi trong `MCPManager`, và đưa tóm tắt an toàn vào `<mcp_status>` trong system prompt cho mọi bot ở mỗi turn (dùng chung cho WebUI, CLI, channels).

## Reuse

- `StartupDiagnostic` và `MCPManager` (`thyca/tools/mcp.py`) — thu thập lỗi trong `spawn_all` và `sync`.
- `ChatApp._sync_mcp` (`thyca/app/chat_app.py`) — đồng bộ MCP trước mỗi turn dưới `self._mcp_lock`.
- `BotTurnScope` -> `BotRuntime._run_bot_turn_detailed` -> `build_workspace_hot` -> `ActiveSnapshot` -> `PromptManager.build` — luồng lắp system prompt và tính `context_budget` chung.

## Accepted Decisions (confirmed 2026-11-13)

- **Phạm vi hiển thị:** Chỉ báo cho **Agent** qua `<mcp_status>` trong system prompt; chưa đụng WebUI / HTTP API.
- **Chính sách vòng đời & bot scope:** Chỉ giữ báo lỗi theo lần `spawn_all` / `sync` (không tự respawn server đang chạy bị chết giữa chừng); hiển thị `<mcp_status>` cho mọi bot khi có MCP server lỗi.
- **Tương thích & an toàn:** Rút gọn lỗi thành 1 dòng ngắn gọn (unwrap `BaseExceptionGroup` nếu có, không stack trace); khi không có server lỗi, `mcp_status` rỗng và `<mcp_status>` không xuất hiện trong prompt.

## Tasks

| ID | Task | Status | Notes |
|---|---|---|---|
| TASK-001 | `MCPManager` lưu `failed_servers()`, rút gọn lỗi (`_summarize_error`), xuất `prompt_summary()` via `format_mcp_status` | done | `thyca/tools/mcp.py` |
| TASK-002 | Nối `mcp_status` qua `ActiveSnapshot`, `PromptManager`, `BotTurnScope`, `WorkspaceContextBuilder`, `BotRuntime`, `ChatApp` | done | Covers WebUI, CLI, channels |
| TASK-003 | Bổ sung unit & integration tests (startup fail, recovery khi `sync` lại, gỡ khỏi config, prompt sạch khi không lỗi, `ChatApp` bơm `<mcp_status>` vào lượt chạy) | done | `tests/test_mcp.py`, `tests/test_llm_prompt_manager.py`, `tests/test_chat_app.py` |

## Verification

- Focused tests:
  - `.venv/bin/python -m pytest -q tests/test_mcp.py tests/test_llm_prompt_manager.py tests/test_workspace_context.py tests/test_chat_app.py tests/test_cli.py tests/test_serve_chat.py tests/test_bots_context.py` -> **244 passed** (2026-11-13)
- Regression check:
  - Full suite + `git diff --check`
