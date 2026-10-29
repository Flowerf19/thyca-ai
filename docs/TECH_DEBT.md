# Nợ kỹ thuật Thyca

Ghi nhận các nợ kỹ thuật đã biết, tác động, quyết định xử lý và trạng thái.
Mỗi mục có đủ bốn phần: **mô tả**, **tác động**, **quyết định/hướng xử lý**, **trạng thái**.

## 4. Store SQLite chưa được nối vào runtime (mới phát hiện)

- **Mô tả:** `AgentStore`/`AdminStore` mới chỉ được test trên fake root; chưa caller
  production nào truyền `actor` thật, chưa có lifecycle/bot-ID nào gọi
  `bootstrap_initial_dm`.
- **Tác động:** Bảo vệ ở lớp store chưa có hiệu lực với dữ liệu thật.
- **Quyết định/hướng xử lý:** Khi nối runtime, mọi đường `ChatApp`/CLI/memory writer
  phải lấy `AgentStore` (không bao giờ `AdminStore`), truyền đúng bot actor, và
  chặn legacy writer như plan hai-cửa đã nêu.
- **Trạng thái:** Mở.

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
- **Trạng thái:** Đã thay thế và gia cố xong (nhánh `pi/continue-20261029`).
  Store mới: `thyca/conversations/store.py` trên SQLite (`<root>/conversations.db`,
  WAL, `busy_timeout` 5s, `foreign_keys=ON`, STRICT tables, `user_version` schema).
  Tách `AgentStore` (chỉ `read`/`append` kèm `actor`, kiểm tra thành viên trong
  transaction, người ngoài/thành viên cũ nhận lỗi chung `unavailable`) và
  `AdminStore` (tạo DM/group, `set_metadata`, retention, compaction, `VACUUM`).
  Trigger chặn UPDATE/DELETE trên `messages`; root 0700 + check owner, DB 0600 +
  umask 077, từ chối symlink; giới hạn content/metadata, content `str` không
  NUL/surrogate, metadata giới hạn độ sâu JSON; `read` phân trang
  (`after_seq`, `limit`); mọi lỗi sqlite được bọc, không lộ exception thô.
  Đã xóa journal/guard/layout/document và test cũ; `tests/test_conversation_store.py`
  bao phủ quyền agent/admin, đọc/ghi chéo, thành viên cũ, init 16 process,
  quyền file/symlink, dữ liệu hỏng, rollback giữa chừng, retention/compaction.

## 2. DB hội thoại phình theo thời gian (append-only) — còn lại sau khi chuyển SQLite

- **Mô tả:** Agent chỉ được INSERT tin nhắn, không UPDATE/DELETE; DB vì vậy chỉ tăng.
- **Tác động:** DB lớn dần làm chậm query, tốn disk; không có cơ chế quên/tóm tắt
  dữ liệu cũ.
- **Quyết định/hướng xử lý:** Về sau cần retention job hệ thống (xóa theo chính sách
  tuổi/dung lượng, do system/admin chạy — không phải quyền agent), compaction
  (tóm tắt tin cũ thành bản ghi tóm tắt), và VACUUM định kỳ ngoài giờ cao điểm.
- **Trạng thái:** API đã có trong đợt gia cố SQLite này: `AdminStore.purge_before`
  (xóa theo tuổi, trigger được gỡ/tái tạo trong cùng transaction ngắn),
  `compact_through_seq` (hàm tóm tắt truyền vào chạy NGOÀI transaction — có test
  chứng minh write khác vẫn qua trong lúc summarize), `vacuum()`. Còn mở: giá trị
  chính sách retention cụ thể và lịch chạy định kỳ ngoài giờ cao điểm.

## 3. Transaction SQLite phải ngắn; cấm gọi LLM khi đang mở transaction

- **Mô tả:** `seq` tin nhắn được cấp trong transaction `BEGIN IMMEDIATE`
  (`MAX(seq)+1`), `busy_timeout` chỉ chịu được tranh chấp ngắn.
- **Tác động:** Giữ transaction mở trong lúc gọi LLM/tính toán lâu sẽ khóa writer
  khác, gây `database is locked` dây chuyền và làm mất tính độc lập giữa các hội thoại.
- **Quyết định/hướng xử lý:** Mọi transaction chỉ bao bọc đọc-ghi DB thuần túy;
  chuẩn bị payload (kể cả gọi LLM) xong mới mở transaction. Test song song phải
  bao phủ trường hợp này.
- **Trạng thái:** Đã thực thi trong code + test: mọi public API (trừ `compact`,
  summarizer chạy ngoài transaction có chủ đích) không nhận callback; storm ghi
  song song hoàn tất trong ngưỡng; `seq` cấp trong `BEGIN IMMEDIATE` ngắn.
  Còn mở: giữ quy ước này khi nối store vào runtime chính.
