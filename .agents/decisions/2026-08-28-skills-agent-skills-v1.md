---
status: accepted
created: 2026-08-28
last_updated: 2026-08-28
---

# Skills v1 — chuẩn Agent Skills, file-first, không tool mới

## Bối cảnh

Thyca cần agent tự **dispatch / tạo / load** skill. Hai lựa chọn:

- **A — tool-writer**: `skill_save(name, description, content)` như form, tool sinh frontmatter + ghi file (single-writer kiểu `memory_remember`).
- **B — file-first**: skill chỉ là file theo chuẩn Agent Skills; tạo bằng `write`/`edit`, load bằng `read`, dispatch bằng index trong system prompt.

## Quyết định

**B.** Skill = thư mục `~/.thyca/skills/<name>/SKILL.md` theo spec agentskills.io (frontmatter `name` + `description`, regex `^[a-z0-9]+(-[a-z0-9]+)*$`, name khớp dirname, description ≤1024).

Lý do:

1. **Nhất quán bảng 3 kho nhớ**: L2 cần format chặt + index suy ra (sqlite) nên cần single writer; SOUL/USER/IDENTITY — tài liệu agent tự soạn — để CRUD tự do. Skill thuộc họ hai.
2. **Edit rẻ**: cập nhật skill = `edit` diff, không gửi lại toàn bộ body như `skill_save`.
3. **Đường tạo đã tồn tại**: PathGuard không chặn `skills/`, `write`/`edit` ghi được ngay, 0 dòng code.
4. **Validate-at-scan đủ**: scanner (`SkillStore`) validate spec-exact mỗi `refresh()`; skill lỗi hiện dòng cảnh báo ngay trong index → agent tự đọc, tự sửa (self-healing). Đây cũng là mô hình Claude Code.

## Bề mặt

| Động tác | Cơ chế |
|---|---|
| Dispatch | `<skills>` index trong system prompt (`- name — description`, truncate 256), scan mỗi turn, **không dispatch ẩn** |
| Tạo/sửa | `write`/`edit` — hướng dẫn đầy đủ trong seed `create-skill` |
| Load | `read` SKILL.md + resource (`scripts/`, `references/`, `assets/`) |

Progressive disclosure đúng 3 mức của spec: metadata (index) → body (read) → resources (read/bash).

## Hệ quả

- **0 tool mới** (tool count giữ 11); thêm dep `pyyaml`; `RESULT_CAP_BYTES` dời về `protocol.py`.
- Seed 2 skill mặc định: `create-skill`, `create-mcp-tool` (nâng từ repo `skills/create-mcp-tool.md`). `_RULES` rút đoạn FastMCP còn 2 dòng trỏ tới seed.
- Skill của agent dev (pi) ở repo `skills/` là hệ riêng, không đụng `~/.thyca/skills/`.

## Không làm v1

`skill_load`/`skill_save` tool, Skill tool kiểu Claude Code, `allowed-tools`/gate, search/sqlite cho skills, workspace skills, delete tool. Xem lại khi index lỗi nhiều hoặc skill vượt vài chục.