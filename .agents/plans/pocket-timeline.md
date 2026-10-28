# Pocket activity timeline

Status: approved by user on 2026-10-28. Lead sample implemented in
`refine(webui): simplify pocket activity timeline`.

## Decisions

- Pocket header shows one Vietnamese state label plus elapsed time.
- Generic state summaries are hidden in the pocket; concrete actions such as a
  real `current_action` still show.
- Thinking is an ordinary timeline step with marker, connector and current
  pulse, not an oversized mark-less pseudo-heading.
- Repeated thinking rounds fold into one row with a quiet `×N` count badge.
- A completed intermediate LLM round says “đã viết một phần”; only terminal
  `turn.completed` may say “đã gửi phản hồi”.
- Tool rows distinguish running from done and render repeats as
  “bash · 2 lần”, not `bashx2`.

## Non-goals

- No per-step durations; current feed data does not support them reliably.
- No nested/grouped timeline redesign and no new activity states.
- No backend or event-shape changes for this visual cleanup.

## Tasks

- [x] POCKET-001 Lead sample: red tests, implementation, render check, user
  eye approval.
- [x] POCKET-002 (audited 2026-10-28, no code change): rendered production
  pocket (real card()/updateCard/syncFeedList + sidebar/avatars CSS) at
  374/330/360px widths, type-scale large zoom, reduced-motion, all states
  (thinking current + ×N pulse badge, done settled, intermediate
  'đã viết một phần', concrete vs generic summary, long tool labels,
  error/offline, idle whisper, waiting reply). Screenshots:
  /tmp/seal-proto/pocket-full.png, pk-think-cur-w374/w330, pk-done-set-w374,
  pk-concrete-long-w374/w360, pk-error-x-w374, pk-idle-whisper-w374,
  pk-wait-reply-w374, pk-inter-part-w374, pk-offline.png, pk-typescale.png,
  pk-reduced.png, pk-reduced-done.png. No rendered defect: heading +
  elapsed correct, generic summaries hidden, connectors aligned, no
  overflow, reduced-motion static-correct (feedPulse/li-enter none).
  Idle-open empty pocket is by design (idle: footer only).
- [x] POCKET-003 (verified 2026-10-28, no production change): terminal
  turn.completed/cancelled/failed carry no bot_id (bridge.py), so only the
  primary feed upgrades to 'đã gửi phản hồi'; child turn.accepted carries
  stage bot_id and clears that child's own rows (loop.py:220). Added 2
  regression tests (green, pin existing contract):
  test_refollow_completion_upgrades_write_only_at_terminal
  (test_webui_live_recovery.py) and
  test_nested_child_never_claims_final_reply
  (test_webui_live_rounds.py, incl. one added child llm.thinking event).
- [ ] POCKET-004 Lead review and full gate completed 2026-10-28; first full
  run found three stale wording expectations in `tests/test_turn_status.py`,
  lead updated them to the approved đang/đã + repeat-count contract, then
  `uv run pytest -q` passed 1792. Push remains a separate user decision.

## Verification

- `uv run pytest -q tests/test_team_frontend.py tests/test_webui_theme.py tests/test_webui_format.py tests/test_webui_live_rounds.py tests/test_webui_live_recovery.py tests/test_webui_stream.py tests/test_bot_exchanges_ui.py`
- `uv run pytest -q` → 1792 passed
- `git diff --check`
