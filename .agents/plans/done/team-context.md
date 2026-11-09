---
status: draft
created: 2026-10-13
last_updated: 2026-10-13
---

# Team context/token — cơ chế quản lý ngữ cảnh cho đội bot

User chốt 2026-10-13 (sau master plan `team-v1.md`). Áp trên `integration/team-v1`.
Nguyên tắc: không chế thuật toán token mới; đau đầu nằm ở kỷ luật dữ liệu chảy
giữa các bot. Ngân sách từng context giữ nguyên cơ chế cũ (compact trước lượt,
shrink 80%, backstop 95%, hiệu chuẩn theo `prompt_tokens` từng model).

## Lớp 1 — Ngân sách từng context (giữ nguyên, chỉ kiểm chứng)
Mỗi instance AgentLoop (Thyca/bot, cửa sổ chính/phòng) tự compact/shrink theo
session của nó. Việc cần làm: test khẳng định bot instance dùng đúng
`context_tokens` từ card/config và overhead (hot + tools) được tính đủ.

## Lớp 2 — Prompt chỉ mang mục lục (BE)
- Danh bạ đội inject vào prompt bot: chỉ `name + role` (bỏ description dài).
- Index skills: giữ dạng tên + mô tả ngắn sẵn có; khẳng định không nạp thân skill.
- Snapshot mem: giữ đuôi nóng theo `hotTailKB` như hiện tại, đếm trong hot tokens.
- View việc đang mở của bot (xem Lớp 4) cũng chỉ là mục lục: việc · trạng thái · phòng.

## Lớp 3 — A2A truyền con trỏ, không truyền nội dung (BE)
- Envelope bus thêm `refs` (đường dẫn file, entry id mem, span_id) và giới hạn
  phần thân text: mặc định kỹ thuật excerpt tối đa 1000 ký tự; nội dung đầy đủ
  lưu ở artifact store của bus theo `span_id` (bounded retention như gateway).
- Bot nhận muốn xem đủ thì gọi tool lấy artifact theo `span_id`
  (mở rộng `bot_send`/thêm `bot_fetch` — lane chọn, ghi vào plan thi công).
- Nội dung lớn qua file/mem thì chỉ gửi refs, bot nhận tự `read`/`memory_get`.

## Lớp 4 — Compact có xả mem + view việc (BE)
- Trước khi một session của bot bị compact (pre-turn hoặc idle): job mem chắt
  tri thức vào kho trước (thứ tự đã chốt: trích trước, compact sau), dùng lại
  hằng số sẵn có (`TRIGGER_RATIO`/`BACKSTOP_RATIO`), không đặt ngưỡng mới.
- Sau compact, lượt đầu được bơm lại hit mem liên quan tới việc hiện tại
  (query = nội dung việc/tin hiện tại, reader = chính bot, giới hạn trong
  ngân sách nóng).
- **Task = view suy ra từ log sự kiện** (không kho mới): hàm thuần quét
  transcript nội bộ của bot + phong bì bus đang chờ → việc đang mở
  (mở = có delegate/task_open mà chưa result/lỗi; trạng thái = sự kiện gần nhất;
  chờ ai = phong bì chưa được trả lời). View này nạp vào prompt dạng mục lục và
  là cùng nguồn watchdog/Thyca đọc. Việc phát sinh trong chat thường mà kéo dài
  qua lượt thì bot mở bằng tool `task_open` (ghi sự kiện, không sửa sổ tay).

## Lớp 5 — Cửa sổ chính mỗi bot + kinh tế đội (BE+FE)
- Mỗi bot có **một session chính (DM) cố định** với user: tạo lười, participants
  = [bot], mở bot là vào thẳng đó (FE). Kết quả việc theo lịch/việc độc lập của
  bot được app ghi vào DM này mang tên bot; việc thuộc phòng nào thì nhắn vào
  phòng đó (không tạo session mới cho việc thuộc phòng).
- Phòng chung giữ session theo phòng như hiện tại; session nội bộ (bot × phòng)
  không đổi.
- Model/budget theo card như cũ; chưa đặt trần chi tiêu cứng (chỉ đo qua Dashboard).

## Việc theo lane
BE: CBE-1 kiểm chứng ngân sách bot instance; CBE-2 danh bạ gọn + view việc vào
prompt; CBE-3 envelope refs/excerpt + artifact store + fetch; CBE-4 flush-trước-
compact + bơm lại sau compact + hàm task-view; CBE-5 DM session cố định +
kết quả lịch/độc lập vào DM.
FE: CFE-1 bấm bot vào thẳng cửa sổ chính (session chính ghim đầu cấp 2, các
session khác thành mục phụ); CFE-2 dấu chưa đọc cho row bot mới (lưu local),
tổng chưa đọc ở dòng bot; CFE-3 giữ nguyên tạo phiên chung.

Quy trình như master plan §8: đọc plan → plan thi công của lane → code →
review độc lập (Wonder 1.0) → test. Không đụng `pricing.py`/`test_llm_pricing.py`.
