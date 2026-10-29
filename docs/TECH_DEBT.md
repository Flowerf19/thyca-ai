# Nợ kỹ thuật Thyca

Ghi nhận các nợ kỹ thuật đã biết, tác động, quyết định xử lý và trạng thái.
Mỗi mục có đủ bốn phần: **mô tả**, **tác động**, **quyết định/hướng xử lý**, **trạng thái**.

## 1. Lưu trữ hội thoại bằng journal file phẳng + lớp guard bù

- **Mô tả:** Mỗi hội thoại là một file `conversations/{dm,group}/<id>.jsonl`; dòng đầu
  là header JSON (`version`/`kind`/`id`/`metadata`) vừa chứa dữ liệu vừa dùng để
  chứng minh danh tính hội thoại. Xung quanh file phẳng là một lớp bảo vệ riêng:
  bản ghi guard `clean`/`active`/`poison`, registry `known.json`, lifecycle lock
  và health lock, cùng logic kiểm tra alias/symlink/inode (`st_nlink == 1`) mỗi lần
  mở một hội thoại.
- **Tác động:**
  - Header dòng đầu không giới hạn kích thước (chứa `title`/`metadata` tùy ý) nhưng
    lại được parse dưới khóa health EX toàn cục → một hội thoại làm nghẽn mọi hội
    thoại khác (finding 3 của review độc lập), chi phí tăng theo tổng dữ liệu.
  - Mỗi lần ghi/quét đều phải duyệt toàn bộ transcript đã biết dưới khóa chung;
    hội thoại này không tiến triển độc lập được với hội thoại khác.
  - Lớp guard phức tạp, dễ sai ở các đường crash/fsync (từng phát hiện: trạng thái
    "sạch" giả sau lỗi ghi, lỗi dùng chung bị quên, lỗi Bob chặn Alice, đóng nhầm
    descriptor) — độ phức tạp này chỉ tồn tại để bù cho file phẳng.
- **Quyết định/hướng xử lý:** Thay toàn bộ journal + header + kiểm tra alias/inode +
  `known.json` + guard + lifecycle lock bằng **SQLite** (một file
  `<root>/conversations.db`, WAL, append-only cho agent). Không migration dữ liệu
  cũ (data hiện tại được xác nhận không có giá trị).
- **Trạng thái:** Đã thay thế xong (nhánh `pi/continue-20261029`, chưa commit).
  Store mới: `thyca/conversations/store.py` trên SQLite (`<root>/conversations.db`,
  WAL, `busy_timeout` 5s, `foreign_keys=ON`, agent append-only, `seq` cấp trong
  `BEGIN IMMEDIATE`). Đã xóa: `thyca/conversations/guards.py`,
  `thyca/core/conversation_guard_tree.py`, `thyca/core/conversation_layout.py`,
  `thyca/core/conversation_document.py`, `tests/test_conversation_guards.py`,
  `tests/test_conversation_layout.py`; viết lại `tests/test_conversation_store.py`
  (đa process/thread, đọc-trong-ghi, append-only, tách DM/group).

## 2. DB hội thoại phình theo thời gian (append-only) — còn lại sau khi chuyển SQLite

- **Mô tả:** Agent chỉ được INSERT tin nhắn, không UPDATE/DELETE; DB vì vậy chỉ tăng.
- **Tác động:** DB lớn dần làm chậm query, tốn disk; không có cơ chế quên/tóm tắt
  dữ liệu cũ.
- **Quyết định/hướng xử lý:** Về sau cần retention job hệ thống (xóa theo chính sách
  tuổi/dung lượng, do system/admin chạy — không phải quyền agent), compaction
  (tóm tắt tin cũ thành bản ghi tóm tắt), và VACUUM định kỳ ngoài giờ cao điểm.
- **Trạng thái:** Mở — chưa thiết kế trong đợt SQLite này.

## 3. Transaction SQLite phải ngắn; cấm gọi LLM khi đang mở transaction

- **Mô tả:** `seq` tin nhắn được cấp trong transaction `BEGIN IMMEDIATE`
  (`MAX(seq)+1`), `busy_timeout` chỉ chịu được tranh chấp ngắn.
- **Tác động:** Giữ transaction mở trong lúc gọi LLM/tính toán lâu sẽ khóa writer
  khác, gây `database is locked` dây chuyền và làm mất tính độc lập giữa các hội thoại.
- **Quyết định/hướng xử lý:** Mọi transaction chỉ bao bọc đọc-ghi DB thuần túy;
  chuẩn bị payload (kể cả gọi LLM) xong mới mở transaction. Test song song phải
  bao phủ trường hợp này.
- **Trạng thái:** Mở — là quy ước bắt buộc cho mọi writer sau này (kể cả khi nối
  store vào runtime chính).
