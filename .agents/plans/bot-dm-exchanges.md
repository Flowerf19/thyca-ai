---
status: done
created: 2026-10-26
last_updated: 2026-10-26
---

# Bot↔bot exchanges trong DM + gộp trong room

## Goal

Khi 2 bot trao đổi với nhau, người dùng phải thấy được trong DM của cả 2 bot:
một mục gộp "N messages with <Bot B>" theo từng cặp bot; bấm vào mở modal
xem toàn bộ hội thoại A↔B. Trong room gốc, các dòng trao đổi bot↔bot liên
tiếp cũng được gộp thành một mục bung tại chỗ.

## Bối cảnh (điều tra 2026-10-26)

- Bot gửi tin qua `bot_send` (`thyca/bots/admin_tools.py:144-176`); recipient
  chạy turn riêng (`chat_app.py:1482-1525`).
- Tin gửi đi chỉ được ghi thành row trong room KHÔNG phải DM
  (`_record_bus_events`, `chat_app.py:1620-1660`, meta `bus_event:True`,
  kind delegate/message/help_request, content cắt 1000 ký tự); reply ghi ở
  `chat_app.py:1606-1617` (kind `result`). DM của từng bot hiện không chứa
  tin bot→bot đến.
- UI cũ từng có collapse inline trong room (mất ở rewrite SPA `f4249c0`);
  cửa sổ xem thread A↔B và mục "messages with" trong DM chưa từng có.
  Test `test_team_frontend.py` cấm dùng lại tên legacy
  (`team-exchange`...) — khôi phục phải đặt tên/CSS mới.

## Accepted decisions (user, 2026-10-26)

- Mục gộp nằm trong **DM của cả 2 bot** (cần backend đọc chéo, chỉ đọc).
- Bấm vào mở **modal nổi**: header A ⇄ B, bong bóng theo từng bot.
- Gộp **theo cặp bot**: toàn bộ trao đổi 2 chiều với bot đối tác.
- Vòng này gồm cả **gộp bung tại chỗ trong room gốc**.

## Tasks

- [x] TASK-001 (prereq): Xác định chính xác cách nhận diện cặp bot cho từng
      bus/result row hiện có (sender từ `meta.bot_id`; recipient suy từ đâu —
      liên kết `trace_id`/`parent_span_id` với result row, hay field sẵn có).
      Nếu recipient không suy được cho dữ liệu cũ thì ghi rõ giới hạn và,
      nếu cần, bổ sung meta khi ghi mới (không sửa dữ liệu đã lưu).
- [x] TASK-002: Endpoint backend chỉ đọc (theo pattern route/API hiện có):
      danh sách đối tác trao đổi của một bot (partner, count, last_ts) và
      toàn bộ tin A↔B đã ghi (2 chiều, thứ tự thời gian, kèm kind/ts).
      Không ghi thêm row, không đổi privacy model của DM.
- [x] TASK-003: Frontend DM: render mục gộp "N messages with <Bot B>" cho
      từng đối tác (vị trí theo mốc thời gian trao đổi gần nhất trong dòng
      DM), bấm mở modal (snapshot lúc mở) với header A ⇄ B và bong bóng
      theo bot gửi; text qua escape/format hiện có.
- [x] TASK-004: Frontend room: gộp các row trao đổi bot↔bot liên tiếp
      (bus/result cùng cặp/trace) thành một mục thu gọn "A và B đã trao đổi
      N tin", bung tại chỗ xem từng dòng; tên class/CSS mới, không dùng
      tên legacy bị cấm.
- [x] TASK-005: Tests — backend (lọc theo cặp 2 chiều, count, thứ tự);
      frontend (DM grouping render, modal mở/đóng + nội dung, room
      collapse/expand); chạy full suite `uv run pytest -q`.

## Checks

- Focused: tests chạm serve/sessions API, chat frontend, team frontend.
- Full gate trước khi báo xong. Chưa kiểm chứng live 2 bot thật ở vòng này
  (dùng fixture/fake như test hiện có).

## Open questions

- Dữ liệu cũ: trao đổi đã ghi trước thay đổi này có suy được cặp bot đầy đủ
  không (phụ thuộc TASK-001) — ĐÃ TRẢ LỜI: suy được khi recipient đã reply
  trong cùng room (nối `parent_span_id` → bus `span_id`); bus row đơn lẻ
  không reply thì không suy được và bị loại khỏi endpoint. Dữ liệu latch
  cũ nên refresh: một turn bot_send mới sẽ ghi `recipient` tường minh.
