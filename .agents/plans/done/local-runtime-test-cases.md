---
status: in-progress
created: 2026-10-31
last_updated: 2026-10-31
---

# Local runtime test cases

> The user redirected subsequent work to module-by-module end-to-end testing. Continue with `module-e2e-test-cases.md`; this file preserves the earlier local test evidence and outstanding cases.

## Scope and safety

Test the existing Thyca application from the checkout at `6ead47b`, using the already-running loopback server. The user explicitly authorized using `/home/flowerf/.thyca` for this local app and Chromium DevTools for testing. Do not record API keys, prompt or reply contents, private memories, or full API payloads here. Do not reset, delete, or overwrite existing data; do not stop or restart the user's server without separate confirmation. Provider calls can incur cost and transmit conversation and memory context: obtain explicit confirmation of each new real inference before sending it. Use fake roots and fake providers for destructive, failure-injection, stress, and concurrent-writer tests.

The running app currently uses `SessionManager` and the memory SQLite index; `thyca/conversations/store/` is an unwired prototype. Tests of that prototype do not validate the running chat flow. Do not change product code as part of this test-plan task.

## Cases and results

- [x] **TC-01 — Keyless startup:** when provider setup was required, Chromium loaded the Provider workspace with setup state `required`. Initial HTTP `/` and `/api/config/status` returned 200. No inference was sent by the tester.
- [x] **TC-02 — Configured startup:** after the user configured the provider, `/api/config/status` returned ready, `/api/sessions` and `/api/memory/stats` returned 200, and Chromium opened Chat. The tester did not read or record credentials.
- [x] **TC-03 — Existing real conversation and reload:** the user sent a message and reported a response. In an isolated Chromium profile, one user message and one assistant message were visible before and after reload. Counts only; no content captured. During the brief probe there were no observed uncaught JavaScript exceptions or HTTP failures.
- [x] **TC-04 — Memory/runtime focused tests:** with isolated temporary HOME, no provider token, offline dependencies and temporary pytest base, 98 tests passed across `test_memory_lifecycle.py`, `test_memory_archived.py`, `test_memory_tools.py`, `test_serve_daemon.py`, `test_bot_workspace.py`, `test_workspace_timeline.py`, and `test_workspace_e2e.py`. This does not establish real-provider quality or complete end-to-end safety.
- [x] **TC-05 — Conversation-store prototype baseline:** on synthetic roots at `6ead47b`, `test_conversation_store.py` plus `test_conversation_ref.py` produced 71 passed and 1 failed: `test_concurrent_group_appends_multiprocess_no_loss` ended in `BrokenProcessPool`. This is not a failure in the running chat path.
- [x] **TC-06 — Navigation and draft preservation:** in an isolated Chromium DevTools profile, navigated Memories, Profile, Provider, Dashboard and Chat; each showed one active workspace at `/`, without document reload. An unsent synthetic draft remained in the composer on return. The settings dialog opened and closed. During this bounded run: zero observed browser exceptions, console errors, HTTP errors or non-GET requests. The local harness exited nonzero after the assertions because Chromium's temporary profile was still being written during cleanup; the tester removed that run's own profile after confirming it was no longer in use. This was a test-harness cleanup issue, not evidence of an app failure.
- [x] **TC-07 — New real-model turn:** after explicit user approval of one exact turn and cost/context disclosure, Chromium DevTools submitted exactly one local turn POST; HTTP 200, streaming content and one `turn.completed` event were observed, with no tool invocation or uncaught JavaScript exception. The expected brief reply and user row were visible and persisted after reload. No message content, credentials, or provider payload was recorded; no second turn was sent.
- [x] **TC-08 — Real app memory screen, basic view:** the Memories workspace opened during TC-06 and `/api/memory/stats` returned HTTP 200. No `forget`, `reinforce`, `update`, or search/get tool was invoked. This verifies navigation and the basic stats request, not the correctness of private memory content or mutation flows.
- [ ] **TC-09 — Concurrency and error paths:** use isolated synthetic state and fake providers to verify same-bot lease, independent bots, busy/timeout paths, no partial writes, and non-duplication. Do not assume 16-process prototype stress reflects the deployed product topology.
- [ ] **TC-10 — Restart and persistence:** blocked until the user separately approves stopping/restarting the live server. Keep credentials and all existing user data untouched.
- [ ] **TC-11 — Broader gate:** assess safe isolation of full-suite tests before running, especially shared `/tmp` outputs, browser processes, network, inherited credentials, and real data. Run applicable checks only once isolation is established; report skips and existing failures.

## Findings log

- **OBS-01 — session list before provider setup:** during keyless startup, direct `GET /api/sessions` returned 503. `ChatApp.list_payload()` resolves the configured provider/model, which raises `ConfigError` when the default model is empty. Once configured, the same endpoint returned 200. The setup UI prevents normal use before configuration; whether the endpoint should instead return a setup-specific response is unconfirmed and needs a product decision before changing behavior. No credentials or payload contents recorded.
- **BUG-01 — unwired prototype concurrent writes:** at `6ead47b` the synthetic multiprocess group-append test failed with `BrokenProcessPool`; the checkpoint's commit message records `SIGBUS`/SQLite locking protocol failures. Existing SQLite conversation store is not used by the live app. Avoid treating this as a proven live-runtime defect. Earlier uncommitted experimental changes were reverted at user request.

## Open permissions and limitations

Explicit approval remains required for each paid provider request, destructive operation, app-server stop/restart, or changes to runtime code. The user has not selected a fork/process-support design for the prototype. Test findings are not authorization to alter accepted data-safety or permission rules. Never publish raw browser DOM, local DB contents, keys, trace text, or user messages in this plan.
