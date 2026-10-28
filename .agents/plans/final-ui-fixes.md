# Final UI fixes (bundle duyệt từ review cuối)

## Decisions (user-approved)

- TASK A: idle pocket mở vẫn hiện "sợi" line vì `.paper-footer` giữ
  `border-top` dù heading/scroll đã ẩn. Sửa scoped CSS: state idle thì
  footer không viền trên; các state khác giữ separator.
- TASK B: star = ghim lên đầu. Thứ tự hiển thị: pinned trước, ổn định
  trong từng nhóm; không đổi thứ tự mảng `bots`. Click star/unstar dời
  node DOM hiện có (giữ identity), không rerender toàn bộ. Thyca hệ
  thống luôn pinned. Wording: ghim/bỏ ghim (toast "Đã ghim X lên đầu." /
  "Đã bỏ ghim X.", system "không bỏ ghim được"), giữ aria-pressed/disabled.
- TASK C: shape picker analog tint: localStorage `thyca.shape.<id>`,
  values `tall|squat|cloud` ("Giọt cao", "Giọt tròn", "Giọt mây").
  `normalizeBot` mang `shape`; `dropShapeFor` ưu tiên override hợp lệ;
  `setBotShape` validate + persist + patch tại chỗ (path `d` +
  `data-drop-shape`). Picker 3 preview trong Expression Studio ở cả hai
  dialog markup. Không đụng backend `BotCard.avatar`/API/dữ liệu lưu.
- TASK D: exchange entry làm rõ perspective: `data-exchange-owner` +
  giữ `data-exchange-partner`, bỏ self-partner, label
  `N tin nhắn với X` (X = partner theo `detail.dm`), pill sơn bằng
  palette của partner qua `--bot-*` + fallback neutral, giữ dashed.

## Tasks

- [x] TASK A — idle pocket stray line. CSS: `.bot-card[data-state=idle] .paper-footer{border-top:0}`. Test đỏ→xanh trong contract CSS sidebar. Render: `/tmp/final-ui-fixes/idle-pocket.png` (footer border 0px, heading none) — sạch sợi.
- [x] TASK B — star pins to top. `teamDisplayOrder()` (pinned trước, ổn định từng nhóm, không đổi mảng `bots`), `renderTeam()` dùng thứ tự này, click star dời node DOM hiện có (`pinCardOrder`, giữ identity) + wording ghim/bỏ ghim (toast/aria/title; system "không bỏ ghim được"). Test đỏ→xanh: `test_pinned_bots_render_first_stable`, `test_star_click_pins_card_to_top_in_place`; siết wording ở `test_system_bot_star_is_permanent`. Render: `sidebar-pinned.png` + toast "Đã ghim Coder lên đầu." / "Đã bỏ ghim Coder.".
- [x] TASK C — Custom bot shape picker. `normalizeBot.shape` ← `thyca.shape.<id>` (validate DROP_NAMES), `dropShapeFor` ưu tiên override, `ThycaShell.setBotShape` (validate/persist/patch tại chỗ path `d` + `data-drop-shape`), picker 3 preview trong cả 2 dialog markup + `studio.css` scope/wrap, dialog-note + Settings copy màu → màu+hình. Test đỏ→xanh (4 test shape + `id="shape-picker"` trong markup test). Render: `shape-picker.png`, `shape-picker-tall.png` (pressed flip, portrait avatar đổi shape, localStorage `tall`). Không đụng backend.
- [x] TASK D — exchange perspective + partner color. `renderExchangeGroups`: `data-exchange-owner`, giữ `data-exchange-partner`, bỏ self-partner, label/aria `N tin nhắn với X`, pill sơn palette partner (`setAttribute('style', paletteStyle)`); `chat.css` dùng `var(--bot-*)` + fallback neutral, giữ dashed. Test đỏ→xanh (label cũ fail + test swap reciprocal/datasets/palette + CSS var contract). Render: `exchange-thyca.png` (pill xanh Helper, "4 tin nhắn với Helper"), `exchange-helper.png` (reciprocal, không stale).
- [x] TASK E — contained focus ring (CSS-only, thêm từ steering giữa chừng). `.bot-card .bot-select:focus-visible` (radius 12px, outline `--bot-ink`, offset −2px); `.icon-button`/footer/reply (+2px, radius). Test đỏ→xanh trong contract CSS. Render: `focus-select-wide.png` (bug gốc: khung forest vuông tràn viền) → `focus-select-fixed-wide/narrow.png`, `focus-fav-fixed-narrow.png` (ring palette nằm trong card; narrow check với drawer mở vì sidebar `inert` khi đóng là đúng production).
- [x] TASK F — tách shape picker khỏi studio tabs (CSS-only). `.shape-row{margin:12px 0 18px}` (thêm margin-bottom 18px theo nhịp dialog; trước đó bottom row = top tabs = 263.1875, gap 0). Test đỏ→xanh trong `test_expression_dialog_shape_markup_consistent`. Render production: desktop gap `tabs.top − shapeRow.bottom = 18px`; wrap ở viewport 380px (picker cao 128px) gap vẫn 18px; outline selected không chạm tab row. Screenshots: `shape-tabs-desktop.png`, `shape-tabs-wrap380.png`.
- [x] Lead review/commit: reviewed the real diff and screenshots, reran the focused suite (126 passed), fixed the one stale full-gate expectation through the executor, then reran the full suite and actual-app browser checks before committing to `review/final-ui`.

## Evidence

- Baseline suite (trước sửa): 119 passed. Sau sửa: 126 passed (7 test mới), `git diff --check` sạch.
- Lead verification: full `uv run pytest -q` — 1799 passed. Actual app at `127.0.0.1:19502` in an isolated browser profile: pinned order and pin click, shape picker + persisted shape, reciprocal Thyca/March exchange labels and palettes, and contained `:focus-visible` ring all passed with no console/page errors. Screenshots: `/tmp/final-ui-fixes/lead-actual-*.png`.
- Lead TASK F verification: actual-app computed gap between `.shape-row` and `.studio-tabs` is 18px; `tests/test_team_frontend.py`, `tests/test_webui_markdown.py`, and `tests/test_webui_spa_frontend.py` — 99 passed.
- Lead full-gate catch (2026-10-28): full `uv run pytest -q` 1798 passed + 1 failed — `test_webui_markdown.py::test_shared_sidebar_renders_team_and_groups` còn assert chuỗi cũ `"bots.map(card)"`; production TASK B cố ý render `teamDisplayOrder().map(card)` (behavior đã phủ bởi test mới). Sửa expectation tĩnh → `teamDisplayOrder().map(card)` (không đụng production). Focused rerun: markdown 1 passed + team_frontend (pinned/star_click/sidebar) 4 passed, `git diff --check` sạch; full gate để lead rerun.
- Screenshots: `/tmp/final-ui-fixes/` (idle-pocket, sidebar-pinned, shape-picker[−tall], exchange-thyca/helper, focus-select-wide, focus-select-fixed-wide/narrow, focus-fav-fixed-narrow).

## Verification

- `uv run pytest -q tests/test_team_frontend.py tests/test_bot_exchanges_ui.py tests/test_webui_theme.py tests/test_webui_format.py tests/test_webui_live_rounds.py tests/test_webui_live_recovery.py tests/test_webui_stream.py tests/test_turn_status.py`
- `git diff --check`
