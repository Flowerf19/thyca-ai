---
status: in-progress
created: 2026-10-31
last_updated: 2026-11-06
---

# Conversation-store wiring plan (prototype -> runtime)

## Goal

Replace the live JSONL `SessionManager`/`SessionStore` path with the SQLite
`AgentStore`/`AdminStore` conversation store, without losing chat, trace,
bot, memory or serve behavior. Delete the old session logic only after the
new path proves itself on fixture roots and the approved live checks.

This is a plan only. No runtime wiring is implemented by writing this file.

## Non-goals

- No change to provider/model behavior, memory semantics, or bot policies.
- No migration of real `~/.thyca` data until explicitly approved.
- Live server stays stopped until a wiring slice is approved to restart it.

## Accepted decisions (sources)

- Prototype is the goal store: descriptor-relative root, `LOCK_EX` writers /
  `LOCK_SH` readers, `BEGIN IMMEDIATE`, WAL, append-only messages, membership
  checks (`.agents/plans/two-doors-hard-cut.md`, checkpoint `a76a3e2`).
- `ChatApp` is the integration point; `SessionManager` is used in
  `thyca/app/chat_app.py`, `SessionAdminService`, `TurnState`,
  `trace_api`/`trace.py`, `workspace_turns`, `toolchain`, serve routes.
- Actor must come from a trusted source, not prompt/facade
  (`docs/TECH_DEBT.md` item 4). Lifecycle/reset authority is still open.
- No automatic recovery: commit-unknown resolves only by reading
  marker/DB pair; no retry of mutations.

## Prerequisites to any runtime activation (must land before Phase 1 writes)

- P0-1 Trusted init/bootstrap + registry coordination: decide the narrow
  explicit caller chain for `initialize`/`bootstrap_initial_dm` (not
  `current_bot_id` alone), wire BotRegistry transaction into the lock
  hierarchy `lifecycle -> model coordination -> config -> bot ->
  admission-directory -> store-root -> SQLite`, and verify startup audit runs
  before *any* writer — including `ActiveMemory.open_session` and bot seeding
  in `ChatApp.__init__` (`thyca/app/chat_app.py:112-131`), not just before
  the first conversation append.
- P0-2 Legacy-writer exclusion: block JSONL writers when the new store is
  active; independent store-code review must pass first.
- P0-3 Phase 1 reads are synthetic-callers-only unless trusted read
  authority is separately approved; do not claim production read parity.

## Settled constraints (not open, do not reopen)

- No migration/dual-write; no live `~/.thyca/sessions` changes in this plan.
- Logical ordering/generation: summary at effective logical position, suffix
  preserved, stale generation -> resync.

## Unresolved decisions with explicit gates

Blocking before Phase 1 implementation (reads):

- R1 Listing + admin/user reads + empty-conversation timestamps +
  full-history assembly across pages + `detail`/`summary`/`trace` payload
  fields + title/naming read semantics (naming fires once after two
  completed turns per `thyca/sessions/title.py:25`, `thyca/app/naming.py:30-47`;
  prototype rejects `content=None` per `contracts.py:144-152`). Phase 1
  consumes these; do not implement Phase 1 reads until specified.
- R2 Trusted read authority for any non-synthetic caller (Phase 1 stays
  synthetic-callers-only until approved).

Blocking before Phase 2 implementation (writes):

- W1 Conversation deletion, retry truncation (`truncate_to_last_user`
  rewrites history — incompatible with append-only), error-marker mutation
  (`mark_turn_error` rewrites — incompatible), atomic metadata updates.
  Do not weaken append-only or discard sidecar usage records silently.
- W2 Trusted actor + lifecycle/reset authority for writes (who may call
  `bootstrap_initial_dm`; external authority design pending) + startup audit
  before writers + legacy-writer exclusion + independent store review.

## Slice plan (dependencies, not task numbers)

### Phase 0 — Freeze and harness

- Freeze `two-doors-hard-cut.md` lock contract; record any deviation as a
  decision, not an implementation detail.
- Add a fixture-only adapter harness limited to already-specified store
  operations until missing semantics are decided: open prototype root in tmp,
  seed DM/group via `AdminStore`, exercise `AgentStore.read/append` and
  `AdminStore.create_group/set_metadata` through existing call shapes only.
  Listing, admin/user reads, full-history assembly, conversation deletion,
  retry truncation, and error-marker mutation stay out of the harness until
  R1 (reads) and W1 (writes) are specified.
- Checks: `pytest -q tests/test_conversation_store.py
  tests/test_conversation_ref.py tests/test_private_followups.py`;
  `git diff --check`. No live server, no real root.

### Phase 1 — Read-only adapter, synthetic callers only (no live writes)

- Affected: `SessionAdminService.get_payload/list`, `ChatApp.get_payload/
  list_payload/bot_dm`, `trace_api.cached_turns_for/collect_turns`,
  `sessions/wire.session_summary`, `is_blank`, title read paths. Also cover:
  `BotRuntime._window_for_room` (opens legacy JSONL),
  `MemoryView._origin_info` (opens legacy sessions for privacy),
  `BotPostingService` writers, CLI selection/error paths, WebUI channel
  adapters, `CanonicalSessionStore` subclass, `BotRuntime`/`AgentLoop`/
  `Observe`/compaction consumers of `SessionManager` — inventory only, no
  deletion here. Public-storage removal is separate from shared
  orchestration/canonical-timeline/wire-helper responsibilities; private
  tool/reasoning rows must never move into the public store.
- Implement a read adapter: `ConversationRef` <-> session id, `PageCursor` ->
  message windows, metadata -> title/bots/dm/kind/channel/window.
- Scope: synthetic fixture callers only. No production read parity claimed
  without trusted read authority (see P0-3).
- Tool-path protection prerequisite (before any activation): extend
  `PathGuard.deny_write/deny_read` to cover `conversations.db*`/sidecars/
  locks/markers for child/group raw-read restrictions (parity with
  `sessions/`), and `BotPolicy`/gateway to deny matching file/`bash`
  targets; add negative read/write gateway tests. Bash stays unsandboxed;
  do not claim facade separation is filesystem isolation.
- Checks: new adapter unit tests + SQLite-backed fixture init asserting
  legacy accesses are rejected + existing `test_serve_chat` read paths +
  `test_usage_trace_source_e2e` fixture case. Non-root evidence required
  (namespace UID 0 skips the permission test).
  Rollback: delete adapter, no live impact (fixture roots reseeded).

### Phase 2 — Write path on disposable fixture roots only (contract-gap gate)

- Gate first: specify listing, conversation deletion, retry truncation,
  error-marker mutation, admin/user reads, atomic metadata updates,
  empty-conversation timestamps, full-history assembly, and
  generation/cursor resync. Retry/error paths that rewrite history are
  incompatible with append-only; resolve without weakening guarantees.
- Affected: `SessionAdminService.create/set_room_bots/rename/delete`,
  `SessionStore.append/append_bots/append_dm/append_title_if_missing`,
  `SessionManager.create/rename/append/truncate_to_last_user/
  mark_turn_error/mark_naming_attempted`.
- Route writes for disposable fixture roots to `AdminStore`/`AgentStore`
  only; live root stays on old path. Block legacy writers when on.
- Turn/routing integration moves ahead of any full-turn gate (no bare
  append-only turn claim).
- Checks (backend-aware fixtures): SQLite-backed init; histories beyond the
  500-message page; generation changes during assembly; trace-cache
  invalidation; startup refusal before memory/bot writes;
  missing/pending/mismatched state; membership removal during dispatch;
  bot-ID reuse; metadata races; commit-unknown without mutation retry;
  canonical trace survival; CLI flow. Rebaseline TECH_DEBT instead of
  reusing historical failures.
  Rollback: fixture roots only, explicit reseed; never reopen legacy writers
  against a switched root by config flip.

### Phase 3 — Turn pipeline, errors, naming, trace (fixture roots only; switch-off inherits Phase 2 disposable-root+reseed rule, never reopen legacy writers)

- Affected: `ChatApp.turn` (loop turns, retry, cancel, busy), `bridge
  pump_stream/stream_turn`, `errors.public_turn_error`, `TurnState`,
  `trace.py` turn slices, `title.refresh_title/mark_naming_attempted`.
- Preserve: 400/404/409/503 mapping, single terminal NDJSON item, busy/cancel/
  follow semantics, failed-turn markers, Trace tool-row expansion.
- Checks: `tests/test_serve_chat.py` (errors, busy, cancel, retry, follow,
  model override), `test_turn_status`, `test_webui_concurrent_streams`
  fixture subset. Rollback: switch off.

### Phase 4 — Bots, workspace, A2A, memory, jobs (fixture roots only; switch-off inherits Phase 2 disposable-root+reseed rule; live activation boundary: scratch root only, never live `~/.thyca`)

- Affected: `workspace_turns`, `bot_operations/bot_tasks/exchanges`,
  `private_followups`, `a2a_delivery`, `background_jobs`, `toolchain`,
  `mcp` hot-reload paths that touch sessions.
- Prove DM/group/A2A routing, per-bot settings, pending exchanges, and memory
  write-through on fixture roots. No live bot/DM creation/deletion.
- Checks: `test_bot_workspace`, `test_workspace_e2e`,
  `test_private_followup_integration`, `test_bots_*`, memory lifecycle.
  Rollback: switch off.

### Phase 5 — Delete old public-storage logic only (does not close two-doors)

- Only after Phases 1-4 pass on fixture + approved live E2E batch passes on a
  scratch root, and after direct legacy readers/canonical consumers are
  converted or explicitly retained: `BotRuntime._window_for_room`,
  `MemoryView._origin_info`, `BotPostingService` writers, CLI/WebUI channel
  adapters, `CanonicalSessionStore` subclass, `BotRuntime`/`AgentLoop`/
  `Observe`/compaction consumers of `SessionManager`. Deletion permitted
  only when shared orchestration, canonical timelines, and private
  tool/reasoning history remain intact with passing canonical/private-history
  tests. Close only this storage subplan; leave two-doors completion
  (`two-doors-hard-cut.md:195-228`: explicit interfaces, alias removal,
  client conversion, memory/provenance, independent verification) to its own
  acceptance checks; do not mark two-doors `done` here.
- Checks: full `pytest -q` green in isolated namespace + live smoke batch
  (empty guard, baseline turn, cancel, retry, reload-continue) on an approved
  scratch root. Never delete live `~/.thyca/sessions` as part of this step.

## Interface scope (temporary characterization vs final contract)

This plan is a limited storage subplan and does not close two-doors.
Final contract (two-doors) requires removing session aliases, explicit
DM/group identities, and no migration; timestamp route grammar cannot address
bot IDs or `group-...` IDs today (`thyca/serve/routes.py:63-89`). Do not
promise `/api/sessions` preservation, ID mapping, migration, or dual-write
as final behavior.

## Interfaces exercised by characterization fixtures

- HTTP: `GET /api/sessions`, `POST /api/sessions`, `GET /api/sessions/<id>`,
  `POST .../turn`, `POST .../turn/stream`, `GET .../turn/stream` (follow),
  cancel, rename (PATCH), delete, compact, private-followup; `GET /api/bots`,
  `.../dm`, `.../exchanges...`, `.../rooms/.../operations|tasks`; `GET
  /api/traces`, `/stats`, `/<id>/<n>`.
- NDJSON: `turn.accepted`, `llm.*` deltas, `tool.started/finished`,
  exactly one of `turn.completed/turn.failed/turn.cancelled`.
- Payloads: `session_summary`, `detail` (messages without `tool_calls`;
  Trace carries tool rows), `TurnSummary`, cursor/generation resync.

## Test commands and evidence

- Focused: `.venv/bin/python -m pytest -q -p no:cacheprovider
  tests/test_conversation_store.py tests/test_conversation_ref.py
  tests/test_private_followups.py --basetemp=/tmp/pytest`.
- Serve/chat: `tests/test_serve_chat.py`, `test_turn_status`,
  `test_webui_concurrent_streams` (fixture subsets first).
- Fixture E2E: `tests/test_webui_setup_gate_source_e2e.py`,
  `tests/test_usage_trace_source_e2e.py` under
  `env -i ... unshare --user --map-root-user --mount --net` with ro repo bind,
  masked `~/.thyca`, loopback only (see `thyca-e2e-test-plan.md`).
- Record per-ID pass/fail/skip; never present integration as E2E; never log
  keys, prompts, replies, private memory, or raw payloads.

## Execution log

- 2026-11-01 Phase 1 read adapter (synthetic callers only, R1/R2):
  added `AdminStore.list` (paginated, newest-first, empty conversations
  included), `thyca/conversations/store/read_adapter.py` (`SyntheticReadIntent`
  gate, cursor-based `assemble_history` with bounded resync, `summary_for` /
  `detail_for` mirroring `session_summary`/`session_detail` shapes,
  `trace_turns_for`/`trace_detail_for` via `turns_from_session` +
  `message_dict(include_args=True)` so `tool_calls` appear only in Trace,
  empty-conversation `updated_at` from listing `created_at`, titles via
  `session_title`); all writes stay on the legacy JSONL path. Checks:
  104 passed (`test_conversation_store`, `test_conversation_ref`,
  `test_private_followups`, new `test_conversation_read_adapter` — 16 tests)
  + 16 passed (`test_serve_trace`, 2 `test_serve_chat` read-path cases);
  `git diff --check` clean. No live server, no real root, no commit.
- 2026-11-01 Phase 0 harness (existing ops only):
  `.venv/bin/python -m pytest -q -p no:cacheprovider
  tests/test_conversation_store.py tests/test_conversation_ref.py
  tests/test_private_followups.py --basetemp=/tmp/pytest-phase0` => 88 passed;
  `git diff --check` clean. No live server, no real root.

- 2026-11-06 Blocker fix pass (B1-B6, M1-M4): trusted ChatApp init
  (existing DB audited; missing DB with any store artifact fails closed;
  fresh root only via `initialize_root`), no JSONL fallback in trace
  runtime collect, acting-bot threading with server-mediated user/A2A
  display rows, kind from explicit refs only, corrupt-door 503 coverage,
  atomic membership in store transactions, sanitized public errors, dead
  `sessions_api.py` deleted. Rebaselines (equal strength): internal
  canonical timeline ids keep timestamp grammar (not public doors),
  group id cannot address DM endpoint (404), fixture title source uses
  store grammar (`user`), Usage E2E uses `/api/traces/groups/<id>/<n>`
  and `group:` keys. Gates: focused 248 passed, frontend 165 passed,
  full isolated 2033 passed / 5 skipped, `git diff --check` clean.

- 2026-11-06 Legacy public-session removal (Writers A+B+C): deleted the
  JSONL dual path in `thyca/sessions/store.py` (fail-closed when the runtime
  store is uninitialized), legacy `create`/`ensure_dm` branches and the
  `Path.home()` default in `manager.py`, the file-mtime fallback in
  `wire.py`, `fixture_switch.py`/`bot_workspace.py` and every
  `Synthetic*`/`fixture_*` production export, and the file-mtime trace
  cache in `serve/trace_api.py`. Canonical grammar ownership moved to
  `thyca/bots/workspace_store.py` (`_INTERNAL_SESSION_ID_RE`);
  `CanonicalSessionStore.path_for` validates it and
  `WorkspaceTimeline.project` no longer routes through the public
  `SessionStore.path_for`, which now accepts only `group-<hex32>`/bot-id.
  Dead `_LEGACY_SESSION_ID` removed from `thyca/core/conversation.py`.
  Remaining bare-manager tests ported to canonical-timeline or runtime
  harnesses at equal strength; deleted-with-subject:
  `test_list_paths_survives_racing_stat`,
  `test_compact_of_deleted_session_reports_not_found`,
  `test_rewrite_unserializable_meta_wrapped` (file subjects gone).
  Full isolated gate: 1999 passed, 5 skipped (pre-existing Chrome/env
  skips); `git diff --check` clean. Repo-wide search: no JSONL
  public-session writing path, no `fixture_switch`/`Synthetic`
  production exports, no public timestamp grammar. Live smoke on a
  scratch root was not part of this slice, so this plan stays
  `in-progress` (not marked done).

- 2026-11-06 Sessions merged into conversations: `thyca/sessions/*`
  moved to `thyca/conversations/runtime/` (class names unchanged) and
  `thyca/app/session_admin` renamed to `thyca/app/conversation_admin`;
  all imports updated, no shim. Full gate result in the merge handoff.

## Handoff

- Status `in-progress`: public-storage removal verified as logged above.
  Remaining before `done`: the approved live smoke batch on a scratch
  root (Phase 5 checks) and the separate two-doors acceptance checks.
- Related: `.agents/plans/two-doors-hard-cut.md`,
  `.agents/plans/thyca-e2e-test-plan.md`,
  `.agents/plans/module-e2e-test-cases.md`, `docs/TECH_DEBT.md` items 1-6.
