---
status: done
created: 2026-10-24
last_updated: 2026-10-24
---

# Provider single-editor redesign

## Accepted design and evidence

The Provider page is one editor: draft connection identity/URL/key, explicitly load models before persistence, select or type a model, enter optional prices, then save connection + model/pricing atomically with **Lưu và dùng**. Keep expert-only controls collapsed, retain manual model entry, and never treat listing as a paid probe. Do not change `defaultProvider` implicitly; bind the selected model to its chosen provider and update only `defaultModel` for the primary action. Preserve masked credentials, existing metadata, ownership guards, drafts, and async generation checks.

Research references: Open WebUI's unsaved-credential model discovery flow (`AddConnectionModal.svelte`, <https://github.com/open-webui/open-webui/blob/main/src/lib/components/AddConnectionModal.svelte>; <https://docs.openwebui.com/getting-started/quick-start/connect-a-provider/starting-with-openai-compatible/>). Jan's compact connection dialog is a visual reference, not its save-before-discovery order (<https://docs.jan.ai/desktop/remote-models/custom-endpoint>). Avoid copying AnythingLLM's expert parameter surface (<https://docs.anythingllm.com/setup/llm-configuration/cloud/openai-generic>).

## First-run setup gate (accepted extension)

Use the existing SPA router and server readiness contract only: `GET /api/config/status` is authoritative for the resolved default model/effective provider key, including environment keys and unregistered legacy models. If it returns `ready: false`, require a successful, well-formed `GET /api/config` before classifying setup as required; malformed responses, HTTP failures, and network errors are unknown/error, never a claim that the model is missing. Do not persist an onboarding flag in browser storage.

While readiness is unknown or setup is required, keep Chat hidden/inert, block non-Provider navigation and sidebar actions, and hold the initial normalized destination in memory. Provider saves and retries recheck the server; unlock and restore that destination only after `ready: true`. An already-ready session ignores ordinary config-save events. Profile catalog invalidation must drain after any in-flight bot settings save rather than be lost on one unsuccessful enter.

## Scope

- Replace the current two-card/two-form Provider workspace with one connection + model editor; keep API wire contracts unchanged.
- Merge Provider saves into fresh `/api/config` values, post only after explicit save, and broadcast `thyca:config-saved` without secret data.
- Refresh Chat inheritance labels/catalog and Profile's model catalog on that event without losing active drafts/streams.
- Make Settings read fresh config before merging its limits/timeline patch so it cannot overwrite concurrent Provider edits.
- Add focused controller/caller regressions and isolated mock-browser/source-CLI evidence. Run one full-suite final gate after lead review. No real provider calls, dependency changes, or Git publication.

## Verification

Run the agreed focused frontend/config pytest gate, `node --check` for changed JavaScript, `git diff --check`, and isolated Chrome at desktop/320/375 with a mock API journal. Verify the actual source CLI with fresh temporary data and a local stub provider; never use a live provider or the user's data. Record exact checks and artifacts. The lead completed the functional diff and evidence review; user visual acceptance and real-provider behavior are not claimed.

## Setup-gate evidence

- Browser readiness regressions: `/tmp/thyca-setup-gate-browser.json` (required/route bypass/save restore, ready legacy config, malformed and unavailable status/config retries, stale check suppression).
- Actual source CLI: `/tmp/thyca-provider-single-editor-source-e2e.json`; non-secret screenshot `/tmp/thyca-setup-gate-source-e2e.png`; process-stop proof `/tmp/thyca-setup-gate-source-e2e-stop.log`. The source E2E verifies a keyless unregistered legacy config is gated, a local `/models` stub accepts only the synthetic test key, saving a model restores Chat with the new inherited model and creates its DM only after setup, and an environment-key legacy config is ready without browser storage. The stub received one model-list GET and zero model-completion POSTs; the browser sent no turn.
- Final focused regressions: **100 passed**. Final full suite: `HOME=/tmp/sandbox-home PYTHONPATH=. .venv/bin/python -m pytest -q` — **1,738 passed**, exit 0, in 171.86s. Exact command/output: `/tmp/thyca-provider-onboarding-full-suite.log`.
- Six changed JavaScript files passed `node --check`; `git diff --check` passed. Final check record: `/tmp/thyca-provider-onboarding-final-checks.log`. Ruff was unavailable and not installed. Earlier Python compilation reported two existing invalid-escape warnings in unchanged test strings.
- Demo WebUI and stub were stopped as requested; `/tmp/thyca-provider-preview-stop.log`. All temporary E2E servers/browser processes were stopped. The user's installed app and data were untouched.
- Functional implementation and lead review are complete. Human UX acceptance and broader canonical-workspace independent review remain separate. No commit, push, CLI update, or release readiness is implied.
