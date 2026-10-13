---
status: in-progress
created: 2026-10-13
last_updated: 2026-10-13
---

# Hybrid compaction (Claude-style): manual + auto + microcompaction

## Summary

User direction (2026-10-13 discussion, memory `2026-10-08#d8a7b71b`):
move Thyca compaction from auto-only truncation to the hybrid the field
converged on (Claude Code / Cursor / Codex CLI research, same day):

1. **Microcompaction** — bulky tool results shrink early, in RAM.
2. **Auto-compact** — safety net near the cap, no user action needed.
3. **Manual compact** — user triggers at a task boundary, optionally with
   keep/drop instructions, so what survives is shaped for the next task.
4. **Rehydrate** — after summarizing, restore recent working state
   (recent files, continuation cue) instead of summary-only.

Large context (1M) does not remove the need: the estimator is still
heuristic and a million noisy tokens still degrade quality. Field
consensus from the research: auto-only is fine for short sessions and
degrades on long tasks; explicit user control over what persists is the
practical advantage.

## Goal / approved scope / success

- Goal: a long session can continue indefinitely without provider 400s,
  without silent quality collapse, and with the user able to shape the
  carried context at task boundaries.
- Approved so far (implemented, uncommitted, see "Already landed"):
  rule-based manual compact + context meter + quiet suggestion chip.
- Phase 1 was approved (user, 2026-10-13, including the proposed
  defaults below) and is implemented; Phases 2–4 remain proposals.
- Out of scope here: multi-bot/orchestrator + shared/private memory
  (separate direction; this plan is its prerequisite — each bot will
  need its own SessionManager/context budget/shrink state).

## Already landed (MVP, 2026-10-13, uncommitted)

Rule-based manual path reusing the existing turn-safe tail — no LLM,
no new summarizer:

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | `sessions/compaction.py`: `context_usage()` (transcript + overhead, same estimator as guards) + `compact(force=)`; export via `sessions/__init__.py` | x | 2026-10-13 |
| TASK-002 | `sessions/manager.py`: `compact_manual()` over shared `_compact_locked()`; exact before/after counts; honest no-op | x | 2026-10-13 |
| TASK-003 | `sessions/wire.py`: `session_detail.context` (caller overhead-aware, transcript-only fallback) | x | 2026-10-13 |
| TASK-004 | `memory/active.py`: `peek()` read-only snapshot (no state roll, no file creation, no day-close hook) sharing `_snapshot()` with `refresh()` | x | 2026-10-13 |
| TASK-005 | `app/chat_app.py`: `compact_session()` (turn-style setup, fresh manager) + `_overhead_tokens()` via `peek`; `_detail` carries context | x | 2026-10-13 |
| TASK-006 | `serve/turn_state.py`: `compact_unclaimed()` busy gate mirroring `delete_unclaimed()` | x | 2026-10-13 |
| TASK-007 | `serve/sessions_api.py` + `serve/routes.py`: `POST /api/sessions/<id>/compact` (`{}` or `{"model": …}`; 404/409/400/503 mapping) | x | 2026-10-13 |
| TASK-008 | WebUI (`index.html`, `pages/chat/{app,composer}.js`, `chat.css`): `#context-meter` quiet % label + `#context-nudge` chip styled as the idle nudge (Gọn phiên / Phiên mới / Để sau); thresholds warn 0.7 / suggest 0.8 / urgent 0.9; per-session dismiss with +10% / urgent reshow; one nudge at a time (context wins over idle) | x | 2026-10-13 |
| TASK-009 | Tests: `test_session.py` (usage/force/no-op/incomplete-tail), `test_memory_active.py` (peek), `test_serve_chat.py` (payload context, app compact, busy 409 path, HTTP compact + WebUI pins); CHANGELOG bullet | x | 2026-10-13 |

Verification 2026-10-13:

- Focused: session/context/chat/serve/memory-active/WebUI suites green
  (counts in session log; e.g. 139 + 42 on the last focused runs).
- Full: `uv run pytest -q` → **1162 passed, 1 failed**.
  The one failure (`test_memory_fallback_safety.py::test_today_undecodable_file_keeps_typed_error`)
  reproduces on clean HEAD `3039f9f` in a detached worktree: the test
  hard-codes TODAY=2026-09-29, so once the real date passed it the file
  is past-day and `MemoryFacade` reindex raises at init instead of the
  fallback raising at `get`. Pre-existing date fragility, not this
  change; fix belongs to that test (inject/derive today), tracked
  separately — do not mask it here.
- Self-review only; no independent review yet. Pre-existing uncommitted
  `llm/pricing.py` + `test_llm_pricing.py` edits predate this task and
  are not part of it.

Known MVP limits (accepted, feed Phase 1–3): summary is still the
1000-char excerpt; `_detail` re-peeks hot files + skills index on every
detail build (turn landing included) — correct but duplicate I/O;
dismissing an error note does not suppress the suggestion bucket.

## Accepted decisions (sources)

- Hybrid over auto-only; auto stays as the safety net with the shared
  `BACKSTOP_RATIO=0.95` (no dead zone) — discussion 2026-10-13.
- Manual reuses the turn-safe tail (never orphan `tool_call_id`),
  atomic rewrite, title preservation — existing `services/session.md`
  contract.
- Read paths never call `refresh()`; estimates use `peek()` — fix from
  the `test_create_keeps_blank_session_with_running_turn` regression
  during MVP (refresh on GET raced turn state and mutated shared day).
- Usage display is real wire numbers only ("No ambient copy",
  `chat-status.js`); old last-turn usage meter stays dropped
  (`test_chat_meter.py` pins forbidden ids/names).
- Token counting (user, 2026-10-13): calibrate the estimator from
  provider-reported `prompt_tokens` already stored in transcript meta,
  per model, with the `/4` heuristic as fallback. No new dependency in
  the first step — `tiktoken` is exact only for OpenAI models, while
  the models in daily use (Muse via OpenAI-compat gateways) publish no
  tokenizer; calibration corrects the bias for the actual model.
  Revisit a BPE library only if calibrated residual error stays large.
- Summarizer model (user, 2026-10-13): the session's chat model (same
  provider/effort resolution as the turn, naming-style sidecar call).
- LLM summary on **both** paths (user, 2026-10-13): manual and auto.
  Rule-based excerpt remains the failure fallback on both, so a
  summarizer error can never block a turn or a manual compact.
- Summary contract (user confirmed proposed defaults, 2026-10-13):
  4000-char cap, prior summaries merged into one, apply immediately
  (no preview/undo). Seed prompt `seeds/prompts/compaction.md`
  (strategic structure: Goal/Done/Current state/Files/Errors &
  fixes/Constraints/Next), packaged via wheel force-include.
- Calibration storage (confirmed default): transcript
  `(estimate_tokens, usage.prompt_tokens)` pairs, per-model median of
  the last 10 in-band ratios, clamped to [0.5, 2.5], exposed as
  `detail.context.calibration`.

## Open questions (block Phase 2 only)

1. Rehydrate scope for Thyca: recent files derivable from recent
   `read`/`edit` tool calls. Thyca has no todo/plan system — explicitly
   out until one exists. Confirm file count/size caps and PathGuard
   behavior for files that moved/grew.

## Phase 1 landed (2026-10-13, uncommitted)

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-100 | Persist raw pre-call estimate in assistant meta; `calibration_factor()` per-model median (last 10 in-band pairs, clamped [0.5, 2.5]) applied in compactor, shrink guard and `context_usage`; `/4` when no pairs | x | 2026-10-13 |
| TASK-101 | `sessions/compaction.py`: `CompactionPlan` + `plan()`/`excerpt_body()`/`render()` split; marker prefix + meta counts unchanged; `apply_compaction()` re-validates the snapshot before rewrite | x | 2026-10-13 |
| TASK-102 | `sessions/summary.py` + seed `compaction.md` (force-included in wheel, verified by building it) + `app/summarizing.py` sidecar on the session model; `kind=compaction` meta-only record (assemble/trace/title/transcript treat sidecars like naming); input = user/assistant text + tool names/paths, capped 120k chars tail-biased | x | 2026-10-13 |
| TASK-103 | Manual: loop-thread summarization under new `TurnState` compact gate; API `instructions` (≤500 chars, 400 on bad shape); WebUI "Giữ ý chính gì?" input + calibration tooltip + summary wording in the confirmation note | x | 2026-10-13 |
| TASK-104 | Auto: implemented inside `AgentLoop.run` via injected summarizer (covers WebUI **and** CLI through `build_agent_loop`, superseding the run_turn-only sketch); failure falls back to rule compact in the same turn | x | 2026-10-13 |
| TASK-105 | Prior bodies merged by the summarizer prompt (prior block + merged output, 4000-char cap on accept); excerpt fallback keeps the old reserve-clip chaining | x | 2026-10-13 |

Phase 1 verification 2026-10-13: focused suites green (compaction
unit/LLM orchestration incl. changed-transcript fallback and loop
auto path, session, chat/serve, trace, WebUI node suites). Full
`uv run pytest -q` → **1177 passed, 1 failed** — the same
pre-existing date failure as the MVP run, no new failures. Wheel
build verified to contain `seeds/prompts/compaction.md`. Self-review
only; live browser/product-flow check with a real provider summary
still outstanding (tests use fakes). Not yet committed/deployed.

## Proposed tasks (not approved)

### Phase 2: Rehydrate

| ID | Task | Prereq |
|----|------|--------|
| TASK-201 | Recent-files collector: last N distinct paths from kept + just-omitted `read` calls, re-read capped via existing read/PathGuard rules; missing/oversize files degrade to path-only mentions | Q1, Phase 1 |
| TASK-202 | Continuation cue as part of the summary message (not a new role); verify assemble keeps marker hoisting and providers accept the shape on chat + responses | TASK-201 |

### Phase 3: Tokenizer library + output reserve (only if calibration is not enough)

| ID | Task | Prereq |
|----|------|--------|
| TASK-301 | Measure calibrated residual error against stored `prompt_tokens` over representative sessions (code/CJK/Vietnamese/tool JSON); publish numbers before adding any dependency | TASK-100 |
| TASK-302 | If residual warrants it: BPE tokenizer (`tiktoken`) in `llm/` only (core stays stdlib-only), calibrated `/4` remains fallback; recalibrate 0.7/0.8/0.9/0.95 together. Needs explicit dependency approval | TASK-301 |
| TASK-303 | Explicit output/reasoning reserve in the effective budget (today only the 5% headroom covers output) | TASK-301 |

### Phase 4: Surfaces

| ID | Task | Prereq |
|----|------|--------|
| TASK-401 | `context.compacted` TurnEvent (allowlist in `agent/events.py`) so a following client sees the rewrite live; Trace shows compact turns distinctly | Phase 1 |
| TASK-402 | CLI parity: REPL command for manual compact (exact spelling is a product choice) | Phase 1 |
| TASK-403 | Cache hot-token estimate per landed turn to drop the duplicate peek/scan in `_detail` | — |

## Test plan (per phase, before calling it done)

- Unit: compactor with fake summarizer (summary lands, caps hold,
  fallback on LLM error/empty, prior-summary chaining, incomplete tail
  and single-oversize-turn still safe); estimator calibration cases.
- Integration: `ChatApp.compact_session` with scripted LLM (summary +
  cost meta, busy still 409, bad instructions 400); HTTP round-trip;
  next real turn's assembled prompt contains summary (+ rehydrated
  files) and no orphan tool ids on both API shapes.
- Product flow: `thyca --serve`, drive a session past 80% with a small
  `contextTokens`, compact manually with instructions, confirm meter
  drop, transcript, Trace cost, and that the following answer uses the
  kept facts. Mocks do not prove this.
- Regression gates every phase: full `uv run pytest -q` (report the
  known pre-existing date failure separately until its own fix lands),
  `node --check` on touched chat JS, `git diff --check`.

## Risks

- Summary hallucination silently replaces facts — mitigate with
  path-only file references, caps, and fallback; never summarise away
  the only copy without the marker counts staying truthful.
- Cost surprise on auto-summarize for 1M sessions — accepted by the
  user (LLM on both paths); mitigated by summarizing user/assistant
  text only, input caps, and the rule-based fallback.
- Stored-data compatibility: old markers must keep loading; any marker
  format change needs store/assemble/trace tests in the same change.
