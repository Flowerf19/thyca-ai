---
status: done
created: 2026-09-17
last_updated: 2026-09-17
---

# Live thinking — backend stream + persist

## Summary

Thay khoảng chờ ambient bằng **thinking thật của model**. Backend stream chuỗi reasoning theo thời gian thực trên NDJSON hiện có; persist reasoning vào JSONL để Trace/UI prototype đọc lại. **Không đụng WebUI** trong plan này — wire ổn định để prototype UI riêng.

TurnEvent giữ nguyên: không content. Thinking đi type mới `llm.thinking`, không vào allowlist `events.py`.

OpenAI Chat Completions luôn `stream: true`. Field reasoning: `delta.reasoning_content` rồi `delta.reasoning` (string). Không parse `reasoning_details`. Không stream `content` như thinking. Không gửi reasoning lại cho model (v1).

Provider không trả reasoning → không emit `llm.thinking`; `ChatReply.reasoning` rỗng; UI hiện tại giữ panel rỗng/caret, không tạo copy suy nghĩ giả.

## Success

1. Một round LLM: sau `llm.started`, zero-or-more `{"type":"llm.thinking","round":N,"delta":"..."}`, rồi `llm.finished`.
2. Follow (`GET /turn/stream`) replay đúng mọi delta đã emit.
3. JSONL assistant message có `reasoning` (string, có thể rỗng/omit khi không có); `content` không chứa CoT.
4. `TurnEvent` không có field content; test `test_agent_events` không đổi contract.
5. FakeLLM / CLI không stream vẫn chạy; không thinking event.
6. `uv run pytest tests/test_llm_openai_chat.py tests/test_agent_loop.py tests/test_agent_events.py tests/test_turn_stream.py tests/test_chat_app.py -q` xanh.

## Wire

NDJSON, cùng stream với TurnEvent:

```json
{"type": "llm.thinking", "round": 1, "delta": "First I check…"}
```

- `round` integer ≥ 1, cùng round với `llm.started`.
- `delta` string non-empty, đã redact API key, từng chunk đã coalesce.
- Không `text` tích lũy trên wire — client ghép delta.
- Không emit delta rỗng.

Dataclass `ThinkingDelta` (file mới `thyca/agent/thinking.py` hoặc cạnh `events.py`): `to_dict()` như trên. `TurnHub.publish` nhận `TurnEvent | ThinkingDelta | terminal tuple` như hiện tại. `pump_stream` ghi `ThinkingDelta.to_dict()` giống TurnEvent.

Cap: tích lũy tối đa `RESULT_CAP_BYTES` (32768) mỗi round; quá cap im lặng (không delta thêm, `reasoning` persist bị cắt cùng mốc, không báo trên wire).

Coalesce trong `OpenAIChat`: flush hook khi ≥64 chars **hoặc** ≥80ms từ flush trước, và khi stream kết thúc.

## Tasks

### GOAL-001: Parse + stream reasoning trong OpenAIChat

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | `ChatReply.reasoning: str \| None = None`. `_parse_reply` (non-stream, nếu còn) và stream assembler đọc `reasoning_content` rồi `reasoning` (str); ignore kiểu khác. | x | 2026-09-17 |
| TASK-002 | `OpenAIChat.chat` luôn `stream: true`. Thêm `stream_options: {include_usage: true}`. Parse SSE `data:` JSON; `[DONE]` kết thúc. Lắp `content`, tool_calls (arguments cộng dồn), usage chunk cuối, `reasoning`. | x | 2026-09-17 |
| TASK-003 | Optional `on_reasoning: Callable[[str], None] \| None` trên `chat(...)`. Default None. Flush coalesce 64 chars / 80ms / end. Redact API key. Cắt `RESULT_CAP_BYTES`. Không gọi hook khi None. | x | 2026-09-17 |
| TASK-004 | Retry: 429/5xx/transport như hiện tại **trước khi** hoặc khi stream đứt trước chunk hợp lệ đầu tiên. `reasoning_effort` 400 drop-and-retry giữ. Stream đã emit thinking rồi đứt → `llm.retry` (hook cũ) rồi request mới; round không tăng. | x | 2026-09-17 |
| TASK-005 | `LLMPort` / `Connect.chat` thêm kwarg `on_reasoning=None`. FakeLLM bỏ qua. `_to_openai_message` không gửi `reasoning`. | x | 2026-09-17 |

### GOAL-002: Emit live trên loop + HTTP

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-006 | `ThinkingDelta(round, delta)` + `to_dict`. Không đăng ký `TurnEvent`. | x | 2026-09-17 |
| TASK-007 | `Think.think` truyền `on_reasoning` vào `chat`. `AgentLoop` sau `llm.started` gắn hook: publish `ThinkingDelta` qua `event_sink` **không** đi `emit_event(TurnEvent)`. Sink hiện tại chỉ `TurnEvent` — đổi `EventSink` thành `Callable[[TurnEvent \| ThinkingDelta], None]` hoặc sink riêng trên Think. Chọn: **widen ChatApp hub sink**; `emit_event` vẫn chỉ TurnEvent. Loop gọi `event_sink(ThinkingDelta(...))` trực tiếp khi sink không None. | x | 2026-09-17 |
| TASK-008 | `pump_stream`: `isinstance(ThinkingDelta)` → `write_line(to_dict())`. Không đổi terminal completed/failed. Follow replay qua hub log. | x | 2026-09-17 |
| TASK-009 | `EventSink` type ở `events.py` giữ TurnEvent-only. ChatApp `sink` nhận cả hai: `hub.publish(event)` nếu `TurnEvent` hoặc `ThinkingDelta`. | x | 2026-09-17 |

### GOAL-003: Persist cho capture / Trace sau này

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-010 | `Message.reasoning: str \| None = None`. Không tính vào `meta` (cap 4096). Canonical JSONL thêm key `reasoning` khi non-empty. `from_dict` đọc optional. | x | 2026-09-17 |
| TASK-011 | `Observe.assistant` / `observe` copy `stage.reply.reasoning` (đã cap) vào Message. Naming message không lấy reasoning. | x | 2026-09-17 |
| TASK-012 | `session_wire.message_dict` thêm `reasoning` khi non-empty. Trace pill chưa đổi (UI plan sau). | x | 2026-09-17 |

### GOAL-004: Tests

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-013 | `test_llm_openai_chat.py`: helper SSE. Text + reasoning_content stream; OpenRouter `reasoning`; không field → reasoning None; tool_calls streamed; usage `include_usage`; key redact trong delta; cap 32KB; coalesce không bắt buộc assert timing, chỉ non-empty deltas ghép đúng. Non-stream JSON fixture đổi sang SSE. | x | 2026-09-17 |
| TASK-014 | `test_agent_loop.py`: FakeLLM có `reasoning` → Message.reasoning; không emit ThinkingDelta vì FakeLLM không gọi hook. Scripted stream double: LLM fake gọi `on_reasoning` vài delta → sink nhận ThinkingDelta đúng round, rồi llm.finished. | x | 2026-09-17 |
| TASK-015 | `test_turn_stream.py` (hoặc test mới): NDJSON thứ tự `turn.accepted`, `llm.started`, `llm.thinking`×N, `llm.finished`, `turn.completed`. Follow replay đủ delta. | x | 2026-09-17 |
| TASK-016 | `test_agent_events.py` không nhận `llm.thinking` như TurnEvent (unknown type). `ThinkingDelta.to_dict` exact keys. | x | 2026-09-17 |

## Test Plan

- TASK-013..016 rồi `uv run pytest -q`.
- Tay: `thyca --serve`, model compat có reasoning (DeepSeek/OpenRouter). `curl` NDJSON `/turn/stream` thấy `llm.thinking` khi model trả field; gpt-4o không thinking, không regress.
- UI hiện tại đọc `llm.thinking` riêng; nếu provider không có reasoning thì không hiển thị nội dung giả.

## Assumptions

1. UI prototype dùng đúng wire TASK-008; không cần đổi `chat-ambient.js` lúc này.
2. OpenAI o-series có thể không trả text — im lặng, không mock.
3. Không replay `reasoning_content` lên request round sau (DeepSeek 400 = việc sau).
4. Không stream assistant `content` (câu trả lời vẫn đợi `turn.completed` như hiện tại).
5. Coalesce 64/80ms là default; không config.
6. Thinking có thể chứa nội dung prompt/tool — user chấp nhận; chỉ redact API key.
7. `META_CAP` không đụng; reasoning là field Message riêng.
8. CLI `-p` / REPL không in thinking v1.
9. `OpenAIResponses` / Anthropic / Google vẫn stub.
