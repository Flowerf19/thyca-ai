---
status: done
created: 2026-08-25
last_updated: 2026-08-25
---

# Drop MEMORY.md — L2 chỉ daily

## Summary

`MEMORY.md` là daily không ngày: cùng `memory_remember`, cùng heading+bullet, cùng TTL. User đã ghi mem ra `memory/YYYY-MM-DD.md`. Bỏ file này khỏi product.

Không xóa `~/.thyca/MEMORY.md` (user data; Worldfone chỉ nằm đó). Không index, không inject, không tạo, không purge file đó.

`memory_remember` chỉ ghi daily hôm nay. Bỏ `target`.

## Tasks

### GOAL-001: Runtime không còn MEMORY.md

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | ActiveMemory: không tạo/đọc `MEMORY.md`. Gỡ `ActiveSnapshot.memory`. Prompt không còn `<memory>` | x | 2026-08-25 |
| TASK-002 | `remember` chỉ daily; bỏ `target`. Writer/archive/stats không index/purge `MEMORY.md`. `locate(memory#)` giữ cho leftover forget | x | 2026-08-25 |
| TASK-003 | PathGuard vẫn deny `MEMORY.md` (không để agent tái tạo kho). Tool copy bỏ MEMORY | x | 2026-08-25 |
| TASK-004 | WebUI: bỏ page `MEMORY.md` / `memory#`. Mock `data.js` ghi daily | x | 2026-08-25 |
| TASK-005 | Tests chuyển fixture/assert sang daily đã đóng (search) hoặc today (stats/get) | x | 2026-08-25 |
| TASK-006 | PROJECT_CONTEXT, memory README, root README, AGENT_RULES, decision | x | 2026-08-25 |

## Test Plan

- `uv run pytest -q` — nhớ mặc định ra `memory/{today}.md`; `ensure_files` không tạo `MEMORY.md`; search không hit leftover MEMORY.md; prompt không có `<memory>`.
- Fixture search dùng daily `timeline_day < today`.

## Assumptions

1. Không migrate Worldfone vào daily. User tự xử file leftover.
2. `SOUL.md` / `USER.md` / `IDENTITY.md` không đổi.
3. `memory#` leftover: forget/read_session vẫn locate được; không hiện stats/search.
4. Historical plans (`l2-memory-retrieval.md`, harness-v1) giữ nguyên — chúng là lịch sử.
