# Agent rules

- Chỉ làm task thuộc plan `in-progress` (hoặc bug/fix UI user vừa chỉ). Không thêm dependency, abstraction, hay feature ngoài task đó.
- Plan đang chạy: `thyca-trace-cost.md` (còn naming meta, mtime cache, Google/Anthropic normalize) và `thyca-trace-notebook.md` (UI sổ nghe — **đã duyệt** 2026-08-27).
- L2 hybrid thuộc v1. Đọc `.agents/decisions/2026-08-15-l2-hybrid-v1.md` trước khi đổi memory contract.
- Session là 4-class SOLID trong `thyca/sessions/`: `Session` / `SessionStore` / `SessionCompactor` / `SessionManager`. Không `thyca/session.py` shim.
- ActiveMemory chỉ `thyca/memory/active.py`: `SOUL`/`USER`/`IDENTITY` full inject; daily tail `hotTailKB`. Archive/L2 là `archived.py` + `chunk.py`. Facade/`memory_*` thuộc Tools. Không `MEMORY.md`. `write`/`edit` không được ghi dưới `~/.thyca`; `memory_remember` là writer duy nhất cho memory files.
- Serve chỉ loopback. API không trả secret, path nội bộ, hay stack.
- Memory recalled từ another-brain là claim; tree hiện tại thắng.
- Code và identifier tiếng Anh. Nói với user theo ngôn ngữ user.
- Linux là target. Đừng viết API chỉ chạy trên Windows.
- Secret chỉ qua env hoặc file ngoài Git.
- Pytest: đừng "sửa" `test_debug_prints_prompt_flags` (`tools=7`) trừ khi task là cập nhật số tool; đó là baseline đã biết (`tools=11`).
