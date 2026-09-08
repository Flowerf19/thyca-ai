# Five-screen implementation contract

## Boundaries and ownership

- Work only in `/home/flowerf/Templates/thyca-css`. Static vanilla HTML/CSS/JS. No packages, API calls, auth, git init/commits, production repo changes, or new backend hook comments. Existing one comment stays in index.html.
- Each implementation agent owns ONLY `<screen>.html`, `<screen>.css`, `<screen>.js` for its assigned screen(s): cost, usage, settings, memories, trace. Three agents divide work: cost+usage; settings; memories+trace. Other files are read-only. Parent owns `styles.css`, `screens.css`, `navigation.js`, `index.html`, tests and docs.
- Read `styles.css`, `screens.css`, `index.html`, this contract, and thoughtful-coder skill before coding. Parent has already created the execution plan in `.agents/plans/remaining-screens.md`.
- User explicitly requires CSS REUSE and fidelity. Reuse existing tokens, `.notebook-shell`, `.sidebar`, `.sidebar-top`, `.menu-button`, `.workspace`, `.brand-header`, `.brand-sprig`, `.icon-button`. New common primitives already implemented in `screens.css`: `.screen-surface`, `.screen-heading`, `.screen-toolbar`, `.screen-card`, `.screen-note`, `.screen-muted`, `.screen-icon`, `.screen-badge`, `.screen-button` (+ `.is-primary`), `.screen-input`, `.screen-select`, `.screen-tabs`, `.screen-nav`, `.screen-sidebar-footer`, `.screen-dialog`.
- Do not recreate shell, palette, type scales, generic cards/buttons/inputs/dialogs in per-screen CSS. Scope page-specific CSS under e.g. `.cost-*`, `.usage-*`. Grid columns minmax(0,1fr). All colors/fonts use existing tokens; chart colors also available: --color-chart-input, --color-chart-cache, --color-accent, --color-success-ink, --font-mono. Extend missing tokens only in a page-local token block if genuinely needed.
- Verified `openai-codex/gpt-5.6-luna` supports images: READ your assigned reference image with the read tool. The parent has also read all images and supplies exact visual briefs below. Do NOT use shared Chrome MCP (agents would race). Parent handles browser QA. No external research necessary for sample model names/dates: they are frozen screenshot copy, not recommendations.

## HTML integration

Use full standalone HTML documents with lang=vi, meta viewport (copy index head font imports), stylesheets in order `styles.css`, `screens.css`, `<screen>.css`. No framework, templates, fetched fragments or inline CSS stylesheet blocks.

Structure:
```
<main class="notebook-shell screen-shell <screen>-shell" aria-label="...">
  <aside class="sidebar" aria-label="...">
    <header class="sidebar-top">
      <button class="icon-button menu-button" type="button" aria-label="Mở mục lục">[hamburger SVG from index]</button>
    </header>
    [page-specific sidebar with shared .screen-nav OR existing .sessions]
    [optional .screen-sidebar-footer]
  </aside>
  <section class="workspace" aria-label="...">
    [copy existing brand-header including two botanical SVGs verbatim from index.html]
    <section class="screen-surface <screen>-surface" aria-label="...">[page content]</section>
  </section>
</main>
<script src="./navigation.js" defer></script>
<script src="./<screen>.js" defer></script>
```
Do NOT load app.js on new pages; it is for chat only. Do not reproduce `paper-edge` or shell ::after: repaired book corners rely on native shell box-shadow.

Global navigation is parent-owned. Every `.menu-button` opens a shared native dialog from navigation.js, with routes: `index.html` (Trò chuyện), `memories.html` (Ký ức), `trace.html` (Trace), `usage.html` (Sử dụng), `cost.html` (Chi phí), `settings.html` (Cài đặt). Cross-links between these are real anchors. No href="#" placeholders. Unsupported tabs/links visible in screenshots must be clearly disabled buttons with title="Chưa có trong bản mẫu"; do not fabricate additional screens.

Desktop reference: 1536×1024; existing shell near x64,y39,width1416,height941, sidebar324. Main content begins under100px brand header, x411,y139. Preserve existing shell and warm cream/terracotta/brown. Serif reading copy, Fraunces brand/headings; restrained fine-line SVG icons. New surfaces should fill remaining height and scroll internally; never make content unreachable below a clipped fixed-height viewport. Mobile <=896: sidebar hides except menu; main fills viewport; card/controls stack. Check min-width, long words, focus, keyboard, reduced motion. Controls touch targets ~44px, native labels, dialog Escape/focus return. Sample data shown in image is allowed but clearly demo in subtle page note, not a huge warning banner.

## Cost brief (`cost`)

Reference `/tmp/pi-clipboard-05a386db-b165-4274-b159-7011f97b9414.png`.
Sidebar global nav: Trò chuyện, Lịch sử (link trace), Sổ tay (link memories), Chi phí active, Cài đặt. Bottom warm small Thyca Pro / Gói hiện tại card (not purchase CTA).
Surface top tabs: Tổng quan disabled, Chi phí active, Sử dụng link usage, Gói dịch vụ disabled. Heading Chi phí and subline Tổng · theo mô hình; right native period selector default 30 ngày qua, also 7 ngày qua (local fixtures).
Two-column row ~37% /63% with 24px gap. Left card Tổng chi phí, huge serif `178.240` with smaller `đ`; Từ 01/05 – 31/05/2024. Right card title Chi phí theo ngày with terracotta SVG line/soft area and round markers, baseline grid, y labels 0đ/5Kđ/10Kđ/15Kđ/20Kđ, dates 02/05 …31/05. Wavy rising line, last ~15K with spike ~14K around20/05. Use SVG no library; real dataset and labelled accessible chart.
Below Chi phí theo mô hình: one rounded list with 4 horizontal rows, icon tile left, name/subtitle, cost and percentage badge right. GPT-4o / Văn bản · Hình ảnh /102.480đ/57%; Claude 3.5 Sonnet / Văn bản /49.760đ/28%; Gemini 1.5 Pro / Văn bản /18.240đ/10%; DALL·E 3 / Hình ảnh /7.760đ/5%. Rows can native details expand sample breakdown instead of dead chevrons. Muted centered footer daily update, Đồng(VND), dữ liệu mẫu. Period selector updates visible period/total/chart/list together using coherent fixtures. Mobile chart full width, sums remain legible, rows wrap metadata, no cropped data.

## Usage brief (`usage`)

Reference `/tmp/pi-clipboard-cd06dc91-88ae-4342-bd83-f6d08d83b5df.png`.
Sidebar reuse existing chat `.sessions` list and active Viết lời bài hát; source other labels from index; do not duplicate app.js. Bottom Thùng rác may disabled mock. Top surface tabs Trò chuyện link index, Usage active, Cost link cost, Cài đặt link settings.
Heading Sử dụng token with line-chart/bar icon and subtitle Thống kê lượng token bạn đã sử dụng mỗi ngày trong tháng này; right selector Tháng 5, 2024 (also Tháng 4, 2024 fixture).
Large chart card ~340px high. Legend Input (tan), Cache read (pale peach), Output (terracotta) top-left. Token/Lượt segmented native buttons top-right with aria-pressed. SVG stacked daily bars across31days. Y-axis0,200K,400K,600K,800K,1.0M,1.2M. Bar peaks day3~0.9M,day14~0.83M,day21~0.94M,day24~1.13M; others~0.35–0.8M. Fit plot using SVG viewBox; mobile chart can use fewer date ticks, not unreadable full31 text.
Three equal summary cards underneath: Tổng Input `12.45M token`, `73.2% tổng sử dụng`, `14,562 lượt`; Tổng Cache read `3.21M`,18.9%,8,104 lượt; Tổng Output1.34M,7.9%,4,321 lượt. Bottom wide summary strip with botanical avatar, Tổng cộng16.99M token, right26,987 lượt /trong tháng5,2024. Screenshot rounding is allowed; dataset should match totals as closely as practical. Toggle Token/Lượt changes chart scale/accessible description, month select updates visible dates and fixtures. Clearly mock data. Mobile stack summary cards, preserve main chart hierarchy.

## General settings brief (`settings`)

Reference `/tmp/pi-clipboard-b855d1bd-0213-477e-8253-421ea05d7b40.png`.
Sidebar title Cài đặt, small botanical decoration; list Chung(active), Provider(disabled, no extra provider screen), Giao diện(disabled), Usage(link), Cost(link). Include standard menu-button for global navigation even though screenshot omits hamburger. Bottom card Thyca Premium / Gói hàng năm /Gia hạn:12/09/2025 (dữ liệu mẫu).
Main rounded surface title gear icon + Chung; subtitle Tùy chỉnh các thiết lập cơ bản cho trải nghiệm của bạn. Thin divider. Four spacious settings rows separated by rules. Left approx44% descriptive label/text, right control.
Row1 Mô hình ngôn ngữ /Chọn mô hình phù hợp với nhu cầu của bạn; native select Thyca Pro (GPT-4o), additional demo options from reference model family; recommendation badge Đề xuất plus Mạnh mẽ, hiểu sâu, phù hợp cho hầu hết tác vụ.
Row2 Nhiệt độ (Temperature) /Điều chỉnh mức độ sáng tạo của phản hồi. Native range min0 max2 step0.1 default0.7; terracotta track fill accurate to actual value (not the image's inconsistent midpoint); live numeric output and end labels0 Tập trung,2 Sáng tạo. Helper Giá trị thấp giúp câu trả lời chính xác, giá trị cao giúp câu trả lời đa dạng hơn.
Row3 Top P (Nucleus sampling) /Kiểm soát phạm vi từ ngữ được lựa chọn. range0..1 step0.1 default0.9, labels Chặt chẽ/Mở rộng; helper Giá trị cao cho phép mô hình xem xét nhiều khả năng hơn.
Row4 Tần suất hiện diện (Presence penalty) /Khuyến khích mô hình đề xuất ý tưởng mới. range-2..2 step0.1 default0, labels Giảm lặp lại/Khuyến khích mới.
No fake backend save: live preview values only, optional reset-to-defaults button and subtle settings only in this mock note. Mobile rows one column, ranges usable by keyboard and associated output.

## Memories brief (`memories`)

Reference `/tmp/pi-clipboard-533a1525-3207-4013-9441-7d7f0846d610.png`.
Sidebar: Chat(link index), Memories(active), Trace(link), Settings(link). Bottom Thùng rác (local removed-items view optional; don't implement backend).
Main surface top centered small title Ký ức của bạn with short rules. Search input Tìm trong ký ức… (~half width) left; terracotta + Thêm ký ức right. Filter chips Tất cả(active), Cảm xúc, Thói quen, Sự kiện, Bài học, Ý tưởng. Right sort select Mới nhất /Cũ nhất.
Four wide rounded cards (about140px each) vertically with 10px gap. Left circular line-icon medallion ~76px; center serif title ~21px + muted description + tags; right date and labelled overflow action button.
1 Viết giúp mình một đoạn điệp khúc; description Viết giúp mình một đoạn điệp khúc về bình yên và những ngày chậm lại.; tags Cảm xúc,Âm nhạc;20/05/2025; botanical icon.
2 Kế hoạch buổi sáng; Thức dậy 6:30, thiền 10 phút, viết nhật ký, đọc sách 20 phút.;Thói quen,Sức khỏe;18/05/2025;feather.
3 Ngày mưa và một tách trà; Mưa rơi nhẹ ngoài hiên. Trà nóng, nhạc Trịnh, lòng thấy an yên.;Cảm xúc,Nhật ký;15/05/2025;cup.
4 Ý tưởng cho dự án mới; Ứng dụng ghi lại cảm xúc mỗi ngày và gợi ý bài hát phù hợp.;Ý tưởng,Dự án;12/05/2025;bulb.
Implement combined search+category+sort, honest empty state. Add uses native labelled dialog: title, description, category; local in-memory only; safe textContent rendering. Overflow action can open same edit dialog, optionally local delete with undo; avoid complexity. No persistence needed, no HTML injection. Mobile date/actions reflow, medallion smaller, filter chips wrap (no horizontal overflow), add button and search stack.

## Trace brief (`trace`)

Reference `/tmp/pi-clipboard-0c695d72-360e-4ee2-85c6-1f6a2d7bf1d8.png`.
This screen differs structurally: full-width brand header across shell above both sidebar and content; hamburger top-left. Allowed override only in trace.css `.trace-shell` grid with shared header spanning both columns (brand centered); sidebar starts under header. Keep shared shell and corners, minmax grid. Can move copied brand header directly under main above aside/workspace, with `.menu-button` in header. Mobile timeline becomes compact top selector or horizontal buttons wrapping above detail; all5 turns reachable, no hidden controls.
Left timeline heading Timeline /5 lượt ·1.2s, vertical fine line and dots; active first terracotta rounded block. Turns:
1 LLM10:24:01 Nhận yêu cầu & đề xuất hướng đi /412tokens ·0.21s ·$0.00065
2 TOOL10:24:01 Tìm ý tưởng & hình ảnh /198tokens ·0.36s ·$0.00042
3 LLM10:24:02 Tổng hợp ý tưởng & viết nháp /685tokens ·0.28s ·$0.00103
4 TOOL10:24:03 Kiểm tra vần & nhịp /210tokens ·0.18s ·$0.00038
5 LLM10:24:04 Hoàn thiện điệp khúc /552tokens ·0.19s ·$0.00088
Below summary card Tổng quan phiên:2,057 tokens /1.2s /$0.00336. Bottom Xóa trace should either disabled labelled demo or local confirmation then reversible empty state, no destructive backend.
Main breadcrumb Trace >Viết giúp mình một đoạn điệp khúc về bình yên; right ID trace_7f3c9a2e copy button.
Expanded turn card header Lượt1 LLM10:24:01;412tokens ·0.21s ·$0.00065; green success badge and caret. Thông tin chung row5 fields: Mô hình gpt-4o-mini, Nhiệt độ0.7, Thời gian phản hồi0.21s, Trạng thái Thành công, Chi phí$0.00065.
3 equal token panels INPUT192 /INPUT CACHE128 /OUTPUT92, headings, description and monospace Vietnamese sample prompt, clickable Chi tiết token reveals sample breakdown. Below Tool calls panel planner.suggest_directions /Thành công /0.15s; two JSON pre blocks side-by-side Input {theme:bình yên,tone:nhẹ nhàng,sâu lắng,length:điệp khúc4dòng}, Output directions array of three calm lyrical ideas. Xem raw JSON opens native dialog/pre; Metadata native details collapsed.
Timeline selecting another turn must update displayed title/type/timestamp/metrics and appropriate sample payload, not just active highlight. ID copy handles Clipboard errors with honest fallback text. Use pre-wrap/overflow-wrap to keep JSON readable on mobile; token panels and tool JSON stack. No network calls.
