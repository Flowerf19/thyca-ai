---
status: done
created: 2026-08-25
last_updated: 2026-08-25
---

# Khuông ngang trong block Thyca

## Summary

Gỡ nốt trên gutter/gạch dọc. Mỗi block Thyca có khuông 5 dòng trong dải hồng, dưới label THYCA, trên chữ. Nhạc đi theo chuỗi thinking (I–vi–IV–V), không hash/token. Cùng dải với câu thinking. Palette accent hiện tại.

## Tasks

### GOAL-001: Gỡ rail dọc, gắn khuông ngang

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | Xóa `note-rail.js` + CSS rail. Layout `.entry-thyca` xếp dọc: THYCA → khuông → chữ | x | 2026-08-25 |
| TASK-002 | `staff-map.js`: giọng theo mode, thinking → sự kiện (lặng/root/walk/dyad/triad/nốt trắng/I). Không map token | x | 2026-08-25 |
| TASK-003 | `staff-draw.js` + `staff.js`: 5 dòng, khóa Sol, vạch nhịp, stem, hợp âm, lặng. Max 2 khuông × 8 ô | x | 2026-08-25 |
| TASK-004 | Chat: block hồng trống+khóa Sol; mỗi 1s đổi câu + thêm 1 sự kiện; settle giữ nốt thinking; chốt “Sắp xong rồi…” về I | x | 2026-08-25 |

## Test Plan

- `uv run pytest tests/test_staff_map.py tests/test_note_rail.py -q` — note-rail file xóa, test map thay thế.
- `uv run pytest -q` không regress.
- Cùng `line+index+key` → cùng event. Thinking không lặp 3 câu gần nhất, breath không liền nhau.

## Assumptions

1. Nhạc theo thinking, không theo token/text. Tin lịch sử không vẽ khuông.
2. Chat = Đô trưởng. Memories = Sol trưởng (không có block chat). Không còn mode Thơ.
3. Khuông nằm trong block trả lời Thyca (bot), dưới label THYCA, trên chữ. Không composer, không tin user, không gutter.
4. Serve: `uv run thyca --serve --stop` rồi `uv run thyca --serve --daemon` — không dùng binary tool cũ.
