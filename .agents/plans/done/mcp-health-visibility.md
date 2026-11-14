---
status: done
created: 2026-11-13
last_updated: 2026-11-14
---

# MCP Health Visibility — Báo trạng thái MCP server lỗi cho Agent

## Summary

Slice đầu chỉ bơm lỗi spawn vào `<mcp_status>`. Đã mở rộng (2026-11-14): connect nền song song, stub/wait từ `last_tools`, package `thyca/tools/mcp/`. Nguồn chuẩn hiện tại: `.agents/PROJECT_CONTEXT.md` mục MCP runtime.

## Reuse (hiện tại)

- `thyca/tools/mcp/` — `process`, `catalog`, `manager` (`schedule` / `settle` / `sync`).
- `ChatApp._sync_mcp` — settle + upsert specs mỗi turn.
- `BotTurnScope` → hot snapshot → `PromptManager` — chỉ cold fail vào `<mcp_status>`.

## Accepted Decisions

- Agent thấy lỗi qua stub tool khi đã có catalog; `<mcp_status>` chỉ cold fail.
- Boot không block chờ MCP; settle ở biên turn.
- Chưa đụng WebUI / HTTP health API; không auto-respawn process chết giữa phiên.
- CHANGELOG viết cho người dùng (xem `AGENT_RULES.md`), không nhật ký kỹ thuật.

## Tasks

| ID | Task | Status | Notes |
|---|---|---|---|
| TASK-001 | Cold-fail tracking + `prompt_summary` | done | `catalog` / `manager` |
| TASK-002 | Wire `<mcp_status>` qua hot snapshot | done | chỉ cold fail |
| TASK-003 | Background schedule/settle + stub/wait + package split | done | 2026-11-14 |
| TASK-004 | Tests focused MCP/chat/cli/registry | done | 146 passed trên nhóm đó |

## Verification

- Focused: `tests/test_mcp.py` + chat/cli/registry/prompt liên quan → **146 passed** (2026-11-14).
- Full suite / force-push origin: theo lệnh user sau docs.
