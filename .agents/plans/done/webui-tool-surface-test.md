---
status: done
created: 2026-09-25
last_updated: 2026-09-25
---

# WebUI new tool-surface browser test plan

## Summary

Goal: browser-driven end-to-end verification of the NEW TOOL SURFACE (gateway
backgrounding: `still running: execN`, `tool_read` polling + offset/limit
paging, `tool_kill`, fast-path results, head+tail `[… clipped N bytes]`
markers, exec-id rendering) through the real WebUI served by a temp-profile
`ChatApp`, driven via the chrome-devtools MCP server (CDP).

- CDP availability: VERIFIED. `chrome-devtools` MCP server connects with 29
  tools (`list_pages`, `new_page`/`navigate_page`, `take_snapshot`,
  `take_screenshot`, `fill`, `click`, `press_key`, `wait_for`,
  `evaluate_script`, console/network readers). Browser-attach smoke check
  (list_pages against a live browser) NOT run — executor runs it as TASK-001.
- LLM-driving approach: scripted double primary ($0, NO new seam — reuse
  `ChatApp(root, cfg, connect=...)` with `ScriptedLLM` exactly like
  `tests/test_serve_chat.py:39-57`; tool calls target real local tools so
  gateway behavior is real) PLUS one real-LLM turn (TASK-013, user-approved
  2026-09-25): cheapest configured model, key from ENVIRONMENT ONLY (never
  written to the temp profile or disk), trivial prompt, loose assertions
  (turn completes, no error rows); SKIPPED (not failed) on any key/model
  problem.
- Serve approach: NEVER run the `thyca --serve` binary — it hardcodes
  `~/.thyca` (`Cli._thyca_dir` is constructor-only, no env override;
  `thyca/app/cli.py:217`). Executor boots a python shim in-process:
  `ChatApp(tmp_root, cfg, connect=script)` + `make_server(host="127.0.0.1",
  port=0, ...)` + `serve_forever` in a thread (pattern:
  `tests/test_serve_chat.py:60-100`). Loopback-only bind is enforced by
  `make_server`; port 0 gives an ephemeral port.
- Scenario count: 10 browser scenarios (TASK-003..TASK-010, TASK-012..TASK-013) + fixture (TASK-002)
  + teardown (TASK-011).
- Non-duplication: existing `tests/test_webui_*.py` + `test_trace_*.py` run
  the JS modules under Node/jsdom fakes (stream reader, live rounds, format,
  concurrent streams, recovery, memory edit, trace journal/score/paging).
  None opens a real browser or a live `ChatApp`; all scenarios below assert
  pixels/DOM-after-real-turns, which those tests cannot cover.
- Version-dependence: grounded at HEAD `8a51a75`. B1 streams are concurrently
  editing `thyca/app/chat_app.py`, `thyca/agent/think.py`, serve tests. If any
  referenced symbol moved, `git diff 8a51a75 -- <file>` and adapt; plan
  targets the post-B1 tree.

Measurable pass criteria: all 10 scenarios pass with assertions below;
zero LLM-endpoint requests for TASK-003..012 (assert via double's request
log AND empty network log to provider hosts), exactly one real-LLM turn's
requests for TASK-013; temp profile dir removed at teardown; `~/.thyca`
untouched (assert mtime/absence before+after).

## Tasks

### GOAL-001: Fixture — temp serve + CDP attach (no live profile, no real LLM)

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | CDP smoke: `mcp(connect chrome-devtools)` then `list_pages`; record browser/channel (headed vs headless) and fail fast with the exact gap if attach fails | x | 2026-09-25 |
| TASK-002 | Boot shim: `mktemp -d`; write `config.json` via `default_config()` with `softTimeoutS=2` (fast backgrounding), provider stub (never called); `ScriptedLLM` scripted per scenario; `ChatApp(tmp,cfg,connect)`; `make_server(127.0.0.1, port 0)`; thread; record base URL; snapshot `~/.thyca` mtime beforehand to prove untouched | x | 2026-09-25 |

### GOAL-002: Chat scenarios — live tool surface

Chat page `/` (index.html): composer `#composer`, input `#message`, send
`#send-message`, stop `#stop-button`, list `#message-list`, live rows
`.live-status`. Turn API: `POST /api/sessions/<id>/turn`, stream
`.../turn/stream`. CDP loop per scenario: `new_page`/`navigate_page` →
`take_snapshot` → `fill #message` → `click #send-message` → `wait_for` text →
assert via `take_snapshot`/`evaluate_script` (+ `take_screenshot` on failure).

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-003 | Slow-tool backgrounding: script LLM → `bash(sleep 6; echo done)`; assert live row shows tool line, settled transcript contains `still running: exec1` text and later the `done` result after poll; assert second LLM round issued `tool_read` (double log) | x | 2026-09-25 |
| TASK-004 | tool_read paging: script LLM → `bash(seq 1 50)` with slow output; drive `tool_read id=execN offset/limit` rounds; assert transcript shows paged line windows and past-end offset renders the honest `(gap: line offset …)` message, never invented output | x | 2026-09-25 |
| TASK-005 | tool_kill mid-run: script LLM → `bash(sleep 30)`; after `still running` appears, script next round → `tool_kill id=execN`; assert transcript shows killed status and a following `tool_read` reports finished/killed, no hang | x | 2026-09-25 |
| TASK-006 | Fast-path result: script LLM → fast `bash(echo ok)`; assert result renders inline in transcript with NO `still running` text and NO exec id retained (`unknown execution id` if `tool_read` attempted — assert error surfaces, not a crash) | x | 2026-09-25 |
| TASK-007 | Head+tail truncation: script LLM → `bash` emitting > cap bytes; assert transcript shows `[... clipped N bytes; ...]` marker BETWEEN head and tail segments; fast-path variant asserts `; full output was not retained]` suffix | x | 2026-09-25 |

### GOAL-003: Trace / dashboard / provider scenarios

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-008 | Trace exec rendering: open `/trace.html`, select the TASK-003 session/turn; assert steps view shows the tool call + `still running: execN` + polled result in real message order; page through a multi-page trace (seed 25+ turns or reuse) and assert pager math holds (no dupes, single pager) | x | 2026-09-25 |
| TASK-009 | Dashboard freshness: open `/dashboard.html` after TASK-003..007; assert request/token/cost panels include the new turns (reload wins over stale boot snapshot; no permanently stale numbers after `wait_for` on totals) | x | 2026-09-25 |
| TASK-010 | Provider softTimeoutS round-trip: open `/provider.html` `#provider-form`; set limits soft-timeout field (`provider-dom.js` `limitsSoftTimeoutS`, resolve exact `#id` via snapshot — range 1..300); save; reload page and assert persisted; run one slow-tool turn and assert backgrounding threshold honors the new value | x | 2026-09-25 |

### GOAL-004: Teardown + evidence

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-011 | Teardown: `httpd.shutdown()`, join thread, `chat.shutdown()`; assert `~/.thyca` mtime unchanged; `rm -rf` temp root; attach per-scenario screenshot + console-error log (zero unexpected console errors) to the report | x | 2026-09-25 |

## Test Plan

Per-scenario assertions are in the TASK rows. Shared failure protocol: on any
assert failure, `take_screenshot` + `list_console_messages` + `get_network_request`
capture, then continue to teardown (never leave the shim running).

Edge cases (must be covered inside the scenarios above, not skipped):
- Timeout: `tool_read wait=0` returns immediately with progress (TASK-004);
  `wait_for` CDP timeouts must fail the scenario, not hang the suite.
- Kill mid-run: TASK-005; also kill an already-finished id → status report,
  no traceback in UI.
- Stale dashboard: TASK-009 — boot snapshot taken before turns must refresh
  on reload/navigation, never show permanently stale totals.
- Multi-page traces: TASK-008 — pager clamp, no duplicate pagers, deeplink
  turn on page N selects pages before reveal (mirrors `test_trace_*.py`
  Node coverage, now in a real browser).
- Turn-cancel vs backgrounding: covered by dedicated TASK-012 (user decision
  2026-09-25: separate, not folded into TASK-005).
- Live `~/.thyca` contact: FORBIDDEN in every executor step — no `thyca`
  binary, no `Path.home()/".thyca"`, temp root only.

## Assumptions

- Headless Chromium via the MCP-managed browser (decided 2026-09-25);
  headed only to debug a failing scenario.
- Scripted-double turns are acceptable proxies for real-LLM turns because
  the tool surface under test lives server-side of the LLM boundary
  (`Act`→`ToolGateway`); UI assertions are on rendered transcript/steps DOM.
- `softTimeoutS=2` in the temp config is tolerated (production default is
  higher); provider page round-trips whatever value the user picks.
- B1 streams land without changing the `ChatApp(root, cfg, connect)` /
  `make_server` / `ScriptedLLM` seams; executor re-verifies imports first.

Resolved 2026-09-25: (1) scripted double + ONE real-LLM turn (TASK-013);
(2) headless, headed only to debug a failing scenario; (3) separate
TASK-012.

### GOAL-005: Extended scenarios (execute after GOAL-003, before TASK-011)

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-012 | Stop-cancel vs backgrounding: script LLM → `bash(sleep 30)`; after `still running` appears, CDP `click #stop-button`; assert turn UI shows cancelled (no hang, no error rows), exec keeps running server-side per gateway contract (later `tool_read` shows completion); assert no dangling busy session (fresh turn works right after) | x | 2026-09-25 |
| TASK-013 | One real-LLM turn (user-approved): executor picks cheapest configured model at runtime (record which); key from ENVIRONMENT ONLY — construct the provider entry in-memory, never write it to temp `config.json`/`auth.json` or disk; trivial prompt (`reply with exactly: OK` + one `bash(echo hi)` tool call if the model complies); assert loosely — turn completes 200, transcript shows assistant text, zero error rows; network log shows only this turn's provider requests; on any key/model failure, mark SKIPPED (not failed) and continue | x | 2026-09-25 |
