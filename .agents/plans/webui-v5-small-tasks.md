---
status: in-progress
created: 2026-10-15
last_updated: 2026-10-15
---

# Thyca Chat v5 — small tasks for Luna Code

## Permission and goal

Implementation authorized by the user on 2026-10-15: use parallel Luna workers,
prioritize speed, require each worker's self-review, then hand off for user review.
This does not authorize a commit or push. Give Luna **one small task per invocation**;
reuse a worker's context for later tasks on the same file. Workers must not edit
the same file concurrently. The parent owns final integration and test alignment.

Execution scheduling: workers perform file-level checks and self-review per task;
the coordinator runs suite gates on coherent integrated checkpoints. Do not treat
expected in-flight visual assertion mismatches as passing tests. Test-file edits
are serialized separately from parallel production CSS work.

Port the supplied v5 interface into the existing WebUI, retaining real product
behavior. Reuse shared CSS across screens as much as possible. Tasks are small
because Luna should follow concrete changes rather than design new architecture.

### Sources checked

- Repository: `feat/agent-status`, commit
  `a48426981f6054fd34d84886949568d17e86588a`; working tree initially clean.
- Reference: `/home/flowerf/Downloads/thyca-designs/thyca_bot_avatar_design_system_v5.html`.
  SHA-256: `12a2077e4f8059f92c0a5361f5efcac1de22b3b45630d0156273cdfb20d3a0b6`.
- Read `.agents/README.md`, `.agents/PROJECT_CONTEXT.md`, and
  `.agents/AGENT_RULES.md` before implementation. Some historical version/test
  counts in those documents are stale; obtain the baseline from current tests.
- Reference and running app at `http://127.0.0.1:8765/` were viewed in a browser.
  No runtime files were changed and no test suite was run during planning.
- Reference CSS: lines 3–7; shell/sidebar: 9–20; watermark: 22–30;
  messages: 32–54; composer: 57–67. Renderer begins at
  `initThreeWoodStudio` (129), models at `buildThreeBotModel` (269),
  thumbnails at `buildSidebarThumbnails` (487), sidebar at `renderSidebar`
  (498), demo state at `setState` (526). Re-find symbols if the source changes.

## Scope and decisions

### Requested / retained

- Match the v5 frame, quiet paper surfaces, compact bot rail, thin message
  stripes, spacing, composer, muted metadata, and terracotta accent.
- Other screens inherit its shared palette and chrome. The reference contains
  **one chat page**, not new designs for every other screen: preserve their
  information architecture, labels, routes, and workflows.
- Recolor the whole WebUI when needed, through shared tokens/components.
- Keep existing background assets. The user delegated selection among three.
- Preserve real bots/groups, sessions, unread indicators, new-session actions,
  menus, mentions, model/reasoning selectors, stop/continue behavior, streaming,
  recovery, memory editing, provider settings, and existing accessibility.
- Preserve current font families (`Fraunces`, `Source Serif 4`, `Be Vietnam Pro`)
  per the existing accepted font decision; tune size/weight/spacing toward v5.
  Do not silently replace them with the demo's Georgia/system-ui stack.
- Preserve `avatarVariantFor`, `avatarStateFor`, `teamBadgeState`, existing
  variant IDs, stored avatar metadata, and responding-bot identity resolution.
  Keep public SVG renderers as fallback and for existing consumers/tests.

### Background selection

Use **`thyca/webui/images/thyca-bg-outer.png`**. It is the lightest, quietest
image and closest to v5's almost-flat paper. `thyca-bg-paper.png` has stronger
musical/edge decoration; `thyca-bg-menu.png` is substantially more orange.
Keep all three files unmodified. Change tint through shared CSS overlays only.

This selection replaces the previous paper-image choice for this redesign.
It does not revoke unrelated rules in `AGENT_RULES.md`. Use a shared overlay
starting at the existing 65%; verify its appearance rather than asserting that
65% is the final visual match. Do not combine the three images or bake a new PNG.

### B1 — actual 3D renderer (accepted for execution)

The reference bundles **Three.js r128**, MIT, and uses one live WebGL canvas
plus static snapshots for inactive avatars. The app currently uses SVG.

The user's approval to execute the proposed plan includes TASK-032–041:
vendor that exact local runtime with its license and add a progressive-enhancement renderer. No CDN,
package manager, framework migration, or replacement of SVG public APIs.
Do not substitute flat SVG and claim that the 3D reference was fully ported.
The complete v5 UI remains unfinished until the avatar branch and its checks pass.

### Explicitly out of scope

Backend/API changes, new attachment/voice capabilities, bot creation rules,
identity remapping, persistence migrations, invented data, model catalogs,
mock responses/timers, deletion of production components, and automatic commits.
Keep currently unavailable actions unavailable; do not make a demo affordance
look like a working capability.

## CSS contract — reuse first

Do **not** create a root `v5.css`, one copied stylesheet per screen, a second
token system, or a new UI framework.

| Existing owner | Responsibility |
|---|---|
| `thyca/webui/shared/css/tokens.css` | All shared colors, image/overlay tokens, shadows, radii, existing font tokens |
| `thyca/webui/shared/css/kit.css` | Shell, header, sidebar/workspace surfaces, search, buttons, inputs, dialogs, cards, journal rows, pagers |
| `thyca/webui/shared/css/states.css` | Existing loading/empty/error/live-feedback components |
| `thyca/webui/shared/css/markdown.css` | Markdown typography and content treatment |
| `thyca/webui/shared/css/settings.css` | Provider + Settings shared forms/rows; keep this existing reuse |
| `thyca/webui/shared/css/bot-avatar.css` | Avatar display slots, state/fallback styling |
| `thyca/webui/pages/*/*.css` | Only genuinely screen-specific placement and components |

All six real HTML entrypoints already load tokens/kit/states/markdown.
`trace.html` is a redirect to Dashboard: keep it a redirect, not a seventh app.
Settings already shares `shared/css/settings.css` with Provider; it does not
need a new page stylesheet. Extend current scoped classes rather than adding
broad global `button`, `input`, or `h2` resets.

Put a shared component's appearance in its existing owner. Page files may
position/size it, but must not repeat its palette, focus, hover, border, and
background rules. Do not generalize unrelated components solely because they
share one color. Avoid cross-page imports of another page's CSS.

### Palette targets from the actual reference

Reuse existing semantic names first; add a token only for a distinct role.

| Role | Reference target |
|---|---|
| Desk | `#eeeae2` |
| Frame | `#f1ece2` |
| Main paper | `#faf7f0` |
| Sidebar | `#f9f5eb` |
| Selected item | `#eee5d7` |
| Composer/input surface | `#fdfbf6` |
| Ink / muted / border | `#3b332a` / `#8a7c69` / `#e0d6c6` |
| Accent | `#a85b43` |
| Composer border | `#d8cbb8` |

Keep aliases coherent: changing the input shade may legitimately require
updating its former surface alias. Preserve semantic success/wait/error colors.
Do not recolor all statuses terracotta. The wood avatar variant colors are
`star #A85B43`, `hollow #986758`, `single-flag #66735A`,
`double-flag #A28550`, `dotted #60766C`, `fermata #916B50`, `plain #635044`.
Map these through existing variant tokens, not newly invented bot-name rules.

## How to hand one task to Luna

Normally edit one production file, plus the directly affected test file.
Some shared changes have expectations in both theme and team tests; identify
that before editing. If the task expands beyond its named responsibility,
return the blocker to the coordinator instead of refactoring adjacent code.

```text
Implement only TASK-XXX in .agents/plans/webui-v5-small-tasks.md.
First confirm implementation was explicitly authorized and prerequisites passed.
Read the shared contract, the named files, and their relevant tests/callers.
Use the original reference HTML for the named visual component only.
Do not change backend behavior, public APIs, identity rules, or unrelated CSS.
Reuse shared tokens and existing shared component classes; no copied page theme.
Do not execute another task, commit, push, or update another worker's files.
Report changed files, exact checks/results, screenshot evidence, and blockers.
```

### Per-task gate

- Review `git diff --check` and the diff for unrequested edits.
- Run the named test group below. Do not claim screenshot acceptance from
  substring tests, or full behavior acceptance from a single narrow suite.
- Source-pinned **old visual values** may change with the new design. Update
  those assertions explicitly to the new requirement; do not delete tests,
  remove semantic assertions, or disable tests to get green output.
- Particularly relevant old pins: exact colors and three paper-image URLs in
  `tests/test_webui_theme.py`; old imagery/transparency, avatar palette, rail
  sizes, and header geometry in `tests/test_team_frontend.py`.
- If a pin protects behavior rather than appearance, preserve it. Root direct
  replies with trace IDs, subordinate sessions without avatars, per-bot new
  sessions, card identity, and streaming/recovery are not cosmetic exceptions.

Test groups, all from repository root:

```bash
# A — required for theme/chat/avatar changes; Node must be installed
uv run pytest -q tests/test_team_frontend.py tests/test_webui_theme.py tests/test_webui_format.py

# B — chat controls, mobile pick flow, streaming and replay
uv run pytest -q tests/test_serve_chat.py tests/test_webui_live_rounds.py tests/test_webui_live_recovery.py tests/test_webui_stream.py tests/test_webui_concurrent_streams.py

# C — dashboard/journal presentation
uv run pytest -q tests/test_dashboard_journal.py tests/test_cost_journal.py tests/test_trace_journal.py tests/test_journal_visual.py

# D — memory editing behavior
uv run pytest -q tests/test_webui_memory_edit.py

# E — profile/static serving and provider validation regressions
# These do not replace browser checks of Provider/Settings forms.
uv run pytest -q tests/test_serve_memory_stats.py tests/test_onboarding.py

# Final regression gate (A alone is not the entire WebUI suite)
uv run pytest -q
```

## Small-task backlog

Paths below are relative to `thyca/webui/` unless prefixed with `tests/` or
`.agents/`. Each checkbox is an independent handoff. No phase is a single task.

### Phase 0 — capture the baseline

- [x] **TASK-000 — Baseline only.** No code edits. Record branch/status, run A–E,
  and capture Chat plus the five other screens at desktop and mobile widths.
  Record existing failures separately. Use an isolated/test profile for any
  interaction that writes data. Done: commands and screenshots are recorded;
  do not copy the historical “719 passed” count as a current result.

### Phase 1 — shared CSS, one responsibility at a time

- [ ] **TASK-001 — Neutral/brand palette.** After 000. Edit
  `shared/css/tokens.css` and affected theme assertions. Apply the palette table
  using current semantic names; keep font families and semantic statuses.
  Add distinct surface tokens only when required by the table. Check A.
- [ ] **TASK-002 — Wood variant colors.** After 001. Edit tokens and the avatar
  color expectations in `tests/test_team_frontend.py`. Change the seven note
  colors to the supplied wood palette; leave variant IDs and identity lookup
  untouched. SVG fallback must inherit the same colors. Check A.
- [ ] **TASK-003 — Reusable image/overlay and elevation tokens.** After 001.
  Edit `shared/css/tokens.css`. Define one shared background image token using
  `../../images/thyca-bg-outer.png`, overlay, and needed restrained shadow/radius
  tokens. Retain current token names where possible. Check A and asset loading.
- [ ] **TASK-004 — Apply the shared artwork.** After 003. Edit only background
  declarations of `.notebook-shell`, `.notebook-shell::before`, and
  `.screen-dialog` in `shared/css/kit.css`, plus affected imagery assertions.
  Reuse the same image/overlay token, not three copied URL/gradient definitions.
  Check A; inspect shell and dialog texture. Keep all PNGs unchanged.
- [ ] **TASK-005 — Outer frame geometry.** After 004. Edit shell rules in
  `shared/css/kit.css`: target reference max-width 1440px, 14px frame spacing,
  20px outer radius, subtle border/shadow. Remove the heavy stacked-paper
  appearance, not the notebook structure. Preserve usable viewport scrolling
  and existing list/sidebar modes. Check A+C; view all six screens.
- [ ] **TASK-006 — Shared header chrome.** After 005. Edit header rules in
  `shared/css/kit.css`. Match the quiet v5 menu/brand alignment and spacing.
  Preserve the actual navigation trigger, labels, and keyboard behavior; the
  reference's decorative hamburger is not a replacement button. Check A+B.
- [ ] **TASK-007 — Shared sidebar/workspace surfaces.** After 006. Edit their
  rules in `shared/css/kit.css`: v5 thin border, 14px radius, distinct sidebar
  and paper shades, reduced shadow. Do not change page-specific content grids
  or remove existing list modes. Check A+C; compare all screen surfaces.
- [ ] **TASK-008 — Shared controls.** After 007. Edit `.screen-button`,
  `.screen-input`, `.screen-select`, badges/cards in `shared/css/kit.css`.
  Use shared tokens and the existing primary modifier; preserve disabled,
  focus, and semantic error behavior. No new control framework. Check A+C.
- [ ] **TASK-009 — Shared search colors.** After 008. Edit only `.thyca-search`
  styling in `shared/css/kit.css`. Replace its local hardcoded brand colors
  with shared semantic token references; preserve size modifiers, clear button,
  focus, and filled states. Check A+C on more than one search consumer.
- [ ] **TASK-010 — Dialog/menu polish.** After 009. Edit dialog/navigation
  component styles in `shared/css/kit.css`. Match v5 borders, radii, spacing,
  muted text and selected rows. Do not change focus trapping, backdrop-close,
  dialog actions, or navigation targets. Check A+B and keyboard navigation.
- [ ] **TASK-011 — Shared feedback.** After 001. Edit
  `shared/css/states.css` only for visual alignment. Loading, empty, error,
  live-status and nudge components reuse tokens; errors remain distinguishable.
  Do not change event/state meaning. Check A+B.
- [ ] **TASK-012 — Shared rendered content.** After 001. Edit
  `shared/css/markdown.css` only where necessary for v5 ink, spacing, rules,
  quote/code surfaces. Preserve readable code, long-link wrapping, copy actions,
  and content hierarchy. No markdown parser changes. Check A+C.
- [ ] **TASK-013 — Provider/Settings shared rows.** After 008. Edit
  `shared/css/settings.css`. Make the existing shared settings rows/forms use
  the same borders, spacing, and control tokens. Do not copy this stylesheet
  into either page directory. Check A and both screens, including validation.

### Phase 2 — chat CSS; these tasks are sequential

- [ ] **TASK-014 — Compact rail dimensions.** After 007. Edit rail layout
  rules in `pages/chat/chat.css`. Target 112px desktop rail and 92px bot slot
  from v5; keep expanded sidebar behavior. Existing 48px expanded and 56px
  compact SVG remain fallback sizes until the 3D branch is approved. Check A+B.
- [ ] **TASK-015 — Rail label and selection.** After 014. Edit rail-specific
  rules in `pages/chat/chat.css`. Display the real bot name below its avatar,
  v5 selected wash and inset accent. Selection belongs to the button, not an
  SVG halo. Preserve unread badges and accessible names. Check A+B.
- [ ] **TASK-016 — Expanded/session rows.** After 015. Edit the existing
  bot/session list rules in `pages/chat/chat.css`. Reuse shared row colors;
  retain per-bot `+ Phiên mới`, subordinate indentation, group distinctions,
  and session actions. Do not add avatars to subordinate solo sessions. Check A+B.
- [ ] **TASK-017 — Date/title spacing.** After 016. Edit chat date/title rules
  in `pages/chat/chat.css`. Match the understated 12px-style divider and thin
  ticks; preserve real date generation. No hardcoded “15 tháng 10”. Check A+B.
- [ ] **TASK-018 — Message appearance.** After 017. Edit message/brand rules
  in `pages/chat/chat.css`. Thin 2px stripe, ~16px inset, restrained titles,
  metadata and paragraph spacing. Keep responding-bot name/avatar/tint,
  disclosure toggles, direct replies, and message actions. Check A+B.
- [ ] **TASK-019 — Composer container and textarea.** After 018. Edit only
  composer container/label/textarea rules in `pages/chat/chat.css`: v5 input
  shade, 14px radius, thin border, subtle focus, approximately 100px draft
  height with viewport-safe limits. Preserve resizing and existing submit/IME
  handling; no JavaScript changes. Check A+B.
- [ ] **TASK-020 — Composer toolbar.** After 019. Edit its rules in
  `pages/chat/chat.css`. Align existing left tools/right model/send controls
  with v5, using the shared accent/control tokens. Keep reasoning, context,
  advanced, stop/continue, attachment and unavailable-action states. Do not
  replace real model options with demo options. Check A+B.
- [ ] **TASK-021 — Quiet watermark.** After 020. Edit existing watermark
  rules in `pages/chat/chat.css`. Reduce prominence to the v5 treatment without
  adding another decorative background or masking text. Keep it noninteractive
  and hidden from assistive technology. Check A and a long conversation.

### Phase 3 — other screens inherit; do not redesign them

For each task, first view the page with Phase 1 applied. If shared CSS already
solves it, report **no page-specific change needed** rather than inventing work.
If a duplicated component rule belongs in shared CSS, propose that exact small
shared edit to the coordinator; do not modify another worker's file.

- [ ] **TASK-022 — Dashboard layout deltas.** After 010–012. Inspect/edit
  `pages/dashboard/dashboard.css`. Keep only Dashboard-specific placement;
  remove redundant theme overrides when inherited equivalents are verified.
  Preserve all sections and view switching. Check C.
- [ ] **TASK-023 — Cost view deltas.** After 022. Inspect/edit
  `pages/dashboard/cost.css`. Reuse shared cards, journal rows, pagers and
  palette; retain amounts, bot breakdowns, filters and request detail. Check C.
- [ ] **TASK-024 — Usage chart colors.** After 001+022. Inspect/edit
  `pages/dashboard/usage.css`. Use shared chart tokens, retain input/cache/output
  distinctions and chart axes. Do not move unrelated chart JS. Check C.
- [ ] **TASK-025 — Trace deltas.** After 022. Inspect/edit
  `pages/dashboard/trace.css`. Keep trace-specific layout, code light/dusk
  pairing, errors and delegation readability. Reuse the shared journal kit.
  Do not touch `trace.html` redirect/deep links. Check A+C.
- [ ] **TASK-026 — Memories deltas.** After 010–012. Inspect/edit
  `pages/memories/memories.css`. Inherit common surfaces and controls; retain
  memory chart, list, search, pending queue and edit feedback. Its chart already
  consumes shared chart-renderer class names: preserve them. Check A+D.
- [ ] **TASK-027 — Profile deltas.** After 010–012. Inspect/edit
  `pages/profile/profile.css`. Keep editor-specific placement; inherit shared
  controls/surfaces. Preserve file selection, unsaved state, save/cancel and
  validation feedback. Check A+E and a test-profile editor flow.
- [ ] **TASK-028 — Provider deltas.** After 013. Inspect/edit
  `pages/provider/provider.css`. Keep provider-detail layout only; inherit
  shared setting rows and controls. Preserve validation, secret handling and
  model-fetch states. Do not edit provider JS or actual user configuration.
  Check A+E plus the browser form states; backend validation tests do not
  prove frontend form rendering.
- [ ] **TASK-029 — Settings reuse check.** After 013. Inspect
  `settings.html` against `provider.html`. Change markup only if a missing
  existing shared class prevents reuse; preserve IDs, input names, handlers,
  preview and preference persistence. No new settings.css. Check A+E and real
  theme/type-scale/reduced-motion controls in a test profile.

### Phase 4 — responsive behavior

- [x] **TASK-030 — Shared narrow-screen layout.** After 010+013 and 022–029.
  Edit media rules in `shared/css/kit.css`. Check all six screens at 320, 375,
  414 and 768px plus desktop. No root horizontal scrolling, clipped controls,
  unreachable dialogs or broken list-to-detail/back flows. Do not replace
  existing mobile navigation with the reference's purely visual layout. Check A+C.
- [x] **TASK-031 — Chat narrow-screen layout.** After 021+030. Edit media
  rules in `pages/chat/chat.css`. Use v5's 700/420px values as visual reference,
  not a reason to remove the app's pick/back behavior. Wrap toolbar groups,
  not action labels; keep textarea/send/stop reachable with long model/bot names.
  Test 320/375/414/768px, long transcript and large type preference. Check A+B.

### Phase 5 — approved 3D branch

Keep these sequential except where the coordinator explicitly assigns disjoint
files. Do not copy reference `BOT_REGISTRY`, hardcoded `teamMembers`, sidebar
fixtures, demo send handler, or demo timeout transitions into production.

Execution interface frozen by the coordinator before TASK-033:

- `createWoodAvatarModel(THREE, { bot, members = null, palette = {}, canvasFactory })`
  returns `{ group, animate(elapsed, { reducedMotion, gazeX, gazeY }), dispose() }`.
  The model uses existing variant/state helpers. `members` is the real ordered
  group, not a list inferred from bot names. Model imports are DOM/WebGL-inert.
- `createAvatar3DController()` returns `{ mount, update, thumbnail, dispose }`.
  `mount(slot, { bot, members })`, `update({ bot, members })`, and
  `thumbnail({ bot, members })` are async; thumbnail resolves a data URL or null
  on unavailable WebGL. `dispose()` is synchronous and final.
- The controller lazily imports `../../vendor/three-r128.min.js` and reads
  `globalThis.THREE`; no global script tag/index.html change is needed.
- Controller-owned palette values come from computed shared CSS tokens. Base
  colors reuse `--note-*`; wood light/side values use `--wood-<variant>-light`
  and `--wood-<variant>-side`, sourced from the reference registry.
- Existing sidebar integration supplies records and owns SVG fallback markup.
  One active canvas is reused/reparented. Stale async requests cannot overwrite
  a newer selection or resurrect a disposed controller.

Texture creation may allocate a canvas only when invoked, not during import.
Imports must not touch WebGL/DOM until mounting, so Node tests and SVG-only
consumers continue working.

- [x] **TASK-032 — Vendor only.** After B1. Extract the exact r128 runtime
  bundled in the reference to `vendor/three-r128.min.js`, retaining its MIT
  notice/license. No CDN or version upgrade. Record origin/checksum. Do not
  wire it to a page yet. Done: file/license reviewed; unrelated files untouched.
- [ ] **TASK-033 — Single wood model base.** After 032. Create
  `shared/js/bot-avatar-3d-model.js`. Port only the reference texture/materials,
  note head/stem and approved variant geometry. Reuse `avatarVariantFor` from
  the existing SVG module; use supplied palette data/tokens, not bot-ID guesses.
  Do not initialize a renderer or change sidebar code. Check A and a model fixture.
- [ ] **TASK-034 — Model expressions/effects.** After 033. Extend that same
  model file with the reference eyes and thinking/running/done/error/offline
  effects. Consume existing `avatarStateFor`; draft text must not synthesize
  backend status. Dispose shared geometry/materials/textures safely. Check A
  and the seven existing states in a browser fixture.
- [ ] **TASK-035 — Group geometry.** After 034. Extend the model file for real
  member arrays, reusing each member's variant/color and `teamBadgeState`.
  Keep dedicated two-member layout, up to three visible heads and the existing
  +N indication; preserve member order. No hardcoded three-member team.
  Check 0/1/2/3/4+ members and mixed offline/error/running states.
- [ ] **TASK-036 — Static renderer lifecycle.** After 035. Create
  `shared/js/bot-avatar-3d.js`. Mount one local Three renderer lazily, resize to
  its actual slot, cap device pixel ratio as in the reference, and render a
  supplied model. Supply cleanup for observer/listeners/context resources.
  WebGL failure leaves the existing SVG visible. Check repeated mount/dispose.
- [ ] **TASK-037 — Animation and resource limits.** After 036. Extend that
  renderer file with reference motion/state effects. Respect both
  `prefers-reduced-motion` and Thyca's stored reduced-motion preference. Pause
  when hidden/offscreen; cancel frames and listeners on disposal. No renderer
  per bot, per message, or repeated RAF loop after sidebar refresh. Check A+B
  and repeated bot changes in the browser.
- [ ] **TASK-038 — Inactive thumbnails.** After 037. Extend the renderer file
  to capture/cache snapshots for inactive slots and regenerate only when
  relevant variant/color/member/state inputs change. Use one renderer; do not
  rebuild a thumbnail on every animation frame. Preserve SVG on capture error.
  Check no-WebGL, cache invalidation and group-member changes.
- [x] **TASK-039 — Sidebar mounting only.** After 038+015. Wire the renderer
  into `pages/chat/team-sidebar.js`; add local vendor loading to `index.html`
  only if the chosen module boundary requires it. Use current bot/group
  selection; preserve row handlers/ARIA/unread/session actions. A fresh browser
  defaults to compact v5 rail; explicit stored expanded/compact preferences
  continue to win. Repeated render
  must reparent/reuse one canvas rather than leak it. Check A+B.
- [x] **TASK-040 — State and pointer integration.** After 039. Extend the
  integration in `pages/chat/team-sidebar.js`: feed actual refreshed bot states
  to the renderer; gaze may follow the conversation pointer without intercepting
  clicks. Ignore stale selection updates; reset on pointer leave. No mock
  thinking/done timers, extra API polling, or state-contract changes. Check A+B
  including selection during an active/background turn.
- [x] **TASK-041 — Shared 3D slot styling.** After 040. Edit
  `shared/css/bot-avatar.css` and only the necessary rail sizing rules in
  `pages/chat/chat.css`. Target v5 64px inactive and 86px active desktop slots,
  with narrow-screen variants. Preserve existing expanded/SVG fallback behavior
  and hidden decorative accessibility. No duplicated avatar CSS per screen.
  Check A+B and update only explicitly superseded visual-size expectations.

### Phase 6 — integration, review, documentation

- [ ] **TASK-042 — Cross-screen reuse audit.** After all approved CSS tasks.
  Review only. Check the six HTML import chains, raw color/URL duplicates and
  component rules in page CSS. Confirm one token source, one shell/component
  kit, existing shared settings rows, and no copied v5 page themes. Return
  exact findings; each fix gets its own small task, not an unrestricted cleanup.
- [ ] **TASK-043 — Actual-product acceptance.** After 042 and approved avatar
  work. No runtime edits. Run A–E, full `uv run pytest -q`, and `git diff --check`.
  Compare reference/app at identical desktop width/zoom; inspect all six screens
  at 320/375/414/768px. Check console/network, local assets offline, dialogs,
  settings preferences, long content and fallback. Use test sessions for real
  send/stream/stop/reload/recovery; no fabricated pass if provider unavailable.
- [ ] **TASK-044 — Independent review.** After 043. Review only, by a reviewer
  other than the implementer. Check product-wide CSS impact, identity/state
  preservation, Three licensing/loading/lifecycle, security, accessibility,
  missing states, and test expectation changes. No automatic fixes or commits;
  coordinator verifies findings and creates small fixes. If unavailable, record
  independent review as outstanding, not “ready to merge”.
- [ ] **TASK-045 — Record accepted theme changes.** After implementation and
  review, edit only the relevant WebUI decision section in
  `.agents/AGENT_RULES.md` and this plan. Record new image/palette, actual avatar
  decision, preserved behavior, exact checks and limitations. Do not rewrite
  unrelated guides, claim B1 approval retrospectively, or mark incomplete work done.

## Execution evidence (2026-10-15)

- Baseline, before any runtime edits: `uv run pytest -q` → **1406 passed,
  2 failed, 6 errors, 1 warning** in 126.80s.
- Existing failures: `tests/test_serve_chat.py::test_webui_keeps_streaming_card_across_session_switch`
  and `::test_webui_follows_a_turn_it_did_not_start` pin an older
  `createLiveStatus` call signature. Six setup errors originate from the
  existing `tests/test_webui_concurrent_streams.py` Node fixture. They are not
  permission to change streaming logic as part of this visual transplant.
- Baseline output: `.pi/background-tasks/instances/run_20261015T063943632_p362726_116742/bg_6dbeee5d2c384a458d048dd4c2db85ca/output.log`
  (local task artifact, not a product file).
- Worker model verified from the active runtime catalog:
  `openai-codex/gpt-6-luna`. Workflow `wf_efdfd8152d5a` uses that model explicitly
  on initial worker calls, then resumes each lane for its next small task.
- Browser baseline uses a HEAD snapshot in `/tmp/thyca-v5-baseline`, served
  read-only on loopback port 8876; API GETs proxy the existing app, writes are
  rejected. This avoids writes to user sessions/settings during layout checks.
  Browser baseline passed 30 page/width checks (six screens × five widths),
  no root horizontal overflow or page JS errors; 12 screenshots plus a contact
  sheet are under `/tmp/thyca-v5-baseline-shots` and
  `/tmp/thyca-v5-baseline-contact.png`.
- New contract test `tests/test_webui_avatar_3d.py` is owned by a separate Luna
  worker. Parent reviewed its resource-disposal, input immutability, import
  inertness and reduced-motion assertions; final integrated rerun remains required.

- Parent verified TASK-032: vendored runtime matches the reference's bundled
  script byte-for-byte after trimming outer whitespace. SHA-256:
  `9274bbcec8d96168626c732b5d31c775aa8cfb7eaa0599bec0c175908a2c1ce2`.
  MIT license retained separately; provenance uses the portable source basename.

- Parent integration fixes so far: restored color-only shadow tokens (their
  consumers provide offsets), kept dusk desk aliases coherent with the new
  desk primitive, added offline/cream/ink/gold wood tokens, and preserved the
  tiny gold star separately from the Thyca body tint. Shared theme tests:
  **10 passed**. Parent-reviewed model regressions + theme: **11 passed**.
- Independent model review found wrong note-token mapping, a missing Thyca flag,
  and missing offline wood coloration. Focused fixes and behavioral regressions
  were verified; reviewer approved those specific fixes. This is not renderer
  or sidebar acceptance.
- First integrated six-screen layout run: **30 checks, no root overflow or page
  JS errors**, 12 screenshots under `/tmp/thyca-v5-first-integrated-shots`.
  These were captured while implementation was in flight, so final rerun remains.
- Real WebGL component smoke caught blank inactive thumbnails and a second
  renderer. A fix was prepared without editing the workflow-owned source:
  `/tmp/thyca-v5-renderer-single-context.js`. An HTTP-only test override verified
  nontransparent pixels for all seven variants and 0/1/2/3/5-member groups,
  **one WebGL context, one peak RAF, canvas reparenting, zero pending frames
  after dispose, and no-WebGL SVG fallback**. It still needs source ownership
  handoff, application and final integrated rerun. More renderer review notes:
  `/tmp/thyca-v5-renderer-review-notes.md`.
- Existing visual test expectations were updated only for the accepted v5
  theme/rail; behavioral, identity and SVG fallback checks remain. Parent must
  inspect final test results before closing any task.

## Execution evidence — Devin handoff (2026-10-15, second session)

- Codex quota ended the workflow before TASK-030/031/039; Devin continued as
  coordinator+implementer on the same checkout.
- TASK-030/031 verified no-op: `/tmp/thyca-v5-browser-check.py` rerun → 30
  checks (6 screens × 1440/768/414/375/320), zero root overflow, zero page
  errors; chat conversation at 768–320px keeps composer/textarea/send/toolbar
  reachable, pick/back flow intact (`/tmp/thyca-v5-chat-mobile/`).
- TASK-039/040: `team-sidebar.js` mounts `createAvatar3DController` on the
  selected slot (bot row, or group row while a shared session is open);
  other slots get cached `thumbnail()` `<img>` over hidden SVG; fresh
  browsers default to the compact rail (`thyca.teamRail` unset → rail),
  stored "0"/"1" still win. Pointer gaze on `.chat-canvas` is passive and
  resets on leave. Node minimal-DOM harnesses skip the feature via a
  capability gate (no observers/documentElement/matchMedia → SVG only).
- Controller additions in `bot-avatar-3d.js`: `gaze(x, y)` (additive, the
  frozen mount/update/thumbnail/dispose interface unchanged), an
  input+palette keyed fast path so repeated `mount()` reparents the canvas
  without rebuilding the model, and the reference lighting/camera
  (ambient 0xFFF3DE .78, key 0xFFF7E8 .95 @(4,6,5), rim 0xE8A86B .45,
  sRGBEncoding + ACESFilmic 1.05, 36° camera @(0,.4,7.2), thumbnails
  @(0,.12,6.1) with base rotation .02/.38/0). shadowMap stays off — no
  mesh casts/receives shadows in this port.
- TASK-041: rail slots now v5 sizes — `.is-rail .team-avatar` 4rem (64px),
  active slot 5.375rem (86px), active row min-height 6.875rem (110px) and
  first row widened so the live canvas never covers the bot name.
  `.team-avatar > canvas / img.team-avatar-3d` overlay rule lives in
  `shared/css/bot-avatar.css`. Superseded size pin in
  `tests/test_team_frontend.py` updated (3.5rem → 4rem).
- Browser verification on the live app (127.0.0.1:8765): fresh profile
  boots into compact rail, exactly 1 canvas on the selected slot and
  thumbnails elsewhere; canvas follows selection across bot clicks and a
  group row; rail toggle off/on keeps a single canvas; `--disable-webgl`
  leaves 4 SVGs, 0 canvases, no overflow, no errors. Avatar component smoke
  (`/tmp/thyca-v5-avatar-smoke.py`, repointed to :8765): 7 variants + 0–5
  member groups all render non-transparent PNGs, 1 WebGL context, 1 peak
  RAF, 0 pending frames after dispose, canvas reuse confirmed.
- Full `uv run pytest -q`: 1407 passed, 5 failed, 6 errors. Baseline
  failures unchanged (2× test_serve_chat createLiveStatus pin, 6×
  concurrent_streams Node fixture). New superseded visual pins fixed in
  tests/test_webui_markdown.py (reading font now inherited from shared
  `.screen-shell`; flat `.memory-card`). `tests/test_config.py`
  `test_f6_unknown_keys_warn_listing_keys_but_load` failed once inside the
  full run and passes in isolation and per-file rerun — recorded as flaky
  ordering, unrelated to the webui diff.
- TASK-042 light audit: `rg` over `pages/*/*.css` shows no raw hex colors
  or image URLs — palette/artwork live only in shared tokens/kit.
- Still open: TASK-043 full acceptance partially evidenced above (no real
  send/stream/stop writes performed against the user's profile); TASK-044
  independent review outstanding; TASK-045 doc record pending.

## Scheduling and handoff

- Phase 1 tasks editing `tokens.css` or `kit.css` are sequential per file.
  `states.css`, `markdown.css`, and `settings.css` tasks may overlap once their
  token prerequisites are stable and common test-file ownership is assigned.
- Phase 2 tasks all edit `chat.css`: sequential. Do not allocate them to six
  workers at once. Phase 3 page tasks can run concurrently on distinct files.
- TASK-030 edits the shared kit after page review; TASK-031 edits chat after it.
  The 3D phase must not overlap another editor of its integration/CSS files.
- Tests are shared files too. Reserve their ownership; serialize changes to
  `test_team_frontend.py` / `test_webui_theme.py` even if production files differ.
- Handoff includes source commit, task ID, prerequisite evidence, changed files,
  accepted decisions/open gates, exact test results and screenshots. No worker
  starts a next task based only on an unchecked plan number.

## Completion definition

The result is complete only when the approved visual transplant and inherited
cross-screen theme are verified; no essential real action has disappeared;
background artwork is retained; repeated CSS belongs to shared owners; all
required checks have evidence; and independent review is resolved. Any unapproved
3D branch, unavailable provider integration, skipped browser check, or existing
failure must be reported explicitly. This plan does not authorize a Git commit.
