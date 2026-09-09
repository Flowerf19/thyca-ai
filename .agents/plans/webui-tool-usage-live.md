---
status: done
created: 2026-09-08
last_updated: 2026-09-08
---

# Summary

Show live tool activity in the chat UI, aggregate completed tool usage by display name, and use a pen icon for tool rows.

### GOAL-001: Stream and aggregate tool usage

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | Track active and completed tool/skill events in the live chat card. | ✓ | 2026-09-08 |
| TASK-002 | Render counts such as `bash ×5` and group memory tools as `memories`. | ✓ | 2026-09-08 |
| TASK-003 | Replace the terminal icon with a pen icon and update styles. | ✓ | 2026-09-08 |

## Test Plan

- Run focused JavaScript/stream tests.
- Run `uv run pytest -q`.
- Verify the working tree diff and do not push.

## Assumptions

- Tool counts use completed calls for live state and all assistant tool calls for persisted conversation rendering.
- Memory tool names (`memory_*`) display as `memories`.
- Skills remain visible as named active/completed entries.
