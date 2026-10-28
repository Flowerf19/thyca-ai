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
- [ ] POCKET-002 Audit actual pocket rendering in narrow/mobile contexts,
  reduced motion and available themes. Fix only issues proven by renders.
- [ ] POCKET-003 Verify child-bot/nested A2A feed semantics and recovery
  contexts; add a regression test only if a real gap is found.
- [ ] POCKET-004 Lead review of real diff, full gate and push decision.

## Verification

- `uv run pytest -q tests/test_team_frontend.py tests/test_webui_theme.py tests/test_webui_format.py tests/test_webui_live_rounds.py tests/test_webui_live_recovery.py tests/test_webui_stream.py tests/test_bot_exchanges_ui.py`
- `git diff --check`
