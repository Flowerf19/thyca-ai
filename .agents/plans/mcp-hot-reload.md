---
status: done
created: 2026-09-28
last_updated: 2026-09-28
---

# MCP hot-reload + bash self-kill guard (0.86.2)

## Summary

Bug verified 2026-09-28 (Tavily setup): MCP servers spawn once in
`ChatApp.__init__` (`chat_app.py:111`); adding `mcpServers.tavily` to
`config.json` never loads until serve restarts. The agent's only restart path
— `bash` running `thyca --serve --stop` + `--serve --daemon` (transcript
`2026-09-28T14-19-53_8a51.jsonl` round 9) — SIGTERMs its own parent serve
process mid-turn, so the turn never completes. `bash` is "no sandbox": nothing
blocks self-killing commands.

Fix (agreed with user): automatic MCP sync at turn start (no LLM-invoked
reload tool — the loop snapshots `tools` at turn start anyway, so an explicit
tool would not be usable until the next turn either), plus a cheap bash guard
as a safety net. Model/provider/pricing/limits already hot via per-turn
`_current_cfg` re-read; only MCP (+ 2 trivial stale fields) is fixed here.

## Tasks

### GOAL-001: Sync core (manager + registry)

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | `tools/mcp.py`: track `self._configs[name]` on successful spawn; add `sync(servers) -> tuple[diags, removed_spec_names]` (stop removed/changed, spawn added/changed via shared `_spawn_one` used by `spawn_all`; per-server errors into diags, never raise) | x | 2026-09-28 |
| TASK-002 | `tools/registry.py`: add `unregister(name)` (silent no-op when absent, idempotent) | x | 2026-09-28 |
| TASK-003 | `app/toolchain.py`: `install_mcp_specs` skips already-registered names (makes re-install idempotent; cross-server clash diags already emitted at spawn) | x | 2026-09-28 |

### GOAL-002: ChatApp turn-start sync

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-004 | `app/chat_app.py`: keep `self._registry`, add `self._mcp_lock` + optional `mcp_factory` ctor param (test seam, mirrors `connect` injection); `_sync_mcp(turn_cfg)` (submit sync → stderr diags → unregister removed → install → refresh `self._tools`) called in `turn()` after `overlay_turn_cfg`, before claim | x | 2026-09-28 |
| TASK-005 | Same file: refresh `self._memory.tail_kb`, `timezone_name`, `self._zone` from `turn_cfg` each turn (1–3 lines) | x | 2026-09-28 |

### GOAL-003: Bash self-kill guard

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-006 | `tools/builtin/bash.py`: refuse-before-run check in `handler` (covers foreground + background): parse segments on shell operators, strip `sudo`/`env` wrappers, deny host-kill binaries (`reboot`/`shutdown`/`poweroff`/`halt`/`init`), `systemctl reboot|poweroff|halt`, `pkill|killall …thyca…`, `thyca … --stop`; `ValueError` with "applies next turn / restart manually" message; one sentence in tool description | x | 2026-09-28 |

### GOAL-004: Tests + release

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-007 | `tests/test_mcp.py`: sync adds / removes (proc aclosed) / respawns on cfg change / no-ops when unchanged / failed spawn → diag, old intact | x | 2026-09-28 |
| TASK-008 | `tests/test_tool_registry.py`: unregister removes + idempotent no-op | x | 2026-09-28 |
| TASK-009 | `tests/test_tool_bash.py`: refused table (`reboot`, `sudo reboot`, `cmd; reboot`, `thyca --serve --stop`, `systemctl poweroff`, `pkill -f thyca`) + allowed table (`echo reboot`, `grep reboot f`, `thyca --serve --daemon`, `thyca --version`) | x | 2026-09-28 |
| TASK-010 | `tests/test_chat_app.py`: turn N sees no MCP tool → save config with fake server → turn N+1 sees `tavily__search` in `llm.tools` → remove → next turn gone; env change respawns | x | 2026-09-28 |
| TASK-011 | Bump `0.86.2.dev0` (`pyproject.toml`, `thyca/__init__.py`, `uv.lock`, README) + CHANGELOG entry; full `pytest -q`; `git diff --check` | x | 2026-09-28 |

## Test Plan

- Focused: `pytest -q tests/test_mcp.py tests/test_tool_registry.py tests/test_tool_bash.py tests/test_chat_app.py tests/test_serve_chat.py tests/test_cli.py`
- Full: `pytest -q` (baseline 984 passed on 0.86.1.dev0 per prior plan; failures = regression).
- Manual: add Tavily-like MCP to config while serve runs → next turn lists new tool, no restart; `bash reboot` refused with guidance.

## Assumptions

- Sync runs once at turn start, never mid-turn: tool list stays stable within a turn (loop snapshot).
- Lock order `_mcp_lock` → `_claim_lock` only; concurrent turns serialize briefly on sync (no-op diff is microseconds; spawn only on real change).
- Removing a server while another session's turn uses it yields a per-call "unknown tool" error for that call only (explicit user action; accepted).
- `kill <serve-pid>` by raw pid is NOT guarded (handler has no root context); documented limitation.
- Guard applies to CLI too (same spec): agent-initiated host reboot is never legitimate; the human runs it in a terminal.
- MCP crash-after-ready recovery stays out of scope (existing `RuntimeError` behavior unchanged).
- Follow-up (not this task): `ChatApp` already exceeds the 300-line class cap (334 → 359); split is a separate refactor.
- Review round 2026-09-28 (self): 2 Important fixed (`tail_kb` now global-section to avoid cross-turn race; added turn-completes-on-spawn-failure test) + 6 Minor fixed/noted (fingerprint copy, nested-exec doc, docstring wording, format; fail-closed FPs kept).
- Review round 2 (meta/muse-spark-1.3, max thinking, fresh context): Approve with fixes; 2 new Minor fixed (`&` added as segment separator + duration tokens skipped after wrappers, 5 tests added). Full suite 1049 passed, ruff at baseline 188, `git diff --check` clean.
