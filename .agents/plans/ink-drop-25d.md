---
status: in-progress
created: 2026-10-28
last_updated: 2026-10-28
---

# Giọt mực 2.5D thay avatar con dấu sáp

## Goal

Thay toàn bộ avatar con dấu sáp bằng nhân vật **giọt mực 2.5D**: silhouette
đọc rõ ở 24–65px, bề mặt có thể tích kiểu mực bóng trên giấy, hai hạt mực
nhỏ ở hai bên làm tay, và toàn bộ biểu cảm/trạng thái hiện có vẫn hoạt động.
Màu phải bám chủ đề giấy kem + Vintage Ink hiện tại, suy ra từ palette của
từng bot; không hardcode màu neon/tím như ảnh thử nghiệm.

## Accepted decisions (user, 2026-10-28)

- **Thay hẳn** con dấu sáp bằng giọt mực cho mọi bot; không giữ song song
  hai họ avatar.
- Dùng **2.5D SVG nhiều lớp**, không dùng WebGL/3D thật: bóng tiếp xúc,
  thân, lớp rãnh/rim (`--well-x/y`), lớp specular (`--hi-x/y`), tay và mặt
  (`--par-x/y`) tách mặt phẳng để dùng lại parallax hiện có.
- Hai hạt mực nhỏ "rơi vãi" ở hai bên được dùng làm **đôi tay**: buông khi
  nghỉ/chờ, một tay nhấc khi suy nghĩ/bối rối, gõ nhịp khi viết/làm việc,
  giơ lên khi hoàn tất/được gọi, rũ xuống khi lỗi/offline.
- Silhouette tất định theo bot id: `thyca` giọt cao, `march7th` giọt lùn,
  `coder` giọt mây, `researcher` giọt lùn; bot khác chọn theo hash id.
  Đổi tên không đổi hình; màu vẫn theo tint/palette hiện có.
- Giữ hợp đồng chức năng của engine hiện tại (`seal-avatar`, `data-state`,
  `data-idle`, gaze/blink, music notes, group/mention) để không phá caller;
  chỉ thay lớp hình và modifier `mk-drop`. Tên class nội bộ `seal-*` được
  giữ như legacy API của component, copy hiển thị cho user dùng "giọt mực".
- Quy trình: lead làm mẫu TASK-001 và tự kiểm chứng; sau khi user duyệt
  mắt bản mẫu mới giao các task tiếp theo cho `fusion-executor` bằng brief
  chi tiết + mẫu đã duyệt + checklist. Lead review diff thật sau mỗi lần
  giao; báo cáo của subagent không tự thành nghiệm thu.

## Evidence đã có

- Prototype đối chứng ngoài repo: sáp A–G, giọt mực H1/H2/H3 và bản tích
  hợp thật đã render ở 260/154/65/52/30/24px, bốn tint và chín trạng thái
  (`/tmp/seal-proto/integrated-drop.png`, `drop-25d.png`,
  `drop-parallax.png`, `app-drop.png`).
- H3 (mắt kem trơn) bị loại vì mất gaze và trông rỗng; H2 được chọn vì
  giữ mắt dập + glint để liếc/chớp như hệ hiện tại.
- Kiểm chứng bản mẫu: 5 test hợp đồng mới đỏ trước khi sửa, xanh sau;
  `tests/test_team_frontend.py`, `test_webui_theme.py`,
  `test_webui_format.py`, `test_webui_live_rounds.py`,
  `test_webui_live_recovery.py`, `test_webui_stream.py`,
  `test_bot_exchanges_ui.py` — **108 passed**; full gate
  `uv run pytest -q` — **1789 passed**. Daemon đã restart bằng code
  mới và chụp màn hình thật: Thyca giọt cao nâu đỏ, march7th giọt lùn xanh
  lá, hai tay đọc được ở sidebar mà không lệch tông giấy.
- Kiểm chứng TASK-002/003 (2026-10-28, `fusion-executor`): harness
  `/tmp/seal-proto/task23.html` render 311 avatar (mọi ngữ cảnh 24–154px ×
  9 trạng thái × 3 shape + 8 idle gestures × 3 shape + mention/group/
  streaming + expression-preview + motion-paused), chụp bằng Chrome:
  `task23-full.png`, `crop-s154/s65/s42/s30/s26/s24/s24b/s46/s34`,
  `z24/z30/z34` (raster thật zoom 4–6x), `probe-thinking/writing/working`
  (orbit đóng băng mỗi 45°), `probe-parallax` (cực ±4px),
  `p2-stack/tlseals/paused/reduced`, và app thật `app-verify.png`
  (sidebar 57–65px: march7th lùn, researcher mây, thyca cao). Kết luận:
  không lỗi LOD — không sửa `avatars.css`/`avatars.js`; copy hiển thị
  đồng bộ "giọt mực" ở `shell.js`+`settings.js`, test mới
  `test_avatar_display_copy_uses_ink_drop` đỏ→xanh; suite 7 file —
  **109 passed**; `git diff --check` sạch.

## Tasks

- [x] TASK-001 (lead làm mẫu): Tích hợp giọt mực 2.5D vào
      `avatars.js`/`avatars.css` (gradient theo palette, contact shadow,
      depth/shine planes, hạt vệ tinh thay vành trạng thái, hai tay mực có
      cử động theo trạng thái), cập nhật gradient dùng chung + copy trong
      `index.html`/idle details và test hợp đồng. **User đã duyệt mắt
      ngày 2026-10-28; commit mẫu `1928f22`.**
- [x] TASK-002 (subagent `fusion-executor` 2026-10-28, đã kiểm chứng — xem Evidence mới dưới đây):
      Rà soát mọi ngữ cảnh hiển thị 24–46px (trace/dashboard/settings/
      group/exchange) và Expression Studio; tinh chỉnh LOD cho tay/grain
      nếu bị rối ở cỡ nhỏ. Chỉ sửa trong phạm vi avatar CSS/markup đã
      duyệt, kèm brief + commit mẫu TASK-001.
- [x] TASK-003 (subagent 2026-10-28, đã kiểm chứng — xem Evidence mới dưới đây): Rà soát reduced-motion, motion-paused, hover
      parallax trên thẻ bot và stage-portrait, trạng thái group/mention;
      dọn mọi tham chiếu wax còn sót trong copy hiển thị (không đổi tên
      class nội bộ `seal-*` nếu không cần).
- [ ] TASK-004 (cuối): Chạy full gate theo AGENT_RULES, chụp kiểm chứng
      qua daemon (sidebar, modal trao đổi, Expression Studio, settings,
      dashboard) và báo cáo kèm ảnh để user duyệt cuối.

## Checks

- Cấu trúc: mỗi avatar chỉ một path thân namespaced; mọi `<use>` trỏ đúng
  path đó; id sinh ra không trùng giữa hai instance; có đủ hai tay
  (`drop-hand-l/r`), hạt vệ tinh (`drop-orbit`) và các mặt phẳng
  `drop-depth`/`drop-shine`.
- Hành vi giữ nguyên: gaze theo `--gaze-x/y`, blink, idle gestures,
  notifyText/music, mention overlay, group speaking, offline grayscale,
  error shake, done tự về idle sau 1,6 giây.
- Thẩm mỹ: không màu hardcode ngoài palette + màu giấy/bóng ấm dùng
  chung; specular là kem ấm (`--bot-eye`) thay cho trắng lạnh; tay không
  che mắt/miệng ở bất kỳ trạng thái nào; ở 24–30px silhouette và mắt vẫn
  đọc được dù grain bị ẩn.
- `git diff --check` sạch; không commit kèm `finnaldesign.html` đang
  deleted (không thuộc plan này).
