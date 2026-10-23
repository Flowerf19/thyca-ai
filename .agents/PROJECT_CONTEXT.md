# Project context

`thyca-ai` is a local personal-assistant harness. The source tree and tests are authoritative; plans and older decision records may describe superseded designs.

## Current runtime

- Version: `0.86.5.dev0` (verified in `pyproject.toml` and `thyca/__init__.py`).
- Entry points: CLI in `thyca/app/cli.py`; loopback WebUI in `thyca/serve/`.
- Root and child turns share `BotRuntime` through `ChatApp` and `WorkspaceTurnService`. The CLI adapts into `ChatApp.turn`; there is no separate root turn executor or `thyca/app/run_turn.py`.
- `WorkspaceStore` is authoritative for bot/window bindings and cursors. Each bot's `WorkspaceTimeline` is the sole canonical execution store. Public room sessions are aggregate read models, not execution transcripts. There is no `room_sessions.json` migration path, shadow timeline, promotion/demotion, cutover, or rollback API.
- Chat history keeps the user message and aggregate assistant reply. Live thinking deltas are streamed while a turn runs. Trace detail resolves trusted bot/span references to original canonical `message` events in the exact room window; it excludes `view_reset` copies, so execution detail survives compaction without duplicates.
- Manual compaction handles every room participant independently, only when that bot already has a canonical context. The result reports each participant under `per_bot`; it does not create missing bindings.
- Effective model precedence: explicit operation override, then a non-default bot-card model, then the configured global default. The context meter is an estimate and omits inputs it cannot know at read time: the next user text, future mailbox batch, and query-specific recall.
- Per-bot model settings (2026-10-23; Profile page 2026-10-23): `bots/<id>/settings.json` owns `{"model": {"id", "thinking"}}`; `TWIN.md` keeps identity/persona/policies and carries no `model` key on canonical writes. Reads hydrate the card from settings when present, else fall back to the legacy card model with no migration writes; corrupt settings mark the bot invalid. The Profile page sidebar selects one bot's dossier (`?bot=<id>`); its scoped model form is the only per-bot model UI besides admin tools. No ?bot shows the shared canonical files; each bot (Thyca included) has its own tab. Turn-time precedence: explicit per-turn model/effort override, then the bot's saved settings, then the global default (effort validated against the effective model's own set). A2A recipients resolve their own card; only an explicit operation effort forwards.

## Data and safety

The user authorized a reset that deleted all `~/.thyca` data, including memories, config, keys, and skills. Do not inspect, access, or recreate `~/.thyca` or its contents. Do not add API-key examples to project guidance. Preserve unrelated working-tree changes and untracked user artifacts.

Serve is loopback-only. Do not expose secrets, internal paths, or stack traces through APIs. Memory Markdown remains its source of truth; SQLite is a derived index. Read `.agents/AGENT_RULES.md` and relevant decisions/plans before changing memory contracts.

## Verification and review status

Use `uv run pytest -q` for the full gate and `git diff --check` for whitespace validation. A focused test or fake provider does not establish full-suite status, browser behavior, or real-provider quality. Independent review of the current canonical workspace work is outstanding; do not claim it is complete.
