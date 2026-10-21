# Trace — nguyên mẫu redesign (tĩnh)

Static, directly-openable prototype for the Thyca Trace screens.
No build, no server, no fetch: open `index.html` straight from disk (`file://`).

## Mở thế nào (how to open)

Mở tệp `index.html` trong thư mục này bằng trình duyệt (double-click là đủ).
`prototype.css` dùng font/token thật từ `../shared/css/tokens.css` nên giữ nguyên
vị trí thư mục khi mở — chỉ cần Google Fonts có mạng, còn lại chạy offline.

## Các màn hình (views, hash routing)

| Hash | Màn hình |
|---|---|
| `#usage` | Sử dụng token: cột ngày xếp chồng Vào/Cache/Ra, 3 thẻ Input–Cache–Output, tổng + Token theo mô hình + Token theo bot |
| `#cost` | Chi phí: thẻ “Tổng chi phí $X (đã ghi nhận)” + cột ngày, ghi chú độ phủ giá (lượt chưa có giá, không tính $0), Chi phí theo mô hình/bot |
| `#sessions` | Trace theo phiên: dải tóm tắt, chip lọc theo bot (lọc thật phía client), thẻ từng phiên → bấm vào xem chi tiết |
| `#bots` | Trace theo bot: thẻ mỗi bot (xếp theo token giảm dần), thanh tỉ lệ Vào/Cache/Ra, danh sách phiên tham gia |
| `#session/<id>` | Chi tiết phiên: avatar nhóm/đơn, dải thành viên, thẻ từng lượt + `<details>` “Xem các bước & dữ liệu” (bước đánh số, Input/Output) |

## Avatar — nguồn

- Viên đá hổ phách đơn (`.mk-peb`): CSS chép **nguyên văn** từ
  `/home/flowerf/Downloads/thyca-bot-group-avatar.html` (“Mẫu 4”, dòng ~185–215;
  `.mk` cơ sở dòng ~89–92; keyframes `spin`/`glyph-glow` từ khối Mẫu 1).
  Khối `<svg><defs>` (6 glyph `#g-*` + gradient) trong `index.html` cũng chép
  nguyên văn theo byte (đã diff kiểm tra).
- Avatar nhóm: hình học “Chồng dấu” (`.gm`/`.gm-slot`/`.gm-coin` nguyên văn,
  vị trí stack 2/3/over + đồng xu `+N`), bên trong là các viên đá hổ phách;
  viên trên cùng = bot lead (nhiều token nhất ở lượt cuối).
- Màu/glyph 5 bot y hệt bảng `BOTS` của tệp tham khảo:
  thyca `#a65a42` quill · thyca-coder `#3e5a5c` code · designer `#2f5d46` brush ·
  writer `#b8863c` nib · research `#6b4a33` lens.
- Lưu ý: trong tệp tham khảo không có hàm JS/HTML nào sinh markup `.mk-peb`
  (JS chỉ sinh dấu sáp `.mk-wax`); cấu trúc pebble trong `prototype.js`
  (`peb-shadow` + `peb-float` > `peb-body` > `peb-core` + `glyph`) dựng theo đúng
  các selector của khối CSS `.mk-peb` chép về.
- Không sao chép ảnh nền base64 của tệp tham khảo; không dùng avatar `.bot-svg` của repo.

## Quy tắc đếm phiên (session-counting rule)

- `totalSessions = sessions.length` — mỗi phiên nhóm chỉ tính **một lần**.
- “Phiên tham gia” của mỗi bot = số phiên có bot đó; cộng các bot lại có thể
  vượt tổng số phiên — không bao giờ cộng dồn vào tổng.
- Token/cost/requests/lượt của mỗi bot chỉ tính từ các phần `botBreakdown`
  của bot đó; mẫu số tỉ trọng token = tổng token (= tổng mọi breakdown).
- Mọi con số hiển thị đều do `prototype.js` tính từ `data.js`
  (`computeStats()` chạy một lần); `data.js` tự suy tổng lượt từ breakdown
  nên tổng không thể lệch. Quy tắc giá: lượt chỉ “có giá” khi **mọi** phần
  breakdown đều có `costUsd` số; ngược lại lượt là `null` (chưa ghi nhận,
  không bao giờ $0).

## Dữ liệu mẫu (`data.js`)

8 phiên (3 DM: thyca/coder/writer; 5 nhóm 2–3–3–4–5 bot) trên 10 ngày qua,
26 lượt: 1 lượt lỗi (`s-release` Lượt 2, kèm thông báo), 1 lượt chưa có giá
(`s-dm-coder` Lượt 2). Mỗi lượt 3–6 bước (input/thinking/tool/output).
