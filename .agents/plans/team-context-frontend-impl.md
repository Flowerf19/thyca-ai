# Team context — frontend lane execution plan (CFE-1..CFE-3)

Branch state: detached HEAD `aa8fabd` (ff-merged `integration/team-v1` as authorized).
Master: `.agents/plans/team-context.md` (Layer 5 = this lane's BE+FE scope).
Prior FE plan: `.agents/plans/team-v1-frontend-impl.md` (all FE-1..FE-6 landed).
Skill: `thoughtful-coder`. No git mutating commands after the merge.

## Backend contract as implemented on this branch (read, not invented)

- `thyca/sessions/wire.py::session_summary` / `session_detail` carry
  `bots: string[]`, default `["thyca"]` when no bots meta line. Detail has
  NO `updated_at` (summary does); messages carry `{role, content, ts, meta?}`.
- `POST /api/sessions {bots:[...]}` → `ChatApp.create(bots)` returns the full
  detail including `bots` (`thyca/app/chat_app.py:399-409`); `_clean_participants`
  validates ids via the bot registry (unknown → 404, paused/empty → 400).
- Solo-room precedent is backend-side only: `thyca/bots/scheduler.py::_solo_room`
  finds `bots == [bot_id]` exactly, else `app.create(bots=[bot_id])`.
- **DM marker gap (honest): no `dm` / `main` / `primary` / `is_dm` marker exists
  in any session payload, route, or store meta on this branch** (grep over
  `thyca/` confirms; only `_solo_room` documents the solo convention).
  → CFE-1 uses the authorized fallback: client-side create-or-reuse of a solo
  session via existing `POST /api/sessions {bots:[bot]}` semantics + localStorage
  remember (`thyca.team.dm.v1`). Reported as gap, not shape invention.
- Transcript rows: `meta.{bot_id, kind, trace_id, parent_span_id, span_id}`;
  sidecar kinds `naming`/`compaction` are transcript-invisible
  (`transcript.js` skips them) → excluded from unread attribution.
- `GET /api/bots` → `{bots: [...]}`; `GET /api/sessions` → `{sessions: [...]}`.
- `renderDetail` (app.js) runs on every open + every turn completion/poll
  (turn-follow.js) → the mark-seen hook. `refreshSessions` runs after turns →
  the staleness detector. No polling exists; focus/visibility refresh is added
  (team mode only).

## CFE-1 — bot main window (team-sidebar.js + team-data.js, additive)

Pure helpers in `team-data.js` (node-testable, new tests below):
- `isSoloSession(session, botId)` — `bots` is exactly `[botId]`.
- `findMainSession(sessions, botId, rememberedId)` — remembered id if still a
  solo session of that bot; else newest solo by `updated_at` desc; else null.
- `partitionBotSessions(sessions, botId, mainId)` — `{main, others}`; `others`
  keeps list order (recency) and excludes `main`.
- `isMainSessionDetail(detail, botId)` — created-session guard: `detail.bots`
  equals `[botId]` (mirrors `verifySharedSession` honesty for the shared flow).

DOM in `team-sidebar.js`:
- Level-1 click → `openBot(botId)` becomes async: set level 2 + render
  immediately (known sessions), then resolve the DM: remembered/newest solo in
  `hub.state.sessions` → remember + `hub.loadSession(id)`; else
  `POST /api/sessions {bots:[botId]}` → `isMainSessionDetail` check →
  remember + `hub.afterTeamSession(detail)`. Failure (paused bot, backend
  ignoring `bots`, network) → honest status line in level 2, other sessions
  still listed; never opens a fake main.
- Level 2: main window pinned first with a `Cửa sổ chính` tag; remaining bot
  sessions under a `Phiên khác` secondary heading (shared ones keep `· chung`).
  `DM_KEY` remember updated whenever a solo session is opened/confirmed.
- Mobile: exiting pick-shell when the DM opens (same `matchMedia` pattern as
  row taps). Shared-session picker/create flow untouched.

## CFE-2 — unread (team-data.js pure + new team-unread.js + wiring)

Pure helpers in `team-data.js`:
- `isBotAttributed(message)` — `meta.bot_id` non-empty, `role !== "user"`,
  `kind ∉ {"naming","compaction"}`.
- `botUnreadCount(messages, sinceTs)` — bot-attributed rows with `ts > sinceTs`
  (string compare, ISO `ts`; rows without `ts` never count).
- `summarizeUnread(seen, summary, messages?)` → `{unread, count, fresh}`:
  no `seen` → `{unread:false, count:0, fresh:true}` (caller baselines, never
  mass-marks); with `messages` → attributed count; list-only → stale when
  `summary.updated_at > seen.ts || summary.message_count > seen.count`
  (dot, `count:0`). List-level staleness is honestly bot activity: the client
  only sends turns in the open session, and opening a session clears it
  (background writers are bots: scheduler DM writes, other-tab turns).

New `pages/chat/team-unread.js` (localStorage only, try/catch guarded):
- `SEEN_KEY "thyca.team.seen.v1"` (`{sessionId:{ts,count}}`), `DM_KEY
  "thyca.team.dm.v1"` (`{botId:sessionId}`); getters/setters + `markSeen(id,
  detail|summary)` (detail: max message `ts` + message length; summary:
  `updated_at` + `message_count`; both also serve as silent baseline).
- Re-exports nothing inventive; imported by team-sidebar + app wiring.

Wiring (all additive, guarded `typeof` like existing team calls):
- `app.js::renderDetail` → `markTeamSeen(detail)` before `renderSessions()`.
- `app.js` hub gains `teamRefresh: async () => { await refreshSessions();
  syncTeamSessions(); }` (imports already present).
- `team-sidebar.js::initTeamSidebar` registers `focus` + `visibilitychange`
  → `hub.teamRefresh()` only when team mode is live (`bots.length`).
- Badges: level-2 session rows get `.team-unread` (count or dot);
  level-1 bot rows aggregate (`Σ counts` + dot for count-less stale).
  Legacy `#session-list` rows untouched.

CSS (`chat.css` only, tokens only — no literal colors):
- `.team-unread` pill (`var(--color-accent)` bg, `var(--color-accent-ink)` text;
  dot variant), `.team-main-tag`, `.team-secondary-title`. FE-6 test still green.

## CFE-3 — regression safety (must keep working)

Untouched files: `sessions-sidebar.js`, `composer.js`, `transcript.js`,
`team-mention.js`, `team-drawer.js`, `team-strip.js`, `team-cards.js`,
`team-pending.js`, cost/trace dashboard files, `memories.js`. Legacy fallback
in `initTeamSidebar` (no `/api/bots` → legacy list + muted note) unchanged.
Thyca-only users never enter team mode → zero behavior delta.

## Checks

1. `node --check` on every touched/new JS file.
2. Extend `tests/test_team_frontend.py` (CFE-1/2 pure only — never weaken):
   `isSoloSession`, `findMainSession` (remembered-valid / remembered-stale /
   newest-solo / none), `partitionBotSessions`, `isMainSessionDetail`,
   `isBotAttributed` (sidecar + user exclusions), `botUnreadCount`,
   `summarizeUnread` (fresh-baseline / detail-count / list-stale / cleared).
3. Focused suites (webui/chat/dashboard/memories/trace) then full
   `uv run pytest -q` if feasible.
4. Review: `code-reviewer` on `meta/muse-wonder-1.0-contributor` if spawnable
   from here; else record independent review as outstanding.
5. Forbidden: backend Python, `thyca/llm/pricing.py`, `tests/test_llm_pricing.py`.

## Status log

- 2026-10-13: plan written. Implementation done (see below). Independent
  review outstanding (no sub-agent tool in this env).
- Gap reported honestly: no DM marker in any backend session payload —
  CFE-1 uses client-side create-or-reuse of the solo session
  (`POST /api/sessions {bots:[bot]}`, verified) + localStorage remember.
  If BE later emits a DM marker, replace `findMainSession` resolution only.
