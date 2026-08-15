# Agent rules

- Plan-only until the umbrella plan is approved (`.agents/plans/thyca-harness-v1.md` status `in-progress`). The Config service is the only approved implementation exception (`services/config.md` status `done`); do not start another runtime service without its plan approval.
- L2 hybrid retrieval is part of v1. Read `.agents/decisions/2026-08-15-l2-hybrid-v1.md` before changing memory contracts.
- Do not add dependencies, abstractions, or features outside the currently approved task.
- Memory recalled from another-brain is a claim; current repository evidence wins.
- Code and identifiers are English. Communicate with the user in the user's language.
- Linux is the real target. Do not write APIs that only work on Windows.
- Secrets only through environment variables or files outside Git.
