# Team context/token — backend lane execution plan (CBE-1..CBE-5)

Status: implemented + tested (independent review outstanding — no spawn
tool in this environment; orchestrator to run `code-reviewer` on
`meta/muse-wonder-1.0-contributor`). Lane lead: BACKEND.
Evidence: new suites `tests/test_bots_context.py` (8), `test_bots_bus_refs.py`
(6), `test_bots_tasks.py` (7), `test_bots_dm.py` (5) all pass; existing bots
suites pass (104 total in the bots run); full `uv run pytest -q` =
1298 passed / 1 failed, the failure being the known pre-existing
`test_memory_fallback_safety` date fragility in an untouched file.
Implementation notes vs the sketch above: compaction detection also uses
the new-marker signal (not only the pre-turn plan); Thyca's own turns get
the same task index via `TurnScope.hot_tasks`; the idle sweep reports bot
sessions under a new `bots` key alongside `rooms`.
Master: `.agents/plans/team-context.md` (user-approved 2026-10-13, 5 layers).
Prior lane: `.agents/plans/team-v1-backend-impl.md` (done). Rules: `.agents/AGENT_RULES.md`.
Scope: `thyca/**` except `thyca/webui/**`, plus `tests/`. Never touch
`thyca/llm/pricing.py` / `tests/test_llm_pricing.py`. No other git-mutating
commands after the authorized `git merge --ff-only integration/team-v1`.

## Verified current state (before coding)

- Bot turns: `BotRuntime.run_bot_turn_detailed` builds the shared
  `build_agent_loop(..., context_tokens=turn_cfg.effective_limits().contextTokens)`
  — per-instance `AgentLoop` with its own `SessionManager` over the bot's
  private (bot × room) store. `AgentLoop.run` compacts pre-turn
  (`compact_with_llm`, fallback rule-based) reserving `hot_tokens`
  (`PromptManager.build(hot)` chars/4) + `tools_tokens` (schema JSON) +
  pending user tokens, calibrates per model, and mid-turn shrinks at
  `TRIGGER_RATIO=0.8` (`thyca/agent/shrink.py`) with backstop
  `BACKSTOP_RATIO=0.95` (`thyca/core/context.py`). BotCard has **no**
  context-token field — "card/config" budget is the effective config
  (card only overrides model id when != "default").
- Prompt overhead: `BotRegistry.team_index_text()` injects
  `name — role — description` (long descriptions land in every bot prompt);
  `skill_index_text()` already caps descriptions at 256 chars, name+short
  only, no skill body. `ActiveSnapshot.team` is the team section;
  `PromptManager.build` renders it, so it is already inside `_hot_tokens`.
- Bus: `Envelope` has no `refs`; payload is unbounded at send; mailbox
  prepend caps each payload at 2000 chars ad-hoc; room rows cap at 2000.
  `MAX_HOPS=16` enforced per trace at `Bus.send`.
- Memory jobs: `thyca/memory/jobs.py` has `extract_from_transcript` /
  `apply_extracted` / `transcript_tail` / `should_idle_sweep` /
  `IDLE_SWEEP_SECONDS`. `ChatApp.sweep_idle_sessions` extracts **then**
  compacts rooms — but bot *internal* sessions are not swept, and the bot
  pre-turn compact path (`BotRuntime`) does no flush. Merge pass is wired
  in the room sweep only.
- Sessions wire: `session_summary`/`session_detail` carry `bots[]`
  (default `["thyca"]`); session meta lines persist `bots` via
  `SessionStore.append_bots/read_bots` + rewrite preservation.
- Scheduler: `thyca/bots/scheduler.py::tick` fires due bots via
  `_solo_room` (find-or-create room with `participants == [bot_id]`) then
  `app.turn(...)` — replies already land in that room's transcript, but
  there is no *marked, deterministic* DM lookup, and no payload marker.

## Lane decisions (technical defaults, recorded here)

1. **Bus excerpt cap = 1000 chars** (master plan technical default):
   `EXCERPT_CHARS = 1000` in `thyca/bots/bus.py`, noted in code. Envelope
   keeps `payload` as the prompt-facing excerpt; the full body goes to
   the artifact store. Envelopes with `refs` and short bodies are untouched.
2. **Artifact store**: new `thyca/bots/artifacts.py` —
   `BusArtifactStore`, in-process, keyed by `span_id`, bounded like the
   gateway retention: at most 500 artifacts and 4 MiB total chars,
   oldest-first eviction. Not a new durable store (bus is in-process v1).
3. **Fetch tool**: extend the existing peer tool family with **`bot_fetch`**
   (chosen over extending `bot_send`: fetch is a read, send is a write;
   separate name keeps the fail-closed allowlist explicit). `bot_fetch`
   takes `span_id`, returns the full artifact body; sender identity rules
   do not apply (any in-flight bot may fetch an artifact addressed into
   its room/trace — artifacts are keyed by unguessable `sp_` ids).
   `bot_fetch` joins `bot_send` as the second `bot_*` name a child may
   hold, and joins `BASELINE_BOT_TOOLS`.
4. **Task view**: new `thyca/bots/tasks.py`, pure functions over
   `list[Message]` + pending envelopes — no new mutable store.
   `task_open` is a new lightweight tool (registered with the admin/send
   family, baseline for children): its *tool call* in the bot's internal
   transcript is the event; the ack result does not close it. A
   delegate/bot_send opens a task keyed by tool-call id; a delivered
   reply or an error tool result closes it, a `queued` result leaves it
   open waiting on the recipient. Meta rows (`kind=delegate|task_open|
   result|error`, span/parent_span) open/close by span linkage.
   Pending incoming envelopes are open tasks for the recipient; pending
   outgoing envelopes set `waiting_on`.
5. **Flush-before-compact**: `BotRuntime` gains two optional hooks
   installed by `ChatApp` (unit tests inject fakes):
   `memory_flush(bot_id, messages) -> None` (extract+apply via existing
   jobs machinery, Thyca-privileged facade) and `memory_searcher`.
   Before `loop.run`, the runtime asks the session's own compactor
   `plan(...)` (same BACKSTOP semantics, hot/tools overhead counted)
   whether this turn will compact; if a plan exists it runs the flush
   hook **first**, then lets the loop compact as today. The idle sweep
   (`ChatApp.sweep_idle_sessions`) also sweeps bot internal sessions
   with the same extract→compact order. No new thresholds: reuse
   `BACKSTOP_RATIO` (via compactor plan), `TRIGGER_RATIO` untouched,
   `IDLE_SWEEP_SECONDS` for idle.
6. **Post-compact re-inject**: when a flush+compact (or a loop compact
   detected by a new `[compaction:` marker) happened for (bot, room),
   the runtime marks it in-memory; on the *next* turn for that pair it
   searches memory as the bot itself (facade `search`, reader = bot via
   `bind_bot_id`), query = current turn text, formats bounded hits
   (≤5 lines, each ≤200 chars) into a new `ActiveSnapshot.recalled`
   section — rendered by `PromptManager`, therefore counted in hot tokens.
   Open-task index similarly rides a new `ActiveSnapshot.tasks` section.
   Both default `""` → zero change for non-bot prompts.
7. **Bot DM (CBE-5)**: a DM is a room session with `participants == [bot]`
   plus a meta marker. Session meta gains `dm: <bot_id>` (store payload +
   reader, preserved across rewrite like `bots`). Wire payloads
   (`session_summary`/`session_detail`) gain `dm: <bot_id>|null`.
   Deterministic lookup: `ChatApp.bot_dm(bot_id)` find-or-create
   (validates card, lazily creates, marks meta), exposed as
   `GET /api/bots/<bot_id>/dm` → `{id, dm, bots}` (§6 style: 404 unknown
   bot via `RegistryError`). Scheduler `_solo_room` reuses `bot_dm`, so
   scheduled/independent results are appended by the app (existing
   `_run_bot_turn` room rows) into the DM; room work still reports in its
   room because delivery/turn paths are room-keyed as today.

## Tasks

### CBE-1 Verify per-instance budget (tests first)
- `tests/test_bots_context.py`: build bot loop path via `BotRuntime` with
  stub LLM capturing assembled prompt; fill internal session past the
  effective `contextTokens`; assert compaction marker lands in the bot's
  internal session, the sent prompt fits under the backstop, and a second
  bot's session is untouched (own budget, own transcript).
- Assert hot+tools overhead counted: make hot snapshot large (team/tasks
  sections) and assert compaction fires earlier than transcript-only math
  (compactor plan with overhead non-None vs None at boundary), plus
  `AgentLoop._hot_tokens/_tools_tokens` non-zero for bot-shaped hot/tools.

### CBE-2 Slim prompt overhead
- `BotRegistry.team_index_text()` → `name — role` only (update docstring;
  `public_list` keeps description for the API — prompts only slim).
  Existing tests asserting the old line format are updated to the new
  approved format (plan layer 2), never weakened silently.
- Runtime injects `hot.tasks` = compact task index
  (`task · status · room`, + `waiting: <bot>` when waiting) from CBE-4's
  view; counted via `PromptManager.build`.
- Test: skill index contains name+≤256-char description and never body text.

### CBE-3 A2A by reference
- `Envelope.refs: tuple[str, ...]` + `to_dict` gains `refs`;
  `Bus.send(..., refs=(), artifact_store=None)`: payload >1000 chars →
  store full body under the envelope `span_id`, envelope payload becomes
  the 1000-char excerpt. `Bus` owns a default `BusArtifactStore`.
- `_prepend_mailbox` and `_record_bus_events` use the excerpt (already
  the envelope payload) — remove the ad-hoc 2000-char re-caps' role for
  bus text (excerpt is canonical); room rows keep excerpt + `refs`.
- `bot_fetch` tool in `admin_tools.py` (deps gain bus artifact access via
  runtime); runtime `SEND_TOOL_NAME` family: `bot_fetch` also exempt from
  the admin-prefix drop, still allowlist-bound.
- Tests: large payload → envelope excerpt ≤1000, `bot_fetch` returns the
  full body, refs round-trip, hop limit still trips with large payloads,
  artifact store evicts oldest beyond bounds.

### CBE-4 Flush-before-compact + re-inject + task view
- `thyca/bots/tasks.py`: `OpenTask`, `derive_open_tasks(messages,
  pending=(), bot_id=None)`, `format_task_index(tasks, room names?)`.
- `task_open` tool (title/task arg; returns open status + span).
- Runtime: pre-turn plan check → flush hook → loop; compaction detection
  → pending re-inject set; next-turn `hot.recalled` bounded hits;
  `hot.tasks` every turn from internal messages + bus pending.
- `ChatApp`: install runtime hooks (facade via `job_facade()`, extraction
  via `jobs.extract_from_transcript/apply_extracted` with the turn's
  connect/model); extend `sweep_idle_sessions` to bot internal sessions
  (extract→compact, per-bot store via runtime).
- Tests: pure view (delegate open→result closed; task_open survives ack;
  waiting-on from pending outgoing; latest status wins); flush order
  (fake flush records call before compaction marker exists; idle sweep
  same); re-inject (after compact, next turn prompt contains the hit,
  bounded, and only once).

### CBE-5 Bot main window (DM)
- Store meta `dm` (payload/reader/rewrite-preserve), `Session.dm_bot`.
- Wire: `dm` in summary+detail. `ChatApp.bot_dm(bot_id)` lazy find-or-create.
- Route `GET /api/bots/<bot_id>/dm`; scheduler `_solo_room` → `bot_dm`
  semantics (find-or-create the marked DM; legacy unmarked solo rooms are
  adopted by marking on first lookup).
- Scheduler/independent results: `_run_bot_turn` already appends bot rows
  to the room the turn ran in; scheduled turns run in the DM (above), so
  their rows land there attributed — test proves it, and that a room turn
  does not leak into the DM.
- Tests: DM created once (same id twice), marker in payload, unknown bot
  404, scheduler fires into DM, room work stays in room.

## Post-review fixes (context review, backend)

All findings fixed; regression coverage in
`tests/test_bots_context_fixes.py` plus updated masking/budget tests:

1. **bot_fetch authorization**: `BusArtifactStore` records sender +
   recipient at `Bus.send`; `bot_fetch` resolves the caller from the
   turn ContextVar and returns the body only to sender/recipient. A
   third bot gets `ok: false` denied with no content; unknown spans
   stay an honest not-found. (Supersedes lane decision 3's "any bot may
   fetch" — unguessable ids alone were not authorization.)
2. **Baseline tools on load**: `filtered_tool_schemas` always keeps the
   current `BASELINE_BOT_TOOLS` for non-system bots, so pre-existing
   cards gain `bot_fetch`/`task_open` without being rewritten;
   `hidden_tools` and the memory/bash policies still win. The masking
   test now uses a legacy card that never listed the new tools. Two
   budget fixtures moved 2000 -> 3000 context tokens because baseline
   schemas are real, counted overhead (assertions unchanged).
3. **task_open lifecycle**: `task_open` returns a linked key (fresh
   span id as `key`/`span_id`/`task_key`); the view keys the task by
   that span, its ack never closes it, and a later result/error event
   with `parent_span_id` == key closes it. Dead `task_open_calls` code
   now guards ack exclusion for real.
4. **bot_dm race**: `ChatApp.bot_dm` holds `_dm_lock` across
   scan/adopt/create; 8 concurrent lookups mint exactly one DM.
5. **Bot idle sweep**: skips internal sessions whose room (via the
   room map built before compacting) is in `_turns.snapshot()`.
6. **Flush prediction**: the runtime pre-check mirrors the loop —
   mailbox-prepended wire-token estimate (rendered from a pending
   snapshot, same `_mailbox_text`) and `body_chars=SUMMARY_MAX_CHARS`.
   The post-compact recall one-shot is discarded only after a
   successful `loop.run`; a failed turn keeps it armed.
7. **Refs bounds**: `Bus.send` caps refs at `MAX_REFS=8` /
   `MAX_REF_CHARS=512`; mailbox rendering clips the same way (aligned
   with the room row's 8-cap).
8. **DM marker integrity** (chosen semantics: *protect DM
   participants*): `set_room_bots` cannot remove a DM's own bot or add
   another bot to a DM (no marker-clearing/convert operation exists in
   v1, so both raise `RegistryError`). Blank DMs survive
   `create()`'s prune: `discard_empty` keeps `dm_bot` sessions like
   running turns.
9. **Task status = latest event**: `derive_open_tasks` lets linked
   `status`/`step` meta rows (and tool result events) replace an open
   task's status; latest wins.

**Open policy question — resolved, behavior preserved**: the
pre-compact flush writes memory even for `memory.write=false` cards.
Decision: keep it. The flush is *system-driven* extraction — Thyca
(the system) extracting a transcript through its privileged facade
before system compaction compacts it — not the bot exercising a
memory tool. `memory.write=false` still gates the bot's own
`memory_*` tool schemas and gateway policy exactly as before. This is
recorded here and in comments at both flush sites
(`BotRuntime.run_bot_turn_detailed`, `ChatApp._flush_bot_memory`) so
the behavior is a documented decision, never a silent default.

## Checks
- Focused: `uv run pytest -q tests/test_bots_context.py tests/test_bots_bus_refs.py tests/test_bots_tasks.py tests/test_bots_dm.py` + existing bots/memory suites.
- Full: `uv run pytest -q` (known pre-existing failure:
  `test_memory_fallback_safety` date fragility — report separately).
- Independent review: spawn `code-reviewer` on
  `meta/muse-wonder-1.0-contributor` if a spawn tool exists; otherwise
  state review outstanding for the orchestrator.
