---
status: done
created: 2026-09-17
last_updated: 2026-09-17
---

# Composer turn controls — model, send, stop, retry

## Summary

Chat send already works: `POST /api/sessions/{id}/turn/stream` `{ text }` → claim → persist user → NDJSON → `turn.completed`. Composer Model / Suy nghĩ / Dừng / Thử lại are markup+CSS only.

This plan extends that path. Do not rebuild send. Do not probe provider `GET /models`. Do not add a catalog route. Do not write `config.json` from the composer.

Composer Model options come only from config: `provider.model` plus keys of `config.models` (the cards saved on the Provider page). `GET /api/config` already returns that. `POST /api/onboarding/verify` stays Settings-only.

**Reuse**

- `POST /api/sessions` + `POST …/turn/stream` + `GET …/turn/stream` (follow)
- `GET /api/config` `{ values.provider.model, values.provider.reasoningEffort, values.models }`
- `Config.effective_provider()` / `effective_limits()` (overlay `models[id]`)
- `SessionStore.rewrite` (compaction already rewrites JSONL)
- `TurnState.claim` / `SessionBusy` 409
- `ConnectFactory` + `replace(ProviderCfg, model=…, reasoningEffort=…)`

**Add**

- Optional `model` / `effort` on the existing turn body (`model` must be a configured id)
- `POST …/turn/cancel` + terminal `turn.cancelled`
- Retry via `{ retry: true }` on the same stream route (rewrite tail, do not append user)

**Out of scope:** attach/voice, streaming assistant `content`, rolling back `memory_remember` side effects, per-session sticky model.

## Tasks

### GOAL-001: Per-turn model and effort on send

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | Parse optional `model` (non-empty str, max 200, no newline) and `effort` (`low`/`medium`/`high`) from `/turn` and `/turn/stream` bodies. Omit → current config. Invalid → 400 `invalid text` is wrong; use 400 `{error:"invalid model"}` / `{error:"invalid effort"}`. Extra keys still ignored. | x | 2026-09-17 |
| TASK-002 | `ChatApp.turn(..., model=None, effort=None)`: `replace` provider before `ConnectFactory.create` and `AgentLoop(model=…)`. Apply `effective_provider`/`effective_limits` against the **chosen** id (not only `cfg.provider.model`). Chosen `model` must be `provider.model` or a key in `config.models`; anything else → 400 `{error:"invalid model"}`. Do not `save()` config. | x | 2026-09-17 |
| TASK-003 | ~~`GET /api/sessions` adds `models[]`.~~ Superseded: catalog is config only (`GET /api/config`). No sessions-list field, no `/api/models`. | x | 2026-09-17 |

### GOAL-002: Stop in-flight turn

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-004 | Keep the asyncio.Task for the claimed session. `POST /api/sessions/{id}/turn/cancel` empty body: 409 `{error:"session idle"}` if none; else `task.cancel()` on the loop thread, wait until `release` (cap ~5s). | x | 2026-09-17 |
| TASK-005 | `CancelledError` is not `chat_unavailable`. Hub terminal `{type:"turn.cancelled"}` (same pump as completed/failed). User line already on disk stays. Completed tool/assistant rows of earlier rounds stay. No assistant row for the cancelled think. `public_turn_error` does not map cancel to 503. | x | 2026-09-17 |
| TASK-006 | Disconnecting POST `/turn/stream` still does **not** cancel (keep `test_stream_disconnect_does_not_cancel_turn`). Only the cancel route stops the worker. Followers on GET stream receive the same terminal. | x | 2026-09-17 |

### GOAL-003: Retry last user turn

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-007 | Stream/turn body `{ retry: true, model?, effort? }` (no `text`, or `text` ignored). 400 if no last `role=user`. 409 if busy. Rewrite JSONL to **inclusive** last user (drop later assistant/tool/naming). Run loop **without** `observe.user` / without appending user. Same NDJSON as a normal send. | x | 2026-09-17 |
| TASK-008 | Cancel-then-retry: last message is already user → no rewrite, just run. Retry always means “that last user”, never an older turn. | x | 2026-09-17 |

### GOAL-004: Wire composer (no new chrome)

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-009 | Enable `#composer-model` / `#thinking-effort`. Fill options from `GET /api/config`: ids = unique(`provider.model` ∪ `keys(values.models)`). Default model = `provider.model`; default effort = `provider.reasoningEffort` (not the HTML `medium`). Send `{ text, model, effort }`. | x | 2026-09-17 |
| TASK-010 | `#stop-button` (add id): POST cancel, keep following until `turn.cancelled`/`completed`/`failed`, then unlock. `.again`: POST `{ retry: true, model, effort }` for that session; ignore if `composerBusy()`. | x | 2026-09-17 |

## Test Plan

- `tests/test_serve_chat.py`: stream body with configured `model`/`effort` reaches FakeLLM/provider payload; omit uses config; unknown model or bad effort → 400. No `models[]` on `GET /api/sessions`.
- Cancel: in-flight FakeLLM blocked on an `asyncio.Event` → POST cancel → `turn.cancelled`, session not busy, user persisted, no assistant. Second cancel 409. Disconnect-without-cancel still persists full reply.
- Retry: user+assistant on disk → `{retry:true}` → one user remains, new assistant; request messages do not duplicate the user. Empty session 400. Busy 409.
- `uv run pytest -q`. No live LLM.

## Assumptions

1. Composer lists only ids already in config (`provider.model` + `models{}`). Settings is where the user fetches `/models` and saves cards. Chat never calls verify.
2. Per-turn override does not persist. Next send uses whatever the selects currently show.
3. Stop is cooperative cancel of the task/httpx; tools already observed stay in JSONL; memory side effects are not undone.
4. Assistant prose still lands only at round end (no content streaming). Stop during think drops that round’s prose.
5. `llm.retry` stays provider 429/5xx. User Thử lại is GOAL-003 only.
6. One turn per session remains. Retry/cancel/send all hit the same claim.
