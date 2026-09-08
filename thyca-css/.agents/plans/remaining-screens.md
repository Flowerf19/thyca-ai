---
status: in-progress
created: 2026-09-07
last_updated: 2026-09-07
---

# Remaining Thyca mock screens

## Summary

Implement the five user-supplied reference screens in the existing static notebook UI: Cost, Usage, General settings, Memories, Trace. Reuse `styles.css` tokens/shell, preserve repaired book corners and chat flow. No dependencies, backend, real credentials, payment actions or production repository edits.

## Tasks

### GOAL-001: Shared presentation and navigation

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | Create `screens.css` shared UI primitives and document the per-agent file contract. | Yes | 2026-09-07 |
| TASK-002 | Add `navigation.js` accessible native-dialog navigation across six HTML routes, including existing chat. | Yes | 2026-09-07 |

### GOAL-002: Reference screens (three GPT-5.6 Luna subagents, disjoint files)

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-003 | `cost.html`, `cost.css`, `cost.js`: total, daily SVG chart, model breakdown, mock period change. | | |
| TASK-004 | `usage.html`, `usage.css`, `usage.js`: stacked daily bars, token/request switch, summary totals. | | |
| TASK-005 | `settings.html`, `settings.css`, `settings.js`: model selector and labelled range controls with live outputs. | | |
| TASK-006 | `memories.html`, `memories.css`, `memories.js`: searchable/filterable/sortable cards and local add/edit dialog. | | |
| TASK-007 | `trace.html`, `trace.css`, `trace.js`: selectable timeline, token panels, tool payloads, metadata and copy ID. | | |

### GOAL-003: Integration and verification

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-008 | Inspect all agent-written files; screenshot comparison at desktop 1536×1024 and mobile 375×812; verify actual child bounds at 320/375/414/768. | | |
| TASK-009 | Browser-test navigation, keyboard/dialog behavior, page controls, console errors and chat regressions; update README and context. | | |

## Test Plan

- `python -m http.server 4173 --bind 127.0.0.1`
- `node --test tests/scroll.test.cjs`
- Use installed browser automation tooling without adding project dependencies.
- All six routes load directly; menu links reach actual routes. Tab links unsupported by supplied screens are clearly disabled, never fake `href="#"`.
- Charts have accessible titles and readable legends. Native form controls are labelled. Dialogs support Escape and focus return. User-entered memory content uses textContent, not HTML interpolation.
- Mock controls do not claim a real save, charge, API request, or destructive server operation.
- Existing one backend hook comment remains the only backend integration marker.

## Assumptions

- Reference screenshots govern hierarchy and copy; existing shared typography/palette/corner fixes remain authoritative across screens.
- Three `openai-codex/gpt-5.6-luna` agents read their assigned reference images. Parent supplies shared CSS/visual specifications and performs browser/image QA.
- Reference metrics/dates are static sample data. No new pricing/billing screens or provider configuration flows beyond the five supplied views.
