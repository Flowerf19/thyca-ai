---
status: done
created: 2026-09-16
last_updated: 2026-09-16
---

# Memory linking — schema v6: project + chat session (việc 3)

## Summary

Nối 2 hệ id đang song song: memory session id (`{ngày}#{entry_id}`) vs chat session id
(`{timestamp}_{hex}`, transcript JSONL). Thêm 2 metadata nullable vào mỗi leaf:

- `project` — absolute path thư mục gốc dự án, **LLM tự điền** qua param tool call.
- `chat_session` — chat session id, **hệ thống inject**, LLM không đụng.

Use-case: nhật ký làm việc. Filter `memory_search` theo proj/chat. Không heuristic dò
tool-use, không kế thừa turn, không backfill mem cũ, không lưu file-level (file đã nằm
trong text leaf, FTS bắt được).

## Thay đổi theo lớp

### 1. Schema v6 — `thyca/memory/schema.sql` + `archive_store.py`

- `chunks` thêm 2 cột nullable: `project TEXT`, `chat_session TEXT` (ALTER TABLE).
- `SCHEMA_VERSION = "6"`; migration v3/v4/v5→v6 adds missing nullable columns with
  `ALTER TABLE`, creates any missing usage table, bumps meta version, and marks
  indexed files stale so the next reindex rereads Markdown. Không xoá dữ liệu nguồn
  hay chunks explicitly; Markdown remains source of truth and SQLite remains derived.
- `replace_source()` INSERT thêm 2 cột mới.
- `fts_search()` / `trigram_search()` nhận thêm filter `project` / `chat_session`
  (exact match, WHERE trên bảng chunks).

### 2. Heading — `thyca/memory/heading.py`

- `HeadingMeta` thêm `proj: str | None = None`, `chat: str | None = None`.
- `render_heading`: JSON comment thêm `"proj"` / `"chat"` khi có
  (vd `<!-- thyca {"id":"ab12cd34","imp":3,"exp":"...","proj":"/home/flowerf/Projects/thyca-ai","chat":"2026-09-16T14-39-01_a1b2"} -->`).
- `parse_heading`: đọc 2 key, heading cũ không có → None (backward compatible).

### 3. Chunker — `thyca/memory/chunk.py`

- `Chunk` thêm field `project`, `chat_session`.
- `_sessions()` truyền `meta.proj` / `meta.chat` vào session dict → chunk.

### 4. Facade — `thyca/tools/memory.py`

- `MemoryFacade.remember(..., proj=None, chat=None)` → `HeadingMeta`.
- `MemoryFacade.search(..., proj=None, chat=None)` → pass xuống store.

### 5. Tool spec — `thyca/tools/memory_tools.py` (description phải tốt)

`register_memory_tools(registry, facade)`; chat session context is bound per in-flight
turn with `ContextVar` (CLI leaves it unset, so `chat=None`).

**`memory_remember`** — param mới `proj`, description đầy đủ:

```
proj: Absolute path of the project root this memory belongs to,
e.g. /home/flowerf/Projects/thyca-ai or /home/flowerf/.thyca.
Use the real repo/workdir root, never a short name or relative path.
Omit (null) for general or user-level memories that belong to no project.
```

- Handler: `chat` lấy từ `chat_provider()` khi gọi, không nằm trong schema cho LLM.

**`memory_search`** — param mới + description:

```
proj:   Filter to leaves saved with this exact project root path.
chat:   Filter to leaves saved during this chat session id.
```

- Filter match ANY khi truyền cả hai? → KHÔNG: match cả hai (AND) — mỗi leaf chỉ có
  1 giá trị mỗi trường, AND là ngữ nghĩa tự nhiên.

**`memory_update`** — thêm param optional `proj` (chat remains system-controlled and is
never exposed to this LLM-facing contract):

```
proj:   New project root path for this memory. Omit to keep the current value.
```

- Lý do cho sửa: proj do LLM điền → có lúc sai (tên ngắn, nhầm repo). Không có đường
  sửa thì phải forget + remember lại = mất entry id, mất expires. Filter chỉ đáng tin
  khi data sửa được.
- Phạm vi sửa: theo SESSION (tất cả leaf cùng session_id) — proj/chat vốn là metadata
  gắn ở heading session, khớp cơ chế update hiện có của writer (đã sửa topic theo
  session). Không hỗ trợ lệch từng leaf.
- Handler: truyền `proj` xuống `writer.update_session`; omitted `proj` preserves the
  current value. `chat` is preserved by writer rewrites but cannot be LLM-mutated.

### 6. Inject điểm — wiring

- `chat_app.py`: bind `session_id` in a `ContextVar` around each `_run_turn`; memory
  remember reads that context, so concurrent turns cannot cross-link.
- `cli.py`: no binding → chat remains `None`.
- Tool registration is identical in ChatApp and CLI.

## Thứ tự thi công

1. heading.py: HeadingMeta + render/parse (test roundtrip trước).
2. chunk.py: Chunk + _sessions truyền qua.
3. schema.sql + archive_store.py: v6, migration, INSERT, filter trong search.
4. tools/memory.py: remember + search thêm param.
5. memory_tools.py: spec mới + description + chat_provider.
6. memory_tools.py: spec mới + description + chat_provider + memory_update thêm proj/chat.
7. chat_app.py / cli.py: wire chat_provider.
8. Tests + full suite. ✅ 522 tests pass (2026-09-16).

## Tests

- Heading roundtrip có/không proj+chat; heading cũ không key → None.
- Chunker: leaf từ heading có proj → chunk.project đúng.
- Migration: DB v5 có sẵn data → mở bằng v6 → reindex → cột mới nạp đúng, data cũ giữ.
- remember(proj=..., chat=...) → file markdown chứa JSON đúng → reindex → DB đúng.
- search(proj=X) chỉ trả leaf của X; search(chat=Y) tương tự; không truyền → như cũ.
- Tool: handler inject chat from the bound context; LLM truyền proj vào schema được
  validate, còn `chat` trong memory_update bị reject.
- memory_update(proj=...): heading session đổi đúng, metadata chat được giữ nguyên;
  không truyền proj → không đổi.
- Regression: 512 test cũ pass.

## Không làm (ngoài phạm vi)

- Không file-level "src" (cần thì thêm field nullable sau, chưa làm).
- Không backfill mem cũ, không heuristic, không kế thừa turn.
- Không sửa proj/chat theo từng leaf riêng lẻ (chỉ theo session).
