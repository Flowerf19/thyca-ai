---
status: done
---

# Profile prompt ownership

## Summary

Approved: keep IDENTITY stable, put memory decision policy in SOUL, and keep tool mechanics in descriptions and runtime rules. Do not modify USER or live profiles under ~/.thyca.

## Tasks

### GOAL-001: Separate policy from mechanics

| ID | Task | Done |
|---|---|---|
| TASK-001 | Revise SOUL retrieval, storage, correction and verification policy; remove tool-description duplication | yes |
| TASK-002 | Replace IDENTITY's deployment-specific subagent restriction with capability boundaries | yes |
| TASK-003 | Keep skills and today's tail guidance in runtime rules; remove duplicate lexical policy | yes |
| TASK-004 | Update focused prompt contract tests, run tests and independent review | yes |
| TASK-005 | Clarify today is already injected; remove daily reread instruction | yes |
| TASK-006 | Extract runtime rules to packaged rules.md without changing assembled content | yes |

## Test Plan

Run prompt, active memory and assemble tests, then full pytest. Verify deletion confirmation remains in tool descriptions. Tests validate prompt content and assembly, not model compliance.

## Assumptions

Verification: full suite 984 passed. Independent review found a relative daily-read path; verified CWD resolution and corrected it to ~/.thyca/memory/YYYY-MM-DD.md. After correction, 56 focused tests passed; git diff --check passed. Changes implemented locally, not committed.

Follow-up verification: 72 focused tests passed after rules extraction. Wheel built successfully and contains thyca/seeds/prompts/rules.md. Runtime rules remain separate from user-editable profile templates; no live file is seeded. No new system message added.

No schema/runtime behavior changes. Existing non-stub live profiles remain unchanged. No README or architecture changes needed.
