# Có gì mới

Thay đổi của Thyca, viết ngắn gọn cho người dùng.

## 0.8.3 — 17/09/2026

- Ô soạn tin kiểu thư: chọn model (từ Cài đặt), mức suy nghĩ (Nhanh / Cân bằng / Kỹ hơn), nút Dừng khi Thyca đang trả lời, Thử lại không gửi lại câu của bạn.
- Chat hiện suy nghĩ thật của model lúc đang viết, không còn câu ambient giả; đồng hồ suy nghĩ tiếp tục đúng khi đổi qua lại giữa các phiên.

## 0.8.2 — 16/09/2026

- Ghi nhớ không còn mất mục khi chat và trang Memories sửa cùng lúc; tìm lại được sau khi mở lại Thyca hoặc sửa SOUL/USER/IDENTITY.
- Model khai `baseUrl` riêng thì gọi đúng máy chủ đó.
- CLI (`thyca -p` / REPL) dùng MCP server trong config, giống WebUI; tool MCP sai tên/schema bị bỏ qua và báo rõ server nào.
- `--continue` tự tạo phiên mới nếu chưa có, bỏ qua file phiên hỏng.
- Trang Cài đặt không còn lộ URL/API key khi kiểm tra provider lỗi.

## 0.8.1 — 11/09/2026

- Hồ sơ thành màn riêng trong mục lục: chọn giữa USER.md / SOUL.md / IDENTITY.md, nội dung hiện đúng markdown (bảng, code, trích dẫn), khung sửa rộng hơn hẳn.
- Chat không còn đứng hình khi bạn tải lại trang hay sang phiên khác giữa lúc Thyca đang trả lời: sidebar và nút phiên mới vẫn bấm được, phiên đang chạy tự hiện đúng và cập nhật tiếp khi xong.
- Dòng "Đã dùng" nối bằng dấu phẩy: bash x2, edit x1.
- Bớt hai dòng cảnh báo lặp của thư viện MCP trong log khởi động.
- Rời một phiên đang trả lời rồi quay lại thì phiên đó vẫn tiếp tục chạy đúng chỗ đang dở, không reset về một dòng trạng thái trống.

## 0.8.0 — 10/09/2026

- Khung soạn tin thành một pill kính mờ, bỏ khung trắng lồng hai lớp; mic và chữ theo mực nâu, nút gửi terra với icon trắng.
- Bỏ dòng chữ nhỏ dưới khung chat ("Đã nhận trả lời.", "Đang xử lý…"): trạng thái đã nằm ở dòng dưới cây bút.
- Một dòng "Đang dùng:" / "Đã dùng:" duy nhất cho cả skill lẫn tool. Lúc chạy liệt kê tên (bash, create-skill); xong thì kèm số lần (bash x2 + create-skill x1). Lượt không dùng gì thì không còn dòng "không dùng tool nào".
- Skill hiện đúng tên skill thay vì "read", cả lúc đang chạy lẫn khi mở lại phiên cũ. Trang Trace hiện tên skill kèm input JSON.
- Lượt lỗi: dòng "Đã dùng" chốt lại đúng phần đã chạy, và câu bạn vừa gõ được trả về ô nhập (không đè nếu bạn đã gõ câu mới).
- Enter khi ô trống không còn im lặng: khung rung nhẹ một nhịp. Máy bật giảm chuyển động thì chỉ có thông báo cho trình đọc màn hình.

## 0.7.9 — 05/09/2026

- Gỡ khuông nhạc (chat và Trace): không còn nốt, piano, font Bravura.

## 0.7.8 — 05/09/2026

- Chat lúc đang nghĩ không còn khuông nhạc: nền giấy ấm, một dòng ambient, một dòng trạng thái thật (đang dùng tool gì).

## 0.7.7 — 05/09/2026

- Khuông chat gọn hơn (không kéo ô trống); chạm khuông nghe cả đoạn, nốt hiện dần theo tiếng; chạm một nốt thì chỉ nốt đó kêu.

## 0.7.6 — 05/09/2026

- Dòng meter dưới khung chat thêm output và cửa sổ request cuối (ctx). Di chuột vào để xem số đầy đủ.
- Khuông nhạc hiện 4/4 ở mọi hàng; chạm khuông để nghe cả đoạn bằng piano.
- Khuông theo câu 8 ô (có V7); tool, skill và retry nghe khác nhau.
- Đổi tab Trace ↔ Chat giữa lượt đang chạy không còn làm mất nốt nhạc đang vẽ, không còn spam log lỗi pipe; lượt vẫn chạy tiếp bình thường.

## 0.7.5 — 05/09/2026

- Dưới khung chat có thêm dòng nhỏ: lượt vừa rồi tốn bao nhiêu token (input, cache) và bao nhiêu tiền. Di chuột vào để xem số đầy đủ.
- Web gọn lại: code giao diện (chat, cài đặt, memories, trace...) được chia thư mục riêng nên tải nhanh và ít lỗi vặt hơn. Bạn không cần làm gì, cứ dùng như cũ.

## 0.7.4 — 05/09/2026

- Đọc memory chính xác giờ hơn khi xem lại phiên cũ.

## 0.7.3 — 05/09/2026

- Tạo phiên chat mới không còn bị đứng khi lượt cũ đang chạy.
- Khi mạng chập chờn, bạn sẽ thấy dòng "Đang thử lại (1/3)..." thay vì báo lỗi ngay.

## 0.7.2 — 05/09/2026

- Nốt nhạc trong chat không còn biến mất khi bạn chuyển tab rồi quay lại.

## 0.7.1 — 03/09/2026

- Chat cuộn xuống cuối khi đổi phiên, có nút "xuống cuối".
- Đổi phiên nhanh không còn hiện nhầm nội dung phiên cũ.
- Trang Cài đặt: lưu provider ổn định hơn, danh sách model tự tải về, đổi model thì tự mở phiên mới.
- Khi mất mạng sẽ báo rõ "không tải được", thay vì trang trắng.

## 0.7.0 — 31/08/2026

- Trạng thái trong lúc chờ (đang dùng tool gì, đang mở skill nào) hiện gộp một dòng, không chớp tắt.
- Nốt nhạc mới có hiệu ứng "thở" khi máy đang nghĩ.
- Xem lại phiên cũ trong Trace khớp với lúc chat trực tiếp.

## 0.6.3 — 28/08/2026

- Có trang Cài đặt: nhập địa chỉ máy chủ AI + API key, chọn model, lưu một lần dùng mãi.

## 0.5.2 — 28/08/2026

- Khung chat hết tràn viền, thanh cuộn gọn hơn.

## 0.5.0 — 27/08/2026

- Thyca có Skills: khả năng mới được nạp từ file, không cần cài thêm tool.
- Cách nói chuyện mặc định gần gũi hơn ("thi ca").

## 0.4.0 — 27/08/2026

- Trang Trace: xem lại từng lượt chat (tốn bao nhiêu token, tiền, thời gian).
- Memories: gộp hồ sơ SOUL/USER/IDENTITY vào một tab "Hồ sơ", sửa trực tiếp trên web.
