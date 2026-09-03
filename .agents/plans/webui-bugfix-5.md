---
status: done
created: 2026-09-03
last_updated: 2026-09-03
---

# WebUI bugfix — 4 bug + 1 thiếu (note 2026-08-29)

Nguồn: memory `2026-08-29#29c4a448#1` — Hòa note 4 bug UI + thiếu auto-reset khi update provider.
Repo: `~/Projects/thyca-ai`, WebUI `webui/js/*.js`, CSS `webui/css/*.css`, serve `thyca/serve.py`.

## Summary

Fix từng bug một, mỗi bug xong báo Hòa trước khi qua bug tiếp. Thứ tự: scroll (4) → switch tab (2) → API key (1) → model dropdown (3) → auto-reset provider (5).

Success: mỗi bug hết repro trên serve local, `uv run pytest -q` vẫn pass, diff tối thiểu.

## Tasks

### GOAL-001: Scroll chat + thanh kéo (bug 4)

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | `fillChatAt`/`renderPage` chat tự xuống cuối khi switch session (không giữ scrollTop cũ) | x | 2026-09-03 |
| TASK-002 | Nút "xuống cuối" thay thế scrollbar ẩn (hiện khi cách đáy > ngưỡng, click xuống cuối) | x | 2026-09-03 |
| TASK-003 | Scroll sau layout ổn định (rAF, không hụt do font/ảnh) + tôn trọng prefers-reduced-motion | x | 2026-09-03 |

Test: mở session dài → switch tab → luôn ở cuối; kéo lên → nút hiện; click → xuống cuối.

### GOAL-002: Switch session tab race + lỗi câm (bug 2)

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-010 | Guard generation cho `fillChatAt` (click A rồi B nhanh không nhảy về A) | x | 2026-09-03 |
| TASK-011 | Fetch fail báo lỗi visible thay vì trang trắng | x | 2026-09-03 |
| TASK-012 | Vào lại mode Chat không ép về "Phiên mới" khi đang xem session | x | 2026-09-03 |

### GOAL-003: Update API key (bug 1)

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-020 | Card phụ check `hasStoredKey` thay vì `schemaValues.provider.apiKey` (server luôn mask rỗng) | x | 2026-09-03 |
| TASK-021 | Lưu xong refresh pages (dropdown, placeholder, hasStoredKey đồng bộ, không cần F5) | x | 2026-09-03 |
| TASK-022 | Không gửi lại key thật ở persist sau (giữ contract rỗng = giữ key cũ) | x | 2026-09-03 |

### GOAL-004: Model ID dropdown (bug 3)

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-030 | Focus ô Model ID tự fetch khi `modelOptions` rỗng | x | 2026-09-03 |
| TASK-031 | Box clone "+ Thêm model" có đủ listener (dropdown/fetch) | x | 2026-09-03 |
| TASK-032 | Dropdown rỗng báo lý do (chưa tải / filter lệch / slice 40), probe endpoint = endpoint sẽ lưu | x | 2026-09-03 |

### GOAL-005: Tự reset khi update provider (thiếu)

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-040 | Persist xong re-check `/api/config/status`, mở khóa composer, refresh kicker chat | x | 2026-09-03 |
| TASK-041 | Reset về phiên mới + clear draft khi provider đổi, giữ khi chỉ sửa giá/limits | x | 2026-09-03 |

## Assumptions

- Scrollbar ẩn là chủ ý design (commit 166ee25 "mặt giấy sạch") → không hiện lại scrollbar, thêm nút thay thế.
- Backend hot-reload config đã có (`ChatApp._current_cfg`) → GOAL-005 chỉ việc frontend.
- WebUI JS chưa có test harness → verify bằng repro tay + `pytest -q` cho backend.
