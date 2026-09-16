---
status: done
created: 2026-09-15
last_updated: 2026-09-16
---

# Backend audit remediation

## Summary

Fix confirmed backend defects from the 2026-09-15 audit, preserve the existing CLI/server/API contracts, and leave `thyca/webui/` and `thyca/chat_ui.py` untouched because another agent owns frontend work. No new dependency is needed.

Success: concurrent memory mutations cannot lose data; memory search indexes current on-disk sources; CLI and serve mode honor equivalent configuration; invalid/legacy inputs fail safely; MCP cleanup is best-effort; onboarding errors never reflect secret-bearing URLs; and the backend suite is deterministic and green.

## Tasks

### GOAL-001: Preserve memory integrity and search correctness

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | Make mutations of the same memory path synchronize across independently constructed `MemoryWriter`/`MemoryFacade` instances; cover append and all rewrite/purge paths. Add a deterministic two-facade regression test proving a concurrent append survives an update. | x | 2026-09-15 |
| TASK-002 | On `MemoryFacade` construction, index valid existing canonical and daily sources after dropping legacy `MEMORY.md`; refresh the index after `write_canonical`. Add startup and canonical-rewrite search regressions. | x | 2026-09-15 |
| TASK-003 | Remove the obsolete `MEMORY.md` mutation/read surface: reject `memory#...` selectors in all memory-tool paths and remove its writer fallback. Preserve valid daily IDs. | x | 2026-09-15 |
| TASK-004 | Make the expired legacy-fixture test deterministic with an explicit pre-expiry clock and retain an explicit expired-row assertion. | x | 2026-09-15 |

### GOAL-002: Honor configuration and session contracts

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-005 | Apply a registered model's non-empty `baseUrl` together with its reasoning override in `Config.effective_provider`; test the effective provider and request target. | x | 2026-09-15 |
| TASK-006 | Wire configured MCP servers into CLI startup, schema registration, dispatch lifecycle, and guaranteed shutdown; add a fake-process integration regression. | x | 2026-09-15 |
| TASK-007 | Make `--continue` create a new session when no prior valid session exists, while explicit `--session` still fails when missing. | x | 2026-09-15 |
| TASK-008 | Have `SessionStore.latest()` skip invalid `.jsonl` filenames exactly as `list_paths()` does. | x | 2026-09-15 |

### GOAL-003: Isolate MCP and onboarding failures

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-009 | Make MCP startup and shutdown cleanup best-effort: one failing `aclose()` must not prevent diagnostics, subsequent startup, or closing the rest. | x | 2026-09-15 |
| TASK-010 | Emit an actionable startup diagnostic when an MCP tool cannot be registered because its name, generated model name, or schema is invalid. | x | 2026-09-15 |
| TASK-011 | Ensure provider probe errors returned by the onboarding API do not echo the submitted base URL, query string, or secret-bearing exception text. | x | 2026-09-15 |

### GOAL-004: Close independent review findings

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-012 | Make public memory appends self-synchronizing so callers cannot bypass the shared path lock; keep the concurrency regression at the public API boundary. | x | 2026-09-16 |
| TASK-013 | Serialize each memory-file mutation with its archive refresh so a stale reindex cannot overwrite a newer source index; cover daily and canonical writes. | x | 2026-09-16 |
| TASK-014 | Remove every caller-supplied URL from provider-probe connectivity errors, including the `OSError` path. | x | 2026-09-16 |
| TASK-015 | Make `continue_last()` skip corrupt valid-shaped candidates and use an older valid session; retain explicit-session corruption errors. | x | 2026-09-16 |
| TASK-016 | Validate MCP input schemas as object schemas before registration and emit diagnostics for malformed schema dictionaries. | x | 2026-09-16 |
| TASK-017 | Reject legacy `memory#...` selectors before every `MemoryFacade.get()` fast path, including the path selector. | x | 2026-09-16 |

### GOAL-005: Close final review boundary cases

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-018 | Convert malformed provider URL construction or encoding failures into a generic `ProviderProbeError` so onboarding responses cannot echo submitted URL secrets. | x | 2026-09-16 |
| TASK-019 | Make public `MemoryWriter.map_heading()` self-synchronizing across its full read-modify-write operation. | x | 2026-09-16 |
| TASK-020 | Normalize invalid UTF-8 while scanning a session as `SessionCorrupt`, allowing `latest()` to skip it but explicit loading to report corruption. | x | 2026-09-16 |
| TASK-021 | Report a `SessionError` when `--continue` fallback session creation fails instead of leaking it out of the CLI. | x | 2026-09-16 |

### GOAL-006: Close post-review remaining issues

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-022 | Convert remaining provider-probe exceptions (`http.client.HTTPException` and other request failures) into a generic `ProviderProbeError` with no URL, query, or exception text. Cover through onboarding verify. | x | 2026-09-16 |
| TASK-023 | Reject MCP tool schemas whose `properties` / nested subschemas are not object-schema shaped; emit a per-tool diagnostic. | x | 2026-09-16 |
| TASK-024 | Make MCP startup diagnostics user-facing include the server name in CLI and ChatApp output. | x | 2026-09-16 |

## Test Plan

- Focused tests for every task above, including memory concurrency via synchronization primitives rather than sleeps.
- `uv run pytest -q` (frontend failures caused by concurrent frontend work may be recorded but not edited here).
- Confirm `git diff --check` and inspect the final diff to ensure no `thyca/webui/` or `thyca/chat_ui.py` changes.

## Assumptions

- The user authorized remediation of confirmed backend audit findings; all tasks are complete.
- In-process synchronization is sufficient for the documented one-process Thyca runtime; do not introduce a cross-process dependency without a demonstrated requirement.
- `MEMORY.md` was deliberately removed from the current memory contract and is not migrated or preserved as an active source.
- CLI MCP behavior should match existing serve-mode MCP behavior for the same configuration.
