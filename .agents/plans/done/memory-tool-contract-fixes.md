---
status: done
created: 2026-09-25
last_updated: 2026-09-25
---

# Plan — fix three memory tool logic errors

## Summary

User approved fixing the three reproduced logic errors from the builtin-tool audit. Keep all **13 builtin tools**, names, selectors and the `memory_get(path=...)` capability. Duplication/consolidation and broad description rewrites are deferred. Preserve the existing uncommitted profile-prompt work; do not touch live `~/.thyca`, commit, push or install.

Evidence: `/tmp/thyca-tool-audit-20260925/orchestrator-verification.json` records public `ToolRegistry.dispatch` reproductions. Contributor: `meta/muse-spark-1.3-contributor` / `xhigh`; independent reviewer: `meta/muse-spark-1.3` / `max`; orchestrator verifies the final diff and runtime checks.

### Contracts and chosen defaults

1. **Update validation** (`thyca/tools/memory.py:MemoryFacade.update`, `memory_tools.py:_update_spec`): `content` requires `summary`; reject absent/effectively empty changes instead of returning `updated`. Reject blank titles/summaries rather than creating malformed/empty entries. Validate before any writer/index mutation. Preserve normal title-only, project-only and summary/body updates, IDs and unrelated metadata. An explicitly empty `content` with a valid summary may clear details; wrappers must not hide a supplied content field from validation. Use a clear argument error, not an inferred body replacement. Invalid update requests through `thyca/serve/memory.py` must return a safe HTTP 400 rather than a new 503; other endpoint behavior stays unchanged.
2. **Get selectors** (`MemoryFacade.get`): require exactly one non-None selector among `chunk_id`, `session_id`, `path`; it must be a nonblank string. Reject invalid input before reads, usage recording or reinforcement. Preserve existing legacy `MEMORY.md` rejection messages. Validate at the facade boundary, not only inside `ArchivedMemory.get`, so neither path precedence nor the session fallback can hide multiple selectors. Keep valid daily-session fallback for today's unindexed notes and the existing path allowlist. When fetching again after reinforcement, pass only the original selector, never both a chunk ID and its derived session ID.
3. **Canonical search→get** (`MemoryFacade.get`, `MemoryWriter.locate`): valid search hits from SOUL/USER must be readable by chunk/session ID. Canonical reads must not invoke daily-memory reinforcement or alter profile files; do not weaken the writer's rejection of canonical mutation. Keep get-usage tracking for successful indexed reads and daily get's existing TTL renewal. Chunk reads remain leaf-level; session reads return their existing bounded context.

No new dependency, tool, parameter, schema framework or memory subsystem refactor. Minimal description clarification is allowed only if needed to explain one of these corrected argument contracts; no unrelated description cleanup. Keep changed classes below 300 lines (MemoryFacade baseline: 267).

## Tasks

### GOAL-001: Reject misleading memory updates

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | Add failing public-tool/facade regressions for content without summary (including alongside a title), no fields, blank-only fields; prove rejected updates preserve content/metadata. Keep positive title/project/summary-body cases. | x | 2026-09-25 |
| TASK-002 | Add smallest update validation and preserve argument presence in the tool wrapper; map newly rejected HTTP update inputs to safe 400 responses. Run focused tests and save this issue's diff separately. | x | 2026-09-25 |

### GOAL-002: Enforce unambiguous memory retrieval

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-003 | Add regressions for zero selectors, every two-selector combination and all three, using real readable session IDs and valid paths; cover blank selectors and no mutation on rejection. | x | 2026-09-25 |
| TASK-004 | Validate selectors before facade branches/fallback; retain legacy rejection and valid today/path/daily retrieval. Preserve the original selector after reinforcement. Run focused tests and save a separate diff. | x | 2026-09-25 |

### GOAL-003: Read canonical search hits without TTL mutation

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-005 | Add search→get regressions for both SOUL/USER and both chunk/session selectors, including leaf-vs-session behavior, unchanged profile bytes and successful usage tracking; retain daily TTL regression coverage. | x | 2026-09-25 |
| TASK-006 | Bypass daily reinforcement for canonical reads without weakening canonical mutation guards. Run focused tests and save a separate diff. | x | 2026-09-25 |

### GOAL-004: Independent review and final verification

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-007 | Independent Muse Spark 1.3 max review of the actual task diff, contracts, downstream HTTP mapping and regression tests; contributor fixes confirmed findings if any. | x | 2026-09-25 |
| TASK-008 | Orchestrator runs focused/full tests and dispatch-level reproductions, verifies 13 tools and preserved profile changes, updates close-out evidence and reports publication status. | x | 2026-09-25 |

## Test Plan

- First demonstrate each regression against the current buggy implementation, then verify its focused tests after the fix. Handle the three issues sequentially, retaining separate incremental diffs under `/tmp` without committing.
- Focused suite: `uv run --offline pytest tests/test_memory_tools.py tests/test_memory_lifecycle.py tests/test_memory_stats.py tests/test_memory_archived.py tests/test_serve_memory_stats.py -q` plus any new focused regression file.
- Full suite: `uv run --offline pytest -q`; last verified baseline including profile work: **760 passed**.
- Public dispatch probes: rejected updates leave data intact; all ambiguous selector combinations fail without side effects; canonical search IDs now read successfully without profile edits; valid daily get still renews TTL; path behavior and all 13 registered tool names remain.
- `git diff --check`; no real LLM, configured MCP, private profile or production-data access.

## Assumptions

- The safe default for content-only updates is a clear error requiring the summary, not implicitly reading/rewriting the old summary. No new partial-body editing feature.
- Exactly-one validation is a runtime boundary check; no new JSON Schema combinators or generic schema-validation infrastructure.
- Automatic daily reinforcement is existing intended behavior, not part of the bug fix to remove.
- The prior profile-prompt change stays in the working tree and is not to be reverted, reformatted or republished by this task.

## Verification evidence — 2026-09-25

- Contributor (`meta/muse-spark-1.3-contributor`, `xhigh`): separate diffs `/tmp/memory-fix-issue1.patch` (246 lines), `/tmp/memory-fix-issue2.patch` (133 lines), `/tmp/memory-fix-issue3.patch` (91 lines); each demonstrated failing regressions before its fix.
- Independent review (`meta/muse-spark-1.3`, `max`): **Approve**, 0 Critical/Important. Three non-blocking Minors acknowledged without scope expansion: fragile `chunk_id.startswith` type guard (safe today via prior validation), pre-existing unstripped `topic` for direct facade callers (wrappers strip), and `ValueError→400` also covering `reinforce` bad-importance input (beneficial; kept and noted).
- Focused suite (`test_memory_contract_fixes` + 5 memory/serve files): **68 passed**. Full suite: `uv run --offline pytest -q` — **771 passed** (760 + 11 new regressions). `git diff --check` clean. MemoryFacade 295 lines (<300).
- Orchestrator dispatch probes (temp root, synthetic data): content-only/no-field/blank updates rejected with bytes intact, HTTP 400 without leak; all ambiguous selector combos rejected with empty usage maps, today fallback works; canonical SOUL/USER chunk+session IDs read with identical profile bytes and usage counted; daily get still renews TTL; all 13 tool names registered.
- Protected pre-existing profile-prompt files verified byte-for-byte unchanged. No live `~/.thyca` access, commit or push.
