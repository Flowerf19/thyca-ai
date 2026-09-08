---
status: done
created: 2026-09-08
last_updated: 2026-09-08
---

# Integrate the Thyca backend into the new UI

## Summary

Promote `thyca-css/` from a fixture-only mock to the backend-served WebUI. Its HTML/CSS remains the source of truth; real session, streaming chat, memory, trace, and provider/config APIs replace fixture data inside that UI. The legacy `webui/` tree is not restyled or used as the migration target.

Success means `thyca --serve` opens the new multipage UI, backend-owned screens render live data and mutations, secrets remain masked, static-only screens are honestly labelled, package builds include the new UI, and focused/full tests plus desktop/mobile browser smoke checks pass.

## Tasks

### GOAL-001: Correct the migration direction

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | ~~Copy the Thyca theme CSS into legacy `webui/`.~~ Superseded: this overlays the new theme on the old UI. | ✓ | 2026-09-08 |
| TASK-002 | ~~Adapt the legacy root shell to visually resemble the mock.~~ Superseded: the new UI must own the DOM. | ✓ | 2026-09-08 |
| TASK-003 | ~~Add compatibility CSS around the legacy SPA.~~ Superseded with TASK-002. | ✓ | 2026-09-08 |
| TASK-004 | ~~Keep fixture-only static screens outside the live UI path.~~ Superseded: `thyca-css/` is now the live UI path. | ✓ | 2026-09-08 |
| TASK-007 | Restore all partial edits made to legacy `webui/` before the clarification. | ✓ | 2026-09-08 |
| TASK-008 | Point source-checkout serving and wheel packaging at `thyca-css/`, while preserving the installed path `thyca/webui`. | ✓ | 2026-09-08 |

### GOAL-002: Bring backend behavior into the new UI

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-009 | Replace the chat fixtures in `thyca-css/index.html` with live session-list, session-detail, create-session, and streaming-turn behavior using the existing visual components. | ✓ | 2026-09-08 |
| TASK-010 | Replace memory fixtures in `thyca-css/memories.html` with `/api/memory/stats` data and wire supported update, reinforce, forget, and canonical mutations without inventing a create-memory API. | ✓ | 2026-09-08 |
| TASK-011 | Replace trace fixtures in `thyca-css/trace.html` with `/api/traces` and trace-detail data, including honest loading, empty, unavailable, and unknown-cost states. | ✓ | 2026-09-08 |
| TASK-012 | Connect `thyca-css/provider.html` to `/api/config` and `/api/onboarding/verify`; save only valid backend config payloads and never echo stored API keys. | ✓ | 2026-09-08 |
| TASK-013 | Update multipage navigation and provider-ready gating so an unconfigured install routes users to the new provider screen and returning to chat works without fixture fallbacks. | ✓ | 2026-09-08 |
| TASK-014 | Keep dashboard/cost/usage/general-preference screens clearly separated: derive data from trace/config endpoints where supported, otherwise label local-only controls instead of claiming persistence. | ✓ | 2026-09-08 |

### GOAL-003: Verify the production switch

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-005 | Update static asset/HTML contract tests for the new production entrypoint and add focused frontend integration tests for API mapping and safe rendering. | ✓ | 2026-09-08 |
| TASK-006 | Run focused backend/UI tests, full pytest, and browser smoke checks at desktop plus 320/375/414/768 widths; record unrelated baseline failures separately. | ✓ | 2026-09-08 |

## Test Plan

- `uv run pytest -q tests/test_serve_chat.py tests/test_serve_memory_stats.py tests/test_serve_config.py tests/test_serve_trace.py`
- Focused Node tests for session rendering, NDJSON stream decoding, memory payload mapping, trace payload mapping, and config persistence.
- `uv run pytest -q`
- Build a wheel and inspect that `thyca/webui/index.html`, all linked CSS/JS, and secondary HTML pages are present.
- Run the loopback server against seeded fake chat/memory/config data; smoke-test chat, session switching, memories, trace, and provider at desktop and 320/375/414/768 widths with no console errors or horizontal overflow.

## Assumptions

- “Kéo backend vào UI mới” means `thyca-css/` owns production HTML/CSS and receives API integration; legacy `webui/` remains untouched and can be removed only in a separately approved cleanup.
- The current backend routes are the contract. The frontend does not add fake session deletion, memory creation, or trace deletion where no endpoint exists.
- `dashboard.html`, `cost.html`, and `usage.html` may summarize existing trace data; they must not show frozen fixture numbers as live backend values.
- No dependency or framework is added. Google Fonts remain an optional remote enhancement; system fallbacks continue to work.
