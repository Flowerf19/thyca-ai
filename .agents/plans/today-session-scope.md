---
status: in-progress
created: 2026-09-28
last_updated: 2026-09-28
---

# Session-scoped <today> injection (0.86.3)

## Summary

Verified 2026-09-28: the daily tail is injected as one undifferentiated blob
across sessions. Session `...8a51` (Tavily) received 5 long `knowledge_rag`
notes from session `...70a1` with the instruction "use directly", so the
model framed a tools question around the RAG project (perceived
hallucination). Worse, the 4KB tail evicted the current session's own first
note to fit the other session's notes. Prompt-only guardrails are
insufficient (SOUL already says "recall relevant memory" and it still
over-triggered on salient content); dropping `<today>` would kill the core
"no need to repeat" feature.

Fix: split into `<today>` (current session's notes, full, own budget) +
`<today_elsewhere>` (other sessions' notes as one-line index with pullable
`memory_get` ids). Same-session continuity stays zero-friction;
cross-session recall becomes pull-on-demand. `session_id=None` keeps legacy
full-tail behavior (old tests/callers unaffected).

## Tasks

### GOAL-001: Split logic

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | `memory/active.py`: `split_today_by_session(text, day, session_id, budget)` — group `iter_session_blocks` by `meta.chat` (unattributed → index), here-blocks joined + `tail_text` budget, index lines `- HH:MM — title [day#entry]` capped at 25 (drop oldest) | x | 2026-09-28 |
| TASK-002 | Same file: `ActiveSnapshot.today_elsewhere = ""`, `refresh(..., session_id=None)` — None keeps legacy full tail + empty index | x | 2026-09-28 |

### GOAL-002: Prompt + plumbing

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-003 | `llm/prompt_manager.py`: render `<today_elsewhere>` section only when non-empty, right after `<today>` | x | 2026-09-28 |
| TASK-004 | `app/chat_app.py` + `app/cli.py`: pass `session_id=sessions.current.id` to `refresh()` | x | 2026-09-28 |
| TASK-005 | `seeds/prompts/rules.md`: rewrite `<today>` paragraph — here = working context (use directly), elsewhere = FYI index (never assume topic; `memory_get` only when relevant) | x | 2026-09-28 |

### GOAL-003: Tests + release

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-006 | `tests/test_memory_active.py`: grouping, legacy fallback, unattributed→index, 25-line cap, empty-here | x | 2026-09-28 |
| TASK-007 | `tests/test_llm_prompt_manager.py`: elsewhere section rendered/omitted | x | 2026-09-28 |
| TASK-008 | `tests/test_chat_app.py`: e2e seeded two-session file → system prompt contains own body + other's title-only + pullable id | x | 2026-09-28 |
| TASK-009 | Bump `0.86.3.dev0` + CHANGELOG; full `pytest -q`; ruff at baseline; `git diff --check` | x | 2026-09-28 |

## Test Plan

- Focused: `pytest -q tests/test_memory_active.py tests/test_llm_prompt_manager.py tests/test_chat_app.py tests/test_cli.py tests/test_serve_chat.py`
- Full: `pytest -q` (baseline 1049 passed).
- Manual: two sessions same day → each sees own notes full, other's titles only.

## Assumptions

- Unattributed notes (no `chat` meta) go to the index (cannot prove same session).
- Expired-note filtering stays out of scope (tail never filtered; separate task).
- CLI plumbing (`session_id=`) has no dedicated test (same one-line pattern as chat e2e).
- `<today>` empty when the session has no notes yet (rules explain it).
- Review round (meta/muse-spark-1.3, max thinking, fresh context): Approve with fixes; 4 Minor fixed (single strip→tail in refresh, `day_str` rename, pinned id→content assertions, duplicate-legacy-titles test). Full suite 1056 passed, ruff at baseline 188, `git diff --check` clean.
