---
status: done
created: 2026-08-28
last_updated: 2026-08-28
---

# Service — Skills (dispatch / tạo / load)

> 8/8. Thuộc `../thyca-agent-architecture.md`. **Done 2026-08-28.**
>
> Skill **đúng chuẩn Agent Skills** (agentskills.io — spec mà Claude/claude.ai dùng). Thống nhất 3 động tác: **dispatch** (index), **tạo** (`write`/`edit`), **load** (`read`).

## Summary

Skill = **thư mục chuẩn spec**: `<name>/SKILL.md` (YAML frontmatter `name` + `description`) + resource tùy ý. Một kho `~/.thyca/skills/`. Index chỉ name + description vào system prompt mỗi turn. **0 tool mới** — tạo bằng `write`, load bằng `read` (path suy ra được từ name).

| Nguyên tắc | Áp vào skills |
|---|---|
| Một kho, markdown là nguồn sự thật | Index quét disk mỗi `refresh()`, không sqlite, không cache |
| Không có dispatch ẩn | LLM đọc index → tự quyết load qua tool_calls |
| File-first (như SOUL/USER/IDENTITY, khác L2) | `write`/`edit` tự do vào `skills/` (PathGuard hiện không chặn); **validate sau** tại scanner, lỗi thì index cảnh báo → agent self-heal |

## Chuẩn Agent Skills (agentskills.io/specification)

Frontmatter YAML, 2 trường required:

| Field | Required | Ràng buộc |
|---|---|---|
| `name` | x | 1–64 ký tự; chỉ `a-z0-9-`; regex `^[a-z0-9]+(-[a-z0-9]+)*$` (không `-` đầu/cuối, không `--`); **phải trùng tên thư mục** |
| `description` | x | 1–1024 ký tự; nói **làm gì + khi nào dùng**, chứa keyword để model nhận diện task |
| `license` | ☐ | bỏ qua ở index |
| `compatibility` | ☐ | ≤500 chars; bỏ qua ở index |
| `metadata` | ☐ | map str→str; bỏ qua ở index |
| `allowed-tools` | ☐ | experimental, Claude-specific — Thyca v1 bỏ qua (chưa có gate seam) |

```markdown
---
name: create-mcp-tool
description: Create a new capability as a FastMCP stdio server. Use when thyca needs an API or capability it lacks (weather, HTTP, search) and the user agrees to add an MCP server.
---

# Create an MCP stdio tool
...
```

Cấu trúc thư mục (spec):

```
~/.thyca/skills/
  <skill-name>/
    SKILL.md            # required — frontmatter + body
    scripts/            # optional — script agent chạy qua bash
    references/         # optional — doc load khi cần
    assets/             # optional — template/data
```

**Progressive disclosure 3 mức của spec → 3 cửa của Thyca:**

| Mức spec | Cửa Thyca | Cơ chế |
|---|---|---|
| 1. Metadata (~100 token/skill) | **Dispatch** | Index `name — description` vào `<skills>` mỗi `refresh()` |
| 2. Body SKILL.md (<5k token, <500 dòng) | **Load** | `read ~/.thyca/skills/<name>/SKILL.md` — path suy từ name |
| 3. Resources | **Load sâu** | `read` đường dẫn tương đối từ skill root (script chạy bằng `bash`) |

## Index — validate theo spec

`ActiveMemory.refresh` quét `skills/*/SKILL.md` qua `SkillStore.list_meta()`. Mỗi skill 1 dòng: `- <name> — <description>`.

Validate (spec-exact, PyYAML `safe_load` trên block frontmatter):

- Frontmatter parse lỗi / thiếu `name` / `name` ≠ dirname / name sai regex → `- <dirname> (SKILL.md lỗi: <lý do ngắn>)`
- `description` thiếu hoặc >1024 → cảnh báo tương tự
- Description hiển thị truncate **256 ký tự** (spec cho 1024 — giữ index gọn; file giữ nguyên)
- Field lạ: bỏ qua (forward-compat, Claude Code cũng vậy)
- Body > 32KB → cảnh báo (registry `RESULT_CAP_BYTES` sẽ cắt khi read)

Frontmatter là YAML thật → **thêm dep `pyyaml>=6,<7`** (parse tay gãy với description chứa `:` / quote).

## Tạo & sửa skill

Agent viết thẳng file theo spec. `_RULES` rút còn ~2 dòng:

```
Check <skills> before multi-step tasks; read a SKILL.md to follow it.
To author a skill load `create-skill`; to add a capability load `create-mcp-tool`.
```

Chi tiết format nằm trong seed `create-skill` (progressive disclosure) — `_RULES` không giữ template inline nữa.

Seed mặc định 2 skill:

- `create-skill` — viết skill đúng spec: frontmatter, name regex, description "what + when + keyword" (điểm agent hay viết sai nhất), cấu trúc thư mục, ví dụ tốt/xấu. Viết mới, ~40 dòng.
- `create-mcp-tool` — tạo capability mới = FastMCP stdio server + config. Nâng từ repo `skills/create-mcp-tool.md`, viết lại theo spec.

Skill của agent dev (pi) ở repo `skills/` không đổi — hai hệ riêng.

## Contracts

```python
# thyca/skills.py — store dùng chung bởi memory (index) và tương lai (tools)
@dataclass(frozen=True)
class SkillMeta:
    name: str            # = dirname
    description: str     # "" nếu frontmatter lỗi
    path: Path           # .../skills/<name>/SKILL.md
    ok: bool             # False = vi phạm spec
    error: str = ""      # lý do, ngắn, cho index

class SkillStore:
    def __init__(self, thyca_dir: Path | None = None) -> None: ...
    @property
    def root(self) -> Path: ...            # ~/.thyca/skills
    def list_meta(self) -> list[SkillMeta]: ...   # scan + validate, sort theo name
    def index_text(self) -> str: ...       # "- name — desc" | "- name (SKILL.md lỗi: ...)"
    def ensure_defaults(self) -> None: ... # seed skill packaged nếu thiếu
```

- `thyca/memory/active.py`: `ensure_files` gọi `ensure_defaults`; `refresh()` thêm `skills=index_text()` vào `ActiveSnapshot` (field mới `skills: str = ""`, default giữ compat).
- `thyca/llm/prompt_manager.py`: section `<skills>` trước `<rules>`, bỏ qua khi rỗng; `_RULES` rút đoạn FastMCP 5 dòng còn 2 dòng trỏ tới 2 skill seed.
- PathGuard **không đổi** — `skills/` vốn đã ghi được bằng `write`/`edit`.
- `pyproject.toml`: thêm `pyyaml`, force-include template skill packaged.

| File | Đổi |
|---|---|
| `thyca/skills.py` (mới) | SkillStore + validate spec + index builder + seed |
| `thyca/memory/active.py` | refresh scan + ensure_defaults |
| `thyca/llm/prompt_manager.py` | `<skills>` + rút `_RULES` |
| `cli.py` / `chat_app.py` | 0 đổi (index đi qua Assemble sẵn) |
| `pyproject.toml` | `pyyaml` + force-include |

## Tasks

| TASK | Nội dung | Trạng thái |
|---|---|---|
| TASK-901 | `thyca/skills.py`: SkillStore (list_meta/index_text/ensure_defaults) + PyYAML + tests: spec name regex, name ≠ dirname, description >1024, frontmatter YAML lỗi, field lạ bỏ qua | x 2026-08-28 |
| TASK-902 | `ActiveSnapshot.skills` + refresh scan + `<skills>` section + test prompt | x 2026-08-28 |
| TASK-903 | Seed `create-skill` + `create-mcp-tool` theo spec + rút `_RULES` + template packaged + force-include + test | x 2026-08-28 |
| TASK-904 | Docs: README kiến trúc, checklist architecture, decision doc sau duyệt | x 2026-08-28 |

## Non-goals v1

- Không `skill_load`/`skill_save` tool — `read`/`write` đủ (v2 xem lại nếu index lỗi nhiều).
- Không `Skill` tool kiểu Claude Code (invoke theo lượt) — load là read thuần.
- Không `allowed-tools`/gate, không search/sqlite, không workspace skills, không delete tool.

## Quyết định đã chốt (2026-08-28)

1. Index truncate description ở **256 ký tự** (file giữ nguyên 1024 theo spec).
2. Seed **2 skill**: `create-skill` + `create-mcp-tool` — `_RULES` chỉ còn trỏ tới.

## Implementation notes (2026-08-28)

- `RESULT_CAP_BYTES` chuyển từ `tools/registry.py` sang `protocol.py` (tránh circular import `skills → tools/__init__ → memory → active → skills`; protocol là leaf). Registry import lại từ protocol — hành vi không đổi.
- Seed = copy-if-missing từ `thyca/skills_templates/` (không ghi đè edit của user).
- Section `<skills>` chỉ render khi index khác rỗng; chuỗi `<skills>` vẫn xuất hiện trong `_RULES` nên test phân biệt bằng closing tag `</skills>`.