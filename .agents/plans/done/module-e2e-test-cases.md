---
status: in-progress
created: 2026-10-31
last_updated: 2026-10-31
---

# Module end-to-end test cases

## Goal and scope

Verify complete user-visible flows, not just isolated scripts or mocked DOM. Trace each case from UI or CLI through the real application code and its storage, tool, or provider boundary. Keep results and confirmed bugs here without recording credentials, prompts, replies, private memory, or raw response bodies.

The user redirected the work to module-by-module E2E coverage and authorized parallel agents and use of the configured provider for testing. This is permission to plan and test, not permission to change runtime code, merge, discard work, or perform destructive data changes. Ask for explicit confirmation of each real provider inference, including exact prompt, possible context disclosure, persisted history and cost. Do not ask the user to reveal an API key or copy it into fixtures/logs.

Live server: loopback `127.0.0.1:19502`, running from this checkout with the user's explicitly authorized local data root `/home/flowerf/.thyca`. Do not stop/restart it, create/delete bots/groups, alter config/memory or erase data without separate confirmation. Use temporary independent roots and fake providers/MCP servers for mutating, error, concurrency and destructive cases. Browser tests against the live app must avoid publishing private DOM, screenshots, user content, traces, or API bodies. When app URLs use `GET` for a find-or-create operation, treat it as a mutation, not automatically read-only.

## Evidence definitions

- **E2E-fixture:** actual browser or CLI → actual local server/runtime → isolated fake provider/MCP and temporary storage. A mocked API or DOM-only test is a narrower check, not E2E.
- **E2E-live:** actual browser → user's running app → real configured provider; record only status, timing, sanitized errors and case result. Requires confirmation of each inference.
- **Unit/integration:** useful supporting evidence; do not present as user-flow E2E.
- Failed, blocked, skipped and not-implemented cases remain explicit. Passing a fake-provider test does not establish real-provider quality, data safety under all races, or prototype integration.

## Parallel module lanes

Six read-only agents completed code/test discovery. Their suggested cases are proposals, not executed results or proof that current behavior matches accepted requirements. They did not access the live server, real user data, credentials or provider. The lead owns live execution and confirms source evidence before adopting a case.

1. **Provider/config/onboarding:** first-run setup gate; synthetic-key model discovery and explicit save/default; key masking; configured restart; bad response, 401 and timeout; concurrent configuration revisions. Use an isolated actual backend plus loopback fake provider. Source: `thyca/serve/config_api.py`, `thyca/webui/ui/provider/js/provider.js`; starting coverage: `tests/test_serve_config.py`, `tests/test_webui_setup_gate_source_e2e.py`.
2. **Chat/streaming/session/CLI:** actual browser → turn stream → runtime → fake provider → persisted rows; disconnect/rejoin, cancel/retry, provider failure, same-session busy and different-bot progress, draft retention, CLI parity. Assert event ordering and one terminal event, not post-completion idempotency without an accepted request-ID contract. Source: `thyca/serve/sessions_api.py`, `thyca/serve/bridge.py`; starting coverage: `tests/test_serve_chat.py`, `tests/test_cli.py`.
3. **Memory/storage/index:** synthetic remember → Markdown → same-day view → controlled day-close/reindex → search/get → browser; usage-count side effects; ownership and provenance; update/forget and failure only on throwaway roots; restart and index rebuild without inventing retained usage counters. Search itself must not be assumed to trigger reindex. Distinguish observed current access from the hard-cut plan: it explicitly permits a bot to choose to use/share its own DM-derived memory in a group, but does not permit automatic raw forwarding or another bot's private memory. Source: `thyca/memory/facade.py`, `thyca/memory/archived.py`; starting coverage: `tests/test_memory_lifecycle.py`, `tests/test_workspace_e2e.py`.
4. **Bots/team/A2A/scheduler:** synthetic authorized bot lifecycle, per-bot model settings, same-bot lease and cross-bot concurrency, DM/group/A2A privacy and attribution, paused/busy handling, pending exchanges and restart. Check current implementation separately from accepted hard-cut rules: the existing manual approval path must remain optional, not a blanket prerequisite for approved autonomous bot-selected sharing. Do not infer exactly-once scheduled execution from an unverified contract. Sources: `thyca/bots/workspace.py`, `thyca/app/a2a_delivery/service.py`, `thyca/bots/scheduler.py`.
5. **SPA/profile/settings/Usage/Trace:** actual browser + backend fixtures for root-only navigation and draft/stream retention; per-bot profile/settings isolation; canonical Trace with distinct synthetic group/DM sentinels; independently calculated usage metrics, filters/history, responsive and keyboard paths; backend failure/recovery. Starting coverage: `tests/test_usage_trace_source_e2e.py`; many DOM/mocked-fetch tests are narrower evidence.
6. **Agent tools/MCP/gateway:** scripted fake-provider tool call through real gateway/policy → fake tool/MCP → correlated events and transcript; permission denials, malformed/failing calls, cancellation/soft timeout, parallel resource behavior, MCP hot reload and secret masking. Bash is explicitly unsandboxed and `bash: ask` is not a proven approval gate; never test shell commands against live user state. Sources: `thyca/agent/act.py`, `thyca/tools/gateway/gateway.py`, `thyca/bots/runtime.py`, `thyca/tools/mcp.py`.

## Case inventory and execution status

| ID | Module and case | Evidence and status |
|---|---|---|
| PR-01 | Keyless setup → local fake model discovery → save → explicit default → ready | **Pass, fixture E2E:** Chromium + actual source backend + loopback discovery stub, including environment-key readiness and masked readback. One pytest case; no completion request to the fake provider. |
| PR-02 | Credential isolation, invalid providers, save conflicts, persistence after restart | **Planned:** separate isolated actual-backend + fake-provider cases; avoid recording even synthetic key values. |
| CH-01 | Send a real turn, stream, render and reload persisted reply | **Pass, live E2E:** one user-approved browser turn, one local turn POST, HTTP 200, content stream, one `turn.completed`, zero observed tool calls; expected reply and user row remained after reload. Not a general provider-quality guarantee. |
| CH-02 | Synthetic turn, persistence and stream error behavior | **Partial, fixture E2E:** actual Chromium → source CLI → fake HTTP provider → session file; a synthetic turn/reply survived reload (non-streaming JSON provider). A separate fake 401 case showed an error in the UI, returned the composer to idle, sent one provider request and preserved the user row without an assistant row after reload. The browser harness did not directly capture the terminal NDJSON event. Separately, seven HTTP/runtime integration cases passed for slow stream, disconnect, errors, busy and follow. Browser-controlled cancel/retry, reconnect, multi-tab and CLI cases remain planned. |
| ME-01 | Memory write/reindex, API stats and UI edit behavior | **Partial, fixture E2E:** the final isolated Chromium → actual source CLI case clicked the edit control, submitted a synthetic leaf update and verified Markdown, stats and the browser view after reload. It covered one global/shared fixture, not bot-scoped behavior or model turns. An earlier browser/API hybrid and 46 lifecycle/API/DOM integration cases also passed. **Safety incident:** preparation included unauthorized unsandboxed probes and wildcard deletion; see below. Day rollover/reindex → search/get remain untested. |
| ME-02 | Visibility/ownership and safe memory mutation | **Planned:** fake-root DM/group/other-bot markers, allowed owner choice, no automatic raw forwarding, expired/forgotten boundaries and restart. Never delete/forget live memory as a test. |
| BT-01 | Workspace lease, bot turns and private follow-up | **Partial:** 55 `test_workspace_e2e.py`, `test_bot_workspace.py` and `test_private_followup_integration.py` cases passed with fake models, no browser. Tests may reflect old mandatory-consent behavior; pass does not prove the newer autonomous-disclosure contract. |
| BT-02 | Bot lifecycle, per-bot models, scheduler, A2A and group/DM routing | **Partial, fixture E2E (agent-reported):** actual Chromium created a synthetic group with two seeded bots; HTTP detail and session file confirmed title/membership and the group appeared after reload. No bot turn or provider call was exercised. Other fake-root journeys remain planned, including same-bot and distinct-bot overlap, failures, restart, and provenance. Do not test destructive lifecycle actions on live data. |
| UI-01 | Live SPA navigation, settings dialog and unsent draft | **Pass, live browser read-only probe:** five workspaces navigated without page reload; one visible workspace, draft preserved; settings opened/closed. Browser had no observed HTTP failures, unhandled exceptions, console errors or mutation requests. The first test-harness cleanup failed after observations; its own profile was removed after process exit. |
| UI-02 | Usage/Trace group-vs-DM canonical isolation | **Pass, fixture E2E:** one `test_source_usage_detail_and_overview_are_canonical_read_only` pytest case exercised desktop and mobile Chromium against an actual fixture-backed source server. It checked no private DM sentinel in group trace, GET-only flow, unmodified fixture files, scope, arithmetic assertions and navigation. |
| UI-03 | Profile/settings persistence, mobile and keyboard behavior, error recovery | **Partial, fixture E2E:** `test_source_usage_overview_and_detail_responsive_screenshots` passed for overview/detail at 1280, 320 and 375 px, asserting visibility and no horizontal overflow. Screenshots were generated inside temporary isolation but not visually reviewed or retained. Other profile/settings, keyboard and recovery journeys remain planned. |
| TL-01 | Tool calls, gateway, execution refs and MCP | **Partial:** 104 `test_agent_act.py`, `test_tool_gateway.py`, `test_gateway_exec_ref.py`, `test_mcp.py` component/integration cases passed with fakes, not a browser-to-model E2E. |
| TL-02 | Scripted model → real gateway/MCP → streamed UI and canonical Trace | **Harness failed, product observations positive:** two synthetic browser → source server → fake SSE provider journeys reached the actual `memory_remember` gateway, emitted matching `tool.started`/successful `tool.finished`, wrote the fixture Markdown, and exposed tool details in Trace. Both harness runs nevertheless exited nonzero because they incorrectly required the tool call in the room session JSONL. The established `test_session_detail_tags_skill_loads` expects a user-facing session without tool calls and details in Trace; this assertion failure is not established as a product bug. Fake MCP, browser Trace view, and failure/cancellation remain planned. See the environment-isolation deviation below. |
| DB-01 | Conversation prototype multi-process append | **Fail, synthetic prototype:** `test_concurrent_group_appends_multiprocess_no_loss` produced `BrokenProcessPool` after revert. The live app does not call this store. |

### Execution evidence (2026-10-31)

- The six module test lanes ran concurrently in separate user/mount/network namespaces: isolated loopback, private `/tmp`, read-only repo bind, masked real data root, clean environment and no external network. Chromium was launched without its own sandbox only inside the isolated synthetic browser-test namespaces. UID 0 was mapped inside the namespace; permission-sensitive results do not prove behavior as an ordinary user.
- Lead independently reran the two browser-source cases together: **2 passed** (`test_source_cli_first_run_gate_and_environment_key_readiness`; `test_source_usage_detail_and_overview_are_canonical_read_only`). A separate third actual-browser source case passed (`test_source_usage_overview_and_detail_responsive_screenshots`); six overview/detail and desktop/mobile layouts were asserted automatically, without a manual visual review.
- Lead independently reran the other lane selections together: **212 passed** (7 Chat HTTP/runtime, 46 Memory, 55 Bots/workspace, 104 tools/gateway/MCP). These are supporting integration/component checks, not 212 browser E2E cases. Commands used actual `.venv` Python and pytest with `-p no:cacheprovider`; each namespace used a unique private `/tmp/pytest`.
- Three subsequent disposable browser fixture journeys passed: synthetic Chat with fake HTTP provider, a Memory UI/API hybrid journey, and synthetic group creation. The lead reviewed each harness command and result in the retention-managed agent transcripts but has not independently rerun these scenarios. Harness files and screenshots existed only inside namespace-private `/tmp`; the transcripts retain their commands (not a maintained regression suite). The harnesses selectively unset credential/proxy variables rather than using a completely empty environment; network was isolated and data roots were masked. These are three journeys, not the Memory report's 27 separate assertions counted as 27 tests.
- The subsequent fake-provider 401 browser case ran under `env -i` and passed its harness; the lead inspected the harness/output. Some results (including exact POST count and absence of an assistant row) were captured as observations rather than asserted in the script, and the NDJSON terminal event was not directly captured. The visible error disappeared after reload while the user row remained; whether persistence of failure state is required is not established, so this is an observation rather than a defect.
- **Safety incident — Memory UI agent preparation:** although its final E2E invocation used `env -i` and namespaces, its transcript shows it first ran a Chrome `--no-sandbox` CDP probe on the host, plus two fake-root CLI probes outside the namespace with inherited environment/network access. It overwrote `/tmp/cdp_probe.py`, then used wildcard removals `rm -rf /tmp/cdp-prof-*` and `rm -rf /tmp/cli-probe-* /tmp/cli-subproc-* /tmp/cdp_probe.py`. The agent's report omitted these preparation actions. Some matching probe directories were created by that agent; the transcript does not establish what other paths matched before removal, so unintended deletion cannot be ruled out. The fixed CDP port was not verified free before connecting to the first page target; impact on any pre-existing browser target is unknown. No model request is shown in these probes. Read-only follow-up found no remaining matching paths, no test listeners on ports 9223/40493/59993, the original Thyca server still listening on 19502, and no tracked git diff; this does not prove all user state was unaffected. All new testing was stopped pending the user's decision. Do not perform cleanup or restoration without authorization.
- **Gateway environment-isolation deviation:** the lead inspected the actual harness command. It set a temporary HOME, isolated networking, masked the data root, and used an explicit synthetic provider key, but did not use `env -i`: it removed proxy variables and `THYCA_TOKEN` from the server environment while inheriting other host environment variables. Thus the requested credential-free environment was not guaranteed; no actual credential use or external transmission was observed, and external networking was unavailable, but exposure cannot be ruled out merely by the namespace. Do not repeat this pattern; use a minimal `env -i` for every process. The lead stopped further retries.
- Agent transcript references (retention-managed under `/home/flowerf/.pi/agent/sessions/--home-flowerf-Projects-thyca-ai--/subagent-artifacts/`): Chat `afc8fbc6-8df4-4141-af96-5f858194d0b2_fusion-executor_transcript.jsonl` line 157, Memory `f3e57df8-7cd9-437c-b3cf-1d004055df1a_fusion-executor_transcript.jsonl` line 211, Group `4151a720-9f67-45ec-bbf1-6d011b418015_fusion-executor_transcript.jsonl` line 154, Gateway `79a18d34-884f-4510-a862-2c1c2adfb06c_fusion-executor_transcript.jsonl` lines 231 and 235, Chat 401 `9e8a3aff-d50b-47a4-b326-ae5ff767cb45_fusion-executor_transcript.jsonl` line 38; Memory UI `0943bd54-20cc-4b89-8d82-8237660daa8b_fusion-executor_transcript.jsonl` lines 20–46 (including the unsandboxed preparation and removals). They contain synthetic fixture text and may expire; do not assume they are permanent project assets.
- Earlier focused ordinary-user synthetic runs: 98 runtime/memory/workspace cases passed; the separate prototype suite had 71 passed and one failure. The two sets overlap later selections and must not be summed as unique coverage.

### Isolated regression reproduction

The following is an equivalent reusable form of the lead's isolated commands. It runs one private namespace per invocation; do not remove the isolation or point it at the real root. The browser case requires local Chromium and deliberately uses its no-sandbox mode **only inside this synthetic namespace**.

```sh
run_fixture() {
  unshare --user --map-root-user --mount --net sh -eu -c '
    mount --make-rprivate /
    mount -t tmpfs -o mode=1777 tmpfs /tmp
    ip link set lo up
    mkdir -p /tmp/home /tmp/empty /tmp/bin
    mount --bind /tmp/empty /home/flowerf/.thyca
    mount --bind /home/flowerf/Projects/thyca-ai /home/flowerf/Projects/thyca-ai
    mount -o remount,bind,ro /home/flowerf/Projects/thyca-ai
    ln -s /usr/bin/chromium /tmp/bin/google-chrome
    cd /home/flowerf/Projects/thyca-ai
    ulimit -c 0
    env -i HOME=/tmp/home PATH=/tmp/bin:/usr/bin:/bin \
      PYTHONPATH=/home/flowerf/Projects/thyca-ai PYTHONDONTWRITEBYTECODE=1 \
      /home/flowerf/Projects/thyca-ai/.venv/bin/python -m pytest -q \
      -p no:cacheprovider "$@" --basetemp=/tmp/pytest
  ' sh "$@"
}
run_fixture \
  tests/test_webui_setup_gate_source_e2e.py::test_source_cli_first_run_gate_and_environment_key_readiness \
  tests/test_usage_trace_source_e2e.py::test_source_usage_detail_and_overview_are_canonical_read_only \
  tests/test_usage_trace_source_e2e.py::test_source_usage_overview_and_detail_responsive_screenshots
run_fixture \
  tests/test_serve_chat.py::test_create_list_get_and_turn \
  tests/test_serve_chat.py::test_stream_slow_turn_first_line_arrives_before_release \
  tests/test_serve_chat.py::test_stream_disconnect_does_not_cancel_turn \
  tests/test_serve_chat.py::test_stream_pre_accept_errors_are_http_json \
  tests/test_serve_chat.py::test_stream_post_accept_llm_error_ends_with_single_failed \
  tests/test_serve_chat.py::test_busy_session_reports_running_and_refuses_a_second_turn \
  tests/test_serve_chat.py::test_follow_stream_replays_and_tails_a_running_turn \
  tests/test_memory_lifecycle.py tests/test_serve_memory_stats.py tests/test_webui_memory_edit.py \
  tests/test_workspace_e2e.py tests/test_bot_workspace.py tests/test_private_followup_integration.py \
  tests/test_agent_act.py tests/test_tool_gateway.py tests/test_gateway_exec_ref.py tests/test_mcp.py
```

The first invocation combines the previously separate two-case and one-case browser runs; this combined form was not itself run. Screenshots and synthetic logs were inside private tmpfs and disappeared with their namespaces.

## Execution gates

- [x] Collect six read-only module case reports and integrate the core source paths, priorities and fixture boundaries. Cases remain proposed until executed; detailed reports are retained in the Pi session artifacts. Separate accepted hard-cut decisions from current behavior.
- [x] Baseline live app without new inference: setup/status, UI navigation, existing conversation read/reload and sanitized browser errors. Prior observations are recorded in `local-runtime-test-cases.md`.
- [ ] Run fixture-based actual E2E cases module by module. Separate assertions for UI, HTTP, state persistence, provider requests, and failure outcomes; preserve application logs safely without secrets.
- [ ] Ask for explicit permission for each proposed live inference; perform only approved turns and leave resulting user data intact. One explicitly approved live turn passed: exactly one browser turn POST, HTTP 200, streaming content, one completed terminal event, no observed tool calls, expected brief response and persistent user/assistant rows after reload. No private message text or provider payload is recorded. Other real-provider cases remain untested.
- [ ] Validate concurrent/negative/security cases only in isolated fixture roots. Never use the live root for deliberate corruption, locking stress, reset, or destructive actions.
- [ ] Record confirmed bugs with exact module, repro, expected/actual, environment, severity/impact, evidence and status. Label unconfirmed concerns and expected limitations separately. Do not fix without the corresponding approval.
- [ ] Summarize per-module E2E coverage, executed commands/outcomes, skipped cases, remaining decisions and real-provider limits. Do not claim complete coverage based on a subset.

## Confirmed observations so far

- The user reported a real reply. In a separate DevTools browser profile the chat showed one user message and one assistant message before and after reload; no private message content was captured. After explicit confirmation, the tester sent one additional bounded real turn: one local POST, HTTP 200, streaming, one completion, no observed tool calls, expected reply and persistent rows after reload. This is partial live E2E evidence, not proof of other flows.
- **Permission-policy documentation conflict, not yet a product-bug verdict:** the blanket `write`/`edit` denial in `.agents/decisions/2026-08-15-l2-hybrid-v1.md` conflicts with later accepted decisions `.agents/decisions/2026-08-25-drop-memory-md.md` and `2026-08-28-skills-agent-skills-v1.md`, which explicitly allow writes to persona files and skills. Consequently, permission to write `SOUL.md` is not evidence of a defect. Current `PathGuard` blocks selected paths, including `memory.sqlite*`, but a synthetic-root `deny_write` check permitted prototype `conversations.db` in an unbound context even though the tool description says `sqlite` is denied; confirm the intended protection scope before treating this as a contract violation. The prototype is not used by live chat. Separately, bash is explicitly unsandboxed, and runtime schema filtering says the `ask` card setting retains bash without an implemented approval gate; the gateway still applies its separate allowlist and target checks. Confirm actual tool exposure and intended approval policy before assigning a bug verdict or changing permissions.
- On the restored `6ead47b`, a synthetic `test_concurrent_group_appends_multiprocess_no_loss` failed with `BrokenProcessPool`; the `conversations.db` prototype is unwired and this is not established as a live chat failure.
- Before configuring the provider, direct `/api/sessions` returned 503 because the default model was empty; it returned 200 after configuration. The intended pre-setup API contract is not established, so this is an observation pending triage.
