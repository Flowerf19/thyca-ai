---
status: done
created: 2026-09-25
last_updated: 2026-09-25
---

# Plan — ToolGateway unified execution + tool surface finalization

## Summary

Replace the split execution path (registry `_run`/escalation + `TaskStore` +
`BackgroundProcs`) with one `ToolGateway` front door in `thyca/tools/`, then
finalize the tool surface on top of it (one poll tool, `memory_get` drops
`path`, proposed `tool_kill`). No loop/Act-phase redesign; completion
delivery (fire-and-continue) stays a later phase — the gateway exposes the
events for it.

Locked user decisions (2026-09-25, Vietnamese discussion):
- D1: full unified runtime now, not background-backend-only: single entry,
  uniform execution records, policy seam, unified shutdown. Name: `ToolGateway`.
- D2: surface on gateway: merge `bash_read`+`tool_read` into one poll tool
  (keep name `tool_read`); `memory_get` drops `path`; `bash` stays run-only.
  No modal bash (no industry precedent, does not remove the confusion class).
- D3: immediate description stopgap for the two poll tools (zero-risk) while
  gateway work runs. Intentional throwaway once ids change.
- D4: delivery/fire-and-continue out of scope.
- Roles: contributor AND reviewer both `meta/muse-spark-1.3` thinking `max`
  (user-authorized for quality); orchestrator verifies final.
- Process: user reviews this plan → commit checkpoint → implement per GOAL
  with separate `/tmp` diffs → review → verify. No live `~/.thyca`, no push
  without approval, no prompt/live-profile changes.

Review locked 2026-09-25: O1 yes (`tool_kill` included, 13→13 net, zero
duplicates); O2 yes (single `execN` id space); O3 yes (slim registry).
Soft window: 60s default via new `limits.softTimeoutS` config knob (drop to
10s when the later delivery phase lands). Naming: no `gateway_` prefix
anywhere (package is the namespace); class `ToolGateway`; plain-verb
methods; descriptive constants; model-facing tool names unchanged.

## Contracts

### Gateway core (`thyca/tools/gateway/` package, new; precedent: `tools/builtin/`)

```
thyca/tools/gateway/
  __init__.py    # re-export ToolGateway, Execution
  gateway.py     # ToolGateway: submit/read/kill/shutdown (<300 lines)
  execution.py   # Execution record + status + unified id gen
  policy.py      # policy hook + allow-all default
```

Engines (`TaskStore`, `BackgroundProcs`) stay at their current paths;
gateway imports them. No file moves beyond the new package (minimal diff).
Naming: no `gateway_` prefix (package is the namespace); `ToolGateway` class
(precedent: `registry.py` :: `ToolRegistry`); plain verbs
(`submit/read/kill/shutdown`); descriptive constants.

### Gateway behavior
- `submit(call)`: policy check → lock → run with soft timeout (60s,
  preserved) → fast: resolve + cap immediately, no retained entry → slow:
  tracked `Execution` with unified id.
- `Execution` record (uniform): id, tool name, status (running/done/failed),
  started/finished timestamps, error flag. Audit = the record exists
  uniformly; no new logging system.
- Policy seam: `policy.check(call)` at entry, default allow-all, zero behavior
  change. No rules/approval UX (separate future feature).
- Engines behind gateway: existing `TaskStore` (coroutine) + `BackgroundProcs`
  (subprocess) stay separate classes (300-line rule); gateway routes by kind.
- Uniform soft-timeout owned by gateway; remove the `escalates` special-case
  (`bash` becomes a normal tool; `background:true` → detached start returning
  the id immediately). Bash foreground/auto-escalation observable behavior
  preserved; id/message strings change to the new names only.
- Soft window configurable via `limits.softTimeoutS` (default 60s), plumbed
  from config through toolchain into the gateway, plus an explicit field in
  the provider UI alongside existing limits fields (small JS, no CSS — the
  provider page wires limits per-field, not schema-driven). No config
  migration — new optional field with default.
- `read(id, wait)`: status + output; delta reads (only new output since the
  last check) for process output; shared wait validation (0..60, default 0;
  dedupe the two inline copies).
- Output rule (locked 2026-09-25; head+tail shape per industry survey same
  day): every reply is capped at 32KB total as head+tail (8KB head + 24KB
  tail). When clipped it MUST attach a marker with the hidden byte count +
  how to read more (no silent drops). Each tracked execution retains a bounded buffer (first
  1MB, then freeze + overflow counter); `read(id, offset, limit)` pages it
  by lines. Beyond-retained reads report the gap honestly, never invent.
  Live deltas stay fresh regardless of retention; fast (untracked) calls keep
  transcript+trace as their only record.
- `kill(id)`: terminate one tracked execution (process-group kill for procs;
  cancel + await for coroutines). Tracked executions only.
- `shutdown()`: cancel tracked tasks → kill procs → settle. Replaces
  scattered cleanup. App order: gateway.shutdown → MCP shutdown → loop stop
  (`ChatApp` + `Cli`).

### Registry slimming (`thyca/tools/registry.py`)
- Keeps: `ToolSpec`, spec store, arg validation, `to_openai_schema`, `register`.
- Moves to gateway: locks, result cap, run/escalation decisions. `Act` switches
  `dispatch` → `gateway.submit` (gateway holds the registry for specs).
  Preserve skill classification at `act.py:78-80` (`skill_name_for_call`
  before submit; reused by trace + wire) — verified must-keep 2026-09-25.
  Registry/concurrency tests will churn (rewrite, never relax).

### Tool surface (on gateway)
- One poll tool (keep name `tool_read`): progress/logs on demand + fallback
  for exec ids. Pull-only role; completion delivery is the later phase.
- Delete `bash_read` spec; update `bash` description + the two
  "Poll ... with bash_read" runtime strings.
- `memory_get` drops `path`: tool schema + `MemoryFacade.get` (exactly one of
  chunk_id/session_id) + remove `ArchivedMemory.get` path branch +
  `_allowed_path` (dead) + tests. Verified 2026-09-25: only tests + the tool
  spec use `path` (WebUI does not); canonical hits are ID-readable since the
  contract fixes, so nothing is stranded.
- PROPOSED `tool_kill(id)` (only if O1 approved).
- No prompt changes expected (`_RULES`/SOUL name only `memory_*`/skills);
  verify by grep during implementation.

### Preserved behavior
- Fast tools: same latency path (no retained entries), same locks/caps
  semantics (moved, not changed).
- Bash observable behavior unchanged apart from new tool/id names.
- Memory contracts, L2 decisions, session/loop phases untouched.
- Transcript compat: old session tool names are display-only (verify old
  sessions render; they are never re-dispatched).

## Tasks

### GOAL-001: D3 description stopgap
| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | `bash_read` description: cover auto-escalated `bg<N>`; add "only for `bg<N>`, not `task<N>`" | | |
| TASK-002 | `tool_read` description: add "only for `task<N>`, not `bg<N>`"; run focused tool tests | | |

### GOAL-002: Gateway core (built beside, wired after)
| ID | Task | Done | Date |
|----|------|------|------|
| TASK-003 | `tools/gateway/` package: `ToolGateway` + `Execution` + unified ids + policy seam (allow-all) + unit tests in `tests/test_tool_gateway.py` | | |
| TASK-004 | Fast-path immediate resolve + moved locks/caps; equivalence tests vs current dispatch | | |

### GOAL-003: Lifecycle migration + wiring
| ID | Task | Done | Date |
|----|------|------|------|
| TASK-005 | Engines behind gateway; uniform soft-timeout; remove `escalates`; `bash` as normal tool | | |
| TASK-006 | `read` (delta + offset/limit paging + retained 1MB + clip markers) + `kill` + `shutdown()`; wire Act/toolchain (incl. `limits.softTimeoutS` plumbing + explicit provider-UI field, no CSS); slim registry; update tests | | |
| TASK-007 | App shutdown unification (`ChatApp` + `Cli`); real-proc kill + task-cancel probes | | |

### GOAL-004: Tool surface finalization
| ID | Task | Done | Date |
|----|------|------|------|
| TASK-008 | `tool_read` takes exec ids; delete `bash_read`; update `bash` description + runtime strings + tests | | |
| TASK-009 | `memory_get` drops `path` (schema/facade/archived/`_allowed_path`/tests) | | |
| TASK-010 | `tool_kill` tool + prompt-grep check for stale tool names | | |

### GOAL-005: Review + final verification
| ID | Task | Done | Date |
|----|------|------|------|
| TASK-011 | Independent `meta/muse-spark-1.3` max review of actual diffs; contributor fixes confirmed findings | | |
| TASK-012 | Orchestrator: full suite, dispatch/shutdown probes, transcript-compat, baseline check, close-out | | |

## Test Plan
- GOAL-001: focused tool-spec tests.
- GOAL-002/003: new gateway tests + full registry/tool/background/memory
  suites; concurrency tests stay green (rewrite churn allowed, no relax).
- GOAL-004: tool-surface + memory suites.
- Full suite at each GOAL end: `uv run --offline pytest -q`; baseline at plan
  time: **771 passed**.
- Probes (temp roots, synthetic data): fast-resolve path; slow→tracked; kill
  one proc + one task; policy default-allow + a denying test double; shutdown
  kills real `sleep` procs + cancels pending tasks; old-session transcript
  render; 13 registered tools (net same count, zero duplicates); exec id-space checks;
  clipped reply carries marker + pointer; paging across retained buffer;
  overflow beyond 1MB reported honestly.
- `git diff --check`; new/changed classes <300 lines.

## Assumptions
- O1/O2/O3 + timeout knob + naming locked yes in review 2026-09-25 (Summary).
- `exec<N>` single id space; `bg<N>`/`task<N>` strings retired with the
  gateway (D3 text is intentional throwaway).
- Policy seam = hook + allow-all default; no rules/UX.
- No delivery/injection/wake in this plan (events exposed for later).
- MCP specs flow through gateway like all tools.
- Checkpoint commit lands after plan approval, before TASK-001.
- Side-impact review 2026-09-25 (`meta/muse-spark-1.3` max, read-only): zero
  file-level deletions; no WebUI/CSS changes for the surface change; no
  skills/store/seed/prompt changes. Correction: provider UI wires limits
  fields explicitly per-field, NOT schema-driven — user chose A 2026-09-25:
  wire `softTimeoutS` explicitly (small JS, no CSS).

## Close-out 2026-09-25

All TASK-001..012 done (contributor 001-010, reviewer 011 APPROVE,
parent 012: full suite 815 + 9 probes green). Docs updated (README +
CHANGELOG Unreleased). Plan archived to `done/`.

## Residual follow-ups (review APPROVE + parent verify)

Backlog for the module-review phase (all minor, non-blocking):
- Paged reads don't advance the delta cursor (`background.py:164-176`):
  define paged-read vs "check" semantics (advance `consumed` or document
  cursor-independence).
- Hard-timeout ≤ soft-window edge lost old wait-past-cap guarantee
  (`bash.py:72-80`, `gateway.py:171`): accept + note, or await up to
  `min(hard+grace, soft)` before tracking.
- `bg<N>`/`task<N>` fallback id strings survive (`background.py:108-110`,
  `task_store.py:43-45`): remove fallbacks or stop naming `tool_read` there.
- `shutdown()` leaves engine maps populated (`gateway.py:126-141`): purge
  engine entries after settle wait.
- Tracked executions never evicted (`gateway.py:55-57`): log as residual
  risk / future cap for the delivery phase.
- Cancelled submit orphans task handlers (`gateway.py:136-145`): document
  asymmetry or cancel unexposed handler tasks too.
- Test gaps: no 13-tool surface-count assertion; no old-transcript
  rendering regression test.
