# Agent rules

## Release status — explicit user decision

- Thyca hiện là **development**, phiên bản `0.86.1.dev0` (số `0.86.1` do user xác nhận, không tự sửa thành `0.8.6.1`).
- Giữ hậu tố `.devN` theo chuẩn Python; không tự bỏ `dev`, chuyển sang beta/RC/stable, hoặc coi bản dev là release chính thức. Chỉ thay giai đoạn phát hành khi user yêu cầu rõ ràng.
- Khi đổi version, đồng bộ `pyproject.toml`, `thyca/__init__.py`, `uv.lock` và README.

- Chỉ làm task thuộc plan `in-progress` (hoặc bug/fix UI user vừa chỉ). Không thêm dependency, abstraction, hay feature ngoài task đó.
- Không còn plan in-progress (2026-09-22): `backend-solid-refactor.md` done (nhánh `refactor/backend-solid`), `webui-solid-refactor.md` done (nhánh `refactor/webui-solid`) — cả hai chờ quyết merge main. Plans cũ ở `plans/done/`.
- L2 hybrid thuộc v1. Đọc `.agents/decisions/2026-08-15-l2-hybrid-v1.md` trước khi đổi memory contract.
- Session là 4-class SOLID trong `thyca/sessions/`: `Session` / `SessionStore` / `SessionCompactor` / `SessionManager` (+ `wire.py` payload contract). Không `thyca/session.py` shim.
- ActiveMemory chỉ `thyca/memory/active.py`: `SOUL`/`USER`/`IDENTITY` full inject; daily tail `hotTailKB`. Archive/L2 là `archived.py` + `chunk.py`. Facade/`memory_*` thuộc Tools. Không `MEMORY.md`. `write`/`edit` không được ghi dưới `~/.thyca`; `memory_remember` là writer duy nhất cho memory files.
- Serve chỉ loopback. API không trả secret, path nội bộ, hay stack.
- Memory recalled từ another-brain là claim; tree hiện tại thắng.
- Code và identifier tiếng Anh. Nói với user theo ngôn ngữ user.
- Linux là target. Đừng viết API chỉ chạy trên Windows.
- Secret chỉ qua env hoặc file ngoài Git (`~/.thyca/auth.json`, mode 0600).
- Layout backend: `thyca/{agent,llm,config,memory,sessions,tools,skills,serve,app,core}/` — cấm file lẻ top-level (trừ `__init__.py`/`__main__.py`) và cấm shim. `core/` là leaf (stdlib-only, không import `thyca.*` khác). `serve/` không import runtime `app/` (chỉ `TYPE_CHECKING` + lazy).
- WebUI root-only native SPA: `thyca/webui/ui/chat/index.html` is the shared application entry at `/`; six known legacy workspace paths resolve to it, with the legacy Trace path normalized to Usage, and the router keeps page/filter/fragment state in browser history while the visible URL stays at `/`. Keep independent page factories/workspaces in `thyca/webui/ui/<page>/` and shared shell/router code in `thyca/webui/ui/share/`; do not introduce an iframe or a second application entry. Keep CSS page-scoped; shared global rules belong only in existing shared styles, and do not add unscoped global CSS.
- Pytest baseline 2026-09-22: **719 passed / 0 fail**. Fail `test_debug_prints_prompt_flags` cũ (`tools=7` vs `tools=13`) không tái hiện — nếu đỏ lại thì là regression, không tự sửa số.

## Accepted WebUI decisions (2026-10-14)

- Root-only SPA (2026-10-23): all six screens share one native application document at `/`, retain inactive workspaces (especially Chat streams and drafts), and route legacy page URLs into the matching workspace with supported query/hash state; legacy Trace detail renders within Usage. No iframe or page reload for in-app navigation.

- Preserve the notebook layout, existing fonts, and shared paper artwork. All shared shell and dialog backgrounds use `thyca/webui/images/thyca-bg-paper.png` with a 65% cream overlay.
- Shared background tokens: frame `#E8DAC3`, surface `#FBF4E5`, selected row `#F4E9D7`. Use shared tokens rather than page-specific neutral backgrounds.
- Bot appearance uses the shared `botLookFor` helper, selected deterministically from immutable bot id: the Vintage Ink tint and glyph stay stable across rename/reload, with no system/role/card-tint/avatar exceptions or backend mutation. The same tint is used for avatar, chat name/header/accent, sidebar, and other bot displays. Sidebar bot seals follow the shared 52px envelope geometry; group seals are 46×44px and compact avatar sizes remain unchanged. Idle bot rows omit the duplicate bot ID; meaningful live, private-wait, error, and paused status labels remain. Subordinate session rows have no avatar; the chat sidebar has no direct rename/delete UI, while existing session APIs remain available to internal callers. Bot-card management remains available through authorized main-bot tools. Narrow exception (user-approved 2026-10-23): the dedicated per-bot settings API — `GET/POST /api/bots/<id>/settings` via `registry.update_settings` (Profile page bot scope + `bot_settings` app ownership) — is the only sanctioned HTTP edit path for a bot's model/thinking besides the admin tools' `model`/`thinking` patch through the same registry write. It does not authorize other blanket bot PATCH/DELETE writes (those routes stay removed) or direct TWIN.md edits; `TWIN.md` stays model-free on canonical writes and is never migrated by reads. Expanded bot rows show a nonblank description in the lower paper regardless of session count; root Thyca remains protected from rename/deletion.
- The sidebar button owns selected styling. Do not reintroduce an SVG selected halo or dashed ring. Single-bot cores are scaled by 1.25; two-member teams have a dedicated centered, larger-head layout.
- Chat headers use the responding bot's identity and shared id-derived glyph/tint. Avatar, name, accent stripe, messages, and sidebar use the same tint; status badges retain semantic colors.
- Root assistant messages with `kind: "message"`, a trace ID, and no parent span are direct replies and stay visible. Nested traced message traffic may collapse into the inter-bot disclosure.
- Verify chat/avatar/theme changes with `uv run pytest -q tests/test_team_frontend.py tests/test_webui_theme.py tests/test_webui_format.py`. When streaming or replay changes, also run `tests/test_webui_live_rounds.py`, `tests/test_webui_live_recovery.py`, and `tests/test_webui_stream.py`.
- UI acceptance does not authorize a Git commit or push. Preserve unrelated working-tree changes.
