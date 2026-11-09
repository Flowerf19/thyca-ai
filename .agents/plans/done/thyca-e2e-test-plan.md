---
status: in-progress
created: 2026-10-31
last_updated: 2026-10-31
---

# Thyca E2E test plan — tử tế, theo module

## 1. Goal, scope, non-goals

**Goal:** Verify user-visible flows from UI/CLI through real app code to storage/tool/provider boundary, not single-turn smoke.

**In scope:**

- 6 module lanes: Provider/config/onboarding, Chat/streaming/session/CLI, Memory/storage/index, Bots/team/A2A/scheduler, SPA/Profile/Settings/Usage/Trace, Tools/MCP/gateway.
- 3 evidence tiers (do not mix):
  - `E2E-live`: real browser -> running app `127.0.0.1:19502` -> real provider. Needs explicit approval per inference batch.
  - `E2E-fixture`: real browser/CLI -> real local server/runtime -> isolated fake provider/MCP + temp storage.
  - `integration/unit`: HTTP/runtime/component with fakes; supporting only, never call it E2E.
- Persistence, error, cancel/retry, busy, reload, concurrency (fixture only), restart (only if approved).

**Out of scope / forbidden without separate approval:**

- No runtime code/config/dependency change, no merge/stage/commit for test findings.
- No reset/delete/overwrite `/home/flowerf/.thyca`, no stop/restart live server, no create/delete bots/groups on live, no forget/delete live memory.
- No recording credentials, raw prompts/replies, private memory, raw payloads, DOM dumps with user data.
- No destructive/corruption/locking stress on live root. All such cases use fixture roots.

## 2. Ground truth and prerequisites

- Checkout: `/home/flowerf/Projects/thyca-ai`, branch `pi/continue-20261029`, base `6ead47b` (verify with `git status --short --branch` + `git log --oneline -3` before each run).
- Live server: loopback only `127.0.0.1:19502`, from this checkout, data root `/home/flowerf/.thyca` (user-authorized). Verify: `ss -ltn '( sport = :19502 )'` + `GET /api/config/status` = `ready:true`.
- Fixture isolation (mandatory for every fixture run):
  - Start with `env -i PATH=/usr/bin:/bin HOME=/tmp unshare --user --map-root-user --mount --net ...`.
  - Inside: private `tmpfs /tmp`, `HOME=/tmp/home`, `lo` only, empty bind over `/home/flowerf/.thyca`, repo bind-remount `ro`, clean env verified before launch, ephemeral ports (never 19502).
  - `chromium --no-sandbox` only inside that namespace. No fallback outside on isolation failure.
  - Harness lives only in namespace-private `/tmp`; kill only owned PIDs; no wildcard `rm -rf /tmp/...`.
- Live browser probes: sandboxed Chromium (no `--no-sandbox` on host), temp profile, no screenshots/DOM with user content retained.

## 3. Live inference budget (to avoid “mỗi 1 lượt” but also avoid spam)

- Do not run live turns until user approves a **batch**, not per-turn ping-pong.
- Each batch approval must state: exact prompts, max turns, expected context disclosure, persisted history, cost cap, stop condition.
- Suggested starter batch (example, needs approval):
  - L1: connectivity marker (`OKTHYCA`-style) — already done once, do not repeat unless regression needed.
  - L2: context/memory: 2–3 turns referencing earlier turn + calculation, then reload, then continue; assert context use, no tool leak.
  - L3: error/recovery: provider failure or cancel; assert UI error, composer idle, no partial assistant row.
- Record only: POST count, HTTP status, `turn.completed/failed/cancelled`, stream event counts, tool count, reload persistence (bool), sanitized error type. No message bodies.

## 4. Case matrix (ID stable, do not renumber old IDs)

### PR — Provider/config/onboarding (fixture-first)

| ID | Flow | Assert | Fixture env |
|---|---|---|---|
| PR-01 | Browser -> source CLI -> loopback `/v1/models` stub -> save/default -> ready | discovery 1 GET, auth present/matched (bool only), masked readback, `ready:true`, no completion POST | E2E-fixture, done once; rerun only on config UI change |
| PR-02 | Invalid provider, 401/timeout/bad JSON, concurrent saves, restart | UI error, no crash, config unchanged or last-write-wins per contract, no secret leak | E2E-fixture; restart of fixture server only |
| PR-03 | Key masking/readback | API/UI never returns full key | integration + fixture browser check |

Sources: `thyca/serve/config_api.py`, `thyca/webui/ui/provider/js/provider.js`, `tests/test_serve_config.py`, `tests/test_webui_setup_gate_source_e2e.py`.

### CH — Chat/streaming/session/CLI

| ID | Flow | Assert |
|---|---|---|
| CH-01 | Live browser turn -> stream -> reload | 1 POST, HTTP 200, `turn.completed==1`, content events >0, tools==0, pair persists after reload |
| CH-02 | Fixture synthetic turn (JSON stub) + 401 stub | JSON turn persists; 401 shows `stream-interruption`, composer idle, 1 provider POST (no-retry per `test_401_does_not_retry`), user-only persistence; capture NDJSON terminal event explicitly (prior gap) |
| CH-03 | Cancel/retry, disconnect/rejoin, busy same-session, cross-bot progress | one terminal event per turn, busy 409/equivalent, no duplicate assistant rows, draft retained |
| CH-04 | CLI parity (`-p`, `--continue`, `--session`) | same persistence + Trace correlation as browser; no browser-only assumption |
| CH-05 | Multi-turn context + calculation + reload-continue (live batch L2) | follow-up uses prior context, arithmetic correct, no tool leak, history intact |

Sources: `thyca/serve/sessions_api.py`, `thyca/serve/bridge.py`, `thyca/serve/errors.py`, `thyca/webui/ui/chat/js/chat.js`, `tests/test_serve_chat.py`, `tests/test_cli.py`.
Contract refs: `test_401_does_not_retry`, `test_turn_errors`, `test_stream_post_accept_llm_error_ends_with_single_failed`, `test_session_detail_tags_skill_loads` (session detail has no `tool_calls`; Trace has them).

### ME — Memory/storage/index

| ID | Flow | Assert |
|---|---|---|
| ME-01 | Fixture seed -> real source CLI -> Chromium edit dialog -> `form.requestSubmit` -> Markdown/API/reload | dialog opens, prefill non-empty, toast, single heading, old text gone, total still 1 |
| ME-02 | Day rollover/reindex -> search/get -> browser | reindex only on close/day change (do not assume search triggers it), `memory_get` full context, counts match independent calc |
| ME-03 | Ownership/provenance: DM vs group vs other-bot markers, allowed owner choice | no auto raw forwarding, no other-bot private read; hard-cut: owner may choose to share own DM memory, not automatic |
| ME-04 | Update/forget/expire on throwaway roots + restart/rebuild | no live delete; index rebuild preserves semantics without inventing retained counters |

Sources: `thyca/memory/facade.py`, `thyca/memory/archived.py`, `thyca/serve/memory.py`, `thyca/webui/ui/memories/js/memories.js`, `tests/test_memory_lifecycle.py`.

### BT — Bots/team/A2A/scheduler

| ID | Flow | Assert |
|---|---|---|
| BT-01 | Fixture create 2 bots -> browser group dialog -> reload | `kind:group`, participants preserved, session file exists, toast; no turn yet |
| BT-02 | Same-bot lease vs cross-bot concurrency, paused/busy, pending exchanges | lease conflict handled, no cross-talk, attribution per bot |
| BT-03 | Per-bot model settings + A2A DM/group routing + provenance | effective precedence: turn override -> bot settings -> global default; no DM sentinel in group trace |
| BT-04 | Scheduler + restart (fixture only) | no exactly-once claim without contract; restart preserves bindings/cursors |

Sources: `thyca/bots/workspace.py`, `thyca/app/a2a_delivery/service.py`, `thyca/bots/scheduler.py`, `thyca/bots/runtime.py`, `tests/test_bot_workspace.py`, `tests/test_workspace_e2e.py`.

### UI — SPA/Profile/Settings/Usage/Trace

| ID | Flow | Assert |
|---|---|---|
| UI-01 | Live read-only nav: 5 workspaces, draft, settings dialog | no reload, no HTTP/console/mutation errors |
| UI-02 | Fixture canonical Usage/Trace: group vs DM sentinels | GET-only, files unmodified, scope + arithmetic (`requests/total_tokens/cost`) independently recomputed |
| UI-03 | Responsive/keyboard: 1280/375/320 overview/detail | no overflow, focus restore, mobile menu closed; screenshots are rendering proof only, not pixel asserts |
| UI-04 | Profile per-bot settings isolation + failure/recovery | bot A change does not leak to bot B; backend error shows safe UI state |

Sources: `thyca/webui/ui/share/router.js`, `thyca/serve/trace_api.py`, `tests/test_usage_trace_source_e2e.py`.

### TL — Tools/MCP/gateway

| ID | Flow | Assert |
|---|---|---|
| TL-01 | Component gate (done): 104 fake tests | regression only, not E2E |
| TL-02 | Fixture browser -> scripted model (SSE) -> real gateway -> `memory_remember` -> Trace/UI/Markdown | `tool.started` + `tool.finished{ok:true}`, Trace `requests:2/rounds:2/tool_calls:1/completed`, Markdown hit, final reply; session JSONL must NOT contain tool rows per canonical boundary |
| TL-03 | Deny/malformed/fail/cancel/timeout, parallel resources, MCP hot reload, secret masking | fail-closed, no secret in logs/API, no live shell |

Sources: `thyca/agent/act.py`, `thyca/tools/gateway/gateway.py`, `thyca/bots/runtime.py`, `thyca/tools/mcp.py`, `thyca/tools/path_guard.py`.
Note: `bash` unsandboxed by design; `bash: ask` is not a proven gate. Never test shell against live state.

### DB — Prototype (not live)

| ID | Flow | Assert |
|---|---|---|
| DB-01 | `test_concurrent_group_appends_multiprocess_no_loss` on synthetic root | Known fail `BrokenProcessPool`; prototype unwired, do not present as live chat bug |

## 5. Execution order and gates

1. Phase 0 — Read-only recon: `git status`, `ss`, `GET /api/config/status`, read affected code/caller/tests. No writes.
2. Phase 1 — Fixture E2E (PR/CH-02/ME-01/BT-01/UI-02/TL-02): each in its own `env -i ... unshare` invocation. Stop on isolation failure.
3. Phase 2 — Live batch only after explicit batch approval (CH-01 already done; CH-05/L2-L3 pending). One turn at a time with terminal-event check; stop on first unexpected tool/error.
4. Phase 3 — Negative/concurrency (fixture only): 401/timeout/cancel/busy/parallel/MCP reload.
5. Phase 4 — Report: per-ID pass/fail/skip, commands, sanitized evidence, confirmed bug vs observation vs limitation. `git diff --check`; no code fix without approval.

## 6. Pass/fail definitions

- Pass = UI + HTTP + persistence + provider-count + reload assertions all green in the stated env.
- Partial = some tiers green (e.g. gateway fired + Trace correct) but harness or a non-canonical assertion failed; name the wrong assumption.
- Fail = product violates an accepted contract/test; include repro, expected/actual, env, severity, log pointer (sanitized).
- Observation = behavior without accepted contract (e.g. error toast not persisted after reload, pre-setup 503); needs product decision, not a bug verdict.
- Skip = blocked by missing approval (restart, live destructive, full suite).

## 7. Required checks per run

- `git status --short --branch`, `git diff --stat`, `git diff --check`.
- Fixture: `.../.venv/bin/python -m pytest -q -p no:cacheprovider <selected> --basetemp=/tmp/pytest` inside namespace.
- Live: Chromium sandboxed temp profile; record POST/status/terminal/tool/reload booleans only.
- Never claim “212 passed = 212 E2E”; label integration vs E2E every time.

## 8. Open questions (blocking execution beyond Phase 1)

1. Approve live batch L2/L3? Exact prompts, max turns/cost, allowed context? (Default: no further live inference.)
2. Allow fixture-only restart/persistence test? (Live restart stays blocked.)
3. Allow creating/deleting fixture bots/groups only, or also live? (Default: fixture only.)
4. Pre-setup `/api/sessions` 503 vs setup-specific response — intended contract?
5. Failure UI persistence after reload — required or client-only?
6. `PathGuard` sqlite scope (`conversations.db` vs `memory.sqlite*`) and `write` persona-file policy — confirm intended contract before bug verdict.
7. Naming sidecar LLM cost/latency — should every live turn budget include it?

## 9. Execution log (2026-10-31, lead direct)

- Phase 1 fixture rerun: **3 passed in 18.43s** (`test_source_cli_first_run_gate...`, `test_source_usage_detail_and_overview_are_canonical_read_only`, `test_source_usage_overview_and_detail_responsive_screenshots`) under `env -i ... unshare --user --map-root-user --mount --net`, private `/tmp`, ro repo bind, masked `~/.thyca`, loopback only.
- Supporting integration/component rerun: **212 passed in 42.42s** (7 Chat HTTP/runtime + 46 Memory + 55 Bots/workspace + 104 tools/gateway/MCP). Label: integration/component, not 212 browser E2E.
- `git diff --check` clean; live server still on 19502; no tracked diff.
- Live batch (user-approved `thử đi`, 2026-10-31): A1 empty guard no POST; A2 5000-char 1 POST HTTP 400 (pre-accept, no model cost); B1 baseline 1 POST 200 completed tools 0; B2 long-turn cancel 1 POST cancelled idle true; B3 retry 1 POST 200 completed; B4 reload true + follow-up 1 POST 200 completed. Zero browser errors. No content recorded, 4 paid provider calls implied (B1/B2-partial/B3/B4).

## 10. Handoff

- Fixture Phase 1 done in this run. Live batch L2/L3 still blocked pending batch approval (prompts, max turns/cost, context).
- To continue: user approves live batch scope; lead updates `last_updated`, preserves IDs/history.
- Related evidence: `.agents/plans/module-e2e-test-cases.md`, `.agents/plans/local-runtime-test-cases.md` (history, not the new contract).
