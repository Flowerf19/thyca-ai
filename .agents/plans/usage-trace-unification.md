---
status: in-progress
created: 2026-10-24
last_updated: 2026-10-24
---

# Usage and Trace unification

## Goal

Propose a single **Sử dụng** workspace that combines the existing usage overview and Trace turn history/detail. Keep one top-level navigation entry, one metrics view, one paged turn list, and an in-workspace turn-detail view. Preserve existing API contracts, statistical meanings, turn rendering, SPA history behavior, and the notebook design.

## Status and permission

The user has accepted the proposal and explicitly authorized implementation within its stated scope. Work is **in progress**; this plan is not complete or ready to merge. Preserve all pre-existing uncommitted Provider/onboarding work, and leave the result uncommitted for lead review.

## Pre-implementation baseline evidence

Baseline is `main` at `f4249c05` (`0.87.0.dev0`). Concurrent Provider/onboarding changes predate this proposal and are outside its scope.

- The root SPA has seven registered workspaces. `Sử dụng` is the dashboard workspace (`thyca/webui/ui/dashboard/js/dashboard.js`); Trace is currently a separate workspace (`thyca/webui/ui/trace/js/trace.js`). Navigation entries are described by `SHELL_PAGES` in `thyca/webui/ui/share/util.js`; the bootstrap document is `thyca/webui/ui/chat/index.html`.
- The dashboard defaults to seven local-calendar days; the server filters `started_at` using UTC dates. It displays five KPI cards, day/bot/model/hour breakdowns, and a recent-turn table (first 15, then 50). Its bot/date filters scope both stats and the list. Model/status are intentionally list-only today.
- Trace is a separate 50-row, session-grouped list with from/to/search/model/status filters and a full-width detail/back view. Its timeline/detail renderers already preserve the turn content described below; request-generation and scroll guards are in place.
- Existing APIs remain `GET /api/traces/stats`, `GET /api/traces`, and `GET /api/traces/<sid>/<turn>`. `thyca/serve/trace_api.py::_apply_trace_filters` supports bot/date/search/model/status for stats and list. This proposal does **not** promote search/model/status into global KPI filters.
- Dashboard links to Trace through `traceHref`/`openTraceRow` in `dashboard.js`. `trace.js` handles `openTurn` and `routeFromUrl`, including `?s=&t=` and `#sid/turn`; hash takes precedence. `thyca/webui/ui/share/router.js` normalizes legacy routes/history and exposes page contexts. `navigate` pushes a same-page route; `updateRoute` replaces it. `routeParts` needs an explicit metadata allowlist for the merged filters/detail state.
- `thyca/serve/static.py` already includes known Trace aliases and root bootstrap mapping. No server static/API changes are proposed. Keep its exact allowlist, single-decoding, unknown-path, traversal, NUL, and symlink protections.
- Canonical root-SPA/workspace decisions are in `.agents/README.md`, `.agents/PROJECT_CONTEXT.md`, `.agents/AGENT_RULES.md`, `docs/archive/plans/webui-spa-root.md`, and the canonical-only section of `bot-workspace-channels.md`. Older `webui-v5-small` Trace redirect/folder/version descriptions are stale and must not be reintroduced.

## Accepted design

### Navigation and overview

- Keep one top-nav item, **Sử dụng**, with canonical page ID `dashboard`. Remove Trace as a nav item; retain exactly the six actual registered workspaces. Do not add tabs that duplicate entire old pages, a second filter/list, an iframe, document reloads, a framework, or a dependency.
- Preserve the existing dashboard overview hierarchy and comfortable current font/spacing treatment: heading, date presets/date controls, bot/date scope and clear-scope action; the five existing KPI values; day-chart toggle; one **Lịch sử lượt** list with its own search/model/status controls; and a lower collapsible **Phân tích** section for bot/model/hour breakdowns.
- Reuse Trace's readable session-grouped/turn-row layout for that single list, rather than retaining both the dashboard table and Trace picker/list. Keep per-turn time, model, status, bot, request/tool/token/cost/latency information available in rows or detail, and search by session title/ID. Do not invent session totals when only part of a session is paged.
- Retain the dashboard's first-15/more-50 paging behavior as the merged list contract; do not fetch an unbounded list. Preserve loaded rows, filters, scroll, and focus when returning from detail.

### In-workspace detail

- Selecting a turn opens a full-width **Chi tiết lượt** view inside the dashboard workspace, not another page or top-level nav item. Hide the overview temporarily; **Sử dụng** back returns to the same overview state, including filters, loaded rows, scroll, and focus.
- Preserve the existing detail renderer's user/assistant/reasoning content, encrypted marker, tool input/output, parse errors, orphan results, bus/system events, synthetic stops, errors, and bot fallback. Keep tool JSON escaped and use the existing shared Markdown sanitizer/fallback unchanged.
- Do not fetch detail for list rows. Make one explicit detail GET when opening a turn. A direct detail deep link must render without waiting for a list/summary request; initialize the overview lazily when returning if needed.

### Filters and date semantics

- Header date presets and bot/date scope apply to both stats and the list. Model/status/search remain clearly located list-only filters; do not send them as KPI filters or change the current dashboard behavior.
- Keep the seven-day default on a new Sử dụng visit and preserve the existing local-calendar/UTC server semantics. Preserve Trace's unbounded-date entry without silently restricting it: a legacy plain Trace entry maps to dashboard with `range=all` (no date filters) and displays **Tất cả**. Explicit `from`/`to` dates override presets; clearing both dates selects `range=all`. One-sided ranges remain valid as on the existing server. Do not change the newest-200-session-file backend cap or claim complete all-time billing.

### State, concurrency, and refresh

- One controller owns bot/date/list/detail state. Keep separate request generations for stats, list, and detail. Filter changes reset list offset; stale results must not restore an old detail or a cleared filter's loading state.
- Changing bot scope from detail returns to the overview for that bot, rather than showing an unrelated turn under a new scope. List-only filters and preset controls belong to the overview.
- Refreshing detail reloads only detail. Refreshing overview reloads stats and the current list once, without an extra POST. Re-entering a retained page without a changed route or explicit refresh must not duplicate GETs, listeners, or timers.

### Routing and compatibility

- Normalize legacy Trace paths, old history entries with `page: 'trace'`, and direct `ThycaRouter.navigate('trace', parts)` calls at the router boundary to `dashboard`. Do not register a hidden seventh Trace factory.
- Preserve valid bot/session/turn intent from `?s=&t=` and `#sid/turn`; the hash wins when both exist. Store the canonical detail hash in SPA history state only; the visible URL remains `/`.
- Allow only route metadata: `bot`, `from`, `to`, `range=all`, `q`, `model`, `status`, and consumed legacy `s`/`t`. Retain the existing bounds and safe-ID/turn validation. Ignore unknown metadata and credential fields. Never place trace bodies, tool arguments, config, or keys in history state or browser storage.
- Opening a turn through `ctx.navigate('dashboard', parts)` pushes detail so native Back returns to that overview entry and Forward returns to detail. Explicit in-app back from a locally opened turn must return to the same prior overview entry. Filter changes use `ctx.updateRoute`/replace. For a fresh legacy/deep-link detail with no known valid in-app return origin, replace the current entry with the overview and clear the detail hash; never blindly call `history.back()` or infer the origin from `history.length`. Preserve/validate navigation-only return-origin metadata in the existing router/context if needed; it must not include trace content or secrets. Native Back/Forward, reload restoration, and explicit back must not create fake or looping entries. Test explicit back after a locally opened turn, a fresh deep link with an external previous entry, and return after reload.

### Module and stylesheet ownership

- `dashboard.js` owns summary/list fetches, route/view state, and the controller. Repurpose `thyca/webui/ui/trace/js/trace.js` as a load-once `window.ThycaTraceDetail.render(detail)` HTML renderer: keep its private rendering helpers, but remove its page registration, own-page fetch/list state, and standalone controller responsibilities. Load the renderer before the dashboard factory from the existing bootstrap.
- Move only the required Trace-detail rules into `dashboard.css`, scoped under `.dashboard-shell`. Keep one active page stylesheet plus existing app-shell/Markdown rules; do not leak active CSS across workspaces.
- Do not delete legacy Trace HTML/CSS assets in this merge. Keep entry compatibility and the safe static allowlist. Update stale entry comments/tests only where standalone Trace assumptions become historical. Do not broaden CSS/font changes or make Provider changes.

### Statistical and error correctness

- `totals.requests` means model requests, not turns; label it **Gọi model**. The list total counts turns. Preserve prompt tokens including cache, displayed uncached input (`prompt - cached`), existing deduplicated multi-model aggregation, bot attribution, p50/p90, and server cost semantics (`null` distinct from zero). Do not invent prices, recompute cost in the browser, or replace server stats with browser aggregates. Keep UTC labels concise and honest.
- Keep stats/list/detail errors independent and retryable. A detail 404 is not a successful empty detail. If a bot disappears, retain the raw-ID fallback.

## Proposed task sequence and dependencies

Execution has started under the explicit implementation authorization above. Task completion remains unclaimed until checks and review are recorded; no later phase is presumed complete.

1. **Characterize before moving code.** Record current dashboard/Trace rendering, routes, paging, filters, date behavior, API parameters, and regression cases. Add merged-behavior assertions before the move. Update legitimate seven-to-six/nav-alias expectations without weakening turn-stream, detail-rendering, route-security, or draft-preservation assertions.
2. **Unify controller and rendering.** Make dashboard the sole owner of stats/list/detail and one paged session-grouped list. Extract Trace's existing detail renderer into its load-once renderer interface and move only required detail CSS into scoped dashboard styles. Verify rendering/escaping, cost semantics, paging, errors, refresh, and stale-request guards.
3. **Normalize routes and history** (depends on task 2). Normalize all Trace aliases at the router boundary, update the metadata allowlist and bootstrap ordering, and implement Back/Forward/reload and fresh-deeplink behavior. Verify six workspaces, setup-gate restoration, route security, and stylesheet lifecycle.
4. **Isolated browser and source-HTTP validation** (depends on tasks 1–3). Use fresh synthetic sessions and canonical artifacts only; verify real source API/SPA behavior, collect sanitized evidence, stop all test fixtures in `finally`, then request lead diff review and consolidated corrections. Broad navigation/detail impact requires independent review before calling the work ready to merge.
5. **Documentation after approved implementation works.** Update only directly affected README/runtime guidance to describe verified behavior; do not update those documents until the merged flow passes implementation checks.

## Verification plan and implementation evidence

Implementation remains in progress. Source-backed Usage browsing now passes on isolated source CLI fixtures; independent lead review remains open. The older full-suite result below predates the latest fixes and is not current verification.

Checks run:

- `HOME=/tmp/sandbox-home .venv/bin/python -m pytest tests/test_journal_visual.py tests/test_dashboard_journal.py tests/test_cost_journal.py tests/test_trace_journal.py tests/test_trace_score.py tests/test_team_frontend.py tests/test_webui_spa_frontend.py tests/test_request_chart.py tests/test_serve_chat.py::test_chat_rows_use_the_shared_sidebar_markup tests/test_webui_markdown.py tests/test_webui_memory_navigation.py tests/test_provider_frontend.py tests/test_webui_setup_gate_source_e2e.py -q` — 197 passed.
- `HOME=/tmp/sandbox-home .venv/bin/python -m pytest tests/test_webui_spa_frontend.py::test_root_spa_retains_all_workspaces_and_restores_history -q` — 1 passed; exercises synthetic ready-state SPA history, Trace aliases, detail return semantics, and 1280/320/375px Usage screenshots.
- `HOME=/tmp/sandbox-home UV_CACHE_DIR=/tmp/thyca-uv-cache PYTHONPATH=. uv run --no-sync pytest -q` — initial run exposed seven stale assertions that expected the separate Trace page; those were updated for the accepted single-workspace design. Final rerun: 1,739 passed in 175.91s.
- `for f in $(find thyca/webui/ui -type f -name '*.js' ! -path '*/vendor/*'); do node --check "$f" || exit; done; git diff --check` — passed before this plan evidence edit.
- Synthetic Usage detail screenshots: `/tmp/thyca-usage-trace-detail-1280.png`, `/tmp/thyca-usage-trace-detail-320.png`, `/tmp/thyca-usage-trace-detail-375.png`; browser result JSON: `/tmp/thyca-usage-trace-spa-browser.json`.


Focused checks after implementation:

```sh
HOME=/tmp/sandbox-home PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_dashboard_journal.py tests/test_trace_journal.py \
  tests/test_cost_journal.py tests/test_journal_visual.py

HOME=/tmp/sandbox-home PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_webui_spa_frontend.py tests/test_team_frontend.py \
  tests/test_webui_theme.py tests/test_webui_format.py

HOME=/tmp/sandbox-home PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_serve_trace.py tests/test_trace.py
```

Latest scoped verification after review fixes (2026-10-24):

- `HOME=/tmp/sandbox-home PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_dashboard_journal.py tests/test_trace_journal.py tests/test_cost_journal.py tests/test_journal_visual.py tests/test_trace_score.py tests/test_request_chart.py tests/test_usage_trace_source_e2e.py` — 82 passed.
- `HOME=/tmp/sandbox-home PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_webui_spa_frontend.py tests/test_team_frontend.py tests/test_webui_theme.py tests/test_webui_format.py tests/test_webui_markdown.py tests/test_webui_memory_navigation.py` — 99 passed.
- `HOME=/tmp/sandbox-home PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_serve_trace.py tests/test_trace.py` — 26 passed.
- `HOME=/tmp/sandbox-home PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_webui_setup_gate_source_e2e.py` — 1 passed.
- `HOME=/tmp/sandbox-home PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_usage_trace_source_e2e.py` — 2 passed after the mobile-range-controls fix. The suite validates canonical source-backed IDs, GET-only calls, unchanged fixture files, exact stats/list/detail payload rendering, overview/detail/back focus+scroll, and responsive 1280/375/320px layouts including visible range controls; fixture stop results are recorded in the JSON evidence.
- `for f in $(git diff --name-only -- '*.js'); do node --check "$f" || exit; done` — all changed JavaScript passed. `HOME=/tmp/sandbox-home PYTHONPATH=. .venv/bin/python -m py_compile tests/test_trace_journal.py tests/test_webui_markdown.py tests/test_usage_trace_source_e2e.py tests/test_webui_setup_gate_source_e2e.py` and `git diff --check` — passed.
- Browser evidence: `/tmp/thyca-usage-trace-source-e2e.json`, `/tmp/thyca-usage-trace-source-responsive.json`, `/tmp/thyca-usage-trace-source-source-flow-1280.png`, and overview/detail screenshots for 1280, 375, and 320px under `/tmp/thyca-usage-trace-source-*.png`. Combined command/result log: `/tmp/thyca-usage-trace-rework-checks.log`.

The earlier structural-test failures were stale expectations for the old debounce/paging implementation and incomplete `hasAttribute`/`focus` DOM stubs; those assertions/stubs now match the current behavior. The source-backed mobile screenshot also exposed clipped range controls, fixed within the dashboard-scoped responsive styles.

If Chat-retention code is touched, also run `tests/test_webui_live_rounds.py`, `tests/test_webui_live_recovery.py`, and `tests/test_webui_stream.py`. Keep existing Provider/setup-gate/Profile-save assertions from the SPA gate. Run `node --check` for changed JavaScript and `git diff --check`.

After focused checks and review corrections, run at most one full gate:

```sh
HOME=/tmp/sandbox-home UV_CACHE_DIR=/tmp/thyca-uv-cache PYTHONPATH=. \
  uv run --no-sync pytest -q
```

Do not repair the environment or install packages as part of that gate. If bootstrap dependencies change, verify package asset inclusion without installing the user's CLI.

For actual-product validation, use two isolated fixture states at desktop 1280px and mobile 320/375px—not the live user previews or app on ports 19503/19502: a keyless state for setup-gate/restore coverage, and a ready Usage/Trace state with a synthetic local-only key and model in a fresh explicit `/tmp` config. Remove inherited `THYCA_TOKEN` from child environments so fixtures cannot inherit real credentials. Never make provider/inference calls or send a real Chat turn. Prepare only fixture-owned synthetic data and perform only required test API writes; stop all owned processes in `finally`. Verify canonical real session IDs through source HTTP endpoints, not only stub names. Cover overview → same-page detail → back with filters/paging/scroll/focus; list-only search scope; seven-day versus legacy-all semantics; old query/hash forms and encoded known aliases; native Back/Forward/reload; explicit back after a locally opened turn; a fresh deep link with an external previous entry; return after reload; bot changes from detail; stale A/B requests; independent failures/retries; paging; null costs; escaped unsafe HTML; Chat stream/composer retention if affected; exactly six workspaces; no duplicate timers, hidden CSS leakage, or extra fetch on retained visits; unknown static paths still 404. Save sanitized evidence under `/tmp/thyca-usage-trace-*.log`, `.json`, or `.png`; never record real keys or content.

## Limits and open status

- Functional implementation, source-backed browser verification, and lead diff review are complete. The plan remains **in progress** for independent review, which is unavailable in this session and is required before declaring the change ready to merge. Lead review is not independent review. The historical full-suite result predates the final corrections; it is not current full-suite verification.
- Final corrections restore the 22px overview card gap and keep failed/pending stats unknown, including after a day-chart metric toggle. The final targeted source-flow/chart gate passed **10 tests**; the last chart-only regression gate passed **9 tests**. JavaScript syntax and whitespace checks passed. Exact results are appended to `/tmp/thyca-usage-trace-rework-checks.log`; `/tmp/thyca-usage-trace-source-e2e.json` confirms the restored gap, unchanged fixture files, focus/scroll restoration, GET-only calls, and fixture shutdown. Earlier six responsive screenshots predate the gap restoration and are labeled accordingly. Human visual acceptance remains separate.
- The pre-existing concurrent bot-create race and broader workspace independent review remain outside scope.
- No version change, install, data reset, commit, or push is authorized by this plan.
