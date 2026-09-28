---
status: done
created: 2026-09-28
last_updated: 2026-09-28
---

# Session naming: two-turn one-shot + language-following titles

## Summary

Approved change to automatic session naming:

- New sessions keep the date/time fallback title initially (`Sáng/Chiều/Tối D thg M`).
- Automatic naming fires exactly once, after TWO user turns have each
  successfully completed an assistant reply, using context from both turns.
- Failure (LLM error, rejected/empty proposal) keeps the fallback and never
  auto-retries, including across reload/reopen.
- User-authored titles keep winning; the mid-turn rename re-read is preserved
  and re-checked after the naming LLM call returns.
- The one-shot state persists as `naming_attempted` on the session meta line,
  following existing meta-line patterns; old files without the key load as
  `False`. No extra LLM classifier call: completion counting is local.
- Titles follow the conversation language; proper names, technical terms, and
  CJK are allowed (blanket CJK exclusion removed; sanitation kept).
- The naming instruction lives in `thyca/seeds/prompts/naming.md`, loaded only
  for the standalone naming system message — never into the main chat prompt.
- Manual batch `retitle_missing` stays an explicit operation: not gated by the
  threshold or the attempted flag.

## Tasks

### GOAL-001: Naming prompt extraction + policy

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | Add `thyca/seeds/prompts/naming.md` (conversation-language title, 3–6 words/brief CJK phrase, ≤32 chars, keep proper names/technical terms, only the title) + `pyproject.toml` force-include line | x | 2026-09-28 |
| TASK-002 | `title.py`: load instruction via `naming_instruction()` (success-only cached file read, no inline fallback); remove `_NAMING_PROMPT` + `_CJK_RE` blanket exclusion; keep `sanitize_title` | x | 2026-09-28 |

### GOAL-002: Two-turn completion counting + context

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-003 | `title.py`: `NAMING_TURNS=2`, `completed_turn_count()` (per-user slices; a turn is completed iff no `meta.error` dict in slice, ends with a non-naming assistant reply, not loop_limit/error — mirrors `trace._turn_status == "completed"`); naming/system rows excluded | x | 2026-09-28 |
| TASK-004 | `title.py`: `naming_messages()` builds context from the first two completed turns (`User:`/`Thyca:` blocks); falls back to first user text when no completed turn exists (manual batch on user-only sessions); `None` only when blank | x | 2026-09-28 |
| TASK-005 | `title.py`: `accept_title()` echo check covers first two user/assistant texts (was first only); CJK accepted | x | 2026-09-28 |

### GOAL-003: One-shot persistence

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-006 | `models.py`: `Session.naming_attempted: bool = False`; `store.py`: `naming_attempted` key on meta payload (omitted when false), `scan()` returns 4-tuple, `append_naming_attempted()`, `rewrite(..., naming_attempted=)` preserves flag incl. title-less flag line; `manager.py`: `mark_naming_attempted()` (idempotent) + pass flag through truncate/mark_error/compact | x | 2026-09-28 |
| TASK-007 | `app/naming.py`: gate on `session.title`, `session.naming_attempted`, `completed_turn_count < NAMING_TURNS` (silent skip, no events); mark flag in all post-attempt paths (success/reject/LLMError); second `refresh_title()` before storing so a rename landing during the naming call still wins | x | 2026-09-28 |

### GOAL-004: Tests + verification

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-008 | Update existing tests for new semantics (CJK acceptance, turn-2 naming, request counts, stream event lists, retry script) | x | 2026-09-28 |
| TASK-009 | New focused tests: threshold, failed/incomplete turns excluded, one attempt across reload incl. failures, two-turn context content, manual title wins, language/CJK, packaged prompt + main prompt exclusion | x | 2026-09-28 |
| TASK-010 | Run focused tests, then full suite if feasible | x | 2026-09-28 |

### GOAL-005: Independent-review corrections

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-011 | `title.py`: delete inline fallback; `naming_instruction()` returns None on missing/blank/unreadable (success-only cache); `naming_messages()` None → no model call, attempt consumed | x | 2026-09-28 |
| TASK-012 | `app/naming.py`: pre-persist attempt before network (skip model on mark failure); swallow ordinary sidecar errors; started/finished always paired; CancelledError propagates with flag persisted | x | 2026-09-28 |
| TASK-013 | `title.py`: strict terminal in `_slice_completed` (assistant, no tool_calls, real text; reject trailing tools/pending/empty); `_slice_text` uses final assistant answer | x | 2026-09-28 |
| TASK-014 | `store.py` `_TITLE_LOCK` + `append_title_if_missing()`; `manager.set_title_if_missing()` with in-memory sync on race loss; sidecar uses it | x | 2026-09-28 |
| TASK-015 | Tests: prompt failure modes + cache behavior, unfinished tool slices, final-answer context, atomic set, provider/store error containment, cancellation pairing, mark-failure skip | x | 2026-09-28 |

### GOAL-006: Compaction limitation accepted

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-016 | Accept compaction limitation; user explicitly declined extra preservation machinery | x | |

User accepted the edge case: keep the fallback when compaction leaves insufficient context. No additional snapshot or compaction exemption is implemented. Current behavior can still name later if surviving history reaches two completed turns; compaction does not permanently disable naming.

**Limitation (verified in code, not fixed):** `AgentLoop.run` compacts at the
start of every turn (`agent/loop.py:82` → `observe.compact()` →
`SessionCompactor.compact`, turn 2's pre-compact runs before the naming gate
at end of turn 2). `rewrite()` replaces the file with marker + tail; dropped
turns survive only as a ≤1000-char unattributed excerpt plus
`omitted_messages/omitted_turns/omitted_chars` meta. The compactor's turn
grouping (assistant/tool-terminated) is not user-turn grouping, so
`omitted_turns` cannot evidence completed user turns. Consequence: when turn 1
alone exceeds `contextTokens` (default 272k; trigger needs giant early turns
or a lowered budget), turn 2's pre-compact drops it and the "first TWO
completed turns" are unrecoverable from the persisted transcript — naming
then fires late on surviving turns or never (fallback remains; no crash, no
wrong title). Minimal design option: exempt the first `NAMING_TURNS` completed
user-turns from omission in `SessionCompactor.compact` (bounded to 2 turns,
keeps naming purely transcript-driven). Alternative: run the naming gate
before observe-compact (architectural move of app-level naming into the loop).

**Assumptions recorded:** single-process sessions (per-manager `threading.Lock`
+ module `_TITLE_LOCK`; `filelock` precedent exists only for config in
`config/store.py`). Cross-process title races and one-shot double-fire are
possible when two processes share a sessions dir; in-process same-session
turns are serialized by the claim lock.

## Test Plan

- `tests/test_session.py`: `completed_turn_count` (failed/user-only/naming-row/loop_limit/system cases); `naming_messages` two-turn content + user-only fallback + blank None; echo across two turns; CJK accepted via `accept_title`/`display_title`; flag round-trip (mark → reload True; legacy file → False; truncate/mark_error/compact preserve); `naming_instruction()` equals file; pyproject contains force-include; `PromptManager.build` output excludes naming text.
- `tests/test_chat_app.py`: turn 1 silent (fallback, 1 request, no naming events); turn 2 names (events, title, naming meta row); LLMError/reject/empty on turn 2 → fallback + no retry on turn 3; failed turn excluded from count (naming fires on turn 3 after a failed turn 2); rename between turns wins; naming request carries both turns' texts.
- `tests/test_serve_chat.py`: single-turn request count 2→1; notebook title on second turn; title-failure via turns 1–2; stream event lists without turn-1 naming; retry script without naming reply.
- Full suite: `python -m pytest tests/ -x -q` (or full run if time permits).

## Assumptions

- Same-session concurrent turns are already serialized by `TurnState.claim`
  (409); the flag needs no cross-turn locking beyond the per-turn fresh load
  (each `_run_turn` builds a new `SessionManager` + `load`, so reload always
  sees the persisted flag). Mid-turn staleness is limited to titles, covered
  by `refresh_title()`.
- `retitle_missing` keeps legacy reach (user-only sessions retitleable via
  fallback context) and never reads/writes the attempted flag.
- `scan()` 4-tuple changes one test unpack site; `read_title()` stays
  title-only since the flag cannot change mid-turn under the claim lock.
- `.agents/` docs are historical plans; no doc refresh in this change.
