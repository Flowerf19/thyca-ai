---
status: in-progress
created: 2026-08-21
last_updated: 2026-08-21
---

# Memory usage stats — đếm `get` theo leaf, bề mặt WebUI

## Summary

Quản lý L2 theo **leaf** (`chunk_id`). Tín hiệu duy nhất: `memory_get(chunk_id|session_id)` — không search, không inject nóng, không `get(path)`. `get` vẫn **tự reinforce** TTL session như hiện tại.

WebUI Memories là bề mặt đầu: số lượng, xài nhiều, không xài, đề xuất loại bỏ (chỉ hiển thị). Không auto-forget. Không tool agent `memory_list` / `memory_forget` trong slice này.

Markdown vẫn sự thật nội dung. Bảng đếm là derived trong `memory.sqlite`, keyed `chunk_id` ổn định (`{session_id}#{leaf_ord}`). Không ghi counter vào heading comment.

Live đối chiếu 2026-08-21: 3 session markdown, 0 lần `memory_get` trong JSONL — mọi leaf đều unused cho đến khi có `get`.

## Tasks

### GOAL-001: Ghi nhận `get` theo leaf

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | Schema v4 additive: bảng `leaf_gets(chunk_id PK, session_id, get_count CHECK >= 1, last_get_at)`. Không FK sang `chunks` (reindex `replace_source` không được xóa đếm). Migrate v3→v4 chỉ `CREATE TABLE` + `meta.schema_version=4`; **không** đi path drop-all của v1/v2 | x | 2026-08-21 |
| TASK-002 | `ArchiveStore.record_gets(chunk_ids, session_id, now)` upsert atomic `get_count = get_count + 1`. `MemoryFacade.get`: thành công rồi mới đếm. `chunk_id` → đúng 1 leaf; `session_id` → mỗi leaf trong payload trả về (cap `GET_SESSION_CAP`); `path` / miss / `ArchiveError` → không đếm. Search/recent/remember không đếm. Reinforce session giữ nguyên sau `get(chunk_id\|session_id)` | x | 2026-08-21 |
| TASK-003 | `forget` và `purge_expired` xóa hàng `leaf_gets` của leaf không còn trên markdown, để đếm chết không biến thành “mem xài nhiều” ma | x | 2026-08-21 |

Xong khi: `tests/test_memory_lifecycle.py` vẫn pass (TTL slide). Test mới: `get(chunk_id)` +1 đúng leaf; `get(session_id)` +1 từng leaf trả về; `get(path)` và `search` không đổi `leaf_gets`; get miss không tạo hàng.

### GOAL-002: Inventory + unused + đề xuất

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-004 | `thyca/memory/stats.py`: entity `LeafStat` + policy `MemoryStats`. I/O đếm ở `ArchiveStore`; `MemoryFacade.stats(now)` orchestrate. Không gộp vào `active.py` / `writer.py` | x | 2026-08-21 |
| TASK-005 | Inventory = leaf **có session heading L2**: `session_id` dạng `YYYY-MM-DD#entry` hoặc `memory#entry`. Loại `canonical#soul\|user\|memory` và `IDENTITY.md`. Union: chunks archived (bỏ expired/forgotten như search) + leaf hôm nay do `Chunker` đọc daily đang mở (chưa FTS). `get_count` mặc định 0 khi chưa có hàng `leaf_gets` | x | 2026-08-21 |
| TASK-006 | Tổng `total` / `used` (`get_count>=1`) / `unused` (`0`). `suggest_removal` = unused **không** gồm today hot, sort `expires_at` ASC (NULL cuối), `chunk_id` ASC. Chỉ list; không gọi `forget` | x | 2026-08-21 |

Xong khi: 3 leaf daily + 0 get → `total=3, used=0, unused=3`; get một leaf → used 1; SOUL/USER không có trong list; stub `# Memory` không có trong list; today unused không nằm `suggest_removal`.

### GOAL-003: HTTP local cho WebUI

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-007 | `thyca --serve` (flag, không subcommand): stdlib HTTP, bind **chỉ** `127.0.0.1`, port mặc định `8765`. Serve `webui/` tĩnh + `GET /api/memory/stats` JSON từ `MemoryFacade.stats()`. Không POST, không forget, không dependency mới | | |
| TASK-008 | Bind khác localhost → refuse. CORS không cần (same origin). Lỗi sqlite → HTTP 503 JSON `{error}` không stack | | |

Xong khi: `thyca --serve` + `GET http://127.0.0.1:8765/api/memory/stats` trả đúng shape; `python -m http.server --directory webui` vẫn là mock tĩnh (không API).

### GOAL-004: Memories page đọc số thật

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-009 | Mode Memories: landing tổng quan `total/used/unused`; sidebar list leaf (heading + get_count); trang/section “Đề xuất loại bỏ”. Persona `SOUL.md`/`USER.md`/`IDENTITY.md` giữ page phụ, không trộn vào inventory leaf | | |
| TASK-010 | Fetch `/api/memory/stats` khi serve cùng origin. Mock tĩnh (`http.server` only) không có API → copy fallback hiện tại, không crash. Không nút xóa | | |

Xong khi: serve + một `get` thật → leaf đó hiện used; unused và đề xuất khớp API; Chat/Trace không đổi.

## Test Plan

- `tests/test_memory_stats.py`: record rules (TASK-002), inventory filter (TASK-005), totals/suggest (TASK-006), migrate v3 file → v4 giữ chunks (TASK-001), forget xóa `leaf_gets` (TASK-003).
- `tests/test_memory_lifecycle.py` không đổi hành vi reinforce.
- `tests/test_serve_memory_stats.py`: bind 127.0.0.1, GET JSON, reject bind không localhost.
- WebUI: parse `webui/index.html`; không yêu cầu browser E2E. Kiểm tra JS không throw khi fetch fail (mock tĩnh).
- `uv run pytest -q`. Không live LLM.

## Assumptions

1. Đơn vị = leaf. TTL/reinforce vẫn theo session heading — không đưa `importance` vào sqlite.
2. “Nhắc lại” = `get(chunk_id|session_id)` thành công. Search, recent, inject `ActiveSnapshot`, `get(path)` không đếm.
3. Bề mặt user = WebUI. Telemetry sqlite + `thyca --serve` là điều kiện, không phải CLI stats.
4. `python -m http.server --directory webui` giữ mock; số thật chỉ khi `thyca --serve`.
5. Hôm nay có trong `total/unused` nhưng không đề xuất loại bỏ.
6. Không soft-forget, không grace 30 ngày, không semantic, không `memory_forget` tool, không đếm search hit.
7. Harness v1 từng “không web server”: slice này chỉ loopback read-only cho Memories stats.
8. Suggest là ranking để người xem; xóa vẫn tay/`forget` facade, ngoài slice.
