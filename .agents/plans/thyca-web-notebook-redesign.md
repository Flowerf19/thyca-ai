---
status: done
created: 2026-08-21
last_updated: 2026-08-21
---

# Thyca web mock — assistant harness surface

## Summary

The static mock in `web/mock/` drifted into a lifestyle notebook (Nhật ký / Thơ / Sách / Nhạc). Thyca is a personal-assistant harness: user talks, the loop runs, tools remember and search, markdown under `~/.thyca` is the source of truth.

Remap the existing warm sidebar/paper shell — do not add a backend, model, or dependency — onto three product surfaces:

1. **Chat** (was Thơ): conversation with assistant Thyca. Not a co-authored diary. Not Messenger bubbles.
2. **Memories** (was Sách): reuse the cover/quote/progress treatment for `SOUL.md` / `USER.md` / `MEMORY.md` / daily files and L2 lexical hits.
3. **Trace** (was Nhạc): reuse the listening/player treatment for one agent turn (`assemble → think → act → observe`, tool rounds).

Success: a stranger who knows the README recognizes Chat / Memories / Trace as the product; composer exists only on Chat; session list / memory files / turns are selectable; 320/375/414/768px still have no horizontal overflow.

## Tasks

### GOAL-001: Establish the notebook design system

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | Replace the existing tokens with warm sidebar, paper, terracotta, mode accents, typography, spacing, motion, and z-index tokens | x | 2026-08-21 |
| TASK-002 | Replace the open-book DOM with the sidebar/workspace/notebook structure while keeping the mock route at `web/mock/index.html` | x | 2026-08-21 |

### GOAL-002: Implement mode-led reading and writing surfaces

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-003 | Add diary, poetry, books, and music views with distinct layout treatments and SVG line icons | x | 2026-08-21 |
| TASK-004 | Render user entries as prose and Thyca entries as quiet left-ruled notes; remove Messenger-style bubbles | x | 2026-08-21 |
| TASK-005 | Add mode-specific quick-action chips and a small music player / book metadata treatment where applicable | x | 2026-08-21 |

Superseded as product IA: diary/poetry/books/music copy. Visual shell kept. See GOAL-004.

### GOAL-003: Implement interaction and responsive behavior

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-006 | Implement mode/page selection, page search, new-page reset, bookmark action, music play state, and mobile sidebar drawer | x | 2026-08-21 |
| TASK-007 | Make Enter insert a newline and Cmd/Ctrl+Enter submit; keep the pen/bookmark submit affordance accessible with loading/error states | x | 2026-08-21 |
| TASK-008 | Verify no horizontal overflow and no broken affordance wrapping at 320/375/414/768px; honor reduced motion | x | 2026-08-21 |

### GOAL-004: Realign mock IA with the assistant harness

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-009 | Replace four lifestyle modes with Chat / Memories / Trace; default to Chat; hide composer outside Chat | x | 2026-08-21 |
| TASK-010 | Chat copy and thread: `you` / `thyca` assistant replies, optional tool strip; sessions in the sidebar | x | 2026-08-21 |
| TASK-011 | Memories: reuse book cover/quote/progress for canonical files, daily, and one lexical hit | x | 2026-08-21 |
| TASK-012 | Trace: reuse player/progress for one loop turn; mini-player shows current phase | x | 2026-08-21 |
| TASK-013 | Verify parser, pytest, overflow at 320/375/414/768px, mode switching, and composer only on Chat | x | 2026-08-21 |

## Test Plan

- `uv run pytest -q` for the existing Python suite.
- Parse `web/mock/index.html` with the stdlib `HTMLParser`.
- Chrome: Chat default, session switch, Memories file switch, Trace play toggle, composer hidden on Memories/Trace, no horizontal overflow at 320/375/414/768px.
- `git diff --check`.

## Assumptions

1. Static mock only. No backend, no live LLM, no new dependency.
2. Demo content is representative of README contracts (session JSONL, `memory_*`, loop phases), not a claim that the web UI is in v1.
3. Visual language stays warm paper / terracotta. IA and copy change.
4. Enter newline + Cmd/Ctrl+Enter submit stays on Chat.
5. Nhật ký as a lifestyle journal is removed; sessions live under Chat.
