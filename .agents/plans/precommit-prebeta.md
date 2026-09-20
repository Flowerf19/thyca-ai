---
status: done
created: 2026-09-20
last_updated: 2026-09-20
---

# Summary
Review all pending WebUI changes, fix verified regressions through GLM-5.3 Flash, verify tests, and publish a consistent pre-beta development version only after approval.

# Tasks
### GOAL-001: Review and publish pre-beta
| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | Independently review tracked and untracked changes; verify actionable findings before fixes. | ✓ | 2026-09-20 |
| TASK-002 | GLM agent fixes confirmed issues with focused regression tests; review actual resulting diff. | ✓ | 2026-09-20 |
| TASK-003 | Set pyproject.toml, thyca/__init__.py and uv.lock to 0.8.5.dev0; replace current README beta claims with pre-beta; add accurate changelog entry. | ✓ | 2026-09-20 |
| TASK-004 | Run full pytest, applicable lint, diff checks and version consistency checks; commit and push only on acceptance. | ✓ | 2026-09-20 |

## Review findings to resolve
- Trace: order boot/reload responses; reject incomplete deduplicated pagination; synchronize navigation URLs; invalidate detail cache on fresh snapshots; retain older history/deep links; restore diagnostic metadata; avoid asserting complete pricing from known-only totals.
- Cost: invalidate old snapshots during reload; preserve pricing disclosures across pages; reject incomplete deduplicated windows; label known/partial costs honestly; remove unused model-card styles/assertions.
- Memories: serialize mutations across mobile editor copies and rerenders; preserve drafts on failure; restore visible focus. Keep the pending inline editor rather than reverting user work; add missing behavioral tests.
- Chat/chart: background completion must not change another sidebar page; thin crowded chart value labels while retaining all bars.
- Initial review verdict: Request changes. Full pre-fix tests passed but missed these transitions.

# Test Plan
- `uv run pytest -q` (initial baseline: 619 passed).
- Run available Ruff and JS syntax checks on changed files, inspect failures against HEAD if necessary.
- Check package/runtime/lock versions match, no stale current beta claims, and no accidental files or secrets in staged diff.
- Review fixes independently before committing; push without force and verify remote commit.

# Assumptions
- User authorized commit/push after successful review.
- Next patch is 0.8.5.dev0: PEP 440 development release, explicitly before beta. Do not rewrite historical release versions.
- Do not publish a package or create a release/tag without further authorization.
