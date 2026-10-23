---
status: done
created: 2026-10-23
last_updated: 2026-10-23
---

# Root-only WebUI SPA

## Approved scope

The user selected a real single-page WebUI, explicitly rejecting an iframe. Opening the loopback host and port must show the app; navigating all seven screens must leave the address at `/`, with no page path, query or fragment. Preserve the notebook appearance, per-bot model settings, existing API contracts, and legacy deep-link destinations. No framework, dependency, version bump, install, data reset, commit or push is authorized for this new work.

Baseline: `main` at `08e14cc`, clean before this plan. `/` and `/ui/chat` currently return 404; `/ui/chat/` works. `safe_file` looks for a missing root index while both serve entrypoints print the root URL. The existing global-script pages cannot simply be evaluated again: their declarations, event handlers and timers would collide or duplicate.

## Implementation design

Use native JavaScript registration and one persistent application document, not iframe, `document.write`, runtime evaluation, or repeated whole-document navigation.

- Keep the existing Chat HTML as the canonical application entry document, with an explicit `/ui/chat/` asset base. Map `/`, `/index.html`, and the seven known legacy page entry paths (bare directory, trailing slash, and `index.html`) to that entry asset in the existing safe static resolver. Unknown pages and traversal attempts must not become SPA fallbacks. Ordinary asset/API resolution remains unchanged.
- Load shared data, avatars, sidebar/studio wiring, shell timers and Chat runtime once. Keep one shared header/sidebar/dialog set. Put the persistent Chat workspace and lazily created non-Chat workspaces in one page host; exactly one workspace is visible and interactive. Do not rebuild a workspace or rebind its handlers on every visit.
- Register the six non-Chat page factories and the Chat UI controller with a small router. Factories isolate the existing page constants/functions in closures. Retain existing rendering and API helpers rather than introducing alternate implementations.
- A page context owns its workspace-bound `$`/`$$`, `{page, search, hash}` route, saved sidebar selection, active-state check, and route subscription. Shared sidebar references are explicit, not generic query fallbacks into another hidden page. Page-owned dialogs belong to that page's retained DOM.
- Router registration: `register(pageId, {title, stylesheet, mount})`. `mount(context)` returns `{ready, onRoute?, selectBot?, loadSession?}` and is called at most once per page. `ready` may be a promise; late readiness must not reactivate a different page. `onRoute` applies external route changes after readiness; returning to the same retained route must not erase a draft.
- Context methods: `navigate(pageId, {search, hash}?)`, `updateRoute({search, hash})`, `highlightBot(id)`, `renderTeam()`, `isActive()`, `onRoute(listener)`, plus shared shell readiness. Missing route arguments reuse the retained page route; explicit route arguments replace it. Internal route updates do not recursively notify their originating page.
- Store only page/search/hash navigation metadata in native `history.state`. Push on screen navigation and replace for current-screen scope/filter changes, always with visible URL `/`. Validate state on startup/popstate. Root reload restores a valid history route; a fresh root visit defaults to Chat. Consume legacy entry path/query/fragment into this route before replacing the visible URL. Preserve Profile bot/shared separation, Provider bot-link handoff, Chat bot/session links and Trace turn anchors.
- Keep Chat's `selectedBot` and canonical live-turn state independent of a non-Chat page's sidebar scope. Non-Chat pages call context highlight/render functions rather than assigning Chat's global selection. The shared sidebar dispatches select/session actions to the active controller. Retain the Chat DOM and running token/stream state while another screen is visible; leaving must not send cancel, start another stream, or discard the composer. Invalidate pending Chat navigation generations on leave.
- Activate only the current page's stylesheet; shared app-shell styles must not alter Chat. Hide inactive views with an explicit scoped rule that cannot be overridden by page display rules. Restore current sidebar labels, selected styling, group visibility, title and focus on activation. Shared timers/listeners are initialized once; cached page observers must not mutate another page's selection or labels.

## Work and ownership

1. **Delegated:** implement the static entry aliases, router/context, retained page host, shared dispatch and seven page controllers.
2. **Delegated:** migrate route readers/writers and page-scoped DOM access; preserve late-response/save/draft guards. Move Profile's edit-dialog markup into its retained page initialization rather than duplicating a second dialog set.
3. **Delegated:** add router/static/lifecycle regressions and adapt tests whose old global-script/HTML assumptions genuinely changed. Keep unit-harness adaptations separate from real registry/controller integration coverage.
4. **Lead:** inspect the complete diff, validate findings, and direct consolidated corrections.
5. **Delegated:** verify the actual browser flow and package assets; update directly affected docs and record evidence. The lead decides acceptance. Do not mark this plan done on test results alone.

## Required checks

Use `HOME=/tmp/sandbox-home` for all Python/build checks. Never access or recreate real `~/.thyca`. Reuse the isolated demo at port 19502 and `/tmp/thyca-demo`; the final verified demo PID is 112431 (verify the process identity before any future restart). Because Python server code changes, restart only this isolated demo when necessary, using its explicit-root launcher, and leave it running. Do not reinstall the user's CLI or call a real provider.

Baseline evidence: `/tmp/thyca-precommit-check.log` records 191 passes before SPA work. `/tmp/thyca-spa-baseline.log` records 12 passes and one existing failure in `tests/test_serve_memory_stats.py::test_profile_screen_renders_markdown`, which still expects removed `sharedBotId`. Its other old Profile assertions must be checked against the accepted separate shared/bot design, not copied as requirements. The concurrent-create race in `/tmp/thyca-postcommit-independent-review.md` predates this task and is outside scope.

Focused checks after implementation:

```sh
HOME=/tmp/sandbox-home .venv/bin/python -m pytest -q \
  tests/test_serve_memory_stats.py tests/test_serve_daemon.py \
  tests/test_team_frontend.py tests/test_bot_settings_frontend.py \
  tests/test_chat_bot_settings_frontend.py tests/test_webui_theme.py \
  tests/test_webui_format.py tests/test_webui_memory_navigation.py \
  tests/test_webui_memory_edit.py tests/test_webui_live_rounds.py \
  tests/test_webui_live_recovery.py tests/test_webui_stream.py
```

Also run new router tests, `node --check` for changed JavaScript and `git diff --check`. Save exact commands and outputs to `/tmp/thyca-spa-checks.log`. At most one full-suite final gate, after focused checks and review corrections.

Required regression cases:

- Root and every known legacy entry render the same app bootstrap; page assets retain correct MIME types; unknown routes, NUL, traversal and symlink escapes remain denied.
- Seven screen switches leave the actual address at `/`; no iframe or extra document navigation. Legacy `?bot`, `?s`, and Trace query/hash destinations survive canonicalization.
- Native Back/Forward and reload restore the stored route without showing query/fragment or exposing provider keys in history state.
- Rapid navigation with delayed readiness/fetches never activates the stale screen, changes another page's scope, or wipes a draft. Visiting an already mounted page does not duplicate listeners/timers, writes, dialogs, or CSS.
- Profile model save and canonical editing work through their existing endpoints; unavailable models and unknown bots remain honest. Provider stays shared-only.
- A fake streamed Chat turn continues while another screen is active; returning shows the same token/transcript, with no extra POST/cancel/replay, and composer overrides stay scoped correctly. Never use a real provider to prove this.
- Desktop and 320/375px show no root or inner overflow; Profile card insets stay equal, card gap remains 22px and Save spacing 20px.
- A wheel built in an isolated output directory contains the canonical entry, router, seven controller assets, CSS/fonts/images/vendor dependencies. No system or user installation is needed.

Browser results/screenshots: `/tmp/thyca-spa-browser.json` and `/tmp/thyca-spa-*.png`. Report real-provider behavior and any unavailable independent review as unverified. Update README and directly affected `.agents` guidance to describe the actual root SPA only after it works.

## Completion evidence (2026-10-23)

The lead reviewed the runtime and test-harness changes, validated the corrections, and accepted the approved SPA scope. No commit or push was made for this work.

- Final full gate: `HOME=/tmp/sandbox-home uv run --no-sync pytest -q` — **1,704 passed**, exit 0, in 146.94s; `/tmp/thyca-spa-final-confirmation.log`. The earlier 1,632-pass/72-failure run preceded migration of the remaining global-script test harnesses; the affected six-file group subsequently passed 85 tests before the final green gate. Business assertions were retained; URL expectations now use the accepted in-memory route contract.
- Real-Chrome integration: `/tmp/thyca-spa-rework-browser.json` covers all seven retained workspaces, Back/Forward and reload, legacy and percent-encoded entry aliases, delayed CSS/initial Chat navigation, pending saves, fake stream/composer preservation, and 320/375px layout. Actual registry/controller code runs without iframe or whole-document navigation. Lead inspection also verified the root-only URL and matching Chat styles through seven screen switches on the real demo.
- Real demo writes: `/tmp/thyca-spa-real-final.json` records a Thyca Profile model save and exact settings restoration, plus shared `USER.md` edit/reload and exact content restoration. Browser errors and chat/provider POST lists were empty. These checks touched only `/tmp/thyca-demo`.
- Packaging: `HOME=/tmp/sandbox-home UV_CACHE_DIR=/tmp/thyca-uv-cache uv build --wheel --out-dir /tmp/thyca-spa-wheel-final` succeeded; `/tmp/thyca-spa-wheel-final-build.log`. Inspected entry/router/controller and supporting assets; the changed entry/router/static files in the wheel matched the source. No user installation was changed.
- JavaScript syntax, Python syntax and whitespace checks passed. `/tmp/thyca-spa-final-postcheck.log` records final `git diff --check`, live demo PID 112431, and HTTP 200 with byte-identical SPA bootstrap responses for `/`, `/ui/profile/index.html` and `/ui/%70rofile/index.html` after the isolated server restart. The server remains running on port 19502.

Limits: real-provider quality was not tested. The pre-existing concurrent bot-create race and broader canonical-workspace independent review are outside this SPA scope and are not claimed resolved. Do not infer merge, publish, or data-reset permission from this completion record.
