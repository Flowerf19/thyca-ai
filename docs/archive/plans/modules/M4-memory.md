---
status: done
created: 2026-09-22
last_updated: 2026-09-22
---

# M4 — memory module plan

## Summary

Module `thyca/memory/` (~1.826 dòng, 8 file + `schema.sql`) đã nằm đúng thư mục đích nên
**không cần `git mv`** (GOAL-002 bỏ qua cho M4, chỉ verify imports). Không file nào vượt
ngưỡng 400 dòng (`archive_store.py` 396 dòng — quyết định: giữ nguyên, lý do ở GOAL-001).
Refactor giới hạn trong nội bộ module: cắt 2 cạnh phụ thuộc sai hướng
(`writer.py` → `archived.py`, `archived.py` import hàm private `_hit_from_row`), nới lỏng
cạnh `active.py` → `thyca.skills` (file thuộc scope M6) bằng injection để M6 di chuyển
`skills.py` không vỡ M4. **Không đổi memory contract**: lexical-only (FTS5 + trigram),
markdown là nguồn sự thật, `memory_remember` là writer duy nhất — theo decision
`.agents/decisions/2026-08-15-l2-hybrid-v1.md` (embedding đã gỡ, không tái giới thiệu).

### GOAL-001: Giữ layout, chốt ranh giới public

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | Verify `thyca/memory/` không có file lẻ cần `git mv` (8 file `.py` + `schema.sql` + `README.md` đã trong thư mục đích); ghi kết quả `git status --porcelain -- thyca/memory/` sạch vào evidence | | |
| TASK-002 | Chốt `archive_store.py` (396 dòng) KHÔNG tách: toàn file là một class `ArchiveStore` + 2 helper private (`_safe_match` ở `archive_store.py:356`, `_hit_from_row` ở `archive_store.py:374`); tách SQL-migration ra file riêng tạo seam giả, không tăng SRP — ghi lý do này vào review note | | |
| TASK-003 | Đóng băng public surface: `thyca/memory/__init__.py` exports + `archived.py` re-exports (`ArchiveStore`, `Hit`, `SearchResult`, `ArchiveError`) giữ nguyên tên vì `thyca/tools/memory.py:8-31`, `thyca/serve_memory.py:4`, tests (`test_memory_archived.py:160`, `test_memory_stats.py:9`) đang import trực tiếp | | |

### GOAL-002: SOLID refactor nội bộ (SRP là chính)

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-004 | Sửa import sai hướng: `writer.py:11` import `ArchiveError` từ `thyca.memory.archived` (orchestration) trong khi class này định nghĩa ở `archive_store.py:25` — chuyển sang `from thyca.memory.archive_store import ArchiveError`, writer không còn phụ thuộc archived | | |
| TASK-005 | Đóng encapsulation rò rỉ: `archived.py:22` import hàm private `_hit_from_row` từ `archive_store` (dùng ở `archived.py:recent_hits`); chuyển `_hit_from_row` thành hàm public `hit_from_row` trong `archive_store.py` (giữ alias private cho tương thích test `test_memory_archived.py:160` nếu cần) và cập nhật call-site | | |
| TASK-006 | Nới cạnh M4→M6: `active.py:15` import `SkillStore` từ `thyca.skills` (file top-level thuộc scope M6, sẽ bị `git mv` ở GOAL-002 tổng) — thêm param optional `skills_store: SkillStore | None = None` vào `ActiveMemory.__init__` (`active.py:52-60`), default vẫn tự tạo để behavior/tests không đổi; import chuyển sang `TYPE_CHECKING` + lazy import trong constructor | | |
| TASK-007 | Ghi nhận nhưng KHÔNG sửa: `active.py:22` đọc prompts từ `thyca/llm/prompts` qua filesystem (không phải import, đã có fallback ở `active.py:24-30`) — tạo issue phối hợp M2 (nếu M2 dời `prompts/`, M4 chỉ đổi hằng path, không vỡ import) | | |
| TASK-008 | Ghi nhận nhưng KHÔNG sửa: `MemoryStats.build` (`stats.py:33-44`, 7 tham số) và `MemoryWriter` locks class-level (`writer.py:24-26`) — tách parameter-object/lock-registry là abstraction suy đoán, ngoài scope; chỉ refactor nếu review chỉ ra lỗi cụ thể | | |

### GOAL-003: Verify + gate

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-009 | Chạy focused tests: `test_memory_active`, `test_memory_archived`, `test_memory_heading`, `test_memory_lifecycle`, `test_memory_stats`, `test_memory_tools` + consumers `test_serve_memory_stats`, `test_skills`, `test_chat_app`, `test_agent_assemble`, `test_llm_prompt_manager` — tất cả xanh | | |
| TASK-010 | Chạy full `uv run pytest -q` (baseline cho phép fail duy nhất `test_cli.py::test_debug_prints_prompt_flags`), `python -c "import thyca.memory, thyca.tools.memory"` không circular, `git diff --check` sạch | | |

## Test Plan

- Focused gate (TASK-009): 6 file `tests/test_memory_*.py` + 5 consumer files liệt kê trên.
  Lệnh: `uv run pytest -q tests/test_memory_active.py tests/test_memory_archived.py tests/test_memory_heading.py tests/test_memory_lifecycle.py tests/test_memory_stats.py tests/test_memory_tools.py tests/test_serve_memory_stats.py tests/test_skills.py tests/test_chat_app.py tests/test_agent_assemble.py tests/test_llm_prompt_manager.py`.
- Full gate (TASK-010): `uv run pytest -q` toàn suite; chỉ chấp nhận fail đã biết
  `test_debug_prints_prompt_flags` (`tools=7` baseline). Không sửa test để pass.
- Contract gate: `ArchivedMemory`/`ArchiveStore`/`Hit`/`MemoryWriter`/`Chunker` public API
  không đổi chữ ký (ngoại trừ param optional thêm ở TASK-006 có default); lexical-only,
  không embedding/vector theo decision L2-hybrid-v1.

## Assumptions

1. M4 không sở hữu `MemoryFacade` (`thyca/tools/memory.py`) — facade thuộc M6; M4 chỉ giữ
   contract các class memory mà facade import (`ActiveMemory`, `ArchivedMemory`, `Chunk`,
   `MemoryStats`, `MemoryWriter`, heading helpers).
2. `thyca.skills.SkillStore` do M6 di chuyển trong GOAL-002 tổng; TASK-006 làm M4 miễn nhiễm
   với vị trí mới của file đó. M4 không import ngược lại từ `thyca.tools`.
3. `thyca.config` (M3) là leaf — import hằng `DEFAULT_LIMITS_HOT_TAIL_KB`,
   `DEFAULT_TIMELINE_TIMEZONE` ở `active.py:13`, `archived.py:11` được giữ nguyên.
4. Không thêm dependency, không đổi schema version (`SCHEMA_VERSION = "6"` ở
   `archive_store.py:14`), không đổi TTL/heading grammar (`heading.py`).
5. `schema.sql` và `thyca/memory/README.md` ở yên — là data/docs của module, không phải file lẻ.

## Close-out (2026-09-22, orchestrator)
All module tasks landed and verified: branch refactor-backend-solid-M4-memory commit 341a473, test 719/719, review approve zero findings. Merged into refactor/backend-solid, full suite 719 passed, plan status done.
