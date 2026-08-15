# Project context

`thyca-ai` là harness trợ lý cá nhân trong terminal. Cảm hứng pi (vòng lặp nhỏ, ít abstraction), không phải coding agent và không clone OpenClaw/Hermes.

Trạng thái repo (2026-08-15): package flat `thyca/` đã có skeleton CLI và Config service; các service còn lại chưa triển khai. `pytest` hiện có 15 test config pass. `thyca -p` vẫn là stub, chưa gọi LLM.

## Định hướng v1

- Một process CLI. User nói → model + tools → trả lời.
- Năng lực đến từ tool, không từ framework.
- MCP là nguồn tool hạng nhất (khác pi: pi cố ý không có MCP).
- Stack: Python 3.14 + uv, loop tự viết.
- Memory: markdown là nguồn sự thật; L2 hybrid gồm FTS5 + trigram + vector/RRF. Lexical search chạy trước; agent tự quyết khi nào gọi semantic search. Daily đóng ngày mới index; `SOUL.md`, `USER.md`, `MEMORY.md` luôn indexable.
- Tool chạy thẳng, không có cửa xác nhận ở v1. Seam `run(call)` để cắm gate (ask/auto) sau.
- `write`/`edit` không được ghi dưới `~/.thyca`; `memory_remember` là writer duy nhất cho memory files. Mutating calls phải serialize theo resource dù read-only calls có thể chạy song song.

## Ngoài v1

- Web UI, Telegram/Discord, subagent, plan mode, GUI popup, todo built-in.
- Catalog hàng chục provider.
- Nhạc/ảnh trong core.
- Memory MCP bên thứ 3 (cùng interface, gắn sau).
- ANN/vector database, background memory watcher, automatic memory prefetch.

Chi tiết và giả định: `.agents/plans/thyca-harness-v1.md`, `.agents/plans/l2-memory-retrieval.md`, và decision `.agents/decisions/2026-08-15-l2-hybrid-v1.md`.
