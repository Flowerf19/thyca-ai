---
status: done
created: 2026-09-18
last_updated: 2026-09-18
---

# Live thinking recovery

## Summary

Investigate and fix incomplete live thinking that becomes visible only after stopping a turn. Saved reasoning exists in session `2026-09-18T15-14-18_0e3f`; do not attribute this report to absent provider reasoning. Reproduce the actual event/UI lifecycle before claiming resolution.

Confirmed failure matching stop-only reveal: `followTurn` falls back to `watchRunning` on stream failure, but the poll discards saved transcript updates while `running=true`. Only stop/completion renders them. The follow finalizer also removes the live card's map entry while polling still owns the mounted card. Reproduced by executing the actual app lifecycle with mocked network/DOM.

Confirmed adjacent failure: `backend/api.js` awaits `requestAnimationFrame` after every NDJSON event. A paused frame scheduler stalls stream consumption after `turn.accepted`, even when reasoning and terminal events are already buffered.

Corrections shipped:

- `backend/api.js`: yield once per read chunk via timer; never gate on animation frames (tests/test_webui_stream.py, 8 variants, fails pre-fix).
- `app.js`: failed follow keeps polling ownership of the live card; `renderPolledProgress` syncs only newly persisted reasoning into the card (`chat-thinking.js sync()`), so no transcript rebuild, selection loss, or forced scroll per poll; terminal poll renders the full transcript; a retained card whose `started_at` changed is replaced instead of reused.
- Review status: api.js fix approved independently; app.js polling fix reviewed with 3 Important findings, all addressed (poll rebuild, stale retained card, test boundary). Concurrent-send ownership fixed separately: per-session `streamingSessions` Set replaces the single global id (tests/test_webui_concurrent_streams.py fails on the previous source). Verified in a real browser: follow GET interrupted by a fetch patch → fallback polls displayed saved reasoning while the turn was still running, full transcript after the terminal.

Residual: browser timer throttling under long background periods untested.

Normal single-turn session switching already resumes its clock in the lifecycle reproduction. No evidence of which transport failure occurred in the historical user session.

## Tasks

### GOAL-001: Restore live updates with regression evidence

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | Trace provider callbacks, transport, and session/card ownership; reproduce failure without paid provider calls. | Yes | 2026-09-18 |
| TASK-002 | Fix confirmed failure at its ownership point and add executable regression tests (not source-string assertions). | Yes | 2026-09-18 |
| TASK-003 | Run focused/full tests and independent review; document remaining runtime uncertainty. | Yes | 2026-09-18 |

## Test Plan

- Exercise NDJSON receipt with unavailable animation callbacks, reasoning events, completion/cancellation, split chunks, and Unicode.
- Exercise session switching while a turn runs; streamed deltas must update the mounted card and its clock must continue from the same start.
- Verify persisted transcript remains unchanged and no user turns/daemon are interrupted.

## Assumptions

- No provider/model changes, dependencies, daemon restart, or push.
- Keep fixes tied to reproduced failures; do not claim a standalone timer test validates session lifecycle.
