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
- **Trạng thái:** Đã đóng (2026-11-06): store SQLite đã nối runtime qua
  `ChatApp` (khởi tạo/audit khi dựng app, fail-closed nếu còn legacy),
  actor lấy từ nguồn đáng tin cậy phía server, và toàn bộ đường ghi
  JSONL công khai cùng fixture scaffolding đã bị xóa. Bằng chứng:
  `thyca/conversations/store/` (`AdminStore`/`AgentStore`, schema v2, WAL),
  guard `require_no_legacy_sessions` trong
  `thyca/conversations/store/runtime.py` được `ChatApp` gọi lúc dựng app.
  Gate toàn cục xanh (xem mục 6).

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
- **Trạng thái:** Đã đóng (2026-11-06): SQLite tại `conversations.db` là nơi lưu
  hội thoại công khai duy nhất; `SessionStore`/`SessionManager` chỉ còn là
  lớp điều phối trên store (fail-closed khi root chưa khởi tạo, không còn
  nhánh đọc/ghi file JSONL công khai). Bằng chứng: route
  `/api/bots/<bot_id>/dm/...`, `/api/groups/<group_id>/...` và
  `/api/conversations` trong `thyca/serve/routes.py`; mọi
  `/api/sessions*` nhận `404` cùng file đó. `WorkspaceTimeline`
  (`bots/<bot>/workspace/timeline.jsonl`, mã ở `thyca/bots/timeline.py`)
  vẫn là execution history canonical riêng, không phải storage công khai.
  Grammar timestamp chỉ còn thuộc canonical
  (`thyca/bots/workspace_store.py`), grammar công khai chỉ còn
  `group-<hex32>`/bot-id.

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
- **Trạng thái:** Còn mở một phần (cập nhật 2026-11-06 cho `0.87.0.dev1`).
  Lịch sử: commit `162d3f4`; 118 test trọng tâm cũ pass nhưng 10 ca thử bổ
  sung thất bại. Một số ca thử cùng một lỗi với thứ tự race khác nhau.
  Sau hard-cut, cổng đầy đủ đạt **1999 passed, 5 skipped** (xem mục 6).
  Kết quả đó đóng các ca thuộc đường JSONL/guard cũ (mục 1) và nối store
  (mục 4). Nó không tự đóng an toàn retention/compaction: race xóa tin
  ngoài snapshot và cutoff không hợp lệ vẫn thuộc mục 2 và cần chính sách
  riêng. Các ca con còn lại (mất DB, cách ly đường dẫn, cursor/seq, thứ tự
  summary, metadata lồng sâu, khởi tạo lạnh, lộ lỗi transaction) chưa được
  xác minh lại trong đợt tài liệu này. Giữ mục này mở cho tới khi từng ca
  có test và review độc lập riêng.

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
- **Trạng thái:** Đã đóng cho lát cắt hard-cut (2026-11-06): full gate cô lập
  `env -i HOME=/tmp/thyca-rm-home PATH=$PATH .venv/bin/python -m pytest -q
  -p no:cacheprovider --basetemp=/tmp/pytest-rm-full3` đạt
  **1999 passed, 5 skipped** (skip Chrome/môi trường có sẵn). Hai ca
  concurrency/timestamp từng đỏ lẻ một lần khi chạy đầy tải
  (`test_bidirectional_a2a_conflict_neither_hangs_nor_double_runs`,
  `test_dm_origin_delivery_records_pair_without_leaking_history`) đều pass
  khi chạy lại riêng và trong lần full-gate xanh; ghi nhận là flaky theo
  thời gian, không phải lỗi storage.
  Ghi chú đợt `0.87.0.dev1`: cổng `ruff` đã cấu hình trong `pyproject.toml`
  nhưng chưa cài trong `.venv` nên chưa xác minh. Chưa chạy kiểm tra khói
  trên gốc thật (live scratch-root smoke). Chưa kiểm chứng chất lượng với
  nhà cung cấp thật. `~/.thyca` không được truy cập trong các đợt này.

## 7. Chưa có bên phát hành vòng đời/đặt lại ngoài ChatApp

- **Mô tả:** Việc khởi tạo và chặn gốc cũ hiện chỉ chạy trong luồng khởi
  động cục bộ của `ChatApp`. Chưa có issuer vòng đời/đặt lại (lifecycle/reset
  issuer) độc lập ở ngoài luồng đó.
- **Tác động:** Thao tác đặt lại hoặc phát hành vòng đời ở quy mô ngoài một
  lần khởi động app chưa có chủ sở hữu rõ ràng.
- **Quyết định/hướng xử lý:** Chốt chủ sở hữu và contract riêng trước khi mở
  thao tác này ra ngoài `ChatApp`. Không tự đặt lại trên dữ liệu thật khi
  chưa được phép rõ ràng.
- **Trạng thái:** Mở. Bằng chứng hiện tại: guard
  `require_no_legacy_sessions` trong `thyca/conversations/store/runtime.py`
  do `ChatApp` gọi. Chưa có issuer ngoài luồng đó.

## 8. Bốn class Session* chưa đổi tên

- **Mô tả:** Bốn class `Session`, `SessionManager`, `SessionStore`,
  `SessionCompactor` trong `thyca/conversations/runtime/` giữ nguyên tên ở
  bước hard-cut này.
- **Tác động:** Tên cũ dễ gây nhầm với khái niệm hội thoại công khai mới.
- **Quyết định/hướng xử lý:** Đổi tên ở một bước riêng về sau. Bước đó phải
  rà mọi call site và test liên quan.
- **Trạng thái:** Mở. Đây là việc hoãn có chủ đích, không phải lỗi của bản
  `0.87.0.dev1`.

## 9. Lá bộ nhớ còn dùng tên session_id

- **Mô tả:** Trường định danh ở lá bộ nhớ (memory leaf) vẫn mang tên
  `session_id`. Tên đúng theo miền là `memory_id`.
- **Tác động:** Tên trường lệch miền dễ gây nhầm khi đọc mã và dữ liệu.
- **Quyết định/hướng xử lý:** Đổi tên ở một bước riêng về sau. Bước đó phải
  xử lý cả đường đọc dữ liệu đã ghi.
- **Trạng thái:** Mở. Đây là việc hoãn có chủ đích, tách khỏi đợt hard-cut
  hội thoại.

## 10. Cách ly private chưa đủ: bash và prefix vô chủ (mở, slice 2 chưa sẵn sàng)

- **Mô tả:** Hai rủi ro độc lập, đều truy vết từ mã hiện tại.
  1. Private outsider vẫn có thể đọc bộ nhớ thô qua `bash` được phép hoặc
     thực thi tùy ý tương đương. Private invocation dựng
     `MemoryView(window=None, denied_origins=...)` tại
     `thyca/bots/runtime.py:458-462` nhưng vẫn giữ tool thường của card tại
     `:716` qua `filtered_tool_schemas`. `BotPolicy` tại
     `thyca/bots/policy.py:68-77` và `:117-123` chỉ chặn đường team-control.
     Handler shell tại `thyca/tools/builtin/bash.py` (`bash_spec`/handler)
     không kiểm tra `MemoryView` hay `PathGuard`. Vì vậy recipient ngoài
     với `tools=("bash",)` có thể chạy `cat <root>/memory/<day>.md` và đọc
     cùng các entry team/foreign mà regression `read` mới chặn. Bản sửa read
     guard hiện tại trong `thyca/tools/path_guard.py`
     (`deny_read`/`_group_sensitive` tôn trọng pinned denials) KHÔNG đóng
     đường `bash`.
  2. Prompt private vẫn tự nhận text daily-memory vô chủ. Private view có
     `window=None` nên `is_group` là false
     (`thyca/memories/visibility.py`). `scope.memory.refresh` cho turn
     private tại `thyca/bots/runtime.py:728-736` đi qua
     `thyca/memories/active.py:207` với
     `include_prefix=not (view is not None and view.is_group)`, nên private
     nhận `True`. Prefix bỏ qua kiểm tra ownership/origin; logic
     `split_today_by_session` trong `thyca/memories/active.py` đưa prefix
     vô chủ vào phần here, và khi file không có heading thì lấy toàn bộ
     file daily. Đây là input Markdown được hỗ trợ, theo
     `tests/test_memory_active.py:438-457`, không phải giả thuyết file hỏng.
     Private outsider vì vậy có thể nhận ghi chú tự do trước heading đầu
     mà người gửi không chọn.
- **Tác động:** Cách ly privacy của private invocation chưa đầy đủ; slice 2
  chưa sẵn sàng và chưa được duyệt.
- **Quyết định/hướng xử lý:** Người dùng yêu cầu KHÔNG sửa trong lát này;
  ghi nhận làm nợ. Hành vi hiện tại không được coi là an toàn đã chấp nhận,
  và chưa duyệt thiết kế cụ thể nào. Về sau cần boundary được review độc lập
  và test fail-first cho từng đường (bash/thực thi tùy ý; prefix có marker
  và file không heading), giữ hành vi DM/unbound bình thường và truy xuất
  own-entry đã cho phép qua tool lọc. Không dùng heuristic từ khóa lệnh để dựng
  boundary shell.
- **Trạng thái:** Mở (hoãn theo yêu cầu người dùng). Hai phát hiện trên là
  truy vết từ đường mã, chưa phải tái hiện đã thực thi; reviewer nêu rõ không
  thực thi được reproduction với công cụ sẵn có.
