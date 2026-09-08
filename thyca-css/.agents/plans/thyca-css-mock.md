---
status: done
created: 2026-09-07
last_updated: 2026-09-07
---

# Thyca CSS mock

## Summary

Recreate the supplied Thyca notebook desktop mock as an independent static HTML/CSS artifact. Success means the 1536×1024 screenshot follows the reference structure, warm palette, typography, icons, live card, and composer. User-approved changes remove the spiral, stretch both message panels, separate composer from scrolling content, and distinguish inactive hover from active selection.

## Tasks

### GOAL-001: Build the reference shell

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | Build leather desk, cream notebook shell, sidebar, and header. Spiral removed at user request. | Yes | 2026-09-07 |
| TASK-002 | Build chat canvas, user bubble, live card, thought disclosure, tool chip, watermark, and composer. | Yes | 2026-09-07 |

### GOAL-002: Verify and polish

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-003 | Compare a 1536×1024 browser screenshot against the reference and correct major visual deltas. | Yes | 2026-09-07 |
| TASK-004 | Verify 375×812 and reduced-motion behavior. | Yes | 2026-09-07 |

### GOAL-003: Complete responsive conversation flow

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-005 | Fix the implicit chat grid column expanding to 380px on 320px screens; allow live copy to wrap above 448px too. | Yes | 2026-09-07 |
| TASK-006 | Verify and correct bottom-button visibility after scroll, disclosure toggle, font load and resize; respect reduced motion. | Yes | 2026-09-07 |
| TASK-007 | Verify current desktop/mobile screenshots, full-width panels, composer separation, paper corner and sidebar hover/active styles; independent review. | Yes | 2026-09-07 |
| TASK-008 | Fix the three reported book-corner artifacts: replace narrow paper-edge element and shallow underlay pseudo-element with a downward solid shadow that inherits the shell silhouette. | Yes | 2026-09-07 |

## Test Plan

- Serve with `python -m http.server 4173`.
- Capture screenshots at 1536×1024 and 375×812.
- Check browser console and child bounding boxes (not only document overflow) at 320, 375, 414, 480, 768, 1024 and 1536px.
- At the top of any overflowing conversation, the down button must appear; clicking must reach the bottom and hide it.
- Toggle Nháp nghĩ, resize and enable reduced motion; verify button state remains correct.
- No forced overflow when desktop content already fits; no backend integration.

## Assumptions

- The supplied desktop image is authoritative.
- Mobile behavior is inferred because no mobile image was supplied in the corrected reference.
- Static mock copy mirrors the visible reference; production data remains unwired.
