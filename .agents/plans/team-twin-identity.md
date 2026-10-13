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

Chưa thuộc gói này: luật ghi row A2A vào phòng gốc kể cả DM 1-1
(`chat_app._deliver_envelope`/`_record_bus_events`) — chờ user chốt
DM giữ 1-1 tuyệt đối hay phòng thường được promote thành team session.
