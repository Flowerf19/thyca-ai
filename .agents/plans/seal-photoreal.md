---
status: in-progress-approved 2026-10-28 (user duyệt: hướng 3 + giữ nguyên biểu cảm; lead làm mẫu TASK-001 trước)
created: 2026-10-28
last_updated: 2026-10-28
---

# Con dấu sáp photoreal (giữ nguyên biểu cảm nhân vật)

## Goal

Nâng cấp lớp đáy con dấu của avatar lên chất sáp như ảnh chụp thật —
mép đổ sáp hữu cơ, bề mặt bóng ướt với specular gắt, khắc chìm sâu —
trong khi **toàn bộ lớp biểu cảm nhân vật ở trên giữ nguyên không đổi**:
đôi mắt/chớp mắt/ánh nhìn, mày, miệng, ghi chú nhạc, dấu hỏi, vành quỹ
đạo, mọi `data-state`/`data-idle`, parallax 2.5D, thở/nhún/lắc.

Ảnh tham chiếu do user cung cấp (2026-10-28): con dấu sáp ngoài đời —
(1) sáp đỏ ánh kim khắc hoa hồng trên phong bì, (2) phong bì kem + dấu
sáp đồng nhỏ, (3) dấu sáp đỏ sẫm mép chảy tự nhiên khắc phù hiệu,
(4) dấu sáp vàng-đồng buộc dây. Đặc điểm dùng làm chuẩn thị giác:
vệt sáng trắng hẹp và gắt trên bề mặt sáp, mép ngoài không tròn đều
mà lượn theo dòng chảy, họa tiết khắc có bóng đổ trong rãnh.

## Bối cảnh (điều tra 2026-10-28)

- Avatar hiện tại là SVG vẽ tay hai lớp trong
  `thyca/webui/ui/share/avatars/avatars.js`:
  - Lớp đáy (`waxMarkup`): thân sáp từ 3 path hữu cơ hardcode
    (`WAX_PATHS`: thyca/coder/researcher, bot khác chọn theo hash id),
    tô phẳng `var(--bot-color)` + 2 radial gradient tĩnh toàn cục
    `#waxShade`/`#waxHl` (khai báo ở `index.html`), cộng 3 vòng tròn
    `.wax-ring-hi`/`.wax-ring`/`.wax-notch` (vành khắc + vành quỹ đạo có
    animation theo trạng thái).
  - Lớp mặt (`sealFace`): một SVG riêng chồng lên trên — mắt, con
    ngươi, mí, mày, miệng, nốt nhạc, tia sao... điều khiển hoàn toàn
    bằng CSS qua `data-state`, `data-idle`, `data-expression`,
    `data-streaming` trong `avatars.css` (184 dòng) + JS
    (`setState`, `mentionBot`, `notifyText`, `orbitStyle`, parallax).
- Ranh giới hai lớp sạch: `avatar()` =
  `<span.seal-motion> { waxMarkup } { sealFace } </span>`. Thay lớp đáy
  không cần đụng vào mặt nếu giữ nguyên hợp đồng DOM mà mặt/trạng
  thái dựa vào: class `.seal-avatar.mk-wax`, `.wax-ring`,
  `.wax-ring-hi`, `.wax-notch` và các biến CSS `--bot-color`,
  `--bot-stamp`, `--bot-dark`, `--well-x/y`, `--hi-x/y`, `--par-x/y`,
  `--ring-start/end/delay`.
- Màu bot là **token, không phải màu cố định**: tint lấy tất định từ
  bot id qua `normalizeBot`/`botLookFor`, đổi màu được qua
  `localStorage thyca.tint.<id>` và color picker trong Expression
  Studio (`index.html` `#bot-color`, `setBotColor`). Avatar xuất hiện ở
  30px (header modal/nhóm), ~52px (sidebar), 42–46px (group stack),
  76–92px (expression tiles), 85px (info-lockup), 154px (stage-portrait).
- Ràng buộc đã duyệt (`.agents/AGENT_RULES.md` mục WebUI): tint/glyph
  tất định theo bot id, dùng nhất quán ở avatar + tên/header/sidebar;
  seal sidebar theo hình học envelope dùng chung; không thêm CSS
  global không scope.
- Verify giao diện theo AGENT_RULES: `uv run pytest -q
  tests/test_team_frontend.py tests/test_webui_theme.py
  tests/test_webui_format.py`; khi đụng streaming/replay cộng thêm bộ
  `test_webui_live_*`/`test_webui_stream.py`.

## Accepted decisions (user, 2026-10-28)

- Hướng **3 — chất sáp như ảnh chụp thật (photoreal-feel)** thay cho
  lớp đáy SVG tô phẳng hiện tại, **nhưng giữ nguyên mọi biểu cảm của
  bot** (điều kiện bắt buộc kèm theo, không phải tùy chọn).
- Quy trình làm việc: lead **tự tay làm TASK-001 làm mẫu** (test đỏ →
  sửa → test xanh → verify, commit riêng), sau đó mới giao các task
  còn lại cho subagent kèm brief chi tiết + commit mẫu + checklist
  nghiệm thu; lead review diff thật sau mỗi lần giao.

## Phản hồi từ agent khác (2026-10-28) và đánh giá của lead

Agent khác đề xuất chi tiết thêm (user chuyển để tham khảo). Lead đã
dối chiếu với code thật; phân loại như sau:

**Tiếp thu — đưa vào thiết kế lớp sáp:**
1. *Rãnh hào sâu + vành chỉ nổi*: chuyển `.wax-ring` từ nét kẻ đơn
   sang cấu trúc 3 lớp: rãnh lõm tối (vệt tối lệch xuống) → gờ chỉ
   mảnh bắt sáng gắt (`.wax-ring-hi` hiện tại chính là gờ này, đang
   dịch lên `-.7px` — sẽ tăng tương phản và tách rõ 3 tầng) → nền
   lòng dấu trung gian. Khớp với cặp ring sẵn có, không cần markup
   mới ngoài một lớp rãnh.
2. *Vòng hạt cườm nổi (beaded ring)*: thêm một vòng hạt quanh lòng
   dấu. Kỹ thuật rẻ nhất là `stroke-dasharray: 0.1 N` +
   `stroke-linecap: round` (đúng như gợi ý), nhưng phải chọn N theo
   chu vi vòng r=16.4–19.5 hiện tại và kiểm chứng riêng ở 30px/52px
   — ở cỡ nhỏ hạt dễ bệt thành vòng đứt nét mất nét; đây là điểm
   phải verify bằng mắt trong TASK-001, không mặc định đúng.
3. *Bóng tiếp xúc sát mặt giấy (contact shadow)*: thay
   `drop-shadow` một lớp hiện tại bằng bóng ngắn-đậm sát chân sáp
   (ưu tiên `filter: drop-shadow` hai lớp; tránh `<filter>
   feDropShadow` vì gradient/filter global theo id đã có tiền lệ
   `#waxShade` và filter trên nhiều instance tốn hiệu năng render
   sidebar).
4. *Mắt phù điêu (bas-relief)*: bổ sung cho đôi mắt hiện tại một
   **viền gờ sáng ở cạnh dưới** để mắt đọc như vết dập lún vào sáp —
   áp dụng bằng CSS/lớp phụ trên cấu trúc mắt hiện tại, giữ nguyên
   hình dạng học và mọi animation (chớp, liếc, mí).

**Không tiếp thu — xung đột với quyết định đã duyệt/kiến trúc:**
1. *Thay mắt bằng mắt "Grok"* (viên thuốc đen không lòng trắng,
   bỏ tròng/con ngươi): phá vỡ trực tiếp điều kiện bắt buộc "giữ
   nguyên biểu cảm" — idle `look/dream/sleepy/spark` và liếc theo
   `--gaze-x/y` cần con ngươi di chuyển trong hốc mắt (`idle.js`,
   `.seal-pupils`); mắt cười/nhắm/lim dim, mí, nháy mắt một bên đều
   gắn với cấu trúc mắt hiện tại. Bỏ con ngươi là thiết kế lại nhân
   vật, ngoài phạm vi hướng 3 đã chọn.
2. *Bảng biểu cảm mới* (mắt trăng khuyết khi writing, mắt chữ bát
   khi error...): hệ thống trạng thái hiện tại đã có đủ 11 trạng
   thái + 8 idle với mô tả trong `stateDetails` và test bao quanh;
   thay ngôn ngữ biểu cảm là việc khác, không thuộc plan này. Nếu
   muốn đổi thần thái từng trạng thái thì cần plan riêng và duyệt
   riêng từng trạng thái.
3. *Code mẫu SVG*: không dùng được trực tiếp — viewBox 100×100
   (thực tế 64×64), gradient/filter theo `id` cố định sẽ **trùng id
   khi render nhiều avatar** (code hiện tại cố tình namespaced bằng
   `uid`), tên biến màu `--bot-wax/--bot-deep` không tồn tại
   (`derivePalette` cho `color/dark/highlight/stamp/eye/pupil/...`),
   và cấu trúc class không khớp hợp đồng `.seal-avatar/.seal-motion`
   mà CSS/JS đang dựa vào. Chỉ lấy ý tưởng thị giác, viết lại theo
   cấu trúc hiện có.
4. *Nhận định "thyca sáp đồng cổ / march7th sáp tím nhũ"*: sai dữ
   kiện — tint thực tế: thyca=`accent` (nâu đỏ `#a65a42`),
   march7th=`botanical` (xanh lá `#6f7f57`), và user đổi được màu
   tùy ý. Lớp sáp phải **trung tính theo hue**: mọi hiệu ứng sáng/
   tối/nhũ suy ra từ palette của chính bot, không giả định họ màu.

## Đề xuất kỹ thuật (chờ bạn duyệt kèm plan)

Giữ kiến trúc 2 lớp; chỉ thay cách tạo lớp đáy bằng "sáp có
chất liệu" vẫn nằm trong SVG: mask theo path thân sáp + lớp texture
sáp vẽ một lần + multiply/overlay tint theo `--bot-color` để bot đổi
màu thì sáp đổi theo (không khóa cứng màu đỏ/vàng của ảnh). Lý do
không dùng PNG render theo nghĩa đen: mỗi bot một màu tùy ý (có cả
color picker), avatar render ở 6 cấp kích thước, và vành khắc phải
tham gia parallax/animation — 3 việc này chỉ khả thi nếu đáy vẫn là
SVG có lớp. Nếu giai đoạn thử nghiệm cho thấy cần texture raster,
dùng một ảnh sáp trung tính (grayscale) làm lớp multiply lên trên nền
`--bot-color`, thay vì ảnh màu hoàn chỉnh.

Chi tiết lớp sáp mới (trong `waxMarkup`/`avatars.css`, gồm các điểm
tiếp thu ở trên):
1. Thân sáp: giữ `WAX_PATHS` (hình đổ sáp hiện tại đã hữu cơ), thêm
   một path viền ngoài thứ hai hơi lệch để tạo mép chảy hai tầng
   (ý tưởng "scalloped rim"; không đổi bộ path theo bot).
2. Chất liệu: gradient đổ bóng theo hướng sáng trên–trái cố định
   (giống ảnh chụp), một vệt specular hẹp cong theo thân sáp
   (thay cho radial `waxHl` tròn đều hiện tại), bóng tiếp xúc
   ngắn-đậm ở chân sáp để sáp "ngồi" lên giấy, và ánh nhũ suy từ
   `--bot-highlight`/`--bot-stamp` của từng bot.
3. Vành khắc 3 tầng: rãnh hào tối (mới) → gờ chỉ bắt sáng
   (`.wax-ring-hi`) → lòng dấu trung gian; giữ nguyên vai trò emboss
   và parallax (`--well-x/y`, `--hi-x/y`). Thêm vòng hạt cườm nổi
   quanh lòng dấu (verify cỡ nhỏ). `.wax-notch` giữ nguyên (vành quỹ
   đạo/trạng thái) — không biến beaded ring thành vòng xoay trạng
   thái mới.
4. Mắt phù điêu: thêm viền gờ sáng cạnh dưới cho đôi mắt hiện tại;
   hình dạng, con ngươi, mí, chớp/liếc giữ nguyên 100%.
5. Không đổi: cấu trúc `sealFace` còn lại, mọi selector trạng thái
   trong `avatars.css`, JS trạng thái, kích thước ở từng vị trí hiển
   thị, `seal-brow` hiện tại (đã có sẵn vai trò chân mày).

Ngoài phạm vi: không thêm họa tiết khắc riêng cho từng bot (hướng 2
đã không chọn); không đổi palette/tint logic; không đổi layout
sidebar/modal; không thêm ảnh raster có watermark hoặc lấy từ ảnh
tham chiếu (ảnh chỉ là chuẩn thị giác); không thay ngôn ngữ biểu
cảm (mắt Grok/bảng trạng thái mới) — nếu muốn, làm plan riêng.

## Tasks

- [ ] TASK-001 (lead làm mẫu; prereq cho mọi task sau): Lớp sáp mới
      trong `waxMarkup`/`avatars.css` — mép chảy hai tầng, đổ bóng
      theo hướng sáng, specular cong, bóng tiếp xúc chân sáp, vành
      khắc 3 tầng (rãnh → gờ sáng → lòng dấu), vòng hạt cườm, viền
      gờ sáng cho mắt (phù điêu). Giữ nguyên hợp đồng DOM/biến CSS
      nêu trên và cấu trúc mặt. Viết test cấu trúc trước (markup chứa
      lớp mới, vẫn đủ `.wax-ring`/`.wax-notch`/mặt/con ngươi), chạy
      đỏ → xanh; tự verify bằng mắt ở 52px (sidebar), 30px (header)
      và 154px (stage-portrait qua Expression Studio) — vòng hạt
      cườm phải đọc được ở cỡ nhỏ, nếu không thì rút gọn/bỏ ở cỡ
      nhỏ bằng CSS theo kích thước. Commit riêng làm chuẩn cho
      subagent.
- [ ] TASK-002 (subagent, sau khi TASK-001 được duyệt mắt): Kiểm tra
      chéo mọi kích thước hiển thị (30/42–46/52/76–92/85/154px) và mọi
      trạng thái (`primaryStates` + `extraStates` + idle) qua
      Expression Studio; tinh chỉnh CSS trong `avatars.css` nếu chi
      tiết sáp lấn át mặt ở cỡ nhỏ. Chỉ chỉnh trong phạm vi lớp đáy.
- [ ] TASK-003 (subagent): Rà soát tương thích — `prefers-reduced-motion`,
      `motion-paused`, trạng thái `offline` (grayscale), `error`
      (vành đỏ) với lớp sáp mới; dọn gradient tĩnh toàn cục
      `#waxShade`/`#waxHl` khỏi `index.html` nếu lớp mới không còn
      dùng (chứng minh bằng grep không còn tham chiếu).
- [ ] TASK-004 (subagent, cuối): Chạy bộ verify theo AGENT_RULES +
      chụp kiểm chứng thực tế qua daemon (sidebar chat, modal trao
      đổi, Expression Studio), báo cáo kèm ảnh để user duyệt mắt.

## Vận hành subagent (chuẩn bắt buộc cho plan này và các giao việc sau)

- Lead làm mẫu TASK-001 trước; commit đó là "mẫu chuẩn" được dẫn
  trong mọi brief sau.
- Mỗi lần giao, brief phải kèm: mục tiêu, cwd/repo, phạm vi được sửa
  (liệt kê file), những gì TUYỆT ĐỐI không đụng, bằng chứng/quyết
  định liên quan, tiêu chí hoàn thành, lệnh kiểm chứng bắt buộc,
  điều kiện phải dừng-báo-lại thay vì tự quyết, và mẫu báo cáo
  (file đã đổi, kết quả test nguyên văn, trạng thái git).
- Một writer tại một thời điểm trên cùng working tree; lead không
  sửa file trong lúc subagent đang chạy.
- Sau mỗi lần giao, lead kiểm tra diff thật + chạy lại test liên quan;
  báo cáo của subagent một mình không được tính là nghiệm thu.
- Chỉ tiếp tục cùng một phiên subagent (`fusion-executor`) qua resume
  cho các vòng sửa, không mở writer mới song song.

## Checks

- Đơn vị/cấu trúc: `uv run pytest -q tests/test_team_frontend.py
  tests/test_webui_theme.py tests/test_webui_format.py` — xanh toàn
  bộ; test markup mới cho `waxMarkup` (lớp sáp mới có mặt, hợp đồng
  `.wax-ring`/`.wax-notch`/`.seal-face` còn nguyên, mỗi avatar chỉ
  một path thân như ghi chú hiện tại trong code).
- Luồng thật qua daemon (restart bằng code mới, như các vòng trước):
  sidebar 2 bot hiển thị đúng tint, đổi màu qua Expression Studio
  thì sáp đổi theo, stream một lượt để thấy trạng thái
  thinking/writing/working/done không vỡ (vành quỹ đạo, nốt nhạc,
  tia sao done), hover parallax còn chạy, reload giữa lượt vẫn
  theo được stream.
- Duyệt mắt bởi user ở hai cỡ: 52px sidebar và 154px stage-portrait
  so với 4 ảnh chuẩn. Test tự động xanh không thay cho bước này.
- `git diff --check` sạch; không đụng `~/.thyca` ngoài log/pid của
  daemon; file `finnaldesign.html` (đang deleted, không thuộc plan)
  giữ nguyên trạng, không commit kèm.

## Open questions

- Nếu làm xong TASK-001 trong SVG thuần mà mắt vẫn thấy chưa đủ
  "như ảnh thật", có cho phép bổ sung một lớp texture grayscale
  dạng ảnh tĩnh (tự tạo, không watermark) nhân multiply lên nền
  `--bot-color` không? — cần user duyệt sau khi xem bản mẫu
  TASK-001; không tự quyết trước.
- Vệt sáng "bóng ướt" nên cố định hướng trên–trái (như ảnh chụp)
  hay chạy theo parallax cùng `--hi-x/y` hiện tại? — đề xuất: cố
  định hướng nhưng vẫn trôi nhẹ theo parallax; chốt khi duyệt mắt
  TASK-001.
