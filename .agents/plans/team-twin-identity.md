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

## Bổ sung 2026-10-14 — trình bày sidebar (user duyệt theo mock tự đưa)

- Hàng bot có avatar slot cố định (hiện là chữ cái đầu trên nền tint;
  avatar thật/sinh sau này thay vào đúng ô), sao ✦ cho Thyca quản lý,
  xám khi tạm dừng; chọn = pill wash theo tint.
- Session là cụm con thụt dưới bot (guide dọc, chữ nhỏ, dòng phụ muted
  gồm giờ · lượt · tên thành viên); phiên chung = hai vòng tint chồng.
- Rail thu gọn desktop: chỉ avatar các bot, tooltip tên · vai trò,
  nhớ lựa chọn theo trình duyệt.
- Tạo bot không khai tint → gán tint ít dùng nhất trong bảng đội.
- Phòng thường: bot nhận delegate lần đầu được thêm vào participants
  (session thành team session); envelope message/help_request có trace
  cũng vào dòng gộp "Xem các bot đã làm gì".

## Bổ sung 2026-10-14 — thẻ hỏi/lỗi có cấu trúc + rà mockup (đã verify live)

- Tool baseline `ask_user` / `report_error`: bot hỏi user hoặc báo lỗi
  bằng thẻ có cấu trúc thay vì nhắn chữ. Backend `post_bot_card` ghi row
  `meta={bot_id, kind:"card", card, bot_name, bot_tint}` với gate y như DM
  (không phòng trống, không DM của bot khác, phải là participant).
  Contract `meta.card`: question `{type,title,subtitle?,options[2..5]}` /
  error `{type,progress[{label,done|stuck|todo}],choices[2..5],hint?}`.
- Verify live trên demo: Thyca gọi `ask_user` → thẻ hiện 3 chip; bấm
  "Cho phép" gửi lựa chọn, bot tiếp tục tạo `tro-ly-hoc-tap` (bash allow)
  đúng ý user. `report_error` → thẻ hiện 2 bước + hint + 2 nút.
- Kèm theo: `bot_name`/`bot_tint` ghi sẵn trên mọi row message/A2A/envelope
  (runtime `BotTurnResult` + `_bot_display_meta`) để transcript/Trace dùng
  đúng tint card thay vì hash fallback.
- `GET /api/bots` thêm `model/bash/skills/schedule` (drawer Nâng cao đọc);
  `GET /api/memory/candidates` thêm `title/text` preview (read-only,
  không chạm get/TTL).
- Rà mockup đã sửa + chụp duyệt: mention gọn (tên + vai trò, có bot mới),
  drawer neo phải, Cài đặt › Bot 2 cột ngay hàng, Ký ức chờ duyệt có tiêu
  đề + nội dung + "từ bot", Chi phí theo bot theo anatomy journal-entry
  với thanh tint. Full suite 1367 passed.

## Bổ sung 2026-10-14 — Agent Status trong Agent Card API (user chốt)

Avatar design cần trạng thái thật theo từng bot; live event cũ chỉ có
tool/skill/llm theo lượt và session `running` theo phòng, không có
`bot_id`. Thiết kế đã chốt và triển khai:

- Card tĩnh (`TWIN.md`) không lưu trạng thái động. `GET /api/bots`
  ghép thêm `agent_status` từ `AgentStatusTracker` in-memory
  (`thyca/bots/status.py`); field cũ `status: active|paused` giữ nguyên.
- Enum: `offline|idle|thinking|running|waiting|done|error`. Paused →
  offline; `llm.*` → thinking; tool/skill/A2A → running; `ask_user` →
  waiting; `report_error`/turn lỗi → error; turn xong → done.
- `done` latch 60 giây; waiting/error latch tới khi user nhắn lại
  trong đúng phòng hoặc bot bắt đầu lượt mới ở đó. Restart mất latch
  (trạng thái là live, không phải lịch sử).
- `TurnEvent` và thinking/content delta mang thêm `bot_id`
  (Stage đã có sẵn); tracker keyed theo span nên bot chạy lồng A2A
  hoặc song song nhiều phòng vẫn phân biệt được. Status chỉ chứa
  metadata vận hành — không prompt/content/tool args/results.

## Bổ sung 2026-10-14 — Avatar design v1.1 đã port (SVG)

- Agent Card thêm field tùy chọn `avatar` (star|hollow|single-flag|
  double-flag|dotted|fermata|plain); star chỉ dành cho Thyca. Bot tạo
  mới không khai avatar → persist variant ít dùng nhất
  (`suggest_avatar`, cặp đôi với `suggest_tint`); card cũ không có
  field → `effective_avatar` ổn định theo bot id (CRC32), không cần
  migrate file.
- Renderer `shared/js/bot-avatar.js` port geometry từ design file
  (7 variant, 7 state, selected giấy note, team 3 nốt + `+N`, badge
  error>waiting>done) nhưng màu luôn qua `--team-tint`/token chung —
  không copy hex của design. Trạng thái lấy từ `agent_status`.
- Đã gắn: sidebar bot + rail, hàng nhóm chat (team avatar), mention,
  drawer hồ sơ (48px), Cài đặt › Bot. Chưa gắn: transcript header,
  Trace, Dashboard chi phí.
