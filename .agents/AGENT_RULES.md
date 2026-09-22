# Agent rules

- Chỉ làm task thuộc plan `in-progress` (hoặc bug/fix UI user vừa chỉ). Không thêm dependency, abstraction, hay feature ngoài task đó.
- Plan đang chạy duy nhất: `backend-solid-refactor.md` (nhánh `refactor/backend-solid`, 8 module teams). Plans cũ đã đóng hết vào `plans/done/` (2026-09-22).
- L2 hybrid thuộc v1. Đọc `.agents/decisions/2026-08-15-l2-hybrid-v1.md` trước khi đổi memory contract.
- Session là 4-class SOLID trong `thyca/sessions/`: `Session` / `SessionStore` / `SessionCompactor` / `SessionManager` (+ `wire.py` payload contract). Không `thyca/session.py` shim.
- ActiveMemory chỉ `thyca/memory/active.py`: `SOUL`/`USER`/`IDENTITY` full inject; daily tail `hotTailKB`. Archive/L2 là `archived.py` + `chunk.py`. Facade/`memory_*` thuộc Tools. Không `MEMORY.md`. `write`/`edit` không được ghi dưới `~/.thyca`; `memory_remember` là writer duy nhất cho memory files.
- Serve chỉ loopback. API không trả secret, path nội bộ, hay stack.
- Memory recalled từ another-brain là claim; tree hiện tại thắng.
- Code và identifier tiếng Anh. Nói với user theo ngôn ngữ user.
- Linux là target. Đừng viết API chỉ chạy trên Windows.
- Secret chỉ qua env hoặc file ngoài Git (`~/.thyca/auth.json`, mode 0600).
- Layout: `thyca/{agent,llm,config,memory,sessions,tools,skills,serve,app,core}/` — cấm file lẻ top-level (trừ `__init__.py`/`__main__.py`) và cấm shim. `core/` là leaf (stdlib-only, không import `thyca.*` khác). `serve/` không import runtime `app/` (chỉ `TYPE_CHECKING` + lazy).
- Pytest baseline 2026-09-22: **719 passed / 0 fail**. Fail `test_debug_prints_prompt_flags` cũ (`tools=7` vs `tools=13`) không tái hiện — nếu đỏ lại thì là regression, không tự sửa số.
