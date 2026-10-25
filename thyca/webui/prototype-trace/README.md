# Thyca Trace — nguyên mẫu tĩnh

Mở `index.html` trực tiếp (`file://`) hoặc xem qua static server. Không có build,
API, fetch, hay truy cập dữ liệu người dùng. Giữ nguyên thư mục để các tệp tương
đối và `./tokens.css` hoạt động.

## Điều hướng

- Sidebar giống các trang Thyca: mục **Tổng quan**, sau đó **Đội** với năm bot
  (avatar hổ phách, tên và vai trò). Không có mục điều hướng riêng cho chat nhóm.
- Trong từng phạm vi, ba liên kết nội dung là **Sử dụng token**, **Chi phí**,
  **Trace**. Chọn bot sẽ giữ nội dung đang xem. Ở màn hình nhỏ, roster nằm trong
  `<details>` “Chọn bot” để không đẩy nội dung xuống dưới.
- `#overview?view=usage|cost|trace` xem toàn đội; `#bot/<botId>?view=usage|cost|trace`
  xem bot trong cả chat riêng lẫn chat nhóm.
- Bấm một lượt trong Trace để tới `#overview/turn/<sessionId>/<turnIndex>` hoặc
  `#bot/<botId>/turn/<sessionId>/<turnIndex>`. Trong chi tiết lượt, “Xem cả phiên”
  mở `#overview/session/<sessionId>` hoặc `#bot/<botId>/session/<sessionId>`.
  Chi tiết lượt ghi rõ số liệu đóng góp của bot (nếu đang lọc bot), còn steps và
  Input/Output được gắn nhãn ngữ cảnh toàn bộ lượt/phiên — nội dung không được
  giả định là của riêng bot đang chọn.
- Legacy `#usage`, `#cost`, `#sessions`, `#bots`, `#session/<id>` vẫn được ánh xạ
  sang các màn hình tương ứng trong nguyên mẫu.

## Quy tắc số liệu và phạm vi

- Tổng quan đếm phiên duy nhất: một phiên nhóm = một phiên. `phiên tham gia` là
  số phiên có bot đó, và tổng participation của các bot không được cộng thành
  tổng số phiên.
- Khi chọn bot, chỉ cộng phần `botBreakdown` của bot đó; các contribution trong
  chat riêng và chat nhóm đều có mặt. Phiên có bot trong danh sách thành viên
  nhưng không có lượt đóng góp vẫn được tính là `phiên tham gia`, không tạo lượt
  hay token giả.
- Cache là tập con của Input. “Vào” trong chart là Input trừ Cache.
- Chi phí đã ghi nhận giữ phần giá biết được; `null` vẫn là chưa biết, không phải
  $0. Lượt chỉ được tính vào giá trung bình khi thành công và có đủ giá.
- `scope.js` tạo các projection không làm thay đổi mock data. `scope.test.js`
  kiểm tra số liệu tổng, attribution theo bot, phí null/zero/partial và trường
  hợp bot có tham gia nhưng không có contribution.

## Avatar

CSS `.mk`/`.mk-peb` (Mẫu 4 · Viên đá hổ phách), `.gm`/`.gm-slot`/`.gm-coin`
(hình học “Chồng dấu”), glyph defs và màu/glyph bot giữ nguyên từ
`/home/flowerf/Downloads/thyca-bot-group-avatar.html`. Không dùng avatar mới của
SPA, không sao chép nền base64. Dấu hổ phách và cấu trúc markup không đổi trong
bản đơn giản hóa này.
