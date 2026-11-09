---
status: in-progress
created: 2026-10-25
last_updated: 2026-10-25
---

# Usage bot scope

## Accepted interface

The real WebUI dashboard is the single Usage workspace with route-persisted `view=usage|cost|trace`. Its sidebar bot control is an explicit scope; a separate Overview button clears that scope. Statistics and trace lists use the authoritative backend projection; the UI does not reconstruct scoped metrics from paginated rows. Bot attribution uses explicit per-bot breakdowns and verified `bot_id` fallback only. Unknown/unattributed work is overview-only. Scoped detail displays the selected bot's recorded contribution alongside the unchanged canonical full-turn context.

The presentation choices in `usage-trace-unification.md`—one no-tabs view and its grouped-list presentation—are superseded by the accepted Usage, Cost, and Trace content views and flat turn list. Its remaining lifecycle, security, routing/history, range, error/retry, focus/scroll, and verification obligations remain in force; this plan does not supersede them.

## Backend projection (lead-owned)

- `thyca/serve/trace.py::scope_turn_to_bot` projects only assistant-row metrics with explicit bot attribution. Session membership and DM ownership are context, never inferred usage attribution. When an explicit breakdown is unavailable, a verified single `TurnSummary.bot_id` is the supported fallback.
- `thyca/serve/trace_api.py` applies that projection before list/stat model, status, search, and date filters. Unattributed rows stay in the unscoped overview; the original detail endpoint continues to return the complete turn under the same `(session_id, turn_index)` identity.
- Statistics add session, turn, and recorded-price coverage counts and daily token counters. Session counts are unique `session_id` values; the overall turn count is the number of parsed turn rows. By-bot contribution turn counts deduplicate `(session_id, turn_index)` across breakdown parts. `priced_turns` counts turns with recorded known cost, not fully priced turns. Missing cost remains `null`, distinct from a recorded zero. Daily token totals cover the day bucket's turns, and cached tokens remain a subset of prompt/input tokens.
- The UI consumes these authoritative fields and retains the unscoped overview's `unknown` attribution row without treating it as a selectable bot.

## Frontend implementation

- Added Usage, Cost, and Trace views with persisted routes; view changes do not alter stats/list cache keys.
- Added explicit Overview scope control, scope-aware sidebar labels, stable bot selection, and scoped detail contribution.
- Kept unattributed rows informational; made only roster-known bot breakdown rows actionable.
- Hid team bot cost breakdown under bot scope, retained shared avatar/rendering conventions, and kept the stat grid auto-fit.
- Reused the real SPA dashboard workspace, shared shell, and dashboard-scoped styles; no new top-level workspace was added.
- Added focused behavior, SPA, and source-backed checks. The source fixture exercises private and shared contributions read-only.

## Verification evidence

Lead backend verification (reported complete; not rerun during the frontend follow-up):

`HOME=/tmp/sandbox-home PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_trace_bot_scope.py tests/test_trace.py tests/test_serve_trace.py`

Result: 34 passed. The HTTP test exercises synthetic session files through stats/list/detail and verifies the source files were not mutated.

Frontend focused gate:

`HOME=/tmp/sandbox-home PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_dashboard_journal.py tests/test_request_chart.py tests/test_cost_journal.py tests/test_trace_journal.py tests/test_trace_score.py tests/test_webui_spa_frontend.py tests/test_usage_trace_source_e2e.py tests/test_usage_bot_scope_frontend.py`

Result: 90 passed. Full output: `/tmp/thyca-usage-bots-checks.log`.

Final full-gate evidence: `HOME=/tmp/sandbox-home UV_CACHE_DIR=/tmp/thyca-uv-cache PYTHONPATH=. uv run --no-sync pytest -q` completed with 1,754 passed and four stale UI expectations failed; output: `/tmp/thyca-usage-bots-final-gate.log`. The affected assertions were updated to match the accepted flat turn list and separate Cost view, preserving unrelated checks. The requested targeted command for those four tests passed: 4 passed; output: `/tmp/thyca-usage-bots-expectation-checks.log`. The full suite was not rerun, so no all-suite-green result is claimed.

Source-backed fixture artifacts are under `/tmp/thyca-usage-bots-*`; fixture file snapshots are compared before/after and owned CLI processes are stopped in `finally` with exit-code evidence. Existing preview server PID 28062 on port 8765 is unrelated and remains running.

## Review status

The backend-only read-only review is recorded at `/tmp/thyca-usage-bots-backend-review.md` and was accepted. Lead UI review is complete. Independent UI review remains outstanding; do not describe the UI work as independently reviewed or merge-ready until that review occurs.
