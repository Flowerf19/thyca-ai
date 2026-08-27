---
status: done
created: 2026-08-21
last_updated: 2026-08-21
---

# Split static mock into top-level `webui`

## Summary

Move the static assistant UI out of the 589-line `web/mock/index.html` into a top-level `webui/` tree (not `web/webui/`) with split markup, CSS, and ES modules. Preserve current Chat / Memories / Trace behavior. Delete `web/mock/` after the move. No backend, no new dependency, no bundler.

Success: `python -m http.server --directory webui` serves the same UI; `web/mock` is gone; pytest still passes.

## Tasks

### GOAL-001: Split the static surface

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | Add `webui/index.html` (markup only) plus `css/{tokens,shell,workspace,composer,chrome,app}.css` | x | 2026-08-21 |
| TASK-002 | Move demo data and UI logic into `js/{data,state,dom,drawer,render,app}.js` as ES modules | x | 2026-08-21 |
| TASK-003 | Delete `web/mock/` after the new tree serves Chat / Memories / Trace | x | 2026-08-21 |

## Test Plan

- Parse `webui/index.html` with stdlib `HTMLParser`.
- `uv run pytest -q`.
- Confirm `web/mock` does not exist.
- `git diff --check`.

## Assumptions

1. Static files only. `type="module"` in the browser; no build step.
2. Demo content stays in `data.js`; no `thyca` Python import.
3. User explicitly asked to delete the mock after the move.
