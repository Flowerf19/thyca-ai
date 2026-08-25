---
status: accepted
created: 2026-08-25
last_updated: 2026-08-25
---

# Decision — bỏ MEMORY.md

## Decision

L2 không còn file `MEMORY.md`. `memory_remember` chỉ append `~/.thyca/memory/YYYY-MM-DD.md`. Hồ sơ bền nằm `USER.md` / `SOUL.md` / `IDENTITY.md` qua `write`/`edit`.

`MEMORY.md` trùng daily (cùng heading+bullet, cùng TTL) nên không có vai trò thứ ba. Leftover `~/.thyca/MEMORY.md` không bị xóa, không inject, không purge; `write`/`edit` vẫn deny path đó. Mở `MemoryFacade` `drop_source` index cũ.

## Consequences

- `ActiveSnapshot` không còn field `memory`; prompt không có `<memory>`.
- `target` trên `remember` bị gỡ.
- `session_id` L2 là `YYYY-MM-DD#entry`. `memory#` chỉ còn locate leftover forget.
- Decision 2026-08-15 (canonical gồm MEMORY.md) superseded ở điểm này.
