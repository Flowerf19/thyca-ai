# Nợ kỹ thuật Thyca

Ghi nhận các nợ kỹ thuật đã biết, tác động, quyết định xử lý và trạng thái.
Mỗi mục có đủ bốn phần: **mô tả**, **tác động**, **quyết định/hướng xử lý**, **trạng thái**.

## 4. Store SQLite chưa được nối vào runtime (mới phát hiện)

- **Mô tả:** `AgentStore`/`AdminStore` mới chỉ được test trên fake root; chưa caller
  production nào truyền `actor` đáng tin cậy, chưa có lifecycle/bot-ID nào gọi
  `bootstrap_initial_dm`. Hai lớp dùng cùng `<root>/conversations.db`; việc tách
  lớp không tự chứng minh cách ly DB vật lý hoặc xác thực `actor`. `ChatApp` hiện
  vẫn dùng `SessionManager`.
- **Tác động:** Bảo vệ ở lớp SQLite chưa có hiệu lực với luồng ứng dụng hiện tại.
- **Quyết định/hướng xử lý:** Khi nối runtime, phải ràng buộc actor với nguồn đáng
  tin, phân quyền agent/admin và chuyển hoặc chặn các legacy writer theo plan
  hai-cửa. Không coi facade hoặc prompt là ranh giới xác thực.
- **Trạng thái:** Mở; bị chặn bởi các lỗi ở mục 5 và kiểm thử tích hợp.

## 1. Lưu trữ hội thoại bằng journal file phẳng + lớp guard bù

- **Mô tả:** Trong checkpoint file phẳng trước đây, mỗi hội thoại là một file
  `conversations/{dm,group}/<id>.jsonl`; dòng đầu
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
- **Trạng thái:** Đã thay checkpoint file phẳng bằng store SQLite thử nghiệm tại
  `thyca/conversations/store.py`, nhưng **chưa gia cố xong và chưa nối runtime**.
  Code có WAL, `AgentStore`/`AdminStore`, kiểm tra thành viên, trigger và phân
  trang. Các test hiện có chưa chứng minh đầy đủ an toàn đường dẫn, khởi tạo lạnh,
  tính bền vững hoặc xử lý lỗi; review và các ca giả ở mục 5 đã phát hiện lỗi.

## 2. DB hội thoại phình theo thời gian (append-only) — còn lại sau khi chuyển SQLite

- **Mô tả:** Agent chỉ được INSERT tin nhắn, không UPDATE/DELETE; DB vì vậy chỉ tăng.
- **Tác động:** DB lớn dần làm chậm query, tốn disk; không có cơ chế quên/tóm tắt
  dữ liệu cũ.
- **Quyết định/hướng xử lý:** Về sau cần retention job hệ thống (xóa theo chính sách
  tuổi/dung lượng, do system/admin chạy — không phải quyền agent), compaction
  (tóm tắt tin cũ thành bản ghi tóm tắt), và VACUUM định kỳ ngoài giờ cao điểm.
- **Trạng thái:** Có các API thử nghiệm `AdminStore.purge_before`,
  `compact_through_seq` và `vacuum()`, nhưng retention/compaction **chưa an toàn**:
  có thể xóa tin ngoài snapshot hoặc xóa dữ liệu với cutoff không hợp lệ (mục 5).
  Chưa có API dọn theo dung lượng; chính sách retention cụ thể và lịch chạy định kỳ
  còn mở. Hàm tóm tắt được gọi ngoài transaction, nhưng điều đó không chứng minh
  bước ghi kết quả an toàn khi có thay đổi đồng thời.

## 3. Transaction SQLite phải ngắn; cấm gọi LLM khi đang mở transaction

- **Mô tả:** `seq` tin nhắn được cấp trong transaction `BEGIN IMMEDIATE`
  (`MAX(seq)+1`), `busy_timeout` chỉ chịu được tranh chấp ngắn.
- **Tác động:** Giữ transaction mở trong lúc gọi LLM/tính toán lâu sẽ khóa writer
  khác, gây `database is locked` dây chuyền và làm mất tính độc lập giữa các hội thoại.
- **Quyết định/hướng xử lý:** Mọi transaction chỉ bao bọc đọc-ghi DB thuần túy;
  chuẩn bị payload (kể cả gọi LLM) xong mới mở transaction. Test song song phải
  bao phủ trường hợp này.
- **Trạng thái:** Đã kiểm thử hàm tóm tắt chạy ngoài transaction và một đợt ghi
  đồng thời hoàn tất; **chưa chứng minh mọi transaction đều ngắn**. Chẳng hạn,
  `set_metadata` đọc và giải mã toàn bộ tin trong transaction, còn `purge_before`
  có thể xóa nhiều dòng trong một transaction. Test storm đo tổng thời gian, không
  đo trực tiếp thời gian giữ khóa. Cần kiểm tra theo tải và giữ quy ước này khi
  nối runtime; chưa có ngưỡng thời gian nghiệp vụ nào được chốt.

## 5. Security và an toàn dữ liệu SQLite chưa đạt

- **Mô tả:** Review độc lập `eccd362a` đối với `b433ec2..162d3f4` yêu cầu sửa;
  các ca sau đã được kiểm tra trên root giả, không phải dữ liệu production:
  1. **Nghiêm trọng — compaction mất tin:** khi summarizer đang chạy, tin mới
     được ghi vào vùng `through_seq` có thể bị xóa dù không nằm trong snapshot
     đã tóm tắt; cũng tái hiện với retention xen giữa.
  2. **Nghiêm trọng — mất DB bị coi là mới:** đọc một DB đã mất tự tạo DB rỗng;
     sau đó `bootstrap_initial_dm` có thể tạo lại bot ID cũ mà không chứng minh
     bot mới. Cần phân biệt khởi tạo được ủy quyền với state đã mất/hỏng.
  3. **Nghiêm trọng — cách ly đường dẫn:** symlink ở thư mục cha và hardlink DB
     sang root khác được chấp nhận. Việc kiểm tra rồi mở lại đường dẫn cũng có
     rủi ro bị thay thế; race đó chưa được chứng minh bằng test.
  4. **Cursor/seq:** sau khi retention xóa hết dòng, `seq` có thể dùng lại; đọc
     `after_seq` cũ bỏ sót tin mới. Kết quả đọc cũng không trả seq/cursor để
     người gọi tiếp tục tin cậy sau khoảng trống.
  5. **Thứ tự summary:** summary của tin cũ hiện được thêm sau các tin mới hơn.
     Cần chốt contract thứ tự hiển thị/context và kiểm thử, không tự đoán rule.
  6. **Metadata tin:** metadata lồng sâu có thể được chấp nhận khi ghi nhưng bị
     từ chối khi đọc, khiến hội thoại báo corrupt.
  7. **Khởi tạo lạnh:** khi 16 process cùng tạo root chưa tồn tại, một số gặp
     `FileExistsError`. Test 16 process hiện có tạo root/schema trước nên chưa
     phủ đúng ca này.
  8. **Lỗi transaction:** giữ khóa ghi quá `busy_timeout` có thể làm lộ
     `sqlite3.OperationalError` thay vì `ConversationStoreError`; cần kiểm thử
     thêm lỗi lúc commit và rollback.
  9. **Retention cutoff:** `purge_before('z')` được chấp nhận và có thể xóa toàn
     bộ tin thay vì từ chối đầu vào không phải timestamp hợp lệ.
- **Tác động:** Có nguy cơ mất hoặc bỏ sót tin, vượt qua kỳ vọng cách ly root và
  làm lộ lỗi nội bộ nếu store được tích hợp. Các phát hiện này thuộc prototype,
  không chứng minh runtime session cũ đang dùng SQLite.
- **Quyết định/hướng xử lý:** Sửa và kiểm thử lại từng ca, gồm race giữa append,
  retention và compaction, khởi tạo/corruption/restart, quyền truy cập và alias;
  sau đó review độc lập lại trước khi coi store sẵn sàng tích hợp hoặc merge.
  Thiết kế bootstrap authority, cursor và thứ tự summary phải giữ các quyết định
  identity/data-safety đã chấp nhận; lựa chọn còn thiếu cần được chốt riêng.
- **Trạng thái:** Mở. Commit `162d3f4`; 118 test trọng tâm cũ pass nhưng 10 ca
  thử bổ sung thất bại. Một số ca thử cùng một lỗi với thứ tự race khác nhau.

## 6. Các kiểm thử còn đỏ ngoài SQLite

- **Mô tả:** Chạy toàn bộ `tests/` tại `162d3f4` trong network namespace và `/tmp`
  riêng: **1.915 passed, 3 failed, 8 errors**. Hai test
  `tests/test_webui_concurrent_streams.py` về refollow/replay thất bại; tám test
  `tests/test_turn_status.py` lỗi fixture Node vì truy cập `b.feed` không tồn tại.
  Các lỗi này cũng tái hiện ở commit trước `b433ec2`. Test quyền file
  `tests/test_private_followups.py::test_write_permission_failure_propagates` thất
  bại khi namespace chạy UID root, nhưng pass khi chạy lại bằng user thường.
- **Tác động:** Full suite chưa xanh; kết quả lỗi quyền trong namespace không đủ
  bằng chứng kết luận có lỗi quyền ở runtime thường. Test Chrome chat trước đó
  dùng luồng session cũ nên không chứng minh tích hợp SQLite.
- **Quyết định/hướng xử lý:** Điều tra hai lỗi refollow/replay và fixture Node;
  giữ test quyền dưới user thường hoặc môi trường cách ly tương đương không có
  đặc quyền root. Chạy lại full suite sau khi sửa, không làm yếu assertion.
- **Trạng thái:** Mở; không thuộc thay đổi code của commit SQLite `162d3f4`.
