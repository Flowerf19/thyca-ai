# Agent rules (project-specific)

Shared workflow policies (Inverted Trust Pyramid, permissions, clean cutover, comment ban, missing business rules, skill routing, and Verification Artifacts) live in global agent rules. This file records only Thyca-specific decisions and constraints.

## Release status — explicit user decision

- Thyca hiện là **development**, phiên bản `0.88.0.dev3` (số `0.88.0` do user xác nhận, không tự sửa thành số khác).
- Giữ hậu tố `.devN` theo chuẩn Python; không tự bỏ `dev`, chuyển sang beta/RC/stable, hoặc coi bản dev là release chính thức. Chỉ thay giai đoạn phát hành khi user yêu cầu rõ ràng.
- Khi đổi version, đồng bộ `pyproject.toml`, `thyca/__init__.py`, `uv.lock`, `README.md`, và `.agents/` (được kiểm tra tự động bởi `scripts/checks/check-tier1.sh`).

## CHANGELOG — viết cho người dùng

`CHANGELOG.md` (“Có gì mới”) là ghi chú sản phẩm, không phải nhật ký kỹ thuật.

- Viết những gì người dùng thấy hoặc làm được khác đi: lỗi hết, hành vi mới, chỗ cần chú ý khi dùng.
- Ngắn, tiếng Việt dễ đọc. Một ý một bullet.
- Không viết kiểu dev: tên class/hàm/file/module, API nội bộ, biến môi trường, số test passed/skipped, `git diff --check`, commit hash, “kiểm chứng…”, hay câu “đây là bản dev / chưa phải beta” (trạng thái phát hành chỉ nằm ở rule này và README).
- Không nhét refactor thuần nội bộ trừ khi người dùng phải biết (ví dụ đổi lệnh, đổi cấu hình, mất tương thích).

## Project Architecture & Safety Constraints

- Mọi kế hoạch đã xong được lưu dưới `.agents/plans/done/`. Không đọc plan đã lưu như hướng dẫn hiện hành; nguồn chuẩn là `FEATURE_MAP.md`, `PROJECT_CONTEXT.md`, và mã hiện tại.
- L2 hybrid thuộc v1. Đọc `.agents/decisions/2026-08-15-l2-hybrid-v1.md` trước khi đổi memory contract.
- Conversation runtime là 4-class SOLID trong `thyca/conversations/runtime/`: `ConversationRuntime` / `ConversationStore` / `ConversationCompactor` / `ConversationManager` (+ `wire.py` payload contract). Không shim `thyca/session.py` hay alias `Session*`.
- ActiveMemory chỉ `thyca/memories/active.py`: `SOUL`/`USER`/`IDENTITY` full inject; daily tail `hotTailKB`. Archive/L2 là `archived.py` + `archive_store.py` + `chunk.py` (schema v10). Facade/`memory_*` thuộc Tools. Không `MEMORY.md`. `write`/`edit` không được ghi dưới `~/.thyca`; `memory_remember` là writer duy nhất cho memory files.
- Serve chỉ loopback (`127.0.0.1` / `::1` / `localhost`). API không trả secret, path nội bộ, hay stack trace.
- Không chạm `~/.thyca` thật khi chạy test hoặc root cô lập (`ensure_default(target)` ghi guide cạnh `target.parent`).
- Linux là target. Code và identifier tiếng Anh; giao tiếp theo ngôn ngữ user.
- Secret chỉ qua env hoặc file ngoài Git (`~/.thyca/auth.json`, mode `0600`).
- Layout backend (enforced by `scripts/checks/check-tier1.sh`): `thyca/{agents,app,bots,configs,conversations,core,llms,memories,platform,seeds,serve,skills,tools,webui}/` — cấm file lẻ top-level (trừ `__init__.py`/`__main__.py`) và cấm shim. `core/` và `platform/` là leaf (stdlib-only, không import `thyca.*` khác). `serve/` không import runtime `app/` ở mức module (chỉ `TYPE_CHECKING` + lazy).

## Tier 1 & Tier 2 Verification

- **Tier 1 (0 errors required):** `bash scripts/checks/check-tier1.sh` (gồm `ruff check .`, `node --check`, boundary + version sync, `scripts/checks/check-no-weakened-tests.sh`, `scripts/checks/check-comment-ban.sh`, `git diff --check`).
- **Tier 2:** chạy test liên quan (hoặc `uv run pytest -q` cho full gate) + kiểm chứng runtime thật, xuất bundle vào `.agents/artifacts/manifest.md` và kiểm tra bằng `bash scripts/checks/check-artifacts.sh`.
- Kiểm thử trình duyệt WebUI dùng helper chung `chromium_binary()` trong `tests/conftest.py` (`THYCA_CHROME`, `google-chrome`, `chromium`, `chromium-browser`).

## Accepted WebUI decisions

- Root-only native SPA: `thyca/webui/ui/chat/index.html` is the shared application entry at `/`; six known legacy workspace paths resolve to it, with legacy Trace normalized to Usage, and the router keeps page/filter/fragment state in browser history while the visible URL stays at `/`. Keep independent page factories/workspaces in `thyca/webui/ui/<page>/` and shared shell/router code in `thyca/webui/ui/share/`; do not introduce an iframe or a second application entry. Keep CSS page-scoped; shared global rules belong only in existing shared styles.
- Preserve the notebook layout, existing fonts, and shared paper artwork. All shared shell and dialog backgrounds use `thyca/webui/images/thyca-bg-paper.png` with a 65% cream overlay.
- Shared background tokens: frame `#E8DAC3`, surface `#FBF4E5`, selected row `#F4E9D7`. Use shared tokens rather than page-specific neutral backgrounds.
- Bot appearance is the circular character medallion rendered by `thyca/webui/ui/share/avatars/avatars.js` (shared `.seal-avatar` markup: SVG circle clip with frame rings and a per-state character rig). Tint comes from the card tint token mapped in `normalizeBot` (`thyca/webui/ui/share/data.js`), with a browser-only `thyca.tint.<id>` override; shape comes from `data-drop-shape`, with a browser-only `thyca.shape.<id>` override (`setBotShape` in `thyca/webui/ui/share/shell-app.js`). The same tint is used for avatar, chat name/header/accent, sidebar, and other bot displays. The sidebar lays bot cards in a 2-column tile grid (`thyca/webui/ui/share/sidebar.css`); group rows overlap small medallions in `.group-stack`. Idle bot rows omit the duplicate bot ID; meaningful live, private-wait, error, and paused status labels remain. Subordinate session rows have no avatar; the chat sidebar has no direct rename/delete UI, while existing session APIs remain available to internal callers. Bot-card management remains available through authorized main-bot tools. Narrow exception (user-approved 2026-10-23, UI hosted in shared Settings dialog): `GET/POST /api/bots/<id>/settings` via `registry.update_settings` (`#settings-dialog` “Bot của bạn” / `#settings-bot` + `bot_settings`) is the only sanctioned HTTP edit path for a bot's model/thinking besides admin tools; `TWIN.md` stays model-free on canonical writes and is never migrated by reads. Expanded bot rows show a nonblank description in the lower paper regardless of session count; root Thyca remains protected from rename/deletion.
- The sidebar button owns selected styling. Do not reintroduce an SVG selected halo or dashed ring.
- Chat headers use the responding bot's identity and shared id-derived glyph/tint. Avatar, name, accent stripe, messages, and sidebar use the same tint; status badges retain semantic colors.
- Root assistant messages with `kind: "message"`, a trace ID, and no parent span are direct replies and stay visible. Nested traced message traffic may collapse into the inter-bot disclosure.
- Verify chat/avatar/theme changes with `uv run pytest -q tests/test_team_frontend.py tests/test_webui_theme.py tests/test_webui_format.py`. When streaming or replay changes, also run `tests/test_webui_live_rounds.py`, `tests/test_webui_live_recovery.py`, and `tests/test_webui_stream.py`.
- UI acceptance does not authorize a Git commit or push. Preserve unrelated working-tree changes.
