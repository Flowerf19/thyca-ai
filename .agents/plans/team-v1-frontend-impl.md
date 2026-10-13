# Team v1 — frontend lane implementation plan

Branch: lane branch of this worktree (`git rev-parse --abbrev-ref HEAD` at report time).
Master: `docs/team-plan` → `.agents/plans/team-v1.md` (§6 contract frozen).
Design: `docs/team-frontend` → `.agents/plans/team-frontend.md` (r9), r9 supersedes
session-list sidebar (two-level Bot → Session) and note-paper "Quyết định" box.

No sub-agent tool is available in this environment — all work is done
sequentially by the lane lead, who also does the final self-verification.
An independent review is requested via a separate model call if possible,
otherwise recorded as outstanding.

## Test-pin constraints (must not break)

- `tests/test_serve_chat.py::test_chat_js_shipped`, `test_chat_nav_opens_new_session`,
  `test_chat_mobile_pick_pattern`, `test_idle_remember_nudge_in_webui`,
  `test_context_nudge_in_webui`, `test_empty_submit_nudges_without_sending` pin
  substrings in `index.html`, `app.js`, `composer.js`, `sessions-sidebar.js`,
  `turn-follow.js`, `chat-view.js`, `transcript.js`, `live-status.js`,
  `chat-thinking.js`, `streams.js`, `states.css`, `chat.css`.
  → Existing chat files: additive edits only; never remove pinned strings.
  → `newSession` keeps `state.activeId = "";` and no `postJson`.
- `tests/test_cost_journal.py` pins exact counts over concat
  (`cost.js`+`cost-view.js`+`cost-rows.js`+`pager.js`): `view = null;` ×3,
  `if (!view) return;` ×3, `modelsPage = 1;` ×4, `sessionsPage = 1;` ×2, etc.
  → Do NOT add those literal strings to cost files. Cost-per-bot lives in a NEW
  module `cost-bots.js` (not in the concat) + pure `aggregateBotCost` export in
  `cost-data.js` (eval-imported directly — new exports are safe).
- `tests/test_trace_journal.py` pins substrings over
  (`trace.js`+`trace-view.js`+`trace-turns.js`+`trace-deeplink.js`+`pager.js`).
  → Delegation tree lives in NEW `trace-delegation.js` + pure
  `buildDelegationTree` export in `trace-data.js`. `trace.js` may gain one
  import line (no pinned-string collision).
- `tests/test_webui_memory_edit.py` strips imports from `memories.js` and runs
  it under a minimal DOM harness expecting `{state, el, render, startEdit,
  cancelEdit, mutateMemory}`.
  → Do NOT touch `memories.js`. Pending tab lives in NEW `team-pending.js`,
  wired from `memories.html` with its own `<script type="module">`.
- `tests/test_dashboard_journal.py:376` pins the `fetchAllTraces` import line
  in cost files — keep it.

## New files (all under thyca/webui/**)

1. `pages/chat/team-data.js` (FE-1/2/5, pure, DOM-free, node-testable):
   - `BOT_TINTS = ["accent","botanical","success-ink","ink","accent-deep"]`,
     `tintForBot(botId, tint)` — card `tint` wins when known, else hash fallback,
     `thyca` pinned to `accent`. `tintVar(tint)` → `var(--color-…)`.
   - `normalizeBots(payload)` — `GET /api/bots` contract shape
     `[{id,name,role,description,tint,status}]`, tolerant of missing backend
     (`{bots: []}` / fetch failure → `[]`).
   - `sessionsForBot(sessions, botId)` — filter by `session.bots[]`; sessions
     without `bots` count as `thyca`-only (matches contract default
     `participants=["thyca"]`).
   - `mentionCandidates(bots, query)` — case-insensitive id/name match.
   - `splitExchange(messages)` — group inter-bot `delegate|step|status` runs
     (meta.kind) for the "Xem các bot đã làm gì" collapse.
   - `buildDelegationTree(messages)` — shared with trace lane: group by
     `trace_id`, parent via `parent_span_id` (also exported from trace-data.js;
     team-data re-exports to keep chat free of dashboard imports).
   - `aggregateBotCost(rows)` — group cost rows by `bot_id` meta (shared with
     cost lane; same re-export rule).
2. `pages/chat/team-sidebar.js` (FE-1, DOM): two-level sidebar. Level 1 bot
   list (tint dot + name + status); click bot → its sessions (1-1 + shared
   labeled); Back "‹ Đội" to level 1; "＋ Phiên chung" button opens a small
   picker (checkbox list of bots) → `POST /api/sessions {bots:[…]}` per
   contract; on failure (backend not yet) shows honest "chưa có backend" note.
   Graceful: `GET /api/bots` fails → keep legacy session list untouched and
   show a muted "Đội bot chưa sẵn sàng" line (no fake data).
3. `pages/chat/team-mention.js` (FE-2, DOM): `@` popup in composer —
   ↑/↓/Enter/Esc, filters via `mentionCandidates`, last row always
   "Nhờ Thyca tạo bot mới" (inserts "Nhờ Thyca tạo bot mới: " text, no fake
   bot creation). Positioned above composer, token-only CSS.
4. `pages/chat/team-cards.js` (FE-2/3, DOM): renderers for transcript meta
   cards driven by contract meta, no backend behavior invented:
   - question cards (create-bot / bash-permission reuse): title + option chips;
     click chip sends the option text as a normal turn (user-visible, honest).
     No "Tạo bot"/approve button.
   - error card: "Làm dở tới đâu" checklist + "Làm tiếp thế nào?" chips.
   - "Quyết định"/handoff rendered as plain text lines (no note-paper box).
   - "Xem các bot đã làm gì" `<details>` collapse for inter-bot exchange.
   Cards render only when message meta carries the matching shape; otherwise
   the transcript path is byte-identical to today (no visual regression).
5. `pages/chat/team-drawer.js` (FE-4, DOM): read-only bot drawer
   (`<dialog>` in index.html): name/role/description, "Làm được gì/Không làm"
   from card, "Nâng cao" `<details>` with raw specs, "Nhắn Thyca" button
   (inserts "Thyca ơi, về <bot>…" into composer). Opens from participant-strip
   name click. Unavailable backend → drawer shows "chưa có hồ sơ bot" state.
6. `pages/memories/team-pending.js` (FE-5, DOM): "Chờ cho cả đội nhớ" tab —
   hidden unless pending > 0 (`GET /api/memory/candidates`); bot filter
   chips; per-item "Cho cả đội nhớ" (`POST …/approve`) / "Bỏ qua" (`…/reject`).
   Backend missing → tab stays hidden (contract-blocked, reported).
7. `pages/dashboard/cost-bots.js` (FE-5, DOM): "Chi phí theo bot" block on the
   Cost view, fed by `aggregateBotCost` over the same trace snapshot the Cost
   journal pages (wired from `cost.js` with one import + one call; no pinned
   strings added). No snapshot → honest "chưa có dữ liệu bot" line.
8. `pages/dashboard/trace-delegation.js` (FE-5, DOM): delegation-tree block on
   the Trace view from `buildDelegationTree` over the loaded trace detail
   messages (wired from `trace.js`). No `trace_id` meta yet → empty-state line.

## Edits to existing files (additive, token-only CSS)

- `shared/css/tokens.css` (FE-6): add `--color-danger: var(--color-accent-deep);`
  in `:root` + comment. No other literal colors anywhere.
- `shared/css/states.css`: change `.backend-state.is-error` fallback
  `var(--color-danger, var(--color-accent))` → `var(--color-danger,
  var(--color-accent-deep))` (r9 decision; only test pin is `.live-status`).
- `index.html` (FE-2/4): participant strip container `#team-strip` in
  `.chat-title-head`; composer hint line made visible
  ("Gõ @ để gọi một bot"); "Nâng cao ▾" button (no popover, per design);
  mention popup container `#mention-popup`; bot drawer `<dialog
  id="bot-drawer">`. Keep all pinned ids/structure; keep `screen-shell` out.
- `pages/chat/chat.css`: team styles, all `var(--…)` tokens, sticky
  `.chat-title-head`, shimmer honoring `prefers-reduced-motion`. No new
  literal colors.
- `pages/chat/app.js`: import + init the four team modules via the existing
  `hub` (no pinned-string changes).
- `pages/chat/transcript.js`: route assistant messages with
  `meta.bot_id`/`meta.kind` through team-cards renderers; plain path
  unchanged. Keep `formatMarkdown`, `chat-brand` usage.
- `pages/chat/composer.js`: hook `@` detection → team-mention (additive).
- `pages/chat/sessions-sidebar.js`: expose hook for team-sidebar filtering
  without changing pinned functions.
- `memories.html`: pending-tab nav button (hidden by default) + script tag
  for `team-pending.js`; mobile `.memory-view` section for it.
- `settings.html` + `pages/settings/settings.js` (FE-4): "Bot" nav item +
  section listing bots from `GET /api/bots` (read-only rows opening the same
  drawer? drawer lives on chat page — settings rows link back with bot id;
  simplest honest: read-only list + "Nhắn Thyca trong Chat" hint).
- `dashboard.html`: mount points `#cost-bots`, `#trace-delegation`
  (empty-state owned by the new modules).
- `pages/dashboard/cost-data.js`: add `aggregateBotCost` (pure export only).
- `pages/dashboard/trace-data.js`: add `buildDelegationTree` (pure export only).
- `pages/dashboard/cost.js`, `trace.js`: one import + one boot call each.

## Checks per task

- After each task: `node --check` on touched/new JS files.
- New pure logic covered by a new pytest file `tests/test_team_frontend.py`
  (node-eval style, DOM-free imports from `team-data.js`, `cost-data.js`,
  `trace-data.js`) — must not weaken any existing test.
- Focused suites: tests matching webui/chat/dashboard/memories/trace, then
  full `uv run pytest -q` if feasible.
- Forbidden: any backend Python, `thyca/llm/pricing.py`,
  `tests/test_llm_pricing.py`. Read-only `git status --porcelain` at the end.

## Order

FE-6 (token) → FE-1 (sidebar+team-data core) → FE-2 (strip/mention/transcript)
→ FE-3 (cards) → FE-4 (drawer+settings) → FE-5 (pending/cost/trace) →
review → full tests → report.

## Lane outcome (2026-10-13, detached HEAD 228b738)

- FE-1 done (team-data.js + team-sidebar.js; graceful legacy fallback).
- FE-2 done (strip, mention, transcript team path, sticky title, hint, Nâng cao).
- FE-3 done (question/error cards, no approve button).
- FE-4 done (drawer + Settings › Bot; drawer opens from strip).
- FE-5 done (pending tab hidden-unless-pending; cost-bots block; delegation tree).
- FE-6 done (--color-danger from accent-deep; no new literals).
- Review: no sub-agent tool in this env — self-review of the actual diff,
  6 findings fixed (mobile pick CSS, back-button visibility, empty-article
  guard, picker state, harness typeof guards, trace.css font pin).
  Independent review outstanding.
- Tests: 11 new pass; full `uv run pytest -q` → 1186 passed, 1 failed
  (test_memory_fallback_safety pre-existing backend date issue, untouched).
- Known limits: bot reasoning/thinking not shown in team blocks; date/version
  corner move is fixed-position; all contract-blocked screens render honest
  unavailable states until the backend lane lands.
