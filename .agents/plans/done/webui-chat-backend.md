---
status: done
created: 2026-08-22
last_updated: 2026-08-27
---

> Superseded in part by `thyca-operational-music-trace.md` (2026-08-26):
> `POST /api/sessions/{id}/turn` JSON remains; Chat live turns use
> `POST .../turn/stream` NDJSON. Assumption “Không SSE/stream” no longer holds.

# WebUI Chat — nối session JSONL + AgentLoop

## Summary

Memories đã đọc `GET /api/memory/stats`. Chat vẫn mock (`data.js`) và composer giả gửi. Nối Chat vào cùng `thyca --serve` loopback: liệt kê / mở / tạo session JSONL thật, gửi một lượt qua **cùng** `AgentLoop` với CLI.

Success: `thyca --serve` → sidebar Chat là `~/.thyca/sessions/*.jsonl`; mở phiên thấy `you`/`thyca` + tool strip; Gửi chạy loop và append JSONL; `python -m http.server --directory webui` vẫn mock, không crash. Trace: JSONL `Message.meta` usage/cost — UI sổ nghe là `thyca-trace-notebook.md` (không phải dump admin).

## Tasks

### GOAL-001: Session list/read trên store + HTTP

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | `SessionStore.list_paths()`: regular `*.jsonl`, không symlink, id khớp `_ID_RE`, sort `mtime_ns` desc rồi `name` desc. Dir thiếu → `[]` | x | 2026-08-22 |
| TASK-002 | `SessionManager.list_sessions()` load từng path, **không** đổi `current`. `SessionCorrupt`/`SessionNotFound` bỏ qua (một file hỏng không chết sidebar) | x | 2026-08-22 |
| TASK-003 | `GET /api/sessions` JSON `{model, sessions:[{id,title,updated_at,message_count}]}`. Title = dòng đầu content user, cắt 48; trống → `Phiên trống`. `GET /api/sessions/{id}` `{id,title,model,messages}` (canonical role/content/ts/tool_calls/tool_call_id). Id sai → 404. Corrupt → 503 `{error}` không path/stack | x | 2026-08-22 |

### GOAL-002: Tạo phiên + một lượt AgentLoop

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-004 | `POST /api/sessions` body rỗng → `create()`, 200 `{id,title,model,messages:[]}`. `POST` khác `/api/sessions*` và `POST /api/memory/stats` vẫn 405 | x | 2026-08-22 |
| TASK-005 | `ChatApp` (file mới `thyca/chat_app.py`): một `SessionManager` + `ActiveMemory` + tool registry như `Cli._run`. `POST /api/sessions/{id}/turn` body `{"text"}` (strip, 1..4000). Một `_turn_lock`. Trong lock: `load` + `asyncio.run(loop.run)` + `memory.refresh`. `Connect` mới mỗi lượt trừ khi inject (httpx `AsyncClient` không sống qua nhiều `asyncio.run`). Trả full session + `reply`. 400 text trống/dài/JSON; 404 id; 503 `LLMError`/`SessionError` `{error}` không secret/stack | x | 2026-08-22 |
| TASK-006 | `Cli._serve` dựng `ChatApp` (dùng `self._connect` nếu có). `make_server(..., chat=)` optional; thiếu chat thì route chat 404 — test stats cũ không bắt buộc ChatApp | x | 2026-08-22 |

### GOAL-003: Màn Chat đọc/gửi thật

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-007 | `webui/js/chat.js`: pages từ `/api/sessions`; body từ GET một phiên; `role=system` ẩn; user/assistant prose; `tool_calls` → tool-strip. Escape HTML. Composer POST turn rồi vẽ lại. Phiên mới → POST create. Fetch fail → giữ `data.js`, không throw | x | 2026-08-22 |
| TASK-008 | `render.js`/`app.js`: hydrate Chat giống Memories (không poll). `state.activeSessionId`. Nút Phiên mới và submit không còn timeout giả. Memories/Trace không đổi hành vi | x | 2026-08-22 |

## Test Plan

- `tests/test_session.py`: `list_paths` rỗng / skip symlink+tên lạ / sort mtime; `list_sessions` bỏ file corrupt, không đổi `current`.
- `tests/test_serve_chat.py`: list rỗng; create+list+get; turn với `FakeLLM` persist JSONL; 400/404/503; POST stats vẫn 405; traversal 404; bind non-loopback vẫn refuse.
- `tests/test_serve_memory_stats.py` vẫn pass.
- Parse `webui/index.html`; `chat.js` có mặt.
- `uv run pytest -q`. Không live LLM.

## Assumptions

1. Chat sống = gửi lượt, không chỉ xem lịch sử. Composer giả là sai khi đã hydrate session thật.
2. ~~Không SSE/stream. Một POST đợi hết loop.~~ Superseded: `/turn` still blocks; `/turn/stream` is NDJSON.
3. Một user local: serialize mọi turn. Tab thứ hai chờ, không 409.
4. Cùng origin, bind `127.0.0.1`. Không CORS, không auth — cùng trust với CLI (tool chạy thẳng).
5. Mock tĩnh không API → copy `data.js` như Memories.
6. ~~Không đổi compaction, protocol, Trace, memory stats.~~ Superseded riêng phần Trace: `/api/traces*` là bề mặt riêng (`thyca-trace-cost.md`); compaction/protocol/memory stats vẫn nguyên.
7. `SessionStore.read` vẫn reject dòng hỏng; GET lúc đang append có thể 503 — chấp nhận. Trace scan không 503 theo file: file hỏng bị skip, không chặn cả endpoint.
8. Không extract `runtime.py` rộng; wiring loop nằm trong `ChatApp`, song song `Cli._run`.
