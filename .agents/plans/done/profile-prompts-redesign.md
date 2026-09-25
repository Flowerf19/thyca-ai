---
status: done
created: 2026-09-25
last_updated: 2026-09-25
---

# Plan — redesign SOUL / IDENTITY / USER project-level

## Summary

User duyệt ngày 2026-09-25: áp dụng đúng ba nội dung đã đề xuất cho `thyca/llm/prompts/identity.md`, `soul.md`, và `user.md`. USER là mẫu hồ sơ dữ kiện có hướng dẫn AI duy trì và các mục trống, không bịa thông tin cá nhân. Seed khi file thiếu; không đụng `~/.thyca` live, không commit/push hay cài lại thay user.

Vấn đề trước thay đổi (đã đối chiếu code):
- SOUL và IDENTITY trùng lặp nặng: cả hai đều nói Thyca/thi ca, general-purpose, mirror style, memory matters — không file nào sở hữu rõ thứ gì (`thyca/llm/prompts/*.md`).
- Memory guidance mơ hồ: chỉ "remember what matters / check before claiming / never invent" — thiếu WHEN (khi nào remember/search/get/update/forget), thiếu phân biệt `<today>` (đã inject) vs archive (phải search), thiếu phân biệt L2 daily (`memory_remember`) vs canonical (`write/edit`).
- Role mơ hồ: "personal assistant first" + "Not a coding agent" nhưng bot vẫn code được qua tools — cần nói rõ general-purpose nghĩa là gì, capability từ đâu (tools, one process, no subagents).

Nguyên tắc tách bạch sau redesign:
- `IDENTITY.md` = sự thật ổn định về bản thân (who am I): tên, nghĩa, role, ranh giới (là gì / không là gì), capability từ tools, surface-independence. Declarative, không chứa workflow tool.
- `SOUL.md` = cách hành xử (how I act): ngôn ngữ/xưng hô, phong cách trả lời, triết lý dùng tool, vòng đời memory đầy đủ (where + when), skills, honesty. Imperative, operational.
- `USER.md` = dữ kiện user đã chia sẻ/xác nhận, cách xưng hô, sở thích, bối cảnh lâu dài; hướng dẫn cập nhật cục bộ, không suy đoán, không lưu secret hoặc dữ liệu nhạy cảm chưa được đồng ý.
- Giữ thứ tự inject hiện tại: `<identity>` rồi `<role>` (SOUL), `<user>` nếu không trống/stub, `<today>`, `<skills>` nếu có, `<rules>`. `PromptManager.template` hỗ trợ cả ba template; không thêm fallback USER khi user đã chủ động để trống.
- CLI debug nhận diện section có mặt, không phụ thuộc câu chữ persona cũ (`You are Thyca` / `Name: Thyca`).

Ngoài scope: không đổi `<rules>`, memory tool contracts, WebUI hay layout hồ sơ; không migration/ghi đè hồ sơ live, không xóa dữ liệu, không thay dependency/version/install script.

### GOAL-001: Viết lại template persona + cập nhật tests

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | Viết lại `thyca/llm/prompts/identity.md`: who-am-I declarative (tên/nghĩa/role, one process, capability từ tools, surface-independence, not-coding-agent nhưng code được khi cần) | x | 2026-09-25 |
| TASK-002 | Viết lại `thyca/llm/prompts/soul.md`: how-I-act (mirror ngôn ngữ/xưng hô, trả lời ngắn, tools philosophy; memory where: profile vs daily vs archive; memory when: search trước khi nói không biết, remember proactive cuối task, get/update/forget lifecycle, lexical-only + never invent; skills check) | x | 2026-09-25 |
| TASK-003 | Cập nhật tests prompt: identity sở hữu tên/nghĩa/role, soul sở hữu hành vi/memory, user có cấu trúc và upkeep; giữ order/live override/stub semantics và kiểm tra tên template không hợp lệ | x | 2026-09-25 |
| TASK-004 | Chạy `uv run pytest -q`, baseline 719 passed / 0 fail — mọi đỏ mới là regression | x | 2026-09-25 |

### GOAL-002: Seed USER, compatibility và đóng gói

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-005 | Thêm `prompts/user.md`, seed qua `ActiveMemory._default_files`, cho phép đọc template USER và force-include trong wheel | x | 2026-09-25 |
| TASK-006 | Test khởi tạo đủ ba file, không ghi đè hồ sơ có sẵn kể cả trống/stub, refresh đầy đủ và inject mẫu USER khi cài mới | x | 2026-09-25 |
| TASK-007 | Sửa CLI debug để không phụ thuộc persona wording; kiểm tra default và hồ sơ tùy chỉnh | x | 2026-09-25 |
| TASK-008 | Build distribution, kiểm tra đủ ba template và seed từ wheel ở thư mục tạm; cập nhật README/CHANGELOG liên quan, independent review | x | 2026-09-25 |

## Test Plan

- `uv run --offline pytest tests/test_llm_prompt_manager.py tests/test_memory_active.py tests/test_cli.py -q`.
- `uv run --offline pytest -q` — full suite pass, không giảm số test; baseline ghi trong docs là 719, số hiện tại phải lấy từ test output.
- Build wheel/sdist vào thư mục tạm, kiểm tra ba template thực tế được đóng gói; smoke test `ActiveMemory` + `PromptManager` từ wheel ngoài checkout, dùng root tạm.
- Đọc prompt build ra để kiểm tra ownership, user trống không bịa dữ kiện, `<rules>` không đổi. Các kiểm tra này không thay thế đánh giá hành vi trên model thật.

## Assumptions

- Giữ tiếng Anh cho template (consistent với hiện tại; bot mirror ngôn ngữ user lúc chat, không phải lúc đọc persona).
- Nội dung đầy đủ user đã duyệt thay thế mục tiêu độ dài ~600B/~1.5KB của draft cũ; không tự cắt mất các điều kiện an toàn hay lifecycle.
- Hồ sơ có sẵn được giữ nguyên, kể cả file trống/stub. SOUL/IDENTITY vẫn fallback runtime như cũ; USER trống/stub vẫn bị bỏ khỏi prompt. Mẫu USER mới chỉ seed khi file chưa tồn tại.
- User dự định tự cài lại từ GitHub sau này. Bản sửa local chưa có trên GitHub cho tới khi được commit/push; không cần xóa cả `~/.thyca` để đổi hồ sơ.

## Verification evidence — 2026-09-25

- Focused tests (`test_llm_prompt_manager`, `test_memory_active`, `test_cli`): **65 passed**.
- Full suite: `uv run --offline pytest -q` — **760 passed in 80.05s**.
- `uv build --offline --out-dir <temp>` tạo sdist và wheel từ sdist thành công. Kiểm tra byte-for-byte đủ `identity.md`, `soul.md`, `user.md` trong cả hai distribution; smoke từ wheel đã extract ngoài checkout seed đủ ba hồ sơ vào root tạm và build system prompt thành công (5649 bytes).
- Template sizes: IDENTITY 725 bytes, SOUL 2310 bytes, USER 1033 bytes. CLI debug dùng closing tags để không nhầm `<user>` được nhắc trong SOUL với section USER thực sự.
- Smoke CLI từ wheel đã extract: one-shot qua fake LLM với root tạm tạo đủ ba hồ sơ; lượt tiếp theo giữ USER trống và báo `user=False` dù SOUL nhắc `<user>`. Không gọi API thật.
- AST comparison với HEAD xác nhận `_RULES` không đổi. `git diff --check`: pass. README/CHANGELOG đã cập nhật hành vi hồ sơ và seed.
- Independent review bằng `meta/muse-spark-1.3`, thinking `max`: **Approve**; reviewer tự chạy lại focused suite: **65 passed**. Hai ghi nhận không chặn: literal `</user>` trong profile tùy chỉnh có thể làm debug flag báo nhầm; USER giữ newline cuối file nên có một dòng trống trước closing tag. Không mở rộng scope để sửa hai điểm cosmetic này.
- Orchestrator verify cuối: focused suite **65 passed in 0.84s**, `git diff --check` pass, runtime/templates/tests hiện tại byte-match distribution đã kiểm tra. Tái hiện độc lập cả hai ghi nhận của reviewer; không ảnh hưởng nội dung/đường inject hồ sơ.
- Đã hoàn tất phạm vi được duyệt tại working tree. Không đọc/sửa/xóa hồ sơ live; không commit/push. Chưa đánh giá hành vi trên LLM thật.
