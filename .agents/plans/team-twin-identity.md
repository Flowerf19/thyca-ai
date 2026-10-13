---
status: decided
created: 2026-10-13
last_updated: 2026-10-13
---

# Twin identity — sửa §2 của master plan team-v1

User chốt 2026-10-13 sau khi demo lộ lỗi: bot con có thân persona trống
thì runtime rơi về SOUL của Thyca và đồng thời vẫn nhận nguyên
`IDENTITY.md` của Thyca, nên bot tự xưng là Thyca và tự delegate cho
chính mình.

## Quyết định

- Card file đổi tên `BOT.md` → **`TWIN.md`** (mọi bot trong đội đều là
  twin của Thyca). Registry đọc cả hai tên (`TWIN.md` thắng); mọi lần ghi
  đều ghi `TWIN.md` và xoá bản `BOT.md` legacy nếu có.
- **Danh tính hai lớp** (thay ý "thân persona inject thay vai trò SOUL"
  ở master plan §2):
  - Lớp hồn chung: `SOUL.md` nạp cho mọi twin kể cả Thyca — giọng và
    chuẩn ứng xử chung; file này không xưng tên ai.
  - Lớp danh tính riêng: section `<identity>` của bot con sinh từ
    `TWIN.md` của chính nó (`thyca.bots.card.identity_text`); bot con
    không bao giờ nhận `IDENTITY.md` của Thyca. Thyca giữ identity thật.
  - Thân persona trong `TWIN.md` là lớp phủ riêng append sau hồn chung,
    thắng khi mâu thuẫn; trống cũng an toàn (không còn fallback).
- Bus chặn `sender == recipient` (tự gửi luôn là nhầm danh tính).

## Bổ sung cùng ngày — luật kênh riêng (user chốt)

"User nhắn riêng phải trả lời riêng. Bot cần bàn riêng với user thì
nhắn DM."

- DM cố định là kênh riêng tuyệt đối (user + bot chủ kênh): app không
  ghi bất kỳ row A2A nào vào DM của bot khác. Bot nhận delegate vẫn
  chạy trong session nội bộ, reply về làm tool result cho bot gửi —
  bot gửi tự báo cáo dưới tên mình. Result chỉ được ghi vào một DM khi
  bot nhận là chủ DM đó (việc lịch/độc lập báo về nhà).
- Tool baseline `message_user`: bot đăng tin cho user vào DM của chính
  nó (danh tính từ turn context, không có tham số bot_id). Đang ở ngay
  trong DM của mình thì bị từ chối kèm hướng dẫn trả lời bình thường,
  tránh đăng trùng.
