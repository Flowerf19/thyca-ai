# Team v1 — backend lane implementation plan (BE-1..BE-6)

Status: in-progress. Lane lead: BACKEND. Master plan: `docs/team-plan`
(`.agents/plans/team-v1.md`, frozen incl. contract §6). Lane design doc:
`docs/team-backend` (`.agents/plans/team-backend.md`).
No sub-agent tool is available in this environment — all work is done
sequentially by the lane lead; independent review is requested from the
orchestrator (see §8).

## Dependency order (master plan §7)

1. **BE-1** Registry + Agent Card (`thyca/bots/`) — no deps.
2. **BE-6-meta** `Message.meta` carries `bot_id/kind/trace_id/parent_span_id`
   (`thyca/agent/meta.py`, `observe.py`) + trace grouping (`thyca/serve/trace.py`).
   Needed before BE-2/BE-3 can record attributable turns. (Rest of BE-6 is
   verification at the end.)
3. **BE-2** Bot runtime (`thyca/bots/runtime.py`, `bus.py`) — deps BE-1, BE-6-meta.
   Built on existing `build_agent_loop`; tools allowlist minus `hidden_tools`
   enforced at filter time AND at `ToolGateway` Policy.
4. **BE-3** Rooms (`participants` on sessions, `bots:[]` in payloads, routes).
   Deps BE-1 (registry validates bot ids), BE-2 (routing to runtime).
5. **BE-4** Memory `bots` end-to-end (heading→chunk→SQLite migration→facade→tools,
   ContextVar identity, candidate queue + promote, Thyca memory job hooks,
   merge funnel + Union-Find + `related`). Deps BE-1 (bot identity), BE-2 (job triggers).
6. **BE-5** Thyca admin tools + create-bot chat flow + scheduler. Deps BE-1..BE-4.

## Tasks, files, tests

### BE-1 Registry + Agent Card
- NEW `thyca/bots/__init__.py` (exports), `thyca/bots/card.py`
  (`BotCard` dataclass; `parse_bot_card(text, bot_id)` — YAML frontmatter +
  body; name↔folder match, `^[a-z0-9]+(-[a-z0-9]+)*$`, ≤64 chars, like skills
  store; fields per master §2 incl. `tint` token check, `version: 1`,
  `model{id, thinking}`, `skills[]`, `tools[]`, `hidden_tools[]`,
  `bash: ask|allow|deny`, `memory{read,write,propose_team}`, `schedule`).
- NEW `thyca/bots/registry.py` (`BotRegistry`: `list/get/create/update/delete/pause/restore`,
  `team_index_text()` — name+role+description index injected into prompts;
  per-bot `skills/` + shared `skills-team/` resolution with private-wins).
- Seed Thyca system card (`thyca/seeds/bots/thyca/BOT.md`, `system: true`,
  always-on, undeletable) copied to `~/.thyca/bots/thyca/` on ensure.
- Test: `tests/test_bots_registry.py` (parse valid/invalid ×N, name rules,
  hidden>tools precedence note, team index, seed).

### BE-6-meta Trace/cost attribution
- `thyca/agent/meta.py`: `assistant_meta(..., bot_id?, trace_id?, parent_span_id?)`;
  `tool_message(..., bot_id?)`. `kind` already exists; bot turns use
  `kind: "llm"` + `bot_id`.
- `thyca/agent/observe.py`: `Observe` accepts optional `bot_ctx`
  (`bot_id/trace_id/parent_span_id`) and stamps every appended message.
- `thyca/serve/trace.py`: `TurnSummary.bot_id` (first non-null `bot_id` in
  slice) → `to_payload()` gains `bot_id/trace_id`; `aggregate()` gains
  `by_bot` (same shape as `by_model`, grouped by `bot_id`, `unknown` fallback).
- Test: extend `tests/test_bots_trace.py` (meta carries ids; trace groups by bot).

### BE-2 Bot runtime + A2A bus
- NEW `thyca/bots/bus.py`: `Envelope{from,to,kind,session_id,trace_id,parent_span_id,ts,payload}`,
  kinds `delegate|message|step|status|result|help_request`; per-bot
  `queue.Queue` mailboxes; per-`trace_id` hop counter with limit
  (`MAX_HOPS = 16`, technical default recorded here); `BusLog` event list the
  app layer records into the room transcript.
- NEW `thyca/bots/runtime.py`: `BotRuntime` — `build_loop_for(bot_id, room_session_id)`
  via existing `build_agent_loop` (model from card, filtered tools, per-bot
  snapshot); internal session id `bot × room` (`BotSessionStore` under
  `~/.thyca/bots/<id>/sessions/` reusing `SessionStore` grammar? — decision:
  reuse `SessionStore` with `sessions_dir=bots/<id>/sessions`, same id grammar);
  `ToolGateway` `Policy` subclass denying `hidden_tools` (wins over allowlist,
  applies to MCP-loaded tools too since check is by name at submit).
- Test: `tests/test_bots_runtime.py` (hidden tool denied at gateway even when
  in tools list; envelope round-trip; hop limit trips; internal session isolation).

### BE-3 Rooms (participants, routing, live 1-1)
- `thyca/sessions/store.py`: meta-line support for `bots` (extend `_meta_payload`
  with `bots` list + reader; title logic untouched).
- `thyca/sessions/wire.py`: `session_summary` + `session_detail` gain
  `bots: string[]` (default `["thyca"]` when no meta line).
- `thyca/app/chat_app.py`: `create(bots?)`, `list_payload` passes through,
  `set_room_bots(session_id, bot_id, action)` (validates via registry,
  add/remove); `run_turn` routes: room 1-1 → that bot answers; room N —
  `@<bot-id>` mention selects, else Thyca (first participant) answers;
  bot turns run through `BotRuntime` and room transcript rows are stamped
  with `bot_id/kind/trace_id/parent_span_id` by the app layer (bot never
  writes the room transcript directly); bus events appended as transcript
  rows under the bot's name.
- `thyca/serve/sessions_api.py` + `routes.py`: `POST /api/sessions` accepts
  `bots?`; NEW `POST /api/sessions/<id>/bots`; NEW `GET /api/bots` (public
  card list: id/name/role/description/tint/status).
- Test: `tests/test_bots_rooms.py` (create with bots, add/remove incl. unknown
  bot 404, payloads carry bots, @mention routing with stubbed runtime).

### BE-4 Memory bots + candidates + jobs + merge
- `thyca/memory/heading.py`: `HeadingMeta.bots: list[str] | None`
  (None = legacy → `"team"`); parse/render `bots` in JSON comment.
- `thyca/memory/chunk.py`: `Chunk.bots`; `_sessions` carries it.
- `thyca/memory/schema.sql` + `schema.py`: migration v6→v7 — `ALTER TABLE
  chunks ADD COLUMN bots TEXT` (JSON array; NULL = legacy team) + index;
  `migrate()` handles v6→v7 non-destructively.
- `thyca/memory/archive_store.py`: persist/filter `bots` in
  `replace_source/fts_search/trigram_search/get_chunk/get_session/recent_rows`;
  NEW `related` edges table ops (`add_related(a,b,reason)`, `related_of(id)`,
  `drop_edges_of(session_id)`) — edges drop when entry forgotten/expired
  (enforced in `purge` path + `drop_source`).
- `thyca/memory/search.py` + `facade.py`: `bots` filter param; `MemoryFacade`
  enforces identity from ContextVar (`bind_bot_id` in `memory_tools.py`):
  read allowed when bot ∈ `bots` or `bots` contains `"team"` (None counts as
  team); write defaults `bots=[self]`; non-Thyca bot cannot write/edit/delete
  `"team"` entries (typed `ArchiveError`); `update/forget/reinforce` check
  ownership (owner bot, Thyca, or user/None context).
- `thyca/memory/candidates.py` NEW: file-backed candidate queue
  (`~/.thyca/memory_candidates.json`, atomic write): `propose(bot_id, session_id)`,
  `list_pending()`, `approve(id, decided_by)` (flips entry `bots`→`["team"]`,
  id stable via `map_heading`), `reject(id, decided_by)`.
- `thyca/tools/memory_tools.py`: `memory_propose_team` tool (bot proposes;
  creates candidate, never writes team directly); facade `remember` binds bot.
- `thyca/serve/memory.py` + `routes.py`: `GET /api/memory/candidates`,
  `POST /api/memory/candidates/<id>/approve|reject` (user path; Thyca
  self-approval records `decided_by: "thyca"`).
- `thyca/memory/jobs.py` NEW: `extract_memories(transcript_tail, ...)` hook
  surface + `run_after_task(...)` (called by app after task-done) and
  `idle_sweep(...)` (backend idle-15min trigger entry point; the timer/trigger
  lives in `ChatApp`/serve layer calling this); extract-before-compact ordering
  documented + enforced by call order in `compact_session`.
- `thyca/memory/merge.py` NEW: funnel `text_norm` hash exact-dup → lexical
  candidates via `memory_search` → LLM review stub
  (`review_pair(a, b) -> trùng|liên quan|không`, injectable reviewer so no
  heuristic numbers ship as decided) → `related` edges + Union-Find clusters
  (`clusters()`); merge = link clusters (no entry deletion), `bots` union
  reported per cluster.
- Tests: `tests/test_bots_memory.py` (bots round-trip heading→sqlite→facade;
  ownership denials; candidate propose→approve flips to team, id stable;
  related edges drop on forget), `tests/test_bots_merge.py` (hash dup,
  union-find clustering, stub reviewer), `tests/test_bots_jobs.py` (hooks run,
  extract-before-compact order).

### BE-5 Admin tools + create-bot flow + scheduler
- NEW `thyca/bots/admin_tools.py`: `register_admin_tools(registry, bot_manager)`
  — `bot_create/bot_pause/bot_delete/bot_restore/bot_edit_card/bot_delegate/bot_stop`;
  registered ONLY on Thyca's registry (runtime filters them out for child bots).
- `thyca/bots/scheduler.py` NEW: real scheduler — `schedule(bot_id, cron)` stored
  on card; `due(bot_id, now)` cron matcher (stdlib, minute/hour/dom/month/dow;
  `*` + `*/n` + lists + ranges); `tick(app, now)` entry invoked by serve loop
  calls the bot via runtime when due.
- Create-bot chat flow: `thyca/bots/create_flow.py` NEW — questionnaire state
  machine (role/main-job/data-touched/bash-permission) → card defaults from
  Settings → `registry.create`. Covered by unit test on the state machine
  (no LLM in test).
- Test: `tests/test_bots_admin.py` (admin tools absent for child, present for
  Thyca; pause/delete/restore; scheduler due/next-fire; create-flow builds card).

### BE-6 (final verification)
- `aggregate()` `by_bot` + meta consistency test; FE contract checklist
  (§6: every endpoint + payload field asserted in `tests/test_bots_contract.py`).

## Non-goals / frozen constraints
- Do NOT touch `thyca/llm/pricing.py`, `tests/test_llm_pricing.py`,
  `thyca/webui/**`.
- Serve stays loopback-only; no secrets/paths/stacks in new API payloads.
- `core/` stays stdlib-only → `thyca/bots/` lives outside `core/`.
- `serve/` never imports runtime `app/` (TYPE_CHECKING + lazy only).
- No invented business rules: hop limit 16, idle 15min, TTL reuse are
  technical/user-accepted values recorded here, not heuristics presented as decided.

## §8 review/test
- Independent review: no sub-agent tool in this env — orchestrator must run
  `code-reviewer` (`meta/muse-wonder-1.0-contributor xhigh`) on the diff.
- Tests: per-area files above + full `uv run pytest -q`; never weaken tests.
