# Team context — frontend lane execution plan (CFE-1..CFE-3)

Branch state: detached HEAD `aa8fabd` (ff-merged `integration/team-v1` as authorized).
Master: `.agents/plans/team-context.md` (Layer 5 = this lane's BE+FE scope).
Prior FE plan: `.agents/plans/team-v1-frontend-impl.md` (all FE-1..FE-6 landed).
Skill: `thoughtful-coder`. No git mutating commands after the merge.

## Backend contract as implemented on this branch (read, not invented)

- `thyca/sessions/wire.py::session_summary` / `session_detail` carry
  `bots: string[]`, default `["thyca"]` when no bots meta line, plus `dm`
  (the bot id whose fixed main window the session is, else null; CBE-5).
  Detail has NO `updated_at` (summary does); messages carry
  `{role, content, ts, meta?}`.
- `GET /api/bots/<botId>/dm` → `ChatApp.bot_dm(bot_id)` find-or-create
  (adopts a legacy unmarked solo room instead of making a second window;
  unknown/paused → 404) returns the session detail with `dm` = bot id and
  `bots` = `[bot_id]` (`thyca/serve/sessions_api.py::bot_dm`, CBE-5).
- `POST /api/sessions {bots:[...]}` → `ChatApp.create(bots)` returns the full
  detail including `bots` (`thyca/app/chat_app.py:399-409`); `_clean_participants`
  validates ids via the bot registry (unknown → 404, paused/empty → 400).
- Solo-room precedent is backend-side: `ChatApp.bot_dm` finds
  `dm_bot == bot_id` first, else adopts the unmarked `bots == [bot_id]`
  room, else `app.create(bots=[bot_id])` + marks it.
- ~~DM marker gap~~ RESOLVED by CBE-5 (merged `integration/team-v1`):
  session payloads carry `dm` and `GET /api/bots/<botId>/dm` resolves the
  fixed window. → CFE-1 prefers the endpoint (verified with the new
  `isDmDetail` guard on `dm` + `bots`) and `findMainSession` prefers
  `dm === botId` hits. Client-side solo resolution + localStorage remember
  (`thyca.team.dm.v1`) + `POST /api/sessions {bots:[bot]}` stay ONLY as
  the legacy fallback when the endpoint/marker is unavailable.
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
- `isSoloSession(session, botId)` — `bots` is exactly `[botId]` (legacy
  fallback helper).
- `isDmDetail(detail, botId)` — DM endpoint guard: `detail.dm === botId`
  AND `detail.bots` exactly `[botId]`.
- `findMainSession(sessions, botId, rememberedId)` — `dm === botId` hit
  first (remembered id wins among marked); else legacy: remembered solo,
  else newest solo by `updated_at` desc, else null.
- `partitionBotSessions(sessions, botId, mainId)` — `{main, others}`; `others`
  keeps list order (recency) and excludes `main`.
- `isMainSessionDetail(detail, botId)` — created-session guard: `detail.bots`
  equals `[botId]` (mirrors `verifySharedSession` honesty for the shared flow).

DOM in `team-sidebar.js`:
- Level-1 click → `openBot(botId)` becomes async and mints a resolve token:
  set level 2 + render immediately, then `resolveMain(botId, token)`:
  primary `GET /api/bots/<botId>/dm` → `isDmDetail` check → remember +
  `hub.afterTeamSession(detail)`; on endpoint failure the legacy fallback
  (remembered/newest solo → `hub.loadSession(id)`, else `POST`
  `isMainSessionDetail`-verified create → `hub.afterTeamSession`). Every
  `await` re-validates token ownership (`owned()`); only the owning resolve
  may navigate or clear `mainResolving` — rapid bot switching can no longer
  land the user in the first bot's window. Failure → honest status line in
  level 2, other sessions still listed; never opens a fake main.
- Level 2: main window pinned first with a `Cửa sổ chính` tag; remaining bot
  sessions under a `Phiên khác` secondary heading (shared ones keep `· chung`).
  `DM_KEY` remember updated whenever a solo session is opened/confirmed
  (level-2 solo row taps re-remember; render prunes remembered ids missing
  from the list via the now-wired `forgetDmSession`). Level-2 session
  buttons mirror the level-1 aria unread cue.
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
  mass-marks); with `messages` → attributed count; list-only → stale ONLY
  when `summary.updated_at > seen.ts` (dot, `count:0`). Count drift with no
  newer stamp (sidecars/compaction the transcript hides) never dots —
  conservative, documented in code. Rendering is dot-only by design: the
  sidebar never fetches per-session messages, so exact counts exist only in
  the pure helpers for callers that already hold messages. List-level
  staleness means "something new arrived after the baseline" (background
  writers are bots: scheduler DM writes, other-tab turns); opening a
  session attributes and clears it.

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
- Badges: level-2 session rows and level-1 bot rows get a dot-only
  `.team-unread` (the count branch of the badge stays dormant — no counts
  ever reach it list-side; exact counts live only in the pure helpers).
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
   newest-solo / none, PLUS dm-marker preference / legacy-no-dm payloads),
   `partitionBotSessions`, `isMainSessionDetail`, `isDmDetail`,
   `isBotAttributed` (sidecar + user exclusions), `botUnreadCount`,
   `summarizeUnread` (fresh-baseline / detail-count / list-stale / cleared /
   count-drift-ignored), team-unread storage (dm remember/forget round-trip,
   readMap array/corrupt guard). Race-token logic is async/DOM (not
   node-testable as pure functions) → covered by `node --check` + review.
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
- 2026-10-13 (frontend context fix lead): CBE-5 landed on the merged
  branch — the gap above is RESOLVED. FE now prefers `dm` + the DM
  endpoint (isDmDetail guard, dm-first findMainSession, node fixtures with
  dm fields); rapid-switch race closed with a resolve token re-validated
  after every await; unread corrected to honest dot-only with conservative
  stamp-only staleness + readMap array guard + level-2 aria cue;
  `forgetDmSession` wired (stale-DM prune) and solo row taps re-remember.
  Independent review still outstanding (no sub-agent tool in this env).
